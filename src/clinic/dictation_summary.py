"""Explicit, revision-bound clinic summaries on Speakr's existing summary queue."""

import json
import os

import httpx

from flask import abort, jsonify, request
from flask_login import current_user
from flask_wtf.csrf import validate_csrf
from sqlalchemy.exc import MultipleResultsFound
from wtforms.validators import ValidationError

from src.clinic import config, policy, pool_policy, revisions
from src.clinic.identity import ClinicAccountPolicy
from src.clinic.models import ClinicDictation, ClinicDictationSummary, ClinicOriginal
from src.database import db
from src.models import ProcessingJob, Recording, SystemSetting, User
from src.services import audit, job_control, llm
from src.services.job_queue import DICTATION_SUMMARY_JOB, job_queue
from src.utils.error_formatting import is_transcription_error


class SummaryUnavailable(RuntimeError):
    permanent = True


def local_model_ready():
    # The process's constructed client is authoritative as well as the current feature gate.
    return (config.clinic_mode_enabled() and not config.feature_locked('generative_summary')
            and llm.client is not None and config.is_local_model_url(str(llm.client.base_url)))


def allowed(user, action, dictation):
    if user is None or not user.is_active:
        return False
    account = db.session.get(ClinicAccountPolicy, user.id)
    return ((account is None or account.status == 'active')
            and pool_policy.decide(policy.build_subject(user.id), action, dictation).allowed)


def usable(text):
    return bool(text and text.strip() and not is_transcription_error(text.lstrip()) and not
                text.lstrip().startswith(('[Summary skipped', '[Summary not generated', '[Summary generation failed')))


def source_recording(dictation, head):
    """Look up the trusted source receipt with the same owner and anchor checks as ASR."""
    from src.clinic.legacy_backfill import ORIGIN_REF_PREFIXES
    from src.clinic.web_ingest import dictation_for_recording

    originals = ClinicOriginal.query.filter_by(dictation_id=dictation.id)
    if head:
        originals = originals.filter_by(id=head.original_version_id)
    sources = originals.limit(2).all()
    if len(sources) != 1:
        return None
    ref = sources[0].origin_ref
    if not ref or not ref.startswith(ORIGIN_REF_PREFIXES):
        return None
    parts = ref.split(':', 3)
    if len(parts) != 4 or not parts[2].isascii() or not parts[2].isdigit() or len(parts[2]) > 19:
        return None
    rid = int(parts[2])
    if not 0 < rid < 2**63 or rid == dictation.recording_id:
        return None
    row = db.session.get(Recording, rid)
    anchor = db.session.get(Recording, dictation.recording_id)
    if not row or not anchor or row.user_id != anchor.user_id:
        return None
    try:
        return row if dictation_for_recording(rid) == dictation else None
    except MultipleResultsFound:
        # Ambiguous historical receipts cannot establish which dictation owns the summary.
        return None


def summary_state(dictation, head, may_generate):
    saved = db.session.get(ClinicDictationSummary, dictation.id)
    text = None
    status = 'empty'
    if saved:
        text = saved.body
        status = 'ready' if head and head.id == saved.revision_id else 'stale'
    else:
        anchor = db.session.get(Recording, dictation.recording_id)
        legacy = anchor and anchor.summary
        if not usable(legacy):
            source = source_recording(dictation, head)
            if source and source.summary:
                legacy = source.summary
        if usable(legacy):
            # Historical summaries have no binding to a particular clinic revision.
            status, text = 'stale', legacy
        elif legacy:
            status = 'failed'
    latest = ProcessingJob.query.filter_by(recording_id=dictation.recording_id, job_type=DICTATION_SUMMARY_JOB
                                          ).order_by(ProcessingJob.id.desc()).first()
    if latest and latest.status in ('queued', 'processing', 'failed'):
        status = latest.status
    return dict(status=status, text=text, may_generate=may_generate)


