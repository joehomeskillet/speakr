"""Clinic follow-up job handlers for the real app (CLINIC_MODE only).

``register_clinic_job_handlers`` registers one handler per ``CLINIC_JOBS`` type, so queued
follow-ups are claimed instead of waiting forever. Every handler fails closed with a permanent
``ClinicJobRefused`` (reason in the job error, no retry loop) when its binding, target or
trusted context is missing. Nothing leaves the database except a KIS delivery, and that goes
only through the configured adapter (MockKIS on staging, DisabledAdapter in production).

- ``clinic_finalize``: re-reads the durable release the job names and checks its binding.
  No external effect; the release itself was committed before the job was enqueued.
- ``clinic_output``: delivers the named release to the KIS target through ``kis.deliver``
  with the same ledger key as the export route (at most one send per release). Refused without
  target ``kis``, without an enabled adapter, or without a trusted context. An UNKNOWN outcome
  is returned to the queue, which records it as unresolved and never resends.
- ``clinic_notify``: in-app notice through ``clinic_notify_handler`` with the real
  visibility check (central pool policy; an active admin sees everything, W12).
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.clinic import finalize_gates, kis, pool_policy, policy
from src.clinic.models import ClinicContextRevision, ClinicDictation, ClinicRelease
from src.database import db
from src.services import job_queue as jq

OUTPUT_TARGET = "kis"
_BOUND = (
    ("audio_revision_id", "audio_revision_id"),
    ("transcript_revision_id", "transcript_revision_id"),
    ("context_revision_id", "context_revision_id"),
    ("text_hash", "text_hash"),
)


def _release_of(params: dict, correlation_id: str) -> ClinicRelease:
    release_id = params.get(jq.CLINIC_OPERATION_PARAMS[0])
    release = db.session.get(ClinicRelease, release_id) if isinstance(release_id, str) and release_id else None
    if release is None:
        raise jq.ClinicJobRefused(f"[{correlation_id}] release not found")
    for param, column in _BOUND:
        if param in params and params[param] != getattr(release, column):
            raise jq.ClinicJobRefused(f"[{correlation_id}] release binding differs from the job ({param})")
    return release


def finalize_handler():
    def handle(params, correlation_id):
        _release_of(params, correlation_id)
    return handle


def output_handler(adapter_for):
    def handle(params, correlation_id):
        if params.get("target") != OUTPUT_TARGET:
            raise jq.ClinicJobRefused(f"[{correlation_id}] no configured output target; nothing was sent")
        adapter = adapter_for()
        if not adapter.enabled:
            raise jq.ClinicJobRefused(f"[{correlation_id}] KIS adapter not configured; nothing was sent")
        release = _release_of(params, correlation_id)
        context = db.session.get(ClinicContextRevision, release.context_revision_id)
        if context is None or not finalize_gates.trusted_context(context, adapter):
            raise jq.ClinicJobRefused(f"[{correlation_id}] context is not trusted; nothing was sent")
        result = kis.deliver(release, context, adapter, f"export:{release.id}", datetime.now(timezone.utc))
        if result.outcome == kis.DELIVERED:
            return None
        if result.outcome == kis.UNKNOWN:
            return kis.UNKNOWN
        raise jq.ClinicJobRefused(f"[{correlation_id}] KIS {result.outcome}: {result.reason}")
    return handle


def can_see(user_id: int, item) -> bool:
    """Whether the user may see the dictation of a workflow row (pool policy; admin sees all)."""
    from src.models import User

    dictation = db.session.get(ClinicDictation, item.id)
    user = db.session.get(User, user_id)
    if dictation is None or user is None:
        return False
    subject = policy.build_subject(user_id, is_admin=bool(user.is_admin))
    return pool_policy.decide(subject, "view", dictation).allowed


def register_clinic_job_handlers(flask_app) -> None:
    from src.clinic import routes_output

    handlers = {
        "clinic_finalize": finalize_handler(),
        "clinic_output": output_handler(lambda: routes_output.configured_adapter(flask_app)),
        "clinic_notify": jq.clinic_notify_handler(can_see),
    }
    assert set(handlers) == set(jq.CLINIC_JOBS), "every CLINIC_JOBS type needs a handler"
    for job_type, handler in handlers.items():
        jq.register_clinic_handler(job_type, handler)
