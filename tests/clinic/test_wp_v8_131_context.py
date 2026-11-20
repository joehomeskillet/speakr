"""Fall-ID simulation through the real app with an isolated synthetic database."""

import io
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest


@pytest.mark.parametrize('profile', ['staging', 'production', 'off'])
def test_context_route(profile, tmp_path):
    root = Path(__file__).resolve().parents[2]
    env = dict(os.environ, PYTHONPATH=str(root), CLINIC_MODE='0' if profile == 'off' else '1',
               CLINIC_PROFILE=profile, SQLALCHEMY_DATABASE_URI=f'sqlite:///{tmp_path / "context.db"}',
               UPLOAD_FOLDER=str(tmp_path / 'uploads'), RATELIMIT_ENABLED='false',
               ENABLE_AUTO_PROCESSING='false', TRANSCRIPTION_BASE_URL='http://localhost:9/v1')
    result = subprocess.run([sys.executable, __file__, profile], cwd=root, env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'P-SYN-PRIVATE' not in result.stdout + result.stderr


def exercise(profile):
    from src.app import app, db
    from src.clinic import finalize_gates, lease, policy
    from src.clinic.context_grants import ClinicContextGrantUse
    from src.clinic.kis import DisabledAdapter, MockKisAdapter
    from src.clinic.models import ClinicContextRevision, ClinicDictation
    from src.init_db import initialize_database
    from src.models import AuditEvent, Recording, User
    from src.services import audit

    app.config['TESTING'] = True
    with app.app_context():
        initialize_database(app)
        owner = User(username='synthetic-author', email='author@example.invalid', password='x')
        other = User(username='synthetic-reader', email='reader@example.invalid', password='x')
        db.session.add_all([owner, other])
        db.session.flush()
        recording = Recording(user_id=owner.id, title='Synthetic', original_filename='sample.wav')
        db.session.add(recording)
        db.session.flush()
        row = ClinicDictation(recording_id=recording.id, author_user_id=owner.id)
        db.session.add(row)
        db.session.commit()
        did, actor, reader = row.id, owner.id, other.id

    def login(uid):
        client = app.test_client()
        with client.session_transaction() as state:
            state.update(_user_id=str(uid), _fresh=True)
        token = client.get('/api/csrf-token').json['csrf_token']
        return client, {'X-CSRF-Token': token}

    client, csrf = login(actor)
    base = f'/api/clinic/v1/dictations/{did}'
    url = base + '/context'
    case = {'case_reference': 'SYN-0001'}

    def post(body=case):
        return client.post(url, json=body, headers=csrf)

    if profile == 'off':
        assert client.get(url).status_code == 404
        assert post().status_code == 404
        return

    if profile == 'production':
        for response in (post(), client.get(url)):
            assert response.status_code == 503
            assert response.mimetype == 'application/problem+json'
            assert response.json['code'] == 'CAPABILITY_UNAVAILABLE'
            assert response.json['title'] == 'Fall-ID-Simulation nicht verfügbar'
        with app.app_context():
            assert ClinicContextRevision.query.count() == ClinicContextGrantUse.query.count() == 0
        return

    assert client.get(url).json == {'case_display': None}
    assert post().status_code == 423
    outsider, outsider_csrf = login(reader)
    assert outsider.get(url).status_code == 404
    assert outsider.post(url, json=case, headers=outsider_csrf).status_code == 404
    grants = {reader: policy.Grant('all', frozenset({'view'})),
              actor: policy.Grant('own', frozenset({'release'}))}  # Baseline lease route also needs release.
    policy.register_grant_provider(lambda uid: [grants[uid]] if uid in grants else [])
    assert outsider.get(url).status_code == 200
    assert outsider.post(url, json=case, headers=outsider_csrf).status_code == 403
    with app.app_context():
        lease.acquire(did, reader, datetime.now(timezone.utc))
        db.session.commit()
    assert post().status_code == 423
    with app.app_context():
        held = lease.current(did)
        lease.release(held, held.generation, datetime.now(timezone.utc), user_id=reader)
        db.session.commit()

    acquired = client.post(base + '/leases', headers={**csrf, 'Idempotency-Key': str(uuid4())}, json={})
    assert acquired.status_code == 200, acquired.get_data(as_text=True)
    assert client.post(url, json=case).status_code == 400  # The app's global CSRF handler.
    for invalid in ('P-SYN-PRIVATE', 'SYN-１２３４', 'SYN-0001\n', 1234):
        refused = post({'case_reference': invalid})
        assert refused.status_code == 400 and 'P-SYN-PRIVATE' not in refused.get_data(as_text=True)
    assert post({**case, 'patient_reference': 'P-SYN-PRIVATE'}).status_code == 400
    with patch.dict(app.extensions, {'clinic_kis_adapter': DisabledAdapter()}):
        assert post().status_code == client.get(url).status_code == 503
    with patch.dict(app.extensions, {'clinic_kis_adapter': MockKisAdapter(known_contexts=set())}):
        assert post().status_code == 422
    with app.app_context():
        assert ClinicContextRevision.query.count() == ClinicContextGrantUse.query.count() == 0

    logs = io.StringIO()
    handler = logging.StreamHandler(logs)
    logging.getLogger().addHandler(handler)
    try:
        with patch('src.clinic.routes_context.token_urlsafe', return_value='synthetic-grant-nonce'):
            created = post()
            assert created.status_code == 201 and created.json == {'case_display': 'SYN-0001'}
            replay = post({'case_reference': 'SYN-0002'})
            assert replay.status_code == 409 and replay.json['code'] == 'GRANT_REPLAYED'
        assert post().status_code == 409
        shown = client.get(url)
    finally:
        logging.getLogger().removeHandler(handler)
    assert shown.status_code == 200 and shown.json == created.json
    assert shown.headers['Cache-Control'] == created.headers['Cache-Control'] == 'no-store'
    assert client.get(base + '/detail').json['case_display'] == 'SYN-0001'

    audit.flush()
    with app.app_context():
        current = ClinicContextRevision.query.one()
        proof = ClinicContextGrantUse.query.one()
        assert current.dictation_id == did and current.case_ref == 'SYN-0001'
        assert proof.revision_id == current.id and proof.actor_user_id == actor
        assert proof.purpose == 'bind_existing' and finalize_gates.trusted_context(
            current, app.extensions['clinic_kis_adapter']) is True
        assert current.patient_ref and current.patient_ref != current.case_ref
        for response in (created, shown, replay):
            assert current.patient_ref not in response.get_data(as_text=True)
            assert 'synthetic-grant-nonce' not in response.get_data(as_text=True)
        assert current.patient_ref not in logs.getvalue() and 'synthetic-grant-nonce' not in logs.getvalue()
        assert all(current.patient_ref not in str(event.details) for event in AuditEvent.query.all())


if __name__ == '__main__':
    exercise(sys.argv[1])
