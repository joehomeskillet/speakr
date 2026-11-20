"""Real lease-authorized repeat ingress and synthetic worker byte evidence."""

from datetime import datetime, timezone
from hashlib import sha256
import io
import wave

import pytest

from src.app import app
from src.clinic import alignment, revisions
from src.clinic.audio_revisions import apply_cut, current_audio_revision
from src.clinic.lease import acquire
from src.database import db
from src.models import ProcessingJob, Recording
from src.services.transcription.connectors.clinic_stub import ClinicStubTranscriptionConnector
from test_wp_rest27_asr_successor import prepared, _binding, _response, _run  # noqa: F401
from test_wp_v8_asr_link_and_receipt import upload  # noqa: F401
from test_wp_v8_web_ingest import author, originals  # noqa: F401


def _client(dictation, source):
    held = acquire(dictation.id, source.user_id, datetime.now(timezone.utc))
    db.session.commit()
    client = app.test_client()
    with client.session_transaction() as session:
        session['_user_id'] = str(source.user_id)
        session['_fresh'] = True
    return client, {'lease_generation': held.generation}


def _post(client, recording_id, body):
    return client.post(f'/recording/{recording_id}/reprocess_transcription', json=body)


@pytest.mark.parametrize('anchor_first', [True, False])
def test_anchor_and_source_share_one_queue_job(prepared, anchor_first):
    dictation, source, source_job = prepared
    client, body = _client(dictation, source)
    ids = [dictation.recording_id, source.id]
    if not anchor_first:
        ids.reverse()
    response = _post(client, ids[0], body)
    assert response.status_code == 200, response.json
    job = ProcessingJob.query.filter_by(recording_id=source.id, job_type='reprocess_transcription').one()
    assert _binding(job)['source_job_id'] == source_job.id
    assert _binding(job)['audio_revision_id'] == current_audio_revision(dictation).id
    # Even a stale projection must not bypass the active-job check.
    source.status = 'COMPLETED'
    db.session.commit()
    assert _post(client, ids[1], body).status_code == 400
    assert ProcessingJob.query.filter_by(job_type='reprocess_transcription').count() == 1


@pytest.mark.parametrize('target', ['anchor', 'source'])
def test_both_ingress_aliases_require_current_lease(prepared, target):
    dictation, source, _ = prepared
    client, body = _client(dictation, source)
    rid = dictation.recording_id if target == 'anchor' else source.id
    assert _post(client, rid, {}).status_code == 400
    assert _post(client, rid, {'lease_generation': body['lease_generation'] + 1}).status_code == 409
    assert ProcessingJob.query.filter_by(job_type='reprocess_transcription').count() == 0


