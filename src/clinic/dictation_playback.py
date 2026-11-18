"""Browser-playable derivative for DS2/DSS dictation recordings.

Browsers cannot play DS2/DSS. The stored original stays byte-identical and is
the export/download source; ``/audio/<id>`` serves a separate MP3 stored next
to it (``<stem>.playback.mp3``). The MP3 is made from the same decoder output
the ASR path uses, at ingest (reusing the ASR WAV) or lazily on first playback
for recordings stored before this existed.
"""
from __future__ import annotations

import logging
import os
import shutil
import tempfile
from typing import Optional

from src.clinic import dictation_upload

logger = logging.getLogger(__name__)

PLAYBACK_SUFFIX = '.playback.mp3'
PLAYBACK_MIME = 'audio/mpeg'


def playback_path(original) -> str:
    return os.path.splitext(str(original))[0] + PLAYBACK_SUFFIX


def ensure_playback(original, wav=None) -> Optional[str]:
    """Path of the playable MP3 for a DS2/DSS original, creating it if missing.

    Returns None when ``original`` is not a dictation container or the
    derivative cannot be made (the caller then serves the original). ``wav`` is
    an already decoded WAV to reuse instead of decoding again.
    """
    original = str(original)
    derived = playback_path(original)
    if os.path.exists(derived) and os.path.getsize(derived) > 0:
        return derived
    if not dictation_upload.sniff_container(original):
        return None
    work = tempfile.mkdtemp(prefix='.playback-', dir=os.path.dirname(original) or '.')
    try:
        from src.utils.ffmpeg_utils import convert_to_mp3
        source_wav = wav or dictation_upload.decode_for_asr(original, work)
        tmp_mp3 = convert_to_mp3(source_wav, os.path.join(work, 'out.mp3'),
                                 bitrate='64k', sample_rate='16000', channels=1)
        os.replace(tmp_mp3, derived)
        return derived
    except Exception as exc:
        logger.warning('Playback derivative failed: %s', type(exc).__name__)
        return None
    finally:
        shutil.rmtree(work, ignore_errors=True)


def playable_locator(storage, audio_path: str) -> str:
    """Storage locator of what a player should read: the MP3 derivative for a
    local DS2/DSS original (created if missing), else ``audio_path`` itself."""
    try:
        local = storage.resolve_local_filesystem_path(audio_path)
    except Exception:
        return audio_path
    if ensure_playback(local):
        return playback_path(audio_path)
    return audio_path


def remove_playback(original) -> None:
    """Delete the derivative together with its recording (best effort)."""
    try:
        os.remove(playback_path(original))
    except OSError:
        pass
