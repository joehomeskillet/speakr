"""GET and save return one canonical correction document (PAR-COR-002, PAR-LIVE-005).

Plain-text clients omit ``document``. Two saves of the same opened snapshot cannot
overwrite each other: a stale ``expected_document_version`` is a 409 and writes nothing,
so the rejected tab still has its own text.
"""

import uuid
from datetime import datetime, timezone

import pytest
from flask import Flask, jsonify
from flask_login import LoginManager, login_user
from flask_wtf.csrf import CSRFProtect, generate_csrf

from src.app import app as main_app, db
from src.clinic import routes_correction
from src.clinic.audio_revisions import init_audio_revision
from src.clinic.correction import get_state, link_asr_segments
from src.clinic.lease import acquire
from src.clinic.models import ClinicDictation, ClinicTranscriptRevision
from src.clinic.parity_dispatch import CommandProblem
from src.clinic.richtext import ClinicRichTextSnapshot, load_document
from src.models import User
from test_correction import SEGMENTS, _scene

BASE = '/api/clinic/v1/dictations/{}/correction'


def _nodes(paragraphs, *, bold=False, italic=False):
    return {'schema_version': 1, 'nodes': [{
        'id': f'p{index}', 'kind': 'paragraph', 'level': 0,
        'runs': [{
            'id': f'r{index}', 'text': text, 'bold': bold, 'italic': italic,
            'underline': False, 'audio_anchor_id': None,
        }],
    } for index, text in enumerate(paragraphs, start=1)]}


def _plain(document):
    return '\n'.join(node['runs'][0]['text'] for node in document['nodes'])


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv('CLINIC_MODE', '1')
    with main_app.app_context():
        scene = _scene()
        scene.audio = init_audio_revision(scene.dictation, scene.original, 10000)
        link_asr_segments(scene.dictation, scene.asr, SEGMENTS)
        scene.lease = acquire(scene.dictation.id, scene.author.id, datetime.now(timezone.utc))
        db.session.commit()
        ids = dict(dictation=scene.dictation.id, author=scene.author.id, asr=scene.asr.id,
                   generation=scene.lease.generation)

    flask = Flask(__name__)
    flask.config.update(TESTING=True, SECRET_KEY='synthetic-test-key',
                        SQLALCHEMY_DATABASE_URI=main_app.config['SQLALCHEMY_DATABASE_URI'])
    db.init_app(flask)
    login = LoginManager(flask)
    CSRFProtect(flask)
    receipts = {}

    @login.user_loader
    def load(uid):
        return db.session.get(User, int(uid))

    @flask.get('/as/<int:uid>')
    def as_user(uid):
        login_user(db.session.get(User, uid))
        return jsonify(csrf=generate_csrf())

    def run_once(key, digest, apply):
        if key in receipts:
            if receipts[key][0] != digest:
                raise CommandProblem('IDEMPOTENCY_CONFLICT')
            return receipts[key][1]
        try:
            result = apply()
            db.session.commit()
        except BaseException:
            db.session.rollback()
            raise
        receipts[key] = (digest, result)
        return result

    flask.register_blueprint(routes_correction.create_blueprint(
        authorize=lambda user, action, row: user.id == row.author_user_id, run_once=run_once,
        require_if_match=True))
    client = flask.test_client()
    headers = {'X-CSRF-Token': client.get(f"/as/{ids['author']}").json['csrf']}
    with main_app.app_context():
        yield client, headers, ids


def _get(client, ids):
    return client.get(BASE.format(ids['dictation']))


def _save(client, headers, ids, text, *, if_match, document=None, version=None, state=None,
          key=None, base=None, position=100, extra=None):
    sent = dict(headers, **{'Idempotency-Key': key or str(uuid.uuid4())})
    if if_match is not None:
        sent['If-Match'] = if_match
    payload = dict(text=text, audio_position_ms=position, lease_generation=ids['generation'],
                   expected_text_revision_id=ids['asr'] if base is None else base)
    if state is not None:
        payload['expected_state_version'] = state
    if document is not None:
        payload['document'] = document
    if version is not None:
        payload['expected_document_version'] = version
    if extra:
        payload.update(extra)
    return client.post(BASE.format(ids['dictation']) + '/save', headers=sent, json=payload)


def _revisions(ids):
    return db.session.query(ClinicTranscriptRevision).filter_by(dictation_id=ids['dictation']).count()


def _snapshots(revision_id=None):
    query = db.session.query(ClinicRichTextSnapshot)
    if revision_id is not None:
        query = query.filter_by(revision_id=revision_id)
    return query.count()


def _state(ids):
    dictation = db.session.get(ClinicDictation, ids['dictation'])
    return get_state(dictation, ids['author'])


