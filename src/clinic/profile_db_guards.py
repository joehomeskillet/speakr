"""Database guards for ASR profile versions and training history.

ORM listeners on these rows already refuse UPDATE and DELETE. Raw SQL and
bulk writes are rejected by the shared append-only installer. The profile
head stays out of that registry so a versioned save can still update it
and append a history row.
"""

from __future__ import annotations

from sqlalchemy.engine import Engine

from src.clinic.append_only_guards import APPEND_ONLY_TABLES, install_append_only_guards
from src.clinic.asr_profiles import (
    ClinicAsrProfile,
    ClinicAsrProfileVersion,
    ClinicAsrTrainingRun,
)

PROFILE_HISTORY_TABLES: tuple[str, ...] = (
    ClinicAsrProfileVersion.__tablename__,
    ClinicAsrTrainingRun.__tablename__,
)

PROFILE_HEAD_TABLE = ClinicAsrProfile.__tablename__


def install_profile_db_guards(engine: Engine) -> None:
    """Reject UPDATE and DELETE on profile versions and training runs.

    Idempotent. Tables that do not exist yet are skipped by the shared
    installer, so the call is safe before ``create_all``.
    """
    missing = [name for name in PROFILE_HISTORY_TABLES if name not in APPEND_ONLY_TABLES]
    if missing:
        joined = ", ".join(missing)
        raise RuntimeError(
            f"ASR profile history is not in the append-only registry: {joined}"
        )
    if PROFILE_HEAD_TABLE in APPEND_ONLY_TABLES:
        raise RuntimeError("ASR profile head must stay writable")
    install_append_only_guards(engine)
