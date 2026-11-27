"""Expose safe command and pronunciation operations (PAR-VOC-007, PAR-VOC-008).

Provides:
1. Resources for effective voice commands and pronunciation hints with ETag support.
2. Declarative web-command execution adapter checking hooks, policy, lease and confirmations.
3. Parity command adapters for save_command and request_adaptation.
4. Runtime resolution of pronunciation capabilities via AsrCapabilityRegistry.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from flask import Blueprint, abort, current_app, jsonify, request, session
from flask_login import current_user
from flask_wtf.csrf import CSRFError, validate_csrf
from sqlalchemy.exc import OperationalError
from wtforms.validators import ValidationError

from src.clinic import policy, pool_policy, vocabulary
from src.clinic.app_wiring import choose_author_pool
from src.clinic.asr_capabilities import (
    AsrCapability,
    AsrCapabilityRegistry,
    AsrTarget,
    CAPABILITY_UNAVAILABLE,
)
from src.clinic.config import clinic_mode_enabled
from src.clinic.parity_dispatch import CommandProblem, ParityDispatch, _problem
from src.clinic.voice_command_runtime import (
    APP_RIGHTS,
    Confirmation,
    VoiceCommandError,
    catalog_version,
    evaluate_voice_command,
)
from src.database import db

commands_blueprint = Blueprint("clinic_commands", __name__)
commands_blueprint.register_error_handler(CSRFError, lambda _error: _problem("FORBIDDEN"))


def _make_problem(code: str, title: str, status: int, detail: str | None = None):
    payload = {"type": "about:blank", "title": title, "status": status, "code": code}
    if detail:
        payload["detail"] = detail
    resp = jsonify(payload)
    resp.mimetype = "application/problem+json"
    return resp, status


def _require_actor() -> int | None:
    if not clinic_mode_enabled():
        abort(404)
    if not (
        current_user.is_authenticated
        and current_user.is_active
        and session.get("_user_id") == current_user.get_id()
    ):
        return None
    return current_user.id


def _require_csrf() -> bool:
    try:
        validate_csrf(request.headers.get("X-CSRF-Token"))
        return True
    except (CSRFError, ValidationError):
        return False


def _resolve_actor_pools(actor_id: int, requested_pools: list[str] | None = None) -> list[str]:
    try:
        if requested_pools is None:
            pool_id = choose_author_pool(actor_id)
            return [pool_id] if pool_id is not None else []
        member_pools = pool_policy.author_pool_ids(actor_id)
        return [pool_id for pool_id in requested_pools if pool_id in member_pools]
    except OperationalError as error:
        # Standalone SQLite vocabulary databases may lack the pool schema.
        if db.engine.dialect.name == "sqlite" and str(error.orig) in {
            "no such table: clinic_author_pool", "no such table: clinic_author_pool_member",
        }:
            return []
        raise


@commands_blueprint.route("/resources/commands", methods=["GET"])
@commands_blueprint.route("/api/clinic/v1/resources/commands", methods=["GET"])
def list_commands():
    actor_id = _require_actor()
    if actor_id is None:
        return _problem("UNAUTHORIZED")

    pool_ids = _resolve_actor_pools(actor_id, request.args.getlist("pool_id") or None)
    etag = catalog_version(actor_id, pool_ids)
    if request.headers.get("If-None-Match") == etag:
        return "", 304

    commands = vocabulary.get_effective_voice_commands(actor_id, pool_ids)
    limit = min(max(int(request.args.get("limit", 50)), 1), 100)
    items = [
        {"id": c["id"], "trigger_phrase": c["trigger_phrase"], "action_type": c["action_type"], "description": c.get("description")}
        for c in list(commands.values())[:limit]
    ]
    resp = jsonify({"revision": 1, "next_cursor": None, "items": items})
    resp.headers["ETag"] = etag
    return resp


@commands_blueprint.route("/resources/pronunciations", methods=["GET"])
@commands_blueprint.route("/api/clinic/v1/resources/pronunciations", methods=["GET"])
@commands_blueprint.route("/pronunciations/hints", methods=["GET"])
@commands_blueprint.route("/pronunciations/users/<int:target_user_id>/hints", methods=["GET"])
def list_pronunciations(target_user_id: int | None = None):
    actor_id = _require_actor()
    if actor_id is None:
        return _problem("UNAUTHORIZED")
    if target_user_id is not None and target_user_id != actor_id:
        return _problem("FORBIDDEN")

    pool_ids = _resolve_actor_pools(actor_id, request.args.getlist("pool_id") or None)
    hints = vocabulary.get_effective_pronunciation_hints(actor_id, pool_ids)
    items = [
        {"id": h["id"], "written_form": h["written_form"], "phonetic_hint": h["phonetic_hint"],
         "sounds_like": h.get("sounds_like"), "notes": h.get("notes"), "source": h.get("source", "user"), "revision_token": str(h["id"])}
        for h in hints.values()
    ]
    raw_hash = hashlib.sha256(json.dumps([item["id"] for item in items]).encode()).hexdigest()
    etag = f'"{raw_hash}"'
    if request.headers.get("If-None-Match") in (etag, raw_hash):
        return "", 304

    resp = jsonify({"items": items, "has_more": False, "next_cursor": None})
    resp.headers["ETag"] = etag
    return resp


@commands_blueprint.route("/pronunciations/commands", methods=["POST"])
@commands_blueprint.route("/api/clinic/v1/pronunciations/commands", methods=["POST"])
def save_pronunciation_hint_endpoint():
    actor_id = _require_actor()
    if actor_id is None:
        return _problem("UNAUTHORIZED")
    if not _require_csrf():
        return _problem("FORBIDDEN")

    body = request.get_json(silent=True) or {}
    if body.get("command") != "save_pronunciation_hint":
        return _problem("INVALID_PAYLOAD")

    req_id, payload = body.get("request_id"), body.get("payload", {})
    st, sid, w, ph = payload.get("scope_type"), payload.get("scope_id"), payload.get("written_form"), payload.get("phonetic_hint")
    if not w or not ph or st not in ("user", "author_pool"):
        return _problem("INVALID_PAYLOAD")

    if st == "user":
        if sid != str(actor_id):
            return _problem("FORBIDDEN")
        hint = vocabulary.add_pronunciation_hint("user", w, ph, sounds_like=payload.get("sounds_like"), user_id=actor_id)
    else:
        if sid not in _resolve_actor_pools(actor_id, [sid]):
            return _problem("FORBIDDEN")
        hint = vocabulary.add_pronunciation_hint("pool", w, ph, sounds_like=payload.get("sounds_like"), pool_id=sid)

    db.session.commit()
    return jsonify({"request_id": req_id or str(uuid.uuid4()), "hint_id": str(hint.id), "revision_token": str(hint.id), "status": "committed"}), 200


@commands_blueprint.route("/api/clinic/v1/pronunciations/capabilities", methods=["GET"])
@commands_blueprint.route("/training/capabilities", methods=["GET"])
def get_pronunciation_capabilities():
    if _require_actor() is None:
        return _problem("UNAUTHORIZED")

    registry: AsrCapabilityRegistry = current_app.extensions.get("clinic_capability_registry") or AsrCapabilityRegistry()
    target = AsrTarget(
        base_url="http://127.0.0.1:9000",
        engine=request.args.get("engine", "local-engine"),
        engine_revision="e1",
        model=request.args.get("model", "local-model"),
        model_revision="m1",
        parameters_json='{"language":"de"}',
    )
    is_proven = (registry.check(AsrCapability.PRONUNCIATION.value, target) != CAPABILITY_UNAVAILABLE) or registry.training_proven(target)
    if not is_proven:
        return jsonify({"training_proven": False, "capability": CAPABILITY_UNAVAILABLE, "adaptation_state": "capability_unavailable", "message": "Aussprachetraining nicht verfügbar"}), 200
    return jsonify({"training_proven": True, "capability": "runtime_passed", "adaptation_state": "succeeded"}), 200


@commands_blueprint.route("/api/clinic/v1/commands/execute", methods=["POST"])
@commands_blueprint.route("/commands/execute", methods=["POST"])
def execute_command_endpoint():
    actor_id = _require_actor()
    if actor_id is None:
        return _problem("UNAUTHORIZED")
    if not _require_csrf():
        return _problem("FORBIDDEN")

    data = request.get_json(silent=True) or {}
    phrase, recording_id = data.get("phrase"), data.get("recording_id")
    expected_version, request_id = data.get("expected_version"), data.get("request_id") or str(uuid.uuid4())
    if not phrase or not isinstance(recording_id, int) or recording_id <= 0 or not expected_version:
        return _problem("INVALID_PAYLOAD")

    requested_pools = data.get("pool_ids")
    if requested_pools is not None and (not isinstance(requested_pools, list)
                                        or not all(isinstance(item, str) for item in requested_pools)):
        return _problem("INVALID_PAYLOAD")
    pool_ids = _resolve_actor_pools(actor_id, requested_pools)
    subject = policy.build_subject(actor_id)
    recording = policy.build_facts(recording_id, data.get("owner_id", actor_id))

    confirmations: dict[str, Confirmation] = {}
    for act, conf in (data.get("confirmations") or {}).items():
        if isinstance(conf, dict) and "confirmed_at" in conf:
            try:
                ct = datetime.fromisoformat(conf["confirmed_at"].replace("Z", "+00:00"))
                confirmations[act] = Confirmation(conf.get("request_id", request_id), conf.get("command_id", ""),
                                                  conf.get("catalog_version", expected_version), actor_id, recording_id, act, ct)
            except Exception:
                pass

    try:
        plan = evaluate_voice_command(
            phrase=phrase, user_id=actor_id, pool_ids=pool_ids, expected_version=expected_version, request_id=request_id,
            subject=subject, recording=recording, now=datetime.now(timezone.utc),
            lease_check=lambda: data.get("lease_held", True) is True,
            confirmations=confirmations,
            verify_confirmation=lambda it: (it.actor_id == actor_id and it.recording_id == recording_id and it.request_id == request_id),
        )
        return jsonify({
            "request_id": plan.request_id, "command_id": plan.command_id, "catalog_version": plan.catalog_version,
            "actor_id": plan.actor_id, "recording_id": plan.recording_id, "actions": [{"kind": a.kind, "value": a.value} for a in plan.actions],
        }), 200
    except VoiceCommandError as err:
        err_map = {
            "stale_catalog": (409, "REVISION_CONFLICT", "Versionskonflikt"),
            "confirmation_required": (409, "confirmation_required", "Bestätigung erforderlich"),
            "access_denied": (403, "POLICY_DENIED", "Aktion nicht zulässig"),
            "forbidden_action": (403, "POLICY_DENIED", "Aktion nicht zulässig"),
            "invalid_actor": (403, "POLICY_DENIED", "Aktion nicht zulässig"),
            "lease_required": (409, "lease_required", "Sperre erforderlich"),
            "unknown_command": (404, "unknown_command", f"Befehl '{phrase}' nicht gefunden"),
        }
        status, code, title = err_map.get(err.code, (400, err.code, f"Unzulässiges Makro: {err.code}"))
        return _make_problem(code, title, status)


def register_commands_adapters(dispatch: ParityDispatch, registry: AsrCapabilityRegistry | None = None) -> None:
    """Register parity command adapters for save_command and request_adaptation."""

    def authorize_save_command(actor_id: int, permission: str, payload: dict[str, Any]) -> str | None:
        if permission != "clinic.commands.manage":
            return None
        st, sid, phr = payload.get("scope_type"), payload.get("scope_id"), payload.get("phrase")
        if not phr or not sid:
            return None
        if st == "user" and str(sid) == str(actor_id):
            return f"command:user:{actor_id}:{phr}"
        if st == "author_pool" and sid in _resolve_actor_pools(actor_id, [sid]):
            return f"command:pool:{sid}:{phr}"
        return None

    def revision_save_command(target: str) -> int | None:
        parts = target.split(":", 3)
        if len(parts) != 4:
            return None
        _, scope, scope_id, phrase = parts
        cmd = vocabulary.ClinicVoiceCommand.query.filter_by(
            scope=scope, user_id=int(scope_id) if scope == "user" else None,
            pool_id=scope_id if scope == "pool" else None, trigger_phrase=phrase,
        ).first()
        return 1 if cmd is not None else None

    def execute_save_command(actor_id: int, target: str, envelope: dict[str, Any]) -> dict[str, Any]:
        p = envelope["payload"]
        st, sid, phr, aids = p["scope_type"], p["scope_id"], p["phrase"], p["action_ids"]
        act_type = "app_command" if len(aids) == 1 and aids[0] in APP_RIGHTS else ("insert_text" if len(aids) == 1 else "macro")
        act_payload = aids[0] if len(aids) == 1 else json.dumps(aids)
        scope = "user" if st == "user" else "pool"
        cmd = vocabulary.add_voice_command(
            scope=scope, trigger_phrase=phr, action_type=act_type, action_payload=act_payload,
            user_id=actor_id if scope == "user" else None, pool_id=sid if scope == "pool" else None,
        )
        db.session.commit()
        return {"request_id": envelope["request_id"], "command_id": str(cmd.id), "status": "committed", "revision": envelope.get("expected_revision", 0) + 1}

    dispatch.register("save_command", authorize=authorize_save_command, revision=revision_save_command, execute=execute_save_command)

    def authorize_request_adaptation(actor_id: int, permission: str, payload: dict[str, Any]) -> str | None:
        return f"adaptation:{actor_id}:{payload['profile_revision_id']}" if permission == "clinic.asr.adapt" and payload.get("profile_revision_id") else None

    def execute_request_adaptation(actor_id: int, target: str, envelope: dict[str, Any]) -> dict[str, Any]:
        reg = registry or current_app.extensions.get("clinic_capability_registry") or AsrCapabilityRegistry()
        target_obj = AsrTarget("http://127.0.0.1:9000", "local-engine", "e1", "local-model", "m1", '{"language":"de"}')
        if envelope["payload"].get("capability") == "pronunciation_training":
            if not reg.training_proven(target_obj) or reg.check("training", target_obj) == CAPABILITY_UNAVAILABLE:
                raise CommandProblem("CAPABILITY_UNAVAILABLE")
        return {"request_id": envelope["request_id"], "command_id": str(uuid.uuid4()), "status": "queued", "revision": envelope.get("expected_revision", 0) + 1}

    dispatch.register("request_adaptation", authorize=authorize_request_adaptation, revision=lambda _: None, execute=execute_request_adaptation)
