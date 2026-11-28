"""Browser commands for dictation metadata, workflow transitions, release and archive.

Thin HTTP layer over ``workflow_metadata``, ``workflow_actions``,
``workflow_finalize`` and ``archive_policy``. The actor is always the logged-in
session user; no request field names an actor, role or lease holder. Every
write needs CSRF, an ``Idempotency-Key`` and an ``If-Match`` version, passes an
explicit per-object check, and runs inside the caller's ``run_once`` receipt.
A stale or concurrent request gets 409 with the authoritative state, so it can
never create a second release (``finalize`` is itself keyed on the exact
binding).

Parity commands ``request_acceptance`` (submit), ``decide_acceptance``
(``accept``, ``return`` = reject) and ``reopen_dictation`` are registered on the shared
``ParityDispatch`` by ``register_commands``. Their ``expected_revision`` is
``ClinicDictation.version_counter + 1`` because parity revisions start at 1.

With an ``AcceptanceLedger`` every accept or return is also stored there. The stored
revision binds the text revision from the payload together with the audio and context
heads the server reads at decision time (the pinned command schema has no fields for
them). ``bound_decision`` hands release only a decision whose three revisions still
match; anything else (older text, moved audio or context, unbound decision) is not an
acceptance. ``acceptance_state`` reports the resulting state, including "Wiedervorlage"; release
refusals carry it. A return is stored even when audio or context is missing or ambiguous
(placeholder ``-``); only an accept needs all three, and a return is never releasable.

``GET .../acceptance-state`` (v8-74, no CSRF) serves P07: 401 anonymous, 403 without the shared
policy action 'view', and the reject reason only for a viewer. A text save that moves the head of an
accepted dictation puts it back to "Abzunehmen" (correction_transaction), so the new head can be accepted.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable, Collection, Mapping, Sequence
from datetime import date, datetime, time, timezone
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

from flask import Blueprint, Response, abort, jsonify, request, session
from flask_login import current_user
from flask_wtf.csrf import CSRFError, validate_csrf
from werkzeug.exceptions import HTTPException
from wtforms.validators import ValidationError

from src.clinic import acceptance_rules, archive_policy, lease, live_session, policy
from src.clinic import pools, recipient_defaults as rd, recognition_state, revisions, workflow
from src.clinic import workflow_actions, workflow_finalize
from src.clinic import workflow_metadata
from src.clinic.acceptance_policy import SCOPE_DICTATION, AcceptanceLedger, AcceptancePolicyVersion, PolicyError
from src.clinic.config import clinic_mode_enabled
from src.clinic.dictation_roles import display_name
from src.clinic.routes_categories import CategoryError, check_submission, selection_notes
from src.clinic.metadata_changes import ChangeError
from src.clinic.notify_enqueue import record_notice
from src.clinic.models import (
    ClinicAudioRevision, ClinicContextRevision, ClinicDictation, ClinicRelease, ClinicReviewLease,
)
from src.clinic.parity_dispatch import JSON, CommandProblem, ParityDispatch, RunOnce
from src.clinic.pool_policy import author_pool_ids
from src.clinic.release import _heads as release_heads, current_audio_id
from src.database import db
from src.models import Group, Recording, User
from src.services import audit

PATH = '/api/clinic/v1/dictations/<uuid:dictation_id>'
PERM_METADATA = 'clinic.metadata'
PERM_ARCHIVE = 'clinic.archive'
PERM_SKIP_ASR = 'review'
# Shared permission name whose policy actions are exactly ('view',) in the app's table.
PERM_VIEW = 'history'
Authorize = Callable[[int, str, ClinicDictation], bool]
_CONFLICT = frozenset({'stale_version', 'stale_revision'})
_SEND_CONFLICTS = {
    'not_draft': 'Nur neu angelegte Diktate können eingereicht werden.',
    'audio_required': 'Keine Audiorevision vorhanden.',
    'no_staff_pool': 'Kein Schreibkräfte-Pool zugeordnet.',
}
_STATUS = {'forbidden': 403, 'unauthorized': 403, 'not_found': 404, 'clinic_disabled': 404,
           'audit_unavailable': 503, 'recipient_not_eligible': 403, 'choice_disabled': 403,
           'person_not_allowed': 403}
_CATEGORY_STATUS = {
    'invalid_payload': 400, 'unauthorized': 403, 'unknown_category_set': 404,
    'stale_version': 409, 'missing_category': 422, 'invalid_category': 422,
}
_TITLES = {400: 'Ungültige Anfrage', 401: 'Anmeldung erforderlich', 403: 'Zugriff verweigert',
           404: 'Diktat nicht gefunden', 409: 'Versionskonflikt', 422: 'Aktion nicht zulässig',
           423: 'Diktat ist gesperrt', 428: 'Version erforderlich', 503: 'Dienst nicht verfügbar'}
_PARITY = {'forbidden': 'POLICY_DENIED', 'not_found': 'POLICY_DENIED',
           'clinic_disabled': 'CAPABILITY_UNAVAILABLE', 'audit_unavailable': 'DEPENDENCY_UNAVAILABLE'}


def _etag(dictation: ClinicDictation) -> str:
    return f'"{dictation.id}:{dictation.version_counter}"'


def _problem(status: int, code: str | None = None, **extra: Any) -> Response:
    response = jsonify(type='about:blank', title=_TITLES[status], status=status,
                       code=code or {404: 'NOT_FOUND'}.get(status, 'ERROR'),
                       correlation_id=str(uuid4()), **extra)
    response.status_code = status
    response.mimetype = 'application/problem+json'
    response.headers['Cache-Control'] = 'no-store'
    return response


def _state(dictation_id: str) -> JSON:
    """Authoritative state for a conflict body (read after the failed write rolled back)."""
    db.session.rollback()
    row = db.session.get(ClinicDictation, dictation_id, populate_existing=True)
    item = db.session.get(workflow.ClinicWorkflowDictation, dictation_id, populate_existing=True)
    return {'version': row.version_counter, 'etag': _etag(row), 'status': item.status if item else None}


def _generation(dictation_id: str) -> int:
    """Generation of the stored lease; the service still checks the actor holds it."""
    row = db.session.query(ClinicReviewLease).filter_by(dictation_id=dictation_id).first()
    if row is None:
        raise lease.LeaseConflict(lease.NOT_FOUND)
    return row.generation


def _result(request_id: str, revision: int) -> JSON:
    return {'request_id': request_id, 'command_id': str(uuid4()), 'status': 'committed',
            'revision': revision}


_SEP = '|'
_STATE_LABELS = {
    'offen': 'Noch keine Entscheidung',
    'angenommen': 'Abgenommen',
    'zurueckgegeben': 'Zurückgegeben',
    'wiedervorlage': 'Wiedervorlage: Text, Audio oder Kontext hat sich nach der Abnahme geändert',
}


_MISSING = '-'


def _leaf_id(model, dictation_id: str) -> str | None:
    """Id of the single leaf revision, else None (none or forked)."""
    leaves = release_heads(model, dictation_id)
    return next(iter(leaves)) if len(leaves) == 1 else None


def _head_ids(dictation: ClinicDictation) -> tuple[str, str, str]:
    """Current text, audio and context revision ids; _MISSING for a missing or ambiguous one."""
    text = revisions.current_revision(dictation)
    return (text.id if text is not None else _MISSING,
            current_audio_id(dictation.id) or _MISSING,
            _leaf_id(ClinicContextRevision, dictation.id) or _MISSING)


def _heads(dictation: ClinicDictation) -> tuple[str, str, str] | None:
    """Current text, audio and context revision ids; None when one is missing or ambiguous."""
    ids = _head_ids(dictation)
    return None if _MISSING in ids else ids


def _latest(ledger: AcceptanceLedger, dictation: ClinicDictation):
    """Last stored decision while its actor may still decide under the current policy."""
    stored = ledger.decisions(SCOPE_DICTATION, dictation.id)
    if not stored:
        return None
    last = stored[-1]
    rule = ledger.current(SCOPE_DICTATION, dictation.id).as_rule()
    if not acceptance_rules.can_decide(rule, last.decision.actor_id, last.actor_pool_ids):
        return None
    return last.decision


def bound_decision(ledger: AcceptanceLedger, dictation: ClinicDictation, audio_id: str,
                   context_id: str) -> acceptance_rules.AcceptanceDecision | None:
    """Latest decision for ``finalize``, only if it was made for exactly this audio and context.

    The returned accept carries the bare text revision id, so ``final_allowed`` still compares
    it with the head. A decision of any other binding, or an unbound one, yields None.
    """
    try:
        last = _latest(ledger, dictation)
    except PolicyError:
        return None
    if last is None:
        return None
    text, sep, rest = last.revision_id.partition(_SEP)
    if not sep:
        return None
    if last.kind == 'accept' and rest != f'{audio_id}{_SEP}{context_id}':
        return None
    return dataclasses.replace(last, revision_id=text)


def acceptance_state(ledger: AcceptanceLedger, dictation: ClinicDictation) -> JSON:
    """Where the acceptance of the current revisions stands (German label for the UI)."""
    heads = _head_ids(dictation)
    try:
        last = _latest(ledger, dictation)
    except PolicyError:
        last = None
    state = 'offen'
    if last is not None:
        bound = tuple(last.revision_id.split(_SEP))
        same = bound == heads
        if last.kind == 'accept':
            state = 'angenommen' if same else 'wiedervorlage'
        elif same:
            state = 'zurueckgegeben'
    keys = ('text_revision_id', 'audio_revision_id', 'context_revision_id')
    return {'state': state, 'label': _STATE_LABELS[state],
            'current': dict(zip(keys, heads)) if _MISSING not in heads else None}


def register_commands(
    dispatch: ParityDispatch, authorize: Authorize,
    acceptance_policy: Callable[[ClinicDictation], AcceptancePolicyVersion],
    ledger: AcceptanceLedger | None = None,
) -> None:
    """Bind the acceptance and reopen parity commands to ``workflow_actions``.

    With ``ledger``, an accept or return is stored there bound to text, audio and context.
    """

    def bind(command: str, action_for: Callable[[JSON], str | None]) -> None:
        def gate(actor: int, permission: str, payload: JSON) -> str | None:
            row = db.session.get(ClinicDictation, payload['dictation_id'])
            return row.id if row is not None and authorize(actor, permission, row) is True else None

        def revision(target: str) -> int:
            row = db.session.get(ClinicDictation, target, populate_existing=True)
            return row.version_counter + 1

        def execute(actor: int, target: str, envelope: JSON) -> JSON:
            payload = envelope['payload']
            action = action_for(payload)
            if action is None:
                raise CommandProblem('CAPABILITY_UNAVAILABLE')
            row = db.session.get(ClinicDictation, target, populate_existing=True)
            if action == 'submit':
                try:
                    rule = acceptance_policy(row)
                except PolicyError as error:
                    raise CommandProblem('POLICY_DENIED') from error
                if payload['acceptance_rule_id'] != rule.rule_id:
                    raise CommandProblem('REVISION_CONFLICT')
                current = _heads(row)
                if current is None or current[2] != payload.get('context_revision_id'):
                    raise CommandProblem('REVISION_CONFLICT')
            context = payload.get('context_revision_id')
            if context is not None:
                bound = db.session.get(ClinicContextRevision, context)
                if bound is None or bound.dictation_id != target:
                    raise CommandProblem('INVALID_PAYLOAD')
            heads = None
            if ledger is not None and action in ('accept', 'reject'):
                row = db.session.get(ClinicDictation, target, populate_existing=True)
                heads = _heads(row)
                if heads is None and action == 'accept':
                    raise CommandProblem('INVALID_PAYLOAD')
                if heads is None:
                    heads = _head_ids(row)  # a return is stored even without audio or context
            try:
                done = workflow_actions.apply_action(
                    target, action, actor_user_id=actor, expected_version=envelope['expected_revision'] - 1,
                    lease_generation=_generation(target), now=datetime.now(timezone.utc),
                    authorize=authorize, text_revision_id=payload.get('text_revision_id'),
                    reason=payload.get('reason'), acceptance_policy=acceptance_policy)
            except workflow.WorkflowError as error:
                code = str(error)
                raise CommandProblem('REVISION_CONFLICT' if code in _CONFLICT
                                     else _PARITY.get(code, 'INVALID_PAYLOAD')) from error
            except acceptance_rules.AcceptanceError as error:
                raise CommandProblem('POLICY_DENIED' if error.code == 'forbidden'
                                     else 'INVALID_PAYLOAD') from error
            except (lease.LeaseConflict, live_session.LiveSessionError) as error:
                raise CommandProblem('REVISION_CONFLICT') from error
            if heads is not None:
                decided = done.acceptance or done.rejection
                try:
                    ledger.decide_acceptance(
                        SCOPE_DICTATION, target, kind=decided.kind, actor_id=actor,
                        actor_pool_ids=author_pool_ids(actor), revision_id=_SEP.join(heads),
                        reason=payload.get('reason'), previous_assignee_id=decided.handler_id,
                        now=decided.at)
                except (PolicyError, acceptance_rules.AcceptanceError) as error:
                    raise CommandProblem('POLICY_DENIED') from error
            result = _result(envelope['request_id'], done.version + 1)
            event = {'submit': 'acceptance_requested', 'reject': 'acceptance_rejected',
                     'accept': 'accepted'}.get(action)
            if event:
                record_notice(event, db.session.get(ClinicDictation, target), actor,
                              done.version, result['command_id'])
            return result

        dispatch.register(command, authorize=gate, revision=revision, execute=execute)

    bind('request_acceptance', lambda _payload: 'submit')
    bind('decide_acceptance', lambda payload: {'accept': 'accept', 'return': 'reject'}.get(payload['decision']))
    bind('reopen_dictation', lambda _payload: 'reopen')


def _fields(raw: Any) -> dict[str, Any]:
    """JSON metadata fields to service types. Unknown keys reach the service and are refused."""
    if not isinstance(raw, dict) or not raw:
        abort(400)
    out = dict(raw)
    try:
        if isinstance(out.get('deadline_date'), str):
            out['deadline_date'] = date.fromisoformat(out['deadline_date'])
        if isinstance(out.get('deadline_time'), str):
            out['deadline_time'] = time.fromisoformat(out['deadline_time'])
    except ValueError:
        abort(400)
    return out


def create_blueprint(
    *, authorize: Authorize, run_once: RunOnce,
    allowed_actions: Callable[[int, ClinicDictation], Collection[str]],
    record_audit: Callable[[Sequence[Mapping]], None],
    trusted_context: Callable, open_review_flags: Callable,
    acceptance_policy: Callable[[ClinicDictation], AcceptancePolicyVersion],
    job_type: str, book: archive_policy.ArchiveBook,
    acceptance_decision: Callable[[ClinicDictation, str, str], Any] | None = None,
    acceptance_ledger: AcceptanceLedger | None = None,
) -> Blueprint:
    """Mount with the central policy callbacks; ``allowed_actions`` lists rename/priority/reassign.

    ``acceptance_decision(dictation, audio_revision_id, context_revision_id)`` supplies the
    decision bound to the release's audio and context (see ``bound_decision``).
    ``acceptance_ledger`` adds the acceptance state to release refusals.
    """
    bp = Blueprint('clinic_workflow', __name__)

    @bp.before_request
    def authenticate() -> None:
        if not clinic_mode_enabled():
            abort(404)
        if not (current_user.is_authenticated and current_user.is_active
                and session.get('_user_id') == current_user.get_id()):
            abort(401)
        if request.method in ('GET', 'HEAD', 'OPTIONS'):
            return
        try:
            validate_csrf(request.headers.get('X-CSRFToken') or request.headers.get('X-CSRF-Token'))
        except ValidationError:
            abort(403)

    @bp.errorhandler(HTTPException)
    def http_problem(error: HTTPException):
        status = 403 if isinstance(error, CSRFError) else error.code
        return _problem(status) if status in _TITLES else error

    @bp.after_request
    def no_store(response: Response) -> Response:
        response.headers['Cache-Control'] = 'no-store'
        return response

    def _acceptance_extra(target: str, code: str) -> JSON:
        """Release refusals caused by moved revisions carry the Wiedervorlage state."""
        if acceptance_ledger is None or code not in ('stale_revision', 'acceptance_required'):
            return {}
        db.session.rollback()
        row = db.session.get(ClinicDictation, target, populate_existing=True)
        return {'acceptance': acceptance_state(acceptance_ledger, row)} if row else {}

    def command(dictation_id: UUID, name: str, permission: str, body: Any,
                apply: Callable[[ClinicDictation, int, str, datetime], JSON], *, created: int = 200,
                author_only: bool = False):
        target, actor = str(dictation_id), current_user.id
        lock_before_receipt = author_only or name == 'skip_asr'
        # Serialize first archive registration and guarded workflow writes with their receipts on SQLite.
        if lock_before_receipt or name == 'archive':
            connection = db.session.connection()
            if connection.dialect.name == 'sqlite' and not connection.connection.driver_connection.in_transaction:
                connection.exec_driver_sql('BEGIN IMMEDIATE')
        row = db.session.get(ClinicDictation, target, with_for_update=lock_before_receipt,
                             populate_existing=lock_before_receipt)
        if author_only and (row is None or row.author_user_id != actor):
            abort(403)
        if row is None or authorize(actor, permission, row) is not True:
            abort(403 if author_only else 404)
        try:
            key = str(UUID(request.headers.get('Idempotency-Key', '')))
        except ValueError:
            abort(400)
        match = request.headers.get('If-Match')
        if match is None:
            abort(428)
        prefix, _, number = match.strip('"').rpartition(':')
        if prefix != target or not number.isdigit():
            abort(400)
        expected = int(number)
        digest = sha256(json.dumps([name, expected, body], sort_keys=True, default=str).encode()).hexdigest()

        def operation() -> JSON:
            if lock_before_receipt:
                return apply(row, expected, key, datetime.now(timezone.utc))
            locked = db.session.get(ClinicDictation, target, populate_existing=True, with_for_update=True)
            return apply(locked, expected, key, datetime.now(timezone.utc))

        try:
            result = run_once((actor, name, target, key), digest, operation)
        except CommandProblem as error:
            if str(error) == 'IDEMPOTENCY_CONFLICT':
                return _problem(409, 'IDEMPOTENCY_CONFLICT')
            if str(error) == 'DEPENDENCY_UNAVAILABLE':
                return _problem(503, 'DEPENDENCY_UNAVAILABLE')
            raise
        except (workflow.WorkflowError, ChangeError, archive_policy.ArchivePolicyError) as error:
            code = getattr(error, 'code', None) or str(error)
            if code in _SEND_CONFLICTS:
                return _problem(409, code.upper(), detail=_SEND_CONFLICTS[code], state=_state(target))
            if code in _CONFLICT or code.startswith('lease_'):
                return _problem(409, code.upper(), state=_state(target),
                                **_acceptance_extra(target, code))
            return _problem(_STATUS.get(code, 422), code.upper(), **_acceptance_extra(target, code))
        except CategoryError as error:
            return _problem(_CATEGORY_STATUS.get(error.code, 400), error.code, **error.extra)
        except (lease.LeaseConflict, live_session.LiveSessionError):
            return _problem(409, 'LEASE_CONFLICT', state=_state(target))
        response = jsonify(result)
        response.status_code = created if not result.get('replayed') else 200
        return response

    @bp.get(f'{PATH}/acceptance-state')
    def read_acceptance_state(dictation_id: UUID):
        """Acceptance state for P07 (v8-74). Access is the shared policy action 'view'."""
        if acceptance_ledger is None:
            abort(404)
        row = db.session.get(ClinicDictation, str(dictation_id))
        if row is None:
            abort(404)
        if authorize(current_user.id, PERM_VIEW, row) is not True:
            abort(403)
        out = acceptance_state(acceptance_ledger, row)
        if out['state'] == 'zurueckgegeben':
            # The reason is free text typed by the approver: only for users who may view the text.
            try:
                last = _latest(acceptance_ledger, row)
            except PolicyError:
                last = None
            if last is not None and last.reason:
                out['reject_reason'] = last.reason
        return jsonify(out)

    @bp.post(f'{PATH}/metadata')
    def metadata(dictation_id: UUID):
        body = request.get_json(silent=True)
        requested = _fields(body.get('fields') if isinstance(body, dict) else None)

        def apply(row, expected, _key, now):
            actions = allowed_actions(current_user.id, row)
            applied = workflow_metadata.apply_metadata(
                dictation_id=row.id, workflow_id=row.id, actor_user_id=current_user.id,
                expected_version=expected, lease_generation=_generation(row.id),
                requested=requested, allowed_actions=actions, now=now)
            record_audit(applied.audit_rows)
            return {'dictation_id': row.id, 'version': applied.version,
                    'etag': f'"{row.id}:{applied.version}"', 'deferred': dict(applied.deferred)}

        return command(dictation_id, 'metadata', PERM_METADATA, body, apply)

    @bp.post(f'{PATH}/releases')
    def release(dictation_id: UUID):
        body = request.get_json(silent=True)
        keys = {'lease_id', 'lease_generation', 'original_version_id', 'transcript_revision_id',
                'context_revision_id', 'document_type', 'mode', 'text_sha256',
                'confirmed_content', 'confirmed_context'}
        if (not isinstance(body, dict) or body.keys() != keys
                or body['confirmed_content'] is not True or body['confirmed_context'] is not True
                or body['mode'] != 'PLATFORM_REVIEWED_KIS_DRAFT'
                or body['document_type'] not in ('progress_note', 'report', 'letter')
                or type(body['lease_generation']) is not int):
            abort(400)

        def apply(row, expected, _key, now):
            held = lease.assert_current(row.id, current_user.id, body['lease_generation'], now)
            if held.id != body['lease_id']:
                raise workflow.WorkflowError('stale_version')
            text = db.session.get(workflow_finalize.ClinicTranscriptRevision, body['transcript_revision_id'])
            if text is not None and text.normalised_text_hash != body['text_sha256']:
                raise workflow.WorkflowError('stale_revision')
            done = workflow_finalize.finalize(
                row.id, actor_user_id=current_user.id, expected_version=expected,
                lease_generation=body['lease_generation'], now=now, authorize=authorize,
                audio_revision_id=body['original_version_id'],
                transcript_revision_id=body['transcript_revision_id'],
                context_revision_id=body['context_revision_id'], document_type=body['document_type'],
                job_type=job_type, trusted_context=trusted_context, open_review_flags=open_review_flags,
                acceptance_policy=acceptance_policy,
                acceptance_decision=acceptance_decision(
                    row, body['original_version_id'], body['context_revision_id'])
                if acceptance_decision else None)
            stored = db.session.get(ClinicRelease, done.release_id)
            record_notice('completed', row, current_user.id, done.version, stored.id)
            return {'release_id': stored.id, 'dictation_id': row.id,
                    'original_version_id': stored.audio_revision_id,
                    'transcript_revision_id': stored.transcript_revision_id,
                    'context_revision_id': stored.context_revision_id,
                    'document_type': body['document_type'], 'mode': body['mode'],
                    'payload_sha256': stored.text_hash,
                    'created_at': stored.created_at.astimezone(timezone.utc).isoformat()
                    if stored.created_at.tzinfo else stored.created_at.replace(tzinfo=timezone.utc).isoformat(),
                    'etag': f'"{row.id}:{done.version}"', 'job_id': done.job_id,
                    'replayed': done.replayed}

        return command(dictation_id, 'release', workflow_finalize.PERMISSION, body, apply, created=201)

    @bp.get(f'{PATH}/send-preview')
    def send_preview(dictation_id: UUID):
        actor = current_user.id
        row = db.session.get(ClinicDictation, str(dictation_id))
        if (row is None or row.author_user_id != actor or authorize(actor, 'clinic.dictate', row) is not True
                or row.pool_id not in author_pool_ids(actor)):
            abort(403)
        item = db.session.get(workflow.ClinicWorkflowDictation, row.id)
        if item is None or item.status != workflow.DRAFT:
            return _problem(409, 'NOT_DRAFT')
        if item.pool_id != row.pool_id:
            abort(403)
        if db.session.query(ClinicAudioRevision.id).filter_by(dictation_id=row.id).first() is None:
            return _problem(409, 'AUDIO_REQUIRED')

        default = rd.get_default(actor)
        version = 0 if default is None else default.version
        choice = rd.effective_choice(default, rd.get_choice_grant(actor))
        default_target = None
        try:
            resolved = rd.submit(actor, row.pool_id, actor_id=actor, expected_version=version)
            if resolved.mode == 'self_correction' or pools.routable_staff_pool_ids(row.pool_id):
                default_target = workflow.routing_target_label(resolved)
        except rd.VersionConflict:
            return _problem(409, 'STALE_VERSION')
        except rd.RecipientDefaultError as error:
            if error.code not in ('recipient_not_eligible', 'person_not_allowed', 'choice_disabled'):
                raise

        options = []
        if choice != rd.CHOICE_DEFAULT_ONLY:
            available = rd.selectable_overrides(actor, row.pool_id)
            people = User.query.filter(User.id.in_([int(uid) for uid in available['persons']])).all()
            teams = (db.session.query(pools.ClinicStaffPool.id, Group.name)
                     .join(pools.ClinicStaffPool.group)
                     .filter(pools.ClinicStaffPool.id.in_(available['staff_pools'])).all())
            options.extend({'mode': 'person', 'target_ids': [str(person.id)], 'label': display_name(person)}
                           for person in people)
            options.extend({'mode': 'staff_pool', 'target_ids': [team_id], 'label': name}
                           for team_id, name in teams)
            if choice == rd.CHOICE_OPEN:
                if teams:
                    everyone = workflow.RoutingTarget('all_eligible', (), tuple(p.id for p in people), version)
                    options.append({'mode': everyone.mode, 'target_ids': [], 'label': workflow.routing_target_label(everyone)})
                own = workflow.RoutingTarget('self_correction', (), (actor,), version)
                options.append({'mode': own.mode, 'target_ids': [], 'label': workflow.routing_target_label(own)})
            options = sorted((option for option in options if option['label']), key=lambda option: option['label'].casefold())
            if default_target is not None:
                options = [option for option in options
                           if (option['mode'], tuple(option['target_ids'])) != (resolved.mode, resolved.target_ids)]
        return jsonify(dictation_id=row.id, etag=_etag(row), default_version=version,
                       default_label=default_target, options=options)

    @bp.post(f'{PATH}/send')
    def send(dictation_id: UUID):
        body = request.get_json(silent=True)
        if body is None and not request.get_data():
            body = {}
        if not isinstance(body, dict) or body.keys() - {'categories', 'override', 'expected_version'}:
            abort(400)
        selections = body.get('categories', [])
        default_version = body.get('expected_version')
        if 'expected_version' in body and (type(default_version) is not int or default_version < 0):
            abort(400)
        override = body.get('override')
        if 'override' in body:
            if (not isinstance(override, dict) or not isinstance(override.get('mode'), str)
                    or override.keys() - {'mode', 'target_ids'}):
                abort(400)
            targets = override.get('target_ids', [])
            if not isinstance(targets, list):
                abort(400)
            if override['mode'] == 'person' and len(targets) == 1:
                person = targets[0]
                if (not isinstance(person, str) or len(person) > 19 or not person.isascii()
                        or not person.isdecimal() or not 0 < int(person) <= 2**63 - 1):
                    abort(400)

        def apply(row, expected, _key, now):
            if row.version_counter != expected:
                raise workflow.WorkflowError('stale_version')
            item = db.session.get(workflow.ClinicWorkflowDictation, row.id, with_for_update=True)
            if item is None or item.status != workflow.DRAFT:
                raise workflow.WorkflowError('not_draft')
            if db.session.query(ClinicAudioRevision.id).filter_by(dictation_id=row.id).first() is None:
                raise workflow.WorkflowError('audio_required')
            if item.pool_id != row.pool_id or row.pool_id not in author_pool_ids(current_user.id):
                raise workflow.WorkflowError('forbidden')
            target = workflow.store_routing_target(
                db.session(), row.id, actor_id=current_user.id,
                override=override, expected_version=default_version,
            )
            if target.mode != 'self_correction' and not pools.routable_staff_pool_ids(row.pool_id, now):
                raise workflow.WorkflowError('no_staff_pool')
            check_submission(current_user.id, selections)
            note = selection_notes(current_user.id, selections)
            fields = ['workflow_status', 'workflow_send']
            if note:
                recording = db.session.get(Recording, row.recording_id)
                if recording is None:
                    raise workflow.WorkflowError('not_found')
                previous = (recording.notes or '').rstrip()
                recording.notes = f'{previous}\n{note}' if previous else note
                fields.append('notes')

            workflow.transition(db.session(), row.id, 'send_online')
            settled = recognition_state.settle(db.session(), row.id)
            row.version_counter += 1
            if audit.record_in_tx('recording.edited', recording_id=row.recording_id,
                                  recording_owner_id=row.author_user_id, actor_kind='session',
                                  actor_user_id=current_user.id, force=True,
                                  details={'fields': fields}) is None:
                raise workflow.WorkflowError('audit_unavailable')
            result = {'dictation_id': row.id, 'status': settled.status,
                      'version': row.version_counter, 'etag': _etag(row)}
            if default_version is not None:
                result['routing_target'] = workflow.routing_public(item, named=True)
            return result

        return command(dictation_id, 'send', 'clinic.dictate', body, apply, author_only=True)

    @bp.post(f'{PATH}/asr/skip')
    def skip_asr(dictation_id: UUID) -> Response:
        body = request.get_json(silent=True)
        if body != {}:
            abort(400)

        def apply(row, expected, _key, now):
            if row.version_counter != expected:
                raise workflow.WorkflowError('stale_version')
            item = recognition_state.skip_wait(db.session(), row, now)
            row.version_counter += 1
            if audit.record_in_tx('recording.edited', recording_id=row.recording_id,
                                  recording_owner_id=row.author_user_id, actor_kind='session',
                                  actor_user_id=current_user.id, force=True,
                                  details={'fields': ['workflow_status', 'asr_skip']}) is None:
                raise workflow.WorkflowError('audit_unavailable')
            return {'dictation_id': row.id, 'status': item.status,
                    'version': row.version_counter, 'etag': _etag(row)}

        return command(dictation_id, 'skip_asr', PERM_SKIP_ASR, body, apply)

    @bp.post(f'{PATH}/archive')
    def archive(dictation_id: UUID):
        body = request.get_json(silent=True)
        if (not isinstance(body, dict) or body.keys() != {'action', 'confirm'}
                or body['action'] not in (archive_policy.ARCHIVE, archive_policy.DELETE)
                or type(body['confirm']) is not bool):
            abort(400)

        def apply(row, expected, key, now):
            recording = db.session.get(Recording, row.recording_id)
            try:
                done = book.apply(
                    db.session(), row.id, action=body['action'],
                    subject=policy.build_subject(current_user.id),
                    facts=policy.build_facts(recording.id, recording.user_id),
                    expected_version=expected, request_id=key, now=now,
                    confirm=body['confirm'], recording=recording, dictation=row)
            except archive_policy.StaleVersion as stale:
                raise workflow.WorkflowError('stale_version') from stale
            if done.applied:
                row.version_counter += 1
            return {'dictation_id': row.id, 'requested': done.requested, 'status': done.status,
                    'version': done.version, 'applied': done.applied,
                    'retention_changed': done.retention_changed,
                    'dry_run': {'eligible': done.dry_run.eligible, 'reason': done.dry_run.reason,
                                'legal_hold': done.dry_run.legal_hold,
                                'would_delete': done.dry_run.would_delete},
                    'etag': f'"{row.id}:{done.version}"'}

        permission = PERM_ARCHIVE if body['action'] == archive_policy.ARCHIVE else 'clinic.delete'
        return command(dictation_id, 'archive', permission, body, apply)

    return bp
