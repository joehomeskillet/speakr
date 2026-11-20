"""Word timings of a local ASR run, stored as the first alignment revision.

The ASR result of one transcript revision carries word times in seconds on its
stored segments. They are mapped onto the word tokens of that revision by exact
token text, in order, and stored as one alignment revision with source kind
``asr`` on the current audio revision. A word that does not match a token
never gives it a time. Segment times are never used as word times, and no time
is estimated. Helpers flush, never commit.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import namedtuple

from src.clinic import alignment as _alignment
from src.clinic import alignment_tokens as _tokens
from src.clinic.audio_revisions import current_audio_revision, get_revision_edl
from src.clinic.revisions import normalise_text
from src.database import db

NO_AUDIO_REVISION = "NO_AUDIO_REVISION"
NO_WORD_TIMINGS = "NO_WORD_TIMINGS"
NO_VERIFIED_TIMING = "NO_VERIFIED_TIMING"
LOOKAHEAD = 3

AsrAlignmentResult = namedtuple("AsrAlignmentResult", "row reason")


def _seconds_to_ms(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return round(value * 1000)


def asr_words(stored_transcription) -> list:
    """Words of the stored segments as (text, start_ms, end_ms); times may be None."""
    data = stored_transcription
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except ValueError:
            return []
    if not isinstance(data, list):
        return []
    words = []
    for segment in data:
        raw = segment.get("words") if isinstance(segment, dict) else None
        if not isinstance(raw, list):
            continue
        for word in raw:
            if not isinstance(word, dict):
                continue
            text = word.get("word")
            if text is None:
                text = word.get("text")
            if not isinstance(text, str):
                continue
            text = normalise_text(text).strip()
            if text:
                words.append((text, _seconds_to_ms(word.get("start")), _seconds_to_ms(word.get("end"))))
    return words


def _next_match(tokens, words, i, j):
    """(token skip, word skip) of the nearest exact match from (i, j), or None."""
    if words[j][0] == tokens[i][3]:
        return 0, 0
    for step in range(1, LOOKAHEAD + 1):
        if j + step < len(words) and words[j + step][0] == tokens[i][3]:
            return 0, step
        if i + step < len(tokens) and tokens[i + step][3] == words[j][0]:
            return step, 0
    return None


def _map(tokens, words, duration_ms) -> dict:
    """token index to (start_ms, end_ms) for every token that gets a verified time."""
    times = {}
    previous_end = 0
    i = j = 0
    while i < len(tokens) and j < len(words):
        found = _next_match(tokens, words, i, j)
        if found is None:
            i += 1
            continue
        i += found[0]
        j += found[1]
        _, start, end = words[j]
        if start is not None and end is not None and previous_end <= start < end <= duration_ms and start >= 0:
            times[i] = (start, end)
            previous_end = end
        i += 1
        j += 1
    return times


def _operation_key(dictation, audio_revision, revision, tokens_row, model_ref) -> str:
    payload = json.dumps(
        ["asr-ingest", dictation.recording_id, audio_revision.id, revision.id,
         tokens_row.document_hash, tokens_row.tokenization_version, model_ref],
        ensure_ascii=False, separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def store_asr_alignment(dictation, revision, stored_transcription, model_ref) -> AsrAlignmentResult:
    """Store the ASR word timings of ``revision`` as an alignment revision, once.

    Returns the row, or no row with reason ``NO_AUDIO_REVISION`` when the
    dictation has no audio revision. A repeat call returns the stored row.
    """
    audio = current_audio_revision(dictation)
    if audio is None:
        return AsrAlignmentResult(None, NO_AUDIO_REVISION)
    tokens_row = _tokens.tokens_for(dictation, revision)
    key = _operation_key(dictation, audio, revision, tokens_row, model_ref)
    existing = db.session.query(_alignment.ClinicAlignmentRevision).filter_by(
        dictation_id=dictation.id, operation_key=key).first()
    if existing is not None:
        return AsrAlignmentResult(existing, None)
    duration = get_revision_edl(dictation, audio).total_duration_ms
    engine, _, model_revision = model_ref.rpartition(":")
    if not engine:
        engine = model_revision = model_ref
    row = _alignment.ClinicAlignmentRevision(
        dictation_id=dictation.id, audio_revision_id=audio.id, transcript_revision_id=revision.id,
        document_hash=tokens_row.document_hash, tokenization_version=tokens_row.tokenization_version,
        state=_alignment.STATE_PENDING, source_kind="asr", engine=engine, model_revision=model_revision,
        audio_duration_ms=duration, items=[], quality=_alignment._zero_quality(),
        operation_key=key, operation_state="pending", created_at=_alignment._utcnow(),
    )
    db.session.add(row)
    db.session.flush()
    word_tokens = _alignment._word_tokens(tokens_row.document)
    words = asr_words(stored_transcription)
    times = _map(word_tokens, words, duration) if words else {}
    items = []
    for index, (word_id, node_id, token_index, text) in enumerate(word_tokens):
        span = times.get(index)
        items.append({
            "word_id": word_id, "text_node_id": node_id, "token_index": token_index,
            "displayed_text": text, "state": "valid" if span else "unaligned",
            "start_ms": span[0] if span else None, "end_ms": span[1] if span else None,
            "reason_code": None if span else NO_VERIFIED_TIMING,
        })
    _alignment.complete(row, items, [] if words else [NO_WORD_TIMINGS])
    return AsrAlignmentResult(row, None)