def test_plain_open_and_save_stay_free_of_document_fields(api):
    client, headers, ids = api
    opened = _get(client, ids)
    assert opened.status_code == 200
    assert 'document' not in opened.json and 'document_version' not in opened.json
    saved = _save(client, headers, ids, 'Nur Klartext.', if_match=opened.headers['ETag'])
    assert saved.status_code == 200, saved.json
    assert 'document' not in saved.json and 'document_version' not in saved.json
    again = _get(client, ids)
    assert again.json['text'] == 'Nur Klartext.'
    assert 'document' not in again.json and 'document_version' not in again.json


def test_open_and_save_return_the_same_canonical_snapshot(api):
    client, headers, ids = api
    opened = _get(client, ids)
    document = _nodes(['Eins', 'Zwei'])
    saved = _save(client, headers, ids, _plain(document), if_match=opened.headers['ETag'],
                  document=document, version=0, position=1500)
    assert saved.status_code == 200, saved.json
    again = _get(client, ids)
    assert saved.json['document'] == document
    assert saved.json['document_version'] == 1
    assert again.json['document'] == saved.json['document']
    assert again.json['document_version'] == saved.json['document_version']
    assert again.json['text'] == 'Eins\nZwei'
    assert again.json['text_revision_id'] == saved.json['text_revision_id']
    assert load_document(saved.json['text_revision_id'], 1).nodes[0].runs[0].text == 'Eins'
    assert load_document(saved.json['text_revision_id'], 1).nodes[1].runs[0].text == 'Zwei'


def test_second_tab_cannot_overwrite_a_formatted_snapshot(api):
    client, headers, ids = api
    opened = _get(client, ids)
    base = _nodes(['Absatz'])
    first = _save(client, headers, ids, _plain(base), if_match=opened.headers['ETag'],
                  document=base, version=0, position=400)
    assert first.status_code == 200, first.json
    shared = _get(client, ids)
    assert shared.json['document_version'] == 1
    bold = _nodes(['Absatz'], bold=True)
    italic = _nodes(['Absatz'], italic=True)
    tab_a = _save(client, headers, ids, _plain(bold), if_match=shared.headers['ETag'],
                  document=bold, version=shared.json['document_version'],
                  state=shared.json['state']['version'], base=shared.json['text_revision_id'],
                  position=500)
    assert tab_a.status_code == 200, tab_a.json
    assert tab_a.json['document'] == bold and tab_a.json['document_version'] == 2
    before = (_revisions(ids), _snapshots(shared.json['text_revision_id']),
              _state(ids).version, _state(ids).audio_position_ms)
    # The other tab still holds the snapshot and the state it opened.
    tab_b = _save(client, headers, ids, _plain(italic), if_match=shared.headers['ETag'],
                  document=italic, version=shared.json['document_version'],
                  state=shared.json['state']['version'], base=shared.json['text_revision_id'],
                  position=999)
    assert (tab_b.status_code, tab_b.json['code']) == (409, 'REVISION_CONFLICT')
    assert 'document' not in tab_b.json and 'text' not in tab_b.json
    assert (_revisions(ids), _snapshots(shared.json['text_revision_id']),
            _state(ids).version, _state(ids).audio_position_ms) == before
    assert _get(client, ids).json['document'] == bold

    # State caught up, document version still the one this tab opened: still no write.
    caught_up = _save(client, headers, ids, _plain(italic), if_match=shared.headers['ETag'],
                      document=italic, version=shared.json['document_version'],
                      state=tab_a.json['state_version'], base=shared.json['text_revision_id'],
                      position=999)
    assert (caught_up.status_code, caught_up.json['code']) == (409, 'REVISION_CONFLICT')
    assert 'document' not in caught_up.json
    assert (_revisions(ids), _snapshots(shared.json['text_revision_id']),
            _state(ids).version, _state(ids).audio_position_ms) == before
    assert _get(client, ids).json['document'] == tab_a.json['document']


def test_exact_replay_returns_the_stored_snapshot(api):
    client, headers, ids = api
    opened = _get(client, ids)
    document = _nodes(['Einmal'])
    key = str(uuid.uuid4())
    first = _save(client, headers, ids, _plain(document), if_match=opened.headers['ETag'],
                  document=document, version=0, key=key, position=40)
    assert first.status_code == 200, first.json
    count = _snapshots(first.json['text_revision_id'])
    replay = _save(client, headers, ids, _plain(document), if_match=opened.headers['ETag'],
                   document=document, version=0, key=key, position=40)
    assert replay.status_code == 200 and replay.json == first.json
    assert _snapshots(first.json['text_revision_id']) == count
    assert _state(ids).version == first.json['state_version']
    changed = _nodes(['Einmal'], bold=True)
    clash = _save(client, headers, ids, _plain(changed), if_match=opened.headers['ETag'],
                  document=changed, version=0, key=key, position=40)
    assert (clash.status_code, clash.json['code']) == (409, 'IDEMPOTENCY_CONFLICT')
    assert _snapshots(first.json['text_revision_id']) == count
    assert _get(client, ids).json['document'] == document


