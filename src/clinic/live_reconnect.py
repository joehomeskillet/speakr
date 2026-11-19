"""Resume a live session from the last durable phrase (PAR-LIVE-003, service only).

``reconnect`` reopens the holder's interrupted session through
``live_session.recover`` and returns the last committed sequence, audio offset
and text reference. Clients report the generation and the last sequence they
saw acknowledged. A generation below the durable one, or an acknowledgement
beyond durable storage, is rejected and the session is left unchanged. The
reply carries only the missing tail (phrases after the client's
acknowledgement), in sequence order, so a retry or a takeover from another tab
neither loses nor repeats a final phrase. Taking over assigns the new
``client_id``; the old tab is then fenced out by ``live_phrases.store_phrase``.

No Flask routes and no clock: the caller passes ``now`` and commits.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from src.clinic import live_session
from src.clinic.live_phrases import ClinicLivePhrase
from src.database import db

STALE_GENERATION = "stale_generation"
ACK_BEYOND_DURABLE = "ack_beyond_durable"


class ReconnectError(ValueError):
    """The client state is inconsistent with durable storage (``reason`` is stable)."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class TailPhrase:
    sequence: int
    generation: int
    event_id: str
    audio_start_ms: int
    audio_end_ms: int
    audio_storage_key: str
    audio_sha256: str
    transcript_revision_id: str
    text_hash: str


@dataclass(frozen=True)
class ReconnectState:
    session_id: str
    generation: int
    sequence: int
    audio_offset_ms: int
    text_ref: str | None
    tail: tuple[TailPhrase, ...]


def reconnect(
    dictation_id: str,
    user_id: int,
    now: datetime,
    client_id: str | None = None,
    client_generation: int = 1,
    client_ack_seq: int = 0,
    timeout: float | None = None,
) -> ReconnectState:
    """Reopen the holder's session and return the durable position plus the missing tail.

    Raises ``LiveSessionError`` (``no_session``, ``active_session``, ...) from
    ``live_session.recover`` and ``ReconnectError`` for a stale generation or
    an acknowledgement beyond durable storage; both leave the session untouched.
    """
    for label, value, minimum in (("client_generation", client_generation, 1),
                                  ("client_ack_seq", client_ack_seq, 0)):
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ValueError(label + " must be an integer >= " + str(minimum))
    savepoint = db.session.begin_nested()
    try:
        session, point = live_session.recover(dictation_id, user_id, now, client_id, timeout)
        last = (ClinicLivePhrase.query.filter_by(session_id=session.id)
                .order_by(ClinicLivePhrase.sequence.desc()).first())
        generation = last.generation if last else 1
        if client_generation < generation:
            raise ReconnectError(STALE_GENERATION)
        if client_ack_seq > point.ack_seq:
            raise ReconnectError(ACK_BEYOND_DURABLE)
        rows = (ClinicLivePhrase.query
                .filter(ClinicLivePhrase.session_id == session.id,
                        ClinicLivePhrase.sequence > client_ack_seq,
                        ClinicLivePhrase.sequence <= point.ack_seq)
                .order_by(ClinicLivePhrase.sequence).all())
    except Exception:
        savepoint.rollback()
        raise
    savepoint.commit()
    return ReconnectState(
        session.id, generation, point.ack_seq, point.audio_offset_ms, point.text_ref,
        tuple(TailPhrase(r.sequence, r.generation, r.event_id, r.audio_start_ms,
                         r.audio_end_ms, r.audio_storage_key, r.audio_sha256,
                         r.transcript_revision_id, r.text_hash) for r in rows))
