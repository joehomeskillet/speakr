"""Durable workstation override store with versions and ceilings (PAR-CFG-001).

Exercises real SQLite persistence across separate Flask apps / fresh sessions:
- Missing override rows resolve through central defaults/locks/ceilings.
- Stale writes (including concurrent compare-and-set races) are rejected.
- Untrusted or cross-user workstation identifiers are rejected.
- Managed synthetic workstation vs unregistered workstation after a fresh session.
"""

from __future__ import annotations

import pytest
from flask import Flask, jsonify
from flask_login import LoginManager, login_user
from flask_wtf.csrf import CSRFProtect, generate_csrf

from src.clinic import config, registry
from src.clinic.last_selection import SelectionStore
from src.clinic.routes_preferences import PREFIX, PreferenceRoutes
from src.clinic.workstation_preferences import (
    ClinicWorkstation,
    ClinicWorkstationLock,
    ClinicWorkstationOverride,
    PreferenceError,
    StaleVersion,
    WorkstationPreferences,
    create_tables,
)
from src.database import db
from src.models import User


def _make_app(uri: str) -> Flask:
    flask_app = Flask(__name__)
    flask_app.config.update(
        TESTING=True,
        SECRET_KEY="synthetic-workstation-secret",
        SQLALCHEMY_DATABASE_URI=uri,
    )
    db.init_app(flask_app)
    return flask_app


@pytest.fixture
def apps(tmp_path):
    uri = "sqlite:///" + (tmp_path / "workstations.db").as_posix()
    first, second = _make_app(uri), _make_app(uri)
    with first.app_context():
        create_tables(bind=db.engine)
    return first, second


class _Account:
    def __init__(self, user_id: int, authenticated: bool = True) -> None:
        self.id = user_id
        self.is_authenticated = authenticated


def test_models_are_registered_and_create_tables_is_idempotent(tmp_path):
    assert "workstation_preferences" in registry.MODEL_MODULES
    assert ClinicWorkstation.__tablename__.startswith("clinic_")
    assert ClinicWorkstationOverride.__tablename__.startswith("clinic_")
    assert ClinicWorkstationLock.__tablename__.startswith("clinic_")
    uri = "sqlite:///" + (tmp_path / "idempotent.db").as_posix()
    flask_app = _make_app(uri)
    with flask_app.app_context():
        create_tables(bind=db.engine)
        create_tables(bind=db.engine)


def test_missing_row_resolves_through_central_defaults_locks_and_ceilings(apps, monkeypatch):
    monkeypatch.setenv("CLINIC_MODE", "1")
    first, second = apps
    user = _Account(10)
    with first.app_context():
        store = WorkstationPreferences()
        wid = store.issue(user)
        effective = store.effective(user, wid)
        assert effective.version == 0
        assert effective.values == {}
        assert effective.origins == {}
        assert effective.profile == config.client_profile()
        assert store.stored(user, wid) == {}

        store.lock("ui_theme", "desk")
        monkeypatch.setitem(config.SETTING_CEILINGS, "ui_language", ("public_sharing", "de"))

    with second.app_context():
        restarted = WorkstationPreferences()
        effective = restarted.effective(user, wid)
        assert effective.version == 0
        assert effective.values == {"ui_theme": "desk", "ui_language": "de"}
        assert effective.origins == {"ui_theme": "central", "ui_language": "ceiling"}
        assert restarted.stored(user, wid) == {}


def test_overrides_and_locks_persist_across_restart_and_unlock_restores_local(apps, monkeypatch):
    monkeypatch.setenv("CLINIC_MODE", "1")
    first, second = apps
    user = _Account(7)
    with first.app_context():
        store = WorkstationPreferences()
        wid = store.issue(user)
        v1 = store.apply(user, wid, {"ui_theme": "cards", "theme_dark_mode": True}, expected_version=0)
        assert v1 == 1
        assert store.cancel(user, wid, {"ui_theme": "desk"}, expected_version=1) == 1
        store.lock("ui_theme", "desk")

    with second.app_context():
        restarted = WorkstationPreferences()
        seen = restarted.effective(user, wid)
        assert seen.version == 1
        assert seen.values == {"ui_theme": "desk", "theme_dark_mode": True}
        assert seen.origins == {"ui_theme": "central", "theme_dark_mode": "workstation"}
        assert restarted.stored(user, wid) == {"ui_theme": "cards", "theme_dark_mode": True}

        restarted.unlock("ui_theme")
        v2 = restarted.apply(user, wid, {"editor_autosave": True}, expected_version=1)
        assert v2 == 2

    with first.app_context():
        final = WorkstationPreferences().effective(user, wid)
        assert final.version == 2
        assert final.values == {"ui_theme": "cards", "theme_dark_mode": True, "editor_autosave": True}
        assert final.origins["ui_theme"] == "workstation"


