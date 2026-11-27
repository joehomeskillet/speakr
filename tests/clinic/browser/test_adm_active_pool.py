"""P-157: active resource selection, vocabulary isolation and revocation in Flask.

Set P157_ARTIFACTS to an output directory to save full-page PNGs and rendered
DOM snapshots across the shared V8 viewport/theme matrix.
"""

from collections.abc import Callable
from io import BytesIO
import os
from pathlib import Path
import sqlite3
import wave

from playwright.sync_api import Browser, Page, expect

import v8_browser_support as support

API = '/api/clinic/v1'
ACTIVE_POOL = f'{API}/preferences/active-pool'
POOLS = f'{API}/pools'
POOL_A, POOL_B = 'Beispielgruppe A', 'Beispielgruppe B'
BETA_EMAIL = 'beta-p157@local.test'
SHARED_WORD, ONLY_A = 'Musterbegriff', 'Ergänzungswort'
SPOKEN_A, SPOKEN_B = 'muster alpha', 'muster beta'

SEED = f'''
    from src.clinic import presets, vocabulary
    from src.clinic.app_wiring import set_active_author_pool
    beta = user('beta-p157', {BETA_EMAIL!r})
    pool.name = {POOL_A!r}
    db.session.commit()
    b = pools.create_author_pool({POOL_B!r}, actor_id=admin.id)
    pools.link_staff_pool(b.id, staff.id, actor_id=admin.id)
    pools.add_author_member(pool.id, beta.id, actor_id=admin.id)
    pools.add_author_member(b.id, author.id, actor_id=admin.id)
    pools.add_author_member(b.id, admin.id, actor_id=admin.id)
    set_active_author_pool(admin.id, b.id)
    vocabulary.add_vocabulary_entry('pool', {SHARED_WORD!r}, {SPOKEN_A!r}, pool_id=pool.id)
    vocabulary.add_vocabulary_entry('pool', {SHARED_WORD!r}, {SPOKEN_B!r}, pool_id=b.id)
    vocabulary.add_vocabulary_entry('pool', {ONLY_A!r}, pool_id=pool.id)
    vocabulary.add_vocabulary_entry('user', 'Mein Wort', user_id=author.id)
    for selected in (pool, b):
        vocabulary.add_voice_command('pool', 'Musterbefehl', pool_id=selected.id, description=selected.name)
        vocabulary.add_pronunciation_hint('pool', {SHARED_WORD!r}, selected.name, pool_id=selected.id)
        presets.save_template(selected.name, b'synthetic', scope='pool', scope_id=selected.id)
    db.session.commit()
'''
SERVER = support.SERVER.replace('server = make_server', SEED + '\nserver = make_server')


def _open_view(page: Page, base: str, view: str) -> None:
    if view == 'admin-wortschatz':
        page.goto(base + '/admin#/wortschatz')
    else:
        support._open_shell_view(page, base, '#/' + view)
    if view == 'ressourcen':
        expect(page.locator('.clinic-resources-view')).to_be_visible(timeout=support.SHOW_TIMEOUT_MS)


def _select_pool(page: Page, pool_id: str) -> None:
    picker = page.get_by_test_id('active-pool-select')
    with page.expect_response(lambda r: r.url.endswith(ACTIVE_POOL) and r.request.method == 'PUT') as saved:
        picker.select_option(pool_id)
    assert saved.value.ok, saved.value.text()
    assert saved.value.json()['pool_id'] == pool_id
    expect(picker).to_have_value(pool_id)
    expect(page.get_by_test_id('active-pool-status')).to_have_text('Auswahl gespeichert.')


def _assert_words(page: Page, pool_id: str, spoken: str) -> None:
    view = page.locator('.vocabulary-ui')
    picker = view.get_by_label('Wortliste', exact=True)
    expect(picker).to_have_value(pool_id)
    assert picker.evaluate('(el) => el.getBoundingClientRect().height') >= 48
    rows = view.locator('tbody tr')
    shared = rows.filter(has=page.get_by_role('cell', name=SHARED_WORD, exact=True))
    expect(shared).to_have_count(1)
    expect(shared.get_by_role('cell', name=spoken, exact=True)).to_be_visible()
    expect(rows).to_have_count(2 if spoken == SPOKEN_A else 1)
    expect(view.get_by_role('cell', name=ONLY_A, exact=True)).to_have_count(int(spoken == SPOKEN_A))
    expect(view.get_by_role('cell', name=SPOKEN_B if spoken == SPOKEN_A else SPOKEN_A, exact=True)).to_have_count(0)


