"""Unit and contract tests for own-user voice enrollment API (w265).

Covers positive, unauthorized, stale-version and retry/failure cases:
- GET  /api/clinic/v1/voice/enrollment (own consent revision and embedding count)
- POST /api/clinic/v1/voice/consent (opt-in grant with declaration text SHA-256)
- POST /api/clinic/v1/voice/enrollment (transient WAV validation, embedding, consent recheck)
- POST /api/clinic/v1/voice/consent/revoke (revocation, atomic embedding deletion and audit)
- Idempotency replay with identical payload vs 409 on altered payload
- ETag and strong If-Match revision gating (412 on mismatch, 428 on missing)
- Privacy boundaries: self-only actor, no embedding/audio in responses, transient audio wiped
"""

from __future__ import annotations

import io
import json
import math
import struct
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from flask import Flask
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect, generate_csrf

from src.app import app as speakr_app, db
from src.clinic import speaker_client as sc
from src.clinic import voice_consent as vc
from src.clinic.routes_voice_enrollment import (
    clear_idempotency_store,
    validate_enrollment_audio,
    voice_enrollment_bp,
)
from src.models import User, VoiceEmbeddingSpace


def make_test_wav(seconds: float = 4.0, rate: int = 16000, channels: int = 1, width: int = 2) -> bytes:
    """Create a synthetic mono 16-bit 16 kHz PCM sine-wave WAV."""
    frames = int(seconds * rate)
    out = io.BytesIO()
    with wave.open(out, "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(width)
        writer.setframerate(rate)
        data = bytearray()
        for i in range(frames):
            val = int(8000 * math.sin(2 * math.pi * 440 * i / rate))
            data += struct.pack("<h", val) * channels
        writer.writeframes(bytes(data))
    return out.getvalue()


class _StubSpeakerService:
    def __init__(self):
        self.model = "wespeaker-clinic-stub"
        self.vector = [0.12, -0.34, 0.56, -0.78]
        self.delay = 0.0
        self.fail_status = 0
        self.hits = []
        self.bodies = []


@pytest.fixture
def stub_speaker(monkeypatch):
    state = _StubSpeakerService()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, status, payload):
            raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            state.hits.append(("GET", self.path))
            if state.fail_status:
                self._send(state.fail_status, {"error": "failing"})
                return
            self._send(200, {"status": "ok", "model": state.model})

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            state.bodies.append(body)
            state.hits.append(("POST", self.path))
            if state.delay:
                time.sleep(state.delay)
            if state.fail_status:
                self._send(state.fail_status, {"error": "failing"})
                return
            self._send(200, {"embedding": state.vector})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv(sc.ENV_URL, f"http://127.0.0.1:{server.server_address[1]}")
    yield state
    server.shutdown()
    server.server_close()


@pytest.fixture
def endpoint_app(monkeypatch):
    monkeypatch.setenv("CLINIC_MODE", "1")
    clear_idempotency_store()

    test_app = Flask(__name__)
    test_app.config.update(speakr_app.config)
    test_app.config["TESTING"] = True
    test_app.config["SECRET_KEY"] = "synthetic-secret-for-testing"
    test_app.config["WTF_CSRF_ENABLED"] = False
    db.init_app(test_app)

    login_manager = LoginManager(test_app)
    login_manager.user_loader(lambda uid: db.session.get(User, int(uid)))
    CSRFProtect(test_app)
    test_app.register_blueprint(voice_enrollment_bp)
    yield test_app
    clear_idempotency_store()


@pytest.fixture
def client(endpoint_app, world):
    c = endpoint_app.test_client()
    with c.session_transaction() as sess:
        sess["_user_id"] = str(world["user_a"])
        sess["_fresh"] = True
    return c