def test_stale_writes_and_concurrent_first_write_are_rejected(apps):
    first, second = apps
    user = _Account(3)
    with first.app_context():
        store = WorkstationPreferences()
        wid = store.issue(user)
        with pytest.raises(StaleVersion) as missing_stale:
            store.apply(user, wid, {"theme_dark_mode": True}, expected_version=1)
        assert missing_stale.value.version == 0

        # Simulate two racing writers both observing version 0.
        assert store.apply(user, wid, {"theme_dark_mode": True}, expected_version=0) == 1

    with second.app_context():
        other_worker = WorkstationPreferences()
        with pytest.raises(StaleVersion) as stale:
            other_worker.apply(user, wid, {"theme_dark_mode": False}, expected_version=0)
        assert stale.value.version == 1
        assert other_worker.stored(user, wid) == {"theme_dark_mode": True}

        assert other_worker.apply(user, wid, {"theme_dark_mode": False}, expected_version=1) == 2

    with first.app_context():
        with pytest.raises(StaleVersion) as stale2:
            WorkstationPreferences().apply(user, wid, {"editor_autosave": True}, expected_version=1)
        assert stale2.value.version == 2
        assert WorkstationPreferences().stored(user, wid) == {"theme_dark_mode": False}


def test_untrusted_and_foreign_workstations_are_rejected(apps):
    first, second = apps
    owner, stranger = _Account(1), _Account(2)
    with first.app_context():
        store = WorkstationPreferences()
        wid = store.issue(owner)
        store.apply(owner, wid, {"keyboard_shortcuts": False}, expected_version=0)

    with second.app_context():
        restarted = WorkstationPreferences()
        for bad_id in ("", "unregistered-ws", "managed_web_offline", "workstation_token", "x" * 100, None):
            with pytest.raises(PreferenceError) as exc:
                restarted.effective(owner, bad_id)  # type: ignore[arg-type]
            assert exc.value.code == "untrusted_workstation"
            with pytest.raises(PreferenceError) as exc_apply:
                restarted.apply(owner, bad_id, {"theme_dark_mode": True}, expected_version=0)  # type: ignore[arg-type]
            assert exc_apply.value.code == "untrusted_workstation"

        for op in (
            lambda: restarted.effective(stranger, wid),
            lambda: restarted.stored(stranger, wid),
            lambda: restarted.apply(stranger, wid, {"theme_dark_mode": True}, expected_version=1),
            lambda: restarted.cancel(stranger, wid, {"theme_dark_mode": True}, expected_version=1),
        ):
            with pytest.raises(PreferenceError) as denied:
                op()
            assert denied.value.code == "untrusted_workstation"


def test_dump_and_load_roundtrip_in_app_context(apps):
    first, second = apps
    user = _Account(5)
    with first.app_context():
        store = WorkstationPreferences()
        wid = store.issue(user)
        store.apply(user, wid, {"ui_theme": "cards"}, expected_version=0)
        store.lock("audio_player_position", "top")
        snap = store.dump()

    with second.app_context():
        reloaded = WorkstationPreferences.load(snap)
        seen = reloaded.effective(user, wid)
        assert seen.version == 1
        assert seen.values == {"ui_theme": "cards", "audio_player_position": "top"}
        assert seen.origins == {"ui_theme": "workstation", "audio_player_position": "central"}


