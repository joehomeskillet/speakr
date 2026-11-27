"""Durable two-tab draft selection against the real live HTTP and phrase services."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from flask import Flask, jsonify
from flask_login import LoginManager, login_user
from flask_wtf.csrf import CSRFProtect, generate_csrf
from sqlalchemy import create_engine, text

from src.app import app as real_app
from src.clinic import audio_revisions, live_phrases, live_session, revisions, routes_live
from src.clinic.draft_store import DraftStore
from src.clinic.ingest import IngestService
from src.clinic.live_drafts import HolderFence, select_draft
from src.clinic.models import ClinicDictation, ClinicOriginal
from src.clinic.parity_dispatch import ParityDispatch
from src.database import db
from src.models import Recording, User
from test_wp_live_api import COMMANDS, NOW, _command, _phrase

LIVE = routes_live.PREFIX


@pytest.fixture
def scene(clinic_on, world, tmp_path):
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="synthetic",
                      SQLALCHEMY_DATABASE_URI=real_app.config["SQLALCHEMY_DATABASE_URI"])
    db.init_app(app)
    CSRFProtect(app)
    login = LoginManager(app)
    login.user_loader(lambda uid: db.session.get(User, int(uid)))
    author = world["user_a"]
    draft_store = DraftStore()
    with app.app_context():
        live_phrases.create_tables()
        extra = Recording(user_id=author, title="Draft B", status="PENDING")
        db.session.add(extra)
        db.session.flush()
        drafts = []
        for recording_id in (world["rec_a"], extra.id):
            row = ClinicDictation(recording_id=recording_id, author_user_id=author)
            db.session.add(row)
            db.session.flush()
            original = ClinicOriginal(dictation_id=row.id, content_hash="a" * 64, size_bytes=10,
                                      storage_key="synthetic/original", receipt="synthetic",
                                      origin_ref="synthetic")
            db.session.add(original)
            db.session.flush()
            audio = audio_revisions.init_audio_revision(row, original, 9000)
            text = revisions.add_asr_revision(row, original, "Synthetic", "local-test")
            drafts.append(dict(dictation=row.id, audio_revision=audio.id, transcript=text.id,
                               text_hash=text.normalised_text_hash))
            draft_store.create(author, row.id, "Draft", NOW)

    @app.get("/login")
    def sign_in():
        login_user(db.session.get(User, author))
        return jsonify(csrf=generate_csrf())

    ids = {draft["dictation"] for draft in drafts}

    def allowed(uid, key):
        return uid == author and key in ids

    def once(scope, digest, operation):
        try:
            result = operation()
            db.session.commit()
        except Exception:
            db.session.rollback()
            raise
        return result

    dispatch = ParityDispatch(once)
    routes_live.register_live_commands(dispatch, allowed, now=lambda: NOW)
    app.register_blueprint(dispatch.blueprint)
    app.register_blueprint(routes_live.create_blueprint(
        allowed, IngestService(tmp_path), drafts=draft_store, now=lambda: NOW))

    def client():
        browser = app.test_client()
        csrf = browser.get("/login").json["csrf"]
        def post(path, data, **headers):
            return browser.post(path if path == COMMANDS else f"{LIVE}{path}", json=data,
                                headers={"X-CSRF-Token": csrf, **headers})
        return post

    yield app, author, drafts, client
    with app.app_context():
        db.engine.dispose()


def test_existing_session_schema_adds_fence_without_rewriting_rows(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE clinic_live_session (id TEXT PRIMARY KEY)"))
            connection.execute(text("INSERT INTO clinic_live_session (id) VALUES ('existing')"))
        live_session.create_tables(engine)
        live_session.create_tables(engine)
        with engine.connect() as connection:
            assert connection.execute(text("SELECT id, holder_generation FROM clinic_live_session")).one() == (
                "existing", None,
            )
    finally:
        engine.dispose()


def _select(post, draft, tab, **extra):
    return post(f"/drafts/{draft['dictation']}/switch", {"tab_id": tab, **extra})


def _fence(position, tab):
    return {"client_id": tab, "generation": position["generation"]}


@pytest.mark.parametrize("release", [False, True])
def test_switch_requires_current_holder_and_fences_old_events(scene, release):
    app, author, (a, b), client = scene
    tab1, tab2 = client(), client()
    selected = _select(tab1, a, "tab-1")
    assert selected.status_code == 200
    pos_a = selected.json
    assert pos_a["state"] == live_session.PAUSED
    sid_a = pos_a["session_id"]
    fence_a = _fence(pos_a, "tab-1")
    assert tab1(f"/sessions/{sid_a}/continue", fence_a).json["state"] == live_session.RECORDING
    assert tab1(f"/sessions/{sid_a}/phrases", _phrase(a, **fence_a)).status_code == 200

    assert _select(tab2, a, "tab-1").status_code == 409
    assert _select(tab2, b, "tab-2").status_code == 409
    assert _select(tab2, b, "tab-2", acknowledged_holder=dict(
        fence_a, session_id=sid_a, generation=fence_a["generation"] + 1)).status_code == 403
    with app.app_context():
        assert live_session.ClinicLiveSession.query.filter_by(author_user_id=author).count() == 1
        assert db.session.get(live_session.ClinicLiveSession, sid_a).state == live_session.RECORDING
        with pytest.raises(live_session.LiveSessionError, match="active_session"):
            live_session.start(b["dictation"], author, NOW)

    if release:
        assert tab1(f"/sessions/{sid_a}/pause", dict(fence_a, release=True)).status_code == 200
        selected = _select(tab2, b, "tab-2")
    else:
        selected = _select(tab1, b, "tab-2", acknowledged_holder=dict(fence_a, session_id=sid_a))
    assert selected.status_code == 200
    pos_b = selected.json
    sid_b = pos_b["session_id"]
    fence_b = _fence(pos_b, "tab-2")
    assert pos_b["generation"] > pos_a["generation"]
    assert tab2(f"/sessions/{sid_b}/continue", fence_b).json["state"] == live_session.RECORDING
    assert tab1(f"/sessions/{sid_a}/continue", fence_a).status_code == 403
    assert _select(tab1, a, "tab-1", acknowledged_holder=dict(fence_a, session_id=sid_a)).status_code == 403
    assert tab1(f"/dictations/{a['dictation']}/resume", fence_a).status_code == 403
    assert tab1(f"/sessions/{sid_a}/phrases", _phrase(a, sequence=2, **fence_a)).status_code == 403
    assert tab2(f"/sessions/{sid_b}/phrases", _phrase(b, **dict(
        fence_b, generation=fence_a["generation"]))).status_code == 403
    assert tab1(f"/sessions/{sid_b}/phrases", _phrase(b, **dict(fence_b, client_id="tab-1"))).status_code == 403
    assert tab2(f"/sessions/{sid_b}/phrases", _phrase(b, **fence_b)).status_code == 200
    stopped = _command({}, "stop_live_session", {
        "session_id": sid_b, "generation": fence_b["generation"], "last_sequence": 1,
    }, revision=2)
    assert tab1(COMMANDS, stopped, **{
        "Idempotency-Key": "stop-old-tab", "X-Live-Client-Id": "tab-1",
    }).status_code == 403
    with app.app_context():
        rows = live_session.ClinicLiveSession.query.filter_by(author_user_id=author).all()
        assert {row.dictation_id: (row.state, row.ack_seq) for row in rows} == {
            a["dictation"]: (live_session.PAUSED, 1), b["dictation"]: (live_session.RECORDING, 1),
        }
        assert {row.dictation_id for row in live_phrases.ClinicLivePhrase.query.filter(
            live_phrases.ClinicLivePhrase.session_id.in_([sid_a, sid_b]))} == {a["dictation"], b["dictation"]}


def test_competing_workers_cannot_claim_two_drafts(scene):
    app, author, drafts, _client = scene
    barrier = Barrier(2)

    def claim(index):
        with app.app_context():
            barrier.wait(timeout=10)
            try:
                select_draft(author, drafts[index]["dictation"], f"tab-{index}", NOW)
                db.session.commit()
                return "claimed"
            except live_session.LiveSessionError as error:
                db.session.rollback()
                return error.reason

    with ThreadPoolExecutor(max_workers=2) as workers:
        assert sorted(workers.map(claim, range(2))) == [live_session.ACTIVE_SESSION, "claimed"]
    with app.app_context():
        rows = live_session.ClinicLiveSession.query.filter_by(author_user_id=author).all()
        assert len(rows) == 1 and rows[0].state == live_session.PAUSED


def test_rolled_back_switch_preserves_both_drafts(scene):
    app, author, (a, b), _client = scene
    with app.app_context():
        _, old = select_draft(author, a["dictation"], "tab-1", NOW)
        fence = _fence({"generation": old.holder_generation}, "tab-1")
        sid = old.id
        live_session.continue_recording(sid, author, NOW, **fence)
        db.session.commit()
    with app.app_context():
        select_draft(author, b["dictation"], "tab-2", NOW,
                     acknowledged=HolderFence(sid, "tab-1", fence["generation"]))
        db.session.rollback()
    with app.app_context():
        assert live_session._open(b["dictation"]) is None
        assert live_session.heartbeat(sid, author, NOW, **fence).state == live_session.RECORDING