def _assert_effective_resources(page: Page, base: str, *, pool: str | None, previous_etag: str | None = None) -> str:
    def get(path: str, headers: dict[str, str] | None = None):
        response = page.request.get(base + API + path, headers=headers)
        assert response.ok, response.text()
        return response

    words = get('/vocabulary/export?scope=effective').json()['content'].splitlines()
    expected = ['Mein Wort']
    if pool == POOL_A:
        expected.extend([ONLY_A, f'{SHARED_WORD}\t{SPOKEN_A}'])
    if pool == POOL_B:
        expected.append(f'{SHARED_WORD}\t{SPOKEN_B}')
    assert set(words) == set(expected)
    catalog = get('/resources/commands', {'If-None-Match': previous_etag} if previous_etag else None)
    assert [item['description'] for item in catalog.json()['items']] == ([pool] if pool else [])
    hints = get('/resources/pronunciations').json()['items']
    assert [item['phonetic_hint'] for item in hints] == ([pool] if pool else [])
    templates = get('/templates').json()['items']
    assert [item['name'] for item in templates if item['scope'] == 'pool'] == ([pool] if pool else [])
    etag = catalog.headers['etag']
    assert etag != previous_etag
    return etag


def _assert_empty_words(page: Page, *, revoked_pool: str) -> None:
    view = page.locator('.vocabulary-ui')
    picker = view.get_by_label('Wortliste', exact=True)
    expect(picker).to_have_value('')
    expect(picker.get_by_role('option', name=revoked_pool, exact=True)).to_have_count(0)
    expect(view.get_by_text('Noch keine Wörter für diese Auswahl.' if page.url.endswith('/admin#/wortschatz')
                            else 'Keine Einträge', exact=False)).to_be_visible()
    expect(view.locator('tbody tr')).to_have_count(0)


def _csrf_headers(page: Page) -> dict[str, str]:
    token = page.locator('meta[name="csrf-token"]').get_attribute('content')
    assert token
    return {'X-CSRF-Token': token}


def _memberships(page: Page, base: str) -> dict:
    response = page.request.get(base + POOLS)
    assert response.ok, response.text()
    return response.json()


def _set_members(page: Page, base: str, catalog: dict, pool: dict, members: list[int]) -> None:
    response = page.request.put(
        f'{base}{POOLS}/author/{pool["id"]}', headers=_csrf_headers(page),
        data={'expected_revision': catalog['revision'], 'name': pool['name'], 'member_user_ids': members},
    )
    assert response.ok, response.text()
    updated = next(p for p in response.json()['author_pools'] if p['id'] == pool['id'])
    assert updated['member_user_ids'] == members


def _upload_dictation(page: Page, base: str, sample: int, *, expected_status: int) -> dict:
    audio = BytesIO()
    with wave.open(audio, 'wb') as wav:
        wav.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        wav.writeframes(bytes((sample, 0)) * 16000)
    response = page.request.post(base + '/upload', headers=_csrf_headers(page), multipart={
        'file': {'name': f'p157-{sample}.wav', 'mimeType': 'audio/wav', 'buffer': audio.getvalue()},
        'client_upload_id': f'p157-{sample}', 'use_speech_recognition': 'false',
    })
    assert response.status == expected_status, response.text()
    return response.json()


def _assert_dictation_pool(db_path: Path, dictation_id: str, pool_id: str) -> None:
    # The upload receipt doesn't expose the assigned pool; inspect the isolated DB read-only.
    connection = sqlite3.connect(db_path.as_uri() + '?mode=ro', uri=True)
    try:
        assert connection.execute('SELECT pool_id FROM clinic_dictation WHERE id = ?', (dictation_id,)).fetchone() == (pool_id,)
    finally:
        connection.close()


