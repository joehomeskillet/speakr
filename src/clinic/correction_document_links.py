from __future__ import annotations

from bisect import bisect_right

from src.clinic import correction as corr
from src.clinic.models import ClinicDictation, ClinicTranscriptRevision
from src.clinic.revisions import normalise_text
from src.clinic.richtext import MAX_SEGMENTS, CanonicalDocument, Segment
from src.clinic.rich_text_doc import RichTextError
from src.database import db

Span = tuple[int, int]
Transfer = tuple[int, int, int]
_MOVE_KEYS = {'segment_id', 'old_start', 'old_end', 'new_start', 'new_end'}


def _validate_moves(moves: object, old: str, new: str,
                    before: dict[str, Span], after: dict[str, Span]) -> None:
    if not isinstance(moves, list) or len(moves) > MAX_SEGMENTS:
        raise RichTextError('invalid_move_map')
    seen = set()
    for move in moves:
        if not isinstance(move, dict) or move.keys() != _MOVE_KEYS:
            raise RichTextError('invalid_move_map')
        key = move['segment_id']
        a, b, c, d = (move[k] for k in ('old_start', 'old_end', 'new_start', 'new_end'))
        if (not isinstance(key, str) or key in seen
                or any(type(n) is not int for n in (a, b, c, d))
                or before.get(key) != (a, b) or after.get(key) != (c, d)
                or a == b or old[a:b] != new[c:d]):
            raise RichTextError('invalid_move_map')
        seen.add(key)


def _link_offsets(raw: str, spans: dict[str, Span]) -> dict[str, Span]:
    normalized = normalise_text(raw)
    if normalized == raw:
        return spans
    parts: list[str] = []
    mapped = {}
    cursor = offset = 0
    for key, (start, end) in spans.items():
        gap, text = normalise_text(raw[cursor:start]), normalise_text(raw[start:end])
        offset += len(gap)
        mapped[key] = (offset, offset + len(text))
        parts.extend((gap, text))
        offset += len(text)
        cursor = end
    parts.append(normalise_text(raw[cursor:]))
    return mapped if ''.join(parts) == normalized else {}


def _anchored_ids(document: CanonicalDocument) -> set[str]:
    return {run.id for node in document.nodes for run in node.runs
            if isinstance(run, Segment) and run.audio_anchor_id}


def plan_document_links(previous: CanonicalDocument, document: CanonicalDocument,
                        moves: object) -> tuple[list[Transfer], bool]:
    """Return old ranges and their displacement, and whether run provenance changed."""
    old, before = previous.project()
    new, after = document.project()
    _validate_moves(moves, old, new, before, after)
    cleared = _anchored_ids(previous) - _anchored_ids(document)
    kept = [key for key, (a, b) in before.items() if key in after and key not in cleared
            and old[a:b] == new[after[key][0]:after[key][1]]]
    changed = len(kept) != len(before) or before != after
    old_offsets, new_offsets = _link_offsets(old, before), _link_offsets(new, after)
    transfers: list[Transfer] = []
    for key in kept:
        if key not in old_offsets or key not in new_offsets:
            continue
        start, end = old_offsets[key]
        target = new_offsets[key][0]
        if start == end:
            continue
        delta = target - start
        if transfers and transfers[-1][1:] == (start, delta):
            transfers[-1] = (transfers[-1][0], end, delta)
        else:
            transfers.append((start, end, delta))
    return transfers, changed


def carry_document_links(dictation: ClinicDictation, old: ClinicTranscriptRevision,
                         new: ClinicTranscriptRevision, transfers: list[Transfer]) -> int:
    """Copy only intersections backed by unchanged runs; audio remains segment-level."""
    seq = corr._next_seq(corr.ClinicTextAudioLink, dictation.id)
    ends = [end for _, end, _ in transfers]
    count = 0
    for link in corr.effective_links(dictation, old):
        i = bisect_right(ends, link.text_start)
        while i < len(transfers):
            start, end, delta = transfers[i]
            if start >= link.text_end:
                break
            db.session.add(corr.ClinicTextAudioLink(
                dictation_id=dictation.id, transcript_revision_id=new.id,
                seq=seq + count, origin=corr.ORIGIN_AUTO,
                text_start=max(start, link.text_start) + delta,
                text_end=min(end, link.text_end) + delta,
                audio_start_ms=link.audio_start_ms, audio_end_ms=link.audio_end_ms,
            ))
            count += 1
            i += 1
    db.session.flush()
    return count
