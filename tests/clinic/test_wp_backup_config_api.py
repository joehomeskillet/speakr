"""Import-source backup settings, filename rendering and retention preview."""

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from flask import Flask
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import create_engine, text

from src.api.system import get_csrf_token
from src.app import app as main_app, db, load_user
from src.clinic import guard, import_source_config as sources, route_policy, routes_imports_web
from src.clinic.identity import ClinicAccountPolicy
from src.clinic.output import OutputNameContext
from src.models import User


@pytest.fixture
def source(monkeypatch):
    monkeypatch.setenv("CLINIC_MODE", "1")
    monkeypatch.setenv("CLINIC_IMPORT_ROOT_ALLOWLIST", "/synthetic")
    monkeypatch.setattr(guard, "ROUTE_POLICY", route_policy._build())
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="synthetic-backup-config-test",
                      SQLALCHEMY_DATABASE_URI=main_app.config["SQLALCHEMY_DATABASE_URI"])
    db.init_app(app)
    LoginManager(app).user_loader(load_user)
    CSRFProtect(app)
    guard.ensure_guard_first(app)
    app.add_url_rule("/api/csrf-token", endpoint="system.get_csrf_token", view_func=get_csrf_token)
    app.register_blueprint(routes_imports_web.blueprint)
    clients = []
    with app.app_context():
        for is_admin in (True, True, False):
            key = uuid4().hex
            user = User(username=key[:20], email=f"{key}@example.invalid", password="x", is_admin=is_admin)
            db.session.add(user)
            db.session.flush()
            account = ClinicAccountPolicy(user_id=user.id, status="active")
            db.session.add(account)
            db.session.flush()
            client = app.test_client()
            with client.session_transaction() as session:
                session.update(_user_id=user.get_id(), _fresh=True, clinic_provider="local",
                               clinic_security_version=account.security_version)
            clients.append(client)
            if len(clients) == 1:
                owner_id = user.id
        db.session.commit()
        config = sources.create_source_config(
            actor_user_id=owner_id, source_kind="folder", source_ref="synthetic-source",
            approved_root="/synthetic/inbox", target_author_user_id=owner_id,
        )
    return f"/api/clinic/v1/imports/sources/{config['id']}", *clients


def _headers(client):
    token = client.get("/api/csrf-token").json["csrf_token"]
    return {"X-CSRF-Token": token, "Idempotency-Key": str(uuid4())}


def test_backup_settings_round_trip_and_disable_without_losing_policy(source):
    url, client, other_admin, _ = source
    assert other_admin.get(url).status_code == 404
    initial = client.get(url)
    assert initial.status_code == 200
    assert initial.json["backup_enabled"] is False
    assert initial.json["backup_folder"] is None
    headers = _headers(client)
    settings = {"expected_version": 1, "backup_enabled": True, "backup_folder": "archive/backup",
                "backup_filename_template": "%T - %A", "backup_retention_days": 30}
    saved = client.put(url, json=settings, headers=headers)
    assert saved.status_code == 200
    assert {name: saved.json[name] for name in settings if name != "expected_version"} == {
        "backup_enabled": True, "backup_folder": "/synthetic/archive/backup",
        "backup_filename_template": "%T - %A", "backup_retention_days": 30,
    }
    assert client.put(url, json=settings, headers=headers).json == saved.json
    assert client.get(url).json == saved.json
    assert client.get(url.rsplit("/", 1)[0]).json["items"] == [saved.json]
    assert client.put(url, json=settings, headers=_headers(client)).status_code == 409

    disabled = client.put(url, json={"expected_version": 2, "backup_enabled": False}, headers=_headers(client))
    assert disabled.status_code == 200
    assert client.get(url).json == {**saved.json, "backup_enabled": False, "policy_version": 3}
    preview = client.post(f"{url}/retention-preview", json={"entries": []}, headers=_headers(client))
    assert preview.json == {"backup_enabled": False, "backup_retention_days": 30, "plan": None}
    cleared = client.put(url, json={"expected_version": 3, "backup_retention_days": None}, headers=_headers(client))
    assert cleared.status_code == 200
    assert client.get(url).json["backup_retention_days"] is None


