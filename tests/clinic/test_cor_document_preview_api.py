"""Read-only document and template preview (PAR-COR-001, PAR-CFG-002).

Synthetic files only. The preview must not create a dictation, store bytes
or replace a saved revision. No device or word-processor evidence is claimed.
"""
from __future__ import annotations

import hashlib
import io
import uuid
import zipfile

import pytest
from flask import Flask, jsonify
from flask_login import LoginManager, login_user
from flask_wtf.csrf import CSRFProtect, generate_csrf

from src.app import app as main_app
from src.app import db
from src.clinic import document_ingest
from src.clinic.models import ClinicDictation, ClinicOriginal, ClinicTranscriptRevision
from src.clinic.presets import save_template
from src.clinic.revisions import add_human_revision
from src.clinic.routes_correction_documents import FILE_FIELD
from src.models import User

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
T = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
SAVED = "GESPEICHERT-BLEIBT"
KEYS = {
    "format", "text", "sha256", "size", "losses", "insertion_mark",
    "replaces_text", "text_revision_id",
}
RTF = rb"{\rtf1\ansi\ansicpg1252\deff0 Befund \'e4hnlich.\par Zweiter Absatz.}"


def _zip(members):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in members:
            archive.writestr(name, payload)
    return buffer.getvalue()


def _docx(*paragraphs, extra=()):
    body = "".join(f'<w:p><w:r><w:t>{p}</w:t></w:r></w:p>' for p in paragraphs)
    xml = f'<?xml version="1.0"?><w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>'
    return _zip([("word/document.xml", xml), *extra])


def _docx_rich():
    body = (
        '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>'
        '<w:r><w:rPr><w:b/><w:u w:val="single"/></w:rPr><w:t>Fett</w:t></w:r></w:p>'
        '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Zelle</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
        '<w:p><w:r><w:drawing/><w:t>Bild</w:t></w:r></w:p>'
    )
    xml = f'<?xml version="1.0"?><w:document xmlns:w="{W}"><w:body>{body}</w:body></w:document>'
    return _zip([("word/document.xml", xml)])


def _odt(*paragraphs):
    body = "".join(f"<text:p>{p}</text:p>" for p in paragraphs)
    xml = (
        f'<?xml version="1.0"?><office:document-content xmlns:office="urn:x" xmlns:text="{T}">'
        f"<office:body><text:h>Titel</text:h>{body}</office:body></office:document-content>"
    )
    return _zip([
        ("mimetype", "application/vnd.oasis.opendocument.text"),
        ("content.xml", xml),
    ])


def _odt_plain(text):
    xml = (
        '<?xml version="1.0"?>'
        '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"'
        f' xmlns:text="{T}">'
        f"<office:body><office:text><text:p>{text}</text:p></office:text></office:body>"
        "</office:document-content>"
    )
    return _zip([
        ("mimetype", "application/vnd.oasis.opendocument.text"),
        ("content.xml", xml),
    ])


@pytest.fixture
def api(world, monkeypatch):
    monkeypatch.setenv("CLINIC_MODE", "1")
    with main_app.app_context():
        author = world["user_a"]
        dictation = ClinicDictation(
            recording_id=world["rec_a"], author_user_id=author, pool_id="pool-a",
        )
        db.session.add(dictation)
        db.session.flush()
        original = ClinicOriginal(
            dictation_id=dictation.id, content_hash="c" * 64, size_bytes=1,
            storage_key="originals/synthetic-preview", receipt="stored", origin_ref="test",
        )
        db.session.add(original)
        db.session.flush()
        revision = add_human_revision(
            dictation, None, SAVED, author, original_version=original,
        )
        db.session.commit()
        ids = dict(
            dictation=dictation.id, author=author, other=world["user_b"],
            revision=revision.id, original=original.id,
            dictations=db.session.query(ClinicDictation).count(),
        )

    flask = Flask(__name__)
    flask.config.update(
        TESTING=True, SECRET_KEY="synthetic", WTF_CSRF_CHECK_DEFAULT=False,
        SQLALCHEMY_DATABASE_URI=main_app.config["SQLALCHEMY_DATABASE_URI"],
    )
    db.init_app(flask)
    login = LoginManager(flask)
    CSRFProtect(flask)
    login.user_loader(lambda uid: db.session.get(User, int(uid)))

    @flask.get("/login/<int:uid>")
    def _login(uid):
        login_user(db.session.get(User, uid))
        return jsonify(csrf=generate_csrf())

    from src.clinic.routes_correction_documents import create_blueprint

    flask.register_blueprint(create_blueprint(
        authorize=lambda user, action, row: action == "correction_read" and user.id == row.author_user_id,
    ))

    def login_as(uid):
        client = flask.test_client()
        token = client.get(f"/login/{uid}").json["csrf"]
        return client, {"X-CSRF-Token": token}

    client, headers = login_as(ids["author"])
    with main_app.app_context():
        yield client, headers, ids, login_as, flask


