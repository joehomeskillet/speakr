"""Server alias catalog and filename placement on the output routes (PAR-EXP-006).

Synthetic data only. The catalog is a server file of approved aliases. A client
path is refused. Collision and write failures stay distinct, and no response
claims the file was saved on the workstation. BT-EXP-006 and WB-014 stay not_run:
this file does not drive a browser picker.
"""

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from flask import Flask, jsonify
from flask_login import LoginManager, login_user
from flask_wtf.csrf import CSRFProtect, generate_csrf

from src.app import app as real_app
from src.clinic import routes_output as ro
from src.clinic.kis import DisabledAdapter
from src.clinic.models import (
    ClinicAudioRevision, ClinicContextRevision, ClinicDictation, ClinicOriginal,
)
from src.clinic.output import ClinicOutputReceipt, OutputNameContext, expand_filename
from src.clinic.release import create_release
from src.clinic.revisions import add_human_revision
from src.database import db
from src.models import Recording, User

NOW = datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc)


def _catalog(root, *, extra=()):
    rows = [{
        'name': 'workflow',
        'root': str(root),
        'kind': 'workflow',
        'label': 'Workflow-Ablage',
        'version': 2,
    }]
    rows.extend(extra)
    return {'aliases': rows}


@pytest.fixture
def case(world, monkeypatch, tmp_path):
    monkeypatch.setenv('CLINIC_MODE', '1')
    root = tmp_path / 'alias'
    root.mkdir()
    config = tmp_path / 'config'
    config.mkdir()
    (config / 'output-destinations.json').write_text(
        json.dumps(_catalog(root)), encoding='utf-8')
    monkeypatch.setenv('CLINIC_CONFIG_DIR', str(config))
    with real_app.app_context():
        actor = world['user_a']
        dictation = ClinicDictation(recording_id=world['rec_a'], author_user_id=actor)
        db.session.add(dictation)
        db.session.flush()
        audio = ClinicAudioRevision(dictation_id=dictation.id, content_hash='a' * 64)
        original = ClinicOriginal(
            dictation_id=dictation.id, content_hash='b' * 64, size_bytes=1,
            storage_key='originals/synthetic', receipt='stored', origin_ref='test')
        context = ClinicContextRevision(
            dictation_id=dictation.id, source_system='test', tenant_scope='site',
            case_ref='case', patient_ref='patient')
        db.session.add_all([audio, original, context])
        db.session.flush()
        text = add_human_revision(dictation, None, 'Zeile 1\nZeile 2', actor, original_version=original)
        db.session.commit()
        yield dict(
            dictation=dictation, audio=audio, context=context, text=text, actor=actor,
            other=world['user_b'], root=root, config=config, adapter=DisabledAdapter(), jobs={})
        db.session.rollback()


def _release(c):
    row = create_release(
        c['dictation'], c['audio'], c['text'], c['context'], c['text'].normalised_text_hash,
        c['actor'], {'allowed': True}, NOW, document_type='report', release_type='clinical')
    db.session.commit()
    return row


@pytest.fixture
def web(case):
    flask = Flask(__name__)
    flask.config.update(
        TESTING=True, SECRET_KEY='synthetic',
        SQLALCHEMY_DATABASE_URI=real_app.config['SQLALCHEMY_DATABASE_URI'])
    db.init_app(flask)
    login = LoginManager(flask)
    CSRFProtect(flask)
    login.user_loader(lambda uid: db.session.get(User, int(uid)))

    @flask.get('/login/<int:uid>')
    def _login(uid):
        login_user(db.session.get(User, uid))
        return jsonify(csrf=generate_csrf())

    flask.register_blueprint(ro.create_blueprint(
        authorize=lambda user, action, d: action == 'export' and user.id == d.author_user_id,
        adapter_for=lambda: case['adapter'], jobs=case['jobs']))

    def client(uid):
        c = flask.test_client()
        if uid is None:
            return c, {}
        return c, {'X-CSRF-Token': c.get(f'/login/{uid}').json['csrf']}

    with flask.app_context():
        yield client, case


def _key():
    return {'Idempotency-Key': str(uuid.uuid4())}


def _base(c):
    return f"/api/clinic/v1/dictations/{c['dictation'].id}"


def _body(c, **extra):
    body = {'revision_id': c['text'].id, 'format': 'txt'}
    body.update(extra)
    return body


def _place(c, **extra):
    body = {
        'filename_pattern': '%A - %T', 'destination_alias': 'workflow', 'collision_policy': 'fail',
    }
    body.update(extra)
    return _body(c, **body)


