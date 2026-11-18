"""Live session HTTP and command slice (PAR-LIVE-001/003/004), synthetic data only.

Covers start/stop through the parity command contract, pause/continue, durable
phrase append, resume with bounded events, actor/CSRF/policy guards, stale
versions and storage failure. Service rules stay in their own test files.
"""

import base64
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from flask import Flask, g, jsonify
from flask_login import LoginManager, login_user
from flask_wtf.csrf import CSRFProtect, generate_csrf

from src.app import app as real_app
from src.clinic import audio_revisions, live_phrases as phrases, live_session as live, revisions, routes_live
from src.clinic.ingest import IngestService
from src.clinic.live_drafts import LiveDrafts
from src.clinic.models import ClinicDictation, ClinicOriginal
from src.clinic.parity_dispatch import CommandProblem, ParityDispatch
from src.database import db
from src.models import Recording, User

NOW = datetime(2026, 11, 18, 9, 0, tzinfo=timezone.utc)
COMMANDS = "/api/clinic/v1/parity/commands"
LIVE = routes_live.PREFIX


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("CLINIC_MODE", "1")
    flask = Flask(__name__)
    flask.config.update(TESTING=True, SECRET_KEY="synthetic",
                        SQLALCHEMY_DATABASE_URI=real_app.config["SQLALCHEMY_DATABASE_URI"])
    db.init_app(flask)
    login = LoginManager(flask)
    CSRFProtect(flask)
    login.user_loader(lambda uid: db.session.get(User, int(uid)))
    with flask.app_context():
        live.ClinicLiveSession.__table__.create(db.engine, checkfirst=True)
        phrases.create_tables()
        suffix = uuid.uuid4().hex[:10]
        owner = User(username="live" + suffix, email=suffix + "@example.invalid", password="x")
        other = User(username="oth" + suffix, email="o" + suffix + "@example.invalid", password="x")
        db.session.add_all([owner, other])
        db.session.flush()
        recording = Recording(user_id=owner.id, title="synthetic live", status="PENDING")
        db.session.add(recording)
        db.session.flush()
        dictation = ClinicDictation(recording_id=recording.id, author_user_id=owner.id)
        db.session.add(dictation)
        db.session.flush()
        original = ClinicOriginal(dictation_id=dictation.id, content_hash="a" * 64, size_bytes=10,
                                  storage_key="synthetic/original", receipt="synthetic",
                                  origin_ref="synthetic")
        db.session.add(original)
        db.session.flush()
        audio_revision = audio_revisions.init_audio_revision(dictation, original, 9000)
        transcript = revisions.add_asr_revision(dictation, original, "Eine Phrase", "local-test")
        db.session.commit()
        ids = dict(owner=owner.id, other=other.id, dictation=dictation.id,
                   audio_revision=audio_revision.id, transcript=transcript.id,
                   text_hash=transcript.normalised_text_hash)

        clock = {"now": NOW}
        store = IngestService(tmp_path)
        receipts = {}

        def once(scope, digest, operation):
            if scope in receipts:
                if receipts[scope][0] != digest:
                    raise CommandProblem("IDEMPOTENCY_CONFLICT")
                return receipts[scope][1]
            try:
                result = operation()
                db.session.commit()
            except Exception:
                db.session.rollback()
                raise
            receipts[scope] = digest, result
            return result

        def allowed(actor, dictation_id):
            return actor == ids["owner"] and dictation_id == ids["dictation"]

        dispatch = ParityDispatch(once)
        routes_live.register_live_commands(dispatch, allowed, lambda: clock["now"])
        drafts = LiveDrafts()
        flask.register_blueprint(dispatch.blueprint)
        flask.register_blueprint(routes_live.create_blueprint(
            allowed, store, drafts, lambda: clock["now"]))

        @flask.before_request
        def _fresh_request_state():  # the test keeps one app context open; drop per-request caches
            g.pop("csrf_token", None)
            g.pop("_login_user", None)

        @flask.get("/login/<int:uid>")
        def _login(uid):
            login_user(db.session.get(User, uid))
            return jsonify(csrf=generate_csrf())

        def client(user_id):
            c = flask.test_client()
            r = c.get(f"/login/{user_id}")
            return c, {"X-CSRF-Token": r.json["csrf"]}

        yield dict(ids, client=client, clock=clock, store=store, drafts=drafts)
        db.session.rollback()
        db.session.remove()
        phrases.ClinicLivePhrase.__table__.drop(db.engine, checkfirst=True)


def _command(w, command, payload, revision=0):
    return {"command": command, "request_id": str(uuid.uuid4()), "expected_revision": revision,
            "payload": payload}


def _start(w, c, h, key="start-key-1"):
    body = _command(w, "start_live_session", {"dictation_id": w["dictation"],
                                              "profile_revision_id": str(uuid.uuid4()),
                                              "capture_mime": "audio/webm"})
    return c.post(COMMANDS, headers=dict(h, **{"Idempotency-Key": key}), json=body), body


