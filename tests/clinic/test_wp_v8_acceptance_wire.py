"""Acceptance decision wiring (v8-47): binding of text, audio and context, Wiedervorlage, W11.

Synthetic data only. The ledger is the real durable one; policy, receipts and the audit sink
are injected the way app.py injects them.
"""

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from flask import Flask
from flask_login import LoginManager, UserMixin, login_user
from flask_wtf.csrf import generate_csrf

from src.clinic import lease, revisions, workflow
from src.clinic.acceptance_policy import SCOPE_DICTATION, AcceptanceLedger, ClinicAcceptanceDecision
from src.clinic.archive_policy import ArchiveBook
from src.clinic.models import (
    ClinicAudioRevision, ClinicContextRevision, ClinicDictation, ClinicOriginal, ClinicRelease,
)
from src.clinic.parity_dispatch import CommandProblem, ParityDispatch
from src.clinic.routes_workflow import acceptance_state, bound_decision, create_blueprint, register_commands
from src.database import db
from src.models import Recording, User
from src.models.audit import AuditEvent

COMMANDS = '/api/clinic/v1/parity/commands'


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setenv('CLINIC_MODE', '1')
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY='synthetic-acceptance-wire',
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
        head = revisions.add_human_revision(dictation, None, 'Text A.', user.id, original_version=original)
        audio = add(ClinicAudioRevision(dictation_id=item.id, content_hash='b' * 64))
        context = add(ClinicContextRevision(dictation_id=item.id, source_system='test',
                                            tenant_scope='test', case_ref='c', patient_ref='p'))
        fresh = datetime.now(timezone.utc)
        held = lease.acquire(item.id, user.id, fresh)
        ledger = AcceptanceLedger()
        ledger.publish(SCOPE_DICTATION, item.id, required=True, approver_user_ids=[user.id], now=fresh)
        db.session.commit()
        state = {'grant': True, 'flags': 0}
        receipts = {}

        def once(scope, digest, operation):
            if scope in receipts:
                if receipts[scope][0] != digest:
                    raise CommandProblem('IDEMPOTENCY_CONFLICT')
                return receipts[scope][1]
            try:
                result = operation()
            except BaseException:
                db.session.rollback()
                raise
            db.session.commit()
            receipts[scope] = digest, result
            return result

        authorize = lambda _actor, _permission, _target: state['grant']  # noqa: E731
        policy = lambda dictation: ledger.current(SCOPE_DICTATION, dictation.id)  # noqa: E731
        app.register_blueprint(create_blueprint(
            authorize=authorize, run_once=once, allowed_actions=lambda *_: set(),
            record_audit=lambda _rows: None, trusted_context=lambda _c: True,
            open_review_flags=lambda *_: state['flags'], job_type='summarize', book=ArchiveBook(),
            acceptance_policy=policy,
            acceptance_decision=lambda d, a, c: bound_decision(ledger, d, a, c),
            acceptance_ledger=ledger))
        dispatch = ParityDispatch(once)
        register_commands(dispatch, authorize, policy, ledger)
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
                       ledger=ledger, original=original)


def _version(w):
    return db.session.get(ClinicDictation, w['item'].id, populate_existing=True).version_counter


def _command(w, command, payload):
    body = {'command': command, 'request_id': str(uuid4()), 'expected_revision': _version(w) + 1,
            'payload': payload | {'dictation_id': w['item'].id}}
    return w['client'].post(COMMANDS, json=body, headers={
        'Idempotency-Key': str(uuid4()), 'X-CSRF-Token': w['token']})


def _release(w, text_id, **over):
    text = db.session.get(type(w['head']), text_id)
    body = {'lease_id': w['held'].id, 'lease_generation': w['held'].generation,
            'original_version_id': w['audio'].id, 'transcript_revision_id': text_id,
            'context_revision_id': w['context'].id, 'document_type': 'report',
            'mode': 'PLATFORM_REVIEWED_KIS_DRAFT', 'text_sha256': text.normalised_text_hash,
            'confirmed_content': True, 'confirmed_context': True} | over
    return w['client'].post(
        f'/api/clinic/v1/dictations/{w["item"].id}/releases', json=body,
        headers={'X-CSRFToken': w['token'], 'Idempotency-Key': str(uuid4()),
                 'If-Match': f'"{w["item"].id}:{_version(w)}"'})


def _to_accept(w):
    submit = {'text_revision_id': w['head'].id, 'context_revision_id': w['context'].id,
              'acceptance_rule_id': w['ledger'].current(SCOPE_DICTATION, w['item'].id).rule_id}
    assert _command(w, 'request_acceptance', submit).status_code == 200


def _accept(w, text_id=None):
    return _command(w, 'decide_acceptance', {'text_revision_id': text_id or w['head'].id,
                                             'decision': 'accept'})


def _state(w):
    return acceptance_state(w['ledger'], db.session.get(ClinicDictation, w['item'].id))


def test_accept_a_then_release_a_succeeds(world):
    w = world
    _to_accept(w)
    assert _state(w)['state'] == 'offen'
    assert _accept(w).status_code == 200
    assert _state(w)['state'] == 'angenommen'
    done = _release(w, w['head'].id)
    assert done.status_code == 201, done.json
    assert db.session.query(ClinicRelease).count() == 1


