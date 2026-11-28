"""HTTP surface for vocabulary, lists and selected-text additions (PAR-VOC-001/002/003/006).

A thin layer over ``vocabulary``; scoped search, text import/export, suppression,
reactivation and precedence are never re-implemented here. The actor is always
``current_user``: a body never names an actor, user or role. User scope means the
caller's own list. Pool scope is rechecked against the live author-pool membership
on every request; pool writes additionally need the injected ``can_write_pool``
adapter (the later consumer package owning pool-lead rules). Mounting
``blueprint`` in the app is left to the consumer packages (w103, w200).

Entry versions are the entry's ``updated_at``; a write with a different
``expected_version`` fails with 409 ``stale_version`` and changes nothing.
"""

from __future__ import annotations

import base64
import re
import unicodedata
from collections.abc import Callable
from typing import Any

from flask import Blueprint, abort, jsonify, request, session
from flask_login import current_user
from flask_wtf.csrf import CSRFError, validate_csrf
from wtforms.validators import ValidationError

from src.clinic import pool_policy, vocabulary
from src.clinic.config import clinic_mode_enabled
from src.database import db

PREFIX = "/api/clinic/v1/vocabulary"
MAX_BODY_BYTES = 256 * 1024
MAX_SELECTION = 255
SEARCH_MODES = ("contains", "prefix", "suffix", "exact")
_FORBIDDEN_KEYS = frozenset({
    "actor", "actor_id", "user", "user_id", "author_user_id", "role", "roles",
    "is_admin", "created_by",
})
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_STATUS = {
    "unauthenticated": 401, "unauthorized": 403, "not_found": 404, "stale_version": 409,
    "invalid_payload": 400, "duplicate_entry": 409,
}
_TITLES = {400: "Ungültige Anfrage", 401: "Anmeldung erforderlich", 403: "Zugriff verweigert",
           404: "Nicht gefunden", 409: "Versionskonflikt"}

_ENCODINGS = {"utf-8": "utf-8", "cp1252": "cp1252", "latin-1": "latin-1"}

CanWritePool = Callable[[int, str], bool]


class ApiError(Exception):
    def __init__(self, code: str, **extra: Any) -> None:
        super().__init__(code)
        self.code, self.extra = code, extra


def canonical_selection(text: Any) -> str:
    """Bound a selected text to one canonical single-line form (NFC, collapsed spaces)."""
    if not isinstance(text, str):
        raise ApiError("invalid_payload")
    text = " ".join(unicodedata.normalize("NFC", text).split())
    if not text or len(text) > MAX_SELECTION or _CONTROL.search(text):
        raise ApiError("invalid_payload")
    return text


def _problem(code: str, **extra: Any):
    status = _STATUS.get(code, 400)
    response = jsonify(type="about:blank", title=_TITLES[status], status=status, code=code, **extra)
    response.mimetype = "application/problem+json"
    return response, status


def _version(entry: vocabulary.ClinicVocabularyEntry) -> str:
    return entry.updated_at.isoformat()


def _entry_json(entry: vocabulary.ClinicVocabularyEntry) -> dict[str, Any]:
    return {"id": entry.id, "scope": entry.scope, "pool_id": entry.pool_id, "written": entry.written_form,
            "spoken": entry.spoken_form, "status": entry.status, "origin": entry.origin,
            "version": _version(entry)}


def _decode_base64_lines(data: Any, encoding: Any, errors: list[dict[str, Any]]) -> str:
    """Decode base64 bytes line by line; undecodable lines are blanked and reported, line numbers stay stable."""
    if encoding not in _ENCODINGS or not isinstance(data, str):
        raise ApiError("invalid_payload")
    try:
        raw = base64.b64decode(data, validate=True)
    except ValueError:
        raise ApiError("invalid_payload")
    if len(raw) > MAX_BODY_BYTES:
        raise ApiError("invalid_payload")
    lines = []
    for line_no, line in enumerate(raw.splitlines(), start=1):
        try:
            lines.append(line.decode(_ENCODINGS[encoding]))
        except UnicodeDecodeError:
            errors.append({"line": line_no, "reason": "invalid_encoding"})
            lines.append("")
    return "\n".join(lines)


def _body() -> dict[str, Any]:
    if not request.is_json or (request.content_length or 0) > MAX_BODY_BYTES:
        raise ApiError("invalid_payload")
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or _FORBIDDEN_KEYS & body.keys():
        raise ApiError("invalid_payload")
    return body


