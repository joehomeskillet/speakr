"""Failed confirmed file exports stay visible per target after reload.

TXT and DOCX are separate receipts. A forced DOCX render error does not
erase the TXT success, and a later retry runs only the chosen format.
An unknown KIS delivery is never sent again.
"""

import uuid
from datetime import datetime, timezone

import pytest
from flask import Flask, jsonify
from flask_login import LoginManager, login_user
from flask_wtf.csrf import CSRFProtect, generate_csrf

from src.app import app as real_app
from src.clinic import routes_output as ro
from src.clinic.export import render_text
from src.clinic.kis import MockKisAdapter
from src.clinic.models import ClinicAudioRevision, ClinicContextRevision, ClinicDictation, ClinicOriginal
from src.clinic.release import create_release
from src.clinic.revisions import add_human_revision
from src.database import db
from src.models import User

NOW = datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def case(world, monkeypatch):
    monkeypatch.setenv('CLINIC_MODE', '1')
    monkeypatch.setenv('CLINIC_PROFILE', 'staging')
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
        yield dict(dictation=dictation, audio=audio, context=context, text=text, actor=actor, jobs={})
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
        http = flask.test_client()
        token = http.get(f'/login/{uid}').json['csrf']
        return http, {'X-CSRF-Token': token}

    case['adapter'] = MockKisAdapter()
    with flask.app_context():
        yield client, case


def _key():
    return {'Idempotency-Key': str(uuid.uuid4())}


def _base(c):
    return f"/api/clinic/v1/dictations/{c['dictation'].id}"


def _body(c, fmt):
    return {'revision_id': c['text'].id, 'format': fmt}


def _targets(http, c):
    payload = http.get(_base(c) + '/output-targets').json
    return {item['id']: item for item in payload['targets']}


def test_docx_failure_stays_visible_while_txt_stays_ready(web, monkeypatch):
    client, c = web
    _release(c)
    calls = {'txt': 0, 'docx': 0}

    def txt(row):
        calls['txt'] += 1
        return render_text(row)

    def docx(row):
        calls['docx'] += 1
        raise RuntimeError('synthetic docx failure')

    monkeypatch.setitem(ro.FILE_FORMATS, 'txt', txt)
    monkeypatch.setitem(ro.FILE_FORMATS, 'docx', docx)
    cl, csrf = client(c['actor'])
    base = _base(c)
    txt_res = cl.post(base + '/exports', headers=_key() | csrf, json=_body(c, 'txt'))
    assert txt_res.status_code == 201 and txt_res.json['status'] == 'ready'
    assert len(txt_res.json['sha256']) == 64
    docx_key = _key()
    docx_res = cl.post(base + '/exports', headers=docx_key | csrf, json=_body(c, 'docx'))
    assert docx_res.status_code == 201 and docx_res.json['status'] == 'failed'
    assert docx_res.json['reason'] == 'render_failed' and docx_res.json['format'] == 'docx'
    assert 'sha256' not in docx_res.json and calls == {'txt': 1, 'docx': 1}

    reopened, _ = client(c['actor'])
    targets = _targets(reopened, c)
    assert targets['txt']['status'] == 'ready' and targets['docx']['status'] == 'failed'
    assert 'status' not in targets['kis']
    failed = reopened.get(base + f"/exports/{targets['docx']['job_id']}")
    ready = reopened.get(base + f"/exports/{targets['txt']['job_id']}")
    assert failed.status_code == 200 and failed.json['status'] == 'failed'
    assert failed.json['format'] == 'docx' and 'sha256' not in failed.json
    assert ready.status_code == 200 and ready.json['status'] == 'ready'
    assert ready.json['sha256'] == txt_res.json['sha256']

    replay = cl.post(base + '/exports', headers=docx_key | csrf, json=_body(c, 'docx'))
    assert replay.status_code == 200 and replay.json['replayed'] is True
    assert replay.json['status'] == 'failed' and calls['docx'] == 1

    retry = cl.post(base + '/exports', headers=_key() | csrf, json=_body(c, 'docx'))
    assert retry.status_code == 201 and retry.json['replayed'] is False
    assert retry.json['status'] == 'failed' and calls == {'txt': 1, 'docx': 2}
    assert _targets(reopened, c)['txt']['status'] == 'ready'
    assert _targets(reopened, c)['docx']['status'] == 'failed'

    c['adapter'] = adapter = MockKisAdapter(script=['timeout'])
    unknown = cl.post(base + '/exports', headers=_key() | csrf, json=_body(c, 'kis'))
    assert unknown.json['status'] == 'unknown' and adapter.deliver_calls == 1
    again = cl.post(base + '/exports', headers=_key() | csrf, json=_body(c, 'kis'))
    assert again.json['status'] == 'unknown' and again.json['replayed'] is True
    assert again.json['id'] == unknown.json['id'] and adapter.deliver_calls == 1
    cl.post(base + '/exports', headers=_key() | csrf, json=_body(c, 'docx'))
    assert calls['docx'] == 3 and calls['txt'] == 1 and adapter.deliver_calls == 1
