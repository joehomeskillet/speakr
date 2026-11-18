"""Suggest an author for a dictation by comparing voice embeddings.

The service only ever *suggests*. It never assigns an author: the caller shows
the suggestion (a single name or two names to choose from) and a human
confirms. The main use is device imports without an author (clarification
list).

Consent interface
-----------------
The caller passes ``candidates`` as ``(user_id, embedding)`` pairs and must
already have filtered them to users with an *active voice consent*. This module
does not import the voice-consent module and cannot check consent itself; a
candidate in the list is treated as consented. Embeddings are plain sequences
of floats (any length, but all of the same length as the query embedding).

Bands
-----
The thresholds below are calibrated on synthetic data only and are not
validated against real clinic voices. Re-calibrate before relying on them.

* ``BAND_SINGLE``: best cosine score >= ``SINGLE_MIN_SCORE`` and the gap to the
  runner-up >= ``SINGLE_MIN_GAP`` (or there is no runner-up). One suggestion.
* ``BAND_CHOICE``: best score >= ``CHOICE_MIN_SCORE`` but not a single hit
  (score below ``SINGLE_MIN_SCORE`` or gap too small). The top two candidates
  that reach ``CHOICE_MIN_SCORE`` are offered for selection.
* ``BAND_NONE``: best score < ``CHOICE_MIN_SCORE``, no candidates, or the
  dictation contains less than ``MIN_SPEECH_SECONDS`` of speech (embeddings of
  short dictations are unstable). No suggestion.

Pure Python: no database, no Flask, no clock, no I/O.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass


SINGLE_MIN_SCORE = 0.75
SINGLE_MIN_GAP = 0.10
CHOICE_MIN_SCORE = 0.60
MIN_SPEECH_SECONDS = 3.0

BAND_SINGLE = "single"
BAND_CHOICE = "choice"
BAND_NONE = "none"

REASON_SINGLE = "single_match"
REASON_LOW_SCORE_OR_GAP = "ambiguous_match"
REASON_BELOW_THRESHOLD = "below_threshold"
REASON_NO_CANDIDATES = "no_candidates"
REASON_SHORT_SPEECH = "speech_too_short"

# Absorbs float noise so that exact boundary values (0.75, 0.10, 0.60) count as
# reaching the boundary.
_EPS = 1e-9


class AuthorSuggestionError(ValueError):
    """The input was rejected before a suggestion was computed."""

    def __init__(self, code: str, message: str | None = None):
        self.code = code
        super().__init__(message or code)


@dataclass(frozen=True)
class ScoredCandidate:
    """One candidate with its cosine score."""

    user_id: int
    score: float

    def to_dict(self) -> dict:
        return {"user_id": self.user_id, "score": self.score}


@dataclass(frozen=True)
class AuthorSuggestion:
    """Outcome of a suggestion run. Never an assignment."""

    band: str
    reason: str
    suggestions: tuple[ScoredCandidate, ...]
    top_score: float | None
    gap: float | None
    speech_seconds: float

    def to_dict(self) -> dict:
        """JSON-serialisable form for the clarification-list UI."""
        return {
            "band": self.band,
            "reason": self.reason,
            "suggestions": [s.to_dict() for s in self.suggestions],
            "top_score": self.top_score,
            "gap": self.gap,
            "speech_seconds": self.speech_seconds,
            "requires_confirmation": True,
        }


def _validated(vector: Sequence[float], what: str) -> list[float]:
    try:
        values = [float(x) for x in vector]
    except (TypeError, ValueError) as exc:
        raise AuthorSuggestionError("invalid_embedding", f"{what} must be numeric") from exc
    if not values:
        raise AuthorSuggestionError("invalid_embedding", f"{what} must not be empty")
    if any(not math.isfinite(x) for x in values):
        raise AuthorSuggestionError("invalid_embedding", f"{what} must be finite")
    if math.sqrt(sum(x * x for x in values)) == 0.0:
        raise AuthorSuggestionError("invalid_embedding", f"{what} must not be a zero vector")
    return values


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity of two non-zero, equally long vectors."""
    va = _validated(a, "embedding")
    vb = _validated(b, "embedding")
    if len(va) != len(vb):
        raise AuthorSuggestionError("dimension_mismatch", "embedding lengths differ")
    dot = sum(x * y for x, y in zip(va, vb))
    return dot / (math.sqrt(sum(x * x for x in va)) * math.sqrt(sum(y * y for y in vb)))


def classify_scores(ranked: Sequence[ScoredCandidate]) -> tuple[str, str, tuple[ScoredCandidate, ...]]:
    """Map a ranked (descending) score list to ``(band, reason, suggestions)``."""
    if not ranked:
        return BAND_NONE, REASON_NO_CANDIDATES, ()
    best = ranked[0]
    if best.score < CHOICE_MIN_SCORE - _EPS:
        return BAND_NONE, REASON_BELOW_THRESHOLD, ()
    gap_ok = len(ranked) == 1 or best.score - ranked[1].score >= SINGLE_MIN_GAP - _EPS
    if best.score >= SINGLE_MIN_SCORE - _EPS and gap_ok:
        return BAND_SINGLE, REASON_SINGLE, (best,)
    top_two = tuple(c for c in ranked[:2] if c.score >= CHOICE_MIN_SCORE - _EPS)
    return BAND_CHOICE, REASON_LOW_SCORE_OR_GAP, top_two


def suggest_author(
    embedding: Sequence[float],
    candidates: Iterable[tuple[int, Sequence[float]]],
    *,
    speech_seconds: float,
) -> AuthorSuggestion:
    """Suggest the author of a dictation. Suggest only, never assign.

    Args:
        embedding: voice embedding of the dictation.
        candidates: ``(user_id, embedding)`` pairs, already restricted by the
            caller to users with active voice consent.
        speech_seconds: duration of the speech part of the dictation. Below
            ``MIN_SPEECH_SECONDS`` no suggestion is made.

    Ranking is by score descending, ties by ``user_id`` ascending, so the
    result does not depend on candidate order.

    Raises:
        AuthorSuggestionError: invalid or mismatching embeddings, duplicate
            user ids, or an invalid ``speech_seconds``.
    """
    if isinstance(speech_seconds, bool) or not isinstance(speech_seconds, (int, float)):
        raise AuthorSuggestionError("invalid_speech_seconds", "speech_seconds must be a number")
    if not math.isfinite(speech_seconds) or speech_seconds < 0:
        raise AuthorSuggestionError("invalid_speech_seconds", "speech_seconds must be >= 0")
    query = _validated(embedding, "query embedding")
    pairs = list(candidates)
    seen: set[int] = set()
    scored: list[ScoredCandidate] = []
    for user_id, vector in pairs:
        if user_id in seen:
            raise AuthorSuggestionError("duplicate_candidate", f"duplicate user_id {user_id}")
        seen.add(user_id)
        scored.append(ScoredCandidate(user_id, cosine_similarity(query, vector)))
    ranked = sorted(scored, key=lambda c: (-c.score, c.user_id))

    top = ranked[0].score if ranked else None
    gap = ranked[0].score - ranked[1].score if len(ranked) > 1 else None
    if speech_seconds < MIN_SPEECH_SECONDS:
        return AuthorSuggestion(BAND_NONE, REASON_SHORT_SPEECH, (), top, gap, float(speech_seconds))
    band, reason, suggestions = classify_scores(ranked)
    return AuthorSuggestion(band, reason, suggestions, top, gap, float(speech_seconds))