class VocabularyApi:
    """Mount ``blueprint`` once. can_write_pool(actor_id, pool_id) -> bool decides pool writes."""

    def __init__(self, can_write_pool: CanWritePool) -> None:
        self._can_write_pool = can_write_pool
        self.blueprint = bp = Blueprint("clinic_vocabulary", __name__)
        bp.before_request(self._guard)
        bp.register_error_handler(CSRFError, lambda _e: _problem("unauthorized"))
        rules = (
            ("/search", "search", self._search, "GET"),
            ("/export", "export", self._export, "GET"),
            ("/import", "import_text", self._import, "POST"),
            ("/additions", "additions", self._add, "POST"),
            ("/entries/<entry_id>/suppress", "suppress", lambda entry_id: self._toggle(entry_id, True), "POST"),
            ("/entries/<entry_id>/reactivate", "reactivate", lambda entry_id: self._toggle(entry_id, False), "POST"),
        )
        for path, name, view, method in rules:
            bp.add_url_rule(PREFIX + path, name, self._wrap(view), methods=[method])

    @staticmethod
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

    @staticmethod
    def _wrap(view: Callable[..., Any]) -> Callable[..., Any]:
        def run(**kwargs: Any):
            try:
                return view(**kwargs)
            except ApiError as error:
                db.session.rollback()
                return _problem(error.code, **error.extra)
            except vocabulary.VocabularyError as error:
                db.session.rollback()
                return _problem("duplicate_entry" if error.code == "duplicate_entry" else "invalid_payload")
        run.__name__ = view.__name__ if hasattr(view, "__name__") else "view"
        return run

    def _scope(self, scope: Any, pool_id: Any, write: bool) -> dict[str, Any]:
        """Resolve a scope to vocabulary kwargs; pool membership is read live."""
        if scope == "user" and pool_id is None:
            return {"scope": "user", "user_id": current_user.id}
        if scope == "pool" and isinstance(pool_id, str) and pool_id:
            if pool_id not in pool_policy.author_pool_ids(current_user.id):
                raise ApiError("unauthorized")
            if write and not self._can_write_pool(current_user.id, pool_id):
                raise ApiError("unauthorized")
            return {"scope": "pool", "pool_id": pool_id}
        raise ApiError("invalid_payload")

    def _search(self):
        def response(*, entries):
            pool_id = request.args.get("pool_id")
            can_write = pool_id is None or self._can_write_pool(current_user.id, pool_id)
            return jsonify(entries=entries, can_write=can_write)

        return self._search_entries(response)

    def _search_entries(self, jsonify):
        args = request.args
        mode = args.get("mode", "contains")
        status = args.get("status", "active")
        if mode not in SEARCH_MODES or status not in ("active", "suppressed", "all") or len(args.get("q", "")) > MAX_SELECTION:
            raise ApiError("invalid_payload")
        kwargs = self._scope(args.get("scope"), args.get("pool_id"), write=False)
        rows = vocabulary.search_vocabulary(args.get("q", ""), mode=mode, status=None if status == "all" else status, **kwargs)
        return jsonify(entries=[_entry_json(row) for row in rows])

    def _export(self):
        if request.args.get("scope") == "effective":
            pools = sorted(pool_policy.author_pool_ids(current_user.id))
            text = vocabulary.export_effective_vocabulary_text(current_user.id, pools)
        else:
            kwargs = self._scope(request.args.get("scope"), request.args.get("pool_id"), write=False)
            text = vocabulary.export_vocabulary_text(**kwargs)
        return jsonify(content=text)

    def _import(self):
        body = _body()
        allowed = {"scope", "pool_id", "content", "content_base64", "encoding", "dry_run", "duplicate_rule"}
        dry_run = body.get("dry_run", False)
        if body.keys() - allowed or not isinstance(dry_run, bool) or ("content" in body) == ("content_base64" in body):
            raise ApiError("invalid_payload")
        decode_errors: list[dict[str, Any]] = []
        if "content" in body:
            if "encoding" in body or not isinstance(body["content"], str):
                raise ApiError("invalid_payload")
            content = body["content"]
        else:
            content = _decode_base64_lines(body["content_base64"], body.get("encoding", "utf-8"), decode_errors)
        kwargs = self._scope(body.get("scope"), body.get("pool_id"), write=True)
        duplicate_rule = body.get("duplicate_rule", "replace")
        if dry_run:
            # Run the real import inside a savepoint and discard it: counters and errors stay exact.
            savepoint = db.session.begin_nested()
            try:
                result = vocabulary.import_vocabulary_text(content, duplicate_rule=duplicate_rule, **kwargs)
            finally:
                savepoint.rollback()
            db.session.rollback()
        else:
            result = vocabulary.import_vocabulary_text(content, duplicate_rule=duplicate_rule, **kwargs)
            db.session.commit()
        result["errors"] = sorted(decode_errors + result["errors"], key=lambda e: e["line"])
        result["dry_run"] = dry_run
        return jsonify(result)

    def _add(self):
        body = _body()
        if body.keys() - {"scope", "pool_id", "written", "spoken"}:
            raise ApiError("invalid_payload")
        kwargs = self._scope(body.get("scope"), body.get("pool_id"), write=True)
        written = canonical_selection(body.get("written"))
        spoken = canonical_selection(body["spoken"]) if body.get("spoken") else None
        entry = vocabulary.add_vocabulary_entry(written_form=written, spoken_form=spoken, **kwargs)
        db.session.commit()
        return jsonify(entry=_entry_json(entry)), 201

    def _toggle(self, entry_id: str, suppress: bool):
        body = _body()
        if body.keys() - {"expected_version"} or not isinstance(body.get("expected_version"), str):
            raise ApiError("invalid_payload")
        entry = db.session.get(vocabulary.ClinicVocabularyEntry, entry_id)
        # Unknown and not-permitted entries look the same: no existence leak.
        if entry is None:
            raise ApiError("not_found")
        try:
            self._scope(entry.scope, entry.pool_id if entry.scope == "pool" else None, write=True)
        except ApiError:
            raise ApiError("not_found")
        if entry.scope == "user" and entry.user_id != current_user.id:
            raise ApiError("not_found")
        if body["expected_version"] != _version(entry):
            raise ApiError("stale_version", current_version=_version(entry))
        change = vocabulary.suppress_vocabulary_entry if suppress else vocabulary.reactivate_vocabulary_entry
        entry = change(entry_id)
        db.session.commit()
        return jsonify(entry=_entry_json(entry))