def test_release_of_b_after_accepting_a_is_rejected(world):
    w = world
    _to_accept(w)
    assert _accept(w).status_code == 200
    b = revisions.add_human_revision(w['dictation'], w['head'], 'Text B.', w['user'].id,
                                     original_version=w['original'])
    db.session.commit()
    blocked = _release(w, b.id)
    assert (blocked.status_code, blocked.json['code']) == (422, 'ACCEPTANCE_REQUIRED')
    assert blocked.json['acceptance']['state'] == 'wiedervorlage'
    assert _state(w)['state'] == 'wiedervorlage'
    stale = _release(w, w['head'].id)
    assert (stale.status_code, stale.json['code']) == (409, 'STALE_REVISION')
    assert stale.json['acceptance']['label'].startswith('Wiedervorlage')
    assert db.session.query(ClinicRelease).count() == 0


def test_autosave_revision_blocks_confirming_a(world):
    w = world
    _to_accept(w)
    revisions.add_human_revision(w['dictation'], w['head'], 'Text B.', w['user'].id,
                                 original_version=w['original'])
    db.session.commit()
    before = _version(w)
    late = _accept(w)
    assert (late.status_code, late.json['code']) == (409, 'REVISION_CONFLICT')
    assert _version(w) == before and w['item'].status == workflow.TO_ACCEPT
    assert db.session.query(ClinicAcceptanceDecision).count() == 0


def test_moved_audio_or_context_after_accept_blocks_release(world):
    w = world
    _to_accept(w)
    assert _accept(w).status_code == 200
    newer = ClinicAudioRevision(dictation_id=w['item'].id, parent_revision_id=w['audio'].id,
                                content_hash='c' * 64)
    db.session.add(newer)
    db.session.commit()
    assert _state(w)['state'] == 'wiedervorlage'
    blocked = _release(w, w['head'].id, original_version_id=newer.id)
    assert (blocked.status_code, blocked.json['code']) == (422, 'ACCEPTANCE_REQUIRED')
    assert db.session.query(ClinicRelease).count() == 0


def test_reject_needs_reason_then_is_recorded_and_audited(world):
    w = world
    _to_accept(w)
    plain = {'text_revision_id': w['head'].id, 'decision': 'return'}
    assert _command(w, 'decide_acceptance', plain).status_code == 400
    assert _command(w, 'decide_acceptance', plain | {'reason': '  '}).status_code == 400
    assert _command(w, 'decide_acceptance', plain | {'reason': 'ab'}).status_code == 400
    assert db.session.query(ClinicAcceptanceDecision).count() == 0
    audits = db.session.query(AuditEvent).count()
    done = _command(w, 'decide_acceptance', plain | {'reason': 'Dosierung unklar'})
    assert done.status_code == 200
    stored = db.session.query(ClinicAcceptanceDecision).one()
    assert (stored.kind, stored.reason) == ('reject', 'Dosierung unklar')
    assert db.session.query(AuditEvent).count() == audits + 1
    assert w['item'].status == workflow.TO_CORRECT
    assert _state(w)['state'] == 'zurueckgegeben'


def test_return_without_single_context_head_is_stored_and_audited(world):
    w = world
    _to_accept(w)
    db.session.add(ClinicContextRevision(dictation_id=w['item'].id, source_system='test',
                                         tenant_scope='test', case_ref='c2', patient_ref='p2'))
    db.session.commit()
    audits = db.session.query(AuditEvent).count()
    plain = {'text_revision_id': w['head'].id, 'decision': 'return', 'reason': 'Dosierung unklar'}
    done = _command(w, 'decide_acceptance', plain)
    assert done.status_code == 200, done.json
    stored = db.session.query(ClinicAcceptanceDecision).one()
    assert (stored.kind, stored.reason) == ('reject', 'Dosierung unklar')
    assert stored.revision_id.split('|')[0] == w['head'].id and stored.revision_id.endswith('|-')
    assert db.session.query(AuditEvent).count() == audits + 1
    assert w['item'].status == workflow.TO_CORRECT
    assert _state(w)['state'] == 'zurueckgegeben'


def test_accept_without_single_context_head_is_refused(world):
    w = world
    _to_accept(w)
    db.session.add(ClinicContextRevision(dictation_id=w['item'].id, source_system='test',
                                         tenant_scope='test', case_ref='c2', patient_ref='p2'))
    db.session.commit()
    assert _accept(w).status_code == 400
    assert db.session.query(ClinicAcceptanceDecision).count() == 0


def test_read_only_user_cannot_mutate(world):
    w = world
    _to_accept(w)
    w['state']['grant'] = False
    denied = _accept(w)
    assert (denied.status_code, denied.json['code']) == (403, 'POLICY_DENIED')
    assert db.session.query(ClinicAcceptanceDecision).count() == 0
    assert w['item'].status == workflow.TO_ACCEPT


def test_open_review_marker_refuses_finalisation_after_accept(world):
    w = world
    _to_accept(w)
    assert _accept(w).status_code == 200
    w['state']['flags'] = 1
    closed = _release(w, w['head'].id)
    assert (closed.status_code, closed.json['code']) == (422, 'OPEN_REVIEW_FLAGS')
    assert db.session.query(ClinicRelease).count() == 0
    w['state']['flags'] = 0
    assert _release(w, w['head'].id).status_code == 201


def test_unbound_or_foreign_decision_is_never_an_acceptance(world):
    w = world
    _to_accept(w)
    w['ledger'].decide_acceptance(
        SCOPE_DICTATION, w['item'].id, kind='accept', actor_id=w['user'].id, actor_pool_ids=(),
        revision_id=w['head'].id, reason=None, previous_assignee_id=None,
        now=datetime.now(timezone.utc))
    assert bound_decision(w['ledger'], w['dictation'], w['audio'].id, w['context'].id) is None
