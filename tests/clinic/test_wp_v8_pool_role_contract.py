"""Exercise the pool-role contract through independent Flask request sessions."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import subprocess
import sys
from threading import Barrier

from sqlalchemy import event

from src.clinic import pools
from src.clinic.identity import ClinicAccountPolicy
from src.database import db
from src.models import GroupMembership
from test_wp_v8_assignment_route import _client, _user


def test_pool_role_authority_and_simultaneous_requests(tmp_path):
    root = Path(__file__).resolve().parents[2]
    env = {**os.environ, "CLINIC_MODE": "1", "SPEAKR_SKIP_BACKGROUND_TASKS": "true",
           "TRANSCRIPTION_BASE_URL": "http://127.0.0.1:9/v1", "PYTHONPATH": str(root),
           "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'roles.db'}",
           "UPLOAD_FOLDER": str(tmp_path / "uploads")}
    result = subprocess.run([sys.executable, str(Path(__file__).resolve())], cwd=root,
                            env=env, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr


def _exercise():
    from src.app import app
    from src.init_db import initialize_database

    app.config.update(TESTING=True, WTF_CSRF_ENABLED=True)
    with app.app_context():
        initialize_database(app)
        admins = [_user("contract-a", admin=True), _user("contract-b", admin=True)]
        identity_admin = _user("contract-identity")
        db.session.get(ClinicAccountPolicy, identity_admin).is_identity_admin = True
        member = _user("contract-member")
        staff = pools.create_staff_pool("Contract staff", actor_id=admins[0])
        pool_id, group_id = staff.id, staff.group_id
        pools.add_staff_member(pool_id, member, actor_id=admins[0])
        engine = db.engine

    url = f"/api/clinic/v1/pool-admin/pools/staff/{pool_id}/members/{member}/role"

    def request_role(actor, role, path=url):
        client, header = _client(app, actor)
        return client.put(path, json={"role": role}, headers={"X-CSRF-Token": header["X-CSRFToken"]})

    # Identity management rights alone cannot change a staff member's role.
    for path in (url, url.replace(pool_id, "absent-pool")):
        reply = request_role(identity_admin, "lead", path)
        assert reply.status_code == 403, reply.get_data(as_text=True)

    # SQLite does not implement SELECT FOR UPDATE. Force overlapping reads,
    # then let the actual guarded UPDATEs execute on two distinct connections.
    updates = Barrier(2)

    def align_updates(conn, cursor, statement, parameters, context, executemany):
        sql = statement.lower()
        if engine.dialect.name == "sqlite":
            meet = sql.startswith("update " + GroupMembership.__tablename__.lower() + " set")
        else:
            meet = sql.endswith("for update") and GroupMembership.__tablename__ in sql
        if meet:
            updates.wait(timeout=10)

    event.listen(engine, "before_cursor_execute", align_updates)
    try:
        with ThreadPoolExecutor(max_workers=2) as threads:
            responses = list(threads.map(lambda actor: request_role(actor, "lead"), admins))
    finally:
        event.remove(engine, "before_cursor_execute", align_updates)

    assert all(r.status_code in (200, 409) for r in responses)
    successful = {actor for actor, response in zip(admins, responses) if response.status_code == 200}
    assert successful
    assert request_role(admins[0], "lead").status_code == 200
    assert request_role(admins[1], "member").status_code == 200
    with app.app_context():
        assert GroupMembership.query.filter_by(group_id=group_id, user_id=member).one().role == "member"
        history = [h for h in pools.history() if h["change"] == "role_changed"]
        assert len(history) == 2
        winner = history[0]["actor_user_id"]
        assert winner in successful
        assert [(h["actor_user_id"], h["detail"]["old"]["role"], h["detail"]["new"]["role"])
                for h in history] == [(winner, "member", "lead"), (admins[1], "lead", "member")]


if __name__ == "__main__":
    _exercise()
