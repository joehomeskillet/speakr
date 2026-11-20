"""Explicit repeat ingress and real queue runner; synthetic local ASR only."""

import json

import pytest

from src.clinic import alignment, revisions
from src.clinic.asr_repeat import REPEAT_PARAM
from src.clinic.audio_revisions import apply_cut, init_audio_revision
from src.clinic.correction import ClinicTextAudioLink
from src.clinic.models import ClinicOriginal
from src.database import db
from src.models import ProcessingJob, Recording, User
from src.services.reprocessing import queue_transcription_reprocess
from src.services.transcription.base import TranscriptionResponse, TranscriptionSegment
from src.services.transcription.connectors.clinic_stub import ClinicStubTranscriptionConnector
from src.tasks.processing import _plain_transcript_text
from test_wp_v8_asr_link_and_receipt import _run, upload  # noqa: F401
from test_wp_v8_web_ingest import author, originals  # noqa: F401


@pytest.fixture
def prepared(upload):
    dictation, source_job = upload
    _run(source_job)
    assert source_job.status == "completed", source_job.error_message
    original = ClinicOriginal.query.filter_by(dictation_id=dictation.id).one()
    init_audio_revision(dictation, original, 5000)
    source = db.session.get(Recording, source_job.recording_id)
    source.status = "COMPLETED"
    db.session.commit()
    return dictation, source, source_job


def _repeat(source):
    source.status = "COMPLETED"
    db.session.commit()
    job_id = queue_transcription_reprocess(source, db.session.get(User, source.user_id))
    return db.session.get(ProcessingJob, job_id)


def _response(monkeypatch, text="Neuer Entwurf.", during=None):
    response = TranscriptionResponse(text=text, language="de", segments=[
        TranscriptionSegment(text=text, start_time=0.1, end_time=1.2,
                             words=[{"word": text.split()[0], "start": 0.1, "end": 0.4},
                                    {"word": text.split()[1], "start": 0.5, "end": 1.2}]),
    ])

    def transcribe(_self, request):
        assert request.audio_file.read()  # The real runner materialized the retained source.
        if during:
            during()
        return response

    monkeypatch.setattr(ClinicStubTranscriptionConnector, "transcribe", transcribe)
    return response


def _binding(job):
    db.session.refresh(job)
    return json.loads(job.params)[REPEAT_PARAM]


def _projection(dictation, source, expected):
    db.session.expire_all()
    assert revisions.current_revision(dictation).body == expected
    assert _plain_transcript_text(source.transcription) == expected
    assert _plain_transcript_text(db.session.get(Recording, dictation.recording_id).transcription) == expected


def test_explicit_repeat_appends_once_with_ancestry_receipt_and_own_timings(prepared, monkeypatch):
    dictation, source, source_job = prepared
    first = revisions.current_revision(dictation)
    original = db.session.get(ClinicOriginal, first.original_version_id)
    original_values = (original.content_hash, original.storage_key, original.receipt, original.origin_ref)
    old_job_values = (source_job.params, source_job.retry_count, source_job.status)
    old_links = [(row.id, row.transcript_revision_id) for row in ClinicTextAudioLink.query.all()]
    job = _repeat(source)
    binding = _binding(job)
    assert binding["transcript_revision_id"] == first.id
    assert binding["source_job_id"] == source_job.id
    assert binding["source_attempt"] == source_job.retry_count
    _projection(dictation, source, first.body)  # Queueing must not erase the current text.
    _response(monkeypatch)
    _run(job)
    assert job.status == "completed", job.error_message
    successor = revisions.current_revision(dictation)
    assert successor.id != first.id and successor.parent_revision_id == first.id
    assert successor.kind == "ASR" and successor.original_version_id == original.id
    _projection(dictation, source, "Neuer Entwurf.")
    links = ClinicTextAudioLink.query.filter_by(transcript_revision_id=successor.id).all()
    assert [(row.audio_start_ms, row.audio_end_ms) for row in links] == [(100, 1200)]
    assert successor.body[links[0].text_start:links[0].text_end] == successor.body
    aligned = alignment.ClinicAlignmentRevision.query.filter_by(transcript_revision_id=successor.id).one()
    assert aligned.audio_revision_id == binding["audio_revision_id"]
    assert aligned.source_kind == "asr" and aligned.quality["valid_words"] == 2
    assert [(item["start_ms"], item["end_ms"]) for item in aligned.items] == [(100, 400), (500, 1200)]
    receipt = _binding(job)["receipt"]
    assert receipt == {"job_id": job.id, "attempt": 0, "outcome": "applied",
                       "transcript_revision_id": successor.id}
    _response(monkeypatch, "Anderer Entwurf.")
    _run(job)  # Same job with different output must not produce a second successor.
    assert [row.id for row in revisions.history(dictation)] == [first.id, successor.id]
    assert _binding(job)["receipt"] == receipt
    assert alignment.ClinicAlignmentRevision.query.filter_by(transcript_revision_id=successor.id).count() == 1
    assert [(row.id, row.transcript_revision_id) for row in ClinicTextAudioLink.query.filter_by(
        transcript_revision_id=first.id)] == old_links
    assert (original.content_hash, original.storage_key, original.receipt, original.origin_ref) == original_values
    assert (source_job.params, source_job.retry_count, source_job.status) == old_job_values
    _projection(dictation, source, "Neuer Entwurf.")