def _save_dom(page: Page, path: Path) -> None:
    # Freeze the mounted DOM (do not boot the app or retain its session token on replay).
    # Local stylesheet URLs still resolve when the fixture server has stopped.
    html = page.evaluate('''staticBase => {
        const root = document.documentElement.cloneNode(true);
        root.querySelectorAll('script, meta[name="csrf-token"]').forEach(el => el.remove());
        root.querySelectorAll('link[href^="/static/"], img[src^="/static/"]').forEach(el => {
            const attr = el.tagName === 'LINK' ? 'href' : 'src';
            el.setAttribute(attr, new URL(el.getAttribute(attr).slice(1).split('?')[0], staticBase).href);
        });
        const options = root.querySelectorAll('select option');
        document.querySelectorAll('select option').forEach((option, i) => {
            options[i].toggleAttribute('selected', option.selected);
        });
        return '<!doctype html>\\n' + root.outerHTML;
    }''', support.ROOT.as_uri() + '/')
    # The workspace is the static-file origin for these offline snapshots.
    path.write_text(html, encoding='utf-8')


def _capture(browser: Browser, base: str, state: dict, view: str, variant: str, ready: Callable[[Page], None]) -> None:
    destination = os.environ.get('P157_ARTIFACTS')
    if not destination:
        return
    out = Path(destination)
    out.mkdir(parents=True, exist_ok=True)
    for size, (width, height) in support.VIEWPORTS.items():
        for theme in support.THEMES:
            with browser.new_context(storage_state=state, viewport={'width': width, 'height': height},
                                     **support.theme_context_options(theme)) as context:
                context.add_init_script(support.theme_init_script(theme))
                page = context.new_page()
                _open_view(page, base, view)
                ready(page)
                page.evaluate('document.fonts.ready.then(() => window.scrollTo(0, 0))')
                assert page.evaluate("document.documentElement.classList.contains('dark')") == (theme == 'dark')
                stem = out / f'{view}-{variant}-{size}-{theme}'
                page.screenshot(path=stem.with_suffix('.png'), full_page=True, animations='disabled')
                _save_dom(page, stem.with_suffix('.html'))


def test_active_pool_is_saved_scoped_and_invalidated(browser: Browser, tmp_path: Path) -> None:
    with support.serve_app(tmp_path, SERVER) as base:
        _exercise_active_pool(browser, base, tmp_path / 'v860.db')


