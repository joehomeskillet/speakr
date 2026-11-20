"""V8 autosave contract on the correction save path (UI8-004).

Synthetic data only. The test builds the real blueprint with ``require_if_match=True`` (as in the app mount), the real lease
service and the real correction transaction; only the receipt store is a small in-memory stand-in.
"""

import uuid
from datetime import datetime, timezone

import pytest
from flask import Flask, jsonify
from flask_login import LoginManager, login_user
from flask_wtf.csrf import CSRFProtect, generate_csrf

from src.app import app as main_app, db
from src.clinic import lease, routes_correction
from src.clinic.audio_revisions import init_audio_revision
from src.clinic.correction import link_asr_segments
from src.clinic.lease import acquire
from src.clinic.models import ClinicTranscriptRevision
from src.clinic.parity_dispatch import CommandProblem
from src.models import User
from test_correction import SEGMENTS, _scene

BASE = '/api/clinic/v1/dictations/{}/correction'


def _make(monkeypatch, require_if_match):
    monkeypatch.setenv('CLINIC_MODE', '1')
    with main_app.app_context():
        scene = _scene()
        scene.audio = init_audio_revision(scene.dictation, scene.original, 10000)
        link_asr_segments(scene.dictation, scene.asr, SEGMENTS)
        scene.lease = acquire(scene.dictation.id, scene.author.id, datetime.now(timezone.utc))
        db.session.commit()
        ids = dict(dictation=scene.dictation.id, author=scene.author.id, helper=scene.helper.id,
                   asr=scene.asr.id, generation=scene.lease.generation)

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
        authorize=lambda user, action, d: user.id == d.author_user_id, run_once=run_once,
        require_if_match=require_if_match))
    client = flask.test_client()
    headers = {'X-CSRF-Token': client.get(f"/as/{ids['author']}").json['csrf']}
    return client, headers, ids


@pytest.fixture
def api(monkeypatch):
    client, headers, ids = _make(monkeypatch, True)
    with main_app.app_context():
        yield client, headers, ids


@pytest.fixture
def legacy_api(monkeypatch):
    client, headers, ids = _make(monkeypatch, False)
    with main_app.app_context():
        yield client, headers, ids


def get(client, ids):
    return client.get(BASE.format(ids['dictation']))


def save(client, headers, ids, text, *, if_match, key=None, generation=None, base=None, state=None):
    sent = dict(headers, **{'Idempotency-Key': key or str(uuid.uuid4())})
    if if_match is not None:
        sent['If-Match'] = if_match
    payload = dict(text=text, audio_position_ms=100,
                   lease_generation=generation if generation is not None else ids['generation'],
                   expected_text_revision_id=base if base is not None else ids['asr'])
    if state is not None:
        payload['expected_state_version'] = state
    return client.post(BASE.format(ids['dictation']) + '/save', headers=sent, json=payload)


def revision_count(ids):
    return db.session.query(ClinicTranscriptRevision).filter_by(dictation_id=ids['dictation']).count()


def test_get_returns_etag_of_text_revision_and_lease_generation(api):
    client, _headers, ids = api
    resp = get(client, ids)
    expected = f'"{ids["asr"]}.g{ids["generation"]}"'
    assert resp.headers['ETag'] == expected
    assert resp.json['etag'] == expected


def test_missing_if_match_is_428_and_writes_nothing(api):
    client, headers, ids = api
    before = revision_count(ids)
    resp = save(client, headers, ids, 'Ohne Vorbedingung', if_match=None)
    assert (resp.status_code, resp.json['code']) == (428, 'PRECONDITION_REQUIRED')
    assert revision_count(ids) == before


def test_malformed_if_match_is_400(api):
    client, headers, ids = api
    assert save(client, headers, ids, 'x', if_match='W/"abc"').status_code == 400
    assert save(client, headers, ids, 'x', if_match='*').status_code == 400


