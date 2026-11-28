"""PAR-COR-009: the editor marks a saved selection as one vocabulary proposal.

The phrase comes from the confirmed transcript revision of a dictation the
caller may correct. Repeating the mark returns the same PENDING review item
and never publishes a user or pool vocabulary entry.
"""

import uuid
from datetime import datetime, timezone

from flask import Flask, jsonify
from flask_login import LoginManager, login_user
from flask_wtf.csrf import CSRFProtect, generate_csrf

from src.app import app as main_app, db
from src.clinic import routes_correction, vocabulary
from src.clinic.lease import acquire
from src.clinic.parity_dispatch import CommandProblem
from src.models import User
from test_correction import BODY, _scene

BASE = '/api/clinic/v1/dictations/{}/correction'


def _api(monkeypatch):
    monkeypatch.setenv('CLINIC_MODE', '1')
    with main_app.app_context():
        scene = _scene()
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
        authorize=lambda user, action, d: user.id == d.author_user_id, run_once=run_once))

    def login_as(uid):
        client = flask.test_client()
        return client, {'X-CSRF-Token': client.get(f'/as/{uid}').json['csrf']}

    return login_as, ids


def _post(client, headers, ids, payload, key=None):
    sent = dict(headers, **{'Idempotency-Key': key or str(uuid.uuid4())})
    return client.post(BASE.format(ids['dictation']) + '/vocabulary-proposal', headers=sent, json=payload)


def _payload(ids, **extra):
    start = BODY.index('Husten')
    return dict(expected_text_revision_id=ids['asr'], selection_start=start,
                selection_end=start + len('Husten'), lease_generation=ids['generation'], **extra)


def test_selection_is_bound_to_the_confirmed_revision_and_the_editor(monkeypatch):
    login_as, ids = _api(monkeypatch)
    client, headers = login_as(ids['author'])
    marked = _post(client, headers, ids, _payload(ids))
    assert marked.status_code == 200, marked.json
    assert marked.json['written'] == 'Husten'
    assert marked.json['text_revision_id'] == ids['asr']
    assert marked.json['status'] == vocabulary.REVIEW_STATUS_PENDING
    assert marked.json['published'] is False

    stale = _post(client, headers, ids, dict(_payload(ids), expected_text_revision_id=str(uuid.uuid4())))
    assert (stale.status_code, stale.json['code']) == (409, 'REVISION_CONFLICT')
    outside = _post(client, headers, ids, dict(_payload(ids), selection_start=0, selection_end=len(BODY) + 4))
    assert outside.status_code == 400
    helper, helper_headers = login_as(ids['helper'])
    denied = _post(helper, helper_headers, ids, _payload(ids))
    assert denied.status_code == 404

    with main_app.app_context():
        item = db.session.get(vocabulary.ClinicVocabularyReviewItem, marked.json['id'])
        assert (item.user_id, item.dictation_id, item.origin) == (
            ids['author'], ids['dictation'], vocabulary.ORIGIN_CORRECTION)
        assert item.information == f"text_revision_id={ids['asr']}"
        assert db.session.query(vocabulary.ClinicVocabularyEntry).count() == 0


def test_repeating_the_mark_keeps_one_pending_proposal(monkeypatch):
    login_as, ids = _api(monkeypatch)
    client, headers = login_as(ids['author'])
    first = _post(client, headers, ids, _payload(ids))
    second = _post(client, headers, ids, _payload(ids))
    assert first.status_code == second.status_code == 200
    assert second.json['id'] == first.json['id']
    assert second.json['status'] == vocabulary.REVIEW_STATUS_PENDING

    with main_app.app_context():
        pending = vocabulary.list_review_items(
            user_id=ids['author'], status=vocabulary.REVIEW_STATUS_PENDING)
        assert [row.id for row in pending if row.written_form == 'Husten'] == [first.json['id']]
        assert db.session.query(vocabulary.ClinicVocabularyEntry).count() == 0


def test_marking_does_not_publish_user_or_pool_vocabulary(monkeypatch):
    login_as, ids = _api(monkeypatch)
    client, headers = login_as(ids['author'])
    marked = _post(client, headers, ids, _payload(ids))
    assert marked.status_code == 200

    with main_app.app_context():
        item = db.session.get(vocabulary.ClinicVocabularyReviewItem, marked.json['id'])
        assert item.status == vocabulary.REVIEW_STATUS_PENDING
        assert item.pool_id is None
        user_words = vocabulary.search_vocabulary('Husten', user_id=ids['author'], scope='user')
        assert user_words == []
        assert db.session.query(vocabulary.ClinicVocabularyEntry).filter_by(written_form='Husten').count() == 0
