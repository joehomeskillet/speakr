"""P243: clipboard confirmation and workspace-only effects through the clinic shell."""

from io import BytesIO
import os
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
import wave

from playwright.sync_api import Dialog, Page, Response, Route, expect, sync_playwright
import pytest


ART = Path(__file__).resolve().parents[2] / '.wp' / 'artifacts'
DICTATION = '24300000-0000-4000-8000-000000000001'
TEXT = 'Verlaufskontrolle: Der Schlaf hat sich gebessert. Das nächste Gespräch ist am Donnerstag.'
CLIPBOARD_ONLY = 'Der Text wurde nur in die Zwischenablage kopiert. Bitte manuell einfügen.'

SEED_IMPORTS = '''
from datetime import datetime, timezone
from hashlib import sha256
from src.clinic import ingest, live_phrases, live_session, release, revisions
from src.clinic.models import ClinicContextRevision
from src.clinic.richtext import CanonicalDocument, ClinicRichTextSnapshot, Node, Segment
from src.clinic.startup import originals_root

'''
SEED_ORIGINAL = '''    store = ingest.IngestService(originals_root(app))
    wav = bytes.fromhex(__WAV__)
    key = ingest.storage_key_for('p243-synthetic-original')
    path = store.file_path(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(wav)
    original = ClinicOriginal(dictation_id=dic.id, storage_key=key, content_hash=sha256(wav).hexdigest(),
                              size_bytes=len(wav), receipt='stored', origin_ref='device:synthetic')
'''
SEED_RELEASE = '''    now = datetime.now(timezone.utc)
    session = live_session.start(dic.id, author.id, now, client_id='p243-fixture')
    session.holder_generation = generation = 1
    audio = init_audio_revision(dic, original, 500)
    text = revisions.current_revision(dic)
    live_phrases.store_phrase(
        store, dictation_id=dic.id, session_id=session.id, user_id=author.id,
        client_id='p243-fixture', now=now, generation=generation, sequence=1,
        event_id='p243-sentence', audio_start_ms=0, audio_end_ms=500, audio=wav,
        audio_revision_id=audio.id, transcript_revision_id=text.id, text_hash=text.normalised_text_hash,
    )
    live_session.end(session.id, author.id, now, client_id='p243-fixture', generation=generation)
    context = ClinicContextRevision(dictation_id=dic.id, source_system='synthetic',
                                    tenant_scope='test', case_ref='TEST-243', patient_ref='SYNTHETIC')
    document = CanonicalDocument((Node('p243-paragraph', 'paragraph', (
        Segment(id='p243-text', text=text.body, bold=True),)),))
    db.session.add_all([context, ClinicRichTextSnapshot(revision_id=text.id, version=1,
                                                       payload=document.to_payload())])
    db.session.flush()
    release.create_release(dic, audio, text, context, text.normalised_text_hash, author.id,
                           {'allowed': True}, now, document_type='report', release_type='clinical')
    pools.allow(author.id, 'own', actor_id=admin.id, actions=('export', 'audio_export'))
    db.session.commit()
'''


def _wav() -> bytes:
    buffer = BytesIO()
    with wave.open(buffer, 'wb') as audio:
        audio.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        audio.writeframes(bytes(16000))
    return buffer.getvalue()


@pytest.fixture
def live_shell(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parent / 'browser'))
    monkeypatch.setenv('SPEAKR_SKIP_BACKGROUND_TASKS', 'true')
    monkeypatch.setenv('TRANSCRIPTION_BASE_URL', 'http://127.0.0.1:9/v1')
    monkeypatch.setenv('CLINIC_ORIGINALS_ROOT', str(tmp_path / 'originals'))
    import v8_browser_support as support

    wav = _wav()
    source = support.SERVER.replace('with app.app_context():', SEED_IMPORTS + 'with app.app_context():', 1)
    source = source.replace('dic = ClinicDictation(', f'dic = ClinicDictation(id="{DICTATION}", ', 1)
    source = source.replace('title="Synthetisch"', 'title="Verlaufskontrolle – Testfall 243"')
    source = source.replace('"Anamnese Husten seit drei Tagen."', repr(TEXT))
    start, end = source.index('    original = ClinicOriginal('), source.index('    db.session.add(original)')
    source = source[:start] + SEED_ORIGINAL.replace('__WAV__', repr(wav.hex())) + source[end:]
    source = source.replace('    init_audio_revision(dic, original, 4000)\n', SEED_RELEASE)
    with support.serve_app(tmp_path, source) as base, sync_playwright() as playwright:
        with playwright.chromium.launch(
            executable_path=os.environ.get('PLAYWRIGHT_CHROMIUM_EXECUTABLE', '/usr/bin/chromium'),
        ) as browser:
            author = support.login(browser, base, support.ROLES['author'])
            page = browser.new_page(storage_state=author, permissions=['clipboard-read', 'clipboard-write'],
                                    viewport={'width': 1440, 'height': 900}, locale='de-CH', reduced_motion='reduce')
            page.goto(f'{base}/?dictation={DICTATION}#/live')
            yield page, f'{base}/api/clinic/v1/dictations/{DICTATION}', wav, support


