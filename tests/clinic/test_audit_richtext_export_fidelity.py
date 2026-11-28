import base64
import hashlib
import json
import struct
import zlib
from uuid import UUID

import pytest
from flask import Flask
from flask_login import LoginManager, UserMixin

from src.clinic.rich_text_doc import RichTextError, graphic_node, to_plain_text
from src.clinic.richtext import CanonicalDocument, Node, Segment
from src.clinic.richtext_formats import FormatError, web_html
from src.clinic.routes_bundle import PREFIX, BundleSession, create_blueprint

DID = str(UUID(int=197))
WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 24


def _chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def _png():
    header = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    pixels = b"\x00\xff\x00\x00\xff"
    return b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", header) + _chunk(b"IDAT", zlib.compress(pixels)) + _chunk(b"IEND", b"")


PNG = _png()
OBJECT_ID = hashlib.sha256(PNG).hexdigest()


def _document():
    return CanonicalDocument((
        Node("heading", "heading", (Segment(
            id="title", text="Befund", bold=True, font_family="serif", font_size_pt=18,
        ),), level=2, text_align="center", line_spacing=1.5),
        graphic_node("image", OBJECT_ID, "Röntgen"),
        Node("body", "paragraph", (Segment(
            id="text", text="Größe & Verlauf", font_family="sans-serif", font_size_pt=12,
        ),), text_align="right", line_spacing=2),
    ))


class User(UserMixin):
    def __init__(self, uid):
        self.id = uid


@pytest.fixture
def export_client(monkeypatch):
    monkeypatch.setenv("CLINIC_MODE", "1")
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="synthetic-export-fidelity")
    LoginManager(app).user_loader(lambda uid: User(int(uid)))
    state = {"session": BundleSession(_document(), WAV, 4), "graphic": PNG, "requested": []}

    def load_graphic(actor, dictation_id, object_id):
        state["requested"].append((actor, dictation_id, object_id))
        if state["graphic"] is None:
            raise RichTextError("graphic_not_found")
        return state["graphic"]

    def unused(*_args):
        raise AssertionError("not used by export")

    app.register_blueprint(create_blueprint(
        lambda actor, did: state["session"] if actor == 7 and did == DID else None,
        lambda actor, did, action: actor == 7 and did == DID and action == "export",
        unused, unused, lambda: True, load_graphic,
    ))
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = "7"
    return client, state


def _url(kind):
    return f"{PREFIX}/{DID}/export?format={kind}"


def _losses(response):
    return json.loads(response.headers["X-Export-Losses"])


def test_standalone_web_export_preserves_canonical_layout_and_local_graphic(export_client):
    client, state = export_client
    preview = client.head(_url("html"))
    assert preview.status_code == 200 and preview.data == b""
    assert _losses(preview) == []

    response = client.get(_url("html"))
    html = response.data.decode("utf-8")
    assert response.status_code == 200
    assert response.headers["Content-Type"] == "text/html; charset=utf-8"
    assert response.headers["Content-Disposition"] == 'attachment; filename="diktat.html"'
    assert response.headers["ETag"] == '"4"'
    assert response.headers["X-Content-SHA256"] == hashlib.sha256(response.data).hexdigest()
    assert response.headers["Content-Security-Policy"].startswith("default-src 'none'")
    assert html.startswith('<!doctype html><html lang="de">')
    assert '<h2 style="text-align:center;line-height:1.5">' in html
    assert "<strong>" in html and "font-family:serif;font-size:18pt" in html
    assert '<p style="text-align:right;line-height:2">' in html
    assert "font-family:sans-serif;font-size:12pt" in html
    assert "Größe &amp; Verlauf" in html
    assert f'src="data:image/png;base64,{base64.b64encode(PNG).decode("ascii")}"' in html
    assert 'alt="Röntgen"' in html
    assert 'src="local:' not in html and "https:" not in html and "http:" not in html
    assert state["requested"] == [(7, DID, OBJECT_ID), (7, DID, OBJECT_ID)]


def test_txt_is_deliberately_plain_and_restricted_formats_preview_concrete_losses(export_client):
    client, _state = export_client
    txt_preview = client.head(_url("txt"))
    txt_losses = {loss["code"] for loss in _losses(txt_preview)}
    assert {"heading", "font", "text_align", "line_spacing", "graphic", "plain_unformatted"} <= txt_losses
    assert next(loss for loss in _losses(txt_preview) if loss["code"] == "graphic")["detail"].endswith(
        "TXT enthält nur ihren Alternativtext."
    )

    txt = client.get(_url("txt"))
    assert txt.data.decode("utf-8") == to_plain_text(_document())
    assert txt.data.decode("utf-8") == "Befund\n[graphic: Röntgen]\nGröße & Verlauf"
    assert "<h2>" not in txt.text and "font-family" not in txt.text and "data:image" not in txt.text

    rtf_preview = client.head(_url("rtf"))
    rtf_losses = {loss["code"]: loss["detail"] for loss in _losses(rtf_preview)}
    assert {"heading", "font", "text_align", "line_spacing", "graphic"} <= set(rtf_losses)
    assert "plain_unformatted" not in rtf_losses and "odt_marks" not in rtf_losses
    assert rtf_losses["font"] == "Schriftart und Schriftgrösse entfallen in TXT, RTF, ODT und DOCX."
    assert _losses(client.head(_url("sdict"))) == []


def test_web_export_refuses_missing_or_tampered_graphics(export_client):
    with pytest.raises(FormatError, match="graphic_not_found"):
        web_html(_document(), {})
    with pytest.raises(FormatError, match="graphic_integrity_error"):
        web_html(_document(), {OBJECT_ID: PNG + b"tampered"})

    client, state = export_client
    state["graphic"] = None
    response = client.get(_url("html"))
    assert response.status_code == 400 and response.json["code"] == "INVALID_PAYLOAD"