def _exercise_active_pool(browser: Browser, base: str, db_path: Path) -> None:
    alpha_state = support.login(browser, base, support.ROLES['author'])
    beta_state = support.login(browser, base, BETA_EMAIL)
    admin_state = support.login(browser, base, support.ROLES['admin'])
    with browser.new_context(storage_state=beta_state) as beta_context:
        beta = beta_context.new_page()
        _open_view(beta, base, 'einstellungen')
        beta_pool = beta.get_by_test_id('active-pool-select')
        expect(beta_pool.get_by_role('option', name=POOL_A, exact=True)).to_have_count(1)
        expect(beta_pool.get_by_role('option', name=POOL_B, exact=True)).to_have_count(0)
        a_id = beta_pool.input_value()
        assert a_id
        _open_view(beta, base, 'ressourcen')
        _assert_words(beta, a_id, SPOKEN_A)

    with (browser.new_context(storage_state=alpha_state) as alpha_context,
          browser.new_context(storage_state=admin_state) as admin_context):
        alpha, admin = alpha_context.new_page(), admin_context.new_page()
        _open_view(alpha, base, 'einstellungen')
        settings = alpha.get_by_test_id('active-pool-settings')
        picker = settings.get_by_test_id('active-pool-select')
        expect(picker).to_have_value(a_id)
        assert picker.evaluate('(el) => el.getBoundingClientRect().height') >= 48
        b_option = picker.get_by_role('option', name=POOL_B, exact=True)
        expect(b_option).to_have_count(1)
        b_id = b_option.get_attribute('value')
        assert b_id and b_id != a_id
        _open_view(alpha, base, 'ressourcen')
        _assert_words(alpha, a_id, SPOKEN_A)
        a_etag = _assert_effective_resources(alpha, base, pool=POOL_A)

        _open_view(alpha, base, 'einstellungen')
        _select_pool(alpha, b_id)
        _open_view(alpha, base, 'ressourcen')
        _assert_words(alpha, b_id, SPOKEN_B)
        _assert_effective_resources(alpha, base, pool=POOL_B, previous_etag=a_etag)
        alpha.reload()
        _assert_words(alpha, b_id, SPOKEN_B)
        _open_view(alpha, base, 'einstellungen')
        expect(picker).to_have_value(b_id)
        created = _upload_dictation(alpha, base, 1, expected_status=202)
        _assert_dictation_pool(db_path, created['dictation_id'], b_id)

        _open_view(admin, base, 'admin-wortschatz')
        _assert_words(admin, b_id, SPOKEN_B)
        _capture(browser, base, admin_state, 'admin-wortschatz', 'rows', lambda p: _assert_words(p, b_id, SPOKEN_B))
        _capture(browser, base, alpha_state, 'einstellungen', 'rows',
                 lambda p: expect(p.get_by_test_id('active-pool-select')).to_have_value(b_id))
        _capture(browser, base, alpha_state, 'ressourcen', 'rows', lambda p: _assert_words(p, b_id, SPOKEN_B))

        _select_pool(alpha, a_id)
        catalog = _memberships(admin, base)
        pools = {p['id']: p for p in catalog['author_pools']}
        alpha_a = next(p for p in _memberships(alpha, base)['author_pools'] if p['id'] == a_id)
        alpha_id, = alpha_a['member_user_ids']
        a_members = pools[a_id]['member_user_ids']
        assert alpha_id in a_members and len(a_members) == 2
        _set_members(admin, base, catalog, pools[a_id], [uid for uid in a_members if uid != alpha_id])

        def needs_reselection(page: Page) -> None:
            select = page.get_by_test_id('active-pool-select')
            expect(select).to_have_value('')
            expect(select.get_by_role('option', name=POOL_A, exact=True)).to_have_count(0)
            expect(select.get_by_role('option', name=POOL_B, exact=True)).to_have_count(1)
            expect(page.get_by_test_id('active-pool-status')).to_contain_text('Bitte wählen Sie neu.')

        alpha.reload()
        needs_reselection(alpha)
        _assert_effective_resources(alpha, base, pool=None, previous_etag=a_etag)
        denied = _upload_dictation(alpha, base, 2, expected_status=403)
        assert denied['code'] == 'clinic_author_pool_reselection_required'
        _open_view(alpha, base, 'ressourcen')
        expect(alpha.locator('.vocabulary-ui').get_by_label('Wortliste', exact=True)).to_have_value('')
        expect(alpha.get_by_role('option', name=POOL_A, exact=True)).to_have_count(0)
        expect(alpha.get_by_role('cell', name='Mein Wort', exact=True)).to_be_visible()
        expect(alpha.get_by_role('cell', name=SHARED_WORD, exact=True)).to_have_count(0)

        _open_view(alpha, base, 'einstellungen')
        _select_pool(alpha, b_id)
        created = _upload_dictation(alpha, base, 3, expected_status=202)
        _assert_dictation_pool(db_path, created['dictation_id'], b_id)

        if os.environ.get('P157_ARTIFACTS'):
            catalog = _memberships(admin, base)
            a = next(p for p in catalog['author_pools'] if p['id'] == a_id)
            _set_members(admin, base, catalog, a, [])

            def no_pools(page: Page) -> None:
                select = page.get_by_test_id('active-pool-select')
                expect(select).to_be_disabled()
                expect(select.get_by_role('option')).to_have_count(1)
                expect(page.get_by_test_id('active-pool-status')).to_contain_text('keine gemeinsamen Wörter')

            _capture(browser, base, beta_state, 'einstellungen', 'empty', no_pools)
            _capture(browser, base, beta_state, 'ressourcen', 'empty', lambda p: _assert_empty_words(p, revoked_pool=POOL_A))
            catalog = _memberships(admin, base)
            b = next(p for p in catalog['author_pools'] if p['id'] == b_id)
            _set_members(admin, base, catalog, b, [alpha_id])
            _capture(browser, base, admin_state, 'admin-wortschatz', 'empty',
                     lambda p: _assert_empty_words(p, revoked_pool=POOL_B))