def test_cut_worker_receives_exact_current_render(prepared, monkeypatch):
    from src.clinic.app_wiring import make_source_loader
    from src.clinic.audio_render import render_current_revision
    from src.clinic.audio_revisions import get_revision_edl
    from src.clinic.startup import originals_root
    from test_stub_asr import _fixture_bytes

    dictation, source, _ = prepared
    apply_cut(dictation, 0, 1900)
    db.session.commit()
    audio = current_audio_revision(dictation)
    edl = get_revision_edl(dictation, audio)
    expected = render_current_revision(dictation, audio.id,
                                       make_source_loader(originals_root(app))(dictation, edl)).wav
    assert expected != _fixture_bytes()
    client, body = _client(dictation, source)
    response = _post(client, dictation.recording_id, body)
    assert response.status_code == 200, response.json
    job = ProcessingJob.query.filter_by(recording_id=source.id, job_type='reprocess_transcription').one()
    response = _response(monkeypatch)
    captured = []

    def transcribe(self, request):
        captured.append(request.audio_file.read())
        assert captured[-1] == expected
        assert request.mime_type == 'audio/wav'
        return response

    monkeypatch.setattr(ClinicStubTranscriptionConnector, 'transcribe', transcribe)
    _run(job)
    assert job.status == 'completed', job.error_message
    assert captured == [expected]
    head = revisions.current_revision(dictation)
    row = alignment.ClinicAlignmentRevision.query.filter_by(transcript_revision_id=head.id).one()
    assert row.audio_revision_id == audio.id
    assert _binding(job)['audio_input']['sha256'] == sha256(expected).hexdigest()
    with wave.open(io.BytesIO(expected)) as wav:
        print('CUT-BYTES', len(expected), sha256(expected).hexdigest(),
              'duration_ms', wav.getnframes() * 1000 // wav.getframerate(), 'revision', audio.id)


def test_audio_changed_after_queue_never_reaches_connector(prepared, monkeypatch):
    dictation, source, _ = prepared
    client, body = _client(dictation, source)
    assert _post(client, dictation.recording_id, body).status_code == 200
    job = ProcessingJob.query.filter_by(recording_id=source.id, job_type='reprocess_transcription').one()
    head = revisions.current_revision(dictation).id
    before = alignment.ClinicAlignmentRevision.query.count()
    apply_cut(dictation, 0, 1900)
    db.session.commit()
    calls = []
    monkeypatch.setattr(ClinicStubTranscriptionConnector, 'transcribe', lambda *a: calls.append(a))
    _run(job)
    assert not calls
    assert job.status == 'failed'
    assert 'reprocess_audio_stale' in job.error_message
    assert revisions.current_revision(dictation).id == head
    assert alignment.ClinicAlignmentRevision.query.count() == before


@pytest.mark.parametrize('target', ['anchor', 'source'])
@pytest.mark.parametrize('denied', ['anchor', 'source'])
def test_object_deny_cannot_be_bypassed_through_alias(prepared, target, denied):
    from src.clinic import policy

    dictation, source, _ = prepared
    client, body = _client(dictation, source)
    denied_id = dictation.recording_id if denied == 'anchor' else source.id
    policy.register_grant_provider(lambda uid: [policy.Deny(recording_ids=frozenset({denied_id}))])
    rid = dictation.recording_id if target == 'anchor' else source.id
    assert _post(client, rid, body).status_code == 403
    assert ProcessingJob.query.filter_by(job_type='reprocess_transcription').count() == 0


def test_original_tampering_refused_at_ingress(prepared):
    from pathlib import Path
    from src.clinic.models import ClinicOriginal
    from src.clinic.startup import originals_root
    from src.services.storage.locator import local_path_from_key

    dictation, source, _ = prepared
    original = ClinicOriginal.query.filter_by(dictation_id=dictation.id).one()
    path = Path(local_path_from_key(originals_root(app), original.storage_key))
    data = bytearray(path.read_bytes())
    data[-1] ^= 1
    path.write_bytes(data)
    client, body = _client(dictation, source)
    response = _post(client, dictation.recording_id, body)
    assert response.status_code == 409
    assert response.json['code'] == 'reprocess_audio_unavailable'
    assert 'Audioversion' in response.json['error']
    assert ProcessingJob.query.filter_by(job_type='reprocess_transcription').count() == 0


def test_audio_edit_during_recognition_stores_no_word_timings(prepared, monkeypatch):
    dictation, source, _ = prepared
    client, body = _client(dictation, source)
    assert _post(client, dictation.recording_id, body).status_code == 200
    job = ProcessingJob.query.filter_by(recording_id=source.id, job_type='reprocess_transcription').one()
    head = revisions.current_revision(dictation).id
    before = alignment.ClinicAlignmentRevision.query.count()

    def edit():
        apply_cut(dictation, 0, 1900)
        db.session.commit()

    _response(monkeypatch, during=edit)
    _run(job)
    assert revisions.current_revision(dictation).id == head
    assert alignment.ClinicAlignmentRevision.query.count() == before
    assert _binding(job)['receipt']['outcome'] == 'stale'


def test_tampered_audio_after_queue_never_reaches_connector(prepared, monkeypatch):
    from pathlib import Path
    from src.clinic.models import ClinicOriginal
    from src.clinic.startup import originals_root
    from src.services.storage.locator import local_path_from_key

    dictation, source, _ = prepared
    client, body = _client(dictation, source)
    assert _post(client, source.id, body).status_code == 200
    job = ProcessingJob.query.filter_by(recording_id=source.id, job_type='reprocess_transcription').one()
    head = revisions.current_revision(dictation).id
    before = alignment.ClinicAlignmentRevision.query.count()
    original = ClinicOriginal.query.filter_by(dictation_id=dictation.id).one()
    path = Path(local_path_from_key(originals_root(app), original.storage_key))
    data = bytearray(path.read_bytes())
    data[-1] ^= 1
    path.write_bytes(data)
    calls = []
    monkeypatch.setattr(ClinicStubTranscriptionConnector, 'transcribe', lambda *a: calls.append(a))
    _run(job)
    assert not calls
    assert job.status == 'failed'
    assert revisions.current_revision(dictation).id == head
    assert alignment.ClinicAlignmentRevision.query.count() == before
