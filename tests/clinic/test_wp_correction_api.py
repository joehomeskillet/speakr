"""HTTP evidence for the correction save, anchor and marker endpoints (w100)."""

import uuid
from datetime import datetime, timezone

import pytest
from flask import Flask, jsonify
from flask_login import LoginManager, login_user
from flask_wtf.csrf import CSRFProtect, generate_csrf

from src.app import app as main_app, db
from src.clinic import live_session, routes_correction
from src.clinic.audio_revisions import init_audio_revision
from src.clinic.correction import link_asr_segments
from src.clinic.lease import acquire
from src.clinic.parity_dispatch import CommandProblem
from src.models import User
from test_correction import BODY, SEGMENTS, _scene

BASE = '/api/clinic/v1/dictations/{}/correction'


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv('CLINIC_MODE', '1')
    with main_app.app_context():
        scene = _scene()
        scene.audio = init_audio_revision(scene.dictation, scene.original, 10000)
        link_asr_segments(scene.dictation, scene.asr, SEGMENTS)
        scene.lease = acquire(scene.dictation.id, scene.author.id, datetime.now(timezone.utc))
        db.session.commit()
        ids = dict(dictation=scene.dictation.id, author=scene.author.id, helper=scene.helper.id,
                   asr=scene.asr.id, audio=scene.audio.id, generation=scene.lease.generation)

    flask = Flask(__name__)
    flask.config.update(TESTING=True, SECRET_KEY='synthetic-test-key',
                        SQLALCHEMY_DATABASE_URI=main_app.config['SQLALCHEMY_DATABASE_URI'])
    db.init_app(flask)
    login = LoginManager(flask)
    CSRFProtect(flask)
    receipts, calls = {}, {'fail': False}

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
            if calls['fail']:
                raise RuntimeError('store down')
            result = apply()
            db.session.commit()
        except RuntimeError as error:
            db.session.rollback()
            raise CommandProblem('DEPENDENCY_UNAVAILABLE') from error
        except BaseException:
            db.session.rollback()
            raise
        receipts[key] = (digest, result)
        return result

    flask.register_blueprint(routes_correction.create_blueprint(
        authorize=lambda user, action, d: user.id == d.author_user_id, run_once=run_once))
    def login_as(uid):
        c = flask.test_client()
        return c, {'X-CSRF-Token': c.get(f'/as/{uid}').json['csrf']}

    client, headers = login_as(ids['author'])
    with main_app.app_context():
        yield client, headers, ids, calls, login_as, flask


def post(client, headers, ids, tail, payload, key=None):
    h = dict(headers, **{'Idempotency-Key': key or str(uuid.uuid4())})
    return client.post(BASE.format(ids['dictation']) + tail, headers=h, json=payload)


def save_payload(ids, text, **extra):
    return dict(text=text, audio_position_ms=100, lease_generation=ids['generation'],
                expected_text_revision_id=ids['asr'], **extra)


def test_correction_get_reports_numeric_bundle_version(api):
    client, headers, ids, *_ = api
    url = BASE.format(ids['dictation'])
    before = client.get(url).json
    assert before['text_revision_id'] == ids['asr']
    assert before['bundle_version'] == 1
    saved = post(client, headers, ids, '/save', save_payload(ids, BODY + ' Neu'))
    assert saved.status_code == 200
    after = client.get(url).json
    assert after['text_revision_id'] == saved.json['text_revision_id']
    assert after['bundle_version'] == 2


def test_save_derives_links_and_reports_unaligned(api):
    client, headers, ids, *_ = api
    text = BODY.replace('drei', 'zwei') + ' Neu'
    resp = post(client, headers, ids, '/save', save_payload(ids, text))
    assert resp.status_code == 200, resp.json
    assert resp.json['state_version'] == 1 and resp.json['derived_links'] > 0
    got = client.get(BASE.format(ids['dictation']), query_string={'offset': text.index('Husten')}).json
    assert got['text_revision_id'] == resp.json['text_revision_id']
    assert got['selection']['aligned'] and got['selection']['audio_start_ms'] == 0
    changed = client.get(BASE.format(ids['dictation']), query_string={'offset': text.index('zwei')}).json
    assert changed['selection']['aligned'] and changed['selection']['audio_end_ms'] == 4200
    new_word = client.get(BASE.format(ids['dictation']), query_string={'offset': text.index('Neu')}).json
    assert new_word['selection'] == {'aligned': False}