def _capture(page: Page, support, label: str) -> None:
    ART.mkdir(parents=True, exist_ok=True)
    for size, (width, height) in support.VIEWPORTS.items():
        page.set_viewport_size({'width': width, 'height': height})
        for theme in support.THEMES:
            page.emulate_media(color_scheme=theme)
            page.evaluate("dark => document.documentElement.classList.toggle('dark', dark)", theme == 'dark')
            overflow = support.horizontal_overflow(page)
            assert not (overflow['document'] or overflow['body']), (label, size, theme, overflow)
            controls = ('[data-testid="live-transfer"] :is(button, select, a):visible, '
                        '[data-testid="clinic-live-picker"] :is(input, button):visible')
            for control in page.locator(controls).all():
                bounds = control.bounding_box()
                assert bounds and min(bounds['width'], bounds['height']) >= 48, (label, size, theme, bounds)
                assert 0 <= bounds['x'] and bounds['x'] + bounds['width'] <= width, (label, size, theme, bounds)
            page.evaluate("""() => {
                window.scrollTo(0, 0);
                for (let node = document.querySelector('[data-testid="clinic-live-view"]'); node; node = node.parentElement) {
                    node.scrollTop = 0;
                }
            }""")
            stem = ART / f'p243-live-transfer-{label}-{size}-{theme}'
            page.screenshot(path=stem.with_suffix('.png'), full_page=True, animations='disabled', caret='hide')
            dom = page.evaluate('''() => {
                const root = document.documentElement.cloneNode(true);
                root.querySelectorAll('script, meta[name="csrf-token"]').forEach(el => el.remove());
                root.querySelectorAll('select').forEach((select, i) => {
                    const index = document.querySelectorAll('select')[i].selectedIndex;
                    [...select.options].forEach((option, j) => option.toggleAttribute('selected', j === index));
                });
                return '<!doctype html>\\n' + root.outerHTML;
            }''')
            dom = dom.replace('href="/static/', 'href="../../static/').replace('src="/static/', 'src="../../static/')
            stem.with_suffix('.html').write_text(dom, encoding='utf-8')
            detail = {
                'rows': '[data-testid="live-phrases"] li',
                'empty': 'button:has-text("Diktat wieder anzeigen")',
            }.get(label)
            if detail:
                target = page.locator(detail).first
                target.evaluate("(node) => node.scrollIntoView({block: 'end'})")
                expect(target).to_be_in_viewport()
                page.screenshot(path=ART / f'{stem.name}-detail.png', animations='disabled', caret='hide')
    page.set_viewport_size({'width': 1440, 'height': 900})
    page.emulate_media(color_scheme='light')
    page.evaluate("document.documentElement.classList.remove('dark')")


def _copy_with_receipt(page: Page, choice: str, clipboard_format: str) -> None:
    page.get_by_label('Nach dem Kopieren').select_option(choice)
    page.get_by_label('Kopierformat').select_option(clipboard_format)
    with page.expect_event('dialog') as confirmed, page.expect_response(_prepared) as prepare, \
            page.expect_response(_reported) as report:
        page.once('dialog', _accept_confirm)
        page.get_by_role('button', name='Text kopieren', exact=True).click()
    assert confirmed.value.type == 'confirm'
    start, done = prepare.value, report.value
    assert start.ok and done.ok
    assert parse_qs(urlsplit(start.url).query)['format'] == [clipboard_format]
    assert parse_qs(urlsplit(start.url).query)['live'] == ['1']
    assert parse_qs(urlsplit(done.url).query)['live'] == ['1']
    generation = start.json()['generation']
    assert start.json()['status'] == 'UNKNOWN'
    assert type(generation) is int and generation > 0
    assert done.request.post_data_json == {'outcome': 'copied', 'generation': generation}
    assert done.json() == {'status': 'succeeded', 'generation': generation}
    assert page.evaluate('navigator.clipboard.readText()') == TEXT
    if clipboard_format == 'formatted':
        html = page.evaluate('''async () => {
            const [item] = await navigator.clipboard.read();
            return (await item.getType('text/html')).text();
        }''')
        assert '<strong>' in html and TEXT in html
    else:
        assert 'text/html' not in start.json()


