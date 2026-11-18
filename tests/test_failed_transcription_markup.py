"""A FAILED recording without a transcript shows the failure, not "no transcript yet".

Marker test for the detail view (desktop and phone) and the locale keys; the
job outcome is covered in test_cov_job_queue.py.
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DETAIL_DIR = os.path.join(ROOT, 'templates', 'components', 'detail')


def _read(*parts):
    with open(os.path.join(*parts), encoding='utf-8') as handle:
        return handle.read()


def test_the_failed_partial_names_the_error_and_offers_the_existing_retry():
    partial = _read(DETAIL_DIR, '_transcription-failed.html')
    assert 'data-transcription-failed' in partial
    assert "t('transcription.failedTitle')" in partial
    assert "t('transcription.failedMessage')" in partial
    assert 'reprocessTranscription(selectedRecording.id)' in partial
    assert 'selectedRecording.error_message' in partial


def test_desktop_and_phone_show_it_instead_of_the_empty_state():
    desktop = _read(DETAIL_DIR, 'desktop-transcription-panel.html')
    phone = _read(DETAIL_DIR, 'mobile-transcript-panel.html')
    for panel in (desktop, phone):
        assert "selectedRecording.status === 'FAILED' && !selectedRecording.transcription" in panel
        assert "components/detail/_transcription-failed.html" in panel
    assert "v-else-if=\"selectedRecording.status !== 'PROCESSING' && !selectedRecording.transcription\"" in desktop
    assert "selectedRecording.status !== \\'FAILED\\'" in phone


def test_the_texts_are_locale_keys_in_german_and_english():
    for code in ('de', 'en'):
        block = json.loads(_read(ROOT, 'static', 'locales', f'{code}.json'))['transcription']
        assert block['failedTitle'] and block['failedMessage'], code
    german = json.loads(_read(ROOT, 'static', 'locales', 'de.json'))['transcription']
    assert german['failedTitle'] == 'Transkription fehlgeschlagen'
