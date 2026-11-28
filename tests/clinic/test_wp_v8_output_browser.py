"""P16 through the real app: native clipboard flavors and released file downloads."""

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

import pytest
from flask import Flask
from playwright.sync_api import Locator, Page, expect, sync_playwright

import test_wp_mount_correction as mount
from src.clinic.models import ClinicAudioRevision, ClinicContextRevision, ClinicDictation, ClinicRelease
from src.clinic.output import ClinicOutputReceipt
from src.clinic.release import create_release
from src.clinic.revisions import current_revision
from src.database import db
from test_wp_mount_correction import live_server as live_server
from test_wp_v8_130_release_browser import _capture, _open_output

AUTHOR = 'author213@local.test'
BOLD = 'Befund'
BODY = f'Korrigierter {BOLD}.'
FORMATS = ('txt', 'docx', 'rtf', 'odt')
SUCCESS = 'In die Zwischenablage kopiert.'


@pytest.fixture
def output_scene(request, monkeypatch):
    grant_at = '    rec = Recording('
    seed_at = '    dictation_id = dic.id'
    assert grant_at in mount.SERVER and seed_at in mount.SERVER
    server = mount.SERVER.replace(
        grant_at, "    pools.allow(author.id, 'own', actor_id=admin.id, actions=('export',))\n" + grant_at,
    ).replace(seed_at, f'''
    from src.clinic.models import ClinicContextRevision
    from src.clinic.revisions import add_human_revision, current_revision
    from src.clinic.richtext import CanonicalDocument, ClinicRichTextSnapshot, Node, Segment

    text = add_human_revision(dic, current_revision(dic), {BODY!r}, typist.id)
    doc = CanonicalDocument((Node('p1', 'paragraph', (
        Segment(id='s1', text='Korrigierter '), Segment(id='s2', text={BOLD!r}, bold=True),
        Segment(id='s3', text='.'),
    )),))
    db.session.add_all([
        ClinicRichTextSnapshot(revision_id=text.id, version=1, payload=doc.to_payload()),
        ClinicContextRevision(dictation_id=dic.id, source_system='synthetic', tenant_scope='test',
                              case_ref='SYN-P205', patient_ref='opaque'),
    ])
    db.session.commit()
{seed_at}''')
    monkeypatch.setattr(mount, 'SERVER', server)
    original_env = mount._env
    inspector = Flask(__name__)

    def local_profile(path, clinic):
        env = original_env(path, clinic)
        inspector.config['SQLALCHEMY_DATABASE_URI'] = env['SQLALCHEMY_DATABASE_URI']
        return {**env, 'CLINIC_PROFILE': 'staging', 'SPEAKR_SKIP_BACKGROUND_TASKS': 'true',
                'CLINIC_KIS_DELIVERY_ENABLED': 'false', 'TRANSCRIPTION_BASE_URL': 'http://127.0.0.1:9/v1'}

    monkeypatch.setattr(mount, '_env', local_profile)
    base, did = request.getfixturevalue('live_server')
    db.init_app(inspector)
    yield base, did, inspector
    with inspector.app_context():
        db.engine.dispose()


def _capture_output(page: Page, output: Locator, state: str) -> None:
    if dest := os.environ.get('P205_ARTIFACTS_DIR'):
        path = Path(dest)
        path.mkdir(parents=True, exist_ok=True)
        viewport = page.viewport_size
        heading = 'Ausgabeziele' if state == 'rows' else 'Ausgabe'
        _capture(page, output.get_by_role('heading', name=heading, exact=True), path, f'p16-{state}')
        (path / f'p16-{state}.html').write_text(page.content(), encoding='utf-8')
        if viewport is not None:
            page.set_viewport_size(viewport)


def _copy(page: Page, buttons: Locator, label: str) -> dict:
    with page.expect_response(lambda r: r.url.endswith('/outcome') and '/clipboard/' in r.url) as reported:
        buttons.get_by_role('button', name=label, exact=True).click()
    assert reported.value.ok, reported.value.text()
    expect(buttons.get_by_role('status')).to_have_text(SUCCESS)
    return page.evaluate('''async () => {
        const items = await navigator.clipboard.read();
        if (items.length !== 1) throw new Error(`Expected one clipboard item, got ${items.length}`);
        const [item] = items;
        return Object.fromEntries(await Promise.all(item.types.map(async type =>
            [type, await (await item.getType(type)).text()])));
    }''')


