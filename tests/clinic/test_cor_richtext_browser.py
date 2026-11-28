"""BT-COR-002 target evidence through the real worklist, lease, saver and player."""
import os
from pathlib import Path

import pytest
from playwright.sync_api import expect, sync_playwright

import test_wp_mount_correction as mount
from test_wp_rest27_repeat_ui import _repeat_server


LINES = ('Anamnese Hutsen.', 'Die Stimme bleibt ruhig.', 'Der dritte Satz bleibt.')
TIMES = ((0.2, 0.9), (1.3, 2.1), (2.7, 3.6))
MANUAL = ' Ein neuer Hinweis.'


@pytest.fixture
def rich_server(tmp_path_factory):
    yield from _correction_server(tmp_path_factory, empty=False)


def _correction_server(tmp_path_factory, *, empty: bool):
    segments = [dict(text=text, start=start, end=end) for text, (start, end) in zip(LINES, TIMES)]
    seed = f'''    from src.clinic.correction import link_asr_segments
    asr = add_asr_revision(dic, original, {chr(10).join(LINES)!r}, 'model:synthetic')
    link_asr_segments(dic, asr, {segments!r})'''
    if empty:
        seed = '    add_asr_revision(dic, original, "", "model:synthetic")'
    with pytest.MonkeyPatch.context() as patch:
        server = mount.SERVER.replace(
            '    add_asr_revision(dic, original, "Anamnese Husten seit drei Tagen.", "model:synthetic")', seed,
        ).replace('workflow.TO_WRITE', 'workflow.TO_CORRECT')
        patch.setattr(mount, 'SERVER', server)
        yield from _repeat_server(tmp_path_factory)


def _capture(page, state: str) -> None:
    folder = os.environ.get('COR002_ARTIFACTS')
    if not folder:
        return
    out = Path(folder)
    out.mkdir(parents=True, exist_ok=True)
    page.mouse.move(0, 0)
    for theme in ('light', 'dark'):
        page.evaluate("dark => document.documentElement.classList.toggle('dark', dark)", theme == 'dark')
        for width, height in ((1440, 900), (1280, 720), (390, 844)):
            page.set_viewport_size({'width': width, 'height': height})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            page.get_by_test_id('toolbar').evaluate("el => el.scrollIntoView({block: 'center'})")
            path = out / f'correction-{state}-{theme}-{width}x{height}'
            page.screenshot(path=path.with_suffix('.png'), animations='disabled', full_page=True)
            path.with_suffix('.html').write_text(page.content(), encoding='utf-8')
    page.set_viewport_size({'width': 1400, 'height': 900})
    page.evaluate("document.documentElement.classList.remove('dark')")


def _play_passage(page, text: str, bounds: tuple[float, float], *, keyboard: bool = False) -> None:
    passage = page.get_by_test_id('alignment').get_by_role('button', name=f'Passage abspielen: {text}', exact=True)
    if keyboard:
        passage.press('Enter')
    else:
        passage.click()
    page.wait_for_function('''([start, end]) => {
        const audio = document.querySelector('.clinic-correction-view audio');
        return audio && !audio.paused && audio.currentTime > start && audio.currentTime < end;
    }''', arg=bounds, timeout=3000)
    page.locator('.clinic-correction-view audio').evaluate('audio => audio.pause()')


def test_typist_edits_moves_and_reopens_annotated_document(rich_server):
    base, dictation, _ = rich_server
    with sync_playwright() as playwright, mount._launch(playwright) as browser:
        page = mount._login(browser, base, mount.TYPIST)
        mount._open_from_worklist(page, base)
        editor = page.get_by_test_id('correction-document')
        paragraphs = editor.locator('[data-node-id]')
        runs = editor.locator('textarea[data-run-id]')
        expect(runs).to_have_count(3)
        correction_url = f'{base}/api/clinic/v1/dictations/{dictation}/correction'
        initial = page.request.get(correction_url).json()
        assert initial.get('document_version', 0) == 0
        assert [(link['audio_start_ms'], link['audio_end_ms']) for link in initial['links']] == [
            (round(start * 1000), round(end * 1000)) for start, end in TIMES
        ]
        if not runs.first.is_editable():
            page.locator('[data-action=acquire]').click()
        expect(runs.first).to_be_editable()
        page.wait_for_function("document.querySelector('.clinic-correction-view audio')?.readyState >= 2")
        _play_passage(page, LINES[0], TIMES[0])

        corrected = 'Anamnese Husten.'
        runs.first.fill(corrected)
        runs.nth(1).focus()
        page.locator('[data-tool=bold]').click()
        expect(runs.nth(1)).to_have_css('font-weight', '700')
        page.locator('[data-tool=move-up]').click()
        expect(paragraphs.first.locator('textarea')).to_have_value(LINES[1])
        page.locator('[data-tool=insert-run]').click()
        paragraphs.first.locator('textarea').nth(1).fill(MANUAL)
        _play_passage(page, LINES[1], TIMES[1])

        status = page.get_by_test_id('save-state')
        page.get_by_test_id('save').click()
        expect(status).to_have_attribute('data-state', 'saved', timeout=30000)
        stored = page.request.get(correction_url).json()
        assert stored['text'] == f'{LINES[1]}{MANUAL}\n{corrected}\n{LINES[2]}'
        spoken, typed = stored['document']['nodes'][0]['runs']
        assert spoken['bold'] is True
        assert typed['text'] == MANUAL and typed['audio_anchor_id'] is None
        assert sorted((link['audio_start_ms'], link['audio_end_ms']) for link in stored['links']) == [
            (1300, 2100), (2700, 3600),
        ]

        page.get_by_test_id('clinic-correction-back').click()
        mount._open_from_worklist(page, base)
        expect(paragraphs.first.locator('textarea').first).to_have_value(LINES[1])
        expect(paragraphs.first.locator('textarea').first).to_have_css('font-weight', '700')
        expect(paragraphs.first.locator('textarea').nth(1)).to_have_value(MANUAL)
        expect(runs.nth(2)).to_have_value(corrected)
        expect(page.get_by_test_id('alignment').locator('[data-link]')).to_have_count(2)
        expect(page.get_by_test_id('alignment').get_by_text(MANUAL.strip(), exact=True)).to_have_attribute('data-unaligned', 'true')
        page.wait_for_function("document.querySelector('.clinic-correction-view audio')?.readyState >= 2")
        _play_passage(page, LINES[1], TIMES[1], keyboard=True)
        _play_passage(page, LINES[2], TIMES[2])
        paragraphs.first.locator('textarea').first.focus()
        _capture(page, 'rows')
        assert not page.js_errors, page.js_errors


@pytest.fixture
def empty_server(tmp_path_factory):
    yield from _correction_server(tmp_path_factory, empty=True)


def test_empty_correction_is_ready_for_typing(empty_server):
    base, _, _ = empty_server
    with sync_playwright() as playwright, mount._launch(playwright) as browser:
        page = mount._login(browser, base, mount.TYPIST)
        mount._open_from_worklist(page, base)
        field = page.get_by_test_id('correction-document').get_by_role('textbox')
        expect(field).to_be_editable()
        expect(field).to_have_value('')
        expect(field).to_have_attribute('placeholder', 'Hier mit dem Schreiben beginnen')
        expect(page.get_by_test_id('alignment').locator('[data-link]')).to_have_count(0)
        _capture(page, 'empty')
        assert not page.js_errors, page.js_errors
