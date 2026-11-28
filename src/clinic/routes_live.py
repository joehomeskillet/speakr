"""HTTP and parity-command adapters for the live dictation session (PAR-LIVE-001/003/004).

Thin layer over ``live_session`` (holder, speech lock, receipts),
``live_phrases`` (durable final phrases) and ``live_drafts`` (draft switch).
Start and stop use the public parity command contract through
``ParityDispatch``. Pause, continue, phrase append and resume are
browser-session POSTs under ``PREFIX`` with the same guards: CLINIC_MODE, an
authenticated session, a CSRF token and an explicit per-dictation policy
callback.

The actor always comes from the session, never from the request. A body with
an unknown field is refused. Only a phrase that is durably stored returns
``server_saved``. Resume returns the stored generation and sequence plus at
most ``MAX_EVENTS`` phrase envelopes after the client's last known sequence.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Callable, Set
from datetime import datetime, timezone
from uuid import uuid4

from flask import Blueprint, abort, jsonify, request, session
from flask_login import current_user
from flask_wtf.csrf import validate_csrf
from wtforms.validators import ValidationError

from src.clinic import live_phrases, live_session, vocabulary
from src.clinic.config import clinic_mode_enabled
from src.clinic.draft_registry import DraftError
from src.clinic.draft_store import DraftStore
from src.clinic.ingest import IngestService
from src.clinic.live_drafts import HolderFence, LiveDrafts, select_draft
from src.clinic.models import ClinicDictation, ClinicTranscriptRevision
from src.clinic.parity_dispatch import MAX_BODY_BYTES, CommandProblem, ParityDispatch, _problem
from src.database import db

PREFIX = "/api/clinic/v1/live"
MAX_EVENTS = 50

Authorize = Callable[[int, str], bool]
Clock = Callable[[], datetime]

_LIVE_PROBLEMS = {
    live_session.ACTIVE_SESSION: "REVISION_CONFLICT",
    live_session.EXPIRED: "REVISION_CONFLICT",
    live_session.ENDED_SESSION: "REVISION_CONFLICT",
    live_session.STALE_RECEIPT: "REVISION_CONFLICT",
    live_session.NO_SESSION: "REVISION_CONFLICT",
    live_session.SPEECH_LOCK_HELD: "POLICY_DENIED",
    live_session.NOT_HOLDER: "POLICY_DENIED",
    live_session.NOT_FOUND: "POLICY_DENIED",
}
_PHRASE_PROBLEMS = {
    "stale_sequence": "REVISION_CONFLICT", "sequence_conflict": "REVISION_CONFLICT",
    "event_conflict": "REVISION_CONFLICT", "stale_version": "REVISION_CONFLICT",
    "storage_missing": "DEPENDENCY_UNAVAILABLE",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _code(error: Exception) -> str:
    if isinstance(error, live_session.LiveSessionError):
        return _LIVE_PROBLEMS.get(error.reason, "INVALID_PAYLOAD")
    if isinstance(error, live_phrases.PhraseError):
        return _PHRASE_PROBLEMS.get(error.reason, "INVALID_PAYLOAD")
    if isinstance(error, DraftError):
        return {"unauthorized": "POLICY_DENIED", "not_found": "POLICY_DENIED",
                "stale_version": "REVISION_CONFLICT", "version_conflict": "REVISION_CONFLICT",
                "discarded": "REVISION_CONFLICT",
                }.get(error.code, "INVALID_PAYLOAD")
    return "DEPENDENCY_UNAVAILABLE" if isinstance(error, OSError) else "INVALID_PAYLOAD"


def _position(row: live_session.ClinicLiveSession) -> dict:
    """Generation and sequence the server has durably stored for this session."""
    last = (live_phrases.ClinicLivePhrase.query.filter_by(session_id=row.id)
            .order_by(live_phrases.ClinicLivePhrase.sequence.desc()).first())
    return {"session_id": row.id, "dictation_id": row.dictation_id, "state": row.state,
            "generation": row.holder_generation or (last.generation if last else 1),
            "sequence": row.ack_seq,
            "audio_offset_ms": row.audio_offset_ms}


def _events(session_id: str, after: int) -> list[dict]:
    rows = (live_phrases.ClinicLivePhrase.query
            .filter(live_phrases.ClinicLivePhrase.session_id == session_id,
                    live_phrases.ClinicLivePhrase.sequence > after)
            .order_by(live_phrases.ClinicLivePhrase.sequence).limit(MAX_EVENTS).all())
    return [{"phrase_id": r.id, "generation": r.generation, "sequence": r.sequence,
             "event_id": r.event_id, "audio_start_ms": r.audio_start_ms,
             "audio_end_ms": r.audio_end_ms, "transcript_revision_id": r.transcript_revision_id,
             "status": "server_saved"} for r in rows]


def _capture_session_review(row: live_session.ClinicLiveSession, ended_at: datetime) -> None:
    phrases = (live_phrases.ClinicLivePhrase.query.filter_by(session_id=row.id)
               .order_by(live_phrases.ClinicLivePhrase.sequence).all())
    before_text = after_text = ""
    if phrases:
        first = db.session.get(ClinicTranscriptRevision, phrases[0].transcript_revision_id)
        last = db.session.get(ClinicTranscriptRevision, phrases[-1].transcript_revision_id)
        baseline = (db.session.get(ClinicTranscriptRevision, first.parent_revision_id)
                    if first is not None and first.parent_revision_id else None)
        before_text = baseline.body if baseline is not None else ""
        after_text = last.body if last is not None else ""
    dictation = db.session.get(ClinicDictation, row.dictation_id)
    vocabulary.capture_session_changes(
        user_id=row.author_user_id, dictation_id=row.dictation_id,
        started_at=row.started_at, ended_at=ended_at,
        pool_id=dictation.pool_id if dictation is not None else None,
        before_text=before_text, after_text=after_text,
    )


def _client_id(value: object) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 128 and value == value.strip()


def _valid_fence(data: dict) -> bool:
    return _client_id(data.get("client_id")) and type(data.get("generation")) is int and data["generation"] > 0


def register_live_commands(
    dispatch: ParityDispatch, authorize_dictation: Authorize, now: Clock = _utcnow
) -> None:
    """Bind ``start_live_session`` and ``stop_live_session``. run_once commits."""

    def load(session_id: str):
        return db.session.get(live_session.ClinicLiveSession, session_id)

    def result(envelope: dict, revision: int) -> dict:
        return {"request_id": envelope["request_id"], "command_id": str(uuid4()),
                "status": "committed", "revision": revision}

    def authorize_start(actor: int, permission: str, payload: dict) -> str | None:
        if permission != "clinic.dictate" or not authorize_dictation(actor, payload["dictation_id"]):
            return None
        return "dictation:" + payload["dictation_id"]

    def execute_start(actor: int, target: str, envelope: dict) -> dict:
        try:
            row = live_session.start(target.split(":", 1)[1], actor, now())
        except live_session.LiveSessionError as error:
            raise CommandProblem(_code(error)) from error
        return result(envelope, row.ack_seq + 1)

    def authorize_stop(actor: int, permission: str, payload: dict) -> str | None:
        row = load(payload["session_id"])
        if (permission != "clinic.dictate" or row is None or row.author_user_id != actor
                or not authorize_dictation(actor, row.dictation_id)):
            return None
        return "session:" + row.id

    def stop_revision(target: str) -> int | None:
        row = load(target.split(":", 1)[1])
        return None if row is None else row.ack_seq + 1

    def execute_stop(actor: int, target: str, envelope: dict) -> dict:
        payload = envelope["payload"]
        try:
            live_session.lock_author(actor)
            row = live_session._load(target.split(":", 1)[1])
            position = _position(row)
            if (envelope["expected_revision"] != row.ack_seq + 1
                    or payload["last_sequence"] != position["sequence"]
                    or ((position["sequence"] or row.holder_generation is not None)
                        and payload["generation"] != position["generation"])):
                raise CommandProblem("REVISION_CONFLICT")
            ended_at = now()
            ended = live_session.end(
                row.id, actor, ended_at, client_id=request.headers.get("X-Live-Client-Id"),
                generation=payload["generation"],
            )
            _capture_session_review(ended, ended.ended_at or ended_at)
        except live_session.LiveSessionError as error:
            raise CommandProblem(_code(error)) from error
        return result(envelope, row.ack_seq + 1)

    dispatch.register("start_live_session", authorize=authorize_start,
                      revision=lambda _target: None, execute=execute_start)
    dispatch.register("stop_live_session", authorize=authorize_stop,
                      revision=stop_revision, execute=execute_stop)


def create_blueprint(
    authorize_dictation: Authorize, store: IngestService,
    drafts: LiveDrafts | DraftStore | None = None, now: Clock = _utcnow,
) -> Blueprint:
    """Mount pause, continue, phrase append, resume and draft switch.

    ``authorize_dictation(actor_id, dictation_id)`` is the caller's explicit
    per-object policy. ``store`` is the durable audio root for final phrases.
    Pass a ``DraftStore`` to enable durable draft switching.
    Switch returns the selected session's position. The caller must use its new
    tab id and generation on subsequent live requests.
    """
    blueprint = Blueprint("clinic_live", __name__, url_prefix=PREFIX)

    @blueprint.before_request
    def guard():
        if not clinic_mode_enabled():
            abort(404)
        if not (current_user.is_authenticated and current_user.is_active
                and session.get("_user_id") == current_user.get_id()):
            return _problem("UNAUTHORIZED")
        try:
            validate_csrf(request.headers.get("X-CSRF-Token"))
        except ValidationError:
            return _problem("FORBIDDEN")

    @blueprint.after_request
    def no_store(response):
        response.headers["Cache-Control"] = "private, no-store"
        return response

    def body(required: Set[str], optional: Set[str] = frozenset()) -> dict | None:
        if request.content_length is None or request.content_length > MAX_BODY_BYTES:
            return None
        data = request.get_json(silent=True)
        if (not isinstance(data, dict) or data.keys() - required - optional
                or required - data.keys()):
            return None
        return data

    def owned(session_id: str):
        row = db.session.get(live_session.ClinicLiveSession, session_id)
        if row is None or not authorize_dictation(current_user.id, row.dictation_id):
            return None
        return row

    def run(action):
        try:
            return action()
        except (live_session.LiveSessionError, live_phrases.PhraseError, DraftError, OSError) as error:
            db.session.rollback()
            return _problem(_code(error))

    def transition(session_id: str, method):
        data = body(set(), {"client_id", "generation", "release"} if method is live_session.pause
                    else {"client_id", "generation"})
        if data is None or ("release" in data and type(data["release"]) is not bool):
            return _problem("INVALID_PAYLOAD")
        release = data.pop("release", False)
        if (release or data) and not _valid_fence(data):
            return _problem("INVALID_PAYLOAD")
        if owned(session_id) is None:
            return _problem("POLICY_DENIED")

        def act():
            row = method(session_id, current_user.id, now(), **data,
                         **({"release": True} if release else {}))
            db.session.commit()
            return jsonify(_position(row))
        return run(act)

    @blueprint.post("/sessions/<session_id>/pause")
    def pause(session_id):
        return transition(session_id, live_session.pause)

    @blueprint.post("/sessions/<session_id>/continue")
    def resume_recording(session_id):
        return transition(session_id, live_session.continue_recording)

    @blueprint.post("/sessions/<session_id>/phrases")
    def append_phrase(session_id):
        required = {"generation", "sequence", "event_id", "audio_start_ms", "audio_end_ms",
                    "audio_b64", "audio_revision_id", "transcript_revision_id", "text_hash"}
        data = body(required, {"client_id"})
        if data is None or not isinstance(data["audio_b64"], str):
            return _problem("INVALID_PAYLOAD")
        row = owned(session_id)
        if row is None:
            return _problem("POLICY_DENIED")
        try:
            audio = base64.b64decode(data.pop("audio_b64"), validate=True)
        except (binascii.Error, ValueError):
            return _problem("INVALID_PAYLOAD")

        def act():
            receipt = live_phrases.store_phrase(
                store, dictation_id=row.dictation_id, session_id=session_id,
                user_id=current_user.id, client_id=data.pop("client_id", None), now=now(),
                audio=audio, **data)
            return jsonify({"status": receipt.status, "phrase_id": receipt.phrase_id,
                          "session_id": receipt.session_id, "generation": receipt.generation,
                          "sequence": receipt.sequence, "event_id": receipt.event_id})
        return run(act)

    @blueprint.post("/dictations/<dictation_id>/resume")
    def resume(dictation_id):
        data = body(set(), {"client_id", "generation", "after_sequence"})
        after = None if data is None else data.get("after_sequence", 0)
        if (data is None or type(after) is not int or after < 0
                or (data.get("client_id") is not None and not _client_id(data["client_id"]))
                or ("generation" in data and not _valid_fence(data))):
            return _problem("INVALID_PAYLOAD")
        if not authorize_dictation(current_user.id, dictation_id):
            return _problem("POLICY_DENIED")

        def act():
            row, _point = live_session.recover(
                dictation_id, current_user.id, now(), client_id=data.get("client_id"),
                generation=data.get("generation"))
            db.session.commit()
            return jsonify(dict(_position(row), events=_events(row.id, after), max_events=MAX_EVENTS))
        return run(act)

    @blueprint.post("/drafts/<draft_id>/switch")
    def switch_draft(draft_id):
        if drafts is None:
            return _problem("CAPABILITY_UNAVAILABLE")
        data = body({"tab_id"}, {"acknowledged_holder"})
        if data is None or not _client_id(data["tab_id"]):
            return _problem("INVALID_PAYLOAD")
        ack = data.get("acknowledged_holder")
        if "acknowledged_holder" in data and (
            not isinstance(ack, dict) or ack.keys() != {"session_id", "client_id", "generation"}
            or not _client_id(ack["session_id"]) or not _valid_fence(ack)
        ):
            return _problem("INVALID_PAYLOAD")
        if not authorize_dictation(current_user.id, draft_id):
            return _problem("POLICY_DENIED")

        def act():
            previous = HolderFence(ack["session_id"], ack["client_id"], ack["generation"]) if ack else None
            try:
                draft, row = select_draft(
                    current_user.id, draft_id, data["tab_id"], now(), acknowledged=previous,
                    draft_store=drafts if isinstance(drafts, DraftStore) else None,
                )
            except DraftError as error:
                if not isinstance(drafts, LiveDrafts) or ack is not None or error.code != "not_found":
                    raise
                draft = drafts.switch(current_user.id, draft_id, data["tab_id"], now())
                db.session.commit()
                return jsonify({"draft_id": draft.id, "state": draft.state, "version": draft.version,
                              "holder_token": draft.holder_token})
            result = dict(_position(row), draft_id=draft.id, version=draft.version,
                          holder_token=row.holder_generation)
            db.session.commit()
            return jsonify(result)
        return run(act)

    return blueprint
