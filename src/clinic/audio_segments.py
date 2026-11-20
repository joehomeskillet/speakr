"""Validated browser audio sources in the existing clinic store and receipt transaction."""

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import os
import tempfile
from uuid import uuid4

from flask import abort, current_app

from src.clinic import audio_revisions, decoder, guard, startup, workflow
from src.database import db
from src.services.storage.locator import local_path_from_key

MAX_INPUT_BYTES = 32 * 1024 * 1024
MAX_OUTPUT_BYTES = 64 * 1024 * 1024


def assert_writable(dictation, actor, generation):
    """Caller holds the dictation lock through the receipt commit."""
    guard.assert_dictation_edit_allowed(dictation.id, actor, generation, datetime.now(timezone.utc))
    flow = db.session.get(workflow.ClinicWorkflowDictation, dictation.id,
                          populate_existing=True, with_for_update=True)
    if flow is not None:
        if flow.status in (workflow.DONE, workflow.TO_ARCHIVE, workflow.TO_DELETE):
            abort(409)
        if flow.status == workflow.DRAFT and dictation.author_user_id != actor:
            abort(403)


@contextmanager
def validated_upload(upload):
    """Bound input and decoded size; derive format, duration and checksum ourselves."""
    root = Path(startup.originals_root(current_app)).resolve()
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.clinic-segment-', dir=root) as directory:
        source = Path(directory) / 'capture'
        size = 0
        with source.open('xb') as handle:
            while block := upload.stream.read(1024 * 1024):
                size += len(block)
                if size > MAX_INPUT_BYTES:
                    abort(413)
                handle.write(block)
        with source.open('rb') as handle:
            if decoder.detect_container(handle.read(16)) not in ('wav', 'webm', 'ogg', 'mp4'):
                abort(415)
        result = decoder.decode_to_wav(source, directory, max_output_bytes=MAX_OUTPUT_BYTES)
        if result.status in (decoder.DECODER_MISSING, decoder.TIMEOUT):
            abort(503)
        if result.status != decoder.OK or not result.output_path:
            abort(415)
        duration_ms = int(result.duration_s * 1000)
        if duration_ms < 1:
            abort(400)
        yield root, result, duration_ms


def persist_segment(dictation, root, decoded, duration_ms):
    """Publish a private immutable WAV before committing its database source row."""
    key = f'audio-segments/{dictation.id}/{uuid4()}.wav'
    path = Path(local_path_from_key(str(root), key))
    path.parent.mkdir(parents=True, exist_ok=True)
    source = Path(decoded.output_path)
    with source.open('rb') as handle:
        os.fsync(handle.fileno())
    source.chmod(0o400)
    os.link(source, path)  # Exclusive publication: never replace existing audio.
    try:
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        segment = audio_revisions.create_segment(dictation, key, duration_ms, decoded.output_sha256)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return segment, path