def test_stale_text_state_and_lease_are_rejected(api):
    client, headers, ids, *_ = api
    stale = post(client, headers, ids, '/save', dict(save_payload(ids, 'x'), expected_text_revision_id=None))
    assert (stale.status_code, stale.json['code']) == (409, 'REVISION_CONFLICT')
    wrong_lease = post(client, headers, ids, '/save', dict(save_payload(ids, 'x'), lease_generation=99))
    assert wrong_lease.status_code == 409
    ok = post(client, headers, ids, '/save', save_payload(ids, 'Neuer Text.'))
    assert ok.status_code == 200
    state = post(client, headers, ids, '/save', dict(
        save_payload(ids, 'Noch neuer.', expected_state_version=7),
        expected_text_revision_id=ok.json['text_revision_id']))
    assert state.status_code == 409


def test_unauthenticated_csrf_foreign_actor_and_extra_keys(api):
    client, headers, ids, _, login_as, flask = api
    url = BASE.format(ids['dictation'])
    anonymous = flask.test_client()
    assert anonymous.get(url).status_code == 401
    assert client.post(url + '/save', json={}, headers={'Idempotency-Key': str(uuid.uuid4())}).status_code == 403
    other, other_headers = login_as(ids['helper'])
    assert other.get(url).status_code == 404
    assert post(other, other_headers, ids, '/save', save_payload(ids, 'x')).status_code == 404
    forged = post(client, headers, ids, '/save', dict(save_payload(ids, 'x'), actor_user_id=ids['helper']))
    assert forged.status_code == 400
    assert client.post(url + '/save', headers=headers, json=save_payload(ids, 'x')).status_code == 400


def test_live_holder_blocks_foreign_save(api):
    client, headers, ids, *_ = api
    live_session.start(ids['dictation'], ids['helper'], datetime.now(timezone.utc))
    db.session.commit()
    resp = post(client, headers, ids, '/save', save_payload(ids, 'Fremd'))
    assert (resp.status_code, resp.json['code']) == (423, 'LOCKED')


def test_failure_then_retry_and_replay(api):
    client, headers, ids, calls, *_ = api
    key = str(uuid.uuid4())
    calls['fail'] = True
    failed = post(client, headers, ids, '/save', save_payload(ids, 'Retry'), key)
    assert (failed.status_code, failed.json['code']) == (503, 'DEPENDENCY_UNAVAILABLE')
    calls['fail'] = False
    done = post(client, headers, ids, '/save', save_payload(ids, 'Retry'), key)
    assert done.status_code == 200
    replay = post(client, headers, ids, '/save', save_payload(ids, 'Retry'), key)
    assert replay.json == done.json
    clash = post(client, headers, ids, '/save', save_payload(ids, 'Anders'), key)
    assert (clash.status_code, clash.json['code']) == (409, 'IDEMPOTENCY_CONFLICT')


def test_marker_lifecycle_is_revision_bound_with_utf16_anchor(api):
    client, headers, ids, *_ = api
    text = '😀 Anamnese ä'
    saved = post(client, headers, ids, '/save', save_payload(ids, text)).json
    marker = post(client, headers, ids, '/markers', dict(
        position_ms=500, audio_revision_id=ids['audio'], lease_generation=ids['generation'],
        label='Anamnese', anchor_offset=3))
    assert marker.status_code == 200, marker.json
    assert marker.json['anchor_offset'] == 3 and marker.json['anchor_revision_id'] == saved['text_revision_id']
    assert marker.json['audio_revision_id'] == ids['audio'] and marker.json['version'] == 1
    mid, edit = marker.json['id'], dict(lease_generation=ids['generation'])
    stale = post(client, headers, ids, f'/markers/{mid}/move', dict(edit, position_ms=900, expected_version=5))
    assert stale.status_code == 409
    moved = post(client, headers, ids, f'/markers/{mid}/move', dict(edit, position_ms=900, expected_version=1))
    assert moved.status_code == 200 and moved.json['version'] == 2
    outside = post(client, headers, ids, f'/markers/{mid}/move', dict(edit, position_ms=99999, expected_version=2))
    assert outside.status_code == 400
    post(client, headers, ids, f'/markers/{mid}/delete', dict(edit, expected_version=2))
    assert client.get(BASE.format(ids['dictation'])).json['markers'] == []
    bad = post(client, headers, ids, '/markers', dict(
        position_ms=1, audio_revision_id=ids['audio'], lease_generation=ids['generation'], anchor_offset=1))
    assert bad.status_code == 400  # inside a surrogate pair


def test_marker_needs_lease_and_unknown_marker_is_404(api):
    client, headers, ids, *_ = api
    resp = post(client, headers, ids, '/markers', dict(
        position_ms=1, audio_revision_id=ids['audio'], lease_generation=42))
    assert resp.status_code == 409
    missing = post(client, headers, ids, f'/markers/{uuid.uuid4()}/delete',
                   dict(lease_generation=ids['generation'], expected_version=1))
    assert missing.status_code == 404