def _route_app(uri: str) -> Flask:
    flask_app = _make_app(uri)
    login = LoginManager(flask_app)
    CSRFProtect(flask_app)
    login.user_loader(lambda uid: db.session.get(User, int(uid)))

    @flask_app.get("/login/<int:uid>")
    def _login(uid: int):
        login_user(db.session.get(User, uid))
        return jsonify(csrf=generate_csrf())

    routes = PreferenceRoutes(
        allowed_for=lambda _uid: None,  # type: ignore[arg-type, return-value]
        author_pool_for=lambda _uid: None,
        selections=SelectionStore(),
        workstations=WorkstationPreferences(),
    )
    flask_app.register_blueprint(routes.blueprint)
    return flask_app


def test_managed_vs_unregistered_workstation_after_fresh_session(tmp_path, monkeypatch):
    monkeypatch.setenv("CLINIC_MODE", "1")
    uri = "sqlite:///" + (tmp_path / "staging.db").as_posix()
    app_one = _route_app(uri)
    with app_one.app_context():
        db.metadata.create_all(
            bind=db.engine,
            tables=[
                User.__table__,
                ClinicWorkstation.__table__,
                ClinicWorkstationOverride.__table__,
                ClinicWorkstationLock.__table__,
            ],
        )
        u1 = User(username="par_cfg_u1", email="par_cfg_u1@local.test", password="x", is_admin=False)
        u2 = User(username="par_cfg_u2", email="par_cfg_u2@local.test", password="x", is_admin=False)
        db.session.add_all([u1, u2])
        db.session.commit()
        u1_id, u2_id = u1.id, u2.id

    c1 = app_one.test_client()
    h1 = {"X-CSRF-Token": c1.get(f"/login/{u1_id}").json["csrf"]}
    issued = c1.post(PREFIX + "/workstations", headers=h1)
    assert issued.status_code == 201
    managed_wid = issued.json["workstation_id"]

    # Missing override row resolves cleanly with version 0.
    initial = c1.get(f"{PREFIX}/workstations/{managed_wid}")
    assert initial.status_code == 200
    assert initial.json == {
        "values": {}, "origins": {}, "version": 0,
        "transport_mode": "local", "device_redirection_verified": False,
    }

    saved = c1.put(
        f"{PREFIX}/workstations/{managed_wid}",
        headers=h1,
        json={"expected_version": 0, "changes": {"theme_dark_mode": True}},
    )
    assert (saved.status_code, saved.json) == (200, {"version": 1})

    # Fresh app instance + fresh login session on the same database.
    app_two = _route_app(uri)
    fresh = app_two.test_client()
    h_fresh = {"X-CSRF-Token": fresh.get(f"/login/{u1_id}").json["csrf"]}

    managed_after = fresh.get(f"{PREFIX}/workstations/{managed_wid}")
    assert managed_after.status_code == 200
    assert managed_after.json == {
        "values": {"theme_dark_mode": True},
        "origins": {"theme_dark_mode": "workstation"},
        "version": 1,
        "transport_mode": "local",
        "device_redirection_verified": False,
    }

    unregistered_get = fresh.get(f"{PREFIX}/workstations/unregistered-workstation")
    assert (unregistered_get.status_code, unregistered_get.json["code"]) == (403, "untrusted_workstation")

    unregistered_put = fresh.put(
        f"{PREFIX}/workstations/unregistered-workstation",
        headers=h_fresh,
        json={"expected_version": 0, "changes": {"theme_dark_mode": False}},
    )
    assert (unregistered_put.status_code, unregistered_put.json["code"]) == (403, "untrusted_workstation")

    stale_put = fresh.put(
        f"{PREFIX}/workstations/{managed_wid}",
        headers=h_fresh,
        json={"expected_version": 0, "changes": {"theme_dark_mode": False}},
    )
    assert (stale_put.status_code, stale_put.json["code"], stale_put.json["current_version"]) == (
        409,
        "stale_version",
        1,
    )

    other_client = app_two.test_client()
    other_client.get(f"/login/{u2_id}")
    foreign_get = other_client.get(f"{PREFIX}/workstations/{managed_wid}")
    assert (foreign_get.status_code, foreign_get.json["code"]) == (403, "untrusted_workstation")
