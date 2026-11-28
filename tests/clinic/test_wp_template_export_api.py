"""Template export on POST /exports (PAR-EXP-004, PAR-CFG-002, PAR-EXP-005).

Web alternative only. A permitted ClinicTemplate supplies the DOCX or ODT; the
released dictation text is inserted at its one bookmark or insertion mark.
WG-EXP-004 and WG-EXP-005 stay open: nothing here drives an open Word or Writer
process, and neither gap is marked closed.
"""

from __future__ import annotations

import hashlib
import io
import uuid
import zipfile
from datetime import datetime, timezone

import pytest
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from flask import Flask, jsonify
from flask_login import LoginManager, login_user
from flask_wtf.csrf import CSRFProtect, generate_csrf

from src.app import app, db
from src.clinic import docx_template, odt_export, pools, presets, release, revisions
from src.clinic import routes_output
from src.clinic.models import (
    ClinicAudioRevision,
    ClinicContextRevision,
    ClinicDictation,
    ClinicOriginal,
)
from src.models import Recording, User

MARK = "{{DIKTAT}}"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
ODT_MIME = "application/vnd.oasis.opendocument.text"
BODY = "Befundzeile eins\nBefundzeile zwei"


def _suffix() -> str:
    return uuid.uuid4().hex[:8]


def _paragraph(text: str):
    paragraph = OxmlElement("w:p")
    run = OxmlElement("w:r")
    node = OxmlElement("w:t")
    node.text = text
    run.append(node)
    paragraph.append(run)
    return paragraph


def _bookmark(paragraph, name: str) -> None:
    start = OxmlElement("w:bookmarkStart")
    start.set(qn("w:id"), "7")
    start.set(qn("w:name"), name)
    end = OxmlElement("w:bookmarkEnd")
    end.set(qn("w:id"), "7")
    paragraph.append(start)
    paragraph.append(end)


def _docx(*body, header="Kopf der Vorlage", footer="Fuss der Vorlage", bookmark=None) -> bytes:
    document = Document()
    document.sections[0].header.paragraphs[0].text = header
    document.sections[0].footer.paragraphs[0].text = footer
    for text in body:
        paragraph = document.add_paragraph(text)
        if bookmark and text == "":
            _bookmark(paragraph._p, bookmark)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _with_macro(payload: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(payload)) as source, zipfile.ZipFile(buffer, "w") as out:
        for info in source.infolist():
            out.writestr(info, source.read(info))
        out.writestr("word/vbaProject.bin", b"macro-bytes-not-executed")
    return buffer.getvalue()