def _confirm_receipts_and_release(inspector: Flask, did: str) -> tuple[str, str]:
    with inspector.app_context():
        dic = db.session.get(ClinicDictation, did)
        assert dic is not None
        text = current_revision(dic)
        assert text is not None
        receipts = db.session.query(ClinicOutputReceipt).filter_by(dictation_id=did).all()
        observed = [(r.text_format, r.transcript_revision_id, r.text_hash, r.actor_user_id, r.target)
                    for r in receipts]
        assert sorted(observed) == [
            (fmt, text.id, text.normalised_text_hash, dic.author_user_id, 'browser')
            for fmt in ('FORMATTED', 'PLAIN')
        ]
        assert {r.channel for r in receipts} == {'CLIPBOARD'}
        assert db.session.query(ClinicRelease).filter_by(dictation_id=did).count() == 0

        audio = db.session.query(ClinicAudioRevision).filter_by(dictation_id=did).one()
        context = db.session.query(ClinicContextRevision).filter_by(dictation_id=did).one()
        release = create_release(dic, audio, text, context, text.normalised_text_hash, dic.author_user_id,
                                 {'allowed': True}, datetime.now(timezone.utc),
                                 document_type='report', release_type='clinical')
        db.session.commit()
        return text.id, release.id


def _download_text(path: Path, fmt: str) -> str:
    if fmt in ('txt', 'rtf'):
        return path.read_text(encoding='utf-8')
    with ZipFile(path) as archive:
        member = 'word/document.xml' if fmt == 'docx' else 'content.xml'
        return ''.join(ElementTree.fromstring(archive.read(member)).itertext())


def _check_downloads(page: Page, output: Locator, revision_id: str, release_id: str) -> None:
    expect(output.locator('[data-target]')).to_have_count(len(FORMATS))
    for i, fmt in enumerate(FORMATS):
        row = output.locator(f'[data-target={fmt}]')
        with page.expect_response(lambda r: r.url.endswith('/exports') and r.request.method == 'POST') as exported:
            row.get_by_role('button', name='Ausgabe starten').click()
        assert exported.value.status == 201, exported.value.text()
        job = exported.value.json()
        assert (job['format'], job['status'], job['revision_id'], job['release_id']) == (
            fmt, 'ready', revision_id, release_id,
        )
        expect(row.get_by_role('status')).to_have_text('Datei erzeugt')
        with page.expect_download() as downloaded:
            row.get_by_role('link', name='Herunterladen').click()
        download = downloaded.value
        assert download.suggested_filename.endswith(f'.{fmt}')
        assert BODY in _download_text(download.path(), fmt)
        for prior in FORMATS[:i + 1]:
            expect(output.locator(f'[data-target={prior}]').get_by_role('status')).to_have_text('Download gestartet')
        for untouched in FORMATS[i + 1:]:
            untouched_row = output.locator(f'[data-target={untouched}]')
            expect(untouched_row.get_by_role('button', name='Ausgabe starten')).to_be_enabled()
            expect(untouched_row.get_by_role('status')).to_have_count(0)


def test_p16_working_copy_and_released_downloads(output_scene):
    base, did, inspector = output_scene
    with sync_playwright() as playwright:
        browser = mount._launch(playwright)
        try:
            typist = mount._login(browser, base, mount.TYPIST)
            empty = _open_output(typist, did)
            expect(typist).to_have_url(re.compile(r'#/diktat/ausgabe$'))
            expect(empty.get_by_test_id('not-released')).to_be_visible()
            expect(empty.get_by_role('button', name=re.compile('kopieren'))).to_have_count(0)
            expect(empty.get_by_role('link', name='Herunterladen')).to_have_count(0)
            expect(empty.get_by_role('button', name='Ausgabe starten', disabled=False)).to_have_count(0)
            _capture_output(typist, empty, 'empty')
            assert not typist.js_errors, typist.js_errors
            typist.context.close()

            page = mount._login(browser, base, AUTHOR)
            page.context.grant_permissions(['clipboard-read', 'clipboard-write'], origin=base)
            output = _open_output(page, did)
            expect(page).to_have_url(re.compile(r'#/diktat/ausgabe$'))
            expect(output.get_by_text('Arbeitskopie', exact=True)).to_be_visible()
            expect(output.get_by_test_id('not-released')).to_be_visible()
            buttons = output.get_by_role('group', name='Zwischenablage')
            assert _copy(page, buttons, 'Als Text kopieren') == {'text/plain': BODY}
            formatted = _copy(page, buttons, 'Mit Formatierung kopieren')
            assert set(formatted) == {'text/plain', 'text/html'}
            assert formatted['text/plain'] == BODY
            assert page.evaluate('''html => {
                const doc = new DOMParser().parseFromString(html, 'text/html');
                return [...doc.querySelectorAll('strong, b')].map(run => run.textContent);
            }''', formatted['text/html']) == [BOLD]
            _capture_output(page, output, 'working')

            revision_id, release_id = _confirm_receipts_and_release(inspector, did)
            page.get_by_test_id('clinic-dictation-back').click()
            output = _open_output(page, did)
            expect(page).to_have_url(re.compile(r'#/diktat/ausgabe$'))
            expect(output.get_by_test_id('revision')).to_contain_text(revision_id)
            _check_downloads(page, output, revision_id, release_id)
            _capture_output(page, output, 'rows')
            assert not page.js_errors, page.js_errors
        finally:
            browser.close()
