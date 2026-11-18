"""Contract tests for deployment/speaker-service (no real models).

The model loader is injected as a stub. The real sherpa-onnx run is a deploy
acceptance step, not a unit test. Only synthetic audio (sine tones) is used.
"""

import io
import math
import os
import struct
import sys
import tempfile
import wave
from pathlib import Path

import pytest

SERVICE_DIR = Path(__file__).resolve().parents[2] / "deployment" / "speaker-service"
sys.path.insert(0, str(SERVICE_DIR))

from speaker_service.app import create_app  # noqa: E402
from speaker_service.audio import AudioError, decode_wav  # noqa: E402


def make_wav(seconds=4.0, rate=16000, channels=1, width=2):
    frames = int(seconds * rate)
    out = io.BytesIO()
    with wave.open(out, "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(width)
        writer.setframerate(rate)
        data = bytearray()
        for i in range(frames):
            value = int(8000 * math.sin(2 * math.pi * 220 * i / rate))
            data += struct.pack("<h", value) * channels
        writer.writeframes(bytes(data))
    return out.getvalue()


class StubEngine:
    def __init__(self):
        self.embed_calls = []
        self.diarize_calls = []

    def info(self):
        return {
            "sherpa_onnx": "2.0.1",
            "segmentation": {"name": "seg-stub", "sha256": "a" * 64},
            "embedding": {"name": "emb-stub", "sha256": "b" * 64, "dim": 4},
        }

    def embed(self, samples):
        self.embed_calls.append(samples.size)
        return [0.1, 0.2, 0.3, 0.4]

    def diarize(self, samples, num_speakers, threshold):
        self.diarize_calls.append((samples.size, num_speakers, threshold))
        return [
            {"start": 0.0, "end": 1.5, "speaker": "SPEAKER_00"},
            {"start": 1.6, "end": 3.0, "speaker": "SPEAKER_01"},
        ]


@pytest.fixture
def engine():
    return StubEngine()


@pytest.fixture
def client(engine):
    return create_app(lambda: engine, max_bytes=2_000_000).test_client()


def test_routes_exist_and_methods_are_restricted(client):
    assert client.get("/embed").status_code == 405
    assert client.get("/diarize").status_code == 405
    assert client.post("/healthz").status_code == 405
    assert client.get("/nope").status_code == 404


def test_healthz_reports_models_versions_and_hashes(client):
    body = client.get("/healthz").get_json()
    assert body["models_loaded"] is True
    assert body["sherpa_onnx"] == "2.0.1"
    assert body["segmentation"]["sha256"] == "a" * 64
    assert body["embedding"]["sha256"] == "b" * 64
    assert body["embedding"]["dim"] == 4


def test_healthz_503_when_models_fail_to_load():
    def broken():
        raise RuntimeError("hash mismatch for model.onnx")

    client = create_app(broken).test_client()
    response = client.get("/healthz")
    assert response.status_code == 503
    body = response.get_json()
    assert body["models_loaded"] is False
    assert "hash mismatch" in body["error"]
    assert client.post("/embed", data=make_wav()).status_code == 503
    assert client.post("/diarize", data=make_wav()).status_code == 503


def test_embed_returns_vector(client, engine):
    response = client.post("/embed", data=make_wav(4.0), content_type="audio/wav")
    assert response.status_code == 200
    body = response.get_json()
    assert body["embedding"] == [0.1, 0.2, 0.3, 0.4]
    assert body["dim"] == 4
    assert body["model"] == "emb-stub"
    assert body["duration_s"] == pytest.approx(4.0, abs=0.01)
    assert engine.embed_calls == [64000]


def test_diarize_returns_segments_with_labels(client, engine):
    response = client.post("/diarize?num_speakers=2&threshold=0.5", data=make_wav(4.0))
    assert response.status_code == 200
    body = response.get_json()
    assert body["num_speakers"] == 2
    assert [s["speaker"] for s in body["segments"]] == ["SPEAKER_00", "SPEAKER_01"]
    assert engine.diarize_calls == [(64000, 2, 0.5)]


@pytest.mark.parametrize("path", ["/embed", "/diarize"])
def test_no_wav_is_rejected(client, path):
    assert client.post(path, data=b"").get_json()["error"] == "empty_body"
    for junk in (b"this is not audio at all", b"RIFF\x00\x00\x00\x00WAVEgarbage"):
        response = client.post(path, data=junk)
        assert response.status_code == 400
        assert response.get_json()["error"] == "invalid_wav"


@pytest.mark.parametrize("path", ["/embed", "/diarize"])
def test_oversized_body_is_rejected(client, engine, path):
    response = client.post(path, data=b"\x00" * 2_500_000)
    assert response.status_code == 413
    assert response.get_json()["error"] == "payload_too_large"
    assert engine.embed_calls == [] and engine.diarize_calls == []


def test_short_audio_is_rejected_for_embedding(client, engine):
    response = client.post("/embed", data=make_wav(1.0))
    assert response.status_code == 422
    assert response.get_json()["error"] == "audio_too_short"
    assert engine.embed_calls == []


@pytest.mark.parametrize("query", ["num_speakers=0", "num_speakers=x", "threshold=2", "threshold=abc"])
def test_diarize_rejects_bad_parameters(client, query):
    response = client.post(f"/diarize?{query}", data=make_wav(4.0))
    assert response.status_code == 400
    assert response.get_json()["error"] == "invalid_parameter"


def test_resampling_and_downmix_to_16k_mono():
    samples = decode_wav(make_wav(2.0, rate=44100, channels=2))
    assert samples.ndim == 1
    assert samples.size == pytest.approx(32000, abs=2)
    assert float(abs(samples).max()) <= 1.0


def test_decode_rejects_non_wav():
    with pytest.raises(AudioError):
        decode_wav(b"OggS" + b"\x00" * 64)


def test_no_audio_is_persisted(client, monkeypatch, tmp_path):
    """No temp file is created and nothing new appears in the temp dir or cwd."""

    def forbidden(*_args, **_kwargs):
        raise AssertionError("the service must not create temp files")

    for name in ("NamedTemporaryFile", "TemporaryFile", "SpooledTemporaryFile", "mkstemp", "mkdtemp"):
        monkeypatch.setattr(tempfile, name, forbidden)
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    watched = [scratch, cwd]
    before = [sorted(p.iterdir()) for p in watched]
    wav = make_wav(4.0)
    assert client.post("/embed", data=wav).status_code == 200
    assert client.post("/diarize", data=wav).status_code == 200
    assert client.post("/embed", data=b"junk").status_code == 400
    assert [sorted(p.iterdir()) for p in watched] == before


def test_service_source_has_no_disk_writes_of_audio():
    """The service package never opens files for writing and never uses temp files."""
    for path in (SERVICE_DIR / "speaker_service").glob("*.py"):
        source = path.read_text()
        assert "tempfile" not in source, path.name
        assert "'wb'" not in source and '"wb"' not in source, path.name
        assert "write_bytes" not in source, path.name
    assert os.path.isfile(SERVICE_DIR / "THIRD-PARTY-NOTICES")


def test_dockerfile_pins_versions_and_hashes():
    dockerfile = (SERVICE_DIR / "Dockerfile").read_text()
    for digest in (
        "24615ee884c897d9d2ba09bb4d30da6bb1b15e685065962db5b02e76e4996488",
        "220ad67ca923bef2fa91f2390c786097bf305bceb5e261d4af67b38e938e1079",
        "1a331345f04805badbb495c775a6ddffcdd1a732567d5ec8b3d5749e3c7a5e4b",
    ):
        assert digest in dockerfile
    assert "sherpa-onnx==2.0.1" in (SERVICE_DIR / "requirements.txt").read_text()
    assert not list(SERVICE_DIR.rglob("*.onnx"))
    assert not list(SERVICE_DIR.rglob("*.tar.bz2"))
