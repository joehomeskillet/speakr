"""Immutable ingest of one dictation original.

The bytes are written bit for bit under ``root``, fsynced, and only then
recorded as a ``ClinicOriginal``. A failed database commit leaves the file
and no row; the same call again reuses that file and inserts the row.
The same idempotency key and the same bytes return the original receipt
and do not add a second file or a second row.

``IngestService.store_original`` is the callable
``agent_channel_ingest.finalize_ingest`` injects. No route is registered
here. The recording row is only the dictation's anchor: its ``audio_path``
stays empty so a stock convert job cannot rewrite the original.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import locale
import logging
import os
import re
import subprocess
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from src.clinic import batch_import_plan, decoder, pools, recipient_defaults, workflow
from src.clinic.audio_revisions import init_audio_revision
from src.clinic.config import clinic_mode_enabled
from src.clinic.migration_import_intent_metadata import IntentMetadataError, _title
from src.clinic.models import ClinicDictation, ClinicOriginal
from src.database import db
from src.models import ProcessingJob, Recording, User
from src.services.storage import get_storage_service
from src.services.storage.locator import local_path_from_key
from src.services.transcription_defaults import resolve_transcription_params

logger = logging.getLogger(__name__)

# Fits Recording.file_size (32-bit on PostgreSQL) and stops a huge declared
# size from filling the disk. Longer dictations need a BigInteger file size.
MAX_BYTES = 2_147_483_647
_KEY_MAX = 512
_SHA = re.compile(r'^[0-9a-fA-F]{64}$')
_ORIGIN = re.compile(r'^[A-Za-z0-9._:-]{1,32}$')
_CONTROL = re.compile(r'[\x00-\x1f]')
_CHUNK = 1024 * 1024


class IngestError(Exception):
    """Ingest or original-probe failure with a stable ``code``."""

    def __init__(self, code: str, message: str = ''):
        self.code = code
        super().__init__(f'{code}: {message}' if message else code)


@dataclass(frozen=True)
class IngestReceipt:
    """What ``finalize_ingest`` reads. Attributes and ``get`` both work."""

    original_id: str
    dictation_id: str
    sha256: str
    size: int
    storage_key: str

    def get(self, name: str, default=None):
        return getattr(self, name, default)


def storage_key_for(idempotency_key: str) -> str:
    """Stable relative key. The file name is not a conversion of the audio."""
    digest = hashlib.sha256(idempotency_key.encode('utf-8')).hexdigest()
    return f'clinic/originals/v1/{digest}'


def probe_original_duration_ms(path: Path, content_hash: str) -> int:
    """Measure the decoded samples used by playback, including codec padding removal."""
    try:
        if not path.is_file():
            raise IngestError('missing_original_file')
        with tempfile.TemporaryDirectory(prefix='clinic-duration-') as tmp:
            result = decoder.decode_to_wav(path, tmp)
    except OSError as exc:
        raise IngestError('original_read_failed') from exc
    if result.status != decoder.OK:
        raise IngestError(f'audio_{result.status}')
    if result.input_sha256 is None or not hmac.compare_digest(result.input_sha256, content_hash):
        raise IngestError('original_hash_mismatch')
    # Round down so the edit list cannot end beyond the decoded sample count.
    if result.duration_s is None or result.duration_s < 0.001:
        raise IngestError('audio_too_short')
    return int(result.duration_s * 1000)


def _commit() -> None:
    db.session.commit()


def _as_utc(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise IngestError('invalid', 'now must be timezone-aware')
    return now.astimezone(timezone.utc)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(_CHUNK), b''):
            digest.update(block)
    return digest.hexdigest()


def _write_all(fd: int, block: bytes | bytearray) -> None:
    view = memoryview(block)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise IngestError('storage_failed', 'short write')
        view = view[written:]


def _discard(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _spool(stream, directory: Path, declared_size: int) -> tuple[Path, str]:
    """Copy the stream into a new file in ``directory`` and fsync it."""
    directory.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.partial-', dir=directory)
    path = Path(name)
    digest = hashlib.sha256()
    remaining = declared_size
    try:
        while remaining:
            block = stream.read(min(_CHUNK, remaining + 1))
            if not block:
                raise IngestError('invalid', 'stream is shorter than declared_size')
            if isinstance(block, str) or not isinstance(block, (bytes, bytearray)):
                raise IngestError('invalid', 'stream must yield bytes')
            if len(block) > remaining:
                raise IngestError('invalid', 'stream is longer than declared_size')
            _write_all(fd, block)
            digest.update(block)
            remaining -= len(block)
        if stream.read(1):
            raise IngestError('invalid', 'stream is longer than declared_size')
        os.fsync(fd)
    except Exception:
        os.close(fd)
        _discard(path)
        raise
    os.close(fd)
    return path, digest.hexdigest()


@dataclass(frozen=True)
class ImportDefaults:
    title: str | None = None
    priority: int = workflow.PRIORITY_NORMAL
    recipient_user_id: int | None = None
    pool_id: str | None = None
    use_asr: bool = True


def resolve_import_metadata(metadata: Mapping, defaults: ImportDefaults) -> ImportDefaults:
    """Apply an interpreted intent to one file's trusted defaults."""
    changes = {field: metadata[field]['value'] for field in ('title', 'priority', 'recipient_user_id', 'pool_id')
               if metadata[field]['op'] == 'set'}
    if metadata['asr'] != 'keep':
        changes['use_asr'] = metadata['asr'] == 'enable'
    return replace(defaults, **changes)


