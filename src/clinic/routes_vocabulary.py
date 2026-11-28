"""HTTP surface for vocabulary, lists and selected-text additions (PAR-VOC-001/002/003/006).

A thin layer over ``vocabulary``; scoped search, text import/export, suppression,
reactivation and precedence are never re-implemented here. The actor is always
``current_user``: a body never names an actor, user or role. User scope means the
caller's own list. Pool scope is rechecked against the live author-pool membership
on every request; pool writes additionally need the injected ``can_write_pool``
adapter (the later consumer package owning pool-lead rules). Mounting
``blueprint`` in the app is left to the consumer packages (w103, w200).

Entry versions are the entry's ``updated_at``; a write with a different
``expected_version`` fails with 409 ``stale_version`` and changes nothing. The
confirmed bulk endpoint applies that check to every named entry and rolls the
whole list back when any version or permission fails.
"""

from __future__ import annotations

import base64
import re
import unicodedata
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from sqlite3 import SQLITE_CONSTRAINT_UNIQUE
from typing import Any

from flask import Blueprint, abort, jsonify, request, session
from flask_login import current_user
from flask_wtf.csrf import CSRFError, validate_csrf
from sqlalchemy.exc import IntegrityError
from wtforms.validators import ValidationError

from src.clinic import caution_rules, caution_words, pool_policy, vocabulary
from src.clinic.config import clinic_mode_enabled
from src.clinic.identity import ClinicAccountPolicy
from src.clinic.pools import ClinicAuthorPool
from src.database import db

