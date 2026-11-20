"""Local browser proof of the real review endpoint and P07 mount."""

from pathlib import Path

import pytest

from test_wp_mount_correction import TYPIST, _launch, _login, _open_from_worklist, live_server


@pytest.mark.parametrize('width', [1440, 390])
def test_review_live_endpoint_and_visual(live_server, width):
    from playwright.sync_api import sync_playwright

    base, dictation = live_server
    proof = Path('/tmp/speakr-v8-130-proof')
    proof.mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = _login(browser, base, TYPIST)
            page.set_viewport_size({'width': width, 'height': 900})
            _open_from_worklist(page, base)
            page.locator('[data-testid=clinic-dictation-link-review]').click()
            root = page.locator('[data-testid=clinic-review-view]')
            root.wait_for(state='visible')
            response = page.request.get(f'{base}/api/clinic/v1/dictations/{dictation}/review')
            assert response.status == 200
            binding = response.json()['binding']
            assert binding['text_revision_id'] and binding['audio_revision_id']
            page.get_by_text('Revisionsnachweis', exact=True).wait_for()
            page.screenshot(path=str(proof / f'review-{width}.png'), full_page=True)
            root.locator('[data-testid=revisions]').scroll_into_view_if_needed()
            page.screenshot(path=str(proof / f'review-evidence-{width}.png'), full_page=True)
            assert not page.js_errors, page.js_errors
            page.goto('file:///root/work/wp-backlog/ui-v8/03_Prototyp/freigabe.html')
            page.screenshot(path=str(proof / f'prototype-{width}.png'), full_page=True)
        finally:
            browser.close()
