"""Metadata regex preview and confirmed assignment (PAR-CFG-004).

Preview and apply run the same bounded engine from src/clinic/presets.py on the
same rules. Apply only proceeds when the rule version and the input digest of
the preview still match. Actor and role always come from the session; the
per-object decision is delegated to the injected ``authorize`` and ``assign``
adapters of the later consumer packages.

Active system rules can be listed by signed-in users and managed by site
administrators. Preview and apply accept either inline rules or saved rule IDs.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from hashlib import sha256
from typing import Any

from flask import Blueprint, abort, jsonify, request, session
from flask_login import current_user
from flask_wtf.csrf import CSRFError, validate_csrf
from sqlalchemy.exc import SQLAlchemyError
from wtforms.validators import ValidationError as CsrfValidationError

from src.clinic import presets
from src.clinic.admin_dictation_access import _is_active_site_admin
from src.clinic.config import clinic_mode_enabled
from src.clinic.parity_dispatch import CommandProblem, _problem
from src.database import db

MAX_RULES = 20
MAX_PATTERN_LENGTH = 256
MAX_TEXT_LENGTH = 10000
MAX_BODY_BYTES = 256 * 1024
RULE_KEYS = frozenset({"name", "pattern", "target_field", "replacement", "priority_order", "max_input_length"})
PREVIEW_KEYS = frozenset({"dictation_id", "rules", "rule_ids", "text", "max_input_length"})
RULES_URL = "/api/clinic/v1/regex/rules"
# shortcut: heuristic ReDoS guard (nested or adjacent unbounded quantifiers), upgrade to a linear-time engine.
_NESTED_QUANTIFIER = re.compile(r"\((?:[^()\\]|\\.)*(?:[+*]|\{\d*,\d*\})(?:[^()\\]|\\.)*\)(?:[+*]|\{\d*,\d*\})")
_BACKREFERENCE = re.compile(r"\\[1-9]|\(\?P=|\(\?\(")

Authorize = Callable[[int, str, str], bool]
Assign = Callable[[int, str, dict[str, Any]], dict[str, Any]]


def _input_limit(limit: Any) -> int:
    if type(limit) is not int or not 1 <= limit <= MAX_TEXT_LENGTH:
        raise CommandProblem("INVALID_PAYLOAD")
    return limit


def _normalize_rules(rules: Any) -> list[dict[str, Any]]:
    """Validate rules; any refusal raises CommandProblem with a public code."""
    if not isinstance(rules, list) or not 1 <= len(rules) <= MAX_RULES:
        raise CommandProblem("INVALID_PAYLOAD")
    clean = []
    for rule in rules:
        if not isinstance(rule, dict) or rule.keys() - RULE_KEYS or not {"pattern", "target_field"} <= rule.keys():
            raise CommandProblem("INVALID_PAYLOAD")
        pattern, target, replacement = rule["pattern"], rule["target_field"], rule.get("replacement")
        order = rule.get("priority_order", 100)
        if not (isinstance(pattern, str) and isinstance(target, str) and type(order) is int
                and isinstance(rule.get("name", "rule"), str)
                and (replacement is None or isinstance(replacement, str))):
            raise CommandProblem("INVALID_PAYLOAD")
        if target in presets.FORBIDDEN_REGEX_TARGETS:
            raise CommandProblem("POLICY_DENIED")
        if target not in presets.ALLOWED_REGEX_TARGETS:
            raise CommandProblem("INVALID_PAYLOAD")
        if (not pattern or len(pattern) > MAX_PATTERN_LENGTH or _NESTED_QUANTIFIER.search(pattern)
                or _BACKREFERENCE.search(pattern)):
            raise CommandProblem("INVALID_PAYLOAD")
        try:
            compiled = re.compile(pattern)
            if replacement:
                compiled.sub(replacement, "")
        except (re.error, IndexError):
            raise CommandProblem("INVALID_PAYLOAD")
        entry = {"name": rule.get("name", "rule"), "pattern": pattern, "target_field": target,
                 "replacement": replacement, "priority_order": order}
        if "max_input_length" in rule:
            entry["max_input_length"] = _input_limit(rule["max_input_length"])
        clean.append(entry)
    return clean


def rule_version(rules: list[dict[str, Any]], limit: int = MAX_TEXT_LENGTH) -> str:
    payload = rules if limit == MAX_TEXT_LENGTH else {"rules": rules, "max_input_length": limit}
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def input_digest(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def run_preview(rules: Any, text: Any, max_input_length: Any = MAX_TEXT_LENGTH) -> dict[str, Any]:
    """Run the shared engine and bind the result to rule version and input."""
    if not isinstance(text, str) or len(text) > MAX_TEXT_LENGTH:
        raise CommandProblem("INVALID_PAYLOAD")
    clean = _normalize_rules(rules)
    limit = _input_limit(max_input_length)
    fields, matches, truncated = {}, [], False
    try:
        for rule in sorted(clean, key=lambda rule: rule["priority_order"]):
            cap = min(limit, rule.get("max_input_length", MAX_TEXT_LENGTH))
            result = presets.preview_regex_rules([rule], text, cap)
            for target, value in result["resulting_fields"].items():
                # Keep the latest hit last, including when assignment maps aliases to one field.
                fields.pop(target, None)
                fields[target] = value
            matches.extend(result["matches"])
            truncated |= result["truncated"]
    except presets.ForbiddenTargetError:
        raise CommandProblem("POLICY_DENIED")
    except presets.PresetError:
        raise CommandProblem("INVALID_PAYLOAD")
    return {"resulting_fields": fields, "matches": matches, "truncated": truncated,
            "rule_version": rule_version(clean, limit), "input_digest": input_digest(text)}


def _json_body() -> dict[str, Any]:
    if not request.is_json or (request.content_length or 0) > MAX_BODY_BYTES:
        raise CommandProblem("INVALID_PAYLOAD")
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise CommandProblem("INVALID_PAYLOAD")
    return body


def _rule_payload(row: presets.ClinicRegexRule) -> dict[str, Any]:
    return {key: getattr(row, key) for key in RULE_KEYS}


def _system_rules():
    return presets.ClinicRegexRule.query.filter_by(
        scope="system", scope_id=presets.SYSTEM_SCOPE_ID, is_active=True)


def _valid_rule_id(value: Any) -> bool:
    return isinstance(value, str) and 1 <= len(value) <= 36


def _locked_rule(rule_id: Any) -> presets.ClinicRegexRule:
    if not _valid_rule_id(rule_id):
        raise CommandProblem("INVALID_PAYLOAD")
    row = _system_rules().filter_by(id=rule_id).with_for_update().one_or_none()
    if row is None:
        abort(404)
    return row


def _request_preview(body: dict[str, Any], missing_code: str = "INVALID_PAYLOAD") -> dict[str, Any]:
    rules = body.get("rules")
    if "rule_ids" in body:
        ids = body["rule_ids"]
        if ("rules" in body or not isinstance(ids, list) or not 1 <= len(ids) <= MAX_RULES
                or not all(_valid_rule_id(rule_id) for rule_id in ids) or len(set(ids)) != len(ids)):
            raise CommandProblem("INVALID_PAYLOAD")
        by_id = {row.id: _rule_payload(row) for row in
                 _system_rules().filter(presets.ClinicRegexRule.id.in_(ids)).all()}
        if len(by_id) != len(ids):
            raise CommandProblem(missing_code)
        rules = [by_id[rule_id] for rule_id in ids]
    return run_preview(rules, body.get("text"), body.get("max_input_length", MAX_TEXT_LENGTH))


class RegexApi:
    """Mount ``blueprint`` once, after supplying the object policy adapters.

    authorize(actor_id, action, dictation_id) -> bool, action is "regex_preview"
    or "regex_apply". assign(actor_id, dictation_id, fields) writes the confirmed
    fields through the existing metadata service and returns its result.
    """

    def __init__(self, authorize: Authorize, assign: Assign):
        self._authorize, self._assign = authorize, assign
        self.blueprint = Blueprint("clinic_regex", __name__)
        for name, view in (("preview", self._preview), ("apply", self._apply)):
            self.blueprint.add_url_rule(f"/api/clinic/v1/regex/{name}", name, view, methods=["POST"])
        self.blueprint.add_url_rule(RULES_URL, "list_rules", self._list_rules, methods=["GET"])
        self.blueprint.add_url_rule(RULES_URL, "save_rule", self._save_rule, methods=["POST"])
        self.blueprint.add_url_rule(RULES_URL, "delete_rule", self._delete_rule, methods=["DELETE"])
        self.blueprint.add_url_rule(RULES_URL + "/<rule_id>", "delete_rule", self._delete_rule, methods=["DELETE"])
        self.blueprint.before_request(self._guard)
        self.blueprint.register_error_handler(CSRFError, lambda _error: _problem("FORBIDDEN"))
        self.blueprint.register_error_handler(CommandProblem, lambda error: _problem(str(error)))
        self.blueprint.register_error_handler(SQLAlchemyError, self._database_error)

    def _guard(self):
        if not clinic_mode_enabled():
            abort(404)
        if not (current_user.is_authenticated and current_user.is_active
                and session.get("_user_id") == current_user.get_id()):
            return _problem("UNAUTHORIZED")
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            try:
                validate_csrf(request.headers.get("X-CSRF-Token") or request.headers.get("X-CSRFToken"))
            except CsrfValidationError:
                return _problem("FORBIDDEN")

    @staticmethod
    def _database_error(_error):
        db.session.rollback()
        return _problem("DEPENDENCY_UNAVAILABLE")

    def _list_rules(self):
        rule = presets.ClinicRegexRule
        rows = _system_rules().order_by(rule.priority_order, rule.id).all()
        return jsonify(rules=[{"id": row.id, **_rule_payload(row)} for row in rows])

    def _save_rule(self):
        if not _is_active_site_admin(db.session, current_user):
            raise CommandProblem("POLICY_DENIED")
        body = _json_body()
        row = _locked_rule(body.pop("id")) if "id" in body else None
        clean = _normalize_rules([{**(_rule_payload(row) if row is not None else {}), **body}])[0]
        name, replacement = clean["name"], clean["replacement"]
        if (not name.strip() or len(name) > 128 or len(replacement or "") > 256
                or not -(2**31) <= clean["priority_order"] < 2**31):
            raise CommandProblem("INVALID_PAYLOAD")
        status = 201 if row is None else 200
        if row is None:
            row = presets.save_regex_rule(**clean)
        else:
            for key, value in clean.items():
                setattr(row, key, value)
        payload = {"id": row.id, **_rule_payload(row)}
        db.session.commit()
        return jsonify(payload), status

    def _delete_rule(self, rule_id=None):
        if not _is_active_site_admin(db.session, current_user):
            raise CommandProblem("POLICY_DENIED")
        if rule_id is None:
            body = _json_body()
            if body.keys() != {"id"}:
                raise CommandProblem("INVALID_PAYLOAD")
            rule_id = body["id"]
        _locked_rule(rule_id).is_active = False
        db.session.commit()
        return "", 204

    def _guarded(self, action: str, handler: Callable[[int, dict[str, Any]], dict[str, Any]]):
        actor = current_user.id
        body = _json_body()
        if not isinstance(body.get("dictation_id"), str) or not body["dictation_id"]:
            raise CommandProblem("INVALID_PAYLOAD")
        if not self._authorize(actor, action, body["dictation_id"]):
            raise CommandProblem("POLICY_DENIED")
        return jsonify(handler(actor, body))

    def _preview(self):
        def handler(_actor: int, body: dict[str, Any]) -> dict[str, Any]:
            if body.keys() - PREVIEW_KEYS:
                raise CommandProblem("INVALID_PAYLOAD")
            return _request_preview(body)
        return self._guarded("regex_preview", handler)

    def _apply(self):
        def handler(actor: int, body: dict[str, Any]) -> dict[str, Any]:
            if body.keys() - PREVIEW_KEYS - {"rule_version", "input_digest"}:
                raise CommandProblem("INVALID_PAYLOAD")
            if not isinstance(body.get("rule_version"), str) or not isinstance(body.get("input_digest"), str):
                raise CommandProblem("INVALID_PAYLOAD")
            fresh = _request_preview(body, missing_code="REVISION_CONFLICT")
            if (fresh["rule_version"], fresh["input_digest"]) != (body["rule_version"], body["input_digest"]):
                raise CommandProblem("REVISION_CONFLICT")
            try:
                saved = self._assign(actor, body["dictation_id"], fresh["resulting_fields"])
            except CommandProblem:
                raise
            except Exception:
                raise CommandProblem("DEPENDENCY_UNAVAILABLE")
            return {"applied_fields": fresh["resulting_fields"], "result": saved,
                    "rule_version": fresh["rule_version"]}
        return self._guarded("regex_apply", handler)
