import json
import time
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import sync_playwright

from src.app import app
from test_wp_v8_shell_nav import _launch, _login, live_server  # noqa: F401


SHORTCUTS = {
    'Neue Aufnahme': ('/#/neu', 'clinic-recording-view'),
    'Aufnahmen': ('/#/aufnahmen', 'clinic-recordings-view'),
    'Arbeitsliste': ('/#/arbeitsliste', 'clinic-worklist-view'),
}
MANIFEST_URL = '/static/manifest.json'


@pytest.mark.parametrize('clinic_mode', ['0', '1'])
def test_manifest_response_by_mode(monkeypatch, clinic_mode):
    monkeypatch.setenv('CLINIC_MODE', clinic_mode)
    client = app.test_client()
    response = client.get(MANIFEST_URL)
    assert response.status_code == 200
    manifest = response.json
    upstream = json.loads((Path(app.static_folder) / 'manifest.json').read_text(encoding='utf-8'))
    if clinic_mode == '0':
        assert manifest == upstream
        assert manifest['lang'] == 'en'
        assert [shortcut['url'] for shortcut in manifest['shortcuts']] == ['/#upload', '/#gallery']
        return

    assert response.mimetype == 'application/manifest+json'
    assert response.cache_control.no_store
    assert manifest['lang'] == 'de'
    assert manifest['id'] == upstream['id']
    assert manifest['start_url'] == upstream['start_url']
    assert manifest['scope'] == upstream['scope']
    assert [(shortcut['name'], shortcut['url']) for shortcut in manifest['shortcuts']] == [
        (name, url) for name, (url, _) in SHORTCUTS.items()
    ]
    head = client.head(MANIFEST_URL)
    assert head.status_code == 200
    assert head.mimetype == response.mimetype
    assert head.data == b''


def test_shortcut_requires_sign_in_after_idle_expiry(clinic_on, monkeypatch, world):
    monkeypatch.setattr('src.services.session_policy.idle_timeout_minutes', lambda: 1)
    client = app.test_client()
    with client.session_transaction() as session:
        session['_user_id'] = str(world['user_a'])
        session['_fresh'] = True
        session['_last_activity'] = time.time() - 120

    response = client.get('/#/neu')
    assert response.status_code == 302
    assert urlsplit(response.location).path == '/login'
    with client.session_transaction() as session:
        assert '_user_id' not in session


def test_manifest_shortcuts_use_existing_authenticated_views(live_server):
    base, _ = live_server
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        try:
            signed_in = _login(browser, base, 1280, 720)
            signed_out = browser.new_page()
            manifest_url = signed_in.locator('link[rel=manifest]').get_attribute('href')
            assert manifest_url == MANIFEST_URL
            response = signed_out.request.get(base + manifest_url)
            assert response.ok
            for shortcut in response.json()['shortcuts']:
                url, test_id = SHORTCUTS[shortcut['name']]
                assert shortcut['url'] == url

                signed_out.goto(base + url)
                assert urlsplit(signed_out.url).path == '/login'
                assert signed_out.locator('input[name=password]:visible').count() == 1
                assert signed_out.get_by_test_id(test_id).count() == 0

                signed_in.goto('about:blank')
                signed_in.goto(base + url)
                signed_in.locator('.app-loading-overlay').first.wait_for(state='detached', timeout=30000)
                signed_in.get_by_test_id(test_id).wait_for(state='visible', timeout=30000)
                assert signed_in.url == base + url
            assert not signed_in.js_errors, signed_in.js_errors
        finally:
            browser.close()