def test_destinations_list_the_catalog_without_a_path(web):
    client, c = web
    cl, _csrf = client(c['actor'])
    res = cl.get(_base(c) + '/output-destinations')
    assert res.status_code == 200
    listed = res.json['destinations']
    assert listed == [{
        'name': 'workflow', 'kind': 'workflow', 'label': 'Workflow-Ablage',
        'version': 2, 'local_save_proven': False,
    }]
    assert res.json['local_save_proven'] is False
    assert str(c['root']) not in res.get_data(as_text=True)


def test_pattern_expands_in_order_and_sanitises(web):
    client, c = web
    _release(c)
    recording = db.session.get(Recording, c['dictation'].recording_id)
    author = db.session.get(User, c['actor'])
    recording.title = 'Ärzt/Bericht*'
    author.username = 'Müller:Arzt'
    db.session.commit()
    cl, csrf = client(c['actor'])
    res = cl.post(_base(c) + '/exports', headers=_key() | csrf, json=_place(c, filename_pattern='%T - %A'))
    assert res.status_code == 201
    expected = expand_filename(
        '%T - %A', OutputNameContext(author='Müller:Arzt', title='Ärzt/Bericht*'), extension='txt')
    assert res.json['destination']['filename'] == expected
    assert 'Bericht' in expected and 'Müller' in expected
    assert '/' not in expected and '*' not in expected
    assert (c['root'] / expected).is_file()
    assert res.json['destination']['local_save_proven'] is False
    assert res.json['local_save_proven'] is False
    stored = db.session.query(ClinicOutputReceipt).filter_by(dictation_id=c['dictation'].id).one()
    assert stored.target.endswith(expected)
    assert stored.channel == 'TEXT'


def test_placeholders_follow_pattern_order(web):
    client, c = web
    _release(c)
    recording = db.session.get(Recording, c['dictation'].recording_id)
    author = db.session.get(User, c['actor'])
    recording.title = 'Austritt'
    author.username = 'Meier'
    recording.created_at = datetime(2026, 11, 18, 8, 0)
    db.session.commit()
    cl, csrf = client(c['actor'])
    first = cl.post(
        _base(c) + '/exports', headers=_key() | csrf,
        json=_place(c, filename_pattern='%A_%T_%D_%S_%C'))
    assert first.status_code == 201
    name = first.json['destination']['filename']
    assert name.startswith('Meier_Austritt_2026-11-18_Meier_')
    assert name.endswith('.txt')
    second = cl.post(
        _base(c) + '/exports', headers=_key() | csrf,
        json=_place(c, filename_pattern='%T - %A', collision_policy='version'))
    assert second.status_code == 201
    assert second.json['destination']['filename'] == 'Austritt - Meier.txt'


def test_fail_policy_collides_on_the_second_export(web):
    client, c = web
    _release(c)
    cl, csrf = client(c['actor'])
    first = cl.post(_base(c) + '/exports', headers=_key() | csrf, json=_place(c))
    assert first.status_code == 201
    assert first.json['destination']['outcome'] == 'created'
    assert first.json['destination']['local_save_proven'] is False
    created = c['root'] / first.json['destination']['filename']
    assert created.is_file()
    second = cl.post(_base(c) + '/exports', headers=_key() | csrf, json=_place(c))
    assert second.status_code == 409
    assert second.json['code'] == 'NAME_COLLISION'
    assert second.json['destination']['outcome'] == 'collision'
    assert second.json['destination']['reason'] == 'name_taken'
    assert second.json['local_save_proven'] is False
    assert list(c['root'].iterdir()) == [created]
    assert db.session.query(ClinicOutputReceipt).filter_by(dictation_id=c['dictation'].id).count() == 1


def test_replaying_a_placement_key_cannot_write_a_second_file(web):
    client, c = web
    _release(c)
    cl, csrf = client(c['actor'])
    headers = _key() | csrf
    url = _base(c) + '/exports'
    body = _place(c, collision_policy='version')
    first = cl.post(url, headers=headers, json=body)
    assert first.status_code == 201
    repeated = cl.post(url, headers=headers, json=body)
    assert (repeated.status_code, repeated.json['code']) == (409, 'DESTINATION_UNCONFIRMED')
    changed = cl.post(url, headers=headers, json=body | {'collision_policy': 'unique_name'})
    assert (changed.status_code, changed.json['code']) == (409, 'IDEMPOTENCY_CONFLICT')
    assert [path.name for path in c['root'].iterdir()] == [first.json['destination']['filename']]
    assert cl.get(url + f"/{first.json['id']}/download").status_code == 404
    targets = cl.get(_base(c) + '/output-targets?file_formats=all').json['targets']
    assert all('job_id' not in item for item in targets)


