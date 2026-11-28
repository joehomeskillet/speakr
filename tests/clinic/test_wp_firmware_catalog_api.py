"""Firmware catalog metadata and authenticated inventory comparisons (PAR-IMP-010)."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from flask import Flask
from flask_login import LoginManager
from flask_wtf.csrf import generate_csrf
from sqlalchemy import create_engine, text

from src.app import app as speakr_app
from src.clinic import agent_channel as ch, import_source_config, policy, routes_agent
from src.clinic.device_inventory_view import get_firmware_action_status
from src.clinic.identity import ClinicAccountPolicy
from src.clinic.import_centre import ClinicImportIntent
from src.clinic.ingest import IngestService
from src.clinic.migration_inventory_hints import upgrade_firmware_claims
from src.clinic.routes_imports_web import blueprint
from src.database import db
from src.models import AuditEvent, User
from src.services import audit

URL = "/api/clinic/v1/imports/firmware"
DEVICE = "LFH9600"
ENTRY_URL = f"{URL}/{DEVICE}"
TARGET = {"target_version": "1.10", "expected_version": 0}
PROXY = "synthetic-firmware-agent-proxy-secret"
FINGERPRINT = "ab" * 32


@pytest.fixture
def catalog(tmp_path, clinic_on):
    app = Flask(__name__)
    app.config.update(
        TESTING=True, SECRET_KEY="synthetic-firmware-session",
        SQLALCHEMY_DATABASE_URI=speakr_app.config["SQLALCHEMY_DATABASE_URI"],
        SQLALCHEMY_ENGINE_OPTIONS=speakr_app.config.get("SQLALCHEMY_ENGINE_OPTIONS", {}),
    )
    db.init_app(app)
    login = LoginManager(app)

    @login.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id.split(":")[0]))

    @app.get("/csrf")
    def csrf():
        return {"token": generate_csrf()}

    channel = ch.ChannelPolicy(b"f" * 32, authorise=lambda *_: True)
    app.register_blueprint(blueprint)
    app.register_blueprint(routes_agent.create_agent_blueprint(channel, IngestService(tmp_path / "originals"), PROXY))

    users = {}
    with app.app_context():
        for name, admin, status in (
            ("admin", True, "active"), ("staff", False, "active"),
            ("other", False, "active"), ("disabled", True, "disabled"),
        ):
            username = f"firmware-{name}-{uuid4().hex[:8]}"
            user = User(username=username, email=f"{username}@example.invalid", password="unused", is_admin=admin)
            db.session.add(user)
            db.session.flush()
            db.session.add(ClinicAccountPolicy(user_id=user.id, status=status))
            users[name] = (user.id, user.get_id())
        db.session.commit()

    def client(name=None, csrf_header="X-CSRFToken"):
        browser = app.test_client()
        if name is not None:
            with browser.session_transaction() as session:
                session["_user_id"] = users[name][1]
        return browser, {csrf_header: browser.get("/csrf").json["token"]}

    return SimpleNamespace(app=app, client=client, channel=channel, ids={name: user[0] for name, user in users.items()})


def _put(browser, headers, payload, path=ENTRY_URL):
    response = browser.put(path, json=payload, headers=headers)
    assert response.status_code in (200, 201)
    return response.json


def _get(browser):
    response = browser.get(URL)
    assert response.status_code == 200
    assert response.cache_control.private and response.cache_control.no_store
    assert response.json["count"] == len(response.json["items"])
    return response.json


def _register(catalog, roots, fingerprint=FINGERPRINT):
    return ch.register_agent(
        catalog.channel, actor_user_id=catalog.ids["admin"], display_name="Firmware test agent",
        allowed_sources=roots, credential_fingerprint=fingerprint,
    )["agent_id"]


def _report(catalog, items, fingerprint=FINGERPRINT):
    return catalog.app.test_client().put(
        f"{routes_agent.PREFIX}/inventory", json={"items": items},
        headers={routes_agent.FINGERPRINT_HEADER: fingerprint, routes_agent.PROXY_SECRET_HEADER: PROXY},
    )


def _item(source, root="usb-1", snapshot="snapshot-1", **metadata):
    return {
        "source_id": source, "root_id": root, "snapshot_version": snapshot,
        "relative_path": f"{source}.ds2", "size": 1, "sha256": "a" * 64,
        "mtime_utc": "2026-11-17T07:00:00+00:00", **metadata,
    }


def _entry(target, version, device=DEVICE):
    return {"device_type": device, "target_version": target, "version": version}


def test_authenticated_report_round_trips_with_all_firmware_comparisons(catalog):
    with catalog.app.app_context():
        agent_id = _register(catalog, ["usb-1"])
    admin, headers = catalog.client("admin")
    assert _put(admin, headers, TARGET) == _entry("1.10", 1)
    versions = {"equal": "1.10.0", "older": "1.9", "newer": "1.11"}
    items = [_item(status, device_type=DEVICE, firmware_version=version) for status, version in versions.items()]
    items.extend([
        _item("legacy", snapshot="1.10"),
        _item("missing-version", snapshot="1.10", device_type=DEVICE),
        _item("missing-target", device_type="LFH9500", firmware_version="1.10"),
        _item("unsupported-version", device_type="lfh9600", firmware_version="1.10-beta"),
    ])
    response = _report(catalog, items)
    assert response.status_code == 200
    assert response.json["accepted"] == len(items)
    with catalog.app.app_context():
        row = ch.inventory_row(agent_id, "older", "snapshot-1")
        assert (row.device_type, row.firmware_version) == (DEVICE, "1.9")

    view = _get(admin)
    assert view["catalog"] == [_entry("1.10", 1)]
    assert view["firmware_action"] == get_firmware_action_status()
    assert view["firmware_action"]["status"] == "blocked"
    assert view["firmware_action"]["web_gap"] == "WG-IMP-010"
    assert view["firmware_action"]["control_command_issued"] is False
    expected = {
        status: {"source_id": status, "root_id": "usb-1", "snapshot_version": "snapshot-1",
                 "device_type": DEVICE, "firmware_version": version, "target_version": "1.10",
                 "comparison_status": status}
        for status, version in versions.items()
    }
    expected["legacy"] = {
        "source_id": "legacy", "root_id": "usb-1", "snapshot_version": "1.10", "device_type": None,
        "firmware_version": None, "target_version": None, "comparison_status": "unknown",
    }
    expected["missing-version"] = {
        "source_id": "missing-version", "root_id": "usb-1", "snapshot_version": "1.10",
        "device_type": DEVICE, "firmware_version": None, "target_version": "1.10", "comparison_status": "unknown",
    }
    expected["missing-target"] = {
        "source_id": "missing-target", "root_id": "usb-1", "snapshot_version": "snapshot-1",
        "device_type": "LFH9500", "firmware_version": "1.10", "target_version": None, "comparison_status": "unknown",
    }
    expected["unsupported-version"] = {
        "source_id": "unsupported-version", "root_id": "usb-1", "snapshot_version": "snapshot-1",
        "device_type": DEVICE, "firmware_version": None, "target_version": "1.10", "comparison_status": "unknown",
    }
    assert view["count"] == len(expected)
    assert {row["source_id"]: row for row in view["items"]} == expected
    with catalog.app.app_context():
        assert ch.ClinicAgentIntent.query.count() == 0
        assert ClinicImportIntent.query.count() == 0


def test_firmware_inventory_respects_routing_and_current_agent_roots(catalog, monkeypatch):
    monkeypatch.setenv("CLINIC_IMPORT_ROOT_ALLOWLIST", "/synthetic-imports")
    ids = catalog.ids
    policy.register_grant_provider(
        lambda uid: [policy.Grant("own", frozenset({"import"}))] if uid == ids["staff"] else [],
    )
    disabled_fingerprint = "cd" * 32
    with catalog.app.app_context():
        active_agent = _register(catalog, ["usb-1", "usb-2", "revoked"])
        disabled_agent = _register(catalog, ["usb-3"], fingerprint=disabled_fingerprint)
        for root, user in (("usb-1", "staff"), ("usb-2", "other"), ("usb-3", "staff"), ("revoked", "staff")):
            import_source_config.create_source_config(
                actor_user_id=ids["admin"], source_kind="device", source_ref=root,
                approved_root="/synthetic-imports", target_author_user_id=ids[user], may_assign_author=lambda *_: True,
            )
    assert _report(catalog, [_item("forbidden", root="unregistered")]).status_code == 403
    assert _report(catalog, [_item(root, root=root) for root in ("usb-1", "usb-2", "revoked")]).status_code == 200
    assert _report(catalog, [_item("usb-3", root="usb-3")], fingerprint=disabled_fingerprint).status_code == 200
    with catalog.app.app_context():
        ch.set_agent_enabled(catalog.channel, actor_user_id=ids["admin"], agent_id=disabled_agent, enabled=False)
        db.session.get(ch.ClinicAgent, active_agent).allowed_sources = '["usb-1", "usb-2"]'
        db.session.commit()

    admin, _ = catalog.client("admin")
    staff, headers = catalog.client("staff")
    assert {row["source_id"] for row in _get(admin)["items"]} == {"usb-1", "usb-2"}
    assert [row["source_id"] for row in _get(staff)["items"]] == ["usb-1"]
    assert staff.put(ENTRY_URL, json=TARGET, headers=headers).status_code == 403


@pytest.mark.parametrize("name,read_status,write_status", [(None, 401, 401), ("disabled", 403, 403), ("other", 403, 403)])
def test_firmware_requires_active_authorized_session(catalog, name, read_status, write_status):
    browser, headers = catalog.client(name)
    assert browser.get(URL).status_code == read_status
    assert browser.put(ENTRY_URL, json=TARGET, headers=headers).status_code == write_status


def test_catalog_writes_are_versioned_and_forced_audited(catalog, monkeypatch):
    monkeypatch.setattr(audit, "is_enabled", lambda: False)
    admin, headers = catalog.client("admin")
    assert admin.put(ENTRY_URL, json=TARGET).status_code == 403
    assert _put(admin, headers, TARGET) == _entry("1.10", 1)
    for stale in ({"target_version": "2.0"}, {"target_version": "2.0", "expected_version": 0}):
        assert admin.put(ENTRY_URL, json=stale, headers=headers).status_code == 409
    alternate_csrf = {"X-CSRF-Token": headers["X-CSRFToken"]}
    assert _put(admin, alternate_csrf, {"target_version": "1.11", "expected_version": 1}) == _entry("1.11", 2)
    other_device = "LFH9500"
    other = _put(admin, headers, {"target_version": "2.0"}, path=f"{URL}/{other_device}")
    assert other == _entry("2.0", 1, other_device)
    assert sorted(_get(admin)["catalog"], key=lambda row: row["device_type"]) == [other, _entry("1.11", 2)]
    with catalog.app.app_context():
        events = AuditEvent.query.all()
        assert len(events) == 3
        assert {event.actor_user_id for event in events} == {catalog.ids["admin"]}
        assert {event.event for event in events} == {"admin.setting.changed"}
        assert {event.details["key"] for event in events} == {"firmware:LFH9500", "firmware:LFH9600"}
        assert ch.ClinicAgentIntent.query.count() == 0
        assert ClinicImportIntent.query.count() == 0


@pytest.mark.parametrize("payload", [
    [], {}, {**TARGET, "target_version": "1.10\nflash"}, {**TARGET, "expected_version": True},
    {**TARGET, "firmware_flash": True}, {**TARGET, "control_command": "flash"},
])
def test_catalog_rejects_malformed_and_control_fields(catalog, payload):
    admin, headers = catalog.client("admin")
    assert admin.put(ENTRY_URL, json=payload, headers=headers).status_code == 400
    assert _get(admin)["catalog"] == []


def test_catalog_rolls_back_update_and_create_when_audit_fails(catalog, monkeypatch):
    admin, headers = catalog.client("admin")
    original = _put(admin, headers, TARGET)
    monkeypatch.setattr(audit, "record_in_tx", lambda *args, **kwargs: None)
    assert admin.put(ENTRY_URL, json={"target_version": "1.11", "expected_version": 1}, headers=headers).status_code == 503
    assert admin.put(f"{URL}/LFH9500", json=TARGET, headers=headers).status_code == 503
    assert _get(admin)["catalog"] == [original]
    with catalog.app.app_context():
        assert AuditEvent.query.count() == 1


def test_existing_inventory_migration_preserves_snapshot_and_does_not_infer_firmware():
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE clinic_agent_inventory (snapshot_version VARCHAR(128))"))
            connection.execute(text("INSERT INTO clinic_agent_inventory VALUES ('1.10')"))
        assert len(upgrade_firmware_claims(engine)) == 2
        assert upgrade_firmware_claims(engine) == []
        with engine.connect() as connection:
            row = connection.execute(text(
                "SELECT snapshot_version, device_type, firmware_version FROM clinic_agent_inventory"
            )).one()
            assert tuple(row) == ("1.10", None, None)
    finally:
        engine.dispose()
