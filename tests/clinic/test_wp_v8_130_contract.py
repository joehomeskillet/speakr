"""Owner-approved binding contract regressions. Synthetic, local databases only."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from src.clinic import lease, revisions, workflow
from src.clinic.models import ClinicAudioRevision, ClinicContextRevision, ClinicDictation, ClinicOriginal, ClinicRelease
from src.database import db
from src.models import ProcessingJob, Recording
from tests.clinic.test_wp_workflow_api import world, _command, _finalizable, _post
from tests.clinic.test_wp_workflow_finalize import case
from tests.clinic.test_wp_v8_lease_takeover import world as lease_world


def _submit(w, **overrides):
    return dict(text_revision_id=w['head'].id, context_revision_id=w['context'].id,
                acceptance_rule_id=w['rules'][True].rule_id) | overrides


def test_rule_uuid_is_stable_and_separates_scope_target_version(world):
    rule = world['rules'][True]
    assert UUID(rule.rule_id).version == 5
    assert replace(rule).rule_id == rule.rule_id
    assert len({rule.rule_id, replace(rule, scope='routing').rule_id,
                replace(rule, subject_id='another').rule_id,
                replace(rule, version=rule.version + 1).rule_id}) == 4


@pytest.mark.parametrize('kind', ['random', 'version', 'target', 'scope'])
def test_submit_rejects_wrong_policy_uuid_without_mutation(world, kind):
    w = world
    rule = w['rules'][True]
    ids = {'random': str(uuid4()), 'version': replace(rule, version=rule.version + 1).rule_id,
           'target': replace(rule, subject_id='other').rule_id,
           'scope': replace(rule, scope='routing').rule_id}
    result = _command(w, 'request_acceptance', _submit(w, acceptance_rule_id=ids[kind]), 1)
    assert (result.status_code, result.json['code']) == (409, 'REVISION_CONFLICT')
    assert w['dictation'].version_counter == 0
    assert w['item'].status == workflow.TO_WRITE


@pytest.mark.parametrize('missing', ['audio', 'context'])
def test_submit_requires_audio_and_current_context(world, missing):
    w = dict(world)
    def add(row):
        db.session.add(row)
        db.session.flush()
        return row
    item = workflow.start(db.session(), 'create', author_user_id=w['user'].id, pool_id='pool',
                          assignee_user_id=w['user'].id, acceptance_required=True)
    rec = add(Recording(user_id=w['user'].id, title='Synthetic missing binding'))
    dic = add(ClinicDictation(id=item.id, recording_id=rec.id, author_user_id=w['user'].id))
    for trigger in ('send_offline', 'skip_recognition'):
        workflow.transition(db.session(), item.id, trigger)
    original = add(ClinicOriginal(dictation_id=item.id, content_hash='c' * 64, size_bytes=1,
                                 storage_key='test/missing', receipt='stored', origin_ref='test'))
    head = revisions.add_human_revision(dic, None, 'Synthetic.', w['user'].id, original_version=original)
    context = None
    if missing != 'audio':
        add(ClinicAudioRevision(dictation_id=item.id, content_hash='d' * 64))
    if missing != 'context':
        context = add(ClinicContextRevision(dictation_id=item.id, case_ref='SYN'))
    lease.acquire(item.id, w['user'].id, datetime.now(timezone.utc))
    db.session.commit()
    w.update(item=item, dictation=dic, head=head)
    payload = dict(text_revision_id=head.id, context_revision_id=context.id if context else str(uuid4()),
                   acceptance_rule_id=w['rules'][True].rule_id)
    result = _command(w, 'request_acceptance', payload, 1)
    assert (result.status_code, result.json['code']) == (409, 'REVISION_CONFLICT')
    assert w['dictation'].version_counter == 0


def test_submit_rejects_old_context_even_when_it_belongs_to_dictation(world):
    w = world
    db.session.add(ClinicContextRevision(dictation_id=w['item'].id,
                                         parent_revision_id=w['context'].id, case_ref='NEW'))
    db.session.commit()
    result = _command(w, 'request_acceptance', _submit(w), 1)
    assert result.json['code'] == 'REVISION_CONFLICT'
    assert w['dictation'].version_counter == 0


@pytest.mark.parametrize('grant', [False, None, 1])
def test_submit_permission_requires_explicit_true(world, grant):
    world['state']['grant'] = grant
    result = _command(world, 'request_acceptance', _submit(world), 1)
    assert result.json['code'] == 'POLICY_DENIED'
    assert world['dictation'].version_counter == 0


def test_lease_identity_visible_only_to_current_individual_holder(lease_world):
    w = lease_world
    taken = w['call']('a', 'post').json
    assert w['call']('a', 'get').json['lease_id'] == taken['lease_id']
    for viewer in ('b', 'lead', 'admin'):
        assert 'lease_id' not in w['call'](viewer, 'get').json
    moved = w['call']('lead', 'post', '/takeover', {'confirm': True}).json
    assert 'lease_id' not in w['call']('a', 'get').json
    assert w['call']('lead', 'get').json['lease_id'] == moved['lease_id']
    row = lease.current(w['did'])
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.session.commit()
    assert 'lease_id' not in w['call']('lead', 'get').json


def test_release_rejects_wrong_lease_identity(world):
    body = _finalizable(world) | {'lease_id': str(uuid4())}
    result = _post(world, 'releases', body)
    assert (result.status_code, result.json['code']) == (409, 'STALE_VERSION')
    assert db.session.query(ClinicRelease).count() == 0
    assert db.session.query(ProcessingJob).count() == 0


@pytest.mark.parametrize('field', ['audio_revision_id', 'context_revision_id'])
def test_finalize_missing_binding_fails_closed_before_trust_gate(case, field):
    *_, state, run = case
    state['trusted'] = False
    with pytest.raises(workflow.WorkflowError, match='^stale_revision$'):
        run(**{field: None})
    assert db.session.query(ClinicRelease).count() == 0
    assert db.session.query(ProcessingJob).count() == 0


def test_finalize_existing_untrusted_context_has_distinct_refusal(case):
    *_, state, run = case
    state['trusted'] = False
    with pytest.raises(workflow.WorkflowError, match='^untrusted_context$'):
        run()
    assert db.session.query(ClinicRelease).count() == 0
    assert db.session.query(ProcessingJob).count() == 0
