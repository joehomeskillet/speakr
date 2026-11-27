"""File export download (P-042): txt, docx, rtf and odt of one released dictation.

Synthetic data only. The download returns the renderer bytes of the owning actor's
ready job, with the stored sha256. Another user, a missing job and a failed job
are 404. Replaying the same Idempotency-Key does not render a second job.
"""

import hashlib
import uuid
import zipfile
from datetime import datetime, timezone
from io import BytesIO

import pytest
from flask import Flask, g, jsonify
from flask_login import LoginManager, login_user
from flask_wtf.csrf import CSRFProtect, generate_csrf

from src.app import app as real_app
from src.clinic import routes_output as ro
from src.clinic.export import render_docx, render_odt, render_rtf, render_text
from src.clinic.kis import DisabledAdapter
from src.clinic.models import (
    ClinicAudioRevision, ClinicContextRevision, ClinicDictation, ClinicOriginal,
)
from src.clinic.release import create_release
from src.clinic.revisions import add_human_revision
from src.database import db
from src.models import AuditEvent, User
from src.services import audit

NOW = datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc)
RENDERERS = {'txt': render_text, 'docx': render_docx, 'rtf': render_rtf, 'odt': render_odt}


@pytest.fixture
def case(world, monkeypatch):
    monkeypatch.setenv('CLINIC_MODE', '1')
    with real_app.app_context():
        actor = world['user_a']
        dictation = ClinicDictation(recording_id=world['rec_a'], author_user_id=actor)
        db.session.add(dictation)
        db.session.flush()
        audio = ClinicAudioRevision(dictation_id=dictation.id, content_hash='a' * 64)
        original = ClinicOriginal(
            dictation_id=dictation.id, content_hash='b' * 64, size_bytes=1,
            storage_key='originals/synthetic', receipt='stored', origin_ref='test',
        )
        context = ClinicContextRevision(
            dictation_id=dictation.id, source_system='test', tenant_scope='site',
            case_ref='case', patient_ref='patient',
        )
        db.session.add_all([audio, original, context])
        db.session.flush()
        text = add_human_revision(
            dictation, None, 'Zeile 1\nZeile 2', actor, original_version=original,
        )
        db.session.commit()
        yield dict(
            dictation=dictation, audio=audio, context=context, text=text,
            original=original, actor=actor, other=world['user_b'],
            recording_id=world['rec_a'], adapter=DisabledAdapter(), jobs={},
        )
        db.session.rollback()


def _release(c):
    row = create_release(
        c['dictation'], c['audio'], c['text'], c['context'], c['text'].normalised_text_hash,
        c['actor'], {'allowed': True}, NOW, document_type='report', release_type='clinical',
    )
    db.session.commit()
    return row


@pytest.fixture
def web(case):
    flask = Flask(__name__)
    flask.config.update(
        TESTING=True, SECRET_KEY='synthetic',
        SQLALCHEMY_DATABASE_URI=real_app.config['SQLALCHEMY_DATABASE_URI'],
    )
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
        adapter_for=lambda: case['adapter'], jobs=case['jobs'],
    ))

    def client(uid):
        # The fixture holds one app context. Flask-Login caches the resolved
        # user on g for that whole context, so each client must drop it first.
        g.pop('_login_user', None)
        g.pop('csrf_token', None)
        http = flask.test_client()
        if uid is None:
            return http, {}
        return http, {'X-CSRF-Token': http.get(f'/login/{uid}').json['csrf']}

    with flask.app_context():
        yield client, case


def _base(c):
    return f"/api/clinic/v1/dictations/{c['dictation'].id}"


def _post(http, c, csrf, fmt, key=None):
    headers = {'Idempotency-Key': key or str(uuid.uuid4())} | csrf
    return http.post(
        _base(c) + '/exports', headers=headers,
        json={'revision_id': c['text'].id, 'format': fmt},
    )


def test_clinic_mode_accepts_rtf_and_odt_without_listing_them(case):
    assert list(ro.file_formats()) == ['txt', 'docx', 'rtf', 'odt']
    assert list(ro.FILE_FORMATS) == ['txt', 'docx']


