"""Connect consented speaker embeddings to author suggestions.

Thin layer over ``speaker_client`` (embedding), ``voice_consent`` (consent and
storage) and ``author_suggestion`` (bands, short-audio rejection).

* Only users whose own voice consent is active are candidates. Patients and
  speaker identities are never candidates and no profile is ever created for them.
* Candidates are compared only within the embedding space of the model that
  produced the query embedding. Vectors of other spaces (or the legacy space)
  are ignored.
* Consent is rechecked after the slow embedding step. Enrolment is bound to the
  consent version read before processing, so a withdrawal during processing
  prevents a late enrolment. A suggestion is only published for users whose
  consent is unchanged and still active; others are dropped and the ranking is
  recomputed.
* The result is a suggestion only. A human confirms the author.
"""

from __future__ import annotations

import logging
import math
import struct

from src.clinic import author_suggestion, speaker_client, voice_consent

logger = logging.getLogger(__name__)

_MAX_RECHECKS = 3


class SpeakerPipelineError(Exception):
    """The pipeline step failed before it changed state."""

    def __init__(self, code: str, message: str | None = None):
        self.code = code
        super().__init__(message or code)


def _consent_version(user_id: int) -> int:
    history = voice_consent.consent_history(user_id)
    return history[-1]["id"] if history else 0


def _embed(wav_path):
    try:
        return speaker_client.embed_with_space(wav_path)
    except speaker_client.SpeakerServiceError as exc:
        logger.warning("Speaker service failure: %s", exc)
        raise SpeakerPipelineError("service_unavailable", str(exc)) from exc


def enrol_own_voice(user_id: int, wav_path, *, expected_consent_id: int | None = None):
    """Embed ``wav_path`` and store it for ``user_id`` (the signed-in user, acting for themself).

    The consent version is read before the embedding call and bound to the
    store, so consent withdrawn in between raises ``VoiceConsentError("stale_consent")``.
    ``expected_consent_id`` lets a caller bind to an even earlier read.
    """
    if not voice_consent.has_active_consent(user_id):
        raise voice_consent.VoiceConsentError("consent_required", "no active voice profile consent")
    version = _consent_version(user_id) if expected_consent_id is None else expected_consent_id
    result = _embed(wav_path)
    ref = voice_consent.EmbeddingRef(
        vector=struct.pack(f"<{len(result.vector)}f", *result.vector),
        dimension=len(result.vector),
        space_id=result.space_id,
    )
    return voice_consent.enrol(user_id, ref, actor_user_id=user_id, expected_consent_id=version)


def _centroid(vectors: list[list[float]]) -> list[float] | None:
    """Mean of unit-normalised vectors; None when unusable."""
    acc = None
    for vec in vectors:
        norm = math.sqrt(sum(x * x for x in vec))
        if norm == 0.0 or not math.isfinite(norm):
            continue
        unit = [x / norm for x in vec]
        acc = unit if acc is None else [a + b for a, b in zip(acc, unit)]
    if acc is None or math.sqrt(sum(x * x for x in acc)) == 0.0:
        return None
    return acc


def _load_candidates(
    space_id: int, dimension: int, excluded: set[int], candidate_user_ids: set[int] | None = None,
) -> dict[int, tuple[int, list[float]]]:
    """Map user_id -> (consent version, centroid) for consented users in this space."""
    from src.clinic.voice_consent import ClinicVoiceEmbedding

    rows = ClinicVoiceEmbedding.query.filter_by(space_id=space_id, dimension=dimension)
    if candidate_user_ids is not None:
        rows = rows.filter(ClinicVoiceEmbedding.user_id.in_(candidate_user_ids))
    per_user: dict[int, list[list[float]]] = {}
    for row in rows:
        if row.user_id in excluded or len(row.embedding) != dimension * 4:
            continue
        per_user.setdefault(row.user_id, []).append(list(struct.unpack(f"<{dimension}f", row.embedding)))
    out = {}
    for user_id, vectors in per_user.items():
        if not voice_consent.has_active_consent(user_id):
            continue
        centroid = _centroid(vectors)
        if centroid is not None:
            out[user_id] = (_consent_version(user_id), centroid)
    return out


def suggest_for_embedding(
    result, speech_seconds: float, *, candidate_user_ids: set[int] | None = None,
) -> author_suggestion.AuthorSuggestion:
    """Suggest an author for an ``EmbeddingResult``. Never assigns."""
    excluded: set[int] = set()
    for _ in range(_MAX_RECHECKS):
        candidates = _load_candidates(result.space_id, len(result.vector), excluded, candidate_user_ids)
        suggestion = author_suggestion.suggest_author(
            result.vector, [(uid, vec) for uid, (_, vec) in candidates.items()],
            speech_seconds=speech_seconds,
        )
        # Recheck consent right before publishing; drop changed users and rank again.
        changed = {
            s.user_id for s in suggestion.suggestions
            if not voice_consent.has_active_consent(s.user_id) or _consent_version(s.user_id) != candidates[s.user_id][0]
        }
        if not changed:
            return suggestion
        excluded |= changed
    return author_suggestion.suggest_author(result.vector, [], speech_seconds=speech_seconds)


def suggest_author_for_wav(wav_path, speech_seconds: float) -> dict:
    """Embed a dictation WAV and return the JSON-ready suggestion (``requires_confirmation`` is True).

    Short speech is rejected before the speaker service is called.
    """
    if (isinstance(speech_seconds, (int, float)) and not isinstance(speech_seconds, bool)
            and math.isfinite(speech_seconds) and 0 <= speech_seconds < author_suggestion.MIN_SPEECH_SECONDS):
        return author_suggestion.suggest_author([1.0], [], speech_seconds=speech_seconds).to_dict()
    result = _embed(wav_path)
    return suggest_for_embedding(result, speech_seconds).to_dict()
