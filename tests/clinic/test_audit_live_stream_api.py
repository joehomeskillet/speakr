"""Browser WebSocket, local realtime peer and durable phrase tests."""

import json
import socket
import uuid
from base64 import b64encode
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from flask import Flask
from flask_login import LoginManager, login_user
from flask_wtf.csrf import CSRFProtect, generate_csrf
from sqlalchemy.exc import OperationalError
from wsproto import Connection, ConnectionType
from wsproto.events import BytesMessage, CloseConnection, TextMessage

from src.app import app as real_app
from src.clinic import live_phrases, live_session, routes_live, routes_live_stream as stream
from src.clinic.ingest import IngestService
from src.clinic.live_asr_adapter import LiveAsrAdapter
from src.clinic.models import ClinicDictation, ClinicTranscriptRevision
from src.clinic.parity_dispatch import ParityDispatch
from src.database import db
from src.models import Recording, User
from test_audit_live_asr_transport import _FakeSpeaches, _MODEL

NOW = datetime(2026, 11, 28, 9, tzinfo=timezone.utc)
PCM = b"\x01\x00" * (stream.FRAME_BYTES // 2)
BEGIN = {"client_id": "tab-1", "generation": 1, "after_sequence": 0}
ORIGIN = "http://localhost:8899"


class ManualSpeaches(_FakeSpeaches):
    def _ack(self):
        ack = json.loads(super()._ack())
        if self.mode == "ok":
            ack["session"]["turn_detection"] = self.session["session"]["turn_detection"]
        return json.dumps(ack)


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("CLINIC_MODE", "1")
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="synthetic-stream-key",
                      SQLALCHEMY_DATABASE_URI=real_app.config["SQLALCHEMY_DATABASE_URI"])
    db.init_app(app)
    login = LoginManager(app)
    CSRFProtect(app)
    login.user_loader(lambda uid: db.session.get(User, int(uid)))
    with app.app_context():
        live_session.ClinicLiveSession.__table__.create(db.engine, checkfirst=True)
        live_phrases.create_tables()
        suffix = uuid.uuid4().hex[:10]
        owner = User(username="stream-" + suffix, email=suffix + "@example.invalid", password="x")
        other = User(username="other-" + suffix, email="o" + suffix + "@example.invalid", password="x")
        db.session.add_all([owner, other])
        db.session.flush()
        recording = Recording(user_id=owner.id, title="Synthetic browser stream", status="PENDING")
        db.session.add(recording)
        db.session.flush()
        dictation = ClinicDictation(recording_id=recording.id, author_user_id=owner.id)
        db.session.add(dictation)
        db.session.commit()
        w = SimpleNamespace(app=app, did=dictation.id, owner_id=owner.id, other_id=other.id,
                            now=NOW, permitted=True, peer=None, adapter_calls=0)

    def allowed(_actor, did):
        return w.permitted and did == w.did

    def run_once(_scope, _digest, operation):
        result = operation()
        db.session.commit()
        return result

    def adapter():
        w.adapter_calls += 1
        assert w.peer is not None
        return LiveAsrAdapter({"base_url": w.peer.base_url, "model": _MODEL})

    dispatch = ParityDispatch(run_once)
    routes_live.register_live_commands(dispatch, allowed, lambda: w.now, stream_enabled=True)
    app.register_blueprint(dispatch.blueprint)
    app.register_blueprint(routes_live.create_blueprint(
        allowed, IngestService(tmp_path), now=lambda: w.now, stream_adapter=adapter))

    @app.get("/test-login/<int:uid>")
    def test_login(uid):
        login_user(db.session.get(User, uid))
        return {"csrf": generate_csrf()}

    w.owner, w.other = app.test_client(), app.test_client()
    w.csrf = w.owner.get(f"/test-login/{w.owner_id}", base_url=ORIGIN).json["csrf"]
    w.other_csrf = w.other.get(f"/test-login/{w.other_id}", base_url=ORIGIN).json["csrf"]
    command = {"command": "start_live_session", "request_id": str(uuid.uuid4()), "expected_revision": 0,
               "payload": {"dictation_id": w.did, "profile_revision_id": str(uuid.uuid4()),
                           "capture_mime": "audio/pcm"}}
    started = w.owner.post("/api/clinic/v1/parity/commands", json=command, base_url=ORIGIN, headers={
        "X-CSRF-Token": w.csrf, "X-Live-Client-Id": BEGIN["client_id"], "Idempotency-Key": str(uuid.uuid4()),
    })
    assert started.status_code == 200, started.json
    with app.app_context():
        row = live_session.ClinicLiveSession.query.filter_by(dictation_id=w.did).one()
        assert (row.client_id, row.holder_generation) == (BEGIN["client_id"], BEGIN["generation"])
        w.sid = row.id
    w.url = f"{routes_live.PREFIX}/sessions/{w.sid}/stream"
    yield w
    if w.peer is not None:
        w.peer.close()


