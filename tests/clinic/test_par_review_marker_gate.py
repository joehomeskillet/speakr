"""PAR-VOC-004: scoped W11 confirmations and release gates on synthetic dictations."""

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from flask import current_app, g
from flask_login import LoginManager, login_user
from flask_wtf.csrf import generate_csrf

from src.clinic import caution_words, finalize_gates, lease, revisions, routes_correction, workflow
from src.clinic.acceptance_policy import SCOPE_DICTATION, AcceptanceLedger
from src.clinic.audio_revisions import init_audio_revision
from src.clinic.correction import ClinicMarker, marker_events
from src.clinic.kis import MockKisAdapter
from src.clinic.models import ClinicRelease
from src.clinic.parity_receipts import run_once
from src.clinic.workflow_finalize import finalize
from src.database import db
from src.models import User
from test_wp_finalize_gates import _bind, case  # noqa: F401

BODY = '😀 Links, rechts.'
RULES = caution_words.CautionRuleSet('pool', 'v1', ('Links', 'rechts'), authorized=True)


@pytest.fixture
def scene(case, monkeypatch):
    dictation, first_head, original, author, context, _ = case
    monkeypatch.setenv('CLINIC_PROFILE', 'staging')
    app = current_app._get_current_object()
    app.config['SECRET_KEY'] = 'synthetic-review-marker-test'
    login = LoginManager(app)
    login.user_loader(lambda uid: db.session.get(User, int(uid)))

    @app.before_request
    def reset_request_cache():
        # The shared test app context outlives each request.
        g.pop('_login_user', None)
        g.pop('csrf_token', None)

    @app.get('/test-login/<int:uid>')
    def test_login(uid):
        login_user(db.session.get(User, uid))
        return {'csrf': generate_csrf()}

    app.register_blueprint(routes_correction.create_blueprint(
        authorize=lambda user, _action, row: user.id == row.author_user_id, run_once=run_once))
    stranger = User(username='stranger', email='stranger@example.invalid', password='synthetic')
    db.session.add(stranger)
    dictation.pool_id = RULES.pool_id
    head = revisions.add_human_revision(dictation, first_head, BODY, author.id)
    held = lease.acquire(dictation.id, author.id, datetime.now(timezone.utc))
    audio = init_audio_revision(dictation, original, 1000)
    _bind(context, author)

    def inspect(rules=RULES):
        hits = caution_words.inspect(dictation, rules, author.id, audio_revision_id=audio.id,
                                     lease_generation=held.generation)
        db.session.commit()
        return hits

    hits = inspect()

    def client_for(uid):
        client = app.test_client()
        token = client.get(f'/test-login/{uid}').json['csrf']
        return client, {'X-CSRF-Token': token}

    client, headers = client_for(author.id)
    correction_url = f'/api/clinic/v1/dictations/{dictation.id}/correction'

    def confirm(marker_id, revision_id=head.id, key=None, **overrides):
        return client.post(f'{correction_url}/review-markers/{marker_id}/confirm',
                           headers={**headers, 'Idempotency-Key': key or str(uuid4())},
                           json={'expected_text_revision_id': revision_id,
                                 'lease_generation': held.generation, **overrides})

    def release(trusted=True):
        return finalize(dictation.id, actor_user_id=author.id, expected_version=dictation.version_counter,
                        lease_generation=held.generation, now=datetime.now(timezone.utc),
                        authorize=lambda actor, _permission, row: actor == row.author_user_id,
                        audio_revision_id=audio.id, transcript_revision_id=head.id,
                        context_revision_id=context.id, document_type='report', job_type='summarize',
                        trusted_context=lambda row: finalize_gates.trusted_context(row, MockKisAdapter() if trusted else None),
                        open_review_flags=finalize_gates.open_review_flags,
                        acceptance_policy=lambda row: AcceptanceLedger().current(SCOPE_DICTATION, row.id))

    return SimpleNamespace(dictation=dictation, head=head, author=author, stranger=stranger,
                           app=app, client_for=client_for, client=client, headers=headers,
                           correction_url=correction_url, url=correction_url + '/review-markers',
                           hits=hits, inspect=inspect, confirm=confirm, finalize=release,
                           generation=held.generation, audio=audio, original=original)