@pytest.mark.parametrize(("change", "status"), [
    ({"backup_enabled": True}, 400),
    ({"backup_enabled": "true"}, 400),
    ({"backup_enabled": None}, 400),
    ({"backup_folder": "../backup"}, 400),
    ({"backup_folder": "inbox/backup"}, 400),
    ({"backup_folder": "/outside/backup"}, 403),
    ({"backup_filename_template": "%X"}, 400),
    ({"backup_filename_template": "../%A"}, 400),
    ({"backup_retention_days": 0}, 400),
    ({"backup_retention_days": True}, 400),
])
def test_rejected_backup_changes_do_not_mutate_the_source(source, change, status):
    url, client, _, _ = source
    before = client.get(url).json
    body = {"expected_version": 1, "backup_folder": "/synthetic/backup", **change}
    if change == {"backup_enabled": True}:
        body.pop("backup_folder")
    response = client.put(url, json=body, headers=_headers(client))
    assert response.status_code == status
    assert client.get(url).json == before


def test_backup_filename_preserves_placeholder_order_extension_and_copy_identity():
    from src.clinic.backup_naming import backup_filename

    context = OutputNameContext(author="A/uthor", title="Title")
    first, second = UUID(int=1), UUID(int=2)
    assert backup_filename("%T - %A.ds2", context, "input.ds2", first) == f"Title - A_uthor_{first.hex}.ds2"
    assert backup_filename("%A - %T", context, "input.ds2", first) == f"A_uthor - Title_{first.hex}.ds2"
    assert backup_filename("%A - %T", context, "input.ds2", second) == f"A_uthor - Title_{second.hex}.ds2"


def test_admin_previews_retention_without_modifying_the_source(source):
    url, client, other_admin, member = source
    saved = client.put(url, json={"expected_version": 1, "backup_folder": "/synthetic/backup",
                                  "backup_enabled": True, "backup_retention_days": 30}, headers=_headers(client))
    assert saved.status_code == 200
    preview = f"{url}/retention-preview"
    now = datetime.now(timezone.utc)
    old, new = ((now - timedelta(days=days)).isoformat() for days in (31, 1))
    body = {"entries": [
        {"path": "old.ds2", "moved_at": old},
        {"path": "new.ds2", "moved_at": new},
        {"path": "held.ds2", "moved_at": old, "hold": True},
        {"path": "../unsafe.ds2", "moved_at": old},
    ]}
    assert client.post(preview, json=body).status_code == 400  # App-level CSRF rejection.
    denied = member.post(preview, json=body, headers=_headers(member))
    assert denied.status_code == 403
    missing = other_admin.post(preview, json=body, headers=_headers(other_admin))
    assert missing.status_code == 404
    response = client.post(preview, json=body, headers=_headers(client))
    assert response.status_code == 200
    assert response.json == {"backup_enabled": True, "backup_retention_days": 30,
                             "plan": {"expired": 1, "kept": 1, "held": 1, "invalid": 1}}
    invalid = client.post(preview, json={"entries": [{"path": "old.ds2", "moved_at": "2026-01-01"}]},
                          headers=_headers(client))
    assert invalid.status_code == 400
    assert client.get(url).json == saved.json


def test_legacy_backup_setting_is_upgraded_idempotently():
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE clinic_import_source_config (id TEXT PRIMARY KEY, backup_root TEXT)"))
            conn.execute(text("INSERT INTO clinic_import_source_config VALUES ('legacy', '/synthetic/backup')"))
        sources.create_tables(engine)
        with engine.begin() as conn:
            assert conn.execute(text("SELECT backup_enabled FROM clinic_import_source_config")).scalar_one() is None
            conn.execute(text("UPDATE clinic_import_source_config SET backup_enabled = FALSE"))
        sources.create_tables(engine)
        with engine.connect() as conn:
            assert conn.execute(text("SELECT backup_enabled FROM clinic_import_source_config")).scalar_one() == 0
    finally:
        engine.dispose()
