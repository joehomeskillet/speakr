"""W13 assignment through the real clinic-mode Flask startup and database."""

import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from src.clinic import lease, pools, workflow
from src.clinic.identity import ClinicAccountPolicy
from src.clinic.models import ClinicDictation
from src.database import db
from src.models import AuditEvent, Group, GroupMembership, Recording, User
from src.services import audit

ROOT = Path(__file__).resolve().parents[2]


def test_real_app_assignment(tmp_path):
    env = {**os.environ, 'CLINIC_MODE': '1', 'SPEAKR_SKIP_BACKGROUND_TASKS': 'true', 'PYTHONPATH': str(ROOT),
           'TRANSCRIPTION_BASE_URL': 'http://127.0.0.1:9/v1',
           'SQLALCHEMY_DATABASE_URI': f'sqlite:///{tmp_path / "assignment.db"}',
           'UPLOAD_FOLDER': str(tmp_path / 'uploads')}
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve())],
        env=env, cwd=ROOT, capture_output=True, text=True, timeout=120, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _user(name, *, admin=False, status='active'):
    user = User(username=name, name=name.capitalize(), email=f'{name}@example.invalid',
                password='synthetic', is_admin=admin)
    db.session.add(user)
    db.session.flush()
    db.session.add(ClinicAccountPolicy(user_id=user.id, status=status))
    return user.id


def _staff_pool(owner, name, members):
    group = Group(name=name, created_by=owner)
    db.session.add(group)
    db.session.flush()
    staff = pools.ClinicStaffPool(group_id=group.id, created_by=owner)
    db.session.add(staff)
    db.session.flush()
    db.session.add_all(GroupMembership(group_id=group.id, user_id=user_id, role=role)
                       for user_id, role in members)
    return staff.id


def _dictation(author, pool, *, sent):
    rec = Recording(user_id=author, title='Synthetisches Diktat', status='COMPLETED', original_filename='s.wav')
    db.session.add(rec)
    db.session.flush()
    item = workflow.start(db.session(), 'create', author_user_id=author, pool_id=pool)
    db.session.add(ClinicDictation(id=item.id, recording_id=rec.id, author_user_id=author,
                                   pool_id=pool, version_counter=0))
    if sent:
        workflow.transition(db.session(), item.id, 'send_online')
        workflow.transition(db.session(), item.id, 'recognition_done')
    return item.id, rec.id


def _client(app, actor):
    client = app.test_client()
    with client.session_transaction() as session:
        session.update(_user_id=str(actor), _fresh=True)
    return client, {'X-CSRFToken': client.get('/api/csrf-token').json['csrf_token']}


