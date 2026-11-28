"""PAR-ADM-007: resolve and persist the recipient through the real send route."""

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from src.clinic import pools, recipient_defaults as defaults, workflow
from src.clinic.models import ClinicDictation
from src.clinic.parity_receipts import ClinicParityReceipt, scope_key
from src.database import db
from src.models import AuditEvent
from src.services import audit
from test_wp_v8_assignment_route import ROOT, _client, _staff_pool, _user
from test_wp_v8_send_route import _draft


def test_submit_resolves_current_default_and_revalidates_override(tmp_path):
    env = {**os.environ, 'CLINIC_MODE': '1', 'SPEAKR_SKIP_BACKGROUND_TASKS': 'true',
           'TRANSCRIPTION_BASE_URL': 'http://127.0.0.1:9/v1', 'PYTHONPATH': str(ROOT),
           'SQLALCHEMY_DATABASE_URI': f'sqlite:///{tmp_path / "submit.db"}',
           'UPLOAD_FOLDER': str(tmp_path / 'uploads')}
    result = subprocess.run([sys.executable, str(Path(__file__).resolve())], cwd=ROOT,
                            env=env, capture_output=True, text=True, timeout=120, check=False)
    assert result.returncode == 0, result.stdout + result.stderr


def _exercise_submit_defaults():
    from src.app import app
    from src.init_db import initialize_database

    app.config.update(TESTING=True, WTF_CSRF_ENABLED=True)
    with app.app_context():
        initialize_database(app)
        admin = _user('admin', admin=True)
        alpha, lead, typist = map(_user, ('alpha', 'lead', 'typist'))
        author_pool = pools.create_author_pool('Autoren', actor_id=admin).id
        pools.add_author_member(author_pool, alpha, actor_id=admin)
        staff = _staff_pool(admin, 'Schreibdienst', [(lead, 'admin'), (typist, 'member')])
        pools.link_staff_pool(author_pool, staff, actor_id=admin)
        pools.allow(lead, 'pool', pool_id=author_pool, actions=['view'], actor_id=admin)
        person, pool, all_staff, chosen, pending = [_draft(alpha, author_pool) for _ in range(5)]
        db.session.commit()

    browser, token = _client(app, alpha)
    csrf = {'X-CSRF-Token': token['X-CSRFToken']}
    lead_browser, _ = _client(app, lead)
    base = '/api/clinic/v1/dictations/'

    def send(dictation, key, **body):
        return browser.post(base + dictation + '/send',
                            headers={**csrf, 'If-Match': f'"{dictation}:0"', 'Idempotency-Key': key},
                            **body)

    def set_default(mode, version, targets):
        response = browser.put('/api/clinic/v1/preferences/recipient-default', headers=csrf,
                               json={'mode': mode, 'target_ids': targets, 'expected_version': version})
        assert response.status_code == 200, response.json
        assert response.json['version'] == version + 1

    def assert_rejected(key, status, code, **body):
        response = send(pending, key, **body)
        assert (response.status_code, response.json['code']) == (status, code), response.json
        with app.app_context():
            row = db.session.get(ClinicDictation, pending)
            item = db.session.get(workflow.ClinicWorkflowDictation, pending)
            assert (row.version_counter, item.status, item.assignee_user_id) == (0, workflow.DRAFT, None)
            assert workflow.read_routing_target(item) is None
            assert db.session.get(ClinicParityReceipt, scope_key((alpha, 'send', pending, key))) is None
            assert not AuditEvent.query.filter_by(recording_id=row.recording_id, event='recording.edited').count()

    cases = (
        (person, 'person', [str(typist)], [typist]),
        (pool, 'staff_pool', [staff], [lead, typist]),
        (all_staff, 'all_eligible', [], [lead, typist]),
    )
    person_key = str(uuid4())
    for version, (dictation, mode, targets, _) in enumerate(cases):
        set_default(mode, version, targets)
        response = send(dictation, person_key if version == 0 else str(uuid4()), json={})
        assert (response.status_code, response.json['status']) == (200, workflow.TO_WRITE), response.json

    assert_rejected(str(uuid4()), 409, 'STALE_VERSION', json={'expected_version': 2})
    assert_rejected(str(uuid4()), 400, 'ERROR', json={'expected_version': True})
    assert_rejected(str(uuid4()), 400, 'ERROR', json={'override': {}})
    assert_rejected(str(uuid4()), 400, 'ERROR', json={'override': {'mode': 'all_eligible', 'target_ids': {}}})
    assert_rejected(str(uuid4()), 400, 'ERROR', json={'override': {'mode': 'person', 'target_ids': ['9' * 40]}})
    assert_rejected(str(uuid4()), 400, 'ERROR', data='{', content_type='application/json')

    override = {'mode': 'person', 'target_ids': [str(typist)]}
    chosen_body = {'expected_version': 3, 'override': override}
    with patch.object(audit, 'record_in_tx', return_value=None):
        assert_rejected(str(uuid4()), 503, 'AUDIT_UNAVAILABLE', json=chosen_body)
    chosen_key = str(uuid4())
    submitted = send(chosen, chosen_key, json=chosen_body)
    assert submitted.status_code == 200, submitted.json
    assert send(chosen, chosen_key, json={'expected_version': 3}).json['code'] == 'IDEMPOTENCY_CONFLICT'

    with app.app_context():
        db.session.add(defaults.ClinicRecipientChoiceGrant(user_id=alpha, choice=defaults.CHOICE_DEFAULT_ONLY))
        db.session.commit()
    assert_rejected(str(uuid4()), 403, 'CHOICE_DISABLED', json=chosen_body)

    set_default('person', 3, [str(typist)])
    with app.app_context():
        pools.remove_staff_member(staff, typist, actor_id=admin)
    assert_rejected(str(uuid4()), 403, 'RECIPIENT_NOT_ELIGIBLE', json={})
    with app.app_context():
        db.session.delete(db.session.get(defaults.ClinicRecipientChoiceGrant, alpha))
        db.session.commit()
    assert_rejected(str(uuid4()), 403, 'RECIPIENT_NOT_ELIGIBLE', json={'override': override})

    replay = send(chosen, chosen_key, json=chosen_body)
    assert (replay.status_code, replay.json) == (200, submitted.json)
    assert send(person, person_key).status_code == 200
    with app.app_context():
        row = db.session.get(ClinicDictation, chosen)
        assert row.version_counter == 1
        assert AuditEvent.query.filter_by(recording_id=row.recording_id, event='recording.edited').count() == 1
        pools.remove_author_member(author_pool, alpha, actor_id=admin)
    assert_rejected(str(uuid4()), 403, 'FORBIDDEN', json={'override': {'mode': 'all_eligible'}})

    expected = (*cases, (chosen, 'person', [str(typist)], [typist]))
    for version, (dictation, mode, targets, recipients) in zip((1, 2, 3, 3), expected):
        response = lead_browser.get(base + dictation + '/detail')
        assert response.status_code == 200, response.json
        target = response.json['routing_target']
        assert (target['mode'], target['target_ids'], target['recipient_user_ids']) == (mode, targets, recipients)
        assert target['default_version'] == version
        assert response.json['takeover'] is None


if __name__ == '__main__':
    _exercise_submit_defaults()
