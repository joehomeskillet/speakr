"""PAR-OPS-009 software evidence for immutable originals and separate working copies.

Uses the clinic test database, local storage and synthetic audio with FFmpeg.
This does not establish Philips device behavior or hardware parity.
"""

import shutil
from hashlib import sha256
from io import BytesIO
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from src.app import app, db
from src.clinic import decoder
from src.clinic.ingest import IngestError, IngestReceipt, IngestService
from src.clinic.models import ClinicAudioRevision, ClinicOriginal

_AUDIO = (
    Path(__file__).resolve().parents[2]
    / 'clinic-spec/sources/dns-functional/fixtures/generated/audio'
)
_NEEDS_FFMPEG = pytest.mark.skipif(shutil.which('ffmpeg') is None, reason='ffmpeg missing')


@pytest.fixture
def original_store(world, tmp_path):
    with app.app_context():
        yield IngestService(tmp_path / 'store'), world['user_a']


def _ingest(store: tuple[IngestService, int], data: bytes) -> IngestReceipt:
    service, author = store
    return service.store_original(
        BytesIO(data),
        declared_sha256=sha256(data).hexdigest(),
        declared_size=len(data),
        origin='agent',
        origin_ref='agent:test:src-1:v1',
        author_user_id=author,
        idempotency_key='par-ops-009',
    )


def _snapshot(original_id: str) -> dict[str, object]:
    table = ClinicOriginal.__table__
    row = db.session.execute(select(table).where(table.c.id == original_id)).mappings().one()
    return dict(row)


@pytest.mark.parametrize('access', ['bulk', 'sql'])
@pytest.mark.parametrize('operation', ['update', 'delete'])
def test_par_ops_009_database_rejects_direct_original_writes(
    original_store: tuple[IngestService, int], access: str, operation: str,
) -> None:
    """Prove the software rule only: database guards reject bulk and raw writes."""
    receipt = _ingest(original_store, b'original')
    before = _snapshot(receipt.original_id)
    with pytest.raises(IntegrityError, match='clinic original (is immutable|cannot be deleted)'):
        with db.session.begin_nested():
            if access == 'bulk':
                query = ClinicOriginal.query.filter(ClinicOriginal.id == receipt.original_id)
                if operation == 'update':
                    query.update({ClinicOriginal.receipt: 'changed'}, synchronize_session=False)
                else:
                    query.delete(synchronize_session=False)
            elif operation == 'update':
                db.session.execute(
                    text('UPDATE clinic_original_version SET receipt = :receipt WHERE id = :id'),
                    {'id': receipt.original_id, 'receipt': 'changed'},
                )
            else:
                db.session.execute(
                    text('DELETE FROM clinic_original_version WHERE id = :id'),
                    {'id': receipt.original_id},
                )
    assert _snapshot(receipt.original_id) == before


@_NEEDS_FFMPEG
def test_par_ops_009_corrupt_decode_preserves_stored_original(
    original_store: tuple[IngestService, int], tmp_path: Path,
) -> None:
    """Prove the software rule only: a corrupt decode leaves the stored original intact."""
    data = (_AUDIO / 'truncated_wav.bin').read_bytes()
    receipt = _ingest(original_store, data)
    original = original_store[0].file_path(receipt.storage_key)
    before = _snapshot(receipt.original_id)
    work_dir = tmp_path / 'working'
    work_dir.mkdir()
    working_copy = work_dir / f'{original.stem}.16k.wav'
    assert working_copy != original
    assert working_copy.parent != original.parent

    result = decoder.decode_to_wav(original, work_dir)

    assert result.status == decoder.CORRUPT, result.detail
    assert result.output_path is None
    assert result.output_sha256 is None
    assert list(work_dir.iterdir()) == []
    assert list(original.parent.iterdir()) == [original]
    saved = original.read_bytes()
    assert saved == data
    assert sha256(saved).hexdigest() == result.input_sha256 == receipt.sha256
    assert _snapshot(receipt.original_id) == before


@_NEEDS_FFMPEG
def test_par_ops_009_repeated_decode_creates_separate_identical_copies(
    original_store: tuple[IngestService, int], tmp_path: Path,
) -> None:
    """Prove the software rule only: repeated decoding is deterministic and separate."""
    data = (_AUDIO / 'tone_48k_stereo.wav').read_bytes()
    receipt = _ingest(original_store, data)
    original = original_store[0].file_path(receipt.storage_key)
    hashes = []
    for name in ('first', 'second'):
        work_dir = tmp_path / name
        work_dir.mkdir()
        result = decoder.decode_to_wav(original, work_dir)
        assert result.status == decoder.OK, result.detail
        assert result.input_sha256 == receipt.sha256
        assert result.output_path is not None
        output = Path(result.output_path).resolve()
        assert output.parent == work_dir.resolve()
        assert not output.samefile(original)
        digest = sha256(output.read_bytes()).hexdigest()
        assert digest == result.output_sha256
        assert digest != receipt.sha256
        hashes.append(digest)
        assert list(original.parent.iterdir()) == [original]
        assert original.read_bytes() == data
    assert hashes[0] == hashes[1]


def test_par_ops_009_ingest_receipt_survives_conflicting_replay(
    original_store: tuple[IngestService, int],
) -> None:
    """Prove the software rule only: a conflict cannot change the original or its receipt."""
    data = b'original'
    first = _ingest(original_store, data)
    before = _snapshot(first.original_id)
    assert _ingest(original_store, data) == first

    with pytest.raises(IngestError, match='idempotency key reused with different content') as exc:
        _ingest(original_store, b'modified')
    assert exc.value.code == 'conflict'

    assert _ingest(original_store, data) == first
    assert ClinicOriginal.query.count() == 1
    assert _snapshot(first.original_id) == before
    original = original_store[0].file_path(first.storage_key)
    assert list(original.parent.iterdir()) == [original]
    assert original.read_bytes() == data


def test_par_ops_009_revision_does_not_change_any_original_column(
    original_store: tuple[IngestService, int],
) -> None:
    """Prove the software rule only: a working-copy revision leaves the original row intact."""
    receipt = _ingest(original_store, b'original')
    before = _snapshot(receipt.original_id)
    digest = sha256(b'working copy').hexdigest()
    db.session.add(ClinicAudioRevision(dictation_id=receipt.dictation_id, content_hash=digest))
    db.session.commit()

    revision = ClinicAudioRevision.query.filter_by(dictation_id=receipt.dictation_id).one()
    assert revision.content_hash == digest
    assert revision.content_hash != before['content_hash']
    assert _snapshot(receipt.original_id) == before