def test_audio_validator_rejects_malformed_and_out_of_bounds():
    valid_wav = make_test_wav(seconds=4.0, rate=16000, channels=1, width=2)
    ok, err = validate_enrollment_audio(valid_wav)
    assert ok and not err

    # Too short (< 3s)
    short_wav = make_test_wav(seconds=2.0)
    ok, err = validate_enrollment_audio(short_wav)
    assert not ok and "shorter than minimum 3.0s" in err

    # Too long (> 30s)
    long_wav = make_test_wav(seconds=32.0)
    ok, err = validate_enrollment_audio(long_wav)
    assert not ok and "exceeds maximum 30.0s" in err

    # Stereo (2 channels)
    stereo_wav = make_test_wav(channels=2)
    ok, err = validate_enrollment_audio(stereo_wav)
    assert not ok and "mono" in err

    # Non-16kHz (e.g. 8000 Hz)
    bad_rate = make_test_wav(rate=8000)
    ok, err = validate_enrollment_audio(bad_rate)
    assert not ok and "16000 Hz" in err

    # Corrupt / non-WAV
    ok, err = validate_enrollment_audio(b"NOT_A_WAV_FILE_HEADER")
    assert not ok


def test_get_enrollment_initial_and_cache_control(client, world):
    resp = client.get("/api/clinic/v1/voice/enrollment")
    assert resp.status_code == 200
    assert resp.headers.get("Cache-Control") == "no-store"
    assert resp.headers.get("ETag") == '"voice-consent:0"'
    data = resp.get_json()
    assert data == {
        "consent_revision": 0,
        "state": "none",
        "embedding_count": 0,
        "granted_at": None,
        "revoked_at": None,
    }


def test_grant_consent_lifecycle_and_idempotency(client, world):
    headers = {
        "X-CSRFToken": "synthetic-csrf-token",
        "Idempotency-Key": "grant-key-0001",
        "If-Match": '"voice-consent:0"',
        "Content-Type": "application/json",
    }
    body = {"statement_text": "Ich willige in die Nutzung meines Stimmprofils ein."}

    # Successful grant
    resp = client.post("/api/clinic/v1/voice/consent", headers=headers, json=body)
    assert resp.status_code == 200
    assert resp.headers.get("Cache-Control") == "no-store"
    etag = resp.headers.get("ETag")
    assert etag.startswith('"voice-consent:') and etag != '"voice-consent:0"'
    data = resp.get_json()
    assert data["state"] == "granted"
    assert data["consent_revision"] > 0
    assert data["embedding_count"] == 0
    assert data["granted_at"] is not None

    # Exact replay returns cached result
    replay = client.post("/api/clinic/v1/voice/consent", headers=headers, json=body)
    assert replay.status_code == 200
    assert replay.get_json() == data
    assert replay.headers.get("ETag") == etag

    # Same key with different payload returns 409
    diff = client.post(
        "/api/clinic/v1/voice/consent",
        headers=headers,
        json={"statement_text": "Different text"},
    )
    assert diff.status_code == 409
    assert diff.get_json()["code"] == "IDEMPOTENCY_CONFLICT"

    # New key with already granted consent returns 409
    new_grant = client.post(
        "/api/clinic/v1/voice/consent",
        headers={
            "X-CSRFToken": "synthetic-csrf-token",
            "Idempotency-Key": "grant-key-0002",
            "If-Match": etag,
            "Content-Type": "application/json",
        },
        json={"statement_text": "Another grant attempt"},
    )
    assert new_grant.status_code == 409
    assert new_grant.get_json()["code"] == "ALREADY_GRANTED"


