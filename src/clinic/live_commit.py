"""Commit a final local ASR phrase with its original audio and visible revisions."""

from __future__ import annotations

import hashlib
import json
import wave
from datetime import datetime
from io import BytesIO

from src.clinic import audio_revisions, ingest, live_phrases, revisions
from src.clinic.models import ClinicDictation, ClinicOriginal
from src.database import db


def commit_asr_phrase(
    store: ingest.IngestService, *, dictation_id: str, session_id: str,
    user_id: int, client_id: str | None, now: datetime, generation: int,
    sequence: int, event_id: str, audio_start_ms: int, audio_end_ms: int, audio: bytes,
    expected_audio_revision_id: str | None, expected_text_revision_id: str | None,
    text: str, model_ref: str,
) -> live_phrases.PhraseReceipt:
    """Append a final phrase to an authorized draft and commit before acknowledging it.

    ``audio`` is the captured mono PCM16 WAV. The caller supplies the final text
    and model from local ASR and the two draft heads observed before recognition.
    Both heads are ``None`` for a blank draft. Replays use the original inputs.
    A human head stops new ASR appends while leaving earlier receipts replayable.
    """
    if (not isinstance(text, str) or not text.strip()
            or not isinstance(model_ref, str) or not model_ref.strip() or len(model_ref) > 255):
        raise live_phrases.PhraseError('invalid_text')
    text = revisions.normalise_text(text)
    identity = [expected_audio_revision_id, expected_text_revision_id, text, model_ref]
    fingerprint = hashlib.sha256(json.dumps(identity, separators=(',', ':')).encode()).hexdigest()

    def stage_revisions(storage_key: str, audio_hash: str) -> tuple[str, str, str]:
        dictation = db.session.get(ClinicDictation, dictation_id)
        audio_head = audio_revisions.current_audio_revision(dictation)
        text_head = revisions.current_revision(dictation)
        if ((audio_head.id if audio_head else None) != expected_audio_revision_id
                or (text_head.id if text_head else None) != expected_text_revision_id
                or (text_head is not None and text_head.kind == revisions.KIND_HUMAN)):
            raise live_phrases.PhraseError('stale_version')

        duration = audio_end_ms - audio_start_ms
        _check_wav(audio, duration)
        if audio_head is None:
            if text_head is not None or ClinicOriginal.query.filter_by(dictation_id=dictation_id).first():
                raise live_phrases.PhraseError('stale_version')
            key = f'live:{session_id}:{sequence}'
            original = ClinicOriginal(
                dictation_id=dictation_id, content_hash=audio_hash, size_bytes=len(audio),
                storage_key=storage_key, origin_ref=key, created_at=now,
                receipt=ingest._receipt_text(key, audio_hash, len(audio), 'live', key, storage_key),
            )
            db.session.add(original)
            db.session.flush()
            audio_revision = audio_revisions.init_audio_revision(dictation, original, duration)
        else:
            snapshot = audio_revisions.ClinicAudioRevisionSnapshot.query.filter_by(
                dictation_id=dictation_id, audio_revision_id=audio_head.id).one()
            original = db.session.get(ClinicOriginal, snapshot.original_id)
            segment = audio_revisions.create_segment(dictation, storage_key, duration, audio_hash)
            audio_revision = audio_revisions.apply_insert(dictation, snapshot.total_duration_ms, segment)

        body = text_head.body + '\n' + text if text_head and text_head.body else text
        transcript = revisions.add_asr_revision(dictation, original, body, model_ref, parent_revision=text_head)
        return audio_revision.id, transcript.id, transcript.normalised_text_hash

    return live_phrases.store_phrase(
        store, dictation_id=dictation_id, session_id=session_id, user_id=user_id, client_id=client_id,
        now=now, generation=generation, sequence=sequence, event_id=event_id,
        audio_start_ms=audio_start_ms, audio_end_ms=audio_end_ms, audio=audio,
        _revision_commit=(fingerprint, stage_revisions),
    )


def _check_wav(audio: bytes, duration_ms: int) -> None:
    try:
        with wave.open(BytesIO(audio), 'rb') as wav:
            rate, frames = wav.getframerate(), wav.getnframes()
            if ((wav.getnchannels(), wav.getsampwidth()) != (1, 2)
                    or frames * 1000 // rate != duration_ms
                    or len(wav.readframes(frames)) != frames * 2):
                raise live_phrases.PhraseError('invalid_audio')
    except (wave.Error, EOFError) as exc:
        raise live_phrases.PhraseError('invalid_audio') from exc
