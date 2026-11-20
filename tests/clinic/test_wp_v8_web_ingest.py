"""Web upload and recorder bridge to a clinic dictation (V8 P04/P09).

Synthetic rows and a synthetic dictation header only. Runs through the real /upload route.
SHARED-DB: assertions are scoped to the rows the test created.
"""
from __future__ import annotations

import io
import uuid
from unittest.mock import patch

import pytest

from src.app import app, db
from src.clinic import worklist_entries, workflow
from src.clinic.models import ClinicDictation, ClinicOriginal
from src.clinic.pools import ClinicAuthorPool, ClinicAuthorPoolMember
from src.models import ProcessingJob, Recording, User

DS2 = b'\x03ds2' + b'\x00' * 2048
OTHER = b'\x03ds2' + b'\x01' * 2048


@pytest.fixture
def originals(tmp_path, monkeypatch):
    monkeypatch.setenv('CLINIC_ORIGINALS_ROOT', str(tmp_path))
    return tmp_path


@pytest.fixture
def author():
    """Author A in one author pool, plus a user in no pool."""
    with app.app_context():
        tag = uuid.uuid4().hex[:8]
        a = User(username=f'wi_a_{tag}', email=f'wi_a_{tag}@local.test', password='x')
        nopool = User(username=f'wi_n_{tag}', email=f'wi_n_{tag}@local.test', password='x')
        db.session.add_all([a, nopool])
        db.session.flush()
        pool = ClinicAuthorPool(name=f'wi pool {tag}', created_by=a.id)
        db.session.add(pool)
        db.session.flush()
        db.session.add(ClinicAuthorPoolMember(pool_id=pool.id, user_id=a.id, created_by=a.id))
        db.session.commit()
        yield {'id': a.id, 'pool': pool.id, 'nopool': nopool.id}
        db.session.rollback()
        for uid in (a.id, nopool.id):
            ProcessingJob.query.filter_by(user_id=uid).delete(synchronize_session=False)


def _post(uid, content=DS2, filename='diktat.ds2', **fields):
    from flask import g, has_app_context
    if has_app_context():
        g.pop('_login_user', None)
    client = app.test_client()
    with client.session_transaction() as s:
        s['_user_id'] = str(uid)
        s['_fresh'] = True
    with patch('src.services.recording_ingestion.get_duration', return_value=None), \
         patch('src.services.recording_ingestion.get_creation_date', return_value=None), \
         patch('src.services.webhook_dispatch.emit_webhook_event'):
        return client.post('/upload', data={'file': (io.BytesIO(content), filename), **fields},
                           content_type='multipart/form-data')


def _dictations(uid):
    with app.app_context():
        return ClinicDictation.query.filter_by(author_user_id=uid).all()


def test_upload_creates_one_dictation_for_author_and_pool(clinic_on, originals, author):
    response = _post(author['id'], client_upload_id='cu-' + uuid.uuid4().hex[:8])
    assert response.status_code == 202, response.get_data(as_text=True)
    rows = _dictations(author['id'])
    assert len(rows) == 1
    assert rows[0].author_user_id == author['id'] and rows[0].pool_id == author['pool']
    with app.app_context():
        entries = worklist_entries.load_entries(author['id'])
        assert [e.row['dictation_id'] for e in entries] == [rows[0].id]
        assert entries[0].row['status'] == workflow.DRAFT and entries[0].pool_id == author['pool']
        original = ClinicOriginal.query.filter_by(dictation_id=rows[0].id).one()
        assert original.origin_ref.startswith('upload:recording:%d:' % response.get_json()['id'])


def test_same_import_key_creates_no_second_dictation(clinic_on, originals, author):
    cid = 'cu-' + uuid.uuid4().hex[:8]
    assert _post(author['id'], client_upload_id=cid).status_code == 202
    assert _post(author['id'], client_upload_id=cid).status_code == 202
    rows = _dictations(author['id'])
    assert len(rows) == 1
    with app.app_context():
        assert ClinicOriginal.query.filter_by(dictation_id=rows[0].id).count() == 1
        assert len(worklist_entries.load_entries(author['id'])) == 1


def test_same_bytes_without_client_id_reuse_the_dictation(clinic_on, originals, author):
    assert _post(author['id']).status_code == 202
    assert _post(author['id']).status_code == 202
    assert len(_dictations(author['id'])) == 1


def test_same_key_with_other_bytes_is_a_visible_conflict_without_stray_recording(
        clinic_on, originals, author):
    cid = 'cu-' + uuid.uuid4().hex[:8]
    assert _post(author['id'], client_upload_id=cid).status_code == 202
    response = _post(author['id'], content=OTHER, client_upload_id=cid)
    assert response.status_code == 409
    assert response.get_json()['code'] == 'clinic_ingest_conflict'
    assert 'Diktat' in response.get_json()['error']
    assert len(_dictations(author['id'])) == 1
    with app.app_context():
        assert Recording.query.filter_by(user_id=author['id'], is_archived=False).count() == 1


def test_user_without_author_pool_is_refused_and_leaves_nothing(clinic_on, originals, author):
    response = _post(author['nopool'])
    assert response.status_code == 403
    assert response.get_json()['code'] == 'clinic_no_author_pool'
    assert _dictations(author['nopool']) == []
    with app.app_context():
        assert Recording.query.filter_by(user_id=author['nopool']).count() == 0


def test_database_failure_is_visible_and_rolls_back(clinic_on, originals, author):
    with patch('src.clinic.web_ingest.IngestService.store_original', side_effect=RuntimeError('db down')):
        response = _post(author['id'])
    assert response.status_code == 500
    assert response.get_json()['code'] == 'clinic_ingest_failed'
    assert _dictations(author['id']) == []
    with app.app_context():
        assert Recording.query.filter_by(user_id=author['id']).count() == 0


def test_time_source_recording_time_leads_and_fallback_is_marked(clinic_on, originals, author):
    _post(author['id'], client_upload_id='cu-t1', meeting_date='2026-03-02T08:15:00Z')
    _post(author['id'], content=OTHER, client_upload_id='cu-t2')
    with app.app_context():
        by_ref = {o.origin_ref.rsplit('time=', 1)[1]: o for o in ClinicOriginal.query.all()
                  if o.dictation_id in {d.id for d in _dictations(author['id'])}}
        assert set(by_ref) == {'recording', 'upload'}
        assert by_ref['recording'].created_at.replace(tzinfo=None).isoformat() == '2026-03-02T08:15:00'
        entry = {e.row['dictation_id']: e for e in worklist_entries.load_entries(author['id'])}
        assert entry[by_ref['recording'].dictation_id].row['created_at'].startswith('2026-03-02T08:15:00')


def test_file_last_modified_alone_is_marked_as_file_time(clinic_on, originals, author):
    # 2026-03-02T08:15:00Z in milliseconds
    _post(author['id'], client_upload_id='cu-f1', file_last_modified='1772439300000')
    with app.app_context():
        refs = [o.origin_ref for o in ClinicOriginal.query.all()
                if o.dictation_id in {d.id for d in _dictations(author['id'])}]
        assert len(refs) == 1 and refs[0].endswith(':time=file')


def test_cancel_during_request_leaves_no_dictation(clinic_on, originals, author):
    with patch('src.services.upload_cancel.register_recording', return_value=True):
        response = _post(author['id'], client_upload_id='cu-cancel-1')
    assert response.status_code == 409
    assert _dictations(author['id']) == []


def test_clinic_mode_off_creates_no_dictation(clinic_off, author):
    response = _post(author['id'])
    assert response.status_code == 202, response.get_data(as_text=True)
    assert _dictations(author['id']) == []
