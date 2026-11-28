"""Recording templates from the browser upload to the transcription connector."""

import io
import json
import wave

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.clinic import workflow
from src.clinic.models import ClinicDictation
from src.clinic.registry import create_clinic_tables
from src.database import db
from src.models import ProcessingJob, Recording
from src.services.job_queue import job_queue
from src.services.transcription.base import TranscriptionCapability
from src.services.transcription.connectors.clinic_stub import ClinicStubTranscriptionConnector
from test_wp_v8_asr_link_and_receipt import _run
from test_wp_v8_detail_api import URL, api  # noqa: F401 — shared fixture
from test_wp_v8_web_ingest import _post, author, originals  # noqa: F401 — shared fixtures
from test_wp_workflow_api import _post as workflow_post, world  # noqa: F401 — shared fixture


class CapturingConnector(ClinicStubTranscriptionConnector):
    CAPABILITIES = ClinicStubTranscriptionConnector.CAPABILITIES | {TranscriptionCapability.DIARIZATION}

    def __init__(self):
        super().__init__({})
        self.requests = []

    def transcribe(self, request):
        self.requests.append(request)
        return super().transcribe(request)


@pytest.fixture
def upload_take(clinic_on, author, originals, monkeypatch):
    monkeypatch.setattr('src.utils.audio_conversion.AUDIO_COMPRESS_UPLOADS', False)
    monkeypatch.setattr('src.services.recording_ingestion.CONVERT_WORKERS', 0)
    connector = CapturingConnector()
    monkeypatch.setattr('src.services.transcription.get_connector', lambda: connector)
    pcm = io.BytesIO()
    with wave.open(pcm, 'wb') as wav:
        wav.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        wav.writeframes(b'\x00\x00' * 16000 * 5)

    def upload(**fields):
        return _post(author['id'], content=pcm.getvalue(), filename='take.wav', from_recorder='true', **fields)

    return upload, connector


@pytest.mark.parametrize(('requested', 'expected', 'minimum', 'maximum', 'diarize'), [
    (None, 'bericht', 1, 1, False),
    ('bericht', 'bericht', 1, 1, False),
    ('notiz', 'notiz', 1, 1, False),
    ('sitzung', 'sitzung', 2, 8, True),
    ('patientengespraech', 'patientengespraech', 2, 3, True),
])
def test_type_persists_and_reaches_asr(upload_take, api, author, requested, expected, minimum, maximum, diarize):
    upload, connector = upload_take
    connector.default_diarize = not diarize
    fields = {} if requested is None else {'recording_type': requested}
    if diarize:
        fields['use_speech_recognition'] = 'false'
    response = upload(**fields, min_speakers='5', max_speakers='6')
    assert response.status_code == 202, response.get_data(as_text=True)
    body = response.json
    assert body['recording_type'] == expected
    db.session.expire_all()
    dictation = db.session.get(ClinicDictation, body['dictation_id'])
    assert dictation.recording_type == expected
    login_as, allowed, _seen = api
    allowed.add(author['id'])
    detail = login_as(author['id']).get(URL.format(dictation.id))
    assert detail.status_code == 200
    assert detail.json['recording_type'] == expected
    assert ('send' in detail.json['allowed_actions']) is (expected == 'bericht')

    job = ProcessingJob.query.filter_by(recording_id=body['id'], job_type='transcribe').one()
    params = json.loads(job.params)
    assert (params['min_speakers'], params['max_speakers'], params['diarize']) == (minimum, maximum, diarize)
    _run(job)
    assert job.status == 'completed', job.error_message
    [request] = connector.requests
    assert (request.min_speakers, request.max_speakers, request.diarize) == (minimum, maximum, diarize)


@pytest.mark.parametrize('invalid', ['', 'BERICHT', 'other', ['bericht', 'other']])
def test_upload_rejects_invalid_type_without_storing_audio(upload_take, author, invalid):
    upload, _connector = upload_take
    response = upload(recording_type=invalid)
    assert response.status_code == 400
    assert response.json['code'] == 'invalid_recording_type'
    assert Recording.query.filter_by(user_id=author['id']).count() == 0
    assert ProcessingJob.query.filter_by(user_id=author['id']).count() == 0


def test_replaying_upload_with_another_type_cannot_relabel_dictation(upload_take, author):
    upload, _connector = upload_take
    first = upload(recording_type='notiz', client_upload_id='typed-retry')
    assert first.status_code == 202
    assert upload(recording_type='bericht', client_upload_id='typed-retry').status_code == 409
    [dictation] = ClinicDictation.query.filter_by(author_user_id=author['id']).all()
    assert dictation.id == first.json['dictation_id']
    assert dictation.recording_type == 'notiz'
    assert Recording.query.filter_by(user_id=author['id'], is_archived=False).count() == 1


def test_reprocessing_uses_stored_type(upload_take, author):
    upload, _connector = upload_take
    response = upload(recording_type='sitzung')
    assert response.status_code == 202
    job_id = job_queue.enqueue(author['id'], response.json['id'], 'reprocess_transcription',
                               params={'min_speakers': 1, 'max_speakers': 2})
    params = json.loads(db.session.get(ProcessingJob, job_id).params)
    assert (params['min_speakers'], params['max_speakers'], params['diarize']) == (2, 8, True)


@pytest.mark.parametrize('kind', ['sitzung', 'patientengespraech', 'notiz'])
def test_non_report_cannot_be_sent_to_writing_service(world, kind):
    world['dictation'].configured_recording_type = kind
    world['item'].status = workflow.DRAFT
    db.session.commit()
    response = workflow_post(world, 'send', {}, version=world['dictation'].version_counter)
    assert response.status_code == 409
    assert response.json['code'] == 'RECORDING_TYPE_CANNOT_SEND'
    assert world['item'].status == workflow.DRAFT


def test_schema_upgrade_keeps_legacy_dictation_as_report():
    engine = create_engine('sqlite://')
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                'CREATE TABLE clinic_dictation (id VARCHAR(36) PRIMARY KEY, recording_id INTEGER, '
                'author_user_id INTEGER, version_counter INTEGER, pool_id VARCHAR(36), created_at DATETIME)'
            )
            connection.exec_driver_sql(
                'INSERT INTO clinic_dictation (id, recording_id) VALUES (?, ?)', ('legacy', 42)
            )
        create_clinic_tables(engine)
        create_clinic_tables(engine)
        with Session(engine) as session:
            legacy = session.get(ClinicDictation, 'legacy')
            assert legacy.recording_id == 42
            assert legacy.recording_type == 'bericht'
    finally:
        engine.dispose()


def test_non_clinic_upload_retains_upstream_contract(clinic_off, author):
    response = _post(author['id'], recording_type='ignored', min_speakers='5', max_speakers='6')
    assert response.status_code == 202
    assert 'recording_type' not in response.json
    job = ProcessingJob.query.filter_by(recording_id=response.json['id'], job_type='transcribe').one()
    params = json.loads(job.params)
    assert (params['min_speakers'], params['max_speakers']) == (5, 6)
    assert 'diarize' not in params
