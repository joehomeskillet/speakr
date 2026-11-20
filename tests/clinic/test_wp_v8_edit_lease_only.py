"""Check edit-only access through the real clinic lease routes in a disposable app."""

import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from test_wp_mount_correction import _env

ROOT = Path(__file__).resolve().parents[2]


def test_author_and_typist_can_lease_with_edit_only(tmp_path):
    env = {**_env(tmp_path, True), 'PYTHONPATH': str(ROOT), 'SPEAKR_SKIP_BACKGROUND_TASKS': 'true'}
    result = subprocess.run([sys.executable, __file__], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr


if __name__ == '__main__':
    from src.app import app, db
    from src.clinic import pools
    from src.init_db import initialize_database
    from src.models import GroupMembership
    from test_wp_v8_detail_api import _scene, _user

    with app.app_context():
        initialize_database(app)
        ids = _scene(assignee=True)
        staff = db.session.get(pools.ClinicStaffPool, ids['staff_pool'])
        db.session.add(GroupMembership(group_id=staff.group_id, user_id=ids['typist'], role='member'))
        db.session.commit()
    path = f"/api/clinic/v1/dictations/{ids['dictation']}"

    def request_as(role, method, route, **kwargs):
        client = app.test_client()
        with client.session_transaction() as session:
            session['_user_id'] = str(ids[role])
            session['_fresh'] = True
        token = client.get('/api/csrf-token').json['csrf_token']
        headers = {'X-CSRF-Token': token, 'Idempotency-Key': str(uuid4())}
        return client.open(path + route, method=method, headers=headers, **kwargs)

    # The staff link grants view, but an edit lease needs an explicit pool grant.
    assert request_as('typist', 'GET', '/leases').status_code == 200
    assert request_as('typist', 'POST', '/leases').status_code == 404
    with app.app_context():
        admin = _user('admin')
        admin.is_admin = True
        db.session.commit()
        pools.allow(ids['typist'], 'pool', actor_id=admin.id, pool_id=ids['author_pool'], actions=('edit',))
    for role in ('author', 'typist'):
        response = request_as(role, 'POST', '/leases')
        assert response.status_code == 200, response.json
        proof = {key: response.json[key] for key in ('lease_id', 'generation')}
        assert request_as(role, 'POST', '/leases/renew', json=proof).status_code == 200
        detail = request_as(role, 'GET', '/detail')
        assert detail.status_code == 200 and 'release' not in detail.json['allowed_actions']
        assert request_as(role, 'POST', '/leases/release', json=proof).status_code == 200