def _accept_confirm(dialog: Dialog) -> None:
    assert dialog.type == 'confirm'
    dialog.accept()


def _prepared(response: Response) -> bool:
    return '/clipboard/' in response.url and response.request.method == 'GET'


def _reported(response: Response) -> bool:
    return urlsplit(response.url).path.endswith('/outcome') and '/clipboard/' in response.url


def test_live_copy_clear_and_close_preserve_saved_originals(live_shell) -> None:
    page, endpoint, wav, support = live_shell
    view = page.get_by_test_id('clinic-live-view')
    workspace = view.locator('.editor-workspace')
    row = workspace.get_by_test_id('live-phrases').get_by_text(TEXT, exact=True)
    show = view.get_by_role('button', name='Freigegebene Fassung ausgeben', exact=True)
    copy = view.get_by_role('button', name='Text kopieren', exact=True)
    after = view.get_by_label('Nach dem Kopieren')
    format_select = view.get_by_label('Kopierformat')
    picker = page.get_by_test_id('clinic-live-picker')
    requests = []
    page.on('request', lambda request: requests.append(request.url) if '/clipboard/' in request.url else None)
    expect(row).to_be_visible(timeout=support.SHOW_TIMEOUT_MS)
    baseline = page.request.get(f'{endpoint}/audio/original')
    assert baseline.ok and baseline.body() == wav
    show.click()
    expect(copy).to_be_enabled()
    expect(format_select).to_have_value('plain')
    expect(format_select.locator('option')).to_have_text(['Nur Text', 'Mit Formatierung'])
    expect(after).to_have_value('none')
    expect(after.locator('option')).to_have_text(['Diktat offen lassen', 'Ansicht leeren', 'Diktat schliessen'])
    _capture(page, support, 'rows')

    page.evaluate("navigator.clipboard.writeText('Unveränderte Zwischenablage')")
    after.select_option('clear')
    expect(copy).to_be_enabled()
    with page.expect_event('dialog') as declined:
        page.once('dialog', lambda dialog: dialog.dismiss())
        copy.click()
    assert declined.value.type == 'confirm'
    expect(row).to_be_visible()
    assert page.evaluate('navigator.clipboard.readText()') == 'Unveränderte Zwischenablage'
    assert not requests

    page.evaluate("""() => {
        const original = navigator.clipboard.writeText.bind(navigator.clipboard);
        navigator.clipboard.writeText = () => {
            navigator.clipboard.writeText = original;
            return Promise.reject(new DOMException('Test denial', 'NotAllowedError'));
        };
    }""")
    with page.expect_response(_reported) as denied:
        page.once('dialog', _accept_confirm)
        copy.click()
    assert denied.value.json()['status'] == 'failed'
    expect(view.get_by_text('Der Browser hat den Zugriff auf die Zwischenablage verweigert.')).to_be_visible()
    expect(row).to_be_visible()
    expect(copy).to_be_enabled()
    assert page.evaluate('navigator.clipboard.readText()') == 'Unveränderte Zwischenablage'

    _copy_with_receipt(page, 'clear', 'plain')
    expect(workspace).to_be_hidden()
    expect(view.get_by_text('Die Ansicht ist leer. Das gespeicherte Diktat bleibt erhalten.', exact=True)).to_be_visible()
    restore = view.get_by_role('button', name='Diktat wieder anzeigen', exact=True)
    expect(restore).to_be_visible()
    expect(restore).to_be_focused()
    _capture(page, support, 'empty')
    restore.click()
    expect(row).to_be_visible()
    original = page.request.get(f'{endpoint}/audio/original')
    assert original.ok and original.body() == wav

    page.reload()
    expect(row).to_be_visible(timeout=support.SHOW_TIMEOUT_MS)
    show.click()
    expect(copy).to_be_enabled()
    expect(after).to_have_value('none')
    expect(format_select).to_have_value('plain')
    _copy_with_receipt(page, 'close', 'formatted')
    expect(picker).to_be_visible()
    expect(view).to_have_count(0)
    expect(picker.get_by_role('status').filter(has_text=CLIPBOARD_ONLY)).to_be_visible()
    expect(picker.get_by_role('heading', name='Live-Diktat')).to_be_focused()
    assert 'dictation' not in parse_qs(urlsplit(page.url).query)
    _capture(page, support, 'picker')

    page.get_by_test_id('clinic-live-id').fill(DICTATION)
    page.get_by_test_id('clinic-live-open').click()
    expect(row).to_be_visible(timeout=support.SHOW_TIMEOUT_MS)
    original = page.request.get(f'{endpoint}/audio/original')
    assert original.ok and original.body() == wav


