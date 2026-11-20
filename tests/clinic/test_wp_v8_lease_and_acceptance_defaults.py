"""Exercise the mounted clinic routes with synthetic data in an isolated app process."""

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest

from src.clinic import pools, revisions, workflow
from src.clinic.acceptance_policy import SCOPE_DICTATION, AcceptanceLedger
from src.clinic.identity import ClinicAccountPolicy
from src.clinic.models import ClinicAudioRevision, ClinicContextRevision, ClinicDictation, ClinicOriginal
from src.database import db
from src.models import Group, GroupMembership, Recording, User

ROOT = Path(__file__).resolve().parents[2]
TEXT = 'Synthetischer Text.'


def _add(row):
    db.session.add(row)
    db.session.flush()
    return row


def _seed(app):
    with app.app_context():
        users = {}
        for role in ('author', 'typist', 'staff_only', 'admin'):
            user = _add(User(username=role, email=f'{role}@example.invalid', password='synthetic',
                             is_admin=role == 'admin'))
            users[role] = user.id
            db.session.add(ClinicAccountPolicy(user_id=user.id, status='active', login_policy='local_only'))
        author, typist, admin = (users[role] for role in ('author', 'typist', 'admin'))
        pool = pools.create_author_pool('Autoren', actor_id=admin)
        pools.add_author_member(pool.id, author, actor_id=admin)
        group = _add(Group(name='Schreibdienst', created_by=admin))
        staff = _add(pools.ClinicStaffPool(group_id=group.id, created_by=admin))
        pools.link_staff_pool(pool.id, staff.id, actor_id=admin)
        for member in (typist, users['staff_only']):
            db.session.add(GroupMembership(group_id=group.id, user_id=member, role='member'))
        pools.allow(typist, 'pool', actor_id=admin, pool_id=pool.id, actions=('play', 'edit'))

        item = workflow.start(db.session(), 'create', author_user_id=author, pool_id=pool.id,
                              assignee_user_id=typist)
        recording = _add(Recording(user_id=author, title='Synthetisch', status='COMPLETED'))
        dictation = _add(ClinicDictation(id=item.id, recording_id=recording.id, author_user_id=author,
                                         pool_id=pool.id))
        for trigger in ('send_offline', 'skip_recognition'):
            workflow.transition(db.session(), dictation.id, trigger)
        original = _add(ClinicOriginal(dictation_id=dictation.id, content_hash='a' * 64, size_bytes=1,
                                         storage_key='original/synthetic', receipt='stored', origin_ref='test'))
        head = revisions.add_human_revision(dictation, None, TEXT, author, original_version=original)
        audio = _add(ClinicAudioRevision(dictation_id=dictation.id, content_hash='b' * 64))
        context = _add(ClinicContextRevision(dictation_id=dictation.id, source_system='test',
                                             tenant_scope='test', case_ref='SYN-0001', patient_ref='opaque'))
        db.session.commit()
        return users, dict(id=dictation.id, head=head.id, audio=audio.id, context=context.id)


class Browser:
    def __init__(self, app, user_id, dictation_id):
        self.client = app.test_client()
        self.base = f'/api/clinic/v1/dictations/{dictation_id}'
        with self.client.session_transaction() as session:
            session['_user_id'] = str(user_id)
            session['_fresh'] = True
        self.csrf = self.client.get('/api/csrf-token').json['csrf_token']

    def get(self, path):
        response = self.client.get(self.base + path)
        assert response.status_code == 200, response.json
        return response.json

    def write(self, path, *, body=None, if_match=None, method='POST'):
        headers = {'X-CSRFToken': self.csrf, 'Idempotency-Key': str(uuid4())}
        if if_match is not None:
            headers['If-Match'] = if_match
        return self.client.open(self.base + path, method=method, headers=headers, json=body)

    def acquire(self):
        response = self.write('/leases')
        assert response.status_code == 200, response.json
        return response.json

    def publish(self, required, approvers, version):
        return self.write('/acceptance-policy', method='PUT', if_match=f'"{version}"',
                          body={'acceptance_required': required, 'approver_ids': approvers})

    def release(self, target, held, text_revision, text):
        return self.write('/releases', if_match=self.get('/detail')['etag'], body={
            'lease_id': held['lease_id'], 'lease_generation': held['generation'],
            'original_version_id': target['audio'], 'transcript_revision_id': text_revision,
            'context_revision_id': target['context'], 'document_type': 'report',
            'mode': 'PLATFORM_REVIEWED_KIS_DRAFT', 'text_sha256': revisions.text_hash(text),
            'confirmed_content': True, 'confirmed_context': True,
        })