def _headers(token, **overrides):
    return {"Connection": "Upgrade", "Upgrade": "websocket", "Origin": ORIGIN,
            "Sec-WebSocket-Version": "13", "Sec-WebSocket-Key": b64encode(b"0123456789abcdef").decode(),
            "Sec-WebSocket-Protocol": f"{stream.PROTOCOL}, {token}", **overrides}


class Wire:
    def __init__(self, sock):
        self.sock = sock
        self.ws = Connection(ConnectionType.CLIENT)
        self.events = iter(())

    def send(self, payload):
        cls = BytesMessage if isinstance(payload, bytes) else TextMessage
        self.sock.sendall(self.ws.send(cls(data=payload)))

    def receive(self):
        while True:
            for event in self.events:
                if isinstance(event, TextMessage):
                    return json.loads(event.data)
                if isinstance(event, CloseConnection):
                    return None
            data = self.sock.recv(65536)
            if not data:
                return None
            self.ws.receive_data(data)
            self.events = self.ws.events()


@contextmanager
def _wire(w, data=BEGIN, *, pipelined_audio=None):
    server, peer = socket.socketpair()
    with server, peer, ThreadPoolExecutor(max_workers=1) as executor:
        peer.settimeout(5)
        response = w.owner.get(w.url, base_url=ORIGIN, headers=_headers(w.csrf), buffered=False,
                               environ_overrides={"gunicorn.socket": server})
        assert response.status_code == 101, response.status
        assert response.headers["Sec-WebSocket-Protocol"] == stream.PROTOCOL
        assert response.headers["Cache-Control"] == "private, no-store"
        running = executor.submit(list, response.response)
        wire = Wire(peer)
        try:
            start = wire.ws.send(TextMessage(data=json.dumps(data)))
            if pipelined_audio is not None:
                start += wire.ws.send(BytesMessage(data=pipelined_audio))
            peer.sendall(start)
            yield wire
        finally:
            peer.close()
            running.result(timeout=5)
            response.close()


def _persisted(w):
    with w.app.app_context():
        row = db.session.get(live_session.ClinicLiveSession, w.sid)
        phrases = [(item.id, item.audio_size, item.transcript_revision_id) for item in
                   live_phrases.ClinicLivePhrase.query.filter_by(session_id=w.sid).all()]
        texts = [item.body for item in ClinicTranscriptRevision.query.filter_by(dictation_id=w.did).all()]
        return (row.ack_seq, row.audio_offset_ms), phrases, texts


def _send_audio(wire, session_id):
    assert wire.receive()["status"] == "ready"
    wire.send(PCM)
    assert wire.receive() == {"status": "partial", "session_id": session_id,
                              "generation": 1, "sequence": 1, "text": "Guten"}


def test_upgrade_rejects_foreign_session_object_origin_and_csrf(world):
    w = world
    assert w.app.test_client().get(w.url, base_url=ORIGIN, headers=_headers(w.csrf)).status_code == 401
    assert w.other.get(w.url, base_url=ORIGIN, headers=_headers(w.other_csrf)).status_code == 403
    for origin in ("", "null", "https://localhost:8899", "http://localhost", "http://foreign:8899"):
        response = w.owner.get(w.url, base_url=ORIGIN, headers=_headers(w.csrf, Origin=origin))
        assert (response.status_code, response.json["code"]) == (403, "FORBIDDEN")
    assert w.owner.get(w.url, base_url=ORIGIN, headers=_headers("invalid")).status_code == 403
    w.permitted = False
    response = w.owner.get(w.url, base_url=ORIGIN, headers=_headers(w.csrf))
    assert (response.status_code, response.json["code"]) == (403, "POLICY_DENIED")
    assert w.adapter_calls == 0


