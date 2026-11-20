"""Lease visibility, takeover and audit (V8 UI8-003/UI8-004).

Synthetic data only. The routes run in a small app with the real lease service, the real
takeover authority (site admin or staff-pool lead) and the real audit table.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from flask import Flask, g
from flask_login import LoginManager, UserMixin, login_user
from flask_wtf.csrf import generate_csrf

from src.clinic import lease
from src.clinic.identity import ClinicAccountPolicy
from src.clinic.models import ClinicDictation
from src.clinic.parity_dispatch import CommandProblem
from src.clinic.pools import ClinicAuthorPool, ClinicPoolRoute, ClinicStaffPool
from src.clinic.routes_history import create_blueprint
from src.database import db
from src.models import Group, GroupMembership, Recording, User
from src.models.audit import AuditEvent

SECRET_TEXT = "Synthetischer Befundtext 4711"


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setenv("CLINIC_MODE", "1")
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="synthetic-lease-takeover", SQLALCHEMY_DATABASE_URI="sqlite://")
    db.init_app(app)

    with app.app_context():
        db.create_all()

        def add(row):
            db.session.add(row)
            db.session.flush()
            return row

        users = {
            name: add(User(username=name, email=f"{name}@example.invalid", password="synthetic", name=f"Name {name}"))
            for name in ("a", "b", "lead", "admin")
        }
        users["admin"].is_admin = True
        add(ClinicAccountPolicy(user_id=users["admin"].id, status="active"))
        author_pool = add(ClinicAuthorPool(name="authors", created_by=users["a"].id))
        group = add(Group(name="typists", created_by=users["lead"].id))
        add(GroupMembership(group_id=group.id, user_id=users["lead"].id, role="admin"))
        add(GroupMembership(group_id=group.id, user_id=users["b"].id, role="member"))
        staff = add(ClinicStaffPool(group_id=group.id, created_by=users["lead"].id))
        add(ClinicPoolRoute(author_pool_id=author_pool.id, staff_pool_id=staff.id, created_by=users["lead"].id))
        recording = add(Recording(user_id=users["a"].id, title="Synthetic", notes=SECRET_TEXT))
        dictation = add(ClinicDictation(recording_id=recording.id, author_user_id=users["a"].id,
                                        pool_id=author_pool.id))
        db.session.commit()
        ids = {name: user.id for name, user in users.items()}
        did, recording_id = dictation.id, recording.id

        receipts = {}

        def once(scope, digest, operation):
            if scope in receipts:
                if receipts[scope][0] != digest:
                    raise CommandProblem("IDEMPOTENCY_CONFLICT")
                return receipts[scope][1]
            result = operation()
            db.session.commit()
            receipts[scope] = digest, result
            return result

        everyone = set(ids.values())
        app.register_blueprint(create_blueprint(
            authorize=lambda user, _action, _target: user.id in everyone, run_once=once,
            dictation_snapshot=lambda row: {"id": row.id}))

        login = LoginManager(app)

        class Actor(UserMixin):
            def __init__(self, uid):
                self.id = int(uid)

        login.user_loader(Actor)

        @app.get("/synthetic-login/<int:uid>")
        def browser(uid):
            login_user(Actor(uid))
            return {"csrf": generate_csrf()}

        clients = {}
        for name, uid in ids.items():
            client = app.test_client()
            g.pop("csrf_token", None)  # flask_wtf caches the token in g; each client needs its own
            clients[name] = (client, client.get(f"/synthetic-login/{uid}").json["csrf"])

        def call(name, method, path="", body=None):
            client, token = clients[name]
            g.pop("_login_user", None)  # the fixture keeps one app context open; drop the cached user
            g.pop("csrf_token", None)
            headers = {"X-CSRFToken": token, "Idempotency-Key": str(uuid4())}
            url = f"/api/clinic/v1/dictations/{did}/leases{path}"
            if method == "get":
                return client.get(url)
            return client.post(url, headers=headers, json=body) if body is not None else client.post(url, headers=headers)

        yield dict(app=app, ids=ids, did=did, recording_id=recording_id, call=call)


def _rows(world):
    rows = (db.session.query(AuditEvent).filter(AuditEvent.recording_id == world["recording_id"])
            .order_by(AuditEvent.id).all())
    return [(row.details or {}).get("fields", []) for row in rows if row.event == lease.AUDIT_EVENT]


def _proof(response):
    return {"lease_id": response.json["lease_id"], "generation": response.json["generation"]}


def test_get_without_lease_reports_nobody(world):
    got = world["call"]("b", "get")
    assert got.status_code == 200
    assert got.json["held"] is False and got.json["holder_user_id"] is None and got.json["generation"] == 0


def test_get_exposes_holder_until_and_generation(world):
    taken = world["call"]("a", "post")
    assert taken.status_code == 200 and taken.json["generation"] == 1
    got = world["call"]("b", "get")
    assert got.status_code == 200
    assert got.json["holder_user_id"] == world["ids"]["a"]
    assert got.json["holder_name"] == "Name a"
    assert got.json["generation"] == 1 and got.json["held"] is True
    assert got.json["until"] == taken.json["until"]
    assert "lease_id" not in got.json


def test_takeover_needs_permission_and_confirmation(world):
    assert world["call"]("a", "post").status_code == 200
    assert world["call"]("b", "post", "/takeover", {"confirm": True}).status_code == 403
    assert world["call"]("b", "get").json["holder_user_id"] == world["ids"]["a"]
    assert world["call"]("lead", "post", "/takeover", {}).status_code == 400
    assert world["call"]("lead", "post", "/takeover", {"confirm": False}).status_code == 400
    assert world["call"]("lead", "post", "/takeover", {"confirm": True, "x": 1}).status_code == 400
    assert world["call"]("lead", "get").json["generation"] == 1


def test_pool_lead_takes_over_and_old_holder_save_fails(world):
    held = world["call"]("a", "post")
    taken = world["call"]("lead", "post", "/takeover", {"confirm": True})
    assert taken.status_code == 200
    assert taken.json["generation"] == 2
    assert taken.json["holder_user_id"] == world["ids"]["lead"]
    assert taken.json["previous_holder_user_id"] == world["ids"]["a"]

    renew = world["call"]("a", "post", "/renew", _proof(held))
    assert renew.status_code == 409 and renew.json["current_generation"] == 2
    with pytest.raises(lease.LeaseConflict) as error:
        lease.assert_current(world["did"], world["ids"]["a"], held.json["generation"], datetime.now(timezone.utc))
    assert error.value.reason == lease.STALE_GENERATION and error.value.generation == 2
    lease.assert_current(world["did"], world["ids"]["lead"], 2, datetime.now(timezone.utc))


def test_admin_takes_over_and_generation_keeps_rising(world):
    world["call"]("a", "post")
    assert world["call"]("lead", "post", "/takeover", {"confirm": True}).json["generation"] == 2
    again = world["call"]("admin", "post", "/takeover", {"confirm": True})
    assert again.status_code == 200 and again.json["generation"] == 3
    assert again.json["previous_holder_user_id"] == world["ids"]["lead"]


def test_revoked_admin_and_plain_author_cannot_take_over(world):
    world["call"]("b", "post")
    account = db.session.get(ClinicAccountPolicy, world["ids"]["admin"])
    account.status = "disabled"
    db.session.commit()
    assert world["call"]("admin", "post", "/takeover", {"confirm": True}).status_code == 403
    assert world["call"]("a", "post", "/takeover", {"confirm": True}).status_code == 403


def test_every_operation_writes_one_audit_row_without_text(world):
    ids, did = world["ids"], world["did"]
    held = world["call"]("a", "post")
    world["call"]("a", "post", "/renew", _proof(held))
    world["call"]("lead", "post", "/takeover", {"confirm": True})
    taken = world["call"]("lead", "get")
    assert taken.json["generation"] == 2
    lease_row = lease.current(did)
    world["call"]("lead", "post", "/release", {"lease_id": lease_row.id, "generation": 2})
    rows = _rows(world)
    assert [row[0] for row in rows] == ["lease.acquire", "lease.renew", "lease.takeover", "lease.release"]
    assert all(f"dictation:{did}" in row for row in rows)
    assert [row[2] for row in rows] == ["generation:1", "generation:1", "generation:2", "generation:2"]
    assert rows[2][3] == f"from:{ids['a']}"
    stored = db.session.query(AuditEvent).filter(AuditEvent.recording_id == world["recording_id"]).all()
    assert [row.actor_user_id for row in stored] == [ids["a"], ids["a"], ids["lead"], ids["lead"]]
    assert SECRET_TEXT not in repr([row.to_dict() for row in stored])


def test_expiry_is_audited_once_and_release_is_not_expiry(world):
    ids = world["ids"]
    first = world["call"]("a", "post")
    world["call"]("a", "post", "/release", _proof(first))
    world["call"]("b", "post")
    assert [row[0] for row in _rows(world)] == ["lease.acquire", "lease.release", "lease.acquire"]

    row = lease.current(world["did"])
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=5)
    db.session.commit()
    assert world["call"]("a", "post").json["generation"] == 3
    rows = _rows(world)
    assert [row[0] for row in rows][3:] == ["lease.expire", "lease.acquire"]
    assert rows[3][2] == "generation:2"
    stored = db.session.query(AuditEvent).order_by(AuditEvent.id).all()
    assert stored[3].actor_user_id == ids["b"] and stored[3].actor_kind == "system"