def test_identical_result_consumes_attempt_without_replacing_projection(prepared, monkeypatch):
    dictation, source, _source_job = prepared
    first = revisions.current_revision(dictation)
    stored = source.transcription
    job = _repeat(source)
    _run(job)  # The unchanged local stub returns exactly the original result.
    assert revisions.current_revision(dictation).id == first.id
    assert source.transcription == stored
    assert _binding(job)["receipt"]["outcome"] == "unchanged"
    _response(monkeypatch)
    _run(job)
    assert len(revisions.history(dictation)) == 1
    assert source.transcription == stored
    _projection(dictation, source, first.body)


@pytest.mark.parametrize("change", ["head", "audio", "source_attempt", "newer_job", "human_during"])
def test_stale_repeat_preserves_current_text(prepared, monkeypatch, change):
    dictation, source, source_job = prepared
    first = revisions.current_revision(dictation)
    job = _repeat(source)
    alignment_ids = [row.id for row in alignment.ClinicAlignmentRevision.query.filter_by(
        dictation_id=dictation.id)]

    def move_head():
        revisions.add_human_revision(dictation, first, "Menschliche Fassung.", source.user_id)
        db.session.commit()

    if change == "head":
        revisions.add_asr_revision(dictation, db.session.get(ClinicOriginal, first.original_version_id),
                                   "Aktueller Entwurf.", "stub:2")
    elif change == "source_attempt":
        source_job.retry_count += 1
    elif change == "audio":
        apply_cut(dictation, 0, 100)
    elif change == "newer_job":
        job.status = "failed"
        db.session.flush()
        newer = _repeat(source)
        newer.status = "completed"
    db.session.commit()
    _response(monkeypatch, during=move_head if change == "human_during" else None)
    _run(job)
    expected = "Menschliche Fassung." if change == "human_during" else (
        "Aktueller Entwurf." if change == "head" else first.body)
    _projection(dictation, source, expected)
    assert len(revisions.history(dictation)) == (2 if change in ("head", "human_during") else 1)
    assert _binding(job)["receipt"]["outcome"] == "stale"
    assert [row.id for row in alignment.ClinicAlignmentRevision.query.filter_by(
        dictation_id=dictation.id)] == alignment_ids


def test_new_explicit_attempt_after_identical_result_can_advance(prepared, monkeypatch):
    dictation, source, _source_job = prepared
    first = revisions.current_revision(dictation)
    unchanged = _repeat(source)
    _run(unchanged)
    next_job = _repeat(source)
    assert _binding(next_job)["source_job_id"] == unchanged.id
    _response(monkeypatch)
    _run(next_job)
    assert len(revisions.history(dictation)) == 2
    assert revisions.current_revision(dictation).parent_revision_id == first.id
    _projection(dictation, source, "Neuer Entwurf.")


