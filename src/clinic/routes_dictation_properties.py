"""HTTP surface for dictation sending properties and optional protection (PAR-REC-006).

A thin layer over ``routes_categories.check_submission`` (required categories),
``workflow`` (priority and deadline rules), ``audio_format_choice`` (source
names), ``pool_policy`` (author-pool routing) and ``dictation_lock`` (optional
protection with lockout and time-limited grants).

The actor is always ``current_user``; a body never names an actor, user or
role. Every write checks CSRF and the session. A validation call is read-only:
nothing is changed unless every property is valid. An unlock grant is bound to
one user and one dictation, so a secret never opens another object, and it
only adds to the object ACL, which ``ProtectionStore.acl_allows`` decides on
every request.

Persistence of the protection record and the ACL stay behind
``ProtectionStore``; mounting the blueprint and wiring a store are left to the
consumer packages (w133, w134, w198).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, time, timezone
from typing import Any, Protocol

from flask import Blueprint, abort, jsonify, request, session
from flask_login import current_user
from flask_wtf.csrf import CSRFError, validate_csrf
from wtforms.validators import ValidationError

from src.clinic import audio_format_choice, dictation_lock, pool_policy, workflow
from src.clinic.config import clinic_mode_enabled
from src.clinic.routes_categories import CategoryError, check_submission

PREFIX = "/api/clinic/v1/dictations"
# Identity and authority come from the session, never from the request.
_FORBIDDEN_KEYS = frozenset({
    "actor", "actor_id", "user", "user_id", "author_user_id", "role", "roles",
    "is_admin", "created_by",
})
_STATUS = {
    "unauthenticated": 401, "unauthorized": 403, "invalid_secret": 403,
    "unknown_dictation": 404, "stale_version": 409, "locked": 429,
    "invalid_payload": 400, "missing_title": 422, "missing_priority": 422,
    "invalid_priority": 422, "invalid_deadline": 422, "deadline_date_required": 422,
    "routing_not_allowed": 422, "missing_routing": 422, "unknown_source": 422,
    "missing_category": 422, "invalid_category": 422,
}
_TITLES = {400: "Ungültige Anfrage", 401: "Anmeldung erforderlich", 403: "Zugriff verweigert",
           404: "Nicht gefunden", 409: "Versionskonflikt", 422: "Angaben unvollständig",
           429: "Zu viele Versuche"}
_CATEGORY_CODES = {"unknown_category_set": 422, "stale_version": 409, "invalid_payload": 400}


class PropertyError(Exception):
    def __init__(self, code: str, **extra: Any) -> None:
        super().__init__(code)
        self.code, self.extra = code, extra


class ProtectionStore(Protocol):
    """Persistence and ACL seam; the consumer package supplies the real one."""

    def acl_allows(self, user_id: int, dictation_id: str) -> bool: ...
    def can_manage(self, user_id: int, dictation_id: str) -> bool: ...
    def get(self, dictation_id: str) -> tuple[dictation_lock.ProtectionRecord | None, int] | None:
        """``(record or None, version)``, or None for an unknown dictation."""
    def put(self, dictation_id: str, record: dictation_lock.ProtectionRecord | None,
            expected_version: int) -> int | None:
        """Store and return the new version, or None when ``expected_version`` is stale."""


def validate_properties(user_id: int, body: Any) -> dict[str, Any]:
    """Raise ``PropertyError``/``CategoryError`` unless every send property is valid.

    Returns the normalised properties; nothing is stored.
    """
    if not isinstance(body, dict) or _FORBIDDEN_KEYS & body.keys():
        raise PropertyError("invalid_payload")
    title = body.get("title")
    if not isinstance(title, str) or not title.strip():
        raise PropertyError("missing_title")
    check_submission(user_id, body.get("categories"))

    pool_id = body.get("pool_id")
    if not isinstance(pool_id, str) or not pool_id:
        raise PropertyError("missing_routing")
    if pool_id not in pool_policy.author_pool_ids(user_id):
        raise PropertyError("routing_not_allowed")

    raw_priority = body.get("priority")
    if raw_priority is None:
        raise PropertyError("missing_priority")
    priority, unrecognized = workflow.normalize_priority(raw_priority)
    if unrecognized:
        raise PropertyError("invalid_priority")

    deadline_at = _deadline(body.get("deadline_date"), body.get("deadline_time"))

    try:
        known = audio_format_choice.source_allowed(audio_format_choice.SOURCES, body.get("source"))
    except audio_format_choice.FormatError:
        known = False
    if not known:
        raise PropertyError("unknown_source")
    return {"title": title.strip(), "pool_id": pool_id, "priority": priority,
            "deadline_at": deadline_at, "source": body["source"]}


def _deadline(raw_date: Any, raw_time: Any) -> datetime | None:
    if raw_date is None and raw_time is None:
        return None
    if raw_date is None:
        raise PropertyError("deadline_date_required")
    try:
        if not isinstance(raw_date, str) or (raw_time is not None and not isinstance(raw_time, str)):
            raise ValueError
        on_date = date.fromisoformat(raw_date)
        at_time = None if raw_time is None else time.fromisoformat(raw_time)
        return workflow.resolve_deadline(on_date, at_time)
    except (ValueError, workflow.WorkflowError):
        raise PropertyError("invalid_deadline") from None


def _problem(code: str, status: int | None = None, **extra: Any):
    status = status or _STATUS.get(code, 400)
    response = jsonify(type="about:blank", title=_TITLES[status], status=status, code=code, **extra)
    response.mimetype = "application/problem+json"
    return response, status


class DictationPropertyRoutes:
    """Build the blueprint around an injected store, lockout tracker and clock."""

    def __init__(
        self,
        store: ProtectionStore,
        tracker: dictation_lock.LockoutTracker | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        grant_seconds: int = 600,
    ) -> None:
        self.store = store
        self.tracker = tracker or dictation_lock.LockoutTracker()
        self.clock = clock
        self.grant_seconds = grant_seconds
        self._grants: dict[tuple[int, str], dictation_lock.UnlockGrant] = {}

    def access_allowed(self, user_id: int, dictation_id: str) -> bool:
        """ACL first, then the unlock grant of this user for this very dictation."""
        loaded = self.store.get(dictation_id)
        if loaded is None:
            return False
        return dictation_lock.decide_access(
            self.store.acl_allows(user_id, dictation_id), loaded[0] is not None,
            self._grants.get((user_id, dictation_id)),
            user_id=user_id, dictation_id=dictation_id, now=self.clock())

    def blueprint(self) -> Blueprint:
        bp = Blueprint("clinic_dictation_properties", __name__)

        @bp.before_request
        def _guard():
            if not clinic_mode_enabled():
                abort(404)
            if not (current_user.is_authenticated and current_user.is_active
                    and session.get("_user_id") == current_user.get_id()):
                return _problem("unauthenticated")
            if request.method != "GET":
                try:
                    validate_csrf(request.headers.get("X-CSRF-Token"))
                except ValidationError:
                    return _problem("unauthorized")

        bp.register_error_handler(CSRFError, lambda _e: _problem("unauthorized"))

        @bp.post(PREFIX + "/properties/validate")
        def validate():
            try:
                props = validate_properties(current_user.id, request.get_json(silent=True))
            except PropertyError as error:
                return _problem(error.code, **error.extra)
            except CategoryError as error:
                return _problem(error.code, _CATEGORY_CODES.get(error.code), **error.extra)
            deadline = props["deadline_at"]
            return jsonify(ok=True, priority=props["priority"], pool_id=props["pool_id"],
                           source=props["source"], title=props["title"],
                           deadline_at=deadline.isoformat() if deadline else None)

        @bp.get(PREFIX + "/<dictation_id>/protection")
        def protection_state(dictation_id: str):
            loaded = self._visible(current_user.id, dictation_id)
            if loaded is None:
                return _problem("unknown_dictation")
            return jsonify(protected=loaded[0] is not None, version=loaded[1],
                           access=self.access_allowed(current_user.id, dictation_id))

        @bp.put(PREFIX + "/<dictation_id>/protection")
        def set_protection(dictation_id: str):
            loaded = self._visible(current_user.id, dictation_id)
            if loaded is None:
                return _problem("unknown_dictation")
            if not self.store.can_manage(current_user.id, dictation_id):
                return _problem("unauthorized")
            body = request.get_json(silent=True)
            if (not isinstance(body, dict) or _FORBIDDEN_KEYS & body.keys()
                    or isinstance(body.get("version"), bool) or not isinstance(body.get("version"), int)
                    or "secret" not in body or not isinstance(body["secret"], (str, type(None)))):
                return _problem("invalid_payload")
            try:
                # A null secret removes the protection; a string sets it.
                record = None if body["secret"] is None else dictation_lock.set_protection(body["secret"])
            except dictation_lock.LockError:
                return _problem("invalid_payload")
            new_version = self.store.put(dictation_id, record, body["version"])
            if new_version is None:
                return _problem("stale_version", current_version=self.store.get(dictation_id)[1])
            self._drop_grants(dictation_id)
            return jsonify(protected=record is not None, version=new_version)

        @bp.post(PREFIX + "/<dictation_id>/unlock")
        def unlock(dictation_id: str):
            loaded = self._visible(current_user.id, dictation_id)
            if loaded is None:
                return _problem("unknown_dictation")
            record = loaded[0]
            body = request.get_json(silent=True)
            if not isinstance(body, dict) or _FORBIDDEN_KEYS & body.keys() or not isinstance(body.get("secret"), str):
                return _problem("invalid_payload")
            if record is None:
                return jsonify(unlocked=True, expires_at=None)
            now, uid = self.clock(), current_user.id
            until = self.tracker.locked_until(uid, dictation_id, now)
            if until is not None:
                return _problem("locked", locked_until=until.isoformat())
            try:
                ok = dictation_lock.verify(record, body["secret"])
            except dictation_lock.LockError:
                return _problem("invalid_payload")
            if not self.tracker.attempt(uid, dictation_id, ok, now):
                until = self.tracker.locked_until(uid, dictation_id, now)
                if until is not None:
                    return _problem("locked", locked_until=until.isoformat())
                return _problem("invalid_secret")
            grant = dictation_lock.grant(uid, dictation_id, now, self.grant_seconds)
            self._grants[(uid, dictation_id)] = grant
            return jsonify(unlocked=True, expires_at=grant.expires_at.isoformat())

        return bp

    def _visible(self, user_id: int, dictation_id: str):
        """Protection state only for a dictation the ACL lets the caller see; else None."""
        loaded = self.store.get(dictation_id)
        if loaded is None or not self.store.acl_allows(user_id, dictation_id):
            return None
        return loaded

    def _drop_grants(self, dictation_id: str) -> None:
        for key in [k for k in self._grants if k[1] == dictation_id]:
            del self._grants[key]
