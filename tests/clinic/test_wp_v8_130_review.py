"""Independent v8-130 behavior; pinned revision contracts remain unchanged."""

import uuid

import pytest

from test_wp_v8_detail_api import api, _clinic_mode, _scene, DENIED
from test_wp_workflow_finalize import case, _jobs, _releases
from src.app import db
from src.clinic import audio_revisions, revisions, workflow
from src.clinic.models import ClinicDictation, ClinicOriginal


def review_scene():
    ids = _scene()
    row = db.session.get(ClinicDictation, ids['dictation'])
    original = ClinicOriginal(dictation_id=row.id, content_hash='0' * 64, size_bytes=1,
                              storage_key='synthetic/review', receipt='{}', origin_ref='synthetic')
    db.session.add(original)
    db.session.flush()
    head = revisions.add_human_revision(row, None, 'Synthetischer Text', ids['author'], original_version=original)
    audio = audio_revisions.init_audio_revision(row, original, 1000)
    db.session.commit()
    return ids, head.id, audio.id


def test_review_shape_and_no_content_leak(api):
    login_as, allowed, _ = api
    ids, text, audio = review_scene()
    allowed.add(ids['typist'])
    response = login_as(ids['typist']).get(f'/api/clinic/v1/dictations/{ids["dictation"]}/review')
    assert response.status_code == 200
    assert response.headers['Cache-Control'] == 'no-store'
    assert response.json['binding']['text_revision_id'] == text
    assert response.json['binding']['audio_revision_id'] == audio
    assert response.json['binding']['context_revision_id']
    assert response.json['binding']['acceptance_rule_id'] is None
    assert response.json['status'] == workflow.TO_WRITE
    assert response.json['revision'] == 3
    assert 'Synthetischer Text' not in response.text
    assert 'P-SYN-SECRET' not in response.text


@pytest.mark.parametrize('status', [workflow.DRAFT, workflow.TO_ARCHIVE])
def test_review_unavailable_states(api, status):
    login_as, allowed, _ = api
    ids, _, _ = review_scene()
    db.session.get(workflow.ClinicWorkflowDictation, ids['dictation']).status = status
    db.session.commit()
    allowed.add(ids['typist'])
    assert login_as(ids['typist']).get(f'/api/clinic/v1/dictations/{ids["dictation"]}/review').status_code == 404


def test_review_no_head_hidden_unknown_and_anonymous(api):
    login_as, allowed, _ = api
    ids = _scene()
    client = login_as(ids['typist'])
    path = f'/api/clinic/v1/dictations/{ids["dictation"]}/review'
    assert client.get(path).status_code == 404
    allowed.add(ids['typist'])
    assert client.get(path).status_code == 404
    assert client.get(f'/api/clinic/v1/dictations/{uuid.uuid4()}/review').status_code == 404
    assert client.application.test_client().get(path).status_code == 401


def test_send_requires_author_draft_and_edit_policy(api):
    login_as, allowed, _ = api
    ids = _scene()
    row = db.session.get(workflow.ClinicWorkflowDictation, ids['dictation'])
    row.status = workflow.DRAFT
    db.session.commit()
    allowed.update([ids['author'], ids['typist']])
    path = f'/api/clinic/v1/dictations/{ids["dictation"]}/detail'
    assert login_as(ids['author']).get(path).json['transitions']['send'] == workflow.PREP_ONLINE
    assert 'send' not in login_as(ids['typist']).get(path).json['allowed_actions']
    DENIED.add('clinic.dictate')
    assert 'send' not in login_as(ids['author']).get(path).json['allowed_actions']


@pytest.mark.parametrize('absent,code', [(True, 'stale_revision'), (False, 'untrusted_context')])
def test_absent_and_untrusted_context_remain_distinct_refusals(case, absent, code):
    item, dictation, _, _, _, state, run = case
    state['trusted'] = False
    with pytest.raises(workflow.WorkflowError, match=code):
        run(**({'context_revision_id': str(uuid.uuid4())} if absent else {}))
    assert item.status == workflow.TO_WRITE
    assert dictation.version_counter == 0
    assert (_releases(), _jobs()) == (0, 0)
