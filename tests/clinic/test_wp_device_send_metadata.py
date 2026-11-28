import subprocess

import pytest
from flask import Flask

from src.app import app, db
from src.clinic import agent_channel, import_centre, policy, pools, routes_agent, workflow
from src.clinic.models import ClinicDictation, ClinicOriginal
from src.clinic.web_ingest import recognition_disabled
from src.models import GroupMembership, ProcessingJob, Recording

from test_import_centre import NOW, _env as _env, _intent, _upload, env as env
from test_stub_asr import FIXTURE
from test_wp_v8_agent_routes import PROXY, _finalize


@pytest.fixture
def device(env, clinic_on, monkeypatch):
    author = env['author'].id
    env['admin'].is_admin = True
    admin = env['admin'].id
    recipient = env['other'].id
    monkeypatch.setattr(policy, '_grant_providers', [
        lambda uid: (policy.Grant('own', frozenset({'import'})),) if uid == author else (),
    ])
    author_pool = pools.create_author_pool('Device authors', actor_id=admin)
    pools.add_author_member(author_pool.id, author, actor_id=admin)
    staff_pool = pools.create_staff_pool(f'Device typists {author_pool.id}', actor_id=admin)
    pools.link_staff_pool(author_pool.id, staff_pool.id, actor_id=admin)
    db.session.add(GroupMembership(group_id=staff_pool.group_id, user_id=recipient, role='member'))
    db.session.commit()
    monkeypatch.setattr(import_centre, 'utcnow', lambda: NOW)
    api = Flask(__name__)
    api.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI=app.config['SQLALCHEMY_DATABASE_URI'],
                      SQLALCHEMY_ENGINE_OPTIONS=app.config.get('SQLALCHEMY_ENGINE_OPTIONS', {}))
    db.init_app(api)
    api.register_blueprint(routes_agent.create_agent_blueprint(
        env['policy'], env['service'], PROXY, device_map={'USB-1': author},
    ))
    return {**env, 'client': api.test_client(), 'pool': author_pool.id}


def _send(device, name, data):
    ingest_id, body, key = _upload(device, data, name=name)
    response = _finalize(device, ingest_id, body['chunks'], data=data, key=key)
    assert response.status_code == 200, response.get_data(as_text=True)
    receipt = response.json
    replay = _finalize(device, ingest_id, body['chunks'], data=data, key=key)
    assert replay.status_code == 200 and replay.json == receipt
    return receipt


def _tagged_audio(title: str, priority: int, use_asr: bool) -> bytes:
    return subprocess.run([
        'ffmpeg', '-v', 'error', '-i', str(FIXTURE), '-c:a', 'libmp3lame', '-metadata', f'title={title}',
        '-metadata', f'priority={priority}', '-metadata', f'use_speech_recognition={int(use_asr)}',
        '-f', 'mp3', 'pipe:1',
    ], capture_output=True, check=True, timeout=10).stdout


@pytest.mark.parametrize(('mode', 'queued'), [('keep', 1), ('enable', 2), ('disable', 0)])
def test_device_send_preserves_each_file_and_controls_asr(device, mode, queued):
    recipient = device['other'].id
    names = ['first.mp3', 'second.mp3']
    intent = _intent(device, expected_files=names, overrides={
        'recipient_user_id': {'op': 'set', 'value': recipient}, 'asr': mode,
    }, approved_recipients=[recipient])
    unselected = {
        'source_id': 'third', 'root_id': 'usb-1', 'relative_path': 'third.wav', 'size': 10,
        'mtime_utc': NOW.isoformat(), 'sha256': 'a' * 64, 'snapshot_version': 'untouched',
    }
    agent_channel.report_inventory(device['principal'], device['policy'], {'items': [unselected]}, now=NOW)
    inputs = [('Original A', 0, True), ('Original B', 128, False)]
    for name, (title, priority, use_asr) in zip(names, inputs):
        data = _tagged_audio(title, priority, use_asr)
        receipt = _send(device, name, data)
        assert receipt['state'] == 'STORED'
        assert receipt['import']['intent_id'] == intent['id']
        item = db.session.get(ClinicDictation, receipt['dictation_id'])
        row = db.session.get(workflow.ClinicWorkflowDictation, item.id)
        assert (item.pool_id, row.pool_id, row.assignee_user_id) == (device['pool'], device['pool'], recipient)
        assert db.session.get(Recording, item.recording_id).title == title
        original = db.session.get(ClinicOriginal, receipt['original_id'])
        assert device['service'].file_path(original.storage_key).read_bytes() == data
        assert row.priority == priority
        disabled = mode == 'disable' or (mode == 'keep' and not use_asr)
        assert recognition_disabled(item.recording_id) is disabled
        assert row.status == (workflow.TO_WRITE if disabled else workflow.PREP_OFFLINE)
    assert ProcessingJob.query.filter_by(user_id=device['author'].id, job_type='transcribe').count() == queued
    assert ClinicDictation.query.filter_by(author_user_id=device['author'].id).count() == 2
    assert db.session.get(import_centre.ClinicImportIntent, intent['id']).status == 'FULFILLED'
    assert import_centre.ClinicImportDelivery.query.filter_by(source_id='third').count() == 0
    assert agent_channel.ClinicAgentInventory.query.filter_by(source_id='third').one().sha256 == 'a' * 64


def test_explicit_pool_and_metadata_win_over_file_defaults(device):
    admin, author = device['admin'].id, device['author'].id
    target = pools.create_author_pool('Explicit author pool', actor_id=admin)
    pools.add_author_member(target.id, author, actor_id=admin)
    _intent(device, expected_files=['edit.mp3'], target_pool_id=device['pool'], overrides={
        'title': {'op': 'set', 'value': 'Override'}, 'priority': {'op': 'set', 'value': 0},
        'pool_id': {'op': 'set', 'value': target.id}, 'asr': 'disable',
    })
    receipt = _send(device, 'edit.mp3', _tagged_audio('Original', 128, True))
    assert receipt['state'] == 'STORED'
    item = db.session.get(ClinicDictation, receipt['dictation_id'])
    row = db.session.get(workflow.ClinicWorkflowDictation, item.id)
    assert item.pool_id == row.pool_id == target.id
    assert row.priority == 0
    assert db.session.get(Recording, item.recording_id).title == 'Override'
