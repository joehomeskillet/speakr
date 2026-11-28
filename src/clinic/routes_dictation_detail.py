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

from src.clinic import release, revisions, workflow
from src.clinic.acceptance_policy import AcceptanceLedger, PolicyError, SCOPE_DICTATION
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
    403: ('FORBIDDEN', 'Zugriff verweigert'),
    404: ('NOT_FOUND', 'Diktat nicht gefunden'),
    409: ('CONFLICT', 'Der aktuelle Textstand kann nicht zusammengefasst werden'),
    503: ('SUMMARY_UNAVAILABLE', 'Lokales Sprachmodell nicht verfügbar'),
}


def _problem(status: int) -> Response:
    code, title = _PROBLEMS[status]
    response = jsonify(type='about:blank', title=title, status=status, code=code,
                       correlation_id=str(uuid4()))
    response.status_code = status
    response.mimetype = 'application/problem+json'
    response.headers['Cache-Control'] = 'no-store'
    return response


# Workflow action -> (policy permission, workflow trigger); same table as workflow_actions
# and workflow_finalize, so the header offers exactly what the command endpoints accept.
_ACTIONS = {
    'submit': ('clinic.review', 'complete'),
    'accept': ('clinic.accept', 'accept'),
    'reject': ('clinic.accept', 'reject'),
    'reopen': ('clinic.reopen', 'reopen'),
    'release': ('clinic.release', 'complete'),
    'archive': ('clinic.archive', 'report_archive'),
}

# "complete" branches on acceptance: submit is the branch into acceptance, release the one to done.
_REQUIRED_TARGET = {'submit': workflow.TO_ACCEPT, 'release': workflow.DONE}


def _capabilities(user: User, dictation: ClinicDictation, flow, snap: dict,
                  authorize: Callable[[User, str, ClinicDictation], bool], acceptance_policy=None) -> dict:
    """Allowed workflow actions, their target status and the revision binding for the caller."""
    if flow is None:
        return dict(allowed_actions=[], transitions={}, binding={})
    allowed, transitions = [], {}
    if (flow.status == workflow.DRAFT and user.id == dictation.author_user_id
            and authorize(user, 'clinic.dictate', dictation) is True):
        allowed.append('send')
        transitions['send'] = workflow.PREP_ONLINE
    for name, (permission, trigger) in _ACTIONS.items():
        target = workflow._destination(flow.status, trigger, flow.acceptance_required, flow.resume_status)
        if target is None or target[0] != _REQUIRED_TARGET.get(name, target[0]):
            continue
        if authorize(user, permission, dictation) is True:
            allowed.append(name)
            transitions[name] = target[0]
    head = revisions.current_revision(dictation)
    binding = dict(textRevisionId=None if head is None else head.id,
                   contextRevisionId=snap['current_context_revision_id'])
    try:
        rule = (acceptance_policy(dictation) if acceptance_policy else
                AcceptanceLedger().current(SCOPE_DICTATION, dictation.id))
        binding['acceptanceRuleId'] = rule.rule_id
    except PolicyError:
        binding['acceptanceRuleId'] = None
    audio_id = release.current_audio_id(dictation.id)
    binding['audioRevisionId'] = audio_id
    if 'release' in allowed and head is not None and audio_id is not None:
        binding['release'] = dict(
            original_version_id=audio_id,
            transcript_revision_id=head.id, context_revision_id=snap['current_context_revision_id'],
            document_type='report', mode='PLATFORM_REVIEWED_KIS_DRAFT', text_sha256=head.normalised_text_hash)
    return dict(allowed_actions=allowed, transitions=transitions, binding=binding)


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
    acceptance_policy=None,
) -> Blueprint:
    """Mount with the trusted session channel and the current object policy.

    The `history` action (view scope) gates the read; the same `authorize` callback
    decides each workflow action offered in `allowed_actions`, so a reader sees the
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
            routing_target=workflow.routing_public(flow),
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
            **_capabilities(current_user, dictation, flow, snap, authorize, acceptance_policy),
        )

    @bp.get('/api/clinic/v1/dictations/<uuid:dictation_id>/review')
    def get_review(dictation_id: UUID) -> Response:
        dictation = db.session.get(ClinicDictation, str(dictation_id))
        if dictation is None or not authorize(current_user, 'history', dictation):
            abort(404)
        flow = db.session.get(workflow.ClinicWorkflowDictation, dictation.id)
        head = revisions.current_revision(dictation)
        if flow is None or flow.status in (workflow.DRAFT, workflow.TO_ARCHIVE) or head is None:
            abort(404)
        snap = dictation_snapshot(dictation)
        capabilities = _capabilities(current_user, dictation, flow, snap, authorize, acceptance_policy)
        binding = capabilities.pop('binding')
        audit.record('recording.content.read', recording_id=dictation.recording_id,
                     actor_kind='session', actor_user_id=current_user.id, details={'part': 'review'})
        return jsonify(
            dictation_id=dictation.id, etag=snap['etag'], revision=dictation.version_counter,
            asr_status=snap['asr_state'], status=flow.status,
            binding=dict(text_revision_id=binding['textRevisionId'],
                         audio_revision_id=binding['audioRevisionId'],
                         context_revision_id=binding['contextRevisionId'],
                         acceptance_rule_id=binding['acceptanceRuleId'], release=binding.get('release', {})),
            **capabilities,
        )

    from src.clinic.dictation_summary import register_route
    register_route(bp)
    return bp
