"""Web upload receipts and recognition settlement with a synthetic PCM WAV."""
import os
import tempfile
from datetime import datetime
from uuid import UUID

import pytest

from src.clinic import job_lease, recognition_state, revisions, workflow
from src.clinic.correction import ClinicTextAudioLink
from src.clinic.models import ClinicDictation, ClinicOriginal
from src.clinic.web_ingest import dictation_for_recording
from src.database import db
from src.models import ProcessingJob, Recording, User
from src.services.job_queue import MAX_RETRIES, job_queue
from src.services.recording_ingestion import handle_duplicate_replay
from src.services.transcription.connectors.clinic_stub import ClinicStubTranscriptionConnector
from test_stub_asr import _fixture_bytes, _fixture_duration
from test_wp_v8_web_ingest import _post, author, originals  # noqa: F401 - shared fixtures


@pytest.fixture
def upload(clinic_on, author, originals, monkeypatch):
    # The test connector accepts PCM, so keep the WAV in both ingest and ASR.
    monkeypatch.setattr('src.utils.audio_conversion.AUDIO_COMPRESS_UPLOADS', False)
    monkeypatch.setattr('src.services.recording_ingestion.CONVERT_WORKERS', 0)
    stub = ClinicStubTranscriptionConnector({})
    monkeypatch.setattr('src.services.transcription.get_connector', lambda: stub)
    response = _post(author['id'], content=_fixture_bytes(), filename='probe.wav',
                     title='Prüfung', meeting_date='2026-03-02T08:15:00Z')
    assert response.status_code == 202, response.get_data(as_text=True)
    body = response.get_json()
    assert str(UUID(body['dictation_id'])) == body['dictation_id']
    dictation = db.session.get(ClinicDictation, body['dictation_id'])
    job = ProcessingJob.query.filter_by(recording_id=body['id'], job_type='transcribe').one()
    assert dictation.recording_id != job.recording_id
    return dictation, job


def _run(job):
    # Replays target this exact attempt, including completed jobs and delayed retries.
    for name, value in job_lease.claim_values(datetime.utcnow()).items():
        setattr(job, name, value)
    job.status = 'processing'
    db.session.commit()
    job_queue._process_job(job)
    db.session.expire_all()


def test_upload_reaches_asr_and_preserves_human_edits(upload):
    dictation, job = upload
    row = workflow.transition(db.session, dictation.id, 'send_online')
    assert recognition_state.asr_outcome(dictation) == 'running'
    assert recognition_state.settle(db.session, dictation.id).status == workflow.PREP_ONLINE
    _run(job)
    assert job.status == 'completed', job.error_message
    assert row.status == workflow.TO_CORRECT
    assert recognition_state.asr_outcome(dictation) == 'done'
    asr, = revisions.history(dictation)
    assert asr.kind == revisions.KIND_ASR
    links = ClinicTextAudioLink.query.filter_by(transcript_revision_id=asr.id)
    step_ms = int(_fixture_duration() * 1000 / 4)
    assert [(link.audio_start_ms, link.audio_end_ms) for link in links.order_by(ClinicTextAudioLink.seq)] == [
        (i * step_ms, (i + 1) * step_ms) for i in range(4)]
    source = db.session.get(Recording, job.recording_id)
    anchor = db.session.get(Recording, dictation.recording_id)
    assert anchor.status == source.status != 'PENDING'
    assert anchor.title == source.title == 'Prüfung'
    assert anchor.meeting_date == source.meeting_date
    assert anchor.audio_duration_seconds == source.audio_duration_seconds == _fixture_duration()
    anchor.title = 'Eigener Titel'
    _run(job)
    assert [rev.id for rev in revisions.history(dictation)] == [asr.id]
    assert links.count() == 4
    human = revisions.add_human_revision(dictation, asr, 'Korrigierter Satz.', dictation.author_user_id)
    _run(job)
    assert job.status == 'completed'
    assert revisions.current_revision(dictation).id == human.id
    first, edited, late = revisions.history(dictation)
    assert late.kind == revisions.KIND_ASR and late.parent_revision_id == edited.id == human.id
    assert late.body == first.body and anchor.title == 'Eigener Titel'
    from src.tasks.processing import finish_processing
    finish_processing(job.recording_id)
    assert anchor.status == 'COMPLETED'


def test_permanent_budget_failure_releases_prepared_dictation(upload, monkeypatch):
    dictation, job = upload
    row = workflow.transition(db.session, dictation.id, 'send_online')
    monkeypatch.setattr('src.tasks.processing.transcription_tracker.check_budget',
                        lambda uid: (False, 100, 'Monthly transcription budget exceeded'))
    _run(job)
    assert job.status == 'failed'
    assert recognition_state.asr_outcome(dictation) == 'failed'
    assert row.status == workflow.TO_WRITE
    assert not revisions.history(dictation)


def test_upstream_receipt(clinic_off, author):
    response = _post(author['id'])
    assert response.status_code == 202
    assert 'dictation_id' not in response.get_json()