def _phrase(w, sequence=1, **changes):
    audio = b"synthetic-audio-%d" % sequence
    data = {"generation": 1, "sequence": sequence, "event_id": f"event-{sequence}",
            "audio_start_ms": (sequence - 1) * 1000, "audio_end_ms": sequence * 1000,
            "audio_b64": base64.b64encode(audio).decode(), "audio_revision_id": w["audio_revision"],
            "transcript_revision_id": w["transcript"], "text_hash": w["text_hash"]}
    data.update(changes)
    return data


def _open_session(w):
    c, h = w["client"](w["owner"])
    first, _ = _start(w, c, h)
    assert first.status_code == 200
    row = live.ClinicLiveSession.query.filter_by(dictation_id=w["dictation"]).one()
    return c, h, row.id


def test_start_replay_stale_and_policy(world):
    w = world
    c, h = w["client"](w["owner"])
    first, body = _start(w, c, h)
    assert first.status_code == 200 and first.json["status"] == "committed" and first.json["revision"] == 1
    assert c.post(COMMANDS, headers=dict(h, **{"Idempotency-Key": "start-key-1"}), json=body).json == first.json
    again, _ = _start(w, c, h, key="start-key-2")
    assert (again.status_code, again.json["code"]) == (409, "REVISION_CONFLICT")
    oc, oh = w["client"](w["other"])
    denied, _ = _start(w, oc, oh, key="start-key-3")
    assert (denied.status_code, denied.json["code"]) == (403, "POLICY_DENIED")
    assert live.ClinicLiveSession.query.count() == 1
    no_csrf = c.post(COMMANDS, headers={"Idempotency-Key": "start-key-4"}, json=body)
    assert no_csrf.status_code == 403  # a cookie write without a CSRF token never reaches the handler


def test_phrase_is_server_saved_only_when_durable(world):
    w = world
    c, h, sid = _open_session(w)
    w["clock"]["now"] = NOW + timedelta(seconds=1)
    ok = c.post(f"{LIVE}/sessions/{sid}/phrases", headers=h, json=_phrase(w))
    assert ok.status_code == 200 and ok.json["status"] == "server_saved"
    assert (ok.json["generation"], ok.json["sequence"]) == (1, 1)
    assert phrases.ClinicLivePhrase.query.count() == 1
    assert c.post(f"{LIVE}/sessions/{sid}/phrases", headers=h, json=_phrase(w)).json == ok.json
    stale = c.post(f"{LIVE}/sessions/{sid}/phrases", headers=h, json=_phrase(w, sequence=3))
    assert (stale.status_code, stale.json["code"]) == (409, "REVISION_CONFLICT")
    assert phrases.ClinicLivePhrase.query.count() == 1


def test_phrase_rejects_actor_fields_foreign_user_and_missing_csrf(world):
    w = world
    c, h, sid = _open_session(w)
    url = f"{LIVE}/sessions/{sid}/phrases"
    for extra in ({"user_id": w["other"]}, {"role": "admin"}, {"actor": 1}):
        assert c.post(url, headers=h, json=dict(_phrase(w), **extra)).status_code == 400
    assert c.post(url, headers=h, json=_phrase(w, audio_b64="***")).status_code == 400
    c.application.config["WTF_CSRF_CHECK_DEFAULT"] = False  # reach the endpoint guard itself
    assert c.post(url, json=_phrase(w)).status_code == 403
    oc, oh = w["client"](w["other"])
    denied = oc.post(url, headers=oh, json=_phrase(w))
    assert (denied.status_code, denied.json["code"]) == (403, "POLICY_DENIED")
    assert oc.post(f"{LIVE}/sessions/{sid}/pause", headers=oh, json={}).status_code == 403
    assert phrases.ClinicLivePhrase.query.count() == 0


def test_pause_continue_and_unauthenticated(world):
    w = world
    c, h, sid = _open_session(w)
    paused = c.post(f"{LIVE}/sessions/{sid}/pause", headers=h, json={})
    assert paused.status_code == 200 and paused.json["state"] == live.PAUSED
    assert c.post(f"{LIVE}/sessions/{sid}/continue", headers=h, json={}).json["state"] == live.RECORDING
    assert c.post(f"{LIVE}/sessions/{sid}/pause", headers=h, json={"user_id": 1}).status_code == 400
    c.application.config["WTF_CSRF_CHECK_DEFAULT"] = False
    bare = c.application.test_client().post(f"{LIVE}/sessions/{sid}/pause", json={})
    assert bare.status_code == 401


