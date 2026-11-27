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
import wave
from collections.abc import Callable, Set
from datetime import datetime, timezone
from uuid import uuid4

from flask import Blueprint, abort, jsonify, request, send_file, session, url_for
from flask_login import current_user
from flask_wtf.csrf import validate_csrf
from sqlalchemy import tuple_
from sqlalchemy.orm import aliased
from wtforms.validators import ValidationError

from src.clinic import live_phrases, live_session
from src.clinic.config import clinic_mode_enabled
from src.clinic.draft_registry import DraftError
from src.clinic.draft_store import DraftStore
from src.clinic.ingest import IngestError, IngestService, storage_key_for
from src.clinic.live_drafts import HolderFence, LiveDrafts, select_draft
from src.clinic.models import ClinicDictation, ClinicTranscriptRevision
from src.clinic.parity_dispatch import MAX_BODY_BYTES, CommandProblem, ParityDispatch, _problem
from src.database import db
from src.services import audit
from src.utils.mime import sandbox_media_response

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
    last = (live_phrases.ClinicLivePhrase.query.filter_by(session_id=row.id, dictation_id=row.dictation_id)
            .order_by(live_phrases.ClinicLivePhrase.sequence.desc()).first())
    return {"session_id": row.id, "dictation_id": row.dictation_id, "state": row.state,
            "generation": row.holder_generation or (last.generation if last else 1),
            "sequence": row.ack_seq,
            "audio_offset_ms": row.audio_offset_ms}


def _events(row: live_session.ClinicLiveSession, after: int, may_play: bool) -> dict:
    events = []
    cursor = None
    if after < row.ack_seq:
        phrase = live_phrases.ClinicLivePhrase
        revision = ClinicTranscriptRevision
        parent = aliased(revision)
        rows = (db.session.query(phrase, revision.body, parent.body).select_from(phrase)
                .join(revision, (revision.id == phrase.transcript_revision_id)
                      & (revision.dictation_id == phrase.dictation_id))
                .outerjoin(parent, (parent.id == revision.parent_revision_id)
                           & (parent.dictation_id == revision.dictation_id))
                .filter(phrase.session_id == row.id, phrase.dictation_id == row.dictation_id,
                        phrase.sequence > after, phrase.sequence <= row.ack_seq)
                .order_by(phrase.sequence).limit(MAX_EVENTS + 1).all())
        for clip, text, prior_text in rows[:MAX_EVENTS]:
            events.append({
                "phrase_id": clip.id, "generation": clip.generation, "sequence": clip.sequence,
                "event_id": clip.event_id, "audio_start_ms": clip.audio_start_ms,
                "audio_end_ms": clip.audio_end_ms, "transcript_revision_id": clip.transcript_revision_id,
                "audio_revision_id": clip.audio_revision_id, "status": "server_saved",
                "text": text.removeprefix(prior_text + "\n") if prior_text else text,
                "audio_url": url_for(".phrase_audio", dictation_id=row.dictation_id,
                                     phrase_id=clip.id) if may_play else None,
            })
        if len(rows) > MAX_EVENTS:
            cursor = {"session_id": row.id, "after_sequence": events[-1]["sequence"]}
    return {"events": events, "next_cursor": cursor, "max_events": MAX_EVENTS}


def _history(dictation_id: str):
    row = live_session.ClinicLiveSession
    return row.query.filter_by(dictation_id=dictation_id, state=live_session.ENDED)


def _previous_session(row: live_session.ClinicLiveSession) -> str | None:
    model = live_session.ClinicLiveSession
    previous = (_history(row.dictation_id)
                .filter(tuple_(model.started_at, model.created_at, model.id)
                        < (row.started_at, row.created_at, row.id))
                .order_by(model.started_at.desc(), model.created_at.desc(), model.id.desc()).first())
    return previous.id if previous else None


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
            live_session.end(row.id, actor, now(), client_id=request.headers.get("X-Live-Client-Id"),
                             generation=payload["generation"])
        except live_session.LiveSessionError as error:
            raise CommandProblem(_code(error)) from error
        return result(envelope, row.ack_seq + 1)

    dispatch.register("start_live_session", authorize=authorize_start,
                      revision=lambda _target: None, execute=execute_start)
    dispatch.register("stop_live_session", authorize=authorize_stop,
                      revision=stop_revision, execute=execute_stop)


