"""Offline clinic maintenance for upstream speaker identities.

The caller authenticates the operator, stops writers and other purge invocations,
and supplies only a trusted, dedicated upstream sample directory, if one exists.
Use a fresh session. A retry previews the remaining work before applying again.
"""

import hashlib
import json
import os
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select, update

from src.clinic.config import clinic_mode_enabled
from src.clinic.identity import ClinicAccountPolicy
from src.database import db
from src.models import Recording, Speaker, SpeakerSnippet, SpeakerVoiceSample, TranscriptChunk, User
from src.services import audit
from src.utils.speaker_labels import is_generic_speaker_label, normalize_label

_EVENT = 'admin.upstream_voice.purge'
_IDENTITIES = (SpeakerSnippet, SpeakerVoiceSample, Speaker)
_RECORDING_FIELDS = ('speaker_embeddings', 'speaker_embeddings_space_id', 'speaker_label_map',
                     'transcription', 'transcription_translation', 'participants')


@dataclass(frozen=True)
class PurgeInventory:
    version: str
    counts: dict[str, int]


def _anonymous_transcript(text: str | None, labels: dict[str, str], names: set[str]) -> str | None:
    try:
        segments = json.loads(text) if text else None
    except ValueError:
        return text  # Plain text is clinical content, regardless of its spelling.
    if not isinstance(segments, list):
        return text
    changed = False
    for turn in segments:
        if not isinstance(turn, dict):
            continue
        if 'speaker_id' in turn:
            del turn['speaker_id']
            changed = True
        label = turn.get('speaker_label')
        for field in ('speaker', 'speaker_label'):
            value = turn.get(field)
            if normalize_label(value) and not is_generic_speaker_label(value):
                name = normalize_label(value)
                names.add(name)
                turn[field] = label if is_generic_speaker_label(label) else labels.get(name, 'Unknown')
                changed = True
    return json.dumps(segments, ensure_ascii=False) if changed else text


@contextmanager
def _open_store(root: Path | None):
    if root is None:
        yield None
        return
    if not root.is_absolute() or root == root.parent or root.resolve() != root:
        raise ValueError('unsafe_sample_store')
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        yield fd
    finally:
        os.close(fd)


def _sample_version(fd: int, name: str) -> tuple[int, int, int, int]:
    entry = os.stat(name, dir_fd=fd, follow_symlinks=False)
    if not stat.S_ISREG(entry.st_mode) or entry.st_nlink != 1:
        raise ValueError('unsafe_sample_path')
    return entry.st_dev, entry.st_ino, entry.st_size, entry.st_ctime_ns


def _plan(root: Path | None, fd: int | None):
    version = hashlib.sha256(b'upstream_voice_purge:v1')

    def bind(*values):
        version.update(repr(values).encode('utf-8') + b'\n')

    files = {}
    if fd is not None:
        store = os.fstat(fd)
        bind(root, store.st_dev, store.st_ino)
        for name in sorted(os.listdir(fd)):
            # The chunker writes flat speaker_<label>_sample.mp3 files.
            if name.startswith('speaker_') and name.endswith('_sample.mp3'):
                files[name] = _sample_version(fd, name)
        bind(files)
    counts = {'sample_files': len(files)}
    for model in _IDENTITIES:
        table = model.__table__
        counts[table.name] = 0
        for row in db.session.execute(select(table).order_by(table.c.id)):
            bind(table.name, tuple(row))
            counts[table.name] += 1

    recordings = {}
    columns = [getattr(Recording, fld) for fld in _RECORDING_FIELDS]
    for rid, audio_path, embeddings, space, label_map, original, translation, participants in db.session.execute(
        select(Recording.id, Recording.audio_path, *columns).order_by(Recording.id)
    ):
        if audio_path and Path(audio_path).name in files:
            raise ValueError('sample_path_matches_recording_audio')
        labels: dict[str, str] = {}
        if isinstance(label_map, dict):
            for label, name in sorted(label_map.items()):
                if is_generic_speaker_label(label) and normalize_label(name):
                    labels.setdefault(normalize_label(name), label)
        names = set(labels)
        new_original = _anonymous_transcript(original, labels, names)
        new_translation = _anonymous_transcript(translation, labels, names)
        new_participants = participants
        if participants:
            # Speaker assignment mirrors turn identities here. Keep unrelated entries.
            parts = participants.split(',')
            kept = [part for part in parts if normalize_label(part) not in names]
            if kept != parts:
                new_participants = ','.join(kept).strip() or None
        before = (embeddings, space, label_map, original, translation, participants)
        after = (None, None, None, new_original, new_translation, new_participants)
        if before != after:
            bind('recording', rid, before)
            recordings[rid] = dict(zip(_RECORDING_FIELDS, after))
    chunks = []
    for cid, name in db.session.execute(select(TranscriptChunk.id, TranscriptChunk.speaker_name)
                                        .where(TranscriptChunk.speaker_name.is_not(None)).order_by(TranscriptChunk.id)):
        if not is_generic_speaker_label(name):
            bind('chunk', cid, name)
            chunks.append(cid)
    counts.update(recordings=len(recordings), transcript_chunks=len(chunks))
    return PurgeInventory(version.hexdigest(), counts), recordings, chunks, files


def purge_upstream_voice(*, actor_user_id: int, sample_store: Path | None = None,
                         dry_run: bool = True, expected_version: str | None = None) -> PurgeInventory:
    """Preview counts, or apply a matching preview. Never discover paths outside the store."""
    if not clinic_mode_enabled():
        raise PermissionError('clinic_mode_required')

    def record(phase: str, outcome: str = 'success') -> None:
        audit.EVENTS.setdefault(_EVENT, ('admin', frozenset({'operation', 'phase', 'counts'})))
        if audit.record_in_tx(_EVENT, force=True, actor_kind='system', actor_user_id=actor_user_id,
                              outcome=outcome, details={'operation': operation, 'phase': phase,
                                                        'counts': inventory.counts}) is None:
            raise RuntimeError('purge_audit_required')
        db.session.commit()

    try:
        actor = db.session.get(User, actor_user_id, populate_existing=True) if type(actor_user_id) is int else None
        policy = db.session.get(ClinicAccountPolicy, actor.id, populate_existing=True) if actor else None
        if not actor or not actor.is_admin or not policy or policy.status != 'active':
            raise PermissionError('active_admin_required')

        with _open_store(sample_store) as fd:
            inventory, recordings, chunks, files = _plan(sample_store, fd)
            if dry_run is not False:
                return inventory
            if expected_version != inventory.version:
                raise ValueError('stale_purge_inventory')
            if not any(inventory.counts.values()):
                return inventory

            operation = uuid4().hex
            for rid, values in recordings.items():
                # Skip edit hooks that may regenerate translations or queue delivery.
                db.session.execute(update(Recording.__table__).where(Recording.id == rid)
                                   .values(**values, updated_at=datetime.utcnow()))
            for cid in chunks:
                db.session.execute(update(TranscriptChunk.__table__).where(TranscriptChunk.id == cid)
                                   .values(speaker_name=None))
            for model in _IDENTITIES:
                db.session.execute(model.__table__.delete())
            record('database_committed' if files else 'complete')

            if fd is not None and files:
                try:
                    for name, planned_version in files.items():
                        if _sample_version(fd, name) != planned_version:
                            raise ValueError('stale_sample_file')
                        os.unlink(name, dir_fd=fd)
                    os.fsync(fd)
                except (OSError, ValueError):
                    record('files_incomplete', 'failed')
                    raise RuntimeError('sample_purge_incomplete') from None
                record('complete')
            return inventory
    finally:
        db.session.rollback()
