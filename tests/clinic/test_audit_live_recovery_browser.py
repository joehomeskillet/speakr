"""PAR-LIVE-003 recovery in Chromium against real Flask routes and saved audio."""

from __future__ import annotations

from base64 import b64encode
from datetime import timedelta
from threading import Thread
from urllib.parse import urlsplit

import pytest
from flask import jsonify
from flask_login import login_required
from flask_wtf.csrf import generate_csrf
from playwright.sync_api import Browser, Locator, Page, Request, Response, expect, sync_playwright
from werkzeug.serving import make_server

from src.clinic import live_session, revisions, routes_live
from src.clinic.models import ClinicDictation
from src.database import db
from test_audit_live_recovery_api import NOW, _save, _text, _wav, recovery  # noqa: F401
from test_audit_live_session_browser import ART, HTML, ROOT, VIEWPORTS, _theme
from test_wp_mount_correction import _launch

MOUNT = HTML.split('window.calls = [];', 1)[0] + '''
Vue.createApp(LiveView, {
    dictationId: new URLSearchParams(location.search).get('dictation'), vue: Vue,
    onReady(controller) { window.live = controller; },
}).mount('#app');
</script></body></html>'''


def _capture(page: Page, label: str, clips: tuple[bytes, ...] = ()) -> None:
    ART.mkdir(parents=True, exist_ok=True)
    page.locator('link[rel=stylesheet]').evaluate_all(
        'links => { if (!links.every(link => link.sheet?.cssRules.length)) throw Error("Missing CSS"); }'
    )
    for width, height in VIEWPORTS:
        page.set_viewport_size({'width': width, 'height': height})
        for theme in ('light', 'dark'):
            _theme(page, theme)
            page.evaluate('scrollTo(0, 0)')
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), (width, theme)
            page.screenshot(path=ART / f'live-recovery-{label}-{width}x{height}-{theme}.png',
                            full_page=bool(clips), animations='disabled')
            if width == VIEWPORTS[0][0]:
                # Keep the captured appearance and sample audio usable without the test server.
                html = page.evaluate('''(audioSources) => {
                    const snapshot = document.documentElement.cloneNode(true);
                    snapshot.querySelectorAll('script, meta[name="csrf-token"]').forEach(e => e.remove());
                    const liveLinks = document.querySelectorAll('link[rel=stylesheet]');
                    snapshot.querySelectorAll('link[rel=stylesheet]').forEach((link, i) => {
                        const style = document.createElement('style');
                        // tokens.css is also linked directly; drop its duplicate @import for offline use.
                        style.textContent = [...liveLinks[i].sheet.cssRules]
                            .filter(rule => rule.type !== CSSRule.IMPORT_RULE)
                            .map(rule => rule.cssText).join('\\n');
                        link.replaceWith(style);
                    });
                    snapshot.querySelectorAll('audio').forEach((audio, i) => audio.src = audioSources[i]);
                    return '<!doctype html>\\n' + snapshot.outerHTML;
                }''', ['data:audio/wav;base64,' + b64encode(clip).decode('ascii') for clip in clips])
                (ART / f'live-recovery-{label}-{theme}.html').write_text(html, encoding='utf-8')


def _open(browser: Browser, base: str, owner: int, dictation: str) -> tuple[
    Page, list[str], list[Request], list[Response]
]:
    context = browser.new_context(viewport={'width': 1440, 'height': 900})
    assert context.cookies() == []
    page = context.new_page()
    errors: list[str] = []
    posts: list[Request] = []
    resumes: list[Response] = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.on('request', lambda request: posts.append(request) if request.method == 'POST' else None)
    page.on('response', lambda response: resumes.append(response)
            if urlsplit(response.url).path.endswith('/resume') else None)
    assert context.request.get(f'{base}/login/{owner}').ok
    url = f'{base}/live-proof?dictation={dictation}'
    with page.expect_response(lambda response: urlsplit(response.url).path.endswith('/resume'), timeout=5000):
        navigation = page.goto(url)
        assert navigation is not None and navigation.ok
    return page, errors, posts, resumes


def _confirmed(page: Page, count: int) -> Locator:
    phrases = page.get_by_test_id('live-phrases')
    rows = phrases.locator('li[data-state="server_saved"]')
    expect(rows).to_have_count(count)
    expect(phrases.locator('li:not([data-state="server_saved"])')).to_have_count(0)
    for sequence in range(1, count + 1):
        row = rows.nth(sequence - 1)
        expect(row.get_by_text(_text(sequence), exact=True)).to_be_visible()
        expect(row.locator('audio[controls]')).to_have_count(1)
    return rows


