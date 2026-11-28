"""PAR-ADM-005 / PAR-WF-012: each registered action uses its own central grant.

Synthetic users only. The real clinic app decides, so edit does not rename or
set priority, release does not reopen, a deny beats an allow, and a grant
withdrawn after the detail dialog was loaded no longer works. W13 assignment
stays with an admin or a pool lead.
"""

import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]


def test_registered_actions_follow_their_own_grants(tmp_path):
    env = {**os.environ, 'CLINIC_MODE': '1', 'SPEAKR_SKIP_BACKGROUND_TASKS': 'true',
           'PYTHONPATH': str(ROOT), 'TRANSCRIPTION_BASE_URL': 'http://127.0.0.1:9/v1',
           'SQLALCHEMY_DATABASE_URI': f'sqlite:///{tmp_path / "actions.db"}',
           'UPLOAD_FOLDER': str(tmp_path / 'uploads')}
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve())],
        env=env, cwd=ROOT, capture_output=True, text=True, timeout=180, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _exercise():
    from src.app import app, db
    from src.clinic import pools, workflow
    from src.clinic.identity import ClinicAccountPolicy
    from src.clinic.models import ClinicDictation
    from src.init_db import initialize_database
    from src.models import Group, GroupMembership, Recording, User

    app.config.update(TESTING=True, WTF_CSRF_ENABLED=True)
    with app.app_context():
        initialize_database(app)

        def user(name, *, admin=False):
            row = User(username=name, name=name, email=f'{name}@example.invalid',
                       password='synthetic', is_admin=admin)
            db.session.add(row)
            db.session.flush()
            db.session.add(ClinicAccountPolicy(user_id=row.id, status='active'))
            return row.id

        admin = user('admin', admin=True)
        author, lead, schreib1, schreib2, prio = (
            user(name) for name in ('author', 'lead', 'schreib1', 'schreib2', 'prio'))
        pool = pools.ClinicAuthorPool(name='Authors', created_by=admin)
        db.session.add(pool)
        db.session.flush()
        group = Group(name='Schreibkraefte', created_by=admin)
        db.session.add(group)
        db.session.flush()
        staff = pools.ClinicStaffPool(group_id=group.id, created_by=admin)
        db.session.add(staff)
        db.session.flush()
        db.session.add_all(
            GroupMembership(group_id=group.id, user_id=member, role=role)
            for member, role in ((lead, 'admin'), (schreib1, 'member'), (schreib2, 'member')))
        db.session.add(pools.ClinicPoolRoute(
            author_pool_id=pool.id, staff_pool_id=staff.id, created_by=admin))

        def make(*, done=False):
            recording = Recording(user_id=author, title='Alpha', status='COMPLETED',
                                  original_filename='a.wav')
            db.session.add(recording)
            db.session.flush()
            item = workflow.start(db.session(), 'create', author_user_id=author, pool_id=pool.id)
            db.session.add(ClinicDictation(
                id=item.id, recording_id=recording.id, author_user_id=author,
                pool_id=pool.id, version_counter=0))
            workflow.transition(db.session(), item.id, 'send_online')
            workflow.transition(db.session(), item.id, 'skip_recognition')
            if done:
                workflow.transition(db.session(), item.id, 'complete')
            return item.id

        meta, meta_prio, sent, done_ok, done_revoke, done_deny = (
            make(done=flag) for flag in (False, False, False, True, True, True))
        db.session.commit()
        base = pools.allow(
            schreib1, 'all', actor_id=admin,
            actions=['view', 'edit', 'rename', 'reassign', 'reopen']).id
        pools.allow(schreib2, 'all', actor_id=admin, actions=['view', 'release'])
        pools.allow(prio, 'all', actor_id=admin, actions=['priority'])

    def mutate(effect, actor, actions):
        with app.app_context():
            fn = pools.allow if effect == 'allow' else pools.deny
            return fn(actor, 'all', actor_id=admin, actions=list(actions)).id

    def browser(actor):
        client = app.test_client()
        with client.session_transaction() as session:
            session.update(_user_id=str(actor), _fresh=True)
        token = client.get('/api/csrf-token').json['csrf_token']
        return client, {'X-CSRF-Token': token, 'X-CSRFToken': token}

    def state(did):
        with app.app_context():
            row = db.session.get(ClinicDictation, did, populate_existing=True)
            item = db.session.get(workflow.ClinicWorkflowDictation, did, populate_existing=True)
            recording = db.session.get(Recording, row.recording_id, populate_existing=True)
            return recording.title, item.priority, item.status, row.version_counter

    def call(client, headers, path, body, did, version=None):
        headers = {**headers, 'Idempotency-Key': str(uuid4())}
        if version is not None:
            headers['If-Match'] = f'"{did}:{version}"'
        if body is None:
            return client.post(path, headers=headers)
        return client.post(path, headers=headers, json=body)

    one, two, only_prio, lead_browser = (browser(actor) for actor in (schreib1, schreib2, prio, lead))

    def meta_post(who, did, fields):
        client, headers = who
        return call(client, headers, f'/api/clinic/v1/dictations/{did}/metadata',
                    {'fields': fields}, did, state(did)[3])

    def lease(who, did):
        client, headers = who
        response = call(client, headers, f'/api/clinic/v1/dictations/{did}/leases', None, did)
        assert response.status_code == 200, response.get_data(as_text=True)
        return response

    def reopen(who, did):
        client, headers = who
        return call(client, headers, '/api/clinic/v1/parity/commands', {
            'command': 'reopen_dictation', 'request_id': str(uuid4()),
            'expected_revision': state(did)[3] + 1,
            'payload': {'dictation_id': did, 'reason': 'erneut pruefen'},
        }, did)

    # A reassign grant does not make a pool member a W13 lead. The lead still assigns.
    refused = call(one[0], one[1], f'/api/clinic/v1/dictations/{sent}/assignment',
                   {'assignee_user_id': schreib2}, sent, 0)
    assert refused.status_code == 403
    assigned = call(lead_browser[0], lead_browser[1], f'/api/clinic/v1/dictations/{sent}/assignment',
                    {'assignee_user_id': schreib1}, sent, 0)
    assert assigned.status_code == 200, assigned.get_data(as_text=True)
    assert assigned.json['assignee_user_id'] == schreib1
    assert state(sent)[0] == 'Alpha'

    # edit allows the write, rename deny blocks the title, version stays put.
    lease(one, meta)
    before = state(meta)
    mutate('deny', schreib1, ['rename'])
    denied_title = meta_post(one, meta, {'title': 'Neu'})
    assert denied_title.status_code == 403 and denied_title.json['code'] == 'FORBIDDEN'
    assert state(meta) == before

    # Priority follows its own grant: edit does not confer it, and it does not need edit.
    refused_priority = meta_post(one, meta, {'priority': 128})
    assert refused_priority.status_code == 403 and state(meta) == before
    mutate('allow', schreib1, ['priority'])
    changed = meta_post(one, meta, {'priority': 128})
    assert changed.status_code == 200, changed.get_data(as_text=True)
    assert state(meta)[1] == 128 and state(meta)[3] == before[3] + 1
    mutate('deny', schreib1, ['priority'])
    held = state(meta)
    assert meta_post(one, meta, {'priority': 0}).status_code == 403
    assert state(meta) == held

    lease(only_prio, meta_prio)
    priority_only = meta_post(only_prio, meta_prio, {'priority': 128})
    assert priority_only.status_code == 200, priority_only.get_data(as_text=True)
    assert state(meta_prio)[1] == 128
    assert meta_post(only_prio, meta_prio, {'title': 'Fremd'}).status_code == 403
    assert state(meta_prio)[0] == 'Alpha'

    # Release does not reopen. Reopen does, including after the dialog was opened.
    assert 'reopen' not in two[0].get(f'/api/clinic/v1/dictations/{done_ok}/detail').json['allowed_actions']
    assert reopen(two, done_ok).json['code'] == 'POLICY_DENIED'
    assert state(done_ok)[2:] == (workflow.DONE, 0)

    opened = one[0].get(f'/api/clinic/v1/dictations/{done_ok}/detail')
    assert opened.status_code == 200 and 'reopen' in opened.json['allowed_actions']
    lease(one, done_ok)
    assert reopen(one, done_ok).status_code == 200
    assert state(done_ok)[2] == workflow.TO_CORRECT

    dialog = one[0].get(f'/api/clinic/v1/dictations/{done_revoke}/detail')
    assert 'reopen' in dialog.json['allowed_actions']
    with app.app_context():
        pools.revoke(base, actor_id=admin)
    revoked = state(done_revoke)
    assert reopen(one, done_revoke).json['code'] == 'POLICY_DENIED'
    assert state(done_revoke) == revoked

    mutate('allow', schreib1, ['view', 'reopen'])
    opened_again = one[0].get(f'/api/clinic/v1/dictations/{done_deny}/detail')
    assert 'reopen' in opened_again.json['allowed_actions']
    mutate('deny', schreib1, ['reopen'])
    shown = one[0].get(f'/api/clinic/v1/dictations/{done_deny}/detail')
    assert shown.status_code == 200 and 'reopen' not in shown.json['allowed_actions']
    blocked = state(done_deny)
    assert reopen(one, done_deny).json['code'] == 'POLICY_DENIED'
    assert state(done_deny) == blocked


if __name__ == '__main__':
    _exercise()
