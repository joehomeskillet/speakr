"""Contract-Tests des GPU-ASR-Dienstes (deployment/gpu-asr-service) mit gestubbter Engine.

Kein GPU-Zugriff, nur synthetische Bytes. Der echte GPU-Lauf ist Deploy-Abnahme.
"""
import importlib.util
import io
import os
import sys
import threading
import time
from pathlib import Path

import pytest

SERVICE_DIR = Path(__file__).resolve().parents[2] / "deployment" / "gpu-asr-service"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SERVICE_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def svc():
    saved = {n: sys.modules.get(n) for n in ("asr_engine", "asr_service")}
    _load("asr_engine")
    module = _load("asr_service")
    yield module
    for name, old in saved.items():
        if old is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = old


class StubEngine:
    device = "GPU.0"
    versions = {"openvino": "stub", "openvino_genai": "stub"}

    def __init__(self, delay=0.0, fail=None):
        self.calls = []
        self.delay = delay
        self.fail = fail
        self.active = 0
        self.max_active = 0

    def transcribe(self, samples, *, language=None, prompt=None, hotwords=None, timestamps=False):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            self.calls.append({"n": len(samples), "language": language, "prompt": prompt,
                               "hotwords": hotwords, "timestamps": timestamps})
            if self.delay:
                time.sleep(self.delay)
            if self.fail:
                raise self.fail
            out = {"text": "Guten Tag", "segments": [], "words": []}
            if timestamps:
                out["segments"] = [{"start": 0.0, "end": 1.5, "text": "Guten Tag"}]
                out["words"] = [{"word": "Guten", "start": 0.0, "end": 0.7},
                                {"word": "Tag", "start": 0.8, "end": 1.5}]
            return out
        finally:
            self.active -= 1


def make_client(svc, engine=None, loader=None, **config):
    eng = engine or StubEngine()
    app = svc.create_app(
        engine_loader=loader or (lambda *a: eng),
        decoder=lambda data: [0.0] * 16000,
        config={"MODEL_NAME": "whisper-large-v3-turbo-int8-ov", **config},
    )
    return app.test_client(), eng


def post(client, **fields):
    data = {"file": (io.BytesIO(b"synthetic"), "a.wav")}
    data.update(fields)
    return client.post("/v1/audio/transcriptions", data=data, content_type="multipart/form-data")


def test_healthz_reports_loaded_model(svc):
    client, _ = make_client(svc)
    body = client.get("/healthz").get_json()
    assert body["model_loaded"] is True
    assert body["device"] == "GPU.0"
    assert body["versions"]["openvino_genai"] == "stub"
    assert client.get("/healthz").status_code == 200


def test_healthz_503_when_engine_fails_to_load(svc):
    def boom(*_args):
        raise RuntimeError("no GPU")
    client, _ = make_client(svc, loader=boom)
    resp = client.get("/healthz")
    assert resp.status_code == 503
    assert resp.get_json()["model_loaded"] is False
    assert post(client).status_code == 503


def test_json_format_returns_text_only(svc):
    client, eng = make_client(svc)
    resp = post(client, response_format="json", language="de")
    assert resp.status_code == 200
    assert resp.get_json() == {"text": "Guten Tag"}
    assert eng.calls[0]["timestamps"] is False
    assert eng.calls[0]["language"] == "de"


def test_verbose_json_has_segments_and_words(svc):
    client, eng = make_client(svc)
    body = post(client, response_format="verbose_json", language="de", model="whisper-large-v3-turbo-int8-ov").get_json()
    assert body["task"] == "transcribe"
    assert body["language"] == "de"
    assert body["duration"] == 1.0
    assert body["segments"] == [{"id": 0, "start": 0.0, "end": 1.5, "text": "Guten Tag"}]
    assert body["words"][1] == {"word": "Tag", "start": 0.8, "end": 1.5}
    assert eng.calls[0]["timestamps"] is True


def test_prompt_and_hotwords_are_passed_through(svc):
    client, eng = make_client(svc)
    post(client, prompt="Anamnese, Befund", hotwords="Metformin Ramipril")
    assert eng.calls[0]["prompt"] == "Anamnese, Befund"
    assert eng.calls[0]["hotwords"] == "Metformin Ramipril"


@pytest.mark.parametrize("fields,status", [
    ({"response_format": "srt"}, 400),
    ({"language": "de-DE!"}, 400),
    ({"model": "other-model"}, 404),
    ({"timeout": "abc"}, 400),
    ({"timeout": "0"}, 400),
])
def test_invalid_parameters(svc, fields, status):
    client, eng = make_client(svc)
    resp = post(client, **fields)
    assert resp.status_code == status
    assert "error" in resp.get_json()
    assert eng.calls == []


def test_missing_file_is_400(svc):
    client, _ = make_client(svc)
    resp = client.post("/v1/audio/transcriptions", data={"model": "x"}, content_type="multipart/form-data")
    assert resp.status_code == 400


def test_undecodable_audio_is_400(svc):
    def bad_decoder(_data):
        raise svc.engine_mod.AudioDecodeError("audio could not be decoded")
    app = svc.create_app(engine_loader=lambda *a: StubEngine(), decoder=bad_decoder, config={})
    resp = post(app.test_client())
    assert resp.status_code == 400
    assert resp.get_json()["error"]["code"] == "invalid_audio"


def test_engine_failure_is_clear_500(svc):
    client, _ = make_client(svc, engine=StubEngine(fail=svc.engine_mod.EngineError("CL_OUT_OF_RESOURCES")))
    resp = post(client)
    assert resp.status_code == 500
    err = resp.get_json()["error"]
    assert err["code"] == "engine_error"
    assert "CL_OUT_OF_RESOURCES" in err["message"]


def test_timeout_is_504(svc):
    client, _ = make_client(svc, engine=StubEngine(delay=0.6))
    resp = post(client, timeout="0.1")
    assert resp.status_code == 504
    assert resp.get_json()["error"]["code"] == "engine_timeout"


def test_requests_are_serialized_on_one_engine(svc):
    client_app = svc.create_app(
        engine_loader=lambda *a: engine,
        decoder=lambda data: [0.0] * 160,
        config={"MODEL_NAME": "whisper-large-v3-turbo-int8-ov"},
    )
    engine = StubEngine(delay=0.05)
    client_app.extensions["gpu_asr"]["engine"] = engine
    statuses = []

    def run():
        statuses.append(post(client_app.test_client()).status_code)

    threads = [threading.Thread(target=run) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert statuses == [200] * 4
    assert engine.max_active == 1
    assert len(engine.calls) == 4


def test_no_audio_persisted(svc, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    client, _ = make_client(svc)
    assert post(client, response_format="verbose_json").status_code == 200
    assert list(tmp_path.iterdir()) == []
    assert not [p for p in SERVICE_DIR.iterdir() if p.suffix in {".wav", ".mp3", ".ogg", ".m4a"}]


def test_service_source_never_writes_audio_to_disk(svc):
    source = (SERVICE_DIR / "asr_service.py").read_text() + (SERVICE_DIR / "asr_engine.py").read_text()
    for needle in (".save(", "NamedTemporaryFile", "mkstemp", "open("):
        assert needle not in source
    assert os.path.exists(SERVICE_DIR / "model.sha256")


def test_oversized_upload_is_413(svc):
    client, _ = make_client(svc, MAX_CONTENT_LENGTH=10)
    data = {"file": (io.BytesIO(b"x" * 5000), "a.wav")}
    resp = client.post("/v1/audio/transcriptions", data=data, content_type="multipart/form-data")
    assert resp.status_code == 413
