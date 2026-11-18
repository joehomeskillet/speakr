"""Speaker service client against a local stub HTTP server (real round trips)."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from src.app import app, db
from src.clinic import speaker_client as sc
from src.models import VoiceEmbeddingSpace


class _Stub:
    """Mutable behaviour of the stub service, one instance per test."""

    def __init__(self):
        self.model = "wespeaker-test-1"
        self.diarize = (200, {"segments": [
            {"start": 5.0, "end": 9.5, "speaker": "SPEAKER_01"},
            {"start": 0.0, "end": 4.2, "speaker": "SPEAKER_00"},
        ]})
        self.embed = (200, {"embedding": [0.1, 0.2, 0.3, 0.4]})
        self.delay = 0.0
        self.hits = []
        self.bodies = []


@pytest.fixture
def stub(monkeypatch):
    state = _Stub()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, status, payload):
            raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            state.hits.append(("GET", self.path))
            self._send(200, {"status": "ok", "model": state.model})

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            state.bodies.append(self.rfile.read(length))
            state.hits.append(("POST", self.path))
            if state.delay:
                time.sleep(state.delay)
            status, payload = state.diarize if self.path == "/diarize" else state.embed
            try:
                self._send(status, payload)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv(sc.ENV_URL, f"http://127.0.0.1:{server.server_address[1]}")
    yield state
    server.shutdown()
    server.server_close()


@pytest.fixture
def wav(tmp_path):
    path = tmp_path / "clip.wav"
    path.write_bytes(b"RIFF....WAVEfmt fake-audio")
    return path


@pytest.fixture
def clean_spaces():
    def wipe():
        with app.app_context():
            db.session.rollback()
            VoiceEmbeddingSpace.query.filter(
                VoiceEmbeddingSpace.backend_fingerprint.like(sc.FINGERPRINT_PREFIX + "%")
            ).delete(synchronize_session=False)
            db.session.commit()
    wipe()
    yield
    wipe()


def test_diarize_success_returns_asr_shaped_sorted_segments(stub, wav):
    with app.app_context():
        segments = sc.diarize_recording(object(), wav)
    assert segments == [
        {"speaker": "SPEAKER_00", "start_time": 0.0, "end_time": 4.2},
        {"speaker": "SPEAKER_01", "start_time": 5.0, "end_time": 9.5},
    ]
    assert ("GET", "/healthz") in stub.hits and ("POST", "/diarize") in stub.hits
    assert stub.bodies == [wav.read_bytes()]


def test_embed_success_returns_float_list(stub, wav):
    vec = sc.embed(wav)
    assert vec == [0.1, 0.2, 0.3, 0.4]
    assert all(isinstance(x, float) for x in vec)


def test_timeout_raises_timeout_error_without_retry(stub, wav):
    stub.delay = 1.0
    with pytest.raises(sc.SpeakerServiceTimeout):
        sc.diarize(wav, timeout=0.2)
    assert stub.hits.count(("POST", "/diarize")) == 1
    time.sleep(1.2)
    assert stub.hits.count(("POST", "/diarize")) == 1


def test_5xx_raises_http_error_once(stub, wav):
    stub.diarize = (503, {"error": "model loading"})
    with pytest.raises(sc.SpeakerServiceHTTPError) as err:
        sc.diarize(wav)
    assert err.value.status == 503
    assert stub.hits.count(("POST", "/diarize")) == 1


@pytest.mark.parametrize("payload", [
    b"not json at all",
    [1, 2, 3],
    {"nothing": True},
    {"segments": "x"},
    {"segments": [{"start": 0, "end": 1, "speaker": "bob"}]},
    {"segments": [{"start": 3, "end": 1, "speaker": "SPEAKER_00"}]},
    {"segments": [{"start": "a", "end": 1, "speaker": "SPEAKER_00"}]},
])
def test_invalid_diarize_response_raises_bad_response(stub, wav, payload):
    stub.diarize = (200, payload)
    with pytest.raises(sc.SpeakerServiceBadResponse):
        sc.diarize(wav)


@pytest.mark.parametrize("payload", [{"embedding": []}, {"embedding": ["a"]}, {"vector": [1.0]}])
def test_invalid_embed_response_raises_bad_response(stub, wav, payload):
    stub.embed = (200, payload)
    with pytest.raises(sc.SpeakerServiceBadResponse):
        sc.embed(wav)


def test_unconfigured_and_unreachable(monkeypatch, wav):
    monkeypatch.delenv(sc.ENV_URL, raising=False)
    with pytest.raises(sc.SpeakerServiceNotConfigured):
        sc.health()
    monkeypatch.setenv(sc.ENV_URL, "http://127.0.0.1:1")
    with pytest.raises(sc.SpeakerServiceUnavailable):
        sc.health()


def test_space_created_once_per_model_and_reused(stub, wav, clean_spaces):
    with app.app_context():
        first = sc.embed_with_space(wav)
        again = sc.embed_with_space(wav)
        assert first.space_id == again.space_id
        assert first.model == "wespeaker-test-1"
        row = db.session.get(VoiceEmbeddingSpace, first.space_id)
        assert row.dimension == 4
        assert row.backend_fingerprint == sc.FINGERPRINT_PREFIX + "wespeaker-test-1"

        stub.model = "other-model-2"
        other = sc.embed_with_space(wav)
        assert other.space_id != first.space_id

        count = VoiceEmbeddingSpace.query.filter(
            VoiceEmbeddingSpace.backend_fingerprint.like(sc.FINGERPRINT_PREFIX + "%")).count()
        assert count == 2


def test_existing_spaces_are_never_overwritten(stub, wav, clean_spaces):
    with app.app_context():
        legacy = VoiceEmbeddingSpace(dimension=4, canary_embedding=[1.0, 0.0, 0.0, 0.0],
                                     backend_fingerprint="asr-backend")
        db.session.add(legacy)
        db.session.commit()
        legacy_id = legacy.id
        try:
            result = sc.embed_with_space(wav)
            assert result.space_id != legacy_id
            db.session.expire_all()
            kept = db.session.get(VoiceEmbeddingSpace, legacy_id)
            assert kept.backend_fingerprint == "asr-backend"
            assert kept.canary_embedding == [1.0, 0.0, 0.0, 0.0]
        finally:
            VoiceEmbeddingSpace.query.filter_by(id=legacy_id).delete()
            db.session.commit()


def test_dimension_change_of_known_model_is_an_error(stub, wav, clean_spaces):
    with app.app_context():
        sc.embed_with_space(wav)
        stub.embed = (200, {"embedding": [0.1, 0.2]})
        with pytest.raises(sc.SpeakerServiceBadResponse):
            sc.embed_with_space(wav)


def test_long_model_id_fits_fingerprint_column():
    assert len(sc.model_fingerprint("m" * 300)) <= 64
    assert sc.model_fingerprint("m" * 300) == sc.model_fingerprint("m" * 300)
