"""Metadata regex preview and confirmed assignment (PAR-CFG-004).

Preview and apply run the same bounded engine from src/clinic/presets.py on the
same rules. Apply only proceeds when the rule version and the input digest of
the preview still match. Actor and role always come from the session; the
per-object decision is delegated to the injected ``authorize`` and ``assign``
adapters of the later consumer packages.
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
from wtforms.validators import ValidationError as CsrfValidationError

from src.clinic import presets
from src.clinic.config import clinic_mode_enabled
from src.clinic.parity_dispatch import CommandProblem, _problem

MAX_RULES = 20
MAX_PATTERN_LENGTH = 256
MAX_TEXT_LENGTH = 10000
MAX_BODY_BYTES = 256 * 1024
RULE_KEYS = frozenset({"name", "pattern", "target_field", "replacement", "priority_order"})
# shortcut: heuristic ReDoS guard (nested or adjacent unbounded quantifiers), upgrade to a linear-time engine.
_NESTED_QUANTIFIER = re.compile(r"\((?:[^()\\]|\\.)*(?:[+*]|\{\d*,\d*\})(?:[^()\\]|\\.)*\)(?:[+*]|\{\d*,\d*\})")
_BACKREFERENCE = re.compile(r"\\[1-9]|\(\?P=|\(\?\(")

Authorize = Callable[[int, str, str], bool]
Assign = Callable[[int, str, dict[str, Any]], dict[str, Any]]


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
            re.compile(pattern)
        except re.error:
            raise CommandProblem("INVALID_PAYLOAD")
        clean.append({"name": rule.get("name", "rule"), "pattern": pattern, "target_field": target,
                      "replacement": replacement, "priority_order": order})
    return clean


def rule_version(rules: list[dict[str, Any]]) -> str:
    return sha256(json.dumps(rules, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def input_digest(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def run_preview(rules: Any, text: Any) -> dict[str, Any]:
    """Run the shared engine and bind the result to rule version and input."""
    if not isinstance(text, str) or len(text) > MAX_TEXT_LENGTH:
        raise CommandProblem("INVALID_PAYLOAD")
    clean = _normalize_rules(rules)
    try:
        result = presets.preview_regex_rules(clean, text, MAX_TEXT_LENGTH)
    except presets.ForbiddenTargetError:
        raise CommandProblem("POLICY_DENIED")
    except presets.PresetError:
        raise CommandProblem("INVALID_PAYLOAD")
    return {**result, "rule_version": rule_version(clean), "input_digest": input_digest(text)}


def _json_body() -> dict[str, Any]:
    if not request.is_json or (request.content_length or 0) > MAX_BODY_BYTES:
        raise CommandProblem("INVALID_PAYLOAD")
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or not isinstance(body.get("dictation_id"), str) or not body["dictation_id"]:
        raise CommandProblem("INVALID_PAYLOAD")
    return body


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
        self.blueprint.register_error_handler(CSRFError, lambda _error: _problem("FORBIDDEN"))

    def _guarded(self, action: str, handler: Callable[[int, dict[str, Any]], dict[str, Any]]):
        if not clinic_mode_enabled():
            abort(404)
        if not (current_user.is_authenticated and current_user.is_active
                and session.get("_user_id") == current_user.get_id()):
            return _problem("UNAUTHORIZED")
        try:
            validate_csrf(request.headers.get("X-CSRF-Token"))
        except CsrfValidationError:
            return _problem("FORBIDDEN")
        actor = current_user.id
        try:
            body = _json_body()
            if not self._authorize(actor, action, body["dictation_id"]):
                raise CommandProblem("POLICY_DENIED")
            return jsonify(handler(actor, body))
        except CommandProblem as error:
            return _problem(str(error))

    def _preview(self):
        def handler(_actor: int, body: dict[str, Any]) -> dict[str, Any]:
            if body.keys() - {"dictation_id", "rules", "text"}:
                raise CommandProblem("INVALID_PAYLOAD")
            return run_preview(body.get("rules"), body.get("text"))
        return self._guarded("regex_preview", handler)

    def _apply(self):
        def handler(actor: int, body: dict[str, Any]) -> dict[str, Any]:
            if body.keys() - {"dictation_id", "rules", "text", "rule_version", "input_digest"}:
                raise CommandProblem("INVALID_PAYLOAD")
            if not isinstance(body.get("rule_version"), str) or not isinstance(body.get("input_digest"), str):
                raise CommandProblem("INVALID_PAYLOAD")
            fresh = run_preview(body.get("rules"), body.get("text"))
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
