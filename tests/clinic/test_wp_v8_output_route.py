"""Exercise P16 through the real app startup, session guard, and object policy."""

import os
import subprocess
import sys
from pathlib import Path


APP_CHECK = r'''
from datetime import datetime, timezone
from uuid import uuid4

from src.app import app, db
from src.clinic import identity, pools, release, revisions
from src.clinic.models import ClinicAudioRevision, ClinicContextRevision, ClinicDictation, ClinicOriginal
from src.init_db import initialize_database
from src.models import Recording, User


def account(name, admin=False):
    user = User(username=name, email=f'{name}@local.test', password='unused', is_admin=admin)
    db.session.add(user)
    db.session.flush()
    db.session.add(identity.ClinicAccountPolicy(user_id=user.id, status='active'))
    return user.id


def client_for(user_id):
    client = app.test_client()
    with client.session_transaction() as session:
        session.update(_user_id=str(user_id), _fresh=True, clinic_provider='local', clinic_security_version=0)
    return client


with app.app_context():
    initialize_database(app)
    admin = account('admin', admin=True)
    author = account('author')
    typist = account('typist')
    outside = account('outside')
    recording = Recording(user_id=author, title='Synthetisch', status='COMPLETED', original_filename='s.wav')
    db.session.add(recording)
    db.session.flush()
    dictation = ClinicDictation(recording_id=recording.id, author_user_id=author)
    db.session.add(dictation)
    db.session.flush()
    audio = ClinicAudioRevision(dictation_id=dictation.id, content_hash='a' * 64)
    original = ClinicOriginal(dictation_id=dictation.id, content_hash='b' * 64, size_bytes=1,
                              storage_key='originals/synthetic', receipt='stored', origin_ref='test')
    context = ClinicContextRevision(dictation_id=dictation.id, source_system='test',
                                    tenant_scope='site', case_ref='test-case', patient_ref='opaque')
    db.session.add_all([audio, original, context])
    db.session.flush()
    text = revisions.add_human_revision(dictation, None, 'Synthetischer Text', author, original_version=original)
    release.create_release(dictation, audio, text, context, text.normalised_text_hash, author,
                           {'allowed': True}, datetime.now(timezone.utc),
                           document_type='report', release_type='clinical')
    dictation_id, revision_id = dictation.id, text.id
    db.session.commit()
    pools.allow(author, 'own', actor_id=admin, actions=('export',))
    pools.allow(typist, 'all', actor_id=admin, actions=('view', 'play', 'edit', 'release'))

base = f'/api/clinic/v1/dictations/{dictation_id}'
url = base + '/output-targets'
assert app.test_client().get(url).status_code == 401

denied = client_for(outside).get(url)
assert denied.status_code == 404 and denied.mimetype == 'application/problem+json'
assert denied.json['code'] == 'NOT_FOUND'

allowed = client_for(author).get(url)
assert allowed.status_code == 200, (allowed.status_code, allowed.json)
assert allowed.headers['Cache-Control'] == 'no-store'
assert allowed.json == {
    'dictation_id': dictation_id,
    'released_revision_id': revision_id,
    'targets': [
        {'id': 'txt', 'channel': 'file', 'available': True, 'reason': None},
        {'id': 'docx', 'channel': 'file', 'available': True, 'reason': None},
        {'id': 'kis', 'channel': 'kis', 'available': False, 'reason': 'kis_unavailable'},
        {'id': 'clipboard', 'channel': 'clipboard', 'available': True, 'reason': None},
    ],
}

reader = client_for(typist)
listed = reader.get(url)
assert listed.status_code == 200, (listed.status_code, listed.json)
assert listed.headers['Cache-Control'] == 'no-store'
assert listed.json == {
    'dictation_id': dictation_id,
    'released_revision_id': None,
    'targets': [dict(target, available=False, reason='forbidden') for target in allowed.json['targets']],
}

key = {'Idempotency-Key': str(uuid4())}
payload = {'revision_id': revision_id, 'format': 'txt'}
without_csrf = reader.post(base + '/exports', json=payload, headers=key)
assert without_csrf.status_code == 400 and without_csrf.json['csrf_reason']
csrf = reader.get('/api/csrf-token').json['csrf_token']
write = reader.post(base + '/exports', json=payload, headers={**key, 'X-CSRF-Token': csrf})
assert write.status_code == 404 and write.mimetype == 'application/problem+json'
assert reader.get(f'{base}/exports/{uuid4()}').status_code == 404
'''


def test_output_targets_use_view_and_export_rights_separately(tmp_path):
    env = {
        **os.environ,
        'CLINIC_MODE': '1',
        'CLINIC_PROFILE': 'production',
        'SQLALCHEMY_DATABASE_URI': f'sqlite:///{tmp_path / "output.db"}',
        'UPLOAD_FOLDER': str(tmp_path / 'uploads'),
        'TRANSCRIPTION_BASE_URL': 'http://127.0.0.1:9/v1',
    }
    result = subprocess.run([sys.executable, '-c', APP_CHECK], cwd=Path(__file__).resolve().parents[2],
                            env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
