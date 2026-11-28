"""Recovery pages, historical leases and authorized playback of durable final phrases."""

import wave
from datetime import datetime, timedelta, timezone
from io import BytesIO

import pytest
from flask import Flask, jsonify
from flask_login import LoginManager, login_user
from flask_wtf.csrf import CSRFProtect, generate_csrf

from src.app import app as real_app
from src.clinic import live_phrases, live_session, revisions, routes_live
from src.clinic.ingest import IngestService
from src.clinic.live_commit import commit_asr_phrase
from src.clinic.models import ClinicDictation
from src.database import db
from src.models import User

NOW = datetime(2026, 11, 27, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def recovery(clinic_on, world, tmp_path):
    flask = Flask(__name__)
    flask.config.update(TESTING=True, SECRET_KEY="synthetic", SESSION_PROTECTION=None,
                        SQLALCHEMY_DATABASE_URI=real_app.config["SQLALCHEMY_DATABASE_URI"])
    db.init_app(flask)
    login = LoginManager(flask)
    login.user_loader(lambda uid: db.session.get(User, int(uid)))
    CSRFProtect(flask)
    with flask.app_context():
        live_phrases.create_tables()
        drafts = [ClinicDictation(recording_id=world[f"rec_{key}"], author_user_id=world[f"user_{key}"])
                  for key in ("a", "b")]
        db.session.add_all(drafts)
        db.session.flush()
        dictation, foreign = [row.id for row in drafts]
        db.session.commit()

    owner, viewer = world["user_a"], world["user_b"]
    grants = {"edit": {(owner, dictation)}, "view": {(owner, dictation), (viewer, dictation), (owner, foreign)},
              "play": {(owner, dictation), (owner, foreign)}}
    store = IngestService(tmp_path)
    flask.register_blueprint(routes_live.create_blueprint(
        lambda actor, draft: (actor, draft) in grants["edit"], store, now=lambda: NOW,
        authorize_view=lambda actor, draft: (actor, draft) in grants["view"],
        authorize_play=lambda actor, draft: (actor, draft) in grants["play"]))

    @flask.get("/login/<int:uid>")
    def sign_in(uid):
        login_user(db.session.get(User, uid))
        return jsonify(csrf=generate_csrf())

    def client(uid):
        browser = flask.test_client()
        return browser, {"X-CSRF-Token": browser.get(f"/login/{uid}").json["csrf"]}

    return flask, store, grants, dictation, foreign, owner, viewer, client


def _wav(sequence):
    buffer = BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        audio.writeframes(sequence.to_bytes(2, "little") * 80)
    return buffer.getvalue()


def _text(sequence):
    return f"Phrase {sequence}\nZweite Zeile" if sequence == 51 else f"Phrase {sequence}"


def _save(store, dictation, sid, owner, sequence, prior=None):
    return commit_asr_phrase(
        store, dictation_id=dictation, session_id=sid, user_id=owner, client_id=None,
        now=NOW, generation=1, sequence=sequence, event_id=f"event-{sequence}",
        audio_start_ms=(sequence - 1) * 10, audio_end_ms=sequence * 10, audio=_wav(sequence),
        expected_audio_revision_id=prior.audio_revision_id if prior else None,
        expected_text_revision_id=prior.transcript_revision_id if prior else None,
        text=_text(sequence), model_ref="local-test")


def _resume(browser, headers, dictation, data):
    return browser.post(f"{routes_live.PREFIX}/dictations/{dictation}/resume", headers=headers, json=data)


def _snapshot(flask, sid):
    with flask.app_context():
        row = db.session.get(live_session.ClinicLiveSession, sid)
        return (row.state, row.end_reason, row.active_key, row.client_id, row.holder_generation,
                row.lock_expires_at, row.last_activity_at, row.ended_at)


def test_recovery_pages_and_immutable_playback(recovery, monkeypatch):
    flask, store, grants, dictation, foreign, owner, viewer, client = recovery
    with flask.app_context():
        sid = live_session.start(dictation, owner, NOW).id
        db.session.commit()
        receipts = []
        for sequence in range(1, 122):
            receipts.append(_save(store, dictation, sid, owner, sequence, receipts[-1] if receipts else None))
        draft = db.session.get(ClinicDictation, dictation)
        revisions.add_human_revision(draft, revisions.current_revision(draft), "Korrigiert.", owner)
        db.session.commit()

    browser, headers = client(owner)
    first = _resume(browser, headers, dictation, {})
    assert first.status_code == 200 and first.headers["Cache-Control"] == "private, no-store"
    assert first.json["state"] == live_session.PAUSED and not first.json["read_only"]
    assert first.json["previous_session_id"] is None
    assert first.json["next_cursor"] == {"session_id": sid, "after_sequence": 50}

    def no_recover(*args, **kwargs):
        pytest.fail("A recovery page must not acquire the speaker lock")

    monkeypatch.setattr(live_session, "recover", no_recover)
    snapshot = _snapshot(flask, sid)
    cursor = {"cursor": first.json["next_cursor"]}
    second = _resume(browser, headers, dictation, cursor).json
    assert _resume(browser, headers, dictation, cursor).json == second
    third = _resume(browser, headers, dictation, {"cursor": second["next_cursor"]}).json
    assert [len(page["events"]) for page in (first.json, second, third)] == [50, 50, 21]
    assert {page["sequence"] for page in (first.json, second, third)} == {121}
    assert second["read_only"] and third["read_only"] and third["next_cursor"] is None
    events = first.json["events"] + second["events"] + third["events"]
    assert [event["sequence"] for event in events] == list(range(1, 122))
    assert [event["text"] for event in events] == [_text(n) for n in range(1, 122)]
    assert [event["audio_revision_id"] for event in events] == [r.audio_revision_id for r in receipts]
    for after, count in ((71, 50), (121, 0), (2**80, 0)):
        page = _resume(browser, headers, dictation, {"session_id": sid, "after_sequence": after}).json
        assert len(page["events"]) == count and page["next_cursor"] is None
    assert _snapshot(flask, sid) == snapshot
    assert _resume(browser, headers, foreign, cursor).status_code == 403
    assert _resume(browser, headers, dictation, dict(cursor, after_sequence=121)).status_code == 400
    assert _resume(browser, {}, dictation, cursor).status_code in (400, 403)

    for event, sequence in ((events[0], 1), (events[-1], 121)):
        playback = browser.get(event["audio_url"])
        assert playback.status_code == 200 and playback.data == _wav(sequence)
        assert playback.mimetype == "audio/wav"
        assert playback.headers["Cache-Control"] == "private, no-store"
        assert playback.headers["Content-Security-Policy"] == "sandbox"
        with wave.open(BytesIO(playback.data), "rb") as audio:
            assert audio.readframes(80) == sequence.to_bytes(2, "little") * 80
    url = events[0]["audio_url"]
    head = browser.head(url)
    assert head.status_code == 200 and not head.data and head.content_length == len(_wav(1))
    partial = browser.get(url, headers={"Range": "bytes=1-4"})
    assert partial.status_code == 206 and partial.data == _wav(1)[1:5]
    assert partial.headers["Content-Range"] == f"bytes 1-4/{len(_wav(1))}"
    assert browser.get(url.replace(dictation, foreign)).status_code == 404
    assert flask.test_client().get(url).status_code == 401
    store.file_path(receipts[-1].audio_storage_key).unlink()
    assert browser.get(events[-1]["audio_url"]).status_code == 404

    read_only, read_headers = client(viewer)
    view = _resume(read_only, read_headers, dictation, {})
    assert view.status_code == 200 and view.json["read_only"]
    assert view.json["events"][0]["text"] == _text(1) and view.json["events"][0]["audio_url"] is None
    assert read_only.get(url).status_code == 404
    grants["play"].remove((owner, dictation))
    assert browser.get(url).status_code == 404
    grants["view"].remove((owner, dictation))
    assert _resume(browser, headers, dictation, cursor).status_code == 403


def test_empty_resume_distinguishes_edit_access_and_another_draft_holder(recovery):
    flask, _, _, dictation, foreign, owner, viewer, client = recovery
    browser, headers = client(owner)
    view, view_headers = client(viewer)
    for caller, csrf, read_only in ((browser, headers, False), (view, view_headers, True)):
        response = _resume(caller, csrf, dictation, {})
        assert response.status_code == 200
        assert response.json == {"session_id": None, "read_only": read_only}
    assert _resume(view, view_headers, foreign, {}).status_code == 403

    with flask.app_context():
        sid = live_session.start(foreign, owner, NOW).id
        db.session.commit()
    snapshot = _snapshot(flask, sid)
    response = _resume(browser, headers, dictation, {})
    assert response.status_code == 200
    assert response.json == {"session_id": None, "read_only": True}
    assert _snapshot(flask, sid) == snapshot


@pytest.mark.parametrize("holder", ["fenced", "revoked", "other", "expired_other"])
def test_resume_holder_conflict_reads_without_changing_lock(recovery, holder):
    flask, store, _, dictation, _, owner, viewer, client = recovery
    author = viewer if holder in {"other", "expired_other"} else owner
    with flask.app_context():
        sid = live_session.start(dictation, author, NOW).id
        db.session.commit()
        _save(store, dictation, sid, author, 1)
        row = db.session.get(live_session.ClinicLiveSession, sid)
        if holder in {"fenced", "revoked"}:
            row.client_id = "holder-tab"
            row.holder_generation = 1
            if holder == "revoked":
                live_session.revoke_holder(row)
        if holder == "expired_other":
            row.lock_expires_at = NOW - timedelta(seconds=1)
        db.session.commit()

    browser, headers = client(owner)
    snapshot = _snapshot(flask, sid)
    response = _resume(browser, headers, dictation, {})
    assert response.status_code == 200
    assert response.json["session_id"] == sid and response.json["read_only"] is True
    event, = response.json["events"]
    assert event["text"] == _text(1)
    assert browser.get(event["audio_url"]).data == _wav(1)
    assert _snapshot(flask, sid) == snapshot


def test_history_never_reopens_finished_or_expired_sessions(recovery):
    flask, store, _, dictation, _, owner, viewer, client = recovery
    later = NOW + timedelta(hours=1)
    with flask.app_context():
        expired = live_session.start(dictation, owner, NOW).id
        db.session.commit()
        phrase = _save(store, dictation, expired, owner, 1)
        finished = live_session.start(dictation, owner, later).id
        live_session.end(finished, owner, later)
        db.session.commit()

    browser, headers = client(owner)
    expired_snapshot = _snapshot(flask, expired)
    finished_snapshot = _snapshot(flask, finished)
    assert expired_snapshot[:3] == (live_session.ENDED, live_session.END_EXPIRED, None)
    assert finished_snapshot[:3] == (live_session.ENDED, live_session.END_FINISHED, None)
    latest = _resume(browser, headers, dictation, {}).json
    assert latest["session_id"] == finished and latest["previous_session_id"] == expired and latest["read_only"]
    assert latest["can_start"] is True
    view, view_headers = client(viewer)
    assert _resume(view, view_headers, dictation, {}).json["can_start"] is False
    older = _resume(browser, headers, dictation, {"session_id": latest["previous_session_id"]}).json
    assert older["previous_session_id"] is None and older["read_only"]
    assert older["events"][0]["phrase_id"] == phrase.phrase_id
    assert browser.get(older["events"][0]["audio_url"]).data == _wav(1)

    with flask.app_context():
        active = live_session.start(dictation, owner, later + timedelta(seconds=1)).id
        db.session.commit()
    active_snapshot = _snapshot(flask, active)
    for sid, previous in ((active, finished), (finished, expired), (expired, None)):
        page = _resume(browser, headers, dictation, {"session_id": sid}).json
        assert page["previous_session_id"] == previous and page["read_only"]
    assert _snapshot(flask, active) == active_snapshot
    assert _snapshot(flask, expired) == expired_snapshot
    assert _snapshot(flask, finished) == finished_snapshot
