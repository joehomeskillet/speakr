"""The expired-session hint: wiring and texts.

static/js/session-hint.js shows one reload banner; csrf-refresh.js's fetch
interceptor feeds it every response. These tests read the files; the
detection logic itself is covered by static/js/session-hint.test.js.
"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCALES = os.path.join(ROOT, 'static', 'locales')
KEYS = ('sessionHintMessage', 'sessionHintReload')


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as handle:
        return handle.read()


def test_interceptor_feeds_the_hint():
    assert 'window.sessionHint.inspect(url, response)' in _read('static', 'js', 'csrf-refresh.js')


def test_hint_script_loaded_before_interceptor_on_both_pages():
    for page in ('index.html', 'inquire.html'):
        html = _read('templates', page)
        hint = html.index('js/session-hint.js')
        assert hint < html.index('js/csrf-refresh.js'), page


def test_hint_never_reloads_by_itself():
    source = _read('static', 'js', 'session-hint.js')
    # The only reload call sits inside the button's click handler.
    assert source.count('location.reload()') == 1
    assert "addEventListener('click', function () { window.location.reload(); })" in source


def test_hint_texts_exist_in_every_locale():
    files = [name for name in os.listdir(LOCALES) if name.endswith('.json')]
    assert files
    for name in files:
        with open(os.path.join(LOCALES, name), encoding='utf-8') as handle:
            errors = json.load(handle)['errors']
        for key in KEYS:
            assert errors.get(key), f'{name} lacks errors.{key}'