def _exercise():
    from src.app import app
    from src.init_db import initialize_database

    app.config.update(TESTING=True, WTF_CSRF_ENABLED=True)
    with app.app_context():
        initialize_database(app)
        admin = _user('admin', admin=True)
        author, lead, typist, other_lead = map(_user, ('author', 'lead', 'typist', 'other'))
        revoked = _user('revoked', admin=True, status='disabled')
        pool = pools.ClinicAuthorPool(name='Authors', created_by=admin)
        db.session.add(pool)
        db.session.flush()
        staff = _staff_pool(admin, 'Routed staff', [(lead, 'admin'), (typist, 'member')])
        _staff_pool(admin, 'Unrouted staff', [(other_lead, 'admin')])
        db.session.add(pools.ClinicPoolRoute(author_pool_id=pool.id, staff_pool_id=staff, created_by=admin))
        sent, recording_id = _dictation(author, pool.id, sent=True)
        draft, _ = _dictation(author, pool.id, sent=False)
        db.session.commit()

    def assignment(dictation):
        return f'/api/clinic/v1/dictations/{dictation}/assignment'

    def post(browser, recipient, *, target=sent, version=0, key=None, omit=()):
        c, csrf = browser
        headers = {**csrf, 'Idempotency-Key': key or str(uuid4()), 'If-Match': f'"{target}:{version}"'}
        return c.post(assignment(target), json={'assignee_user_id': recipient},
                      headers={name: value for name, value in headers.items() if name not in omit})

    def candidates(browser, target=sent):
        return browser[0].get(assignment(target) + '/candidates')

    def worklist(browser):
        response = browser[0].get('/api/clinic/v1/worklist/rows')
        assert response.status_code == 200, response.json
        return {row['dictation_id']: row for row in response.json['items']}

    admin_browser, lead_browser, typist_browser = (_client(app, user) for user in (admin, lead, typist))
    expected = [{'id': lead, 'display_name': 'Lead'}, {'id': typist, 'display_name': 'Typist'}]
    for browser in (admin_browser, lead_browser):
        response = candidates(browser)
        assert response.status_code == 200, response.status_code
        assert response.json == expected
        assert response.headers['Cache-Control'] == 'no-store'
        assert candidates(browser, draft).status_code == 409
        assert post(browser, typist, target=draft).status_code == 409

    for browser in (typist_browser, *(_client(app, user) for user in (author, other_lead, revoked))):
        assert candidates(browser).status_code == 403
        assert post(browser, typist).status_code == 403
    assert sent not in worklist(typist_browser)
    assert post(admin_browser, typist, omit=('X-CSRFToken',)).status_code == 400
    assert post(admin_browser, typist, omit=('Idempotency-Key',)).status_code == 400
    assert post(admin_browser, typist, omit=('If-Match',)).status_code == 428
    assert post(admin_browser, True).status_code == 400
    assert post(admin_browser, other_lead).status_code == 422
    with patch.object(audit, 'record_in_tx', return_value=None):
        assert post(admin_browser, typist).status_code == 503

    key = str(uuid4())
    assigned = post(admin_browser, typist, key=key)
    assert assigned.status_code == 200, assigned.json
    assert assigned.json['assignee_user_id'] == typist
    assert assigned.json['etag'] == f'"{sent}:1"'
    assert post(admin_browser, typist, key=key).json == assigned.json
    assert post(admin_browser, lead, key=key).json['code'] == 'IDEMPOTENCY_CONFLICT'
    assert post(admin_browser, lead).json['code'] == 'STALE_VERSION'
    stamped = worklist(typist_browser)[sent]
    assert stamped['assignee'] == expected[1] | {'id': str(typist)}
    assert stamped['assignee_user_id'] == typist and stamped['recipient'] == 'Typist'
    first_at = _utc(stamped['assigned_at'])
    with app.app_context():
        stored = db.session.get(workflow.ClinicWorkflowDictation, sent).assigned_at
        assert stored == first_at

    with app.app_context():
        generation = lease.acquire(sent, typist, datetime.now(timezone.utc)).generation
        workflow.transition(db.session(), sent, 'open')
        db.session.get(workflow.ClinicWorkflowDictation, sent).assigned_at = datetime(
            2020, 1, 1, tzinfo=timezone.utc)
        db.session.commit()

    viewers = {lead: lead_browser, typist: typist_browser}
    names = {lead: 'Lead', typist: 'Typist'}
    previous = datetime(2020, 1, 1, tzinfo=timezone.utc)
    for version, recipient in enumerate((lead, typist, None), 1):
        reply = post(lead_browser, recipient, version=version)
        assert reply.status_code == 200, reply.json
        assert reply.json['assignee_user_id'] == recipient
        if recipient is None:
            cleared = worklist(lead_browser)[sent]
            assert cleared['assignee_user_id'] is None
            assert cleared['recipient'] is None and cleared['assigned_at'] is None
        else:
            row = worklist(viewers[recipient])[sent]
            assert row['assignee_user_id'] == recipient and row['recipient'] == names[recipient]
            current = _utc(row['assigned_at'])
            assert current > previous
            previous = current
    assert sent not in worklist(typist_browser)
    with app.app_context():
        assert lease.current(sent).expires_at <= datetime.now(timezone.utc)
        assert db.session.get(workflow.ClinicWorkflowDictation, sent).status == workflow.TO_CORRECT
        events = AuditEvent.query.filter_by(event='recording.edited', recording_id=recording_id).order_by(AuditEvent.id)
        assert [(row.actor_user_id, row.target_user_id, row.details) for row in events] == [
            (admin, typist, {'fields': ['assignee']}),
            (lead, typist, {'fields': [
                'lease.release', f'dictation:{sent}', f'generation:{generation}', f'from:{typist}',
            ]}),
            (lead, lead, {'fields': ['assignee']}),
            (lead, typist, {'fields': ['assignee']}),
            (lead, None, {'fields': ['assignee']}),
        ]
        pools.deny(lead, 'pool', pool_id=db.session.get(ClinicDictation, sent).pool_id,
                   actions=['reassign'], actor_id=admin)
    assert candidates(lead_browser).status_code == 403
    assert post(lead_browser, typist, version=4).status_code == 403


def _utc(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    assert parsed.utcoffset() == timedelta(0)
    return parsed


if __name__ == '__main__':
    _exercise()