PREFIX = "/api/clinic/v1/vocabulary"
MAX_BODY_BYTES = 256 * 1024
MAX_SELECTION = 255
SEARCH_MODES = ("contains", "prefix", "suffix", "exact")
SEARCH_FIELDS = ("written", "spoken", "all")
BULK_ACTIONS = ("suppress", "delete")
MAX_BULK = 100
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
            ("/caution-words", "caution_list", self._caution_list, "GET"),
            ("/caution-words/global", "caution_publish_global", self._caution_publish_global, "POST"),
            ("/caution-words/pool", "caution_publish_pool", self._caution_publish_pool, "POST"),
            ("/caution-words/preview", "caution_preview", self._caution_preview, "POST"),
            ("/entries/<entry_id>", "edit_entry", self._edit, "PUT"),
            ("/entries/<entry_id>/suppress", "suppress", lambda entry_id: self._toggle(entry_id, True), "POST"),
            ("/entries/<entry_id>/reactivate", "reactivate", lambda entry_id: self._toggle(entry_id, False), "POST"),
            ("/entries/bulk", "bulk", self._bulk, "POST"),
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
            except caution_rules.StaleVersion as error:
                db.session.rollback()
                return _problem("stale_version", current_version=error.version)
            except caution_rules.CautionRuleError:
                db.session.rollback()
                return _problem("invalid_payload")
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
        field = args.get("field", "written")
        if (mode not in SEARCH_MODES or field not in SEARCH_FIELDS
                or status not in ("active", "suppressed", "all") or len(args.get("q", "")) > MAX_SELECTION):
            raise ApiError("invalid_payload")
        kwargs = self._scope(args.get("scope"), args.get("pool_id"), write=False)
        rows = vocabulary.search_vocabulary(
            args.get("q", ""), mode=mode, field=field, status=None if status == "all" else status, **kwargs)
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

    def _edit(self, entry_id: str):
        body = _body()
        expected_version = body.get("expected_version")
        if body.keys() - {"expected_version", "written", "spoken"} or not isinstance(expected_version, str):
            raise ApiError("invalid_payload")
        entry = self._entry_for_write(entry_id, expected_version)
        written = canonical_selection(body.get("written"))
        spoken = body.get("spoken")
        spoken = None if spoken in (None, "") else canonical_selection(spoken)

        model = vocabulary.ClinicVocabularyEntry
        updated_at = max(datetime.now(timezone.utc), entry.updated_at + timedelta(microseconds=1))
        # Match the checked version in the write so a concurrent editor cannot be overwritten.
        update = (db.update(model)
                  .where(model.id == entry_id, model.updated_at == entry.updated_at)
                  .values(written_form=written, spoken_form=spoken, updated_at=updated_at)
                  .returning(model))
        try:
            changed = db.session.scalars(update, execution_options={"populate_existing": True}).one_or_none()
        except IntegrityError as error:
            cause = error.orig
            if (getattr(cause, "pgcode", None) == "23505"
                    or getattr(cause, "sqlite_errorcode", None) == SQLITE_CONSTRAINT_UNIQUE):
                raise ApiError("duplicate_entry") from error
            raise
        if changed is None:
            current = db.session.get(model, entry_id, populate_existing=True)
            if current is None:
                raise ApiError("not_found")
            raise ApiError("stale_version", current_version=_version(current))
        result = _entry_json(changed)
        db.session.commit()
        return jsonify(entry=result)

    def _entry_for_write(self, entry_id: str, expected_version: str) -> vocabulary.ClinicVocabularyEntry:
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
        if expected_version != _version(entry):
            raise ApiError("stale_version", current_version=_version(entry))
        return entry

    def _toggle(self, entry_id: str, suppress: bool):
        body = _body()
        if body.keys() - {"expected_version"} or not isinstance(body.get("expected_version"), str):
            raise ApiError("invalid_payload")
        entry = self._entry_for_write(entry_id, body["expected_version"])
        change = vocabulary.suppress_vocabulary_entry if suppress else vocabulary.reactivate_vocabulary_entry
        entry = change(entry.id)
        db.session.commit()
        return jsonify(entry=_entry_json(entry))

    # --- Caution words (PAR-VOC-004, Q-19): site-admin baseline, separately authorized pool additions ---

    @staticmethod
    def _site_admin() -> bool:
        account = db.session.get(ClinicAccountPolicy, current_user.id)
        return bool(getattr(current_user, "is_admin", False)) and (account is None or account.status == "active")

    def _caution_pool(self, pool_id: Any, write: bool) -> str:
        """Pool readers are members or pool writers; only pool writers publish. Checked live."""
        if not isinstance(pool_id, str) or not pool_id:
            raise ApiError("invalid_payload")
        if db.session.get(ClinicAuthorPool, pool_id) is None:
            raise ApiError("unauthorized")
        writer = self._can_write_pool(current_user.id, pool_id)
        if not (writer or (not write and pool_id in pool_policy.author_pool_ids(current_user.id))):
            raise ApiError("unauthorized")
        return pool_id

    def _caution_pools(self) -> list[dict[str, str]]:
        actor_id = current_user.id
        member_pools = pool_policy.author_pool_ids(actor_id)
        return [
            {"id": pool_id, "name": name}
            for pool_id, name in db.session.query(ClinicAuthorPool.id, ClinicAuthorPool.name)
            .order_by(ClinicAuthorPool.name)
            if pool_id in member_pools or self._can_write_pool(actor_id, pool_id)
        ]

    @staticmethod
    def _rule_json(rules: caution_rules.RuleList) -> dict[str, Any]:
        return {"version": rules.version, "words": list(rules.words), "published_by": rules.published_by,
                "published_at": rules.published_at.isoformat() if rules.published_at else None}

    def _caution_list(self):
        scope, pool_id = request.args.get("scope"), request.args.get("pool_id")
        if scope == "global" and pool_id is None:
            key = caution_rules.GLOBAL_KEY
        elif scope == "pool":
            key = caution_rules.pool_key(self._caution_pool(pool_id, write=False))
        else:
            raise ApiError("invalid_payload")
        versions = [self._rule_json(row) for row in caution_rules.history(key)]
        extra = {"pools": self._caution_pools()} if scope == "global" else {}
        return jsonify(scope=scope, pool_id=pool_id, can_write=self._site_admin() if scope == "global"
                       else self._can_write_pool(current_user.id, pool_id),
                       **(versions[0] if versions else self._rule_json(caution_rules.current(key))),
                       history=[{k: v[k] for k in ("version", "published_by", "published_at")}
                                | {"count": len(v["words"])} for v in versions], **extra)

    def _publish(self, key: str, scope: str, pool_id: str | None, body: dict[str, Any]):
        rules = caution_rules.publish(key, body.get("words"), current_user.id, body.get("expected_version"))
        return jsonify(scope=scope, pool_id=pool_id, **self._rule_json(rules)), 201

    def _caution_publish_global(self):
        body = _body()
        if body.keys() - {"words", "expected_version"}:
            raise ApiError("invalid_payload")
        if not self._site_admin():
            raise ApiError("unauthorized")
        return self._publish(caution_rules.GLOBAL_KEY, "global", None, body)

    def _caution_publish_pool(self):
        body = _body()
        if body.keys() - {"pool_id", "words", "expected_version"}:
            raise ApiError("invalid_payload")
        pool_id = self._caution_pool(body.get("pool_id"), write=True)
        return self._publish(caution_rules.pool_key(pool_id), "pool", pool_id, body)

    def _caution_preview(self):
        """Run the W11 matcher on a sample text with the effective list; no marker is created."""
        body = _body()
        text = body.get("text")
        if body.keys() - {"pool_id", "text"} or not isinstance(text, str) or len(text) > MAX_BODY_BYTES // 4:
            raise ApiError("invalid_payload")
        rule_set = caution_rules.effective_rule_set(self._caution_pool(body.get("pool_id"), write=False))
        return jsonify(version=rule_set.version, hits=[
            {"word": word, "offset": offset} for word, offset in caution_words.find_hits(text, rule_set.words)])

    def _permitted(self, entry: vocabulary.ClinicVocabularyEntry) -> bool:
        """True when the caller may write this entry. Failures stay a 404, as on a single toggle."""
        try:
            self._scope(entry.scope, entry.pool_id if entry.scope == "pool" else None, write=True)
        except ApiError:
            return False
        return not (entry.scope == "user" and entry.user_id != current_user.id)

    def _bulk(self):
        """Confirmed bulk suppress or hard delete. One stale version or one forbidden entry rejects all."""
        body = _body()
        entries = body.get("entries")
        if (body.keys() - {"action", "entries", "confirm"} or body.get("confirm") is not True
                or body.get("action") not in BULK_ACTIONS or not isinstance(entries, list)
                or not entries or len(entries) > MAX_BULK):
            raise ApiError("invalid_payload")
        items: list[dict[str, str]] = []
        chosen: list[vocabulary.ClinicVocabularyEntry] = []
        for item in entries:
            if not isinstance(item, dict) or item.keys() - {"id", "expected_version"}:
                raise ApiError("invalid_payload")
            entry_id, expected = item.get("id"), item.get("expected_version")
            if not isinstance(entry_id, str) or not entry_id or not isinstance(expected, str):
                raise ApiError("invalid_payload")
            entry = db.session.get(vocabulary.ClinicVocabularyEntry, entry_id)
            # Check permission before the version, so a foreign entry never reveals that it changed.
            if entry is None or not self._permitted(entry):
                raise ApiError("not_found")
            if expected != _version(entry):
                raise ApiError("stale_version", current_version=_version(entry))
            items.append({"id": entry_id, "expected_version": expected})
            chosen.append(entry)
        try:
            vocabulary.apply_vocabulary_bulk(items, body["action"])
        except vocabulary.VocabularyError as error:
            if error.code == "stale_version":
                raise ApiError("stale_version")
            raise
        db.session.commit()
        if body["action"] == "delete":
            return jsonify(deleted=len(items))
        return jsonify(entries=[_entry_json(row) for row in chosen])