def test_save_with_current_etag_returns_next_etag_and_chains(api):
    client, headers, ids = api
    first = save(client, headers, ids, 'Erster Stand.', if_match=get(client, ids).headers['ETag'])
    assert first.status_code == 200, first.json
    assert first.json['etag'] == f'"{first.json["text_revision_id"]}.g{ids["generation"]}"'
    second = save(client, headers, ids, 'Zweiter Stand.', if_match=first.json['etag'],
                  base=first.json['text_revision_id'], state=first.json['state_version'])
    assert second.status_code == 200, second.json
    assert get(client, ids).headers['ETag'] == second.json['etag']


def test_stale_revision_in_if_match_is_409_revision_conflict(api):
    client, headers, ids = api
    stale = get(client, ids).headers['ETag']
    ok = save(client, headers, ids, 'Neuer Stand.', if_match=stale)
    assert ok.status_code == 200
    before = revision_count(ids)
    late = save(client, headers, ids, 'Veraltet.', if_match=stale, base=ok.json['text_revision_id'])
    assert (late.status_code, late.json['code']) == (409, 'REVISION_CONFLICT')
    assert revision_count(ids) == before


def test_if_match_generation_must_match_the_lease(api):
    client, headers, ids = api
    wrong = f'"{ids["asr"]}.g{ids["generation"] + 5}"'
    resp = save(client, headers, ids, 'x', if_match=wrong)
    assert (resp.status_code, resp.json['code']) == (409, 'REVISION_CONFLICT')


def test_foreign_lease_holder_is_423(api):
    client, headers, ids = api
    now = datetime.now(timezone.utc)
    row, _previous = lease.takeover(ids['dictation'], ids['helper'], now)
    db.session.commit()
    resp = save(client, headers, ids, 'Fremd', if_match=f'"{ids["asr"]}.g{row.generation}"',
                generation=row.generation)
    assert (resp.status_code, resp.json['code']) == (423, 'LOCKED')


def test_same_key_same_body_returns_stored_result_and_other_body_is_rejected(api):
    client, headers, ids = api
    etag, key = get(client, ids).headers['ETag'], str(uuid.uuid4())
    first = save(client, headers, ids, 'Einmal.', if_match=etag, key=key)
    assert first.status_code == 200
    count = revision_count(ids)
    replay = save(client, headers, ids, 'Einmal.', if_match=etag, key=key)
    assert replay.status_code == 200 and replay.json == first.json
    assert revision_count(ids) == count
    clash = save(client, headers, ids, 'Anders.', if_match=etag, key=key)
    assert (clash.status_code, clash.json['code']) == (409, 'IDEMPOTENCY_CONFLICT')
    assert revision_count(ids) == count


def test_second_tab_takeover_makes_the_old_tab_save_fail_visibly(api):
    client, headers, ids = api
    tab_a_etag = get(client, ids).headers['ETag']
    row, _previous = lease.takeover(ids['dictation'], ids['author'], datetime.now(timezone.utc))
    db.session.commit()
    assert row.generation == ids['generation'] + 1
    before = revision_count(ids)
    old_tab = save(client, headers, ids, 'Alter Tab', if_match=tab_a_etag)
    assert (old_tab.status_code, old_tab.json['code']) == (409, 'LEASE_STALE')
    assert revision_count(ids) == before
    new_etag = get(client, ids).headers['ETag']
    assert new_etag == f'"{ids["asr"]}.g{row.generation}"'
    new_tab = save(client, headers, ids, 'Neuer Tab', if_match=new_etag, generation=row.generation)
    assert new_tab.status_code == 200, new_tab.json


def test_default_blueprint_keeps_the_header_optional(legacy_api):
    client, headers, ids = legacy_api
    assert save(client, headers, ids, 'Altvertrag', if_match=None).status_code == 200
    stale = save(client, headers, ids, 'x', if_match=f'"{uuid.uuid4()}.g{ids["generation"]}"')
    assert stale.status_code == 409
