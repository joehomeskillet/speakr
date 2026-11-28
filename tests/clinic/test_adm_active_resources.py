"""PAR-ADM-002: resource routes use the current, validated author pool."""

import os
import subprocess
import sys
from pathlib import Path

from flask.testing import FlaskClient

ROOT = Path(__file__).resolve().parents[2]
ACTIVE_POOL = '/api/clinic/v1/preferences/active-pool'
TEMPLATES = '/api/clinic/v1/templates'
COMMANDS = '/api/clinic/v1/resources/commands'
EXECUTE = '/api/clinic/v1/commands/execute'
PASSWORD = 'synthetic active-resource passphrase'
SHARED_PHRASE = 'Gemeinsamer Baustein'
A_ONLY_PHRASE = 'Baustein nur aus A'


def test_active_resource_routes(tmp_path: Path) -> None:
    env = {
        **os.environ,
        'CLINIC_MODE': '1',
        'SPEAKR_SKIP_BACKGROUND_TASKS': 'true',
        'PYTHONPATH': str(ROOT),
        'TRANSCRIPTION_BASE_URL': 'http://127.0.0.1:9/v1',
        'SQLALCHEMY_DATABASE_URI': f'sqlite:///{tmp_path / "active-resources.db"}',
        'UPLOAD_FOLDER': str(tmp_path / 'uploads'),
        'CLINIC_ORIGINALS_ROOT': str(tmp_path / 'originals'),
    }
    result = subprocess.run([sys.executable, str(Path(__file__).resolve())], cwd=ROOT,
                            env=env, capture_output=True, text=True, timeout=90, check=False)
    assert result.returncode == 0, result.stdout + result.stderr