def test_release_requires_both_markers_and_normal_context_checks(scene):
    first, second = scene.hits
    result = scene.client.get(scene.url)
    assert result.status_code == 200
    assert result.headers['Cache-Control'] == 'no-store'
    assert result.json == {
        'text_revision_id': scene.head.id,
        'review_markers': [
            {'id': first.marker_id, 'label': 'Links', 'anchor_offset': 3},
            {'id': second.marker_id, 'label': 'rechts', 'anchor_offset': 10},
        ],
    }
    assert finalize_gates.open_review_flags(scene.dictation, scene.head) == 2
    response = scene.confirm(first.marker_id)
    assert response.status_code == 200
    assert response.json == {'id': first.marker_id, 'text_revision_id': scene.head.id, 'confirmed': True}
    assert [m['id'] for m in scene.client.get(scene.url).json['review_markers']] == [second.marker_id]
    with pytest.raises(workflow.WorkflowError, match='open_review_flags'):
        scene.finalize()
    assert db.session.query(ClinicRelease).count() == 0

    assert scene.confirm(second.marker_id).status_code == 200
    marker = db.session.get(ClinicMarker, second.marker_id)
    assert [(event.action, event.actor_user_id) for event in marker_events(marker)] == [
        ('CREATE', scene.author.id), ('DELETE', scene.author.id)]
    assert finalize_gates.open_review_flags(scene.dictation, scene.head) == 0
    with pytest.raises(workflow.WorkflowError, match='untrusted_context'):
        scene.finalize(trusted=False)
    assert db.session.query(ClinicRelease).count() == 0
    assert scene.finalize().replayed is False


def test_stale_confirmation_cannot_clear_markers_on_a_new_revision(scene):
    first, second = scene.hits
    key = str(uuid4())
    initial = scene.confirm(first.marker_id, key=key)
    assert initial.status_code == 200
    assert scene.confirm(first.marker_id, key=key).json == initial.json
    scene.dictation.pool_id = 'pool-other'
    db.session.commit()
    assert scene.confirm(first.marker_id, key=key).status_code == 409
    scene.dictation.pool_id = RULES.pool_id
    newer = revisions.add_human_revision(scene.dictation, scene.head, BODY + ' Neu.', scene.author.id)
    fresh = scene.inspect()
    assert len(fresh) == 2
    for response in (scene.confirm(second.marker_id), scene.confirm(second.marker_id, newer.id),
                     scene.confirm(first.marker_id, key=key)):
        assert (response.status_code, response.json['code']) == (409, 'REVISION_CONFLICT')
    assert [m['id'] for m in scene.client.get(scene.url).json['review_markers']] == [h.marker_id for h in fresh]
    assert finalize_gates.open_review_flags(scene.dictation, newer) == 2


def test_review_markers_are_scoped_and_reserved_from_generic_edits(scene):
    first, second = scene.hits
    scene.dictation.pool_id = 'pool-other'
    (foreign,) = scene.inspect(caution_words.CautionRuleSet('pool-other', 'v1', ('Links',), authorized=True))
    scene.dictation.pool_id = RULES.pool_id
    side = revisions.add_asr_revision(scene.dictation, scene.original, 'ASR side.', 'synthetic',
                                      parent_revision=scene.head)
    db.session.commit()
    assert revisions.is_flagged(scene.dictation, side)
    assert finalize_gates.open_review_flags(scene.dictation, scene.head) == 3
    assert [m['id'] for m in scene.client.get(scene.url).json['review_markers']] == [first.marker_id, second.marker_id]
    assert scene.client.get(scene.correction_url).json['markers'] == []
    assert scene.confirm(foreign.marker_id).status_code == 409
    assert scene.confirm(first.marker_id, lease_generation=scene.generation + 1).status_code == 409

    stranger, headers = scene.client_for(scene.stranger.id)
    confirm_url = f'{scene.url}/{first.marker_id}/confirm'
    data = {'expected_text_revision_id': scene.head.id, 'lease_generation': scene.generation}
    assert scene.app.test_client().get(scene.url).status_code == 401
    assert stranger.get(scene.url).status_code == 404
    assert stranger.post(confirm_url, json=data, headers={**headers, 'Idempotency-Key': str(uuid4())}).status_code == 404
    assert scene.client.post(confirm_url, json=data).status_code == 403

    headers = {**scene.headers, 'Idempotency-Key': str(uuid4())}
    raw_marker = f'{scene.correction_url}/markers/{first.marker_id}'
    edit = {'expected_version': 1, 'lease_generation': scene.generation}
    assert scene.client.post(raw_marker + '/delete', headers=headers, json=edit).status_code == 404
    assert scene.client.post(raw_marker + '/move', headers=headers, json={**edit, 'position_ms': 1}).status_code == 404
    forged = scene.client.post(scene.correction_url + '/markers', headers=headers, json={
        'position_ms': 0, 'audio_revision_id': scene.audio.id, 'lease_generation': scene.generation,
        'marker_type': 'INDEX', 'label': 'caution|pool|v1|Links|2', 'anchor_offset': 3,
    })
    assert forged.status_code == 400
    assert finalize_gates.open_review_flags(scene.dictation, scene.head) == 3
