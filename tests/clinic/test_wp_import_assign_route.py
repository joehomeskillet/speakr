"""W13 clarification through the real session, import, ASR queue and worklist routes."""

import os
import subprocess
import sys

from sqlalchemy.exc import OperationalError

from src.app import app, db
from src.clinic import import_centre, import_source_config, pools, workflow
from src.clinic.identity import ClinicAccountPolicy
from src.clinic.models import ClinicDictation, ClinicOriginal
from src.models import AuditEvent, GroupMembership, ProcessingJob

from test_import_centre import _env as _env, _finalize, _upload, _user, env as env
from test_stub_asr import FIXTURE
from test_wp_v8_import_routes import _client


PREFIX = '/api/clinic/v1/imports/'


def test_import_assignment(env, monkeypatch, request):
    if 'clinic_imports_web.unassigned' not in app.view_functions:
        client = app.test_client()
        assert client.get(PREFIX + 'unassigned/missing/candidates').status_code == 404
        assert client.post(PREFIX + 'unassigned/missing/assign', json={}).status_code == 404
        result = subprocess.run(
            [sys.executable, '-m', 'pytest', request.node.nodeid, '-q', '-p', 'no:cacheprovider', '--timeout=120'],
            env={**os.environ, 'CLINIC_MODE': '1', 'PYTEST_DATABASE_URI': ''},
            capture_output=True, text=True, timeout=110, check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return

    admin, author, lead = (env[key] for key in ('admin', 'author', 'other'))
    typist, inactive = _user('typ'), _user('ina')
    admin.is_admin = True
    db.session.add_all(ClinicAccountPolicy(user_id=u.id, status='active') for u in (admin, author, lead, typist))
    db.session.add(ClinicAccountPolicy(user_id=inactive.id, status='disabled'))
    db.session.commit()
    source = pools.create_author_pool('Source authors', actor_id=admin.id)
    target = pools.create_author_pool('Target authors', actor_id=admin.id)
    closed = pools.create_author_pool('Closed authors', actor_id=admin.id)
    pools.add_author_member(target.id, author.id, actor_id=admin.id)
    pools.add_author_member(target.id, inactive.id, actor_id=admin.id)
    pools.add_author_member(closed.id, author.id, actor_id=admin.id)
    pools.set_pool_active('author', closed.id, False, actor_id=admin.id)
    staff = pools.create_staff_pool('Source typists', actor_id=admin.id)
    pools.link_staff_pool(source.id, staff.id, actor_id=admin.id)
    db.session.add_all(GroupMembership(group_id=staff.group_id, user_id=u.id, role=role)
                       for u, role in ((lead, 'admin'), (typist, 'member')))
    db.session.commit()
    monkeypatch.setenv('CLINIC_IMPORT_ROOT_ALLOWLIST', '/synthetic-imports')
    import_source_config.create_source_config(
        actor_user_id=admin.id, source_kind='device', source_ref='usb-1', approved_root='/synthetic-imports',
        target_author_user_id=admin.id, target_pool_id=source.id,
    )

    data = FIXTURE.read_bytes()
    receipts = [_finalize(env, *_upload(env, data, root=root, name='dictation.wav')[:2])
                for root in ('usb-1', 'usb-2')]
    first, second = [receipt['import']['delivery_id'] for receipt in receipts]
    assert all(receipt['state'] == 'UNKNOWN' for receipt in receipts)

    def call(client, path, body=None, headers=None):
        with app.app_context():
            return client.get(path) if body is None else client.post(path, json=body, headers=headers)

    def candidates(client, delivery=first):
        return call(client, PREFIX + f'unassigned/{delivery}/candidates')

    def assign(client, delivery=first, version=receipts[0]['snapshot_version'], **changes):
        headers = {'X-CSRFToken': call(client, '/api/csrf-token').json['csrf_token']}
        body = dict(target_author_user_id=author.id, target_pool_id=target.id, expected_snapshot_version=version)
        return call(client, PREFIX + f'unassigned/{delivery}/assign', body | changes, headers)

    admin_client, lead_client, typist_client = (_client(u) for u in (admin, lead, typist))
    assert candidates(app.test_client()).status_code == 401
    assert candidates(typist_client).status_code == assign(typist_client).status_code == 403
    assert candidates(lead_client, second).status_code == assign(lead_client, second).status_code == 404
    assert call(lead_client, PREFIX + f'unassigned/{first}/assign', {}).status_code == 400
    assert call(lead_client, PREFIX + 'inventory').json['can_clarify'] is True
    response = candidates(lead_client)
    assert response.status_code == 200
    assert response.cache_control.no_store
    assert response.json['items'] == [dict(target_author_user_id=author.id, target_pool_id=target.id,
                                          author_name=author.username, pool_name=target.name)]
    assert candidates(admin_client, second).json['items'] == response.json['items']
    assert assign(lead_client, version='stale').status_code == 409
    assert assign(lead_client, target_author_user_id=inactive.id).status_code == 403
    assert assign(lead_client, target_pool_id=source.id).status_code == 403
    assert assign(lead_client, target_author_user_id=True).status_code == 400

    def fail_commit():
        raise OperationalError('COMMIT', {}, RuntimeError('synthetic commit failure'))

    with monkeypatch.context() as patch:
        patch.setattr(db.session, 'commit', fail_commit)
        assert assign(lead_client).status_code == 500
    jobs = ProcessingJob.query.filter_by(user_id=author.id, job_type='transcribe')
    events = AuditEvent.query.filter_by(event='recording.edited')
    assert jobs.count() == events.count() == ClinicDictation.query.count() == 0
    assert import_centre.ClinicImportIntent.query.count() == workflow.ClinicWorkflowDictation.query.count() == 0
    assert db.session.get(import_centre.ClinicImportDelivery, first).status == 'UNASSIGNED'

    for client, actor, delivery, receipt in zip((lead_client, admin_client), (lead, admin), (first, second), receipts):
        original = db.session.get(import_centre.ClinicImportOriginal, delivery)
        response = assign(client, delivery, receipt['snapshot_version'])
        assert response.status_code == 200, response.get_data(as_text=True)
        assert response.json['delivery_id'] == delivery
        dictation_id = response.json['dictation_id']
        dictation = db.session.get(ClinicDictation, dictation_id)
        assert (dictation.author_user_id, dictation.pool_id) == (author.id, target.id)
        assert ClinicOriginal.query.filter_by(dictation_id=dictation_id).one().storage_key == original.storage_key
        assert env['service'].file_path(original.storage_key).read_bytes() == data
        event = events.filter_by(actor_user_id=actor.id).one()
        assert event.target_user_id == author.id and event.details['fields']['import_delivery_id'] == delivery
        assert assign(client, delivery, receipt['snapshot_version']).status_code == 409

    assert jobs.filter_by(status='queued').count() == events.count() == 2
    assert not import_centre.ClinicImportIntent.query.filter_by(status='OPEN').count()
    target_staff = pools.create_staff_pool('Target typists', actor_id=admin.id)
    pools.link_staff_pool(target.id, target_staff.id, actor_id=admin.id)
    db.session.add(GroupMembership(group_id=target_staff.group_id, user_id=typist.id, role='admin'))
    db.session.commit()
    for client in (_client(author), typist_client):
        response = call(client, '/api/clinic/v1/worklist/rows')
        assert response.status_code == 200
        assert {row['dictation_id'] for row in response.json['items']} == {d.id for d in ClinicDictation.query}
