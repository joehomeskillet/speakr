"""P-146: active author pool routing through real clinic-mode startup."""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from src.clinic import identity, pools
from src.clinic.models import ClinicDictation
from src.clinic.routes_preferences import PREFIX
from src.database import db
from src.models import User

ROOT = Path(__file__).resolve().parents[2]
ACTIVE_POOL = f'{PREFIX}/active-pool'
RECIPIENT_DEFAULT = f'{PREFIX}/recipient-default'
PASSWORD = 'synthetic active-pool passphrase'


def test_active_pool_through_real_app(tmp_path):
    env = {**os.environ, 'CLINIC_MODE': '1', 'SPEAKR_SKIP_BACKGROUND_TASKS': 'true',
           'PYTHONPATH': str(ROOT), 'TRANSCRIPTION_BASE_URL': 'http://127.0.0.1:9/v1',
           'SQLALCHEMY_DATABASE_URI': f'sqlite:///{tmp_path / "active-pool.db"}',
           'UPLOAD_FOLDER': str(tmp_path / 'uploads'), 'CLINIC_ORIGINALS_ROOT': str(tmp_path / 'originals')}
    result = subprocess.run([sys.executable, str(Path(__file__).resolve())], cwd=ROOT,
                            env=env, capture_output=True, text=True, timeout=90, check=False)
    assert result.returncode == 0, result.stdout + result.stderr


def _exercise() -> None:
    from src.app import app
    from src.init_db import initialize_database

    app.config.update(TESTING=True, WTF_CSRF_ENABLED=True)
    with app.app_context():
        initialize_database(app)
        alpha = User(username='alpha', email='alpha@example.invalid', password=identity.hash_password(PASSWORD))
        admin = User(username='admin146', email='admin146@example.invalid', password='x', is_admin=True)
        anne = User(username='anne', email='anne@example.invalid', password='x')
        bea = User(username='bea', email='bea@example.invalid', password='x')
        db.session.add_all([alpha, admin, anne, bea])
        db.session.flush()
        alpha_id, admin_id, anne_id, bea_id = alpha.id, admin.id, anne.id, bea.id
        db.session.add(identity.ClinicAccountPolicy(user_id=alpha_id, status='active', login_policy='local_only'))
        db.session.commit()
        a, b, foreign = (pools.create_author_pool(name, actor_id=admin_id).id for name in ('A', 'B', 'Foreign'))
        for author_pool, recipient in ((a, anne_id), (b, bea_id)):
            pools.add_author_member(author_pool, alpha_id, actor_id=admin_id)
            staff = pools.create_staff_pool(f'Staff {recipient}', actor_id=admin_id).id
            pools.add_staff_member(staff, recipient, actor_id=admin_id)
            pools.link_staff_pool(author_pool, staff, actor_id=admin_id)

    def sign_in():
        client = app.test_client()
        csrf = client.get('/api/csrf-token').json['csrf_token']
        response = client.post('/login', data={'login_id': 'alpha', 'password': PASSWORD, 'csrf_token': csrf})
        assert response.status_code == 302, response.get_data(as_text=True)
        with client.session_transaction() as session:
            assert session['_user_id'] == str(alpha_id)
        return client, {'X-CSRF-Token': client.get('/api/csrf-token').json['csrf_token']}

    def assert_pool(response, selected, *options):
        names = {a: 'A', b: 'B'}
        assert response.status_code == 200, response.get_data(as_text=True)
        assert response.json == {'pool_id': selected, 'reselection_required': selected is None,
                                 'pools': [{'id': pid, 'name': names[pid]} for pid in options]}

    def upload(client, headers, sample):
        with patch('src.services.recording_ingestion.get_duration', return_value=None), \
             patch('src.services.recording_ingestion.get_creation_date', return_value=None), \
             patch('src.services.webhook_dispatch.emit_webhook_event'):
            return client.post('/upload', headers=headers, data={
                'file': (BytesIO(b'\x03ds2' + bytes([sample]) * 2048), f'p146-{sample}.ds2'),
                'client_upload_id': f'p146-{uuid4().hex}', 'use_speech_recognition': 'false',
            })

    assert app.test_client().get(ACTIVE_POOL).status_code == 401
    client, headers = sign_in()
    assert_pool(client.get(ACTIVE_POOL), a, a, b)
    missing_csrf = client.put(ACTIVE_POOL, json={'pool_id': b})
    assert (missing_csrf.status_code, missing_csrf.json['code']) == (403, 'unauthorized')
    denied = client.put(ACTIVE_POOL, headers=headers, json={'pool_id': foreign})
    assert (denied.status_code, denied.json['code']) == (403, 'author_pool_not_allowed')
    assert client.put(ACTIVE_POOL, headers=headers, json={'pool_id': [b]}).status_code == 400
    assert_pool(client.get(ACTIVE_POOL), a, a, b)
    assert_pool(client.put(ACTIVE_POOL, headers=headers, json={'pool_id': b}), b, a, b)

    client, headers = sign_in()
    assert_pool(client.get(ACTIVE_POOL), b, a, b)
    assert client.get(RECIPIENT_DEFAULT).json['overrides']['persons'] == [str(bea_id)]
    saved = client.put(RECIPIENT_DEFAULT, headers=headers,
                       json={'mode': 'person', 'target_ids': [str(bea_id)], 'expected_version': 0})
    assert saved.status_code == 200, saved.get_data(as_text=True)
    resolved = client.post(f'{RECIPIENT_DEFAULT}/resolve', headers=headers, json={})
    assert resolved.json['recipient_user_ids'] == [bea_id]
    created = upload(client, headers, 1)
    assert created.status_code == 202, created.get_data(as_text=True)
    did = created.json['dictation_id']
    with app.app_context():
        row = db.session.get(ClinicDictation, did)
        assert (row.author_user_id, row.pool_id) == (alpha_id, b)
        pools.remove_author_member(b, alpha_id, actor_id=admin_id)

    assert_pool(client.get(ACTIVE_POOL), None, a)
    denied = upload(client, headers, 2)
    assert (denied.status_code, denied.json['code']) == (403, 'clinic_author_pool_reselection_required')
    assert client.get(RECIPIENT_DEFAULT).json['overrides'] is None
    denied = client.post(f'{RECIPIENT_DEFAULT}/resolve', headers=headers, json={})
    assert (denied.status_code, denied.json['code']) == (422, 'not_author_pool')
    with app.app_context():
        rows = ClinicDictation.query.filter_by(author_user_id=alpha_id).all()
        assert [(row.id, row.pool_id) for row in rows] == [(did, b)]
    assert_pool(client.put(ACTIVE_POOL, headers=headers, json={'pool_id': a}), a, a)
    assert client.get(RECIPIENT_DEFAULT).json['overrides']['persons'] == [str(anne_id)]
    created = upload(client, headers, 3)
    assert created.status_code == 202, created.get_data(as_text=True)
    with app.app_context():
        assert db.session.get(ClinicDictation, created.json['dictation_id']).pool_id == a
        pools.set_pool_validity('author', a, None, datetime(2000, 1, 1, tzinfo=timezone.utc), actor_id=admin_id)
    assert_pool(client.get(ACTIVE_POOL), None)
    denied = client.put(ACTIVE_POOL, headers=headers, json={'pool_id': a})
    assert (denied.status_code, denied.json['code']) == (403, 'author_pool_not_allowed')


if __name__ == '__main__':
    _exercise()