def register_route(bp):
    @bp.route('/api/clinic/v1/dictations/<uuid:dictation_id>/summary', methods=['GET', 'POST'])
    def summary(dictation_id):
        row = db.session.get(ClinicDictation, str(dictation_id))
        if row is None or not allowed(current_user, 'view', row):
            abort(404)
        if request.method == 'POST':
            try:
                validate_csrf(request.headers.get('X-CSRFToken') or request.headers.get('X-CSRF-Token'))
            except ValidationError:
                abort(403)
        edit = allowed(current_user, 'edit', row)
        head = revisions.current_revision(row)
        valid = head is not None and usable(head.body) and len(head.body.strip()) >= 10
        ready = local_model_ready()
        if request.method != 'POST':
            audit.record('recording.content.read', recording_id=row.recording_id, actor_kind='session',
                         actor_user_id=current_user.id, details={'part': 'summary'})
            return jsonify(summary_state(row, head, bool(edit and valid and ready)))
        if not edit:
            abort(403)
        if not ready:
            abort(503)
        if not valid:
            abort(409)
        rid, uid, did, rev = row.recording_id, current_user.id, row.id, head.id
        db.session.rollback()
        job_id = job_queue.enqueue(user_id=uid, recording_id=rid, job_type=DICTATION_SUMMARY_JOB,
                                   params={'dictation_id': did, 'revision_id': rev})
        job = db.session.get(ProcessingJob, job_id)
        if json.loads(job.params).get('revision_id') != rev:
            abort(409)
        audit.record('recording.reprocessed', recording_id=rid, actor_kind='session',
                     actor_user_id=uid, details={'kind': 'summary'})
        return jsonify(summary_state(row, head, True)), 202


def _authorized_input(job, params, *, lock=False):
    row = db.session.get(ClinicDictation, params['dictation_id'], populate_existing=True, with_for_update=lock)
    user = db.session.get(User, job.user_id, populate_existing=True)
    if (row is None or row.recording_id != job.recording_id or not allowed(user, 'view', row)
            or not allowed(user, 'edit', row) or not local_model_ready()):
        raise SummaryUnavailable('Summary unavailable')
    head = revisions.current_revision(row)
    if head is None or head.id != params['revision_id'] or not usable(head.body):
        raise SummaryUnavailable('Summary text revision changed')
    return row, head, user


def run_summary(job, params, current_attempt):
    from src.tasks import processing
    from src.services.llm_settings import get_temperature
    from src.transcription_prompts import build_summary_prompt, build_user_content, clean_llm_response

    attempt = (job.status, job.retry_count, job.started_at, job.created_at)
    job_id, user_id = job.id, job.user_id
    row, head, user = _authorized_input(job, params)
    rec = db.session.get(Recording, row.recording_id)
    instructions, _ = processing.resolve_summary_instructions(rec, user)
    limit = SystemSetting.get_setting('transcript_length_limit', 30000)
    text = head.body if limit == -1 else head.body[:limit]
    optimized, breaks = processing.prefix_cache_flags()
    system, prompt = build_summary_prompt(text, '', instructions, user.output_language, optimized)
    messages = [{'role': 'system', 'content': system},
                {'role': 'user', 'content': build_user_content(prompt, text, optimized, breaks)}]
    temperature = get_temperature('summary')
    db.session.rollback()
    job_control.current().check_cancel()
    try:
        # A local URL must stay local even when the host sets a proxy or replies with a redirect.
        with httpx.Client(trust_env=False, follow_redirects=False) as transport:
            client = llm.client.with_options(http_client=transport)
            reply = llm.call_llm_completion(
                messages, temperature=temperature, max_tokens=int(os.environ.get('SUMMARY_MAX_TOKENS', '3000')),
                user_id=user_id, operation_type='summarization', model_client=client)
    except Exception:
        # Provider errors can quote the corrected transcript, which isn't in Recording.transcription.
        raise RuntimeError('Summary provider failed') from None
    choice = reply.choices[0] if reply and reply.choices else None
    body = clean_llm_response(choice.message.content or '') if choice else ''
    if not usable(body) or choice.finish_reason != 'stop':
        raise RuntimeError('Summary provider returned an incomplete result')
    job = current_attempt(job_id, attempt, lock=True)
    if job is None:
        return
    job_control.current().check_cancel()
    row, head, _ = _authorized_input(job, params, lock=True)
    db.session.merge(ClinicDictationSummary(dictation_id=row.id, revision_id=head.id, body=body))
    # The parent worker commits this write with its own terminal job status.