def _file_defaults(path: Path, defaults: ImportDefaults) -> ImportDefaults:
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'error', '-protocol_whitelist', 'file', '-format_whitelist',
             'wav,mp3,flac,ogg,matroska,webm,mov,amr,dss', '-show_entries', 'format_tags',
             '-of', 'json', str(path)],
            capture_output=True, check=True, timeout=5,
        )
        tags = json.loads(result.stdout.decode('utf-8', 'replace')).get('format', {}).get('tags', {})
    except (OSError, subprocess.SubprocessError, ValueError):
        return defaults
    tags = {key.casefold(): value for key, value in tags.items() if isinstance(value, str)}
    title: str | None
    try:
        title = _title(tags.get('title'))
    except IntentMetadataError:
        title = defaults.title
    priority, unrecognized = workflow.normalize_priority(tags.get('priority', defaults.priority))
    asr = tags.get('use_speech_recognition', '').strip().casefold()
    return replace(defaults, title=title, priority=defaults.priority if unrecognized else priority,
                   use_asr={'1': True, '0': False, 'true': True, 'false': False}.get(asr, defaults.use_asr))


def _import_pool(author: int, metadata: ImportDefaults, now: datetime) -> str | None:
    from src.clinic.pool_policy import author_pool_ids

    candidates = {pool_id for pool_id in author_pool_ids(author)
                  if (pool := db.session.get(pools.ClinicAuthorPool, pool_id)) is not None
                  and pools.is_routable(pool, now)}
    if metadata.pool_id is not None and metadata.pool_id not in candidates:
        raise IngestError('invalid', 'import author pool is not eligible')
    pool_id = metadata.pool_id or (next(iter(candidates)) if len(candidates) == 1 else None)
    if metadata.recipient_user_id is not None:
        if not pool_id or metadata.recipient_user_id not in recipient_defaults.eligible_user_ids(pool_id):
            raise IngestError('invalid', 'import recipient is not eligible')
    if pool_id is None and metadata.priority != workflow.PRIORITY_NORMAL:
        raise IngestError('invalid', 'import priority requires an author pool')
    return pool_id


