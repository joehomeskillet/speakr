"""Tests for the metadata field definition routes (x28, PAR-CFG-005).

Real SQLite database in a tmp directory, synthetic users only, no clinical values.
"""

from __future__ import annotations

import pytest
from flask import Flask, jsonify
from flask_login import LoginManager, UserMixin, login_user
from flask_wtf.csrf import CSRFProtect, generate_csrf
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.clinic.presets import ClinicMetadataField, create_tables
from src.clinic.routes_metadata_fields import (
    ENDPOINTS,
    PREFIX,
    create_metadata_fields_blueprint,
    field_version,
    validate_xml_path,
)

ADMIN, USER = 1, 2


class Actor(UserMixin):
    def __init__(self, uid: int) -> None:
        self.id = uid
        self.is_admin = uid == ADMIN

    def get_id(self) -> str:
        return str(self.id)


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("CLINIC_MODE", "1")
    engine = create_engine(f"sqlite:///{tmp_path / 'fields.db'}")
    create_tables(engine)
    db_session = sessionmaker(bind=engine)()
    audits: list[tuple[int, str, str]] = []

    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="synthetic-test-key")
    login = LoginManager(app)
    CSRFProtect(app)
    login.user_loader(lambda uid: Actor(int(uid)) if uid in {"1", "2"} else None)

    @app.get("/as/<int:uid>")
    def as_user(uid):
        login_user(Actor(uid))
        return jsonify(csrf=generate_csrf())

    app.register_blueprint(create_metadata_fields_blueprint(
        session_factory=lambda: db_session,
        is_site_admin=lambda _s, user: bool(user.is_admin),
        audit_write=lambda actor, action, name: audits.append((actor, action, name)),
    ))

    def client_for(uid):
        client = app.test_client()
        return client, {"X-CSRFToken": client.get(f"/as/{uid}").json["csrf"]}

    return client_for, db_session, audits, app


def url(name):
    return f"{PREFIX}/{name}"


def create(client, headers, name="station", **extra):
    body = {"label": "Station", "field_type": "string", "xml_path": "patient/station", **extra}
    return client.put(url(name), json=body, headers=headers)


def test_endpoint_names_listed_and_registered(env):
    _, _, _, app = env
    registered = {rule.endpoint for rule in app.url_map.iter_rules()}
    assert set(ENDPOINTS) <= registered


def test_admin_create_update_and_list(env):
    client_for, _, audits, _ = env
    admin, headers = client_for(ADMIN)
    created = create(admin, headers)
    assert created.status_code == 201
    first = created.json
    assert first["name"] == "station" and first["source_type"] == "xml" and first["is_active"] is True
    assert "default_value" not in first

    update = admin.put(url("station"), headers=headers,
                       json={"label": "Abteilung", "expected_version": first["version"]})
    assert update.status_code == 200
    assert update.json["label"] == "Abteilung" and update.json["version"] != first["version"]

    listing = admin.get(PREFIX).json["fields"]
    assert [row["name"] for row in listing] == ["station"]
    assert set(listing[0]) == {"name", "label", "field_type", "source_type", "xml_path", "display_format",
                               "is_required", "position", "is_active", "version"}
    assert [a[1] for a in audits] == ["create", "update"]
    assert all(a[0] == ADMIN for a in audits)


def test_non_admin_reads_active_only_and_cannot_write(env):
    client_for, db_session, audits, _ = env
    admin, a_headers = client_for(ADMIN)
    create(admin, a_headers, "station")
    gone = create(admin, a_headers, "old", label="Alt").json
    admin.put(url("old"), headers=a_headers, json={"is_active": False, "expected_version": gone["version"]})

    user, u_headers = client_for(USER)
    assert [r["name"] for r in user.get(PREFIX).json["fields"]] == ["station"]
    assert sorted(r["name"] for r in admin.get(PREFIX).json["fields"]) == ["old", "station"]
    denied = create(user, u_headers, "other")
    assert denied.status_code == 403 and denied.json["code"] == "POLICY_DENIED"
    assert db_session.query(ClinicMetadataField).filter_by(name="other").count() == 0
    assert len(audits) == 3  # create, create, archive; the denied write left none


