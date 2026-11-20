"""Browser endpoint for the header of one dictation's detail view (V8 P03).

One read: author, pools, current takeover, recording time with its origin,
Fall-ID and the separate status axes. It carries no title, notes, patient
identifier, audio or transcript text (W16). Author and assignee are shown by
display name, which is staff data. Reading is audit-logged like the history.
"""

from collections.abc import Callable
from uuid import UUID, uuid4

from flask import Blueprint, Response, abort, jsonify, session
from flask_login import current_user
from werkzeug.exceptions import HTTPException

from src.clinic import workflow
from src.clinic.config import clinic_mode_enabled
from src.clinic.models import ClinicDictation
from src.clinic.pools import ClinicAuthorPool, ClinicPoolRoute, ClinicStaffPool
from src.clinic.worklist_entries import stamp
from src.database import db
from src.models import Recording, User
from src.services import audit

PATH = '/api/clinic/v1/dictations/<uuid:dictation_id>/detail'
_PROBLEMS = {
    401: ('UNAUTHORIZED', 'Anmeldung erforderlich'),
    404: ('NOT_FOUND', 'Diktat nicht gefunden'),
}


def _problem(status: int) -> Response:
    code, title = _PROBLEMS[status]
    response = jsonify(type='about:blank', title=title, status=status, code=code,
                       correlation_id=str(uuid4()))
    response.status_code = status
    response.mimetype = 'application/problem+json'
    response.headers['Cache-Control'] = 'no-store'
    return response


def _person(user_id: int | None) -> dict | None:
    """Stable id plus display name; the username stands in for an unset name."""
    if user_id is None:
        return None
    user = db.session.get(User, user_id)
    if user is None:
        return dict(user_id=user_id, display_name=None)
    return dict(user_id=user_id, display_name=user.name or user.username)


def _staff_pools(author_pool_id: str | None) -> list[dict]:
    rows = (
        db.session.query(ClinicStaffPool)
        .join(ClinicPoolRoute, ClinicPoolRoute.staff_pool_id == ClinicStaffPool.id)
        .filter(ClinicPoolRoute.author_pool_id == author_pool_id)
    )
    return sorted((dict(id=row.id, name=row.name) for row in rows), key=lambda p: (p['name'] or '', p['id']))


def create_blueprint(
    *, authorize: Callable[[User, str, ClinicDictation], bool],
    dictation_snapshot: Callable[[ClinicDictation], dict],
) -> Blueprint:
    """Mount with the trusted session channel and the current object policy.

    The `history` action (view scope) gates the read, so a reader sees the
    header exactly when the history is readable. A dictation the caller may not
    see answers 404 like an unknown one.
    """
    bp = Blueprint('clinic_dictation_detail', __name__)

    @bp.before_request
    def authenticate() -> None:
        if not clinic_mode_enabled():
            abort(404)
        if not (current_user.is_authenticated and current_user.is_active
                and session.get('_user_id') == current_user.get_id()):
            abort(401)

    @bp.errorhandler(HTTPException)
    def http_problem(error: HTTPException) -> Response | HTTPException:
        return _problem(error.code) if error.code in _PROBLEMS else error

    @bp.after_request
    def no_store(response: Response) -> Response:
        response.headers['Cache-Control'] = 'no-store'
        return response

    @bp.get(PATH)
    def get_detail(dictation_id: UUID) -> Response:
        dictation = db.session.get(ClinicDictation, str(dictation_id))
        if dictation is None or not authorize(current_user, 'history', dictation):
            abort(404)
        snap = dictation_snapshot(dictation)
        flow = db.session.get(workflow.ClinicWorkflowDictation, dictation.id)
        recording = db.session.get(Recording, dictation.recording_id)
        recorded = None if recording is None else recording.meeting_date
        fallback = recorded is None
        at = stamp(recorded if recorded is not None else (recording.created_at if recording else dictation.created_at))
        pool = None if dictation.pool_id is None else db.session.get(ClinicAuthorPool, dictation.pool_id)
        audit.record('recording.content.read', recording_id=dictation.recording_id,
                     actor_kind='session', actor_user_id=current_user.id, details={'part': 'detail'})
        return jsonify(
            dictation_id=dictation.id,
            etag=snap['etag'],
            revision=dictation.version_counter,
            author=_person(dictation.author_user_id),
            author_pool=None if pool is None else dict(id=pool.id, name=pool.name),
            staff_pools=_staff_pools(dictation.pool_id),
            takeover=_person(None if flow is None else flow.assignee_user_id),
            recording_time=dict(at=at, source='fallback' if fallback else 'recorded'),
            duration_seconds=None if flow is None else flow.audio_seconds,
            priority=None if flow is None else flow.priority,
            deadline_at=None if flow is None else stamp(flow.deadline_at),
            case_display=snap['case_display'],
            status_axes=dict(
                processing=snap['asr_state'],
                workflow=None if flow is None else flow.status,
                acceptance=snap['review_state'],
                output='RELEASED' if snap['active_release_id'] else 'NOT_RELEASED',
            ),
        )

    return bp
