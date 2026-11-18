import hashlib

import pytest

from src.clinic.batch_import_plan import (
    KIND_AUDIO,
    KIND_DOCUMENT,
    BatchItem,
    BatchLimits,
    BatchPlanError,
    ItemVerdict,
    classify,
    plan_batch,
)


def _item(name: str, data: bytes, *, uppercase_digest: bool = False) -> BatchItem:
    digest = hashlib.sha256(data).hexdigest()
    return BatchItem(name, len(data), data[:32], digest.upper() if uppercase_digest else digest)


@pytest.mark.parametrize(
    ("name", "head"),
    [
        ("Diktat.WAV", b"RIFF\x24\x00\x00\x00WAVE"),
        ("diktat.dss", b"\x02dss"),
        ("diktat.ds2", b"\x03ds2"),
        ("diktat.mp3", b"ID3"),
        ("diktat.mp3", b"\xff\xfb\x90\x64"),
        ("diktat.m4a", b"\x00\x00\x00\x18ftypM4A "),
        ("diktat.ogg", b"OggS"),
        ("diktat.opus", b"OggS"),
        ("diktat.opus", b"\x1a\x45\xdf\xa3"),
        ("diktat.flac", b"fLaC"),
    ],
)
def test_classifies_audio_by_matching_signature(name: str, head: bytes) -> None:
    assert classify(name, head) == (KIND_AUDIO, "")


@pytest.mark.parametrize(
    ("name", "head"),
    [
        ("notiz.txt", "Grüezi".encode()),
        ("notiz.txt", "Ärztin – Notiz".encode("cp1252")),
        ("notiz.rtf", b"{\\rtf1 Notiz}"),
        ("notiz.docx", b"PK\x03\x04"),
        ("notiz.odt", b"PK\x03\x04"),
    ],
)
def test_classifies_document_by_matching_header(name: str, head: bytes) -> None:
    assert classify(name, head) == (KIND_DOCUMENT, "")


@pytest.mark.parametrize(
    ("name", "head"),
    [
        ("diktat.mp3", b"{\\rtf1 Notiz}"),
        ("diktat.ogg", b"Notiz"),
        ("diktat.flac", b"OggS"),
        ("diktat.dss", b"\x03ds2"),
        ("diktat.m4a", b"ftypM4A "),
        ("notiz.txt", b"Notiz\x00"),
        ("notiz.txt", b"\x81"),
        ("notiz.rtf", b" {\\rtf1 Notiz}"),
        ("notiz.docx", b"PK\x05\x06"),
        ("notiz.odt", b"Notiz"),
    ],
)
def test_rejects_type_mismatches(name: str, head: bytes) -> None:
    assert classify(name, head) == (None, "type_mismatch")
    assert plan_batch([_item(name, head)]) == [
        ItemVerdict(0, name, None, "type_mismatch", "type_mismatch"),
    ]


def test_mixed_batch_returns_one_ordered_verdict_per_item() -> None:
    items = [
        _item("diktat.mp3", b"ID3"),
        _item("falsch.wav", b"Notiz"),
        _item("notiz.txt", b"Notiz"),  # A rejected item does not reserve its digest.
        _item("kopie.TXT", b"Notiz", uppercase_digest=True),
        _item("format.xyz", b"ID3"),
        _item("leer.txt", b""),
    ]

    assert plan_batch(items) == [
        ItemVerdict(0, "diktat.mp3", KIND_AUDIO, "accepted", ""),
        ItemVerdict(1, "falsch.wav", None, "type_mismatch", "type_mismatch"),
        ItemVerdict(2, "notiz.txt", KIND_DOCUMENT, "accepted", ""),
        ItemVerdict(3, "kopie.TXT", KIND_DOCUMENT, "duplicate", "same_content_as_2"),
        ItemVerdict(4, "format.xyz", None, "unsupported", "unsupported_extension:.xyz"),
        ItemVerdict(5, "leer.txt", None, "unsupported", "empty"),
    ]


@pytest.mark.parametrize(
    ("name", "basename"),
    [
        ("/privat/a.wav", "a.wav"),
        (r"C:\privat\a.wav", "a.wav"),
        (r"\\server\freigabe\a.wav", "a.wav"),
        ("C:a.wav", "a.wav"),
        ("privat/a\x00.wav", "a.wav"),
        ("a\n\x7f\x85.wav", "a.wav"),
        ("", ""),
        (" ", " "),
        ("ä" * 252 + ".wav", "ä" * 252 + ".wav"),
    ],
)
def test_rejects_bad_names_and_exposes_only_safe_basename(name: str, basename: str) -> None:
    assert plan_batch([_item(name, b"RIFF\x24\x00\x00\x00WAVE")]) == [
        ItemVerdict(0, basename, None, "bad_name", "bad_name"),
    ]


def test_batch_limits_count_input_positions_but_charge_only_accepted_bytes() -> None:
    items = [
        _item("gross.txt", b"1234567"),
        _item("eins.txt", b"123456"),
        _item("kopie.txt", b"123456"),
        _item("spaeter.txt", b"12345"),
        _item("passt.txt", b"1234"),
        _item("danach.txt", b"a"),
        _item("ueberzahl.txt", b"b"),
    ]
    limits = BatchLimits(max_files=6, max_file_bytes=6, max_total_bytes=10)

    assert [(v.status, v.reason) for v in plan_batch(items, limits)] == [
        ("too_large", "file_size"),
        ("accepted", ""),
        ("duplicate", "same_content_as_1"),
        ("batch_limit", "total_size"),
        ("accepted", ""),
        ("batch_limit", "total_size"),
        ("batch_limit", "file_count"),
    ]


@pytest.mark.parametrize("digest", ["", "a" * 63, "g" * 64, "a" * 64 + "\n"])
def test_invalid_digest_raises_at_item_construction(digest: str) -> None:
    with pytest.raises(ValueError) as caught:
        BatchItem("datei.txt", 1, b"a", digest)
    assert isinstance(caught.value, BatchPlanError)
    assert caught.value.code == "invalid_sha256"


@pytest.mark.parametrize("size", [True, -1, float("nan"), float("inf")])
def test_invalid_size_is_not_treated_as_byte_count(size: int) -> None:
    item = _item("datei.txt", b"a")
    with pytest.raises(BatchPlanError) as caught:
        BatchItem(item.name, size, item.head, item.sha256)
    assert caught.value.code == "invalid_size"