def _play_original(page: Page, rows: Locator, sequence: int) -> str:
    row = rows.filter(has=page.get_by_text(_text(sequence), exact=True))
    player = row.locator('audio[controls]')
    expect(player).to_be_visible()
    url = player.evaluate('(audio) => audio.src')
    assert urlsplit(url).netloc == urlsplit(page.url).netloc
    response = page.request.get(url)
    assert response.status == 200 and response.body() == _wav(sequence)
    player.evaluate('(audio) => audio.play()')
    page.wait_for_function('''(url) => [...document.querySelectorAll('audio')]
        .some(audio => audio.src === url && audio.ended && audio.currentTime > 0 && !audio.error)''', arg=url)
    return url


@pytest.mark.parametrize('count', [3, 51])
def test_fresh_context_recovers_confirmed_phrases(recovery, count: int) -> None:  # noqa: F811
    flask, store, _, dictation, _, owner, _, _ = recovery
    flask.static_folder = str(ROOT / 'static')
    flask.add_url_rule('/live-proof', 'live_proof', login_required(lambda: MOUNT))
    flask.add_url_rule('/api/csrf-token', 'csrf_token', login_required(
        lambda: jsonify(csrf_token=generate_csrf())))
    server = make_server('127.0.0.1', 0, flask, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    resume_path = f'{routes_live.PREFIX}/dictations/{dictation}/resume'
    try:
        with sync_playwright() as playwright:
            browser = _launch(playwright)
            page, errors, posts, resumes = _open(browser, base, owner, dictation)
            expect(page.get_by_test_id('clinic-live-view')).to_have_attribute('data-phase', 'idle')
            expect(page.get_by_role('button', name='Starten', exact=True)).to_be_enabled()
            _confirmed(page, 0)
            assert [urlsplit(request.url).path for request in posts] == [resume_path]
            assert resumes[0].status == 200 and resumes[0].json()['session_id'] is None
            if count == 3:
                _capture(page, 'empty')

            with flask.app_context():
                sid = live_session.start(dictation, owner, NOW - timedelta(seconds=1)).id
                db.session.commit()
                receipt = None
                for sequence in range(1, count + 1):
                    receipt = _save(store, dictation, sid, owner, sequence, receipt)
                active = sid
                if count == 3:
                    live_session.end(sid, owner, NOW)
                    active = live_session.start(dictation, owner, NOW).id
                db.session.commit()

            page.reload()
            expect(page.get_by_test_id('clinic-live-view')).to_have_attribute('data-phase', 'paused')
            rows = _confirmed(page, count)
            original = [_play_original(page, rows, sequence) for sequence in (1, count)]
            if count == 3:
                # Interim engine text belongs to this tab, not to the confirmed draft.
                page.get_by_role('button', name='Fortsetzen', exact=True).click()
                expect(page.get_by_test_id('clinic-live-view')).to_have_attribute('data-phase', 'live')
                page.evaluate('window.live.partial("Noch nicht bestätigter Satz.")')
                expect(page.locator('li[data-state="partial"]')).to_contain_text('Noch nicht bestätigter Satz.')
                expect(rows).to_have_count(count)
            assert not errors, errors
            page.context.close()

            with flask.app_context():
                draft = db.session.get(ClinicDictation, dictation)
                assert draft is not None
                correction = 'Korrigierter Entwurf, Originalaufnahme bleibt erhalten.'
                revisions.add_human_revision(draft, revisions.current_revision(draft), correction, owner)
                db.session.commit()
                corrected = revisions.current_revision(draft)
            assert corrected is not None and corrected.body == correction

            page, errors, posts, resumes = _open(browser, base, owner, dictation)
            expect(page.get_by_test_id('clinic-live-view')).to_have_attribute('data-phase', 'paused')
            rows = _confirmed(page, count)
            expect(page.get_by_text('Noch nicht bestätigter Satz.', exact=True)).to_have_count(0)
            assert [_play_original(page, rows, sequence) for sequence in (1, count)] == original
            assert [urlsplit(request.url).path for request in posts] == [resume_path, resume_path]
            assert all(response.status == 200 for response in resumes)
            first, last = (response.json() for response in resumes)
            assert first['session_id'] == active and last['next_cursor'] is None
            if count == 51:
                cursor = {'session_id': sid, 'after_sequence': 50}
                assert first['next_cursor'] == cursor
                assert posts[1].post_data_json in ({'cursor': cursor}, cursor)
                assert [len(result['events']) for result in (first, last)] == [50, 1]
            else:
                assert first['next_cursor'] is None and first['previous_session_id'] == sid
                assert posts[1].post_data_json in ({'session_id': sid}, {'session_id': sid, 'after_sequence': 0})
                assert last['session_id'] == sid and last['previous_session_id'] is None
                _capture(page, 'rows', tuple(_wav(sequence) for sequence in range(1, 4)))
            assert not errors, errors
            page.context.close()
            browser.close()
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
