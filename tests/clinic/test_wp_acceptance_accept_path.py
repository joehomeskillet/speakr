"""Evidence for the accept path of ``decide_acceptance`` (x17). Synthetic data only."""

from uuid import uuid4

from src.clinic import workflow, workflow_finalize
from src.clinic.models import ClinicDictation, ClinicRelease
from src.database import db
from tests.clinic.test_wp_workflow_api import NOW, _command, world  # noqa: F401


def _to_accept(w):
    submit = {'text_revision_id': w['head'].id, 'context_revision_id': w['context'].id,
              'acceptance_rule_id': str(uuid4())}
    assert _command(w, 'request_acceptance', submit, 1).status_code == 200
    assert w['item'].status == workflow.TO_ACCEPT
    return {'text_revision_id': w['head'].id, 'decision': 'accept'}


def _audit_count():
    from src.models.audit import AuditEvent

    return db.session.query(AuditEvent).count()


def test_eligible_acceptor_accepts_and_state_advances(world):
    w = world
    decide = _to_accept(w)
    before = _audit_count()
    done = _command(w, 'decide_acceptance', decide, 2)
    assert done.status_code == 200 and done.json['revision'] == 3
    assert w['item'].status == workflow.ACCEPTED
    assert db.session.get(ClinicDictation, w['item'].id).version_counter == 2
    assert _audit_count() == before + 1


def test_ineligible_actor_is_refused_and_nothing_written(world):
    w = world
    decide = _to_accept(w)
    w['state']['grant'] = False
    denied = _command(w, 'decide_acceptance', decide, 2)
    assert denied.json['code'] == 'POLICY_DENIED'
    # Authorised for the capability but not an acceptor under the policy.
    w['state']['grant'] = True
    from src.clinic.acceptance_policy import SCOPE_DICTATION, AcceptanceLedger

    other = AcceptanceLedger().publish(SCOPE_DICTATION, w['item'].id + ':other', required=True,
                                       approver_user_ids=[w['user'].id + 1000], now=NOW)
    w['rules'][True] = other
    before = _audit_count()
    refused = _command(w, 'decide_acceptance', decide, 2)
    assert refused.json['code'] == 'POLICY_DENIED'
    assert w['item'].status == workflow.TO_ACCEPT
    assert db.session.get(ClinicDictation, w['item'].id).version_counter == 1
    assert _audit_count() == before


def test_stale_version_and_outdated_revision_are_refused(world):
    w = world
    decide = _to_accept(w)
    stale = _command(w, 'decide_acceptance', decide, 9)
    assert (stale.status_code, stale.json['code']) == (409, 'REVISION_CONFLICT')
    outdated = _command(w, 'decide_acceptance', dict(decide, text_revision_id=str(uuid4())), 2)
    assert (outdated.status_code, outdated.json['code']) == (409, 'REVISION_CONFLICT')
    assert w['item'].status == workflow.TO_ACCEPT
    assert db.session.get(ClinicDictation, w['item'].id).version_counter == 1


def test_open_review_flag_blocks_completion_after_accept(world):
    w = world
    decide = _to_accept(w)
    assert _command(w, 'decide_acceptance', decide, 2).status_code == 200
    w['state']['flags'] = 1

    def finalize():
        return workflow_finalize.finalize(
            w['item'].id, actor_user_id=w['user'].id, expected_version=2,
            lease_generation=w['held'].generation, now=NOW, authorize=lambda *_: True,
            audio_revision_id=w['audio'].id, transcript_revision_id=w['head'].id,
            context_revision_id=w['context'].id, document_type='report', job_type='summarize',
            trusted_context=lambda _c: True, open_review_flags=lambda *_: w['state']['flags'],
            acceptance_policy=lambda _d: w['rules'][True])

    try:
        finalize()
    except workflow.WorkflowError as error:
        assert str(error) == 'open_review_flags'
    else:
        raise AssertionError('finalize must refuse with an open review flag')
    assert w['item'].status == workflow.ACCEPTED
    assert db.session.query(ClinicRelease).count() == 0


def test_replay_with_same_request_returns_same_result(world):
    w = world
    decide = _to_accept(w)
    body = {'command': 'decide_acceptance', 'request_id': str(uuid4()), 'expected_revision': 2,
            'payload': decide | {'dictation_id': w['item'].id}}
    headers = {'Idempotency-Key': str(uuid4()), 'X-CSRF-Token': w['token']}
    url = '/api/clinic/v1/parity/commands'
    first = w['client'].post(url, json=body, headers=headers)
    again = w['client'].post(url, json=body, headers=headers)
    assert first.status_code == 200 and again.json == first.json
    assert db.session.get(ClinicDictation, w['item'].id).version_counter == 2