def test_enroll_voice_sample_lifecycle(client, stub_speaker, world):
    uid = world["user_a"]
    # 1. Reject enrollment before consent
    wav_bytes = make_test_wav(seconds=4.0)
    headers = {
        "X-CSRFToken": "synthetic-csrf-token",
        "Idempotency-Key": "enroll-key-0001",
        "If-Match": '"voice-consent:0"',
        "Content-Type": "audio/wav",
    }
    resp = client.post("/api/clinic/v1/voice/enrollment", headers=headers, data=wav_bytes)
    assert resp.status_code == 403
    assert resp.get_json()["code"] == "CONSENT_REQUIRED"

    # 2. Grant consent first
    grant_resp = client.post(
        "/api/clinic/v1/voice/consent",
        headers={
            "X-CSRFToken": "synthetic-csrf-token",
            "Idempotency-Key": "grant-key-before-enroll",
            "If-Match": '"voice-consent:0"',
            "Content-Type": "application/json",
        },
        json={"statement_text": "Einwilligung fuer Stimmbiometrie"},
    )
    assert grant_resp.status_code == 200
    consent_etag = grant_resp.headers.get("ETag")

    # 3. Enroll sample successfully
    headers["If-Match"] = consent_etag
    headers["Idempotency-Key"] = "enroll-key-0002"
    enroll_resp = client.post("/api/clinic/v1/voice/enrollment", headers=headers, data=wav_bytes)
    assert enroll_resp.status_code == 201
    assert enroll_resp.headers.get("Cache-Control") == "no-store"
    assert enroll_resp.headers.get("ETag") == consent_etag
    data = enroll_resp.get_json()
    assert data["state"] == "granted"
    assert data["embedding_count"] == 1
    # Verify no embeddings in response
    assert "embedding" not in data
    assert "vector" not in data

    # 4. Verify exact replay
    replay = client.post("/api/clinic/v1/voice/enrollment", headers=headers, data=wav_bytes)
    assert replay.status_code == 201
    assert replay.get_json() == data

    # 5. Revoke consent
    rev_headers = {
        "X-CSRFToken": "synthetic-csrf-token",
        "Idempotency-Key": "revoke-key-0001",
        "If-Match": consent_etag,
        "Content-Type": "application/json",
    }
    rev_resp = client.post("/api/clinic/v1/voice/consent/revoke", headers=rev_headers, json={"confirm": True})
    assert rev_resp.status_code == 200
    rev_data = rev_resp.get_json()
    assert rev_data["state"] == "revoked"
    assert rev_data["embedding_count"] == 0
    new_etag = rev_resp.headers.get("ETag")
    assert new_etag != consent_etag

    # 6. Replaying the earlier enrollment returns cached result without re-inserting
    replay_after_revoke = client.post("/api/clinic/v1/voice/enrollment", headers=headers, data=wav_bytes)
    assert replay_after_revoke.status_code == 201
    assert replay_after_revoke.get_json() == data
    with speakr_app.app_context():
        assert vc.embedding_count(uid) == 0


def test_if_match_and_stale_revision_gates(client, world):
    headers = {
        "X-CSRFToken": "synthetic-csrf-token",
        "Idempotency-Key": "gate-key-0001",
        "Content-Type": "application/json",
    }
    # Missing If-Match -> 428
    no_if_match = client.post(
        "/api/clinic/v1/voice/consent",
        headers=headers,
        json={"statement_text": "Text"},
    )
    assert no_if_match.status_code == 428
    assert no_if_match.get_json()["code"] == "PRECONDITION_REQUIRED"

    # Stale If-Match -> 412
    headers["If-Match"] = '"voice-consent:999"'
    headers["Idempotency-Key"] = "gate-key-0002"
    stale = client.post(
        "/api/clinic/v1/voice/consent",
        headers=headers,
        json={"statement_text": "Text"},
    )
    assert stale.status_code == 412
    assert stale.get_json()["code"] == "PRECONDITION_FAILED"


def test_unauthenticated_and_csrf_denials(endpoint_app, world):
    unauth_client = endpoint_app.test_client()

    # GET unauthenticated -> 401
    get_unauth = unauth_client.get("/api/clinic/v1/voice/enrollment")
    assert get_unauth.status_code == 401

    # POST unauthenticated -> 401
    post_unauth = unauth_client.post(
        "/api/clinic/v1/voice/consent",
        headers={
            "X-CSRFToken": "token",
            "Idempotency-Key": "unauth-key-001",
            "If-Match": '"voice-consent:0"',
        },
        json={"statement_text": "Text"},
    )
    assert post_unauth.status_code == 401

    # Authenticated but missing CSRF header -> 403
    auth_client = endpoint_app.test_client()
    with auth_client.session_transaction() as sess:
        sess["_user_id"] = str(world["user_a"])
    no_csrf = auth_client.post(
        "/api/clinic/v1/voice/consent",
        headers={
            "Idempotency-Key": "nocsrf-key-001",
            "If-Match": '"voice-consent:0"',
        },
        json={"statement_text": "Text"},
    )
    assert no_csrf.status_code == 403
    assert no_csrf.get_json()["code"] == "FORBIDDEN"


