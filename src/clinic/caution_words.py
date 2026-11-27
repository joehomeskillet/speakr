"""Caution words (PAR-VOC-004, W11): revision-bound hits from an authorized rule set.

Thin layer over the existing versioned marker records in ``correction``. No new table.

* A ``CautionRuleSet`` is plain input: pool, version, words, ``authorized``. It does not
  say who administers it; the caller decides authorization before it hands the set in.
  An unauthorized set is refused, never matched.
* ``inspect`` matches the words against the head transcript revision only. Each hit
  becomes one live ``INDEX`` marker anchored to that revision and offset, labelled
  ``caution|<pool>|<rule-set version>|<word>|<offset>``. A repeat creates nothing new.
* ``confirm`` is the existing soft delete of that marker (event log keeps actor and time).
* A confirmation is bound to the revision, rule-set version and pool in the label and
  anchor. An edit moves the head; hits on the new head are open again, and
  confirmations of a superseded revision or rule-set version count for nothing.
* Rule sets of another pool never produce or touch markers of this dictation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.clinic import correction, revisions
from src.clinic.correction import ClinicMarker, CorrectionError
from src.clinic.models import ClinicDictation, ClinicTranscriptRevision
from src.database import db

PREFIX = "caution"
_SEP = "|"


@dataclass(frozen=True)
class CautionRuleSet:
    pool_id: str
    version: str
    words: tuple[str, ...]
    authorized: bool = False


@dataclass(frozen=True)
class CautionHit:
    word: str
    offset: int
    marker_id: str


def find_hits(text: str, words) -> list[tuple[str, int]]:
    """Whole-word, case-insensitive matches as ``(word, offset)``, ordered by offset."""
    hits = []
    for word in sorted({w.strip() for w in words if isinstance(w, str) and w.strip() and _SEP not in w}):
        pattern = r"(?<!\w)" + re.escape(word) + r"(?!\w)"
        hits.extend((word, m.start()) for m in re.finditer(pattern, text, re.IGNORECASE))
    return sorted(hits, key=lambda hit: (hit[1], hit[0]))


def inspect(
    dictation: ClinicDictation,
    rules: CautionRuleSet,
    actor_user_id: int,
    *,
    audio_revision_id: str | None = None,
    lease_generation: int | None = None,
) -> list[CautionHit]:
    """Create the missing open markers for the head revision and return its open hits."""
    if not rules.authorized:
        raise CorrectionError("caution rule set is not authorized")
    if not _same_pool(dictation, rules):
        return []
    head = revisions.current_revision(dictation)
    if head is None:
        return []
    known = {m.label for m in _rows(dictation, head, rules)}
    for word, offset in find_hits(head.body, rules.words):
        label = _label(rules, word, offset)
        if label in known:
            continue
        link = correction.link_at_text(dictation, head, offset)
        correction.add_marker(
            dictation, 0 if link is None else link.audio_start_ms, actor_user_id,
            marker_type=correction.MARKER_INDEX, label=label,
            anchor_revision=head, anchor_offset=offset,
            audio_revision_id=audio_revision_id, lease_generation=lease_generation,
        )
    return open_hits(dictation, rules)


def open_hits(dictation: ClinicDictation, rules: CautionRuleSet) -> list[CautionHit]:
    """Live caution markers of this pool and rule-set version on the current head."""
    head = revisions.current_revision(dictation)
    if head is None or not _same_pool(dictation, rules):
        return []
    return [_hit(m) for m in _rows(dictation, head, rules) if m.deleted_at is None]


def confirmed_hits(dictation: ClinicDictation, rules: CautionRuleSet) -> list[CautionHit]:
    """Confirmed hits that are still valid: same head revision, pool and rule-set version."""
    head = revisions.current_revision(dictation)
    if head is None or not _same_pool(dictation, rules):
        return []
    return [_hit(m) for m in _rows(dictation, head, rules) if m.deleted_at is not None]


def reserved_label(label: str | None) -> bool:
    return bool(label and label.startswith(PREFIX + _SEP))


def is_review_marker(marker: ClinicMarker) -> bool:
    if (marker.marker_type != correction.MARKER_INDEX or marker.anchor_revision_id is None
            or marker.anchor_offset is None or not reserved_label(marker.label)):
        return False
    parts = marker.label.split(_SEP)
    return len(parts) == 5 and bool(parts[1] and parts[3]) and parts[4] == str(marker.anchor_offset)


def open_review_hits(dictation: ClinicDictation, head: ClinicTranscriptRevision | None) -> list[CautionHit]:
    """Open hits already materialized on this revision for the dictation's pool."""
    return [_hit(marker) for marker in _open_review_rows(dictation, head)]