def _url(dictation_id, tail="documents/preview"):
    return f"/api/clinic/v1/dictations/{dictation_id}/{tail}"


def _post(client, headers, dictation_id, data, name):
    return client.post(
        _url(dictation_id),
        data={FILE_FIELD: (io.BytesIO(data), name)},
        headers=headers,
    )


def _saved(ids):
    db.session.expire_all()
    head = db.session.get(ClinicTranscriptRevision, ids["revision"])
    assert head is not None and head.body == SAVED
    originals = db.session.query(ClinicOriginal).filter_by(dictation_id=ids["dictation"]).all()
    assert [row.id for row in originals] == [ids["original"]]
    assert db.session.query(ClinicDictation).count() == ids["dictations"]
    dictation = db.session.get(ClinicDictation, ids["dictation"])
    assert dictation is not None and dictation.version_counter == 0


def test_four_formats_preview_only_the_saved_case(api):
    client, headers, ids, *_ = api
    cases = (
        ("Grüsse\r\nZeile".encode(), "a.txt", "txt", "Grüsse\nZeile", []),
        (RTF, "a.RTF", "rtf", "Befund ähnlich.\nZweiter Absatz.", []),
        (_docx("Eins", "Zwei"), "a.DOCX", "docx", "Eins\nZwei", []),
        (_odt_plain("Eins"), "a.odt", "odt", "Eins", []),
    )
    for data, name, fmt, text, losses in cases:
        response = _post(client, headers, ids["dictation"], data, name)
        assert response.status_code == 200, (name, response.get_json(silent=True))
        body = response.json
        assert set(body) == KEYS
        assert body["format"] == fmt and body["text"] == text
        assert body["losses"] == losses and body["insertion_mark"] is None
        assert body["replaces_text"] is False
        assert body["text_revision_id"] == ids["revision"]
        assert body["sha256"] == hashlib.sha256(data).hexdigest()
        assert body["size"] == len(data)
        assert response.headers["Cache-Control"] == "no-store"
        assert SAVED not in body["text"]
    _saved(ids)


def test_losses_are_shown_and_saved_text_stays(api):
    client, headers, ids, *_ = api
    data = _docx_rich()
    response = _post(client, headers, ids["dictation"], data, "reich.docx")
    assert response.status_code == 200, response.json
    assert response.json["text"] == "Fett\nZelle\nBild"
    assert [item["code"] for item in response.json["losses"]] == [
        "table", "graphic", "underline", "heading", "marks",
    ]
    assert all(item["detail"] for item in response.json["losses"])
    heading = _post(client, headers, ids["dictation"], _odt("Eins"), "a.odt")
    assert heading.json["text"] == "Titel\nEins"
    assert [item["code"] for item in heading.json["losses"]] == ["heading"]
    underlined = rb"{\rtf1\ansi {\ul Unterstrichen}\par}"
    rtf = _post(client, headers, ids["dictation"], underlined, "a.rtf")
    assert rtf.status_code == 200, rtf.json
    assert "Unterstrichen" in rtf.json["text"]
    assert [item["code"] for item in rtf.json["losses"]] == ["underline"]
    _saved(ids)


@pytest.mark.parametrize("data,name,code,status", [
    (b"%PDF-1.4", "a.pdf", "unsupported_format", 400),
    (b"", "a.txt", "empty", 400),
    (b"plain", "a.docx", "format_mismatch", 400),
    (RTF.replace(b"Befund", rb"{\*\objdata 00}Befund"), "a.rtf", "unsafe_archive", 400),
    (_docx("x", extra=[("word/vbaProject.bin", b"x")]), "a.docx", "unsafe_archive", 400),
    (_zip([("word/document.xml", b"a" * 200_000)]), "a.docx", "unsafe_archive", 400),
])
def test_macros_bombs_and_bad_types_are_refused(api, data, name, code, status):
    client, headers, ids, *_ = api
    response = _post(client, headers, ids["dictation"], data, name)
    assert response.status_code == status
    assert response.json["code"] == code
    assert "x" * 50 not in response.get_data(as_text=True)
    _saved(ids)


def test_oversized_file_is_refused(api, monkeypatch):
    client, headers, ids, *_ = api
    monkeypatch.setattr(document_ingest, "MAX_FILE_BYTES", 10)
    response = _post(client, headers, ids["dictation"], b"x" * 11, "a.txt")
    assert (response.status_code, response.json["code"]) == (413, "too_large")
    _saved(ids)