def _can_save(app, users, target, role):
    browser = Browser(app, users[role], target['id'])
    held = browser.acquire()
    text = TEXT + ' Ergänzt.'
    saved = browser.write('/correction/save', if_match=f'"{target["head"]}.g{held["generation"]}"', body={
        'text': text, 'audio_position_ms': 0, 'lease_generation': held['generation'],
        'expected_text_revision_id': target['head'],
    })
    assert saved.status_code == 200, saved.json
    assert saved.json['text_revision_id'] != target['head']
    assert browser.get('/correction')['text'] == text
    if role == 'typist':
        denied = browser.release(target, held, saved.json['text_revision_id'], text)
        # The release route currently masks permission denials as 404.
        assert denied.status_code == 404, denied.json
        assert 'release' not in browser.get('/detail')['allowed_actions']


def _staff_cannot_lease(app, users, target):
    browser = Browser(app, users['staff_only'], target['id'])
    assert browser.get('/detail')['dictation_id'] == target['id']
    assert browser.write('/leases').status_code == 404


def _default_allows_release(app, users, target):
    with app.app_context():
        pools.allow(users['author'], 'own', actor_id=users['admin'], actions=('release',))
    author = Browser(app, users['author'], target['id'])
    held = author.acquire()
    with patch('src.clinic.finalize_gates.trusted_context', return_value=True):
        released = author.release(target, held, target['head'], TEXT)
    assert released.status_code == 201, released.json
    with app.app_context():
        assert AcceptanceLedger().current(SCOPE_DICTATION, target['id']).required is False


def _publication_controls_submit(app, users, target):
    with app.app_context():
        pools.allow(users['author'], 'own', actor_id=users['admin'], actions=('release',))
    author = Browser(app, users['author'], target['id'])
    admin = Browser(app, users['admin'], target['id'])
    initial = {'version': 0, 'acceptance_required': False, 'approver_ids': []}
    assert admin.get('/acceptance-policy') == initial
    assert 'submit' not in author.get('/detail')['allowed_actions']

    # Exercise rollback when audit logging has not started a SQLite write transaction.
    with (patch('src.services.audit.record_in_tx', return_value=None),
          patch('src.clinic.workflow.set_acceptance', side_effect=RuntimeError('synthetic write failure'))):
        assert admin.publish(True, [users['admin']], 0).status_code == 500
    assert admin.get('/acceptance-policy') == initial

    for version, required in enumerate((True, False)):
        saved = admin.publish(required, [users['admin']] if required else [], version)
        assert saved.status_code == 200, saved.json
        assert saved.json['version'] == version + 1
        with app.app_context():
            item = db.session.get(workflow.ClinicWorkflowDictation, target['id'])
            assert item.acceptance_required is required
        detail = author.get('/detail')
        assert ('submit' in detail['allowed_actions']) is required
        if required:
            assert detail['transitions']['submit'] == workflow.TO_ACCEPT


@pytest.mark.parametrize('case', ['author', 'typist', 'staff_only', 'finalize', 'publication'])
def test_mounted_lease_and_acceptance_defaults(tmp_path, case):
    env = {**os.environ, 'CLINIC_MODE': '1', 'CLINIC_PROFILE': 'staging', 'PYTHONPATH': str(ROOT),
           'SQLALCHEMY_DATABASE_URI': f'sqlite:///{tmp_path / "clinic.db"}',
           'UPLOAD_FOLDER': str(tmp_path / 'uploads'), 'SPEAKR_SKIP_BACKGROUND_TASKS': 'true'}
    done = subprocess.run([sys.executable, __file__, case], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr


if __name__ == '__main__':
    from src.app import app
    from src.init_db import initialize_database

    with app.app_context():
        initialize_database(app)
    users, target = _seed(app)
    case = sys.argv[1]
    if case in ('author', 'typist'):
        _can_save(app, users, target, case)
    else:
        {'staff_only': _staff_cannot_lease, 'finalize': _default_allows_release,
         'publication': _publication_controls_submit}[case](app, users, target)