@pytest.mark.parametrize('fmt', ['txt', 'docx', 'rtf', 'odt'])
def test_download_matches_the_release_render(web, fmt):
    client, c = web
    release = _release(c)
    http, csrf = client(c['actor'])
    created = _post(http, c, csrf, fmt)
    assert created.status_code == 201, created.json
    job = created.json
    assert job['status'] == 'ready' and job['format'] == fmt and len(job['sha256']) == 64
    expected = RENDERERS[fmt](release)

    audit.flush()
    baseline = db.session.query(db.func.max(AuditEvent.id)).scalar() or 0
    downloaded = http.get(_base(c) + f"/exports/{job['id']}/download")
    audit.flush()

    assert downloaded.status_code == 200
    assert downloaded.data == expected.content
    assert downloaded.headers['X-Content-SHA256'] == job['sha256'] == expected.sha256
    assert downloaded.headers['X-Content-SHA256'] == hashlib.sha256(downloaded.data).hexdigest()
    assert downloaded.headers['Cache-Control'] == 'no-store'
    assert downloaded.headers['X-Content-Type-Options'] == 'nosniff'
    disposition = downloaded.headers['Content-Disposition']
    assert disposition.startswith('attachment; filename="') and disposition.endswith('"')
    assert expected.filename in disposition and '/' not in disposition
    assert downloaded.mimetype == expected.mimetype.split(';')[0]
    rows = AuditEvent.query.filter_by(
        event='recording.content.read', actor_user_id=c['actor'], recording_id=c['recording_id'],
    ).filter(AuditEvent.id > baseline).all()
    assert [row.details for row in rows] == [{'part': 'export'}]


def test_docx_download_is_a_zip_and_odt_names_mimetype_first(web):
    client, c = web
    _release(c)
    http, csrf = client(c['actor'])
    docx = _post(http, c, csrf, 'docx').json
    odt = _post(http, c, csrf, 'odt').json
    docx_body = http.get(_base(c) + f"/exports/{docx['id']}/download").data
    odt_body = http.get(_base(c) + f"/exports/{odt['id']}/download").data
    assert docx_body[:2] == b'PK'
    with zipfile.ZipFile(BytesIO(odt_body)) as archive:
        assert archive.namelist()[0] == 'mimetype'
        assert archive.read('mimetype') == b'application/vnd.oasis.opendocument.text'


def test_replay_returns_the_same_job_and_the_same_bytes(web):
    client, c = web
    _release(c)
    http, csrf = client(c['actor'])
    key = str(uuid.uuid4())
    first = _post(http, c, csrf, 'rtf', key)
    again = _post(http, c, csrf, 'rtf', key)
    assert first.status_code == 201 and again.status_code == 200
    assert again.json['id'] == first.json['id'] and again.json['replayed'] is True
    assert again.json['sha256'] == first.json['sha256']
    one = http.get(_base(c) + f"/exports/{first.json['id']}/download")
    two = http.get(_base(c) + f"/exports/{first.json['id']}/download")
    assert one.status_code == two.status_code == 200
    assert one.data == two.data
    assert one.headers['X-Content-SHA256'] == two.headers['X-Content-SHA256'] == first.json['sha256']


def test_download_is_hidden_from_other_users_and_missing_jobs(web):
    client, c = web
    _release(c)
    http, csrf = client(c['actor'])
    job = _post(http, c, csrf, 'txt').json
    other, _csrf = client(c['other'])
    assert other.get(_base(c) + f"/exports/{job['id']}/download").status_code == 404
    g.pop('_login_user', None)
    missing = http.get(_base(c) + f"/exports/{uuid.uuid4()}/download")
    assert missing.status_code == 404 and missing.mimetype == 'application/problem+json'
    assert client(None)[0].get(_base(c) + f"/exports/{job['id']}/download").status_code == 401


def test_failed_export_has_no_download(web, monkeypatch):
    client, c = web
    _release(c)

    def refuse(_row):
        raise RuntimeError('synthetic render failure')

    monkeypatch.setitem(ro.FILE_FORMATS, 'docx', refuse)
    http, csrf = client(c['actor'])
    failed = _post(http, c, csrf, 'docx')
    assert failed.status_code == 201 and failed.json['status'] == 'failed'
    assert http.get(_base(c) + f"/exports/{failed.json['id']}/download").status_code == 404