def confirm_review_hit(dictation: ClinicDictation, head: ClinicTranscriptRevision, marker_id: str,
                       actor_user_id: int, *, lease_generation: int) -> None:
    """Confirm a materialized hit after the caller locks the dictation and checks its head."""
    marker = next((row for row in _open_review_rows(dictation, head) if row.id == marker_id), None)
    if marker is None:
        raise correction.CorrectionConflict("no open caution hit for this revision and pool")
    _confirm_marker(marker, actor_user_id, lease_generation)


def confirm(
    dictation: ClinicDictation,
    rules: CautionRuleSet,
    marker_id: str,
    actor_user_id: int,
    *,
    lease_generation: int | None = None,
) -> CautionHit:
    """Confirm one open hit. A hit of an older revision, other pool or version is refused."""
    if not rules.authorized:
        raise CorrectionError("caution rule set is not authorized")
    head = revisions.current_revision(dictation)
    marker = next((m for m in _rows(dictation, head, rules)
                   if m.id == marker_id and m.deleted_at is None), None) if head else None
    if marker is None or not _same_pool(dictation, rules):
        raise CorrectionError("no open caution hit for this revision and rule set")
    _confirm_marker(marker, actor_user_id, lease_generation)
    return _hit(marker)


def _confirm_marker(marker: ClinicMarker, actor_user_id: int, lease_generation: int | None) -> None:
    correction.delete_marker(
        marker, actor_user_id,
        expected_version=marker.version, lease_generation=lease_generation,
    )


def _same_pool(dictation: ClinicDictation, rules: CautionRuleSet) -> bool:
    return bool(rules.pool_id) and dictation.pool_id == rules.pool_id


def _label(rules: CautionRuleSet, word: str, offset: int) -> str:
    return _SEP.join((PREFIX, rules.pool_id, rules.version, word, str(offset)))


def _open_review_rows(dictation: ClinicDictation, head: ClinicTranscriptRevision | None) -> list[ClinicMarker]:
    if head is None or not dictation.pool_id:
        return []
    return [marker for marker in _rows(dictation, head)
            if marker.deleted_at is None and is_review_marker(marker)]


def _rows(
    dictation: ClinicDictation, head: ClinicTranscriptRevision, rules: CautionRuleSet | None = None,
) -> list[ClinicMarker]:
    """Caution markers anchored to ``head``, optionally bound to a specific rule set."""
    scope = (rules.pool_id, rules.version) if rules is not None else (dictation.pool_id,)
    stem = _SEP.join((PREFIX, *scope, ""))
    rows = db.session.query(ClinicMarker).filter(
        ClinicMarker.dictation_id == dictation.id,
        ClinicMarker.marker_type == correction.MARKER_INDEX,
        ClinicMarker.anchor_revision_id == head.id,
    ).order_by(ClinicMarker.seq).all()
    return [m for m in rows if (m.label or "").startswith(stem)]


def _hit(marker: ClinicMarker) -> CautionHit:
    word = marker.label.split(_SEP, 4)[3]
    return CautionHit(word=word, offset=marker.anchor_offset, marker_id=marker.id)