def test_repeat_started_on_human_head_keeps_correction_and_side_revision(prepared, monkeypatch):
    dictation, source, _source_job = prepared
    first = revisions.current_revision(dictation)
    human = revisions.add_human_revision(dictation, first, "Menschliche Fassung.", source.user_id)
    db.session.commit()
    job = _repeat(source)
    _response(monkeypatch)
    _run(job)
    _projection(dictation, source, human.body)
    assert len(revisions.history(dictation)) == 3
    side = revisions.history(dictation)[-1]
    assert side.parent_revision_id == human.id and revisions.is_flagged(dictation, side)
    assert _binding(job)["receipt"]["outcome"] == "protected"
    _response(monkeypatch, "Anderer Entwurf.")
    _run(job)
    assert len(revisions.history(dictation)) == 3
    _projection(dictation, source, human.body)


def test_ingest_failure_rolls_back_successor_and_does_not_consume_repeat(prepared, monkeypatch):
    from src.clinic import correction

    dictation, source, _source_job = prepared
    first = revisions.current_revision(dictation)
    job = _repeat(source)
    _response(monkeypatch)
    ingest = correction.ingest_asr_result

    def fail_after_append(*args, **kwargs):
        ingest(*args, **kwargs)
        raise RuntimeError("synthetic ingest failure")

    monkeypatch.setattr(correction, "ingest_asr_result", fail_after_append)
    _run(job)
    assert len(revisions.history(dictation)) == 1
    assert "receipt" not in _binding(job)
    _projection(dictation, source, first.body)
    monkeypatch.setattr(correction, "ingest_asr_result", ingest)
    _run(job)
    assert len(revisions.history(dictation)) == 2
    assert _binding(job)["receipt"]["outcome"] == "applied"
    _projection(dictation, source, "Neuer Entwurf.")


def test_http_repeat_ingress_queues_bound_job_and_rejects_active_duplicate(prepared, monkeypatch):
    from src.app import app

    dictation, source, source_job = prepared
    first = revisions.current_revision(dictation)
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = str(source.user_id)
        session["_fresh"] = True
    url = f"/recording/{source.id}/reprocess_transcription"
    response = client.post(url, json={})
    assert response.status_code == 200, response.get_data(as_text=True)
    job = ProcessingJob.query.filter_by(recording_id=source.id, job_type="reprocess_transcription").one()
    assert _binding(job)["transcript_revision_id"] == first.id
    assert _binding(job)["source_job_id"] == source_job.id
    assert client.post(url, json={}).status_code == 400
    assert ProcessingJob.query.filter_by(recording_id=source.id, job_type="reprocess_transcription").count() == 1
    _response(monkeypatch)
    _run(job)
    _projection(dictation, source, "Neuer Entwurf.")
    assert revisions.current_revision(dictation).parent_revision_id == first.id


@pytest.mark.parametrize("changed_field", ["retry_count", "started_at"])
def test_superseded_worker_cannot_consume_replacement_attempt(prepared, monkeypatch, changed_field):
    from datetime import datetime

    dictation, source, _source_job = prepared
    first = revisions.current_revision(dictation)
    job = _repeat(source)

    def replace_attempt():
        running = db.session.get(ProcessingJob, job.id)
        if changed_field == "retry_count":
            running.retry_count += 1
        else:
            running.started_at = datetime(2026, 11, 20, 12, 0, 0)
        db.session.commit()

    _response(monkeypatch, during=replace_attempt)
    _run(job)
    _projection(dictation, source, first.body)
    assert "receipt" not in _binding(job)
    assert len(revisions.history(dictation)) == 1
    _response(monkeypatch)
    _run(job)
    _projection(dictation, source, "Neuer Entwurf.")
    assert _binding(job)["receipt"]["attempt"] == job.retry_count
    assert len(revisions.history(dictation)) == 2
