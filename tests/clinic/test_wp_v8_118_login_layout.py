"""V8 P20 login page layout (v8-118): prototype structure in CLINIC_MODE, upstream login unchanged with it off."""

from __future__ import annotations

import pytest
from lxml import html

from src.app import app


def login_html(monkeypatch, clinic: bool) -> str:
    if clinic:
        monkeypatch.setenv("CLINIC_MODE", "1")
    else:
        monkeypatch.delenv("CLINIC_MODE", raising=False)
    monkeypatch.setitem(app.config, "WTF_CSRF_ENABLED", True)
    response = app.test_client().get("/login")
    assert response.status_code == 200
    return response.get_data(as_text=True)


def test_clinic_login_uses_the_v8_page_structure(monkeypatch):
    html = login_html(monkeypatch, True)
    assert "css/clinic-v8.css" in html
    assert '<body class="v8-ui">' in html
    assert 'class="pagehead"' in html
    assert html.count('class="panel"') == 2
    assert 'class="topbar"' in html
    assert 'name="remember"' not in html
    assert "data-password-toggle" in html
    assert "Gestaltungsvorschau" not in html


def test_clinic_login_keeps_the_form_contract(monkeypatch):
    html = login_html(monkeypatch, True)
    assert 'name="csrf_token"' in html
    assert 'name="email"' in html
    assert 'name="password"' in html
    assert 'method="POST"' in html


def test_upstream_login_has_no_v8_markup(monkeypatch):
    html = login_html(monkeypatch, False)
    for marker in ("clinic-v8.css", "v8-ui", "pagehead", "login-ui.js", "data-password-toggle"):
        assert marker not in html
    assert 'name="remember"' in html


@pytest.mark.parametrize(("category", "variant", "role"), [("danger", "burgundy", "alert"), ("warning", "warning", "status")])
def test_clinic_flash_uses_dark_theme_colours(monkeypatch, category, variant, role):
    monkeypatch.setenv("CLINIC_MODE", "1")
    client = app.test_client()
    with client.session_transaction() as session:
        session["_flashes"] = [(category, "Testmeldung")]
    page = html.fromstring(client.get("/login").data)
    note, = page.xpath("//div[text()='Testmeldung']")
    assert note.get("role") == role
    assert {"note", variant} <= note.classes
    for theme in ("[.dark_&]", "[[data-theme=dark]_&]"):
        assert f"{theme}:!text-[color:var(--v8-text)]" in note.classes
        if category == "warning":
            assert f"{theme}:!bg-[var(--v8-s2)]" in note.classes
            assert f"{theme}:!border-[color:var(--line)]" in note.classes
