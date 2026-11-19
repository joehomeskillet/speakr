"""HTTP evidence for the workflow commands (PAR-WF-005, 008-013).

Synthetic data only. Services are the reviewed ones; policy, receipts and
the audit sink are injected the way the app wiring will inject them.
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from flask import Flask
from flask_login import LoginManager, UserMixin, login_user
from flask_wtf.csrf import generate_csrf

from src.clinic import lease, policy, revisions, workflow
from src.clinic.acceptance_policy import SCOPE_DICTATION, AcceptanceLedger
from src.clinic.archive_policy import ARCHIVE, ArchiveBook, Links
from src.clinic.models import (
    ClinicAudioRevision, ClinicContextRevision, ClinicDictation, ClinicOriginal, ClinicRelease,
)
from src.clinic.parity_dispatch import CommandProblem, ParityDispatch
from src.clinic.routes_workflow import create_blueprint, register_commands
from src.database import db
from src.models import ProcessingJob, Recording, User

COMMANDS = '/api/clinic/v1/parity/commands'
NOW = datetime.now(timezone.utc)


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setenv('CLINIC_MODE', '1')
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY='synthetic-workflow-api',
                      SQLALCHEMY_DATABASE_URI='sqlite://')
    db.init_app(app)
    from src.services.job_queue import job_queue

    monkeypatch.setattr(job_queue, '_app', app)
    monkeypatch.setattr(job_queue, '_is_owner', False)

    def add(row):
        db.session.add(row)
        db.session.flush()
        return row

    with app.app_context():
        db.create_all()
        user = add(User(username='author', email='a@example.invalid', password='synthetic'))
        recording = add(Recording(user_id=user.id, title='Synthetic'))
        item = workflow.start(db.session(), 'create', author_user_id=user.id, pool_id='pool',
                              assignee_user_id=user.id, acceptance_required=True)
        dictation = add(ClinicDictation(id=item.id, recording_id=recording.id, author_user_id=user.id))
        for trigger in ('send_offline', 'skip_recognition'):
            workflow.transition(db.session(), item.id, trigger)
        original = add(ClinicOriginal(dictation_id=item.id, content_hash='a' * 64, size_bytes=1,
                                      storage_key='original/test', receipt='stored', origin_ref='test'))
        head = revisions.add_human_revision(dictation, None, 'Synthetischer Text.', user.id,
                                            original_version=original)
        audio = add(ClinicAudioRevision(dictation_id=item.id, content_hash='b' * 64))
        context = add(ClinicContextRevision(dictation_id=item.id, source_system='test',
                                            tenant_scope='test', case_ref='c', patient_ref='p'))
        fresh = datetime.now(timezone.utc)  # NOW is fixed at import; a long serial run outlives the lease
        held = lease.acquire(item.id, user.id, fresh)
        rules = {flag: AcceptanceLedger().publish(SCOPE_DICTATION, item.id, required=flag,
                                      approver_user_ids=[user.id], now=fresh) for flag in (True, False)}
        db.session.commit()
        state = {'grant': True, 'flags': 0, 'required': True, 'audit': []}
        receipts = {}

        def once(scope, digest, operation):
            if scope in receipts:
                if receipts[scope][0] != digest:
                    raise CommandProblem('IDEMPOTENCY_CONFLICT')
                return receipts[scope][1]
            result = operation()
            receipts[scope] = digest, result
            return result

        authorize = lambda _actor, _permission, _target: state['grant']  # noqa: E731
        book = ArchiveBook()
        app.register_blueprint(create_blueprint(
            authorize=authorize, run_once=once, allowed_actions=lambda *_: {'rename', 'priority'},
            record_audit=state['audit'].extend, trusted_context=lambda _c: True,
            open_review_flags=lambda *_: state['flags'], job_type='summarize', book=book,
            acceptance_policy=lambda _d: rules[state['required']]))
        dispatch = ParityDispatch(once)
        register_commands(dispatch, authorize, lambda _d: rules[state['required']])
        app.register_blueprint(dispatch.blueprint)

        login = LoginManager(app)

        class Actor(UserMixin):
            def __init__(self, uid):
                self.id = int(uid)

        login.user_loader(Actor)

        @app.get('/synthetic-login/<int:uid>')
        def browser(uid):
            login_user(Actor(uid))
            return {'csrf': generate_csrf()}

        with app.test_client() as client:
            token = client.get(f'/synthetic-login/{user.id}').json['csrf']
            yield dict(client=client, token=token, item=item, dictation=dictation, head=head,
                       audio=audio, context=context, held=held, user=user, state=state,
                       book=book, rules=rules, app=app)


def _post(w, path, body, *, version=0, key=None, headers=None):
    base = {'X-CSRFToken': w['token'], 'Idempotency-Key': key or str(uuid4()),
            'If-Match': f'"{w["item"].id}:{version}"'}
    return w['client'].post(f'/api/clinic/v1/dictations/{w["item"].id}/{path}', json=body,
                            headers=base | (headers or {}))


def _command(w, command, payload, revision, token=True):
    body = {'command': command, 'request_id': str(uuid4()), 'expected_revision': revision,
            'payload': payload | {'dictation_id': w['item'].id}}
    headers = {'Idempotency-Key': str(uuid4())} | ({'X-CSRF-Token': w['token']} if token else {})
    return w['client'].post(COMMANDS, json=body, headers=headers)


def test_guards_csrf_login_version_and_forged_actor(world):
    w = world
    url = f'/api/clinic/v1/dictations/{w["item"].id}/metadata'
    with w['app'].test_client() as anonymous:
        assert anonymous.post(url, json={}).status_code == 401
    assert w['client'].post(url, json={'fields': {'title': 'x'}}).status_code == 403
    no_match = w['client'].post(url, json={'fields': {'title': 'x'}},
                                headers={'X-CSRFToken': w['token'], 'Idempotency-Key': str(uuid4())})
    assert no_match.status_code == 428
    forged = _post(w, 'metadata', {'fields': {'title': 'x', 'actor_user_id': 99}})
    assert (forged.status_code, forged.json['code']) == (422, 'UNKNOWN_FIELD')
    w['state']['grant'] = False
    assert _post(w, 'metadata', {'fields': {'title': 'x'}}).status_code == 404
    assert db.session.get(ClinicDictation, w['item'].id).version_counter == 0


def test_metadata_positive_stale_and_idempotent_retry(world):
    w, key = world, str(uuid4())
    body = {'fields': {'title': 'Neuer Titel', 'priority': 'Dringend', 'deadline_date': '2030-02-01'}}
    first = _post(w, 'metadata', body, key=key)
    assert first.status_code == 200 and first.json['version'] == 1
    assert db.session.get(Recording, w['dictation'].recording_id).title == 'Neuer Titel'
    assert {row['field'] for row in w['state']['audit']} == {'title', 'priority', 'deadline_date'}
    assert _post(w, 'metadata', body, key=key).json == first.json
    assert db.session.get(ClinicDictation, w['item'].id).version_counter == 1
    reused = _post(w, 'metadata', {'fields': {'title': 'Anders'}}, key=key)
    assert (reused.status_code, reused.json['code']) == (409, 'IDEMPOTENCY_CONFLICT')
    stale = _post(w, 'metadata', {'fields': {'title': 'Zwei'}}, version=0)
    assert (stale.status_code, stale.json['code']) == (409, 'STALE_VERSION')
    assert stale.json['state']['version'] == 1 and stale.json['state']['etag'].endswith(':1"')
    assert _post(w, 'metadata', {'fields': {'title': 'Zwei'}}, version=1).json['version'] == 2


def _finalizable(w):
    workflow.set_acceptance(db.session(), w['item'].id, False)
    w['state']['required'] = False
    db.session.commit()
    return {'lease_id': w['held'].id, 'lease_generation': w['held'].generation,
            'original_version_id': w['audio'].id, 'transcript_revision_id': w['head'].id,
            'context_revision_id': w['context'].id, 'document_type': 'report',
            'mode': 'PLATFORM_REVIEWED_KIS_DRAFT', 'text_sha256': w['head'].normalised_text_hash,
            'confirmed_content': True, 'confirmed_context': True}


def test_release_once_replays_and_refuses_stale_or_closed_gates(world):
    w = world
    body = _finalizable(w)
    w['state']['flags'] = 1
    closed = _post(w, 'releases', body)
    assert (closed.status_code, closed.json['code']) == (422, 'OPEN_REVIEW_FLAGS')
    w['state']['flags'] = 0
    assert _post(w, 'releases', dict(body, text_sha256='f' * 64)).json['code'] == 'STALE_REVISION'
    assert _post(w, 'releases', dict(body, confirmed_content=False)).status_code == 400
    assert _post(w, 'releases', body, version=7).json['state']['version'] == 0
    assert db.session.query(ClinicRelease).count() == 0
    first = _post(w, 'releases', body)
    assert first.status_code == 201 and first.json['payload_sha256'] == w['head'].normalised_text_hash
    again = _post(w, 'releases', body, version=1)
    assert again.status_code == 200 and again.json['release_id'] == first.json['release_id']
    assert (db.session.query(ClinicRelease).count(), db.session.query(ProcessingJob).count()) == (1, 1)


def test_acceptance_commands_dispatch_with_revision_and_policy(world):
    w = world
    submit = {'text_revision_id': w['head'].id, 'context_revision_id': w['context'].id,
              'acceptance_rule_id': str(uuid4())}
    assert _command(w, 'request_acceptance', submit, 1, token=False).status_code == 403
    stale = _command(w, 'request_acceptance', submit, 5)
    assert (stale.status_code, stale.json['code']) == (409, 'REVISION_CONFLICT')
    w['state']['grant'] = False
    assert _command(w, 'request_acceptance', submit, 1).json['code'] == 'POLICY_DENIED'
    w['state']['grant'] = True
    done = _command(w, 'request_acceptance', submit, 1)
    assert done.status_code == 200 and done.json['revision'] == 2
    assert w['item'].status == workflow.TO_ACCEPT
    decide = {'text_revision_id': w['head'].id, 'decision': 'accept'}
    assert _command(w, 'decide_acceptance', dict(decide, text_revision_id=str(uuid4())), 2).json['code'] == 'REVISION_CONFLICT'
    returned = _command(w, 'decide_acceptance', dict(decide, decision='return', reason='Bitte prüfen'), 2)
    assert returned.status_code == 200 and returned.json['revision'] == 3
    assert w['item'].status == workflow.TO_CORRECT


def test_archive_confirm_stale_and_unauthorized(world, monkeypatch):
    w = world
    workflow.set_acceptance(db.session(), w['item'].id, False)
    workflow.transition(db.session(), w['item'].id, 'complete')
    db.session.commit()
    w['book'].register(w['item'].id, user_id=w['user'].id,
                       links=Links('o', w['audio'].id, w['head'].id, w['context'].id, None),
                       data_class='audio', finished_at=NOW - timedelta(days=40), retention_days=30)
    body = {'action': ARCHIVE, 'confirm': True}
    assert _post(w, 'archive', body, version=1).json['code'] == 'UNAUTHORIZED'
    monkeypatch.setattr(policy, 'build_subject',
                        lambda uid: policy.Subject(uid, False, (policy.Grant('own', frozenset({ARCHIVE})),), ()))
    monkeypatch.setattr(policy, 'build_facts', lambda rid, owner: policy.RecordingFacts(rid, owner))
    stale = _post(w, 'archive', body, version=4)
    assert (stale.status_code, stale.json['code']) == (409, 'STALE_VERSION')
    aborted = _post(w, 'archive', dict(body, confirm=False), version=1)
    assert aborted.status_code == 200 and aborted.json['applied'] is False
    assert w['item'].status == workflow.DONE
    moved = _post(w, 'archive', body, version=1)
    assert moved.status_code == 200 and moved.json['applied'] is True
    assert w['item'].status == workflow.TO_ARCHIVE