def test_speaker_service_failure_returns_503(client, stub_speaker, world):
    grant_resp = client.post(
        "/api/clinic/v1/voice/consent",
        headers={
            "X-CSRFToken": "synthetic-csrf-token",
            "Idempotency-Key": "grant-key-for-503",
            "If-Match": '"voice-consent:0"',
            "Content-Type": "application/json",
        },
        json={"statement_text": "Einwilligung fuer 503 Test"},
    )
    consent_etag = grant_resp.headers.get("ETag")

    # Simulate speaker service 500 error
    stub_speaker.fail_status = 500
    wav_bytes = make_test_wav(seconds=4.0)
    headers = {
        "X-CSRFToken": "synthetic-csrf-token",
        "Idempotency-Key": "enroll-key-503",
        "If-Match": consent_etag,
        "Content-Type": "audio/wav",
    }
    resp = client.post("/api/clinic/v1/voice/enrollment", headers=headers, data=wav_bytes)
    assert resp.status_code == 503
    assert resp.get_json()["code"] == "DEPENDENCY_UNAVAILABLE"


def test_clinic_mode_disabled_returns_404(endpoint_app, monkeypatch, world):
    monkeypatch.delenv("CLINIC_MODE", raising=False)
    client = endpoint_app.test_client()
    with client.session_transaction() as sess:
        sess["_user_id"] = str(world["user_a"])

    assert client.get("/api/clinic/v1/voice/enrollment").status_code == 404
    assert client.post("/api/clinic/v1/voice/enrollment").status_code == 404
    assert client.post("/api/clinic/v1/voice/consent").status_code == 404
    assert client.post("/api/clinic/v1/voice/consent/revoke").status_code == 404


def test_real_csrf_validation_when_enabled(monkeypatch, world):
    monkeypatch.setenv("CLINIC_MODE", "1")
    test_app = Flask(__name__)
    test_app.config.update(speakr_app.config)
    test_app.config["TESTING"] = True
    test_app.config["SECRET_KEY"] = "strict-csrf-test-secret"
    test_app.config["WTF_CSRF_ENABLED"] = True
    test_app.config["WTF_CSRF_CHECK_DEFAULT"] = False
    db.init_app(test_app)

    login_manager = LoginManager(test_app)
    login_manager.user_loader(lambda uid: db.session.get(User, int(uid)))
    CSRFProtect(test_app)
    test_app.register_blueprint(voice_enrollment_bp)

    @test_app.route("/get-csrf")
    def _csrf_route():
        return {"token": generate_csrf()}

    client = test_app.test_client()
    with client.session_transaction() as sess:
        sess["_user_id"] = str(world["user_a"])

    # 1. Invalid CSRF token -> 403
    bad_resp = client.post(
        "/api/clinic/v1/voice/consent",
        headers={
            "X-CSRFToken": "invalid-token",
            "Idempotency-Key": "strict-csrf-key-01",
            "If-Match": '"voice-consent:0"',
        },
        json={"statement_text": "Einwilligung"},
    )
    assert bad_resp.status_code == 403
    assert bad_resp.get_json()["code"] == "FORBIDDEN"

    # 2. Valid CSRF token -> 200
    valid_token = client.get("/get-csrf").get_json()["token"]

    good_resp = client.post(
        "/api/clinic/v1/voice/consent",
        headers={
            "X-CSRFToken": valid_token,
            "Idempotency-Key": "strict-csrf-key-02",
            "If-Match": '"voice-consent:0"',
        },
        json={"statement_text": "Einwilligung"},
    )
    assert good_resp.status_code == 200


