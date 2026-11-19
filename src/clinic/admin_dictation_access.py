"""Guarded admin access to dictation metadata and content.

This module is the narrow gate for the admin overview (w305) and for admin
direct audio/transcript opening (w306). It reuses the metadata query from
``src.clinic.admin_dictations`` and the central clinic policy from
``src.clinic.policy``; the only new behaviour is the admin role recheck and
the audit logging for content openings.

Active site administrators bypass the action-specific clinic policy for
content openings (owner decision W12, 2026-11-18). Every admin opening is
audit-logged with the existing event vocabulary; de-duplication is disabled
so each open is recorded.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from src.clinic import policy
from src.clinic.admin_dictations import AdminDictationsPage, query_dictations
from src.clinic.config import clinic_mode_enabled
from src.clinic.identity import ClinicAccountPolicy
from src.models import Recording, User
from src.services import audit


class AdminDictationAccessError(Exception):
    """The requested admin access cannot proceed. ``code`` is stable."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


# part -> (policy action for non-admins, audit event, audit details)
_CONTENT_ACTIONS = {
    "audio": ("play", "recording.media.played", None),
    "transcript": ("view", "recording.content.read", {"part": "transcript"}),
}


def _require_clinic() -> None:
    if not clinic_mode_enabled():
        raise AdminDictationAccessError("clinic_mode_off")


def _is_active_site_admin(session: Session, user: User) -> bool:
    """True for an authenticated, active site administrator.

    The current server-side role is what matters: a revoked admin
    (``User.is_admin`` but ``ClinicAccountPolicy.status != 'active'``) is
    rejected, as are statistics-only, team-admin-only, author and typist
    callers.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return False
    if not user.is_admin:
        return False
    account = session.get(ClinicAccountPolicy, user.id)
    return account is not None and account.status == "active"


def list_dictations(session: Session, user: User, spec) -> AdminDictationsPage:
    """Return one page of the all-user metadata list.

    The query is executed only for an authenticated, active site administrator.
    Anonymous, author, typist, statistics-only, team-admin-only and revoked-admin
    callers receive ``admin_only`` before the query is invoked.
    """
    _require_clinic()
    if not _is_active_site_admin(session, user):
        raise AdminDictationAccessError("admin_only")
    return query_dictations(session, spec)


def open_content(
    session: Session, user: User, recording_id: int, part: str
) -> tuple[policy.Decision, Recording | None]:
    """Decide an audio or transcript opening and audit-log an admin open.

    Active site administrators get ``Decision(True, 'admin_full')`` for any
    existing recording, with no confirmation step and no reason prompt. The
    opening is audit-logged with the normal event vocabulary
    (``recording.media.played`` for audio, ``recording.content.read`` with
    ``part='transcript'`` for transcript); de-duplication is disabled so every
    admin open produces a row.

    Non-admin callers keep the existing action-specific clinic policy unchanged.
    """
    decision, recording = _decide_content(session, user, recording_id, part)
    if decision.allowed and decision.reason == "admin_full":
        _, event, details = _CONTENT_ACTIONS[part]
        audit.record(
            event,
            recording=recording,
            details=details,
            actor_kind="session",
            actor_user_id=user.id,
        )
    return decision, recording


def _decide_content(
    session: Session, user: User, recording_id: int, part: str
) -> tuple[policy.Decision, Recording | None]:
    """Shared decision logic for ``open_content`` and ``can_open_content``; writes no audit row."""
    _require_clinic()
    if part not in _CONTENT_ACTIONS:
        raise AdminDictationAccessError("unknown_part")
    action = _CONTENT_ACTIONS[part][0]

    recording = session.get(Recording, recording_id)
    if recording is None:
        return policy.Decision(False, "not_found"), None

    if _is_active_site_admin(session, user):
        return policy.Decision(True, "admin_full"), recording

    subject = policy.build_subject(user.id, bool(user.is_admin))
    facts = policy.build_facts(recording_id, recording.user_id)
    return policy.decide(subject, action, facts), recording


def can_open_content(session: Session, user: User, recording_id: int, part: str) -> policy.Decision:
    """Decision-only variant of ``open_content``; no audit row is written."""
    decision, _ = _decide_content(session, user, recording_id, part)
    return decision
