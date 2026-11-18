"""GPU to CPU ASR fallback (WP asr-fallback), against stub HTTP servers."""

import io
import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from src.services.transcription.base import TranscriptionRequest
from src.services.transcription.registry import ConnectorRegistry

SEGMENTS = [
    {"id": 0, "start": 0.0, "end": 1.5, "text": "Guten Tag"},
    {"id": 1, "start": 1.5, "end": 3.0, "text": "Frau Muster"},
]


class Stub:
    """A stub ASR service: /healthz plus /v1/audio/transcriptions."""

    def __init__(self, label, *, status=200, health=200, delay=0.0):
        self.label = label
        self.status = status
        self.health = health
        self.delay = delay
        self.posts = 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, code, body):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except OSError:
                    pass

            def do_GET(self):
                if self.path == '/healthz':
                    self._send(outer.health, {"status": "x"})
                else:
                    self._send(404, {})

            def do_POST(self):
                length = int(self.headers.get('Content-Length') or 0)
                self.rfile.read(length)
                outer.posts += 1
                if outer.delay:
                    time.sleep(outer.delay)
                if outer.status != 200:
                    self._send(outer.status, {"error": {"message": "boom"}})
                    return
                self._send(200, {"text": f"{outer.label}: Guten Tag Frau Muster",
                                 "language": "de", "duration": 3.0, "segments": SEGMENTS})

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.url = f'http://127.0.0.1:{self.server.server_address[1]}/v1'
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def stubs():
    made = []

    def make(label, **kwargs):
        stub = Stub(label, **kwargs)
        made.append(stub)
        return stub

    yield make
    for stub in made:
        stub.close()


@pytest.fixture
def env(monkeypatch):
    for var in ("TRANSCRIPTION_CONNECTOR", "ASR_BASE_URL", "USE_ASR_ENDPOINT",
                "CLINIC_GPU_ASR_BASE_URL", "CLINIC_GPU_ASR_TIMEOUT", "CLINIC_GPU_ASR_MODEL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CLINIC_MODE", "1")
    monkeypatch.setenv("TRANSCRIPTION_API_KEY", "k")
    return monkeypatch


def run(env, cpu, gpu=None, timeout=None):
    env.setenv("TRANSCRIPTION_BASE_URL", cpu.url)
    if gpu is not None:
        env.setenv("CLINIC_GPU_ASR_BASE_URL", gpu.url)
    if timeout is not None:
        env.setenv("CLINIC_GPU_ASR_TIMEOUT", str(timeout))
    connector = ConnectorRegistry().initialize_from_env()
    request = TranscriptionRequest(audio_file=io.BytesIO(b"audio-bytes"), filename="a.wav", mime_type="audio/wav")
    return connector, connector.transcribe(request)


def test_gpu_success_uses_gpu_only(env, stubs, caplog):
    cpu, gpu = stubs("cpu"), stubs("gpu")
    with caplog.at_level(logging.WARNING):
        _, response = run(env, cpu, gpu)
    assert response.text.startswith("gpu:")
    assert (gpu.posts, cpu.posts) == (1, 0)
    assert "ASR fallback" not in caplog.text


def test_gpu_5xx_falls_back_once_with_log(env, stubs, caplog):
    cpu, gpu = stubs("cpu"), stubs("gpu", status=503)
    with caplog.at_level(logging.WARNING):
        _, response = run(env, cpu, gpu)
    assert response.text.startswith("cpu:")
    assert (gpu.posts, cpu.posts) == (1, 1)
    assert "ASR fallback" in caplog.text and "HTTP 503" in caplog.text
    assert gpu.url in caplog.text and cpu.url in caplog.text


def test_gpu_timeout_falls_back(env, stubs, caplog):
    cpu, gpu = stubs("cpu"), stubs("gpu", delay=3.0)
    with caplog.at_level(logging.WARNING):
        _, response = run(env, cpu, gpu, timeout=0.5)
    assert response.text.startswith("cpu:")
    assert (gpu.posts, cpu.posts) == (1, 1)
    assert "timeout" in caplog.text


def test_gpu_connection_error_falls_back(env, stubs, caplog):
    cpu, gpu = stubs("cpu"), stubs("gpu")
    gpu.close()
    with caplog.at_level(logging.WARNING):
        _, response = run(env, cpu, gpu)
    assert response.text.startswith("cpu:")
    assert cpu.posts == 1
    assert "ASR fallback" in caplog.text and "unhealthy" in caplog.text


def test_unhealthy_gpu_is_skipped_without_post(env, stubs, caplog):
    cpu, gpu = stubs("cpu"), stubs("gpu", health=503)
    with caplog.at_level(logging.WARNING):
        _, response = run(env, cpu, gpu)
    assert response.text.startswith("cpu:")
    assert (gpu.posts, cpu.posts) == (0, 1)
    assert "/healthz HTTP 503" in caplog.text


def test_gpu_4xx_does_not_fall_back(env, stubs):
    from src.services.transcription.exceptions import TranscriptionError
    cpu, gpu = stubs("cpu"), stubs("gpu", status=400)
    with pytest.raises(TranscriptionError):
        run(env, cpu, gpu)
    assert cpu.posts == 0


def test_cpu_failure_after_fallback_is_not_retried(env, stubs, caplog):
    from src.services.transcription.exceptions import TranscriptionError
    cpu, gpu = stubs("cpu", status=500), stubs("gpu", status=500)
    with caplog.at_level(logging.WARNING):
        with pytest.raises(TranscriptionError):
            run(env, cpu, gpu)
    # One GPU attempt, one fallback. The CPU target keeps the OpenAI client's
    # own retries (upstream behaviour, unchanged), so cpu.posts is not asserted.
    assert gpu.posts == 1
    assert caplog.text.count("ASR fallback") == 1


def test_segments_identical_for_both_targets(env, stubs):
    gpu_ok = stubs("gpu")
    _, via_gpu = run(env, stubs("cpu"), gpu_ok)
    _, via_cpu = run(env, stubs("cpu"), stubs("gpu", status=502))
    assert [(s.text, s.start_time, s.end_time) for s in via_gpu.segments] == \
        [(s.text, s.start_time, s.end_time) for s in via_cpu.segments]
    assert len(via_gpu.segments) == 2 and via_gpu.language == via_cpu.language == "de"


def test_without_gpu_config_behaviour_is_unchanged(env, stubs):
    from src.services.transcription.connectors.openai_whisper import OpenAIWhisperConnector
    cpu = stubs("cpu")
    connector, response = run(env, cpu)
    assert type(connector) is OpenAIWhisperConnector
    assert response.text.startswith("cpu:")


def test_gpu_config_is_ignored_without_clinic_mode(env, stubs):
    from src.services.transcription.connectors.openai_whisper import OpenAIWhisperConnector
    env.setenv("CLINIC_MODE", "0")
    cpu, gpu = stubs("cpu"), stubs("gpu")
    connector, response = run(env, cpu, gpu)
    assert type(connector) is OpenAIWhisperConnector
    assert gpu.posts == 0