class IngestService:
    """Durable original store rooted at one directory the caller owns."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def file_path(self, storage_key: str) -> Path:
        return Path(local_path_from_key(str(self.root), storage_key))

    def stored_path(self, *, idempotency_key: str, declared_sha256: str, declared_size: int) -> Path | None:
        """Find and verify an already published original without using upload chunks."""
        key = _checked_key(idempotency_key)
        digest = _checked_sha(declared_sha256)
        size = _checked_size(declared_size)
        # Keep the generated path intact so resolving a symlink cannot hide it.
        path = self.root / storage_key_for(key)
        if path.resolve() != path:
            raise IngestError('conflict', 'stored original path contains a symlink')
        if not path.exists() and not path.is_symlink():
            return None
        _require_same(path, digest)
        if path.stat().st_size != size:
            raise IngestError('conflict', 'stored original size differs from the declaration')
        return path

    def store_original(
        self,
        stream,
        *,
        declared_sha256: str,
        declared_size: int,
        origin: str,
        origin_ref: str,
        author_user_id: int | None,
        idempotency_key: str,
        now: datetime | None = None,
        commit: bool = True,
        language_id: int | None = None,
        intent_metadata: Mapping | None = None,
        import_defaults: ImportDefaults | None = None,
    ) -> IngestReceipt:
        """Store ``stream`` and return the receipt for that delivery.

        ``author_user_id`` may be omitted. The file is still stored, and the
        call raises ``author_required`` until a later call with the same key,
        the same bytes and an author inserts the row.

        ``commit=False`` leaves the flushed rows in the caller's transaction
        so an import-centre assignment and its dictation can commit together.
        """
        key = _checked_key(idempotency_key)
        digest = _checked_sha(declared_sha256)
        size = _checked_size(declared_size)
        origin = _checked_origin(origin)
        origin_ref = _checked_ref(origin_ref)
        author_user_id = _checked_author(author_user_id)
        moment = _as_utc(now)
        storage_key = storage_key_for(key)
        dest = self.file_path(storage_key)

        spool, actual = _spool(stream, dest.parent, size)
        if not hmac.compare_digest(actual, digest):
            _discard(spool)
            raise IngestError('invalid', 'sha256 does not match the stream')
        self._publish(spool, dest, digest)

        existing = _existing(storage_key)
        if existing is not None:
            return _reuse(existing, digest, size, author_user_id)
        if author_user_id is None:
            raise IngestError('author_required', 'author_user_id is required to record the original')
        if db.session.get(User, author_user_id) is None:
            raise IngestError('invalid', 'author_user_id does not exist')
        duration_ms = None
        import_path = None
        if clinic_mode_enabled():
            try:
                duration_ms = probe_original_duration_ms(dest, digest)
            except IngestError as error:
                # Keep the immutable receipt even when the media cannot yet be decoded.
                logger.warning('clinic ingest: initial audio revision deferred (%s)', error.code)
            if origin in ('agent', 'batch'):
                import_path = dest
            else:
                # Decoding can take time. Recheck for a receipt published meanwhile.
                existing = _existing(storage_key)
                if existing is not None:
                    return _reuse(existing, digest, size, author_user_id)
        metadata = None
        if import_path is not None and intent_metadata is not None:
            defaults = _file_defaults(import_path, import_defaults or ImportDefaults())
            metadata = resolve_import_metadata(intent_metadata, defaults)
        return _insert_row(
            author_user_id, storage_key, digest, size, origin, origin_ref, key, moment,
            duration_ms=duration_ms, commit=commit, import_path=import_path, language_id=language_id,
            metadata=metadata,
        )

    def _publish(self, spool: Path, dest: Path, digest: str) -> None:
        """Make ``dest`` name the spooled bytes without replacing a different file.

        The spool name is always removed. After a successful link the bytes
        stay reachable through ``dest``.
        """
        try:
            if dest.is_symlink() or dest.exists():
                _require_same(dest, digest)
                return
            try:
                # shortcut: a crash after link and before the spool name is removed leaves a
                # .partial- hard link of the same inode. Upgrade = delete .partial- names on startup.
                os.link(spool, dest)
            except FileExistsError:
                _require_same(dest, digest)
                return
            _fsync_dir(dest.parent)
            if not hmac.compare_digest(_sha256_file(dest), digest):
                _discard(dest)
                raise IngestError('storage_failed', 'stored bytes do not match the source')
        finally:
            _discard(spool)


def _checked_key(value) -> str:
    if not isinstance(value, str) or not value or len(value) > _KEY_MAX or _CONTROL.search(value):
        raise IngestError('invalid', 'idempotency key')
    try:
        value.encode('utf-8')
    except UnicodeEncodeError as exc:
        raise IngestError('invalid', 'idempotency key') from exc
    return value


def _checked_sha(value) -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise IngestError('invalid', 'declared_sha256')
    return value.lower()


def _checked_size(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_BYTES:
        raise IngestError('invalid', 'declared_size')
    return value


def _checked_origin(value) -> str:
    if not isinstance(value, str) or not _ORIGIN.fullmatch(value):
        raise IngestError('invalid', 'origin')
    return value


def _checked_ref(value) -> str:
    # ClinicOriginal.origin_ref is String(255). A longer ref cannot be stored.
    if not isinstance(value, str) or not value or len(value) > 255 or _CONTROL.search(value):
        raise IngestError('invalid', 'origin_ref')
    return value


def _checked_author(value) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise IngestError('invalid', 'author_user_id')
    return value


def _require_same(dest: Path, digest: str) -> None:
    if dest.is_symlink() or not dest.is_file():
        raise IngestError('conflict', 'stored original is not a regular file')
    if not hmac.compare_digest(_sha256_file(dest), digest):
        raise IngestError('conflict', 'idempotency key reused with different content')


def _existing(storage_key: str) -> ClinicOriginal | None:
    # shortcut: storage_key has no unique index, so two concurrent first inserts can
    # create two rows. Upgrade = a unique constraint in a later migration.
    rows = ClinicOriginal.query.filter_by(storage_key=storage_key).all()
    if not rows:
        return None
    if len(rows) > 1:
        raise IngestError('conflict', 'more than one original for this key')
    return rows[0]


def _reuse(row: ClinicOriginal, digest: str, size: int, author_user_id: int | None) -> IngestReceipt:
    if row.content_hash != digest or int(row.size_bytes) != size:
        raise IngestError('conflict', 'stored original does not match this delivery')
    if author_user_id is not None:
        dictation = db.session.get(ClinicDictation, row.dictation_id)
        if dictation is None or dictation.author_user_id != author_user_id:
            raise IngestError('conflict', 'idempotency key is bound to another author')
    return _receipt_of(row)


def _receipt_text(key: str, digest: str, size: int, origin: str, origin_ref: str, storage_key: str) -> str:
    return json.dumps({
        'v': 1,
        'idempotency_key': key,
        'sha256': digest,
        'size': size,
        'origin': origin,
        'origin_ref': origin_ref,
        'storage_key': storage_key,
    }, sort_keys=True, separators=(',', ':'))


def lock_original_key(storage_key: str) -> None:
    """Serialize the final receipt lookup and job insert for concurrent deliveries."""
    connection = db.session.connection()
    if connection.dialect.name == 'postgresql':
        lock_id = int.from_bytes(bytes.fromhex(Path(storage_key).name)[:8], 'big', signed=True)
        connection.execute(db.text('SELECT pg_advisory_xact_lock(:key)'), {'key': lock_id})
    elif connection.dialect.name == 'sqlite' and not connection.connection.driver_connection.in_transaction:
        connection.exec_driver_sql('BEGIN IMMEDIATE')


def _queue_import(path: Path, author_user_id: int, size: int, language_id: int | None,
                  *, title: str | None = None) -> str:
    """Stage a worker-owned copy. Queue cleanup must never remove the clinic original."""
    try:
        with path.open('rb') as stream:
            head = stream.read(16)
        extension = decoder.detect_container(head)
        if extension is None and batch_import_plan.classify('Diktat.flac', head)[0] == batch_import_plan.KIND_AUDIO:
            extension = 'flac'
        filename = f'Diktat.{extension or "bin"}'
        # A retry after a failed commit reuses the same working key.
        stored = get_storage_service().upload_local_file(
            str(path), f'clinic/imports/asr/{path.name}/{filename}', delete_source=False)
    except Exception as exc:
        raise IngestError('storage_failed', 'could not stage the import for transcription') from exc
    source = Recording(user_id=author_user_id, audio_path=stored.locator, file_size=size,
                       original_filename=filename, status='QUEUED', processing_source='import',
                       is_inbox=False, is_archived=True, title=title, title_source='user' if title else None)
    db.session.add(source)
    db.session.flush()
    # JobQueue.enqueue uses a separate transaction. Publish the stock job with its link instead.
    language = locale.windows_locale.get(language_id or 0, '').partition('_')[0]
    params = resolve_transcription_params(source, overrides={'language': language})
    job = ProcessingJob(user_id=author_user_id, recording_id=source.id,
                        job_type='transcribe', params=json.dumps(params))
    if extension is None:
        source.status, job.status = 'FAILED', 'failed'
        source.error_message = job.error_message = 'Das Audioformat wird nicht unterstützt.'
    db.session.add(job)
    return f'upload:recording:{source.id}:time=upload'


def _insert_row(author_user_id, storage_key, digest, size, origin, origin_ref, key, moment,
                *, duration_ms, commit=True, import_path=None, language_id=None,
                metadata: ImportDefaults | None = None) -> IngestReceipt:
    recording = Recording(
        user_id=author_user_id,
        status='PENDING',
        is_inbox=False,
        is_archived=True,
        deletion_exempt=True,
        file_size=size,
    )
    try:
        if import_path is not None:
            lock_original_key(storage_key)
            existing = _existing(storage_key)
            if existing is not None:
                receipt = _reuse(existing, digest, size, author_user_id)
                if commit:
                    _commit()
                return receipt
        pool_id = _import_pool(author_user_id, metadata, moment) if metadata is not None else None
        if metadata is not None:
            recording.title = metadata.title
            recording.title_source = 'user' if metadata.title else None
        db.session.add(recording)
        db.session.flush()
        dictation = ClinicDictation(
            recording_id=recording.id,
            author_user_id=author_user_id,
            pool_id=pool_id,
            created_at=moment,
        )
        db.session.add(dictation)
        db.session.flush()
        # Keep the import provenance in the receipt and the ASR link on the original row.
        link = origin_ref
        if metadata is not None and not metadata.use_asr:
            link = f'upload:recording:{recording.id}:asr=off:time=upload'
        elif import_path is not None:
            link = _queue_import(import_path, author_user_id, size, language_id, title=recording.title)
        original = ClinicOriginal(
            dictation_id=dictation.id,
            content_hash=digest,
            size_bytes=size,
            storage_key=storage_key,
            receipt=_receipt_text(key, digest, size, origin, origin_ref, storage_key),
            origin_ref=link,
            created_at=moment,
        )
        db.session.add(original)
        db.session.flush()
        if duration_ms is not None:
            init_audio_revision(dictation, original, duration_ms)
        if metadata is not None and pool_id is not None:
            from src.clinic.recognition_state import settle

            db.session.add(workflow.ClinicWorkflowDictation(
                id=dictation.id, author_user_id=author_user_id, pool_id=pool_id, created_at=moment,
                status=workflow.PREP_OFFLINE, priority=metadata.priority,
                assignee_user_id=metadata.recipient_user_id, audio_seconds=(duration_ms or 0) // 1000,
            ))
            settle(db.session, dictation.id)
        receipt = _receipt_of(original)
        if commit:
            _commit()
    except Exception:
        db.session.rollback()
        logger.warning(
            'clinic ingest %s: receipt transaction failed after the file was stored',
            storage_key, exc_info=True,
        )
        raise
    return receipt


def _receipt_of(row: ClinicOriginal) -> IngestReceipt:
    return IngestReceipt(
        original_id=row.id,
        dictation_id=row.dictation_id,
        sha256=row.content_hash,
        size=int(row.size_bytes),
        storage_key=row.storage_key,
    )