def test_resume_keeps_generation_and_sequence_with_bounded_events(world, monkeypatch):
    w = world
    c, h, sid = _open_session(w)
    for n in (1, 2):
        w["clock"]["now"] = NOW + timedelta(seconds=n)
        assert c.post(f"{LIVE}/sessions/{sid}/phrases", headers=h,
                      json=_phrase(w, sequence=n, generation=n)).status_code == 200
    w["clock"]["now"] = NOW + timedelta(minutes=5)
    monkeypatch.setattr(routes_live, "MAX_EVENTS", 1)
    url = f"{LIVE}/dictations/{w['dictation']}/resume"
    back = c.post(url, headers=h, json={"after_sequence": 0})
    assert back.status_code == 200
    assert (back.json["generation"], back.json["sequence"], back.json["state"]) == (2, 2, live.PAUSED)
    assert [e["sequence"] for e in back.json["events"]] == [1]
    assert all(e["status"] == "server_saved" for e in back.json["events"])
    tail = c.post(url, headers=h, json={"after_sequence": 1})
    assert [e["sequence"] for e in tail.json["events"]] == [2]
    assert c.post(url, headers=h, json={"after_sequence": -1}).status_code == 400
    oc, oh = w["client"](w["other"])
    assert oc.post(url, headers=oh, json={}).status_code == 403


def test_stop_command_requires_current_sequence_and_is_idempotent(world):
    w = world
    c, h, sid = _open_session(w)
    w["clock"]["now"] = NOW + timedelta(seconds=1)
    assert c.post(f"{LIVE}/sessions/{sid}/phrases", headers=h, json=_phrase(w)).status_code == 200
    payload = {"session_id": sid, "generation": 1, "last_sequence": 1}
    stale = c.post(COMMANDS, headers=dict(h, **{"Idempotency-Key": "stop-key-1"}),
                   json=_command(w, "stop_live_session", dict(payload, last_sequence=0), 1))
    assert (stale.status_code, stale.json["code"]) == (409, "REVISION_CONFLICT")
    wrong_revision = c.post(COMMANDS, headers=dict(h, **{"Idempotency-Key": "stop-key-2"}),
                            json=_command(w, "stop_live_session", payload, 1))
    assert (wrong_revision.status_code, wrong_revision.json["code"]) == (409, "REVISION_CONFLICT")
    body = _command(w, "stop_live_session", payload, 2)
    done = c.post(COMMANDS, headers=dict(h, **{"Idempotency-Key": "stop-key-3"}), json=body)
    assert done.status_code == 200 and done.json["status"] == "committed"
    assert c.post(COMMANDS, headers=dict(h, **{"Idempotency-Key": "stop-key-3"}), json=body).json == done.json
    assert db.session.get(live.ClinicLiveSession, sid).state == live.ENDED
    late = c.post(f"{LIVE}/sessions/{sid}/phrases", headers=h, json=_phrase(w, sequence=2))
    assert (late.status_code, late.json["code"]) == (409, "REVISION_CONFLICT")
    oc, oh = w["client"](w["other"])
    other = oc.post(COMMANDS, headers=dict(oh, **{"Idempotency-Key": "stop-key-4"}), json=body)
    assert (other.status_code, other.json["code"]) == (403, "POLICY_DENIED")


def test_storage_failure_returns_no_server_saved_and_retry_works(world, monkeypatch):
    w = world
    c, h, sid = _open_session(w)
    w["clock"]["now"] = NOW + timedelta(seconds=1)

    def unavailable(*_args):
        raise OSError("synthetic disk error")

    with monkeypatch.context() as patcher:
        patcher.setattr(w["store"], "_publish", unavailable)
        failed = c.post(f"{LIVE}/sessions/{sid}/phrases", headers=h, json=_phrase(w))
    assert (failed.status_code, failed.json["code"]) == (503, "DEPENDENCY_UNAVAILABLE")
    assert "status" not in failed.json or failed.json["status"] != "server_saved"
    assert phrases.ClinicLivePhrase.query.count() == 0
    retry = c.post(f"{LIVE}/sessions/{sid}/phrases", headers=h, json=_phrase(w))
    assert retry.json["status"] == "server_saved"


def test_draft_switch_uses_session_actor(world):
    w = world
    c, h = w["client"](w["owner"])
    w["drafts"].create(w["owner"], "Entwurf", NOW, w["dictation"])
    ok = c.post(f"{LIVE}/drafts/{w['dictation']}/switch", headers=h, json={"tab_id": "tab-1"})
    assert ok.status_code == 200 and ok.json["draft_id"] == w["dictation"]
    assert c.post(f"{LIVE}/drafts/{w['dictation']}/switch", headers=h,
                  json={"tab_id": "t", "owner_id": 9}).status_code == 400
    oc, oh = w["client"](w["other"])
    assert oc.post(f"{LIVE}/drafts/{w['dictation']}/switch", headers=oh,
                   json={"tab_id": "t"}).status_code == 403
