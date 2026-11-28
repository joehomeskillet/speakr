"""Transcript and audio of the admin dictation overview (w308), on their own blueprint.

The two views read dictation content, so they must not sit on the upstream ``admin``
blueprint (the upstream audit-coverage test forbids content reads under ``admin.*``).
Same URLs and behaviour as before: active site admin only, audit row stored first,
503 when it cannot be written, no-store. Mounted by ``register_clinic_identity`` in
src/app.py in clinic mode only; with clinic mode off neither route exists.
"""

from __future__ import annotations

import os

from flask import Blueprint, current_app, jsonify, redirect, send_file
from flask_login import current_user, login_required

from src.clinic.config import clinic_mode_enabled
from src.database import db
from src.models import AuditEvent, Recording
from src.services import audit

_AUDIT_EVENTS = ('recording.media.played', 'recording.content.read')

blueprint = Blueprint('clinic_admin_dictations', __name__)


def _response(response, status=200):
    response.status_code = status
    response.headers['Cache-Control'] = 'no-store'
    return response


def _refuse(status, code):
    return _response(jsonify({'error': code.replace('_', ' '), 'code': code}), status)


def _open(dictation_id, part):
    """(row, None) once the opening is allowed and its audit row is stored, else (None, refusal)."""
    from src.clinic.admin_dictation_access import can_open_content, open_content
    decision = can_open_content(db.session, current_user, dictation_id, part)
    if not decision.allowed:
        if decision.reason == 'private_recording_type':
            return None, _refuse(404, 'not_found')
        return None, _refuse(404 if decision.reason == 'not_found' else 403, decision.reason)
    if decision.reason != 'admin_full':
        return None, _refuse(404, 'not_found')
    row = db.session.get(Recording, dictation_id)
    if part == 'audio' and not row.audio_path:
        return None, _refuse(404, 'audio_not_found')
    if part == 'transcript' and row.transcription is None:
        return None, _refuse(404, 'transcript_not_found')
    baseline = db.session.query(db.func.max(AuditEvent.id)).scalar() or 0
    try:
        decision, _ = open_content(db.session, current_user, dictation_id, part)
        stored = decision.allowed and audit.flush() and db.session.query(AuditEvent.id).filter(
            AuditEvent.id > baseline, AuditEvent.actor_user_id == current_user.id,
            AuditEvent.recording_id == dictation_id, AuditEvent.event.in_(_AUDIT_EVENTS),
        ).first() is not None
    except Exception as exc:  # noqa: BLE001 - nothing is delivered without its audit row
        current_app.logger.error('Admin opening of dictation %s not audited: %s', dictation_id, type(exc).__name__)
        return None, _refuse(503, 'audit_unavailable')
    if not stored:
        return None, _refuse(503, 'audit_unavailable')
    return row, None


@blueprint.route('/api/admin/clinic/dictations/<int:dictation_ref>/transcript', methods=['GET'])
@login_required
def transcript(dictation_ref):
    """Transcript of any user's dictation for an active site admin; audit row first."""
    if not clinic_mode_enabled():
        return _refuse(404, 'not_found')
    row, refusal = _open(dictation_ref, 'transcript')
    if refusal is not None:
        return refusal
    return _response(jsonify({'recording_id': row.id, 'transcription': row.transcription}))


@blueprint.route('/api/admin/clinic/dictations/<int:dictation_ref>/audio', methods=['GET'])
@login_required
def audio(dictation_ref):
    """Audio of any user's dictation for an active site admin; audit row first."""
    from src.services.storage import get_storage_service
    from src.utils.mime import served_media_mime, sandbox_media_response
    if not clinic_mode_enabled():
        return _refuse(404, 'not_found')
    row, refusal = _open(dictation_ref, 'audio')
    if refusal is not None:
        return refusal
    try:
        serve_mime = served_media_mime(row.mime_type, row.audio_path)
        delivery = get_storage_service().get_audio_delivery(
            row.audio_path, download=False, mime_type=serve_mime, download_name=None, is_public=False)
        if delivery.mode == 'redirect_url':
            return _response(redirect(delivery.url, code=302), 302)
        if not delivery.local_path or not os.path.exists(delivery.local_path):
            return _refuse(404, 'audio_file_missing')
        from src.clinic import dictation_playback
        path = dictation_playback.ensure_playback(delivery.local_path)
        if path:
            serve_mime = dictation_playback.PLAYBACK_MIME
        response = sandbox_media_response(send_file(path or delivery.local_path, mimetype=serve_mime, conditional=True))
        response.headers['Cache-Control'] = 'no-store'
        return response
    except Exception as exc:  # noqa: BLE001
        current_app.logger.error('Admin audio delivery for dictation %s failed: %s', dictation_ref, type(exc).__name__)
        return _refuse(500, 'unexpected_error')