def test_text_change_uses_the_snapshot_version_that_was_read(api):
    client, headers, ids = api
    opened = _get(client, ids)
    original = _nodes(['Alt'])
    first = _save(client, headers, ids, _plain(original), if_match=opened.headers['ETag'],
                  document=original, version=0)
    assert first.status_code == 200, first.json
    current = _get(client, ids)
    revised = _nodes(['Neu'])
    stale = _save(client, headers, ids, _plain(revised), if_match=current.headers['ETag'],
                  document=revised, version=0, state=current.json['state']['version'],
                  base=current.json['text_revision_id'])
    assert (stale.status_code, stale.json['code']) == (409, 'REVISION_CONFLICT')
    assert _revisions(ids) == 2
    assert load_document(first.json['text_revision_id'], 1).nodes[0].runs[0].text == 'Alt'
    saved = _save(client, headers, ids, _plain(revised), if_match=current.headers['ETag'],
                  document=revised, version=current.json['document_version'],
                  state=current.json['state']['version'], base=current.json['text_revision_id'],
                  position=80)
    assert saved.status_code == 200, saved.json
    assert saved.json['document'] == revised
    assert saved.json['document_version'] == 1
    assert saved.json['text_revision_id'] != first.json['text_revision_id']
    opened_new = _get(client, ids)
    assert opened_new.json['document'] == saved.json['document']
    assert opened_new.json['document_version'] == 1
    assert load_document(first.json['text_revision_id'], 1).nodes[0].runs[0].text == 'Alt'


def test_plain_save_does_not_erase_a_stored_snapshot(api):
    client, headers, ids = api
    opened = _get(client, ids)
    document = _nodes(['Bleibt'])
    rich = _save(client, headers, ids, _plain(document), if_match=opened.headers['ETag'],
                 document=document, version=0, position=10)
    assert rich.status_code == 200, rich.json
    current = _get(client, ids)
    plain = _save(client, headers, ids, _plain(document), if_match=current.headers['ETag'],
                  state=current.json['state']['version'], base=current.json['text_revision_id'],
                  position=11)
    assert plain.status_code == 200, plain.json
    assert 'document' not in plain.json
    kept = _get(client, ids)
    assert kept.json['document'] == document and kept.json['document_version'] == 1
    replaced = _save(client, headers, ids, 'Anders', if_match=kept.headers['ETag'],
                     state=kept.json['state']['version'], base=kept.json['text_revision_id'])
    assert replaced.status_code == 200, replaced.json
    assert 'document' not in replaced.json and 'document' not in _get(client, ids).json
    assert load_document(rich.json['text_revision_id'], 1).nodes[0].runs[0].text == 'Bleibt'
    assert _snapshots(replaced.json['text_revision_id']) == 0


def test_invalid_document_writes_nothing(api):
    client, headers, ids = api
    opened = _get(client, ids)
    etag = opened.headers['ETag']
    before = _revisions(ids)
    document = _nodes(['Absatz'])
    mismatch = _save(client, headers, ids, 'Anders', if_match=etag, document=document, version=0)
    assert mismatch.status_code == 400
    missing_version = _save(client, headers, ids, _plain(document), if_match=etag, document=document)
    assert missing_version.status_code == 400
    missing_document = _save(client, headers, ids, 'Anders', if_match=etag, version=0)
    assert missing_document.status_code == 400
    forged = _nodes(['Absatz'])
    forged['nodes'][0]['runs'][0]['href'] = 'javascript:alert(1)'
    hostile = _save(client, headers, ids, 'Absatz', if_match=etag, document=forged, version=0)
    assert hostile.status_code == 400
    anchored = _nodes(['Absatz'])
    anchored['nodes'][0]['runs'][0]['audio_anchor_id'] = 'missing-link'
    bad_anchor = _save(client, headers, ids, 'Absatz', if_match=etag, document=anchored, version=0)
    assert bad_anchor.status_code == 400
    moved = _save(client, headers, ids, _plain(document), if_match=etag, document=document, version=0,
                  extra={'move_map': []})
    assert moved.status_code == 400
    flagged = _save(client, headers, ids, _plain(document), if_match=etag, document=document, version=True)
    assert flagged.status_code == 400
    assert _revisions(ids) == before and _snapshots() == 0
