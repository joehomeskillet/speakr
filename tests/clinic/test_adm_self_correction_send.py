"""PAR-ADM-007: send to self and use the web editor's existing lease and save APIs."""

import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from src.clinic import pools, workflow
from src.clinic.models import ClinicOriginal
from src.database import db
from test_wp_mount_correction import _env
from test_wp_v8_assignment_route import ROOT, _client, _staff_pool, _user
from test_wp_v8_send_route import _draft

API = '/api/clinic/v1'
SELF = {'mode': 'self_correction'}


def test_self_correction_send(tmp_path: Path) -> None:
    env = {**_env(tmp_path, True), 'PYTHONPATH': str(ROOT), 'SPEAKR_SKIP_BACKGROUND_TASKS': 'true',
           'TRANSCRIPTION_BASE_URL': 'http://127.0.0.1:9/v1', 'LOG_LEVEL': 'WARNING'}
    result = subprocess.run([sys.executable, __file__], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr


def _exercise() -> None:
    from src.app import app
    from src.init_db import initialize_database

    app.config.update(TESTING=True, WTF_CSRF_ENABLED=True)
    with app.app_context():
        initialize_database(app)
        admin = _user('admin', admin=True)
        author, outsider, lead, typist = map(_user, ('author', 'outsider', 'lead', 'typist'))
        pool = pools.create_author_pool('Authors', actor_id=admin).id
        pools.add_author_member(pool, author, actor_id=admin)
        default, override, linked, ordinary = [_draft(author, pool) for _ in range(4)]
        db.session.add(ClinicOriginal(dictation_id=default, content_hash='b' * 64, size_bytes=1,
                                      storage_key='synthetic', receipt='stored', origin_ref='synthetic'))
        before = pools.ClinicVisibilityGrant.query.count()
        db.session.commit()

    client, csrf = _client(app, author)
    path = f'{API}/dictations/{default}'
    preflight = '/api/config?recording_preflight=1'

    def send(target: str, body: dict, key: str | None = None):
        return client.post(f'{API}/dictations/{target}/send', json=body, headers={
            **csrf, 'Idempotency-Key': key or str(uuid4()), 'If-Match': f'"{target}:0"',
        })

    assert client.get(preflight).json == {'routable': False}
    assert send(ordinary, {}).json['code'] == 'NO_STAFF_POOL'
    assert send(override, {'override': SELF}).status_code == 200
    preference = client.put(f'{API}/preferences/recipient-default',
                            headers={'X-CSRF-Token': csrf['X-CSRFToken']},
                            json={**SELF, 'choice': 'default_only', 'expected_version': 0})
    assert preference.status_code == 200, preference.json
    assert client.get(preflight).json == {'routable': True}
    assert send(default, {'override': SELF}).json['code'] == 'CHOICE_DISABLED'
    assert send(default, {'expected_version': 0}).json['code'] == 'STALE_VERSION'
    with app.app_context():
        for target in (default, ordinary):
            row = db.session.get(workflow.ClinicWorkflowDictation, target)
            assert row.status == workflow.DRAFT and workflow.read_routing_target(row) is None

    key = str(uuid4())
    first, retry = send(default, {}, key), send(default, {}, key)
    assert first.status_code == retry.status_code == 200, (first.json, retry.json)
    assert first.json == retry.json
    detail = client.get(path + '/detail')
    assert detail.json['routing_target']['recipient_user_ids'] == [author]
    assert client.get(path + '/leases').json['may_acquire'] is True
    headers = {**csrf, 'Idempotency-Key': str(uuid4())}
    lease = client.post(path + '/leases', headers=headers)
    assert lease.status_code == 200, lease.json
    proof = {key: lease.json[key] for key in ('lease_id', 'generation')}
    assert client.post(path + '/leases/renew', headers=headers, json=proof).status_code == 200
    correction = client.get(path + '/correction')
    assert correction.status_code == 200
    saved = client.post(path + '/correction/save', headers={**headers, 'If-Match': correction.headers['ETag']},
                        json={'text': 'Korrigierter Text.', 'audio_position_ms': 0,
                              'lease_generation': proof['generation'],
                              'expected_text_revision_id': correction.json['text_revision_id']})
    assert saved.status_code == 200, saved.json
    assert client.get(path + '/correction').json['text'] == 'Korrigierter Text.'
    release = client.post(path + '/releases', headers={**headers, 'If-Match': first.json['etag']}, json={
        'lease_id': proof['lease_id'], 'lease_generation': proof['generation'], 'document_type': 'report',
        'mode': 'PLATFORM_REVIEWED_KIS_DRAFT', 'original_version_id': correction.json['audio_revision_id'],
        'transcript_revision_id': saved.json['text_revision_id'], 'context_revision_id': str(uuid4()),
        'text_sha256': 'a' * 64, 'confirmed_content': True, 'confirmed_context': True,
    })
    assert release.status_code == 404, release.json
    assert client.post(path + '/leases/release', headers=headers, json=proof).status_code == 200

    with app.app_context():
        staff = _staff_pool(admin, 'Staff', [(lead, 'admin'), (typist, 'member')])
        pools.link_staff_pool(pool, staff, actor_id=admin)
    assert send(linked, {}).status_code == 200
    for actor in (outsider, lead, typist):
        other, other_csrf = _client(app, actor)
        items = other.get(f'{API}/worklist/rows').json['items']
        assert not {row['dictation_id'] for row in items} & {default, override, linked}
        target_path = f'{API}/dictations/{linked}'
        assert other.get(target_path + '/detail').status_code == 404
        assert other.post(target_path + '/leases', headers=other_csrf).status_code == 404

    with app.app_context():
        for target, version in ((default, 1), (override, 0), (linked, 1)):
            row = db.session.get(workflow.ClinicWorkflowDictation, target)
            assert workflow.read_routing_target(row) == workflow.RoutingTarget('self_correction', (), (author,), version)
            assert row.assignee_user_id is None
        assert pools.ClinicVisibilityGrant.query.count() == before
        pools.allow(typist, 'pool', actor_id=admin, pool_id=pool, actions=('view',))
        pools.deny(author, 'own', actor_id=admin, actions=('edit',))
    assert _client(app, typist)[0].get(f'{API}/dictations/{linked}/detail').status_code == 200
    assert client.post(path + '/leases', headers={**csrf, 'Idempotency-Key': str(uuid4())}).status_code == 404


if __name__ == '__main__':
    _exercise()