@pytest.mark.parametrize('policy', ['version', 'unique_name'])
def test_rename_policy_places_a_second_file_and_receipt(web, policy):
    client, c = web
    _release(c)
    cl, csrf = client(c['actor'])
    body = _place(c, filename_pattern=None, manual_name='Bericht', collision_policy=policy)
    first = cl.post(_base(c) + '/exports', headers=_key() | csrf, json=body)
    second = cl.post(_base(c) + '/exports', headers=_key() | csrf, json=body)
    assert first.status_code == second.status_code == 201
    destination = second.json['destination']
    assert destination['reason'] == policy
    assert destination['filename'] != 'Bericht.txt'
    assert destination['local_save_proven'] is False
    assert {p.name for p in c['root'].iterdir()} == {'Bericht.txt', destination['filename']}
    receipts = db.session.query(ClinicOutputReceipt).filter_by(dictation_id=c['dictation'].id).all()
    assert {row.target for row in receipts} == {'workflow/Bericht.txt', f"workflow/{destination['filename']}"}


@pytest.mark.parametrize('fields', [
    {'filename_pattern': '%A - %T'},
    {'filename_pattern': '%A - %T', 'destination_alias': 'workflow', 'collision_policy': []},
])
def test_malformed_placement_is_rejected_before_creating_a_job(web, fields):
    client, c = web
    _release(c)
    cl, csrf = client(c['actor'])
    response = cl.post(_base(c) + '/exports', headers=_key() | csrf, json=_body(c, **fields))
    assert response.status_code == 400
    assert c['jobs'] == {}
    assert list(c['root'].iterdir()) == []


def test_client_path_is_rejected(web):
    client, c = web
    _release(c)
    cl, csrf = client(c['actor'])
    res = cl.post(
        _base(c) + '/exports', headers=_key() | csrf,
        json=_place(c, client_path='/tmp/evil.txt'))
    assert res.status_code == 409
    assert res.json['code'] == 'DESTINATION_REJECTED'
    assert res.json['reason'] == 'client_path_rejected'
    assert res.json['local_save_proven'] is False
    assert list(c['root'].iterdir()) == []
    assert db.session.query(ClinicOutputReceipt).count() == 0


def test_unknown_alias_is_rejected(web):
    client, c = web
    _release(c)
    cl, csrf = client(c['actor'])
    res = cl.post(
        _base(c) + '/exports', headers=_key() | csrf,
        json=_place(c, destination_alias='frei'))
    assert res.status_code == 409
    assert res.json['reason'] == 'unknown_alias'
    assert list(c['root'].iterdir()) == []


def test_write_error_is_not_a_collision(web):
    client, c = web
    _release(c)
    os.rmdir(c['root'])
    cl, csrf = client(c['actor'])
    res = cl.post(_base(c) + '/exports', headers=_key() | csrf, json=_place(c))
    assert res.status_code == 422
    assert res.json['code'] == 'DESTINATION_WRITE_FAILED'
    assert res.json['destination']['outcome'] == 'write_error'
    assert res.json['destination']['reason'] == 'not_a_directory'
    assert res.json['code'] != 'NAME_COLLISION'
    assert res.json['local_save_proven'] is False
    assert db.session.query(ClinicOutputReceipt).count() == 0


def test_plain_export_without_alias_stays_a_file_job(web):
    client, c = web
    _release(c)
    cl, csrf = client(c['actor'])
    res = cl.post(_base(c) + '/exports', headers=_key() | csrf, json=_body(c))
    assert res.status_code == 201
    assert res.json['kind'] == 'file'
    assert 'destination' not in res.json
    assert list(c['root'].iterdir()) == []


def test_anonymous_and_other_actor_are_refused(web):
    client, c = web
    assert client(None)[0].get(_base(c) + '/output-destinations').status_code == 401
    other, other_csrf = client(c['other'])
    assert other.get(_base(c) + '/output-destinations').status_code == 404
    _release(c)
    refused = other.post(_base(c) + '/exports', headers=_key() | other_csrf, json=_place(c))
    assert refused.status_code == 404
    assert list(c['root'].iterdir()) == []


def test_bad_catalog_row_is_not_listed(web):
    client, c = web
    raw = json.loads((c['config'] / 'output-destinations.json').read_text(encoding='utf-8'))
    raw['aliases'].append({
        'name': 'offen', 'root': 'relative/not-absolute', 'kind': 'named',
        'label': 'Offen', 'version': 1,
    })
    (c['config'] / 'output-destinations.json').write_text(json.dumps(raw), encoding='utf-8')
    cl, _csrf = client(c['actor'])
    names = [row['name'] for row in cl.get(_base(c) + '/output-destinations').json['destinations']]
    assert names == ['workflow']
