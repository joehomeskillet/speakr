"""V8 P20 login page layout (v8-118): prototype structure in CLINIC_MODE, upstream login unchanged with it off."""

from __future__ import annotations

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