@pytest.mark.parametrize("change,code", [
    ("tab", "POLICY_DENIED"), ("generation", "POLICY_DENIED"), ("unfenced", "POLICY_DENIED"),
    ("paused", "REVISION_CONFLICT"), ("expired", "REVISION_CONFLICT"), ("sequence", "REVISION_CONFLICT"),
])
def test_audio_rejected_before_connecting_adapter(world, change, code):
    w = world
    data = dict(BEGIN)
    if change == "tab":
        data["client_id"] = "second-tab"
    elif change == "generation":
        data["generation"] += 1
    elif change == "sequence":
        data["after_sequence"] += 1
    elif change == "expired":
        w.now += timedelta(seconds=61)
    else:
        with w.app.app_context():
            row = db.session.get(live_session.ClinicLiveSession, w.sid)
            if change == "paused":
                row.state = live_session.PAUSED
            else:
                row.holder_generation = None
            db.session.commit()
    # Even a client ignoring the ready barrier cannot send these bytes to ASR.
    with _wire(w, data, pipelined_audio=PCM) as wire:
        assert wire.receive()["code"] == code
    assert w.adapter_calls == 0
    assert _persisted(w) == ((0, 0), [], [])


def test_partial_does_not_save_final_is_durable_and_resume_replays_once(world):
    w = world
    w.peer = ManualSpeaches()
    with _wire(w) as wire:
        _send_audio(wire, w.sid)
        assert _persisted(w) == ((0, 0), [], [])
        wire.send(PCM)
        wire.send("finish")
        saved = wire.receive()
        assert saved["status"] == "server_saved"
        assert (saved["session_id"], saved["generation"], saved["sequence"], saved["audio_end_ms"]) == (
            w.sid, 1, 1, 200)
        assert _persisted(w) == ((1, 200), [(saved["phrase_id"], len(PCM) * 2 + 44,
                                              saved["transcript_revision_id"])], ["Guten Tag"])
    assert w.peer.audio == PCM * 2
    assert w.peer.session["session"]["turn_detection"] is None

    resumed = w.owner.post(f"{routes_live.PREFIX}/dictations/{w.did}/resume", base_url=ORIGIN,
                           headers={"X-CSRF-Token": w.csrf}, json=BEGIN)
    assert resumed.status_code == 200
    assert resumed.json["events"][0]["phrase_id"] == saved["phrase_id"]
    assert resumed.json["events"][0]["event_id"] == saved["event_id"]
    base = f"{routes_live.PREFIX}/sessions/{w.sid}"
    assert w.owner.post(base + "/continue", base_url=ORIGIN, headers={"X-CSRF-Token": w.csrf},
                        json={"client_id": BEGIN["client_id"], "generation": 1}).status_code == 200
    with _wire(w) as wire:
        assert wire.receive()["code"] == "REVISION_CONFLICT"
    legacy = w.owner.post(base + "/phrases", base_url=ORIGIN, headers={"X-CSRF-Token": w.csrf}, json={
        "client_id": BEGIN["client_id"], "generation": 1, "sequence": 2, "event_id": "legacy",
        "audio_start_ms": 200, "audio_end_ms": 300, "audio_b64": b64encode(PCM).decode(),
        "audio_revision_id": saved["audio_revision_id"], "transcript_revision_id": saved["transcript_revision_id"],
        "text_hash": "not used",
    })
    assert (legacy.status_code, legacy.json["code"]) == (403, "POLICY_DENIED")
    assert w.adapter_calls == 1
    assert len(_persisted(w)[1]) == 1


