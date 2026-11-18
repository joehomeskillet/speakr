"""Validated, ordered web voice-command execution (PAR-VOC-008).

Vocabulary definitions use ``insert_text``/``template`` for literal text,
``app_command`` for a listed in-page action, and ``macro`` for a JSON array
of existing trigger phrases. Hooks name an in-page action or a trigger.
Expansion order is before hook, command body, after hook, recursively.

The caller supplies current policy facts, an authoritative lease check and a
server-side confirmation verifier. The batch handler must apply the returned
plan atomically and idempotently, with release/send delegated to their guarded
services. No OS or browser automation is performed here.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from src.database import db

from . import policy, vocabulary

MAX_DEPTH = 8
MAX_STEPS = 64
MAX_TEXT_CHARS = 8192
CONFIRMATION_AGE = timedelta(seconds=60)

# Values are the recording-specific actions checked by the central policy.
APP_RIGHTS = {
    "focus_editor": "edit",
    "open_editor": "edit",
    "insert_marker": "edit",
    "next_marker": "view",
    "previous_marker": "view",
    "send_dictation": "export",
    "release_dictation": "release",
}
SENSITIVE = frozenset({"send_dictation", "release_dictation"})
WRITE_RIGHTS = frozenset({"edit", "export", "release"})


class VoiceCommandError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class VoiceAction:
    kind: str  # insert_text or app_command
    value: str


@dataclass(frozen=True)
class VoiceCommandPlan:
    request_id: str
    command_id: str
    catalog_version: str
    actor_id: int
    recording_id: int
    actions: tuple[VoiceAction, ...]


@dataclass(frozen=True)
class Confirmation:
    """Server-verified explicit confirmation bound to one request and action."""

    request_id: str
    command_id: str
    catalog_version: str
    actor_id: int
    recording_id: int
    action: str
    confirmed_at: datetime


def _catalog(user_id: int, pool_ids: list[str] | None, session: Session | None):
    # Preserve vocabulary.py's existing user-over-pool precedence and filtering.
    entries = vocabulary.get_effective_voice_commands(user_id, pool_ids, session=session)
    # A disabled personal command must not silently expose a pooled namesake.
    s = session if session is not None else db.session
    blocked = {
        phrase.strip().casefold()
        for (phrase,) in s.query(vocabulary.ClinicVoiceCommand.trigger_phrase).filter_by(
            scope="user", user_id=user_id,
        ).filter(
            vocabulary.ClinicVoiceCommand.status != vocabulary.COMMAND_STATUS_ACTIVE,
        ).all()
    }
    by_phrase = {}
    for entry in entries.values():
        phrase = entry["trigger_phrase"].strip().casefold()
        if phrase in blocked:
            continue
        if phrase in by_phrase:
            raise VoiceCommandError("ambiguous_trigger")
        by_phrase[phrase] = entry
    version_data = json.dumps(
        sorted(by_phrase.items()), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    version = hashlib.sha256(version_data.encode("utf-8")).hexdigest()
    return by_phrase, version


def catalog_version(
    user_id: int, pool_ids: list[str] | None = None, *, session: Session | None = None,
) -> str:
    """ETag for the effective user/pool command definitions."""
    return _catalog(user_id, pool_ids, session)[1]


def evaluate_voice_command(
    phrase: str, *, user_id: int, pool_ids: list[str] | None = None,
    session: Session | None = None, expected_version: str, request_id: str,
    subject: policy.Subject, recording: policy.RecordingFacts, now: datetime,
    lease_check: Callable[[], bool] | None = None,
    confirmations: Mapping[str, Confirmation] | None = None,
    verify_confirmation: Callable[[Confirmation], bool] | None = None,
) -> VoiceCommandPlan:
    """Validate the complete expansion before returning any executable intent.

    The HTTP service must fetch fresh Subject/RecordingFacts and pool scope, use
    ``lease.assert_current`` for the lease callback, and verify confirmations
    against server-owned state. The API consumer then checks dictation revision
    preconditions and applies the whole batch in one transaction.
    """
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise VoiceCommandError("invalid_time")
    if not isinstance(subject, policy.Subject) or subject.user_id != user_id:
        raise VoiceCommandError("invalid_actor")
    if not isinstance(recording, policy.RecordingFacts):
        raise VoiceCommandError("invalid_recording")
    if not isinstance(request_id, str) or not 0 < len(request_id) <= 128:
        raise VoiceCommandError("invalid_request_id")

    commands, version = _catalog(user_id, pool_ids, session)
    if expected_version != version:
        raise VoiceCommandError("stale_catalog")
    if not isinstance(phrase, str) or not phrase.strip():
        raise VoiceCommandError("unknown_command")
    root = commands.get(phrase.strip().casefold())
    if root is None:
        raise VoiceCommandError("unknown_command")

    steps: list[VoiceAction] = []
    text_length = 0

    def emit(kind: str, value: str) -> None:
        nonlocal text_length
        if len(steps) >= MAX_STEPS:
            raise VoiceCommandError("too_many_steps")
        if kind == "insert_text":
            text_length += len(value)
            if text_length > MAX_TEXT_CHARS:
                raise VoiceCommandError("text_too_long")
        steps.append(VoiceAction(kind, value))

    def reference(name: str, stack: frozenset[str], depth: int) -> None:
        if not isinstance(name, str) or not name.strip():
            raise VoiceCommandError("invalid_reference")
        if name in APP_RIGHTS:
            emit("app_command", name)
            return
        target = commands.get(name.strip().casefold())
        if target is None:
            raise VoiceCommandError("unknown_reference")
        expand(target, stack, depth)

    def expand(entry: dict[str, Any], stack: frozenset[str], depth: int) -> None:
        if depth > MAX_DEPTH:
            raise VoiceCommandError("macro_too_deep")
        identity = entry["id"]
        if identity in stack:
            raise VoiceCommandError("macro_cycle")
        stack = stack | {identity}

        if entry["before_hook"]:
            reference(entry["before_hook"], stack, depth + 1)
        kind, value = entry["action_type"], entry["action_payload"]
        if kind in ("insert_text", "template"):
            if not isinstance(value, str) or not value or len(value) > MAX_TEXT_CHARS:
                raise VoiceCommandError("invalid_text")
            emit("insert_text", value)
        elif kind == "app_command":
            if value not in APP_RIGHTS:
                raise VoiceCommandError("forbidden_action")
            emit("app_command", value)
        elif kind == "macro":
            if not isinstance(value, str) or len(value) > MAX_TEXT_CHARS:
                raise VoiceCommandError("invalid_macro")
            try:
                members = json.loads(value)
            except (ValueError, RecursionError) as error:
                raise VoiceCommandError("invalid_macro") from error
            if not isinstance(members, list) or not members or len(members) > MAX_STEPS:
                raise VoiceCommandError("invalid_macro")
            for member in members:
                reference(member, stack, depth + 1)
        else:
            raise VoiceCommandError("forbidden_action")
        if entry["after_hook"]:
            reference(entry["after_hook"], stack, depth + 1)

    expand(root, frozenset(), 0)
    sensitive_steps = [step.value for step in steps if step.value in SENSITIVE]
    if len(sensitive_steps) != len(set(sensitive_steps)):
        raise VoiceCommandError("repeated_sensitive_action")
    rights = {
        "edit" if step.kind == "insert_text" else APP_RIGHTS[step.value]
        for step in steps
    }
    for right in sorted(rights):
        if not policy.decide(subject, right, recording).allowed:
            raise VoiceCommandError("access_denied")
    if rights & WRITE_RIGHTS and (lease_check is None or lease_check() is not True):
        raise VoiceCommandError("lease_required")

    now_utc = now.astimezone(timezone.utc)
    for action in sorted(sensitive_steps):
        confirmation = (confirmations or {}).get(action)
        if not isinstance(confirmation, Confirmation) or (
            confirmation.request_id != request_id
            or confirmation.command_id != root["id"]
            or confirmation.catalog_version != version
            or confirmation.actor_id != user_id
            or confirmation.recording_id != recording.recording_id
            or confirmation.action != action
            or not isinstance(confirmation.confirmed_at, datetime)
            or confirmation.confirmed_at.tzinfo is None
            or confirmation.confirmed_at.utcoffset() is None
            or not timedelta(0) <= now_utc - confirmation.confirmed_at.astimezone(timezone.utc) <= CONFIRMATION_AGE
            or verify_confirmation is None
            or verify_confirmation(confirmation) is not True
        ):
            raise VoiceCommandError("confirmation_required")

    return VoiceCommandPlan(
        request_id, root["id"], version, user_id, recording.recording_id, tuple(steps),
    )


def execute_voice_command(
    phrase: str, *, apply: Callable[[VoiceCommandPlan], Any], **kwargs: Any,
) -> Any:
    """Hand one fully validated batch to the caller's atomic/idempotent handler."""
    return apply(evaluate_voice_command(phrase, **kwargs))
