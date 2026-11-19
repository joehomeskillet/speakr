"""Document attachments bound to one explicit dictation (w034).

Synthetic documents only. The authorization callback is injected. No device,
ASR or word-processor evidence is claimed here.
"""
from __future__ import annotations

import hashlib
import uuid
import zipfile
from io import BytesIO

import pytest

from src.app import app, db
from src.clinic import document_ingest as di
from src.clinic.document_ingest import DocumentIngestError, attach_document, parse_document
from src.clinic.ingest import IngestService
from src.clinic.models import ClinicDictation, ClinicOriginal
from src.clinic.revisions import add_human_revision, current_revision, history
from src.models import Recording, User

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
T = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"


def _zip(members):
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in members:
            archive.writestr(name, payload)
    return buffer.getvalue()


def _docx(*paragraphs, extra=()):
    body = "".join(f'<w:p><w:r><w:t>{p}</w:t></w:r></w:p>' for p in paragraphs)
    xml = f'<?xml version="1.0"?><w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>'
    return _zip([("word/document.xml", xml), *extra])


def _odt(*paragraphs, content=None):
    body = "".join(f"<text:p>{p}</text:p>" for p in paragraphs)
    xml = content or (
        f'<?xml version="1.0"?><office:document-content xmlns:office="urn:x" xmlns:text="{T}">'
        f"<office:body><text:h>Titel</text:h>{body}</office:body></office:document-content>"
    )
    return _zip([("mimetype", "application/vnd.oasis.opendocument.text"), ("content.xml", xml)])


RTF = rb"{\rtf1\ansi\ansicpg1252\deff0 Befund \'e4hnlich.\par Zweiter Absatz.}"


@pytest.fixture
def ctx(tmp_path):
    with app.app_context():
        suffix = uuid.uuid4().hex[:10]
        users = [User(username=f"{n}{suffix}", email=f"{n}{suffix}@example.invalid", password="x")
                 for n in ("a", "b")]
        db.session.add_all(users)
        db.session.commit()
        author, other = users
        recording = Recording(user_id=author.id, status="PENDING", is_inbox=False)
        db.session.add(recording)
        db.session.flush()
        dictation = ClinicDictation(recording_id=recording.id, author_user_id=author.id)
        db.session.add(dictation)
        db.session.commit()
        yield IngestService(tmp_path), dictation.id, author.id, other.id


def _allow_author(dictation, actor):
    return dictation.author_user_id == actor


def _attach(c, data, name, *, actor=None, version=0, authorize=_allow_author):
    service, dictation_id, author, _other = c
    return attach_document(
        service, dictation_id=dictation_id, actor_user_id=actor or author, data=data,
        filename=name, expected_version=version, authorize=authorize,
    )


def test_parse_all_four_formats():
    assert parse_document("Grüsse\r\nZeile".encode(), "a.txt").text == "Grüsse\nZeile"
    assert parse_document(RTF, "a.RTF").text == "Befund ähnlich.\nZweiter Absatz."
    assert parse_document(_docx("Eins", "Zwei"), "a.docx").text == "Eins\nZwei"
    assert parse_document(_odt("Eins"), "a.odt").text == "Titel\nEins"


@pytest.mark.parametrize("data,name,code", [
    (b"x", "a.pdf", "unsupported_format"),
    (b"", "a.txt", "empty"),
    (b"\x00\x01\x02", "a.txt", "malformed"),
    (b"plain", "a.docx", "format_mismatch"),
    (b"plain", "a.rtf", "format_mismatch"),
    (_odt("x"), "a.docx", "malformed"),
    (_docx("x"), "a.odt", "malformed"),
    (RTF.replace(b"Befund", rb"{\*\objdata 00}Befund"), "a.rtf", "unsafe_archive"),
    (_docx("x", extra=[("word/vbaProject.bin", b"x")]), "a.docx", "unsafe_archive"),
    (_docx("x", extra=[("../evil", b"x")]), "a.docx", "unsafe_archive"),
    (_docx(" "), "a.docx", "empty"),
    (_zip([("word/document.xml", b"<w:document")]), "a.docx", "malformed"),
])
def test_refusals(data, name, code):
    with pytest.raises(DocumentIngestError) as exc:
        parse_document(data, name)
    assert exc.value.code == code
    assert exc.value.message