def test_foreign_and_patient_identity_injection_rejected(client, world):
    # GET with foreign user_id query -> 403
    foreign_get = client.get(f"/api/clinic/v1/voice/enrollment?user_id={world['user_b']}")
    assert foreign_get.status_code == 403
    assert foreign_get.get_json()["code"] == "FORBIDDEN"

    # POST with foreign user_id or patient identity in JSON body -> 400 (additionalProperties: false)
    headers = {
        "X-CSRFToken": "token",
        "Idempotency-Key": "injection-key-01",
        "If-Match": '"voice-consent:0"',
        "Content-Type": "application/json",
    }
    body_with_user = {
        "statement_text": "Valid statement",
        "user_id": world["user_b"],
    }
    resp1 = client.post("/api/clinic/v1/voice/consent", headers=headers, json=body_with_user)
    assert resp1.status_code == 400
    assert resp1.get_json()["code"] == "INVALID_PAYLOAD"

    body_with_patient = {
        "statement_text": "Valid statement",
        "patient_id": "patient:999",
    }
    resp2 = client.post("/api/clinic/v1/voice/consent", headers=headers, json=body_with_patient)
    assert resp2.status_code == 400
    assert resp2.get_json()["code"] == "INVALID_PAYLOAD"


def test_payload_size_and_media_type_limits(client, world):
    # Oversized audio (> 2MB) -> 413
    oversized_audio = b"RIFF" + b"\x00" * 2_000_100
    headers = {
        "X-CSRFToken": "token",
        "Idempotency-Key": "limits-audio-key",
        "If-Match": '"voice-consent:0"',
        "Content-Type": "audio/wav",
    }
    resp_audio = client.post("/api/clinic/v1/voice/enrollment", headers=headers, data=oversized_audio)
    assert resp_audio.status_code == 413
    assert resp_audio.get_json()["code"] == "PAYLOAD_TOO_LARGE"

    # Wrong Content-Type for audio -> 415
    headers["Content-Type"] = "application/json"
    resp_wrong_ct = client.post("/api/clinic/v1/voice/enrollment", headers=headers, data=b"{}")
    assert resp_wrong_ct.status_code == 415
    assert resp_wrong_ct.get_json()["code"] == "UNSUPPORTED_MEDIA_TYPE"

    # Wrong Content-Type for consent -> 415
    headers["Content-Type"] = "text/plain"
    resp_wrong_consent_ct = client.post("/api/clinic/v1/voice/consent", headers=headers, data=b"plain text")
    assert resp_wrong_consent_ct.status_code == 415
    assert resp_wrong_consent_ct.get_json()["code"] == "UNSUPPORTED_MEDIA_TYPE"

    # Invalid Idempotency-Key length (< 8 chars) -> 400
    headers["Content-Type"] = "application/json"
    headers["Idempotency-Key"] = "short"
    resp_short_key = client.post(
        "/api/clinic/v1/voice/consent",
        headers=headers,
        json={"statement_text": "Valid text"},
    )
    assert resp_short_key.status_code == 400
    assert resp_short_key.get_json()["code"] == "INVALID_PAYLOAD"


def test_concurrent_idempotent_requests(client, world):
    headers = {
        "X-CSRFToken": "token",
        "Idempotency-Key": "concurrent-grant-key",
        "If-Match": '"voice-consent:0"',
        "Content-Type": "application/json",
    }
    body = {"statement_text": "Gleichzeitige Anfrage"}

    results = []

    def make_call():
        resp = client.post("/api/clinic/v1/voice/consent", headers=headers, json=body)
        results.append((resp.status_code, resp.get_json()))

    t1 = threading.Thread(target=make_call)
    t2 = threading.Thread(target=make_call)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert len(results) == 2
    assert results[0][0] == 200
    assert results[1][0] == 200
    assert results[0][1] == results[1][1]