def test_anonymous_and_missing_csrf(env):
    client_for, _, _, app = env
    anon = app.test_client()
    assert anon.get(PREFIX).status_code == 401
    token = anon.get("/as/2").json["csrf"]
    with anon.session_transaction() as sess:
        sess.pop("_user_id", None)
    assert anon.put(url("x"), json={"label": "x"}, headers={"X-CSRFToken": token}).status_code == 401
    admin, _ = client_for(ADMIN)
    refused = admin.put(url("station"), json={"label": "S"})
    assert refused.status_code == 403 and refused.json["code"] == "FORBIDDEN"


def test_stale_version_returns_409(env):
    client_for, _, audits, _ = env
    admin, headers = client_for(ADMIN)
    first = create(admin, headers).json
    admin.put(url("station"), headers=headers, json={"label": "Neu", "expected_version": first["version"]})
    stale = admin.put(url("station"), headers=headers, json={"label": "Alt", "expected_version": first["version"]})
    assert stale.status_code == 409 and stale.json["code"] == "stale_version"
    exists = create(admin, headers)  # creation without expected_version on an existing name
    assert exists.status_code == 400 and exists.json["reason"] == "expected_version_required"
    ghost = admin.put(url("ghost"), headers=headers, json={"label": "G", "expected_version": "abc"})
    assert ghost.status_code == 409
    assert len(audits) == 2


def test_validation_errors(env):
    client_for, db_session, _, _ = env
    admin, headers = client_for(ADMIN)
    cases = [
        ({"field_type": "blob"}, "bad_display_type"),
        ({"xml_path": "../etc"}, "bad_xml_path"),
        ({"xml_path": "/abs"}, "bad_xml_path"),
        ({"xml_path": "a//b"}, "bad_xml_path"),
        ({"xml_path": "a/*"}, "bad_xml_path"),
        ({"xml_path": "a[1]"}, "bad_xml_path"),
        ({"xml_path": "@attr"}, "bad_xml_path"),
        ({"xml_path": "<!DOCTYPE x>"}, "bad_xml_path"),
        ({"xml_path": "x" * 257}, "bad_xml_path"),
        ({"xml_path": "a/b/c/d/e/f/g/h/i"}, "bad_xml_path"),
        ({"label": "  "}, "bad_label"),
        ({"source_type": "ftp"}, "bad_source_type"),
        ({"is_required": "yes"}, "bad_flag"),
        ({"position": -1}, "bad_position"),
        ({"surprise": 1}, "bad_body"),
    ]
    for extra, reason in cases:
        reply = create(admin, headers, **extra)
        assert (reply.status_code, reply.json["reason"]) == (400, reason), extra
    assert admin.put(url("__proto__"), headers=headers, json={"label": "x"}).json["reason"] == "bad_key"
    assert admin.put(url("1bad"), headers=headers, json={"label": "x"}).json["reason"] == "bad_key"
    assert db_session.query(ClinicMetadataField).count() == 0


def test_xml_path_helper():
    assert validate_xml_path("patient/station") == "patient/station"
    assert validate_xml_path(None) is None and validate_xml_path("") is None
    with pytest.raises(Exception):
        validate_xml_path(5)


def test_archive_keeps_row_and_audits(env):
    client_for, db_session, audits, _ = env
    admin, headers = client_for(ADMIN)
    first = create(admin, headers).json
    archived = admin.put(url("station"), headers=headers,
                         json={"is_active": False, "expected_version": first["version"]})
    assert archived.status_code == 200 and archived.json["is_active"] is False
    row = db_session.query(ClinicMetadataField).filter_by(name="station").one()
    assert row.is_active is False
    assert audits[-1][1] == "archive"
    assert field_version(row) == archived.json["version"]


def test_clinic_mode_off_is_404(env, monkeypatch):
    client_for, _, _, _ = env
    admin, headers = client_for(ADMIN)
    monkeypatch.delenv("CLINIC_MODE")
    assert admin.get(PREFIX).status_code == 404
    assert create(admin, headers).status_code == 404