def create_blueprint(
    authorize_dictation: Authorize, store: IngestService,
    drafts: LiveDrafts | DraftStore | None = None, now: Clock = _utcnow, *,
    authorize_view: Authorize | None = None, authorize_play: Authorize | None = None,
) -> Blueprint:
    """Mount pause, continue, phrase append, resume and draft switch.

    ``authorize_dictation`` checks edit rights. Recovery reads use ``authorize_view``
    (defaulting to the edit policy) and playback requires an explicit ``authorize_play``.
    ``store`` is the durable audio root for final phrases.
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
        if request.method in {"GET", "HEAD"}:
            return None
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
        data = body(set(), {"client_id", "generation", "after_sequence", "session_id", "cursor"})
        if data is None:
            return _problem("INVALID_PAYLOAD")
        if "cursor" in data:
            cursor = data["cursor"]
            if (data.keys() != {"cursor"} or not isinstance(cursor, dict)
                    or cursor.keys() != {"session_id", "after_sequence"}):
                return _problem("INVALID_PAYLOAD")
            data = cursor
        after = data.get("after_sequence", 0)
        session_id = data.get("session_id")
        if (type(after) is not int or after < 0
                or ("session_id" in data and (not _client_id(session_id)
                                              or data.keys() - {"session_id", "after_sequence"}))
                or (data.get("client_id") is not None and not _client_id(data["client_id"]))
                or ("generation" in data and not _valid_fence(data))):
            return _problem("INVALID_PAYLOAD")
        actor = current_user.id
        if not (authorize_view or authorize_dictation)(actor, dictation_id):
            return _problem("POLICY_DENIED")

        model = live_session.ClinicLiveSession

        def page(row, read_only):
            may_play = authorize_play is not None and authorize_play(actor, dictation_id)
            result = dict(_position(row), **_events(row, after, may_play), read_only=read_only,
                          previous_session_id=_previous_session(row))
            if result["events"]:
                record_read(dictation_id, "live_phrase_text")
            return jsonify(result)

        if session_id is not None:
            row = model.query.filter_by(id=session_id, dictation_id=dictation_id).first()
            return page(row, True) if row is not None else _problem("POLICY_DENIED")

        def latest_ended():
            row = (_history(dictation_id)
                   .order_by(model.started_at.desc(), model.created_at.desc(), model.id.desc()).first())
            return page(row, True) if row is not None else _problem("REVISION_CONFLICT")

        active = model.query.filter_by(active_key=dictation_id).first()
        if active is None:
            return latest_ended()
        if not authorize_dictation(actor, dictation_id):
            return page(active, True)

        def act():
            try:
                row, _point = live_session.recover(
                    dictation_id, actor, now(), client_id=data.get("client_id"),
                    generation=data.get("generation"), allow_ended=False)
            except live_session.LiveSessionError as error:
                if error.reason != live_session.NO_SESSION or error.session is not None:
                    raise
                db.session.rollback()
                return latest_ended()
            response = page(row, False)
            db.session.commit()
            return response
        return run(act)

    def record_read(dictation_id: str, part: str) -> None:
        dictation = db.session.get(ClinicDictation, dictation_id)
        if dictation is None:
            abort(404)
        audit.record("recording.content.read", recording_id=dictation.recording_id,
                     actor_kind="session", actor_user_id=current_user.id, details={"part": part})

    @blueprint.get("/dictations/<dictation_id>/phrases/<phrase_id>/audio")
    def phrase_audio(dictation_id, phrase_id):
        if authorize_play is None or not authorize_play(current_user.id, dictation_id):
            abort(404)
        phrase = (live_phrases.ClinicLivePhrase.query.filter_by(id=phrase_id, dictation_id=dictation_id)
                  .join(live_session.ClinicLiveSession,
                        (live_session.ClinicLiveSession.id == live_phrases.ClinicLivePhrase.session_id)
                        & (live_session.ClinicLiveSession.dictation_id == dictation_id)).first())
        if phrase is None:
            abort(404)
        key = f"live:{phrase.session_id}:{phrase.sequence}"
        if phrase.audio_storage_key != storage_key_for(key):
            abort(404)
        try:
            path = store.stored_path(idempotency_key=key, declared_sha256=phrase.audio_sha256,
                                     declared_size=phrase.audio_size)
            if path is None:
                abort(404)
            with wave.open(str(path), "rb") as wav:
                if not wav.getnframes():
                    abort(415)
            response = send_file(path, mimetype="audio/wav", conditional=True, etag=False)
        except (wave.Error, EOFError):
            abort(415)
        except (IngestError, OSError):
            abort(404)
        response.headers["X-Content-Type-Options"] = "nosniff"
        record_read(dictation_id, "live_phrase_audio")
        return sandbox_media_response(response)

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