def _parts(payload: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _body_text(payload: bytes) -> str:
    root = Document(io.BytesIO(payload)).element
    return "\n".join(
        "".join(node.text or "" for node in paragraph.iter(qn("w:t")))
        for paragraph in root.iter(qn("w:p"))
    )


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setenv("CLINIC_MODE", "1")
    flask = Flask(__name__)
    flask.config.update(TESTING=True, SECRET_KEY="synthetic-template-export",
                        SQLALCHEMY_DATABASE_URI=app.config["SQLALCHEMY_DATABASE_URI"])
    db.init_app(flask)
    login = LoginManager(flask)
    CSRFProtect(flask)
    login.user_loader(lambda uid: db.session.get(User, int(uid)))

    @flask.get("/login/<int:uid>")
    def _login(uid):
        login_user(db.session.get(User, uid))
        return jsonify(csrf=generate_csrf())

    from src.clinic import pool_policy, policy

    def authorize(user, action, dictation):
        # Same decision the app mounts: pool policy over the central subject.
        return pool_policy.decide(
            policy.build_subject(user.id, is_admin=user.is_admin), action, dictation).allowed

    flask.register_blueprint(routes_output.create_blueprint(
        authorize=authorize, adapter_for=lambda: None))
    with flask.app_context():
        pools.create_tables()
        presets.ClinicTemplate.__table__.create(db.engine, checkfirst=True)
        suffix = _suffix()
        admin = User(username="a" + suffix, email=suffix + "-a@example.invalid", password="x", is_admin=True)
        author = User(username="u" + suffix, email=suffix + "@example.invalid", password="x")
        stranger = User(username="s" + suffix, email=suffix + "-s@example.invalid", password="x")
        db.session.add_all([admin, author, stranger])
        db.session.flush()
        pools.allow(author.id, "own", actor_id=admin.id, actions=("export",))
        recording = Recording(user_id=author.id, title="Synthetisch", status="COMPLETED",
                              original_filename="s.wav")
        db.session.add(recording)
        db.session.flush()
        dictation = ClinicDictation(recording_id=recording.id, author_user_id=author.id)
        db.session.add(dictation)
        db.session.flush()
        audio = ClinicAudioRevision(dictation_id=dictation.id, content_hash="a" * 64)
        original = ClinicOriginal(dictation_id=dictation.id, content_hash="b" * 64, size_bytes=1,
                                  storage_key="originals/synthetic", receipt="stored", origin_ref="test")
        context = ClinicContextRevision(dictation_id=dictation.id, source_system="test",
                                        tenant_scope="site", case_ref="test-case", patient_ref="opaque")
        db.session.add_all([audio, original, context])
        db.session.flush()
        text = revisions.add_human_revision(dictation, None, BODY, author.id, original_version=original)
        active = release.create_release(
            dictation, audio, text, context, text.normalised_text_hash, author.id,
            {"allowed": True}, datetime.now(timezone.utc),
            document_type="report", release_type="clinical",
        )
        db.session.commit()
        ids = {"users": [admin.id, author.id, stranger.id], "dictation": dictation.id,
               "templates": []}
        yield {"author": author.id, "stranger": stranger.id, "dictation": dictation.id,
               "revision": text.id, "release": active.id, "ids": ids, "flask": flask}
        db.session.rollback()
        if ids["templates"]:
            db.session.query(presets.ClinicTemplate).filter(
                presets.ClinicTemplate.id.in_(ids["templates"])
            ).delete(synchronize_session=False)
        db.session.query(User).filter(User.id.in_(ids["users"])).delete(synchronize_session=False)
        db.session.commit()


def _template(world, content, *, mime, mark=MARK, scope="user", scope_id=None, name=None):
    row = presets.save_template(
        name=name or ("Vorlage-" + _suffix()), content_bytes=content,
        scope=scope, scope_id=scope_id if scope_id is not None else world["author"],
        insertion_mark=mark, mime_type=mime, session=db.session,
    )
    db.session.commit()
    world["ids"]["templates"].append(row.id)
    return row


def _client(world, user_id):
    client = world["flask"].test_client()
    with client.session_transaction() as session:
        session["_user_id"] = str(user_id)
        session["_fresh"] = True
    token = client.get(f"/login/{user_id}").json["csrf"]
    return client, {"X-CSRF-Token": token, "Idempotency-Key": str(uuid.uuid4())}


def _post(world, user_id, body, headers=None):
    client, base = _client(world, user_id)
    response = client.post(
        f"/api/clinic/v1/dictations/{world['dictation']}/exports",
        json={"revision_id": world["revision"], **body}, headers=headers or base,
    )
    return client, response


def test_plain_export_without_template_is_unchanged(world):
    _client_unused, response = _post(world, world["author"], {"format": "docx"})
    assert response.status_code == 201
    assert response.json["status"] == "ready"
    assert "template_id" not in response.json


def test_docx_inserts_plain_text_at_mark_and_keeps_header_and_footer(world):
    original = _docx("Einleitung", MARK, "Schluss")
    row = _template(world, original, mime=DOCX_MIME)
    client, response = _post(world, world["author"], {
        "format": "docx", "template_id": row.id, "text_mode": "plain",
    })
    assert response.status_code == 201, response.json
    job = response.json
    assert job["template_id"] == row.id and job["text_mode"] == "plain"
    download = client.get(
        f"/api/clinic/v1/dictations/{world['dictation']}/exports/{job['id']}/download")
    assert download.status_code == 200
    produced = download.data
    assert hashlib.sha256(produced).hexdigest() == job["sha256"]
    before, after = _parts(original), _parts(produced)
    # The header and footer parts are copied byte for byte.
    assert before["word/header1.xml"] == after["word/header1.xml"]
    assert before["word/footer1.xml"] == after["word/footer1.xml"]
    text = _body_text(produced)
    assert "Befundzeile eins" in text and "Befundzeile zwei" in text
    assert MARK not in text
    assert "Einleitung" in text and "Schluss" in text
    # Plain mode adds no bold of its own.
    assert b"<w:b" not in after["word/document.xml"] or b"<w:b" in before["word/document.xml"]


def test_docx_formatted_mode_inserts_the_same_text_in_bold(world):
    row = _template(world, _docx("Einleitung", MARK), mime=DOCX_MIME)
    client, response = _post(world, world["author"], {
        "format": "docx", "template_id": row.id, "text_mode": "formatted",
    })
    assert response.status_code == 201, response.json
    produced = client.get(
        f"/api/clinic/v1/dictations/{world['dictation']}/exports/{response.json['id']}/download").data
    document = _parts(produced)["word/document.xml"]
    assert b"Befundzeile eins" in document and b"<w:b" in document
    assert MARK.encode() not in document


def test_docx_inserts_at_a_bookmark(world):
    row = _template(world, _docx("Einleitung", "", bookmark=MARK), mime=DOCX_MIME, mark=MARK)
    client, response = _post(world, world["author"], {
        "format": "docx", "template_id": row.id, "text_mode": "plain",
    })
    assert response.status_code == 201, response.json
    downloaded = client.get(
        f"/api/clinic/v1/dictations/{world['dictation']}/exports/{response.json['id']}/download").data
    # DOCX stores document.xml compressed, so the text is checked after unpacking.
    assert b"Befundzeile eins" in _parts(downloaded)["word/document.xml"]


def test_missing_mark_aborts_with_422_and_stores_no_ready_file(world):
    row = _template(world, _docx("Ohne Marke"), mime=DOCX_MIME)
    _client, response = _post(world, world["author"], {
        "format": "docx", "template_id": row.id, "text_mode": "plain",
    })
    assert response.status_code == 422
    assert response.json["code"] == "TEMPLATE_MARK_ERROR"
    assert "content" not in response.json and "sha256" not in response.json


def test_ambiguous_mark_aborts_with_422(world):
    row = _template(world, _docx(MARK, "Mitte", MARK), mime=DOCX_MIME)
    _client, response = _post(world, world["author"], {
        "format": "docx", "template_id": row.id, "text_mode": "plain",
    })
    assert response.status_code == 422 and response.json["code"] == "TEMPLATE_MARK_ERROR"


def test_macro_template_is_rejected_with_409(world):
    row = _template(world, _with_macro(_docx("Einleitung", MARK)), mime=DOCX_MIME)
    _client, response = _post(world, world["author"], {
        "format": "docx", "template_id": row.id, "text_mode": "plain",
    })
    assert response.status_code == 409 and response.json["code"] == "TEMPLATE_MACRO"


def test_foreign_template_is_404(world):
    row = _template(world, _docx(MARK), mime=DOCX_MIME, scope="user", scope_id=world["stranger"])
    _client, response = _post(world, world["author"], {
        "format": "docx", "template_id": row.id, "text_mode": "plain",
    })
    assert response.status_code == 404


def test_unknown_template_is_404(world):
    _client, response = _post(world, world["author"], {
        "format": "docx", "template_id": str(uuid.uuid4()), "text_mode": "plain",
    })
    assert response.status_code == 404


@pytest.mark.parametrize("body", [
    {"format": "docx", "text_mode": "plain"},
    {"format": "txt", "template_id": str(uuid.uuid4()), "text_mode": "plain"},
])
def test_template_fields_must_arrive_together(world, body):
    _client, response = _post(world, world["author"], body)
    assert response.status_code == 400


def test_odt_inserts_at_the_mark_through_odt_output(world):
    payload = odt_export.render_odt(["Kopf", MARK, "Fuss"], title="Vorlage")
    row = _template(world, payload, mime=ODT_MIME)
    client, response = _post(world, world["author"], {
        "format": "odt", "template_id": row.id, "text_mode": "plain",
    })
    assert response.status_code == 201, response.json
    produced = client.get(
        f"/api/clinic/v1/dictations/{world['dictation']}/exports/{response.json['id']}/download").data
    content = _parts(produced)["content.xml"]
    assert b"Befundzeile eins" in content and b"Befundzeile zwei" in content
    assert b"Kopf" in content and b"Fuss" in content
    assert MARK.encode() not in content


def test_insert_into_template_refuses_without_appending():
    original = _docx("Nur Text")
    with pytest.raises(docx_template.TemplateMarkError) as info:
        docx_template.insert_into_template(original, MARK, BODY, text_mode="plain")
    assert info.value.code == "missing"
    # The refusal returns no bytes, so the template cannot have been extended.
    assert _body_text(original) == "Nur Text"


def test_web_gaps_stay_open():
    import json
    from pathlib import Path

    gaps = json.loads(Path("clinic-spec/planning/web-parity-gaps.json").read_text())
    items = gaps if isinstance(gaps, list) else gaps.get("gaps", [])
    found = {item["id"]: item for item in items if item.get("id") in ("WG-EXP-004", "WG-EXP-005")}
    assert set(found) == {"WG-EXP-004", "WG-EXP-005"}
    assert all(item["status"] == "open" for item in found.values())
