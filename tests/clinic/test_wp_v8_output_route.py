"""Exercise P16 through the real app startup, session guard, and object policy."""

import os
import subprocess
import sys
from pathlib import Path

import pytest


WORKER = r'''
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


'''

SEED = r'''
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

'''

APP_CHECK = WORKER + SEED + r'''
import sys
from src.clinic.kis import DisabledAdapter

app.extensions['clinic_kis_adapter'] = DisabledAdapter()
expected_kis = [{'id': 'kis', 'channel': 'kis', 'available': False, 'reason': 'kis_unavailable'}]
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
        *(expected_kis if sys.argv[1] == 'staging' else []),
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


@pytest.mark.parametrize('profile', ['production', 'staging'])
def test_output_targets_use_view_and_export_rights_separately(tmp_path, profile):
    env = {
        **os.environ,
        'CLINIC_MODE': '1',
        'CLINIC_PROFILE': profile,
        'CLINIC_KIS_DELIVERY_ENABLED': '1',
        'SQLALCHEMY_DATABASE_URI': f'sqlite:///{tmp_path / "output.db"}',
        'UPLOAD_FOLDER': str(tmp_path / 'uploads'),
        'TRANSCRIPTION_BASE_URL': 'http://127.0.0.1:9/v1',
    }
    result = subprocess.run([sys.executable, '-c', APP_CHECK, profile], cwd=Path(__file__).resolve().parents[2],
                            env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


def test_file_export_status_and_replay_survive_worker_change(tmp_path):
    env = {**os.environ, 'CLINIC_MODE': '1', 'CLINIC_PROFILE': 'production',
           'SPEAKR_SKIP_BACKGROUND_TASKS': 'true', 'UPLOAD_FOLDER': str(tmp_path / 'uploads'),
           'SQLALCHEMY_DATABASE_URI': f'sqlite:///{tmp_path / "workers.db"}',
           'TRANSCRIPTION_BASE_URL': 'http://127.0.0.1:9/v1'}
    transfer = tmp_path / 'file-export.json'
    request = r'''
import json
import sys
from pathlib import Path
transfer = Path(sys.argv[1])
def submit(client, fmt):
    csrf = client.get('/api/csrf-token').json['csrf_token']
    return client.post(base + '/exports', json={'revision_id': revision_id, 'format': fmt},
                       headers={'Idempotency-Key': key, 'X-CSRF-Token': csrf})
'''
    create = r'''
base = f'/api/clinic/v1/dictations/{dictation_id}'
key = str(uuid4())
first = submit(client_for(author), 'docx')
assert first.status_code == 201, first.status_code
assert first.json['status'] == 'ready' and not first.json['replayed']
transfer.write_text(json.dumps([base, key, revision_id, author, typist, admin, first.json]))
'''
    follow = r'''
base, key, revision_id, author, typist, admin, first = json.loads(transfer.read_text())
url = base + '/exports/' + first['id']
client = client_for(author)
status = client.get(url)
assert status.status_code == 200, ('worker B GET after worker A POST 201', status.status_code)
assert status.json['sha256'] == first['sha256'] and status.json['release_id'] == first['release_id']
exact = submit(client, 'docx')
assert exact.status_code == 200 and exact.json == (first | {'replayed': True})
clash = submit(client, 'txt')
assert clash.status_code == 409 and clash.json['code'] == 'IDEMPOTENCY_CONFLICT'
with app.app_context():
    pools.allow(typist, 'all', actor_id=admin, actions=('export',))
assert client_for(typist).get(url).status_code == 404
assert app.test_client().get(url).status_code == 401
with app.app_context():
    pools.deny(author, 'own', actor_id=admin, actions=('export',))
assert client.get(url).status_code == 404
assert submit(client, 'docx').status_code == 404
'''
    for code in (WORKER + SEED + request + create, WORKER + request + follow):
        run = subprocess.run([sys.executable, '-c', code, str(transfer)], cwd=Path(__file__).resolve().parents[2],
                             env=env, capture_output=True, text=True, timeout=60)
        assert run.returncode == 0, run.stdout + run.stderr