def test_xml_entity_bomb_and_size_limits(monkeypatch):
    bomb = ('<?xml version="1.0"?><!DOCTYPE d [<!ENTITY a "aaaa">]>'
            f'<w:document xmlns:w="{W}"><w:body><w:p><w:r><w:t>&a;</w:t></w:r></w:p></w:body></w:document>')
    with pytest.raises(DocumentIngestError) as exc:
        parse_document(_zip([("word/document.xml", bomb)]), "a.docx")
    assert exc.value.code == "unsafe_xml"
    with pytest.raises(DocumentIngestError) as exc:
        parse_document(_zip([("word/document.xml", b"a" * 200_000)]), "a.docx")
    assert exc.value.code == "unsafe_archive"  # ratio limit
    monkeypatch.setattr(di, "MAX_FILE_BYTES", 10)
    with pytest.raises(DocumentIngestError) as exc:
        parse_document(b"x" * 11, "a.txt")
    assert exc.value.code == "too_large"


def test_document_only_binds_original_text_and_actor(ctx):
    service, dictation_id, author, _ = ctx
    data = _docx("Befund unauffällig")
    receipt = _attach(ctx, data, "b.docx")
    assert receipt.work_state == di.WORK_DOCUMENT_ONLY and receipt.replayed is False
    original = db.session.get(ClinicOriginal, receipt.original_id)
    assert original.dictation_id == dictation_id
    assert original.origin_ref == f"document:docx:{author}"
    assert service.file_path(original.storage_key).read_bytes() == data
    assert original.content_hash == hashlib.sha256(data).hexdigest()
    dictation = db.session.get(ClinicDictation, dictation_id)
    head = current_revision(dictation)
    assert head.id == receipt.revision_id and head.body == "Befund unauffällig"
    assert head.original_version_id == original.id
    assert dictation.version_counter == 1


def test_unauthorized_and_missing_leave_no_trace(ctx):
    service, dictation_id, _author, other = ctx
    with pytest.raises(DocumentIngestError) as exc:
        _attach(ctx, b"text", "a.txt", actor=other)
    assert exc.value.code == "forbidden"
    with pytest.raises(DocumentIngestError) as exc:
        attach_document(service, dictation_id="missing", actor_user_id=other, data=b"t",
                        filename="a.txt", expected_version=0, authorize=_allow_author)
    assert exc.value.code == "not_found"
    assert db.session.query(ClinicOriginal).filter_by(dictation_id=dictation_id).count() == 0


def test_stale_version_is_refused(ctx):
    with pytest.raises(DocumentIngestError) as exc:
        _attach(ctx, b"text", "a.txt", version=3)
    assert exc.value.code == "stale_version"
    assert db.session.query(ClinicOriginal).count() == 0


def test_retry_replays_and_failed_parse_keeps_state(ctx):
    first = _attach(ctx, b"Text eins", "a.txt")
    again = _attach(ctx, b"Text eins", "a.txt", version=0)
    assert again.replayed is True and again.original_id == first.original_id
    assert again.revision_id == first.revision_id
    with pytest.raises(DocumentIngestError) as exc:
        _attach(ctx, b"plain", "b.docx", version=1)
    assert exc.value.code == "format_mismatch"
    dictation = db.session.get(ClinicDictation, ctx[1])
    assert len(history(dictation)) == 1 and dictation.version_counter == 1


def test_document_never_replaces_a_correction(ctx):
    _service, dictation_id, author, _ = ctx
    first = _attach(ctx, b"Erste Fassung", "a.txt")
    dictation = db.session.get(ClinicDictation, dictation_id)
    original = db.session.get(ClinicOriginal, first.original_id)
    add_human_revision(dictation, current_revision(dictation), "Korrigiert", author,
                       original_version=original)
    db.session.commit()
    with pytest.raises(DocumentIngestError) as exc:
        _attach(ctx, b"Neue Datei", "n.txt", version=1)
    assert exc.value.code == "text_exists"
    assert current_revision(dictation).body == "Korrigiert"
    assert db.session.query(ClinicOriginal).filter_by(dictation_id=dictation_id).count() == 1


def test_document_with_existing_audio_original(ctx):
    service, dictation_id, _author, _ = ctx
    db.session.add(ClinicOriginal(
        dictation_id=dictation_id, content_hash="a" * 64, size_bytes=4, storage_key="k",
        receipt="{}", origin_ref="agent:x"))
    db.session.commit()
    assert _attach(ctx, b"Text", "a.txt").work_state == di.WORK_WITH_AUDIO