def test_foreign_and_missing_do_not_parse(api, monkeypatch):
    client, headers, ids, login_as, _flask = api
    def refuse_parse(*_args, **_kwargs):
        raise AssertionError("parsed")

    monkeypatch.setattr(document_ingest, "parse_document", refuse_parse)
    other, other_headers = login_as(ids["other"])
    secret = _docx("GEHEIM-DATEI")
    foreign = _post(other, other_headers, ids["dictation"], secret, "a.docx")
    assert foreign.status_code == 404
    assert b"GEHEIM-DATEI" not in foreign.data
    missing = _post(client, headers, uuid.uuid4(), secret, "a.docx")
    assert missing.status_code == 404
    assert db.session.query(ClinicDictation).count() == ids["dictations"]
    _saved(ids)


def test_anonymous_csrf_and_clinic_off(api, monkeypatch):
    client, headers, ids, _login_as, flask = api
    url_args = (ids["dictation"], _docx("VORSCHAU-NEU"), "a.docx")
    assert flask.test_client().post(_url(ids["dictation"])).status_code == 401
    assert _post(client, {}, *url_args).status_code == 403
    assert _post(client, headers, ids["dictation"], b"", "").status_code == 400
    monkeypatch.delenv("CLINIC_MODE", raising=False)
    assert _post(client, headers, *url_args).status_code == 404
    _saved(ids)


def test_template_preview_does_not_replace_saved_text(api):
    client, _headers, ids, *_ = api
    author = save_template(
        "Autor", _docx("VORLAGE-AUTOR"), scope="user", scope_id=ids["author"],
        insertion_mark="[[CURSOR]]", mime_type=DOCX_MIME,
    )
    system = save_template("System", _odt_plain("VORLAGE-SYSTEM"), scope="system", mime_type=(
        "application/vnd.oasis.opendocument.text"
    ))
    pool = save_template(
        "Pool", _docx("VORLAGE-POOL"), scope="pool", scope_id="pool-a", mime_type=DOCX_MIME,
    )
    db.session.commit()
    for row, text, mark in (
        (author, "VORLAGE-AUTOR", "[[CURSOR]]"),
        (system, "VORLAGE-SYSTEM", None),
        (pool, "VORLAGE-POOL", None),
    ):
        response = client.get(_url(ids["dictation"], f"templates/{row.id}/preview"))
        assert response.status_code == 200, (row.name, response.json)
        assert response.json["text"] == text
        assert response.json["insertion_mark"] == mark
        assert response.json["replaces_text"] is False
        assert response.json["text_revision_id"] == ids["revision"]
        assert response.headers["Cache-Control"] == "no-store"
    _saved(ids)


def test_foreign_inactive_and_unsafe_templates_stay_unread(api):
    client, _headers, ids, *_ = api
    foreign = save_template(
        "Fremd", _docx("GEHEIM-VORLAGE"), scope="user", scope_id=ids["other"], mime_type=DOCX_MIME,
    )
    other_pool = save_template(
        "Fremder Pool", _docx("POOL-GEHEIM"), scope="pool", scope_id="pool-b", mime_type=DOCX_MIME,
    )
    inactive = save_template(
        "Inaktiv", _docx("INAKTIV-GEHEIM"), scope="user", scope_id=ids["author"], mime_type=DOCX_MIME,
    )
    inactive.is_active = False
    macro = save_template(
        "Makro", _docx("x", extra=[("word/vbaProject.bin", b"x")]),
        scope="user", scope_id=ids["author"], mime_type=DOCX_MIME,
    )
    pdf = save_template(
        "Pdf", b"%PDF-1.4", scope="system", mime_type="application/pdf",
    )
    db.session.commit()
    for row, secret in (
        (foreign, "GEHEIM-VORLAGE"),
        (other_pool, "POOL-GEHEIM"),
        (inactive, "INAKTIV-GEHEIM"),
    ):
        response = client.get(_url(ids["dictation"], f"templates/{row.id}/preview"))
        assert response.status_code == 404
        assert secret.encode() not in response.data
    refused = client.get(_url(ids["dictation"], f"templates/{macro.id}/preview"))
    assert (refused.status_code, refused.json["code"]) == (400, "unsafe_archive")
    wrong = client.get(_url(ids["dictation"], f"templates/{pdf.id}/preview"))
    assert (wrong.status_code, wrong.json["code"]) == (400, "unsupported_format")
    missing = client.get(_url(ids["dictation"], f"templates/{uuid.uuid4()}/preview"))
    assert missing.status_code == 404
    _saved(ids)


def test_preview_never_attaches(api, monkeypatch):
    client, headers, ids, *_ = api

    def boom(*_args, **_kwargs):
        raise AssertionError("preview must not attach bytes")

    monkeypatch.setattr(document_ingest, "attach_document", boom)
    response = _post(client, headers, ids["dictation"], _docx("NurVorschau"), "a.docx")
    assert response.status_code == 200 and response.json["text"] == "NurVorschau"
    _saved(ids)
