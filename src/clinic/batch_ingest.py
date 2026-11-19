"""Bounded multi-file import over the existing durable ingestion.

Every selected file is its own unit: an independent stable import key, its own
commit and its own visible outcome. A rejected or failed file never rolls back
an original that another file of the batch already stored.

* The batch is first checked by :func:`batch_import_plan.plan_batch` (type,
  size, limits, duplicates inside the batch). Rejected files write nothing.
* Audio goes through ``IngestService.store_original``. The key is derived from
  the batch id and the file's SHA-256, never from its position, so a retry of
  the same batch, also in another order, reuses the per-file receipts.
* Documents go through ``document_ingest.attach_document`` and need the
  explicit dictation they belong to.
* ``authorize_target(actor_user_id, author_user_id, pool_id)`` runs for every
  file before anything is written. ``author_user_id`` and ``pool_id`` are the
  client-supplied values and may be ``None``.

No route is registered here (consumer: w098-import-api).
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from io import BytesIO

from sqlalchemy.exc import SQLAlchemyError

from src.clinic import batch_import_plan as plan
from src.clinic.document_ingest import DocumentIngestError, attach_document
from src.clinic.ingest import IngestError, IngestService, storage_key_for
from src.clinic.models import ClinicDictation, ClinicOriginal
from src.database import db

ORIGIN = "batch"
HEAD_BYTES = 4096

STORED, REPLAYED, REJECTED, FAILED = "stored", "replayed", "rejected", "failed"

MESSAGES = {
    "forbidden": "Keine Berechtigung für diesen Autor oder diesen Pool.",
    "dictation_required": "Für ein Dokument muss ein Diktat gewählt werden.",
    "author_required": "Für diese Audiodatei muss ein Autor gewählt werden.",
    "conflict": "Diese Datei wurde bereits mit anderem Inhalt oder anderem Autor importiert.",
    "storage_failed": "Die Datei konnte nicht gespeichert werden. Bitte erneut versuchen.",
    "invalid": "Die Datei ist ungültig.",
}


class BatchIngestError(Exception):
    """The batch as a whole is unusable. Nothing was written."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class BatchFile:
    name: str
    data: bytes
    author_user_id: int | None = None
    pool_id: int | str | None = None
    dictation_id: str | None = None
    expected_version: int = 0


@dataclass(frozen=True)
class FileOutcome:
    index: int
    name: str
    status: str
    code: str = ""
    message: str = ""
    import_key: str = ""
    original_id: str | None = None
    dictation_id: str | None = None


def import_key_for(batch_id: str, sha256: str) -> str:
    """Stable per-file key. Independent of the file's position in the batch."""
    return f"batch:{batch_id}:{sha256.lower()}"


def ingest_batch(
    service: IngestService,
    *,
    batch_id: str,
    actor_user_id: int,
    files: Sequence[BatchFile],
    authorize_target: Callable[[int, int | None, int | str | None], bool],
    authorize_document: Callable[[ClinicDictation, int], bool],
    limits: plan.BatchLimits = plan.BatchLimits(),
) -> list[FileOutcome]:
    """Import ``files`` one by one and return one outcome per file, in input order."""
    if (not isinstance(batch_id, str) or not batch_id or len(batch_id) > 128
            or any(ord(ch) < 32 for ch in batch_id)
            or type(actor_user_id) is not int
            or not isinstance(files, Sequence) or isinstance(files, (str, bytes, bytearray))
            or not all(isinstance(f, BatchFile) and isinstance(f.data, (bytes, bytearray)) for f in files)):
        raise BatchIngestError("invalid_input")

    items = [
        plan.BatchItem(
            f.name if isinstance(f.name, str) else "", len(f.data),
            bytes(f.data[:HEAD_BYTES]), hashlib.sha256(bytes(f.data)).hexdigest(),
        )
        for f in files
    ]
    verdicts = plan.plan_batch(items, limits)

    outcomes: list[FileOutcome] = []
    for index, (file, item, verdict) in enumerate(zip(files, items, verdicts)):
        key = import_key_for(batch_id, item.sha256)
        if verdict.status != "accepted":
            outcomes.append(FileOutcome(
                index, verdict.name, REJECTED, verdict.status, verdict.reason, key,
            ))
            continue
        outcomes.append(_import_one(
            service, index, verdict, file, key, actor_user_id,
            authorize_target, authorize_document,
        ))
    return outcomes


def _import_one(service, index, verdict, file, key, actor, authorize_target, authorize_document) -> FileOutcome:
    def out(status, code="", message=None, **extra):
        text = MESSAGES.get(code, "") if message is None else message
        return FileOutcome(index, verdict.name, status, code, text, key, **extra)

    try:
        if authorize_target(actor, file.author_user_id, file.pool_id) is not True:
            return out(REJECTED, "forbidden")
        if verdict.kind == plan.KIND_DOCUMENT:
            return _document(service, file, out, actor, authorize_document)
        return _audio(service, file, verdict, key, actor, out)
    except (IngestError, DocumentIngestError) as exc:
        db.session.rollback()
        return _refusal(exc, out)
    except SQLAlchemyError:
        db.session.rollback()
        return out(FAILED, "storage_failed")


def _refusal(exc, out) -> FileOutcome:
    if isinstance(exc, DocumentIngestError):
        status = FAILED if exc.code == "storage_failed" else REJECTED
        return out(status, exc.code, message=exc.message)
    code = exc.code if exc.code in ("invalid", "conflict", "author_required") else "storage_failed"
    return out(FAILED if code == "storage_failed" else REJECTED, code)


def _audio(service, file, verdict, key, actor, out) -> FileOutcome:
    if file.author_user_id is None:
        return out(REJECTED, "author_required")
    storage_key = storage_key_for(key)
    known = ClinicOriginal.query.filter_by(storage_key=storage_key).first() is not None
    data = bytes(file.data)
    receipt = service.store_original(
        BytesIO(data),
        declared_sha256=hashlib.sha256(data).hexdigest(),
        declared_size=len(data),
        origin=ORIGIN,
        origin_ref=verdict.name,
        author_user_id=file.author_user_id,
        idempotency_key=key,
    )
    return out(REPLAYED if known else STORED, original_id=receipt.original_id,
               dictation_id=receipt.dictation_id)


def _document(service, file, out, actor, authorize_document) -> FileOutcome:
    if not file.dictation_id:
        return out(REJECTED, "dictation_required")
    receipt = attach_document(
        service, dictation_id=file.dictation_id, actor_user_id=actor, data=bytes(file.data),
        filename=file.name, expected_version=file.expected_version, authorize=authorize_document,
    )
    return out(REPLAYED if receipt.replayed else STORED, original_id=receipt.original_id,
               dictation_id=receipt.dictation_id)
