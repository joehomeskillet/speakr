"""Database guards for correction and vocabulary decision histories.

Marker events are already in the shared append-only registry. Vocabulary
review decisions are not, so this installer registers that one table and
then calls the shared installer. Marker rows, correction state, review
items and vocabulary entries stay out of the registry and remain writable
through their existing services. Undo stays ``vocabulary.undo_decision``:
it appends a compensating row and does not rewrite the original decision.
"""

from __future__ import annotations

from sqlalchemy.engine import Engine

from src.clinic.append_only_guards import APPEND_ONLY_TABLES, install_append_only_guards
from src.clinic.correction import ClinicCorrectionState, ClinicMarker, ClinicMarkerEvent
from src.clinic.vocabulary import (
    ClinicVocabularyEntry,
    ClinicVocabularyReviewDecision,
    ClinicVocabularyReviewItem,
)

HISTORY_TABLES: tuple[str, ...] = (
    ClinicMarkerEvent.__tablename__,
    ClinicVocabularyReviewDecision.__tablename__,
)

MUTABLE_TABLES: tuple[str, ...] = (
    ClinicMarker.__tablename__,
    ClinicCorrectionState.__tablename__,
    ClinicVocabularyReviewItem.__tablename__,
    ClinicVocabularyEntry.__tablename__,
)

_DECISION_LABEL = "clinic vocabulary review decision"


def install_correction_db_guards(engine: Engine) -> None:
    """Reject UPDATE and DELETE on marker events and vocabulary decisions.

    Idempotent. Tables that do not exist yet are skipped by the shared
    installer, so the call is safe before ``create_all``. w077 calls this
    after the tables exist.
    """
    marker_table = ClinicMarkerEvent.__tablename__
    if marker_table not in APPEND_ONLY_TABLES:
        raise RuntimeError("clinic marker event is not in the append-only registry")
    frozen = [name for name in MUTABLE_TABLES if name in APPEND_ONLY_TABLES]
    if frozen:
        raise RuntimeError(
            f"mutable correction tables must stay writable: {', '.join(frozen)}"
        )
    # The shared installer only reads this registry, and this package cannot
    # edit that module. Registration lasts for the process.
    APPEND_ONLY_TABLES.setdefault(
        ClinicVocabularyReviewDecision.__tablename__,
        _DECISION_LABEL,
    )
    install_append_only_guards(engine)