@pytest.mark.parametrize("failure,code", [
    ("storage", "DEPENDENCY_UNAVAILABLE"), ("database", "DEPENDENCY_UNAVAILABLE"),
    ("changed_holder", "POLICY_DENIED"), ("changed_checkpoint", "REVISION_CONFLICT"),
    ("signed_out", "POLICY_DENIED"),
])
def test_final_never_acknowledges_failed_commit(world, monkeypatch, failure, code):
    w = world
    w.peer = ManualSpeaches()
    with _wire(w) as wire:
        _send_audio(wire, w.sid)
        with monkeypatch.context() as patch:
            if failure == "storage":
                def fail_publish(*_args):
                    raise OSError("synthetic write failure")
                patch.setattr(IngestService, "_publish", fail_publish)
            elif failure == "database":
                def fail_commit():
                    raise OperationalError(None, None, RuntimeError("synthetic commit failure"))
                patch.setattr(db.session, "commit", fail_commit)
            else:
                with w.app.app_context():
                    row = db.session.get(live_session.ClinicLiveSession, w.sid)
                    if failure == "changed_holder":
                        row.holder_generation += 1
                    elif failure == "changed_checkpoint":
                        row.last_activity_at += timedelta(seconds=1)
                    else:
                        db.session.get(User, w.owner_id).end_all_sessions()
                    db.session.commit()
            wire.send("finish")
            result = wire.receive()
            assert (result["status"], result["code"]) == ("error", code)
            assert wire.receive() is None
    assert _persisted(w) == ((0, 0), [], [])


@pytest.mark.parametrize("mode", ["batch", "respond"])
def test_incompatible_peer_fails_before_accepting_audio(world, mode):
    w = world
    w.peer = ManualSpeaches(mode)
    with _wire(w) as wire:
        result = wire.receive()
        assert (result["status"], result["code"]) == ("error", "CAPABILITY_UNAVAILABLE")
    assert not w.peer.audio
    assert _persisted(w) == ((0, 0), [], [])


def test_audio_after_fence_change_never_reaches_asr(world):
    w = world
    w.peer = ManualSpeaches()
    with _wire(w) as wire:
        _send_audio(wire, w.sid)
        with w.app.app_context():
            row = db.session.get(live_session.ClinicLiveSession, w.sid)
            row.client_id = "next-tab"
            row.holder_generation = 2
            db.session.commit()
        wire.send(PCM)
        assert wire.receive()["code"] == "POLICY_DENIED"
    assert w.peer.audio == PCM
    assert _persisted(w) == ((0, 0), [], [])


def test_disconnect_and_oversized_frame_leave_no_receipt(world):
    w = world
    w.peer = ManualSpeaches()
    with _wire(w) as wire:
        _send_audio(wire, w.sid)
    w.peer.close()
    w.peer = ManualSpeaches()
    with _wire(w) as wire:
        assert wire.receive()["status"] == "ready"
        wire.send(PCM + b"\x00\x00")
        result = wire.receive()
        assert (result["status"], result["code"]) == ("error", "INVALID_PAYLOAD")
    assert not w.peer.audio
    assert _persisted(w) == ((0, 0), [], [])


def test_timeout_closes_the_turn_without_a_receipt(world, monkeypatch):
    w = world
    w.peer = ManualSpeaches()
    monkeypatch.setattr(stream, "TURN_TIMEOUT", 1)
    with _wire(w) as wire:
        assert wire.receive()["status"] == "ready"
        result = wire.receive()
        assert (result["status"], result["code"]) == ("error", "DEPENDENCY_UNAVAILABLE")
        assert wire.receive() is None
    assert not w.peer.audio
    assert _persisted(w) == ((0, 0), [], [])


def test_duplicate_open_turns_can_only_commit_one_phrase(world):
    w = world
    first_peer = w.peer = ManualSpeaches()
    try:
        with _wire(w) as first:
            _send_audio(first, w.sid)
            w.peer = ManualSpeaches()
            with _wire(w) as second:
                _send_audio(second, w.sid)
                first.send("finish")
                second.send("finish")
                results = (first.receive(), second.receive())
        assert sorted(result["status"] for result in results) == ["error", "server_saved"]
        assert next(result["code"] for result in results if result["status"] == "error") == "REVISION_CONFLICT"
        assert len(_persisted(w)[1]) == 1
    finally:
        first_peer.close()
