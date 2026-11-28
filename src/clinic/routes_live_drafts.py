"""Session-owned HTTP operations over the durable live draft store."""

from uuid import UUID

from flask import Blueprint, current_app, jsonify
from flask_login import current_user

from src.clinic import lease, live_session, revisions, workflow
from src.clinic.draft_registry import Draft, DraftConflict, DraftError
from src.clinic.draft_store import DraftStore
from src.clinic.parity_dispatch import _problem
from src.clinic.routes_live import Authorize, Clock, PREFIX, _body, _code, _guard, _no_store, _utcnow
from src.database import db


def _payload(draft: Draft) -> dict:
    return {
        "draft_id": draft.id,
        "title": draft.title,
        "text": draft.text,
        "version": draft.version,
        "created_at": draft.created_at.isoformat(),
        "updated_at": draft.updated_at.isoformat(),
        "discarded_at": draft.discarded_at.isoformat() if draft.discarded_at else None,
    }


def _text_input(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _valid_version(value: object) -> bool:
    return type(value) is int and 0 < value < 2**31


def create_blueprint(drafts: DraftStore, authorize: Authorize, now: Clock = _utcnow) -> Blueprint:
    bp = Blueprint("clinic_live_drafts", __name__, url_prefix=PREFIX + "/drafts")
    bp.before_request(_guard)
    bp.after_request(_no_store)

    @bp.errorhandler(DraftError)
    @bp.errorhandler(live_session.LiveSessionError)
    @bp.errorhandler(revisions.RevisionError)
    def problem(error):
        db.session.rollback()
        code = _code(error)
        if isinstance(error, DraftError) and error.code in ("duplicate_id", "original_required", "not_open_draft"):
            code = "REVISION_CONFLICT"
        response, status = _problem(code)
        if isinstance(error, DraftConflict):
            response.set_data(current_app.json.dumps(dict(
                response.json, current_version=error.current_version, current_text=error.current_text,
            )))
        return response, status

    def writable(draft_id: str, moment):
        actor = current_user.id
        if not authorize(actor, draft_id):
            raise DraftError("not_found")
        # Same lock order as draft switching and phrase acknowledgement.
        live_session.lock_author(actor)
        dictation = lease._lock_dictation(draft_id)
        if dictation is None or dictation.author_user_id != actor:
            raise DraftError("not_found")
        flow = db.session.get(workflow.ClinicWorkflowDictation, draft_id,
                              populate_existing=True, with_for_update=True)
        if flow is not None and flow.status != workflow.DRAFT:
            raise DraftError("not_open_draft")
        review = lease.current(draft_id)
        if review is not None and review.expires_at > moment and review.holder_user_id != actor:
            raise DraftError("unauthorized")
        live_session.assert_edit_allowed(draft_id, actor, moment)

    @bp.get("")
    def list_drafts():
        actor = current_user.id
        return jsonify(drafts=[_payload(draft) for draft in drafts.list(actor)
                               if authorize(actor, draft.id)])

    @bp.post("")
    def create_draft():
        data = _body({"dictation_id", "title"})
        if data is None or not isinstance(data["dictation_id"], str) or not _text_input(data["title"]):
            return _problem("INVALID_PAYLOAD")
        try:
            draft_id = str(UUID(data["dictation_id"]))
        except ValueError:
            return _problem("INVALID_PAYLOAD")
        moment = now()
        writable(draft_id, moment)
        return jsonify(_payload(drafts.create(current_user.id, draft_id, data["title"], moment))), 201

    @bp.get("/<uuid:draft_id>")
    def get_draft(draft_id: UUID):
        key = str(draft_id)
        if not authorize(current_user.id, key):
            return _problem("POLICY_DENIED")
        return jsonify(_payload(drafts.get(current_user.id, key)))

    @bp.put("/<uuid:draft_id>")
    def save_draft(draft_id: UUID):
        data = _body({"base_version", "text"})
        if data is None or not _valid_version(data["base_version"]) or not _text_input(data["text"]):
            return _problem("INVALID_PAYLOAD")
        key, moment = str(draft_id), now()
        writable(key, moment)
        return jsonify(_payload(drafts.save(current_user.id, key, data["text"], data["base_version"], moment)))

    @bp.post("/<uuid:draft_id>/discard")
    def discard_draft(draft_id: UUID):
        data = _body({"base_version", "confirm"})
        if data is None or not _valid_version(data["base_version"]) or data["confirm"] is not True:
            return _problem("INVALID_PAYLOAD")
        key, moment = str(draft_id), now()
        writable(key, moment)
        row = live_session._open(key)
        if row is not None and row.author_user_id == current_user.id:
            # The store commits the session closure and tombstone together.
            live_session._close(row, moment, live_session.END_FINISHED)
        return jsonify(_payload(drafts.discard(
            current_user.id, key, data["base_version"], confirm=True, now=moment,
        )))

    return bp
