"""Pool role service and protected route through the real application startup."""

import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

from sqlalchemy.orm import Query

from src.clinic import pools
from src.clinic.identity import ClinicAccountPolicy
from src.clinic.models import ClinicDictation
from src.clinic.routes_pools import save_pool, revision
from src.database import db
from src.models import GroupMembership, User

from test_wp_v8_assignment_route import _client, _dictation, _user

ROOT = Path(__file__).resolve().parents[2]


def test_pool_lead_real_app(tmp_path):
    env = {**os.environ, "CLINIC_MODE": "1", "SPEAKR_SKIP_BACKGROUND_TASKS": "true",
           "PYTHONPATH": str(ROOT), "TRANSCRIPTION_BASE_URL": "http://127.0.0.1:9/v1",
           "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'roles.db'}",
           "UPLOAD_FOLDER": str(tmp_path / "uploads")}
    done = subprocess.run([sys.executable, str(Path(__file__).resolve())],
                          env=env, cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr


def _exercise():
    from src.app import app
    from src.init_db import initialize_database
    from src.clinic.route_policy import ROUTE_POLICY

    app.config.update(TESTING=True, WTF_CSRF_ENABLED=True)
    with app.app_context():
        initialize_database(app)
        admin = _user("role-admin", admin=True)
        lead, other, author = map(_user, ("lead", "other", "author"))
        revoked = _user("revoked-admin", admin=True, status="disabled")
        absent = _user("unconfigured-admin", admin=True)
        db.session.delete(db.session.get(ClinicAccountPolicy, absent))
        db.session.commit()
        staff = pools.create_staff_pool("Synthetic staff", actor_id=admin)
        staff_id, group_id = staff.id, staff.group_id
        for user in (lead, other):
            pools.add_staff_member(staff_id, user, actor_id=admin)
        authors = pools.create_author_pool("Synthetic authors", actor_id=admin)
        pools.link_staff_pool(authors.id, staff_id, actor_id=admin)
        sent, _ = _dictation(author, authors.id, sent=True)
        draft, _ = _dictation(author, authors.id, sent=False)
        foreign = pools.create_author_pool("Foreign authors", actor_id=admin)
        unseen, _ = _dictation(author, foreign.id, sent=True)
        db.session.commit()
        initial_revision = revision()

    url = f"/api/clinic/v1/pool-admin/pools/staff/{staff_id}/members/{lead}/role"
    admin_client, csrf = _client(app, admin)
    # Pool routes retain their existing X-CSRF-Token guard.
    csrf = {"X-CSRF-Token": next(iter(csrf.values()))}
    lead_client, _ = _client(app, lead)

    def put(role, client=admin_client, headers=csrf, target=url):
        return client.put(target, json={"role": role}, headers=headers)

    def rows():
        response = lead_client.get("/api/clinic/v1/worklist/rows")
        assert response.status_code == 200, response.json
        return {row["dictation_id"]: row for row in response.json["items"]}

    assert ROUTE_POLICY["clinic_pool_admin.set_member_role"].kind == "auth"
    assert sent not in rows()
    assert put("lead", headers={}).status_code in (400, 403)
    assert put("lead", client=app.test_client()).status_code in (302, 401)
    for actor in (lead, other, revoked, absent):
        client, token = _client(app, actor)
        assert put("lead", client=client, headers={"X-CSRF-Token": next(iter(token.values()))}).status_code == 403
    for role in ("admin", "", None, True, [], {}):
        assert put(role).status_code == 400
    for body in ({}, {"role": "lead", "actor_id": admin}, {"role": "lead", "is_admin": True}):
        assert admin_client.put(url, json=body, headers=csrf).status_code == 400
    assert put("lead", target=url.replace(staff_id, "missing")).status_code == 404
    assert put("lead", target=url.replace(f"/{lead}/role", f"/{author}/role")).status_code == 404

    reply = put("lead")
    assert reply.status_code == 200, reply.json
    assert reply.json == {"pool_id": staff_id, "user_id": lead, "role": "lead"}
    assert sent in rows() and draft not in rows() and unseen not in rows()
    with app.app_context():
        assert GroupMembership.query.filter_by(group_id=group_id, user_id=lead).one().role == "admin"
        assert not db.session.get(User, lead).is_admin
        assert set(pools.grants_for(lead, db.session.get(ClinicDictation, sent))["allowed"]) == {"view"}
        assert revision() == initial_revision + 1
        event = pools.history()[-1]
        assert event["actor_user_id"] == admin and event["change"] == "role_changed"
        assert event["detail"] == {"kind": "staff", "pool_id": staff_id, "user_id": lead,
                                   "old": {"role": "member"}, "new": {"role": "lead"}}
        # The role history also invalidates a previously loaded membership editor.
        try:
            save_pool("staff", staff_id, "Synthetic staff", [lead, other], initial_revision, actor_id=admin)
        except pools.PoolError as error:
            assert error.code == "stale_revision"
        else:
            raise AssertionError("stale membership save accepted")

    for client, expected in ((admin_client, [lead, other]), (lead_client, [lead])):
        response = client.get("/api/clinic/v1/pools")
        state = next(p for p in response.json["staff_pools"] if p["id"] == staff_id)
        assert state["member_user_ids"] == expected
        assert state["members"] == [{"user_id": uid, "role": "lead" if uid == lead else "member"}
                                    for uid in expected]
    assert put("lead").status_code == 200
    with app.app_context():
        assert revision() == initial_revision + 1  # retry adds no fictional change
        # A concurrent removal/change cannot produce a successful role history row.
        with patch.object(Query, "update", return_value=0):
            try:
                pools.set_staff_member_role(staff_id, lead, "member", actor_id=admin)
            except pools.PoolError as error:
                assert error.code == "conflict"
            else:
                raise AssertionError("lost update accepted")
        assert revision() == initial_revision + 1
        # Failure after the UPDATE rolls the role back with the history.
        with patch.object(pools, "_history", side_effect=RuntimeError("synthetic history failure")):
            try:
                pools.set_staff_member_role(staff_id, lead, "member", actor_id=admin)
            except RuntimeError:
                pass
            else:
                raise AssertionError("history failure swallowed")
        assert GroupMembership.query.filter_by(group_id=group_id, user_id=lead).one().role == "admin"
        assert revision() == initial_revision + 1

    assert put("member").status_code == 200
    assert sent not in rows()
    with app.app_context():
        assert pools.history()[-1]["detail"]["old"] == {"role": "lead"}
        assert pools.history()[-1]["detail"]["new"] == {"role": "member"}
        # Recheck live authority even when the actor is already in the identity map.
        actor = db.session.get(User, admin)
        db.session.query(User).filter_by(id=admin).update({"is_admin": False}, synchronize_session=False)
        db.session.commit()
        try:
            pools.set_staff_member_role(staff_id, lead, "lead", actor_id=actor.id)
        except pools.PoolError as error:
            assert error.code == "forbidden"
        else:
            raise AssertionError("revoked admin accepted")


if __name__ == "__main__":
    _exercise()
