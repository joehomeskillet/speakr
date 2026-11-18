"""Frozen output intents and per-target result transitions.

Wiring must read current bindings and permissions server-side and atomically
persist each transition against the dictation's ledger. Commit UNKNOWN before
calling an adapter so a lost response or interrupted worker cannot cause a
blind resend. A lookup proving absence is only conclusive after the sender
can no longer deliver that claim. Use existing adapters and receipts as evidence.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace

from .kis import LOOKUP_ABSENT, LOOKUP_CONFIRMED, LOOKUP_UNKNOWN, payload_hash
from .models import ClinicAudioRevision, ClinicContextRevision, ClinicDictation, ClinicTranscriptRevision
from .release import active_release

PENDING = "pending"
SUCCEEDED = "succeeded"
FAILED = "failed"
UNKNOWN = "UNKNOWN"


class OutputAttemptError(ValueError):
    """The output intent or transition was refused."""


@dataclass(frozen=True)
class OutputSource:
    dictation_id: str
    audio_revision_id: str
    text_revision_id: str
    context_revision_id: str
    audio_hash: str
    text_hash: str
    context_hash: str
    context_complete: bool


def bind_output_source(
    dictation: ClinicDictation,
    audio: ClinicAudioRevision,
    text: ClinicTranscriptRevision,
    context: ClinicContextRevision,
) -> OutputSource:
    """Capture the server-resolved revisions without retaining patient identifiers."""
    if not dictation.id or any(
        not row.id or row.dictation_id != dictation.id for row in (audio, text, context)
    ):
        raise OutputAttemptError("output revisions must belong to the same dictation")
    binding = {
        "source_system": context.source_system,
        "tenant_scope": context.tenant_scope,
        "case_ref": context.case_ref,
        "patient_ref": context.patient_ref,
    }
    return OutputSource(
        dictation.id, audio.id, text.id, context.id, audio.content_hash,
        text.normalised_text_hash, payload_hash(binding), all(binding.values()),
    )


@dataclass(frozen=True)
class OutputAttempt:
    source: OutputSource
    channel: str
    target: str
    mode: str
    release_id: str | None
    format_settings: tuple[tuple[str, str], ...]
    document_format_hash: str
    payload_hash: str
    idempotency_key: str
    status: str = PENDING
    generation: int = 0
    cancelled: bool = False


def _check_output(
    dictation: ClinicDictation, expected: OutputSource, current: OutputSource,
    mode: str, release_id: str | None, allowed: bool,
) -> None:
    if allowed is not True:
        raise OutputAttemptError("output not authorised")
    if expected != current or current.dictation_id != dictation.id:
        raise OutputAttemptError("stale output revision")
    if mode in ("working_copy", "corrected") and release_id is None:
        return
    if mode in ("accepted", "kis_draft") and release_id and current.context_complete:
        release = active_release(dictation)
        if release is not None and (
            release.id, release.audio_revision_id, release.transcript_revision_id,
            release.context_revision_id, release.text_hash,
        ) == (
            release_id, current.audio_revision_id, current.text_revision_id,
            current.context_revision_id, current.text_hash,
        ):
            return
    raise OutputAttemptError("output mode requires a matching release binding")


def freeze_output_attempts(
    dictation: ClinicDictation, expected: OutputSource, current: OutputSource, *,
    actor_user_id: int, request_id: str, allowed: bool, mode: str,
    targets: Mapping[tuple[str, str], Mapping[str, str]], release_id: str | None = None,
) -> tuple[OutputAttempt, ...]:
    """Freeze (channel, target alias) entries with renderer/format settings as hash input.

    Replays retain the key; consumers must reject a stored key with another payload hash.
    """
    _check_output(dictation, expected, current, mode, release_id, allowed)
    if not request_id or not targets:
        raise OutputAttemptError("output requires a request ID and at least one target")
    attempts = []
    for (channel, target), format_settings in targets.items():
        if not channel.strip() or not target.strip() or not format_settings.get("format"):
            raise OutputAttemptError("output target and format settings are required")
        if channel.casefold() == "kis" and mode != "kis_draft":
            raise OutputAttemptError("KIS output requires a bound KIS draft")
        key = payload_hash({"actor": actor_user_id, "request": request_id,
                            "dictation": expected.dictation_id, "channel": channel, "target": target})
        settings = tuple(sorted(format_settings.items()))
        format_hash = payload_hash(dict(settings))
        digest = payload_hash({"key": key, "source": asdict(expected), "mode": mode,
                               "release": release_id, "document_format_hash": format_hash})
        attempts.append(OutputAttempt(expected, channel, target, mode, release_id, settings, format_hash, digest, key))
    return tuple(attempts)


def begin_output(
    ledger: Mapping[str, OutputAttempt], key: str, dictation: ClinicDictation,
    current: OutputSource, *, allowed: bool,
) -> OutputAttempt:
    """Claim a selected target using all live ledger entries for this dictation."""
    attempt = ledger[key]
    _check_output(dictation, attempt.source, current, attempt.mode, attempt.release_id, allowed)
    if any(row.status == UNKNOWN and row.source.dictation_id == dictation.id for row in ledger.values()):
        raise OutputAttemptError("reconcile unresolved output before delivery")
    if attempt.cancelled or attempt.status not in (PENDING, FAILED):
        raise OutputAttemptError("output cannot be started")
    return replace(attempt, status=UNKNOWN, generation=attempt.generation + 1)


def cancel_output(attempt: OutputAttempt) -> OutputAttempt:
    if attempt.status != PENDING:
        raise OutputAttemptError("only a pending output can be cancelled")
    return replace(attempt, status=FAILED, cancelled=True)


def record_output_result(
    attempt: OutputAttempt, claim: OutputAttempt, evidence: str,
) -> OutputAttempt:
    """Apply trusted delivery or lookup evidence for this exact target and generation."""
    if attempt != claim or attempt.status != UNKNOWN:
        raise OutputAttemptError("output evidence does not match the unresolved claim")
    if evidence == LOOKUP_CONFIRMED:
        return replace(attempt, status=SUCCEEDED)
    if evidence == LOOKUP_ABSENT:
        return replace(attempt, status=FAILED)
    if evidence == LOOKUP_UNKNOWN:
        return attempt
    raise OutputAttemptError("unrecognised output evidence")