def test_exhausted_retries_settle_once(upload, monkeypatch):
    dictation, job = upload
    row = workflow.transition(db.session, dictation.id, 'send_offline')

    def unavailable(*args):
        raise RuntimeError('temporarily unavailable')

    monkeypatch.setattr(ClinicStubTranscriptionConnector, 'transcribe', unavailable)
    job.retry_count = MAX_RETRIES - 2
    _run(job)
    assert job.status == 'queued' and row.status == workflow.PREP_OFFLINE
    _run(job)
    assert job.status == 'failed' and row.status == workflow.TO_WRITE
    assert recognition_state.settle(db.session, dictation.id).status == workflow.TO_WRITE


def test_idle_draft_only_settles_after_send(upload):
    dictation, job = upload
    db.session.delete(job)
    db.session.flush()
    assert recognition_state.asr_outcome(dictation) == 'none'
    assert recognition_state.settle(db.session, dictation.id).status == workflow.DRAFT
    workflow.transition(db.session, dictation.id, 'send_online')
    assert recognition_state.settle(db.session, dictation.id).status == workflow.TO_WRITE


def test_legacy_prefix_and_anchor_priority(upload):
    dictation, job = upload
    prefix = 987654
    db.session.add_all([Recording(id=rid, user_id=job.user_id) for rid in (prefix, prefix * 10)])
    db.session.add(ClinicOriginal(dictation_id=dictation.id, content_hash='f' * 64, size_bytes=1,
                                 storage_key='originals/legacy-test', receipt='stored',
                                 origin_ref=f'legacy:recording:{prefix}0:time=upload'))
    db.session.flush()
    assert dictation_for_recording(prefix) is None
    assert dictation_for_recording(prefix * 10) is dictation
    assert dictation_for_recording(dictation.recording_id) is dictation
    assert dictation_for_recording(job.recording_id) is dictation
    direct = ClinicDictation(recording_id=prefix * 10, author_user_id=job.user_id)
    db.session.add(direct)
    db.session.flush()
    assert dictation_for_recording(prefix * 10) is direct


def _transcript_jobs(recording_ids):
    return ProcessingJob.query.filter(
        ProcessingJob.recording_id.in_(recording_ids),
        ProcessingJob.job_type.in_(('transcribe', 'convert')),
    )


def test_opposite_asr_choices_send_without_a_transcript_when_opted_out(upload):
    """Old uploads queue ASR. An explicit opt-out stays writable, sends, and keeps its head."""
    on_dictation, on_job = upload
    author_id = on_dictation.author_user_id
    on_sent = workflow.transition(db.session, on_dictation.id, 'send_online')
    assert recognition_state.asr_outcome(on_dictation) == 'running'
    assert recognition_state.settle(db.session, on_dictation.id).status == workflow.PREP_ONLINE
    assert on_sent.acceptance_required is False
    on_ref = ClinicOriginal.query.filter_by(dictation_id=on_dictation.id).one().origin_ref
    assert ':asr=off:' not in on_ref
    assert _transcript_jobs([on_job.recording_id]).count() == 1

    response = _post(
        author_id, content=_fixture_bytes(), filename='manual.wav', title='Manuell',
        client_upload_id='cu-manual', meeting_date='2026-03-03T08:15:00Z',
        use_speech_recognition='false')
    assert response.status_code == 202, response.get_data(as_text=True)
    body = response.get_json()
    assert body['use_speech_recognition'] is False
    off = db.session.get(ClinicDictation, body['dictation_id'])
    upload_id = body['id']
    assert _transcript_jobs([upload_id, off.recording_id]).count() == 0
    original = ClinicOriginal.query.filter_by(dictation_id=off.id).one()
    assert ':asr=off:' in original.origin_ref and original.origin_ref.endswith(':time=recording')

    off_row = db.session.get(workflow.ClinicWorkflowDictation, off.id)
    off_row.acceptance_required = True
    db.session.commit()
    human = revisions.add_human_revision(
        off, None, 'Manuell geschrieben.', off.author_user_id, original_version=original)
    assert db.session.get(workflow.ClinicWorkflowDictation, off.id).status == workflow.DRAFT
    assert revisions.current_revision(off).id == human.id

    workflow.transition(db.session, off.id, 'send_online')
    sent = recognition_state.settle(db.session, off.id)
    assert sent.status == workflow.TO_WRITE
    assert sent.acceptance_required is True
    assert _transcript_jobs([upload_id, off.recording_id]).count() == 0
    assert revisions.current_revision(off).id == human.id

    recording = db.session.get(Recording, upload_id)
    handle, path = tempfile.mkstemp(suffix='.wav')
    os.close(handle)
    replay, status = handle_duplicate_replay(
        recording, owner=db.session.get(User, author_id), filepath=path,
        file_hash='ab' * 32, success_status=202, form={})
    assert status == 202 and replay.get_json()['idempotent_replay'] is True
    assert _transcript_jobs([upload_id, off.recording_id]).count() == 0
    assert revisions.current_revision(off).id == human.id
    assert db.session.get(workflow.ClinicWorkflowDictation, off.id).acceptance_required is True
    assert db.session.get(workflow.ClinicWorkflowDictation, on_dictation.id).acceptance_required is False
