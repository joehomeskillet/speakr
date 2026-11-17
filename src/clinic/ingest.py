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
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from src.clinic.models import ClinicDictation, ClinicOriginal
from src.database import db
from src.models import Recording, User
from src.services.storage.locator import local_path_from_key

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
    """Refusal with a stable ``code``: invalid, conflict, author_required or storage_failed."""

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


class IngestService:
    """Durable original store rooted at one directory the caller owns."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def file_path(self, storage_key: str) -> Path:
        return Path(local_path_from_key(str(self.root), storage_key))

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
    ) -> IngestReceipt:
        """Store ``stream`` and return the receipt for that delivery.

        ``author_user_id`` may be omitted. The file is still stored, and the
        call raises ``author_required`` until a later call with the same key,
        the same bytes and an author inserts the row.
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
        return _insert_row(
            author_user_id, storage_key, digest, size, origin, origin_ref, key, moment,
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


def _insert_row(author_user_id, storage_key, digest, size, origin, origin_ref, key, moment) -> IngestReceipt:
    recording = Recording(
        user_id=author_user_id,
        status='PENDING',
        is_inbox=False,
        is_archived=True,
        deletion_exempt=True,
        file_size=size,
    )
    try:
        db.session.add(recording)
        db.session.flush()
        dictation = ClinicDictation(
            recording_id=recording.id,
            author_user_id=author_user_id,
            created_at=moment,
        )
        db.session.add(dictation)
        db.session.flush()
        original = ClinicOriginal(
            dictation_id=dictation.id,
            content_hash=digest,
            size_bytes=size,
            storage_key=storage_key,
            receipt=_receipt_text(key, digest, size, origin, origin_ref, storage_key),
            origin_ref=origin_ref,
            created_at=moment,
        )
        db.session.add(original)
        db.session.flush()
        receipt = _receipt_of(original)
        _commit()
    except SQLAlchemyError:
        db.session.rollback()
        logger.warning(
            'clinic ingest %s: database write failed after the file was stored',
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
