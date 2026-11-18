"""Negative lexicon (PAR-VOC-003): suppressed words applied through a proven engine capability.

Thin layer. Precedence comes from ``vocabulary.get_effective_vocabulary`` (user beats
pool, earlier pool beats later pool, user reactivation beats pool suppression). This
module only restricts the pools to the author's current membership, asks the capability
registry whether the target engine proved ``negative_lexicon`` and hands the term list to
an injected engine adapter. Without proof the result is CAPABILITY_UNAVAILABLE and the
parity item stays open; saving a list in the UI is never evidence of an engine effect.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from sqlalchemy.orm import Session

from src.clinic.asr_capabilities import (
    CAPABILITY_UNAVAILABLE,
    AsrCapability,
    AsrCapabilityRegistry,
    AsrTarget,
    CapabilityEvidence,
)
from src.clinic.pools import ClinicAuthorPoolMember
from src.clinic.vocabulary import STATUS_SUPPRESSED, get_effective_vocabulary

APPLIED = "applied"
FAILED = "failed"
FORBIDDEN = "forbidden"
STALE_VERSION = "stale_version"
PARITY_ITEM = "PAR-VOC-003"


@dataclass(frozen=True)
class NegativeLexiconRun:
    """What a recognition run records about the negative lexicon it used."""

    status: str  # applied | failed | forbidden | stale_version | CAPABILITY_UNAVAILABLE
    terms: tuple[str, ...] = ()
    lexicon_version: str = ""
    engine_revision: str = ""
    model_revision: str = ""
    evidence_ref: str = ""
    error: str = ""
    parity_item_open: bool = True  # closes only via a measured audio pair, never here


def member_pool_ids(user_id: int, pool_ids: Sequence[str], session: Session) -> list[str]:
    """Keep the requested pools (order preserved) the author currently belongs to."""
    current = {
        row.pool_id
        for row in session.query(ClinicAuthorPoolMember).filter_by(user_id=user_id).all()
    }
    return [pid for pid in pool_ids if pid in current]


def resolve_negative_terms(
    user_id: int, pool_ids: Sequence[str], session: Session
) -> tuple[str, ...]:
    """Effective suppressed written forms, sorted, with current pool membership only."""
    effective = get_effective_vocabulary(
        user_id, member_pool_ids(user_id, pool_ids, session),
        include_suppressed=True, session=session,
    )
    return tuple(sorted(
        (row["written_form"] for row in effective.values() if row["status"] == STATUS_SUPPRESSED),
        key=str.casefold,
    ))


def lexicon_version(terms: Sequence[str]) -> str:
    """Stable content hash; changes whenever the effective list changes."""
    payload = json.dumps(sorted(terms, key=str.casefold), ensure_ascii=False)
    return "nl1:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def apply_negative_lexicon(
    *,
    actor_id: int,
    user_id: int,
    pool_ids: Sequence[str],
    session: Session,
    registry: AsrCapabilityRegistry,
    target: AsrTarget,
    engine_apply: Callable[[tuple[str, ...]], None],
    expected_version: str | None = None,
    retries: int = 1,
) -> NegativeLexiconRun:
    """Resolve, check capability, apply once (``retries`` extra attempts), record versions."""
    if actor_id != user_id:
        return NegativeLexiconRun(status=FORBIDDEN)
    terms = resolve_negative_terms(user_id, pool_ids, session)
    version = lexicon_version(terms)
    if expected_version is not None and expected_version != version:
        return NegativeLexiconRun(status=STALE_VERSION, terms=terms, lexicon_version=version)
    proof = registry.check(AsrCapability.NEGATIVE_LEXICON.value, target)
    if not isinstance(proof, CapabilityEvidence):
        return NegativeLexiconRun(status=CAPABILITY_UNAVAILABLE, terms=terms, lexicon_version=version)
    error = ""
    for _ in range(max(0, retries) + 1):
        try:
            engine_apply(terms)
        except Exception as exc:  # engine adapter failure; nothing counts as applied
            error = type(exc).__name__
            continue
        return NegativeLexiconRun(
            status=APPLIED, terms=terms, lexicon_version=version,
            engine_revision=target.engine_revision, model_revision=target.model_revision,
            evidence_ref=proof.evidence_ref,
        )
    return NegativeLexiconRun(status=FAILED, terms=terms, lexicon_version=version, error=error)


def recognition_changed(
    recognize: Callable[[bytes, tuple[str, ...]], str],
    audio: bytes,
    terms: Sequence[str],
) -> bool:
    """Controlled-audio check: the same audio must yield different text once terms apply."""
    return recognize(audio, ()) != recognize(audio, tuple(terms))