def _exercise() -> None:
    from src.app import app
    from src.clinic import identity, pools, presets, vocabulary
    from src.clinic.models import ClinicDictation
    from src.database import db
    from src.init_db import initialize_database
    from src.models import Recording, User

    app.config.update(TESTING=True, WTF_CSRF_ENABLED=True)
    with app.app_context():
        initialize_database(app)
        password_hash = identity.hash_password(PASSWORD)
        alpha, beta = [User(username=name, email=f'{name}@example.invalid', password=password_hash)
                       for name in ('alpha', 'beta')]
        admin = User(username='admin161', email='admin161@example.invalid', password='x', is_admin=True)
        db.session.add_all([alpha, beta, admin])
        db.session.flush()
        alpha_id, beta_id, admin_id = alpha.id, beta.id, admin.id
        db.session.add_all(identity.ClinicAccountPolicy(user_id=uid, status='active', login_policy='local_only')
                           for uid in (alpha_id, beta_id))
        recording = Recording(user_id=alpha_id, title='Synthetic recording', status='PENDING')
        db.session.add(recording)
        db.session.flush()
        recording_id = recording.id
        db.session.commit()

        a, b = (pools.create_author_pool(name, actor_id=admin_id).id for name in ('A', 'B'))
        for pool_id, user_id in ((a, alpha_id), (b, alpha_id), (b, beta_id)):
            pools.add_author_member(pool_id, user_id, actor_id=admin_id)

        dictation = ClinicDictation(recording_id=recording_id, author_user_id=alpha_id, pool_id=b)
        db.session.add(dictation)
        db.session.flush()
        dictation_query = {'dictation_id': dictation.id}

        templates = {
            label: presets.save_template('Gleichnamige Vorlage', label.encode(), scope=scope, scope_id=scope_id).id
            for label, scope, scope_id in (
                ('A', 'pool', a), ('B', 'pool', b), ('alpha', 'user', alpha_id),
                ('beta', 'user', beta_id), ('system', 'system', None),
            )
        }
        commands = {
            'A': vocabulary.add_voice_command('pool', SHARED_PHRASE, pool_id=a, action_payload='A').id,
            'B': vocabulary.add_voice_command('pool', SHARED_PHRASE, pool_id=b, action_payload='B').id,
            'A-only': vocabulary.add_voice_command('pool', A_ONLY_PHRASE, pool_id=a, action_payload='A-only').id,
            'alpha': vocabulary.add_voice_command('user', 'Persönlich', user_id=alpha_id, action_payload='alpha').id,
            'beta': vocabulary.add_voice_command('user', 'Persönlich', user_id=beta_id, action_payload='beta').id,
        }
        db.session.commit()

    def sign_in(username: str, user_id: int) -> tuple[FlaskClient, dict[str, str]]:
        client = app.test_client()
        csrf = client.get('/api/csrf-token').json['csrf_token']
        response = client.post('/login', data={'login_id': username, 'password': PASSWORD, 'csrf_token': csrf})
        assert response.status_code == 302, response.get_data(as_text=True)
        with client.session_transaction() as session:
            assert session['_user_id'] == str(user_id)
        return client, {'X-CSRF-Token': client.get('/api/csrf-token').json['csrf_token']}

    client, headers = sign_in('alpha', alpha_id)

    def select(pool_id: str) -> None:
        response = client.put(ACTIVE_POOL, headers=headers, json={'pool_id': pool_id})
        assert response.status_code == 200, response.get_data(as_text=True)
        assert response.json['pool_id'] == pool_id
        assert response.json['reselection_required'] is False

    def assert_templates(http: FlaskClient, *labels: str, query: dict[str, str] | None = None) -> None:
        response = http.get(TEMPLATES, query_string=query)
        assert response.status_code == 200, response.get_data(as_text=True)
        assert {item['id'] for item in response.json['items']} == {templates[label] for label in labels}

    def catalog(http: FlaskClient, *labels: str, pool_ids: tuple[str, ...] = ()) -> str:
        response = http.get(COMMANDS, query_string=[('pool_id', pool_id) for pool_id in pool_ids])
        assert response.status_code == 200, response.get_data(as_text=True)
        assert {item['id'] for item in response.json['items']} == {commands[label] for label in labels}
        return response.headers['ETag']

    def assert_template_denied(label: str, query: dict[str, str] | None = None) -> None:
        for suffix in ('', '/content'):
            path = f'{TEMPLATES}/{templates[label]}{suffix}'
            assert client.get(path, query_string=query).status_code == 404

    select(a)
    assert_templates(client, 'A', 'alpha', 'system')
    version_a = catalog(client, 'A', 'A-only', 'alpha')
    select(b)
    assert_templates(client, 'B', 'alpha', 'system')
    version_b = catalog(client, 'B', 'alpha')
    assert version_a != version_b
    assert_template_denied('A')
    assert_template_denied('beta')
    template_b = f'{TEMPLATES}/{templates["B"]}'
    metadata = client.get(template_b)
    assert metadata.status_code == 200
    assert metadata.json['scope_id'] == b
    content = client.get(f'{template_b}/content')
    assert (content.status_code, content.data) == (200, b'B')
    assert client.get(f'{template_b}/content', query_string=dictation_query).data == b'B'
    assert_templates(client, 'B', 'alpha', 'system', query=dictation_query)

    beta_client, _ = sign_in('beta', beta_id)
    assert_templates(beta_client, 'B', 'beta', 'system')
    catalog(beta_client, 'B', 'beta')
    catalog(client, 'B', 'alpha', pool_ids=(a, b))
    personal_version = catalog(client, 'alpha', pool_ids=(a,))
    body = {'phrase': SHARED_PHRASE, 'recording_id': recording_id, 'expected_version': version_b}
    for payload in (body, dict(body, pool_ids=[a, b])):
        executed = client.post(EXECUTE, headers=headers, json=payload)
        assert executed.status_code == 200, executed.get_data(as_text=True)
        assert executed.json['command_id'] == commands['B']
        assert executed.json['actions'] == [{'kind': 'insert_text', 'value': 'B'}]
    narrowed = dict(body, expected_version=personal_version, pool_ids=[a], phrase=A_ONLY_PHRASE)
    denied = client.post(EXECUTE, headers=headers, json=narrowed)
    assert (denied.status_code, denied.json['code']) == (404, 'unknown_command')

    with app.app_context():
        pools.remove_author_member(b, alpha_id, actor_id=admin_id)

    # Try both direct reads and the old command version before refreshing preferences or catalogs.
    assert_template_denied('B')
    assert_template_denied('B', dictation_query)
    assert client.post(f'{template_b}/assign', headers=headers, json={'scope': {'scope': 'user'}}).status_code == 404
    assert client.delete(template_b, headers=headers).status_code == 404
    for payload in (body, dict(body, pool_ids=[a, b])):
        denied = client.post(EXECUTE, headers=headers, json=payload)
        assert (denied.status_code, denied.json['code']) == (409, 'REVISION_CONFLICT')
        assert 'actions' not in denied.json
    refreshed = client.get(COMMANDS, headers={'If-None-Match': version_b})
    assert refreshed.status_code == 200
    assert refreshed.headers['ETag'] == personal_version
    assert refreshed.headers['Cache-Control'] == 'no-store'
    assert_templates(client, 'alpha', 'system', query=dictation_query)
    assert_template_denied('A')
    assert_templates(client, 'alpha', 'system')
    assert catalog(client, 'alpha') == personal_version
    denied = client.post(EXECUTE, headers=headers, json=dict(body, expected_version=personal_version))
    assert (denied.status_code, denied.json['code']) == (404, 'unknown_command')

    select(a)
    assert_templates(client, 'A', 'alpha', 'system')
    assert catalog(client, 'A', 'A-only', 'alpha') == version_a
    assert_template_denied('B')


if __name__ == '__main__':
    _exercise()
