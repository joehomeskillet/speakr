"""Resolve repeat ingress and verify the exact audio passed to recognition."""

from contextlib import contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import tempfile

from flask import current_app, has_request_context, request

from src.clinic import audio_revisions, policy, pool_policy
from src.clinic.asr_repeat import ASR_JOBS, REPEAT_PARAM
from src.clinic.models import ClinicOriginal
from src.clinic.revisions import current_revision
from src.clinic.web_ingest import dictation_for_recording
from src.database import db
from src.models import ProcessingJob, Recording
from src.services.reprocessing import ReprocessError


def resolve_repeat_source(recording, user):
    """Authorize the actual object and return its single verified source recording.

    The caller keeps the dictation lock through capture of the repeat binding.
    HTTP callers must hold the dictation lease even when using the upload alias.
    """
    dictation = dictation_for_recording(recording.id)
    if dictation is None:
        return recording
    db.session.refresh(dictation, with_for_update=True)
    subject = policy.build_subject(user.id, bool(user.is_admin))
    decision = pool_policy.decide(subject, 'edit', dictation)
    if not decision.allowed:
        raise ReprocessError('recording_reprocess_denied', 403 if decision.visible else 404)
    if has_request_context():
        from src.clinic import guard, lease, live_session
        body = request.get_json(silent=True) or request.form
        generation = body.get('lease_generation')
        if type(generation) is not int or generation < 1:
            raise ReprocessError('invalid_lease_generation', 400,
                                 'Eine gültige Sperrversion ist erforderlich.')
        try:
            guard.assert_dictation_edit_allowed(dictation.id, user.id, generation,
                                                datetime.now(timezone.utc))
        except (lease.LeaseConflict, live_session.LiveSessionError) as exc:
            raise ReprocessError(exc.reason, 409,
                                 'Die Bearbeitungssperre oder Live-Sitzung erlaubt diese Änderung nicht.') from exc
    head = current_revision(dictation)
    originals = ClinicOriginal.query.filter_by(dictation_id=dictation.id)
    original = (originals.filter_by(id=head.original_version_id).one_or_none()
                if head else originals.one_or_none())
    if original is None:
        raise ReprocessError('reprocess_audio_not_found', 404)
    from src.clinic.legacy_backfill import ORIGIN_REF_PREFIXES
    source = None
    ref = original.origin_ref or ''
    for prefix in ORIGIN_REF_PREFIXES:
        if ref.startswith(prefix):
            number, separator, _ = ref[len(prefix):].partition(':')
            if separator and number.isascii() and number.isdigit() and 0 < int(number) <= 2**63 - 1:
                source = db.session.get(Recording, int(number))
            break
    anchor = db.session.get(Recording, dictation.recording_id)
    if source is None and recording.id == dictation.recording_id and recording.audio_path:
        source = recording
    if (source is None or anchor is None or source.user_id != anchor.user_id
            or dictation_for_recording(source.id) != dictation):
        raise ReprocessError('reprocess_source_invalid', 409)
    db.session.refresh(source, with_for_update=True)
    decision = policy.decide(subject, 'edit', policy.build_facts(source.id, source.user_id))
    if not decision.allowed:
        raise ReprocessError('recording_reprocess_denied', 403 if decision.visible else 404)
    # A projection can lag or be reset; the active queue row is authoritative.
    active = ProcessingJob.query.filter(
        ProcessingJob.recording_id.in_({source.id, anchor.id}),
        ProcessingJob.job_type.in_(ASR_JOBS), ProcessingJob.status.in_(('queued', 'processing')),
    ).first()
    if active is not None:
        raise ReprocessError('recording_already_processing', 400)
    return source


def _audio_bytes(dictation, binding):
    from src.clinic.app_wiring import make_source_loader
    from src.clinic.audio_render import render_current_revision
    from src.clinic.startup import originals_root
    from src.services.storage.locator import local_path_from_key

    audio = audio_revisions.current_audio_revision(dictation)
    if binding['audio_revision_id'] != (audio.id if audio else None):
        raise ReprocessError('reprocess_audio_stale', 409)
    root = originals_root(current_app)
    try:
        if audio is not None:
            edl = audio_revisions.get_revision_edl(dictation, audio)
            rendered = render_current_revision(dictation, audio.id, make_source_loader(root)(dictation, edl))
            return rendered.wav, 'audio/wav', 'current.wav'
        head = current_revision(dictation)
        originals = ClinicOriginal.query.filter_by(dictation_id=dictation.id)
        original = (originals.filter_by(id=head.original_version_id).one_or_none()
                    if head else originals.one_or_none())
        if original is None:
            raise ReprocessError('reprocess_audio_not_found', 404)
        path = Path(local_path_from_key(root, original.storage_key))
        data = path.read_bytes()
        if sha256(data).hexdigest() != original.content_hash or len(data) != original.size_bytes:
            raise ReprocessError('reprocess_audio_integrity_failed', 409)
        return data, None, path.name
    except (audio_revisions.AudioRevisionError, OSError, ValueError) as exc:
        raise ReprocessError('reprocess_audio_unavailable', 409,
                             'Die aktuelle Audioversion kann nicht sicher bereitgestellt werden. '
                             'Es wurde keine Neuverarbeitung gestartet.') from exc


def bind_audio_input(recording, params):
    binding = params[REPEAT_PARAM]
    dictation = dictation_for_recording(recording.id)
    data, mime, filename = _audio_bytes(dictation, binding)
    binding['audio_input'] = {'sha256': sha256(data).hexdigest(), 'size_bytes': len(data),
                              'mime_type': mime, 'filename': filename}


@contextmanager
def worker_audio_input(recording, filepath, filename, mime):
    """Legacy jobs retain their input; bound repeats never fall back to original audio."""
    from src.clinic.config import clinic_mode_enabled
    from src.services import job_control

    job_id = job_control.current().job_id
    job = db.session.get(ProcessingJob, job_id) if clinic_mode_enabled() and job_id else None
    params = json.loads(job.params or '{}') if job else {}
    binding = params.get(REPEAT_PARAM)
    if not isinstance(binding, dict):
        yield filepath, filename, mime
        return
    dictation = dictation_for_recording(recording.id)
    if (dictation is None or dictation.id != binding.get('dictation_id')
            or recording.created_at.isoformat() != binding.get('recording_created_at')):
        raise ReprocessError('reprocess_source_invalid', 409)
    captured = binding.get('audio_input')
    if not isinstance(captured, dict):
        raise ReprocessError('reprocess_audio_unbound', 409)
    try:
        data, rendered_mime, rendered_filename = _audio_bytes(dictation, binding)
    except ReprocessError as error:
        if error.code == 'reprocess_audio_stale':
            from src.clinic.asr_repeat import receipt
            receipt(job, params, 'stale', current_revision(dictation))
        raise
    if captured.get('sha256') != sha256(data).hexdigest() or captured.get('size_bytes') != len(data):
        raise ReprocessError('reprocess_audio_integrity_failed', 409)
    with tempfile.TemporaryDirectory(prefix='clinic-asr-') as directory:
        path = Path(directory) / rendered_filename
        path.write_bytes(data)
        yield str(path), rendered_filename, rendered_mime or mime