@pytest.mark.parametrize('receipt_fault', ['unknown', 'wrong_generation', 'local_edit'])
def test_unconfirmed_live_copy_does_not_clear_or_close(live_shell, receipt_fault: str) -> None:
    page, endpoint, _, support = live_shell
    view = page.get_by_test_id('clinic-live-view')
    row = view.get_by_test_id('live-phrases').get_by_text(TEXT, exact=True)
    expect(row).to_be_visible(timeout=support.SHOW_TIMEOUT_MS)
    view.get_by_role('button', name='Freigegebene Fassung ausgeben', exact=True).click()
    copy = view.get_by_role('button', name='Text kopieren', exact=True)
    expect(copy).to_be_enabled()
    view.get_by_label('Nach dem Kopieren').select_option('close' if receipt_fault == 'unknown' else 'clear')

    def withhold_confirmation(route: Route) -> None:
        # The browser may have written the clipboard, but must wait for a verified server receipt.
        expect(row).to_be_visible()
        payload = route.request.post_data_json
        assert isinstance(payload, dict) and payload['outcome'] == 'copied'
        generation = payload['generation']
        if receipt_fault == 'unknown':
            result = route.fetch(post_data={'outcome': 'aborted', 'generation': generation})
            assert result.ok
            assert result.json() == {'status': 'UNKNOWN', 'generation': generation}
            route.fulfill(response=result)
        else:
            result = route.fetch()
            assert result.ok
            assert result.json() == {'status': 'succeeded', 'generation': generation}
            route.fulfill(response=result, json={'status': 'succeeded', 'generation': generation + 1})

    page.route(f'{endpoint}/clipboard/*/outcome?*', withhold_confirmation, times=1)
    with page.expect_event('dialog') as confirmed, page.expect_response(_reported) as initial:
        page.once('dialog', _accept_confirm)
        copy.click()
    assert confirmed.value.type == 'confirm'
    expect(view.get_by_role('button', name='Ergebnis erneut melden', exact=True)).to_be_visible()
    expect(row).to_be_visible()
    expect(page.get_by_test_id('clinic-live-picker')).to_have_count(0)
    expect(view.get_by_role('button', name='Diktat wieder anzeigen', exact=True)).to_have_count(0)
    expect(view.get_by_label('Kopierformat')).to_be_disabled()
    expect(view.get_by_label('Nach dem Kopieren')).to_be_disabled()
    if receipt_fault == 'wrong_generation':
        view.get_by_role('button', name='Ausgabemöglichkeiten ausblenden', exact=True).click()
        view.get_by_role('button', name='Freigegebene Fassung ausgeben', exact=True).click()
    if receipt_fault == 'local_edit':
        view.get_by_role('button', name='Bearbeiten', exact=True).click()
        draft = view.locator('textarea').first
        expect(draft).to_be_editable()
        draft.fill('Neuer ungespeicherter Entwurf')
    page.evaluate("navigator.clipboard.writeText('Zwischenablage nach dem ersten Kopieren')")
    with page.expect_response(_reported) as retry:
        view.get_by_role('button', name='Ergebnis erneut melden', exact=True).click()
    assert retry.value.request.header_value('idempotency-key') == initial.value.request.header_value('idempotency-key')
    assert retry.value.request.post_data_json == initial.value.request.post_data_json
    assert retry.value.json()['status'] == 'succeeded'
    assert page.evaluate('navigator.clipboard.readText()') == 'Zwischenablage nach dem ersten Kopieren'
    if receipt_fault == 'unknown':
        expect(page.get_by_test_id('clinic-live-picker')).to_be_visible()
    else:
        expect(row).to_be_visible()
        expect(view.get_by_text('In die Zwischenablage kopiert.', exact=True)).to_be_visible()
        expect(view.get_by_role('button', name='Diktat wieder anzeigen', exact=True)).to_have_count(0)
    if receipt_fault == 'local_edit':
        expect(draft).to_have_value('Neuer ungespeicherter Entwurf')
