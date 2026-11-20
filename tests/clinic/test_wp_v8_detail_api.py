"""HTTP evidence for the dictation detail header endpoint (v8-21, V8 P03).

Synthetic data only. The header carries names of staff, the Fall-ID and the
status axes, never a title, notes or patient value (W16).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from datetime import datetime, timezone

import pytest
from flask import Flask, jsonify
from flask_login import LoginManager, login_user

from src.app import app as main_app, db
from src.clinic import revisions, routes_dictation_detail, workflow, worklist_entries
from src.clinic.identity import create_tables as _create_identity_tables
from src.clinic.models import ClinicContextRevision, ClinicDictation, ClinicOriginal
from src.clinic.pools import ClinicAuthorPool, ClinicPoolRoute, ClinicStaffPool
from src.models import AuditEvent, Group, Recording, User
from src.services import audit

ROOT = Path(__file__).resolve().parents[2]
FALL_ID = 'F-SYN-0042'
PATIENT_MARKER = 'P-SYN-SECRET'
TITLE_MARKER = 'TITLE-SYN-SECRET'
DENIED = set()


@pytest.fixture(autouse=True)
def _clinic_mode(monkeypatch):
    monkeypatch.setenv('CLINIC_MODE', '1')
    monkeypatch.setitem(main_app.config, 'TESTING', True)
    with main_app.app_context():
        _create_identity_tables()
    audit.flush()
    audit.reset_memory()
    DENIED.clear()
    yield
    audit.flush()
    audit.reset_memory()


def _user(name, display=None):
    tag = uuid.uuid4().hex[:8]
    user = User(username=f'v821_{name}_{tag}', email=f'v821_{name}_{tag}@example.invalid',
                password='x', name=display)
    db.session.add(user)
    db.session.flush()
    return user


def _scene(*, meeting_date=None, assignee=None, with_context=True, released=False):
    author = _user('author', 'Dr. Synthetisch')
    typist = _user('typist')
    group = Group(name=f'Schreibkraefte-{uuid.uuid4().hex[:6]}', created_by=typist.id)
    db.session.add(group)
    db.session.flush()
    author_pool = ClinicAuthorPool(name=f'Pool-{uuid.uuid4().hex[:6]}', created_by=author.id)
    staff_pool = ClinicStaffPool(group_id=group.id, created_by=author.id)
    db.session.add_all([author_pool, staff_pool])
    db.session.flush()
    db.session.add(ClinicPoolRoute(author_pool_id=author_pool.id, staff_pool_id=staff_pool.id, created_by=author.id))
    rec = Recording(user_id=author.id, title=TITLE_MARKER, status='COMPLETED', original_filename='x.wav',
                    meeting_date=meeting_date, notes=TITLE_MARKER)
    db.session.add(rec)
    db.session.flush()
    dictation = ClinicDictation(recording_id=rec.id, author_user_id=author.id, pool_id=author_pool.id,
                                version_counter=3)
    db.session.add(dictation)
    db.session.flush()
    db.session.add(workflow.ClinicWorkflowDictation(
        id=dictation.id, author_user_id=author.id, pool_id=author_pool.id,
        assignee_user_id=None if assignee is None else typist.id, status=workflow.TO_WRITE, audio_seconds=75))
    if with_context:
        db.session.add(ClinicContextRevision(dictation_id=dictation.id, case_ref=FALL_ID, patient_ref=PATIENT_MARKER))
    db.session.commit()
    return dict(dictation=dictation.id, author=author.id, typist=typist.id,
                author_pool=author_pool.id, staff_pool=staff_pool.id, group_name=group.name,
                pool_name=author_pool.name)


@pytest.fixture
def api():
    with main_app.app_context():
        flask = Flask(__name__)
        flask.config.update(TESTING=True, SECRET_KEY='synthetic-test-key',
                            SQLALCHEMY_DATABASE_URI=main_app.config['SQLALCHEMY_DATABASE_URI'])
        db.init_app(flask)
        login = LoginManager(flask)
        allowed = set()
        seen = []

        @login.user_loader
        def load(uid):
            return db.session.get(User, int(uid))

        @flask.get('/as/<int:uid>')
        def as_user(uid):
            login_user(db.session.get(User, uid))
            return jsonify(ok=True)

        def authorize(user, action, dictation):
            seen.append(action)
            return user.id in allowed and action not in DENIED

        flask.register_blueprint(routes_dictation_detail.create_blueprint(
            authorize=authorize, dictation_snapshot=worklist_entries.dictation_snapshot))

        def login_as(uid):
            client = flask.test_client()
            client.get(f'/as/{uid}')
            return client

        yield login_as, allowed, seen


URL = '/api/clinic/v1/dictations/{}/detail'


def test_header_carries_identities_time_fall_id_and_separate_axes(api):
    login_as, allowed, seen = api
    stamp = datetime(2026, 11, 2, 8, 30, tzinfo=timezone.utc)
    ids = _scene(meeting_date=stamp, assignee=True)
    allowed.add(ids['typist'])
    body = login_as(ids['typist']).get(URL.format(ids['dictation']))
    assert body.status_code == 200 and body.headers['Cache-Control'] == 'no-store'
    data = body.json
    assert data['author'] == dict(user_id=ids['author'], display_name='Dr. Synthetisch')
    assert data['author_pool'] == dict(id=ids['author_pool'], name=ids['pool_name'])
    assert data['staff_pools'] == [dict(id=ids['staff_pool'], name=ids['group_name'])]
    assert data['takeover']['user_id'] == ids['typist'] and data['takeover']['display_name']
    assert data['recording_time'] == dict(at='2026-11-02T08:30:00Z', source='recorded')
    assert data['case_display'] == FALL_ID and data['duration_seconds'] == 75
    assert set(data['status_axes']) == {'processing', 'workflow', 'acceptance', 'output'}
    assert data['status_axes'] == dict(processing='SKIPPED', workflow=workflow.TO_WRITE,
                                       acceptance='QUEUED', output='NOT_RELEASED')
    assert data['etag'] == f'"{ids["dictation"]}:3"' and data['revision'] == 3
    assert seen[0] == 'history'


def _with_status(api, status, *, acceptance_required):
    login_as, allowed, _ = api
    ids = _scene(meeting_date=None)
    row = db.session.get(workflow.ClinicWorkflowDictation, ids['dictation'])
    row.status, row.acceptance_required = status, acceptance_required
    db.session.commit()
    allowed.add(ids['typist'])
    return login_as(ids['typist']).get(URL.format(ids['dictation'])).json


def test_workflow_capabilities_follow_status_and_policy(api):
    data = _with_status(api, workflow.TO_CORRECT, acceptance_required=True)
    assert data['allowed_actions'] == ['submit', 'archive']
    assert data['transitions'] == dict(submit=workflow.TO_ACCEPT, archive=workflow.TO_ARCHIVE)
    assert set(data['binding']) == {'textRevisionId', 'contextRevisionId'}
    data = _with_status(api, workflow.TO_ACCEPT, acceptance_required=True)
    assert data['allowed_actions'] == ['accept', 'reject', 'archive']
    assert data['transitions']['accept'] == workflow.ACCEPTED
    assert data['transitions']['reject'] == workflow.TO_CORRECT
    data = _with_status(api, workflow.DONE, acceptance_required=False)
    assert data['allowed_actions'] == ['reopen', 'archive']
    assert data['transitions']['reopen'] == workflow.TO_CORRECT
    data = _with_status(api, workflow.ACCEPTED, acceptance_required=True)
    assert data['allowed_actions'] == ['release', 'archive']
    assert data['transitions']['release'] == workflow.DONE
    assert data['binding']['textRevisionId'] is None and 'release' not in data['binding']


def test_binding_names_the_current_transcript_head(api):
    login_as, allowed, _ = api
    ids = _scene()
    row = db.session.get(workflow.ClinicWorkflowDictation, ids['dictation'])
    row.status, row.acceptance_required = workflow.ACCEPTED, True
    dictation = db.session.get(ClinicDictation, ids['dictation'])
    original = ClinicOriginal(dictation_id=dictation.id, content_hash='0' * 64, size_bytes=1,
                              storage_key='synthetic/original', receipt='{}', origin_ref='synthetic')
    db.session.add(original)
    db.session.flush()
    head = revisions.add_human_revision(dictation, None, 'Synthetischer Text', ids['author'],
                                        original_version=original)
    db.session.commit()
    allowed.add(ids['typist'])
    data = login_as(ids['typist']).get(URL.format(ids['dictation'])).json
    assert data['binding']['textRevisionId'] == head.id
    assert data['binding']['release']['transcript_revision_id'] == head.id
    assert data['binding']['release']['text_sha256'] == head.normalised_text_hash
    assert data['binding']['release']['mode'] == 'PLATFORM_REVIEWED_KIS_DRAFT'
    assert data['binding']['release']['original_version_id'] == original.id
    assert not [k for k in data['binding']['release'] if 'lease' in k]


def test_workflow_actions_are_withheld_when_the_policy_denies_them(api):
    DENIED.update({'clinic.review', 'clinic.accept', 'clinic.reopen', 'clinic.release'})
    data = _with_status(api, workflow.TO_ACCEPT, acceptance_required=True)
    assert data['allowed_actions'] == ['archive']
    assert data['transitions'] == dict(archive=workflow.TO_ARCHIVE)
    DENIED.add('clinic.archive')
    assert _with_status(api, workflow.TO_ACCEPT, acceptance_required=True)['allowed_actions'] == []


def test_missing_recording_time_is_marked_as_fallback(api):
    login_as, allowed, _ = api
    ids = _scene(meeting_date=None, with_context=False)
    allowed.add(ids['typist'])
    data = login_as(ids['typist']).get(URL.format(ids['dictation'])).json
    assert data['recording_time']['source'] == 'fallback' and data['recording_time']['at']
    assert data['case_display'] is None and data['takeover'] is None


def test_no_title_notes_or_patient_value_in_the_response(api):
    login_as, allowed, _ = api
    ids = _scene()
    allowed.add(ids['typist'])
    text = login_as(ids['typist']).get(URL.format(ids['dictation'])).get_data(as_text=True)
    assert TITLE_MARKER not in text and PATIENT_MARKER not in text
    assert not [k for k in json.loads(text) if 'patient' in k or k in ('title', 'notes')]


def test_foreign_and_unknown_dictations_look_the_same(api):
    login_as, allowed, _ = api
    ids = _scene()
    denied = login_as(ids['typist']).get(URL.format(ids['dictation']))
    unknown = login_as(ids['typist']).get(URL.format(uuid.uuid4()))
    assert denied.status_code == unknown.status_code == 404
    assert denied.json['code'] == unknown.json['code'] == 'NOT_FOUND'
    assert denied.json['title'] == unknown.json['title']


def test_anonymous_gets_401_and_clinic_mode_off_gets_404(api, monkeypatch):
    login_as, allowed, _ = api
    ids = _scene()
    allowed.add(ids['typist'])
    anonymous = login_as(ids['typist']).application.test_client()
    assert anonymous.get(URL.format(ids['dictation'])).status_code == 401
    monkeypatch.delenv('CLINIC_MODE')
    assert login_as(ids['typist']).get(URL.format(ids['dictation'])).status_code == 404


def test_read_is_audit_logged(api):
    login_as, allowed, _ = api
    ids = _scene()
    allowed.add(ids['typist'])
    audit.flush()
    baseline = db.session.query(db.func.max(AuditEvent.id)).scalar() or 0
    assert login_as(ids['typist']).get(URL.format(ids['dictation'])).status_code == 200
    audit.flush()
    recording_id = db.session.get(ClinicDictation, ids['dictation']).recording_id
    rows = AuditEvent.query.filter_by(event='recording.content.read', actor_user_id=ids['typist'],
                                      recording_id=recording_id).filter(AuditEvent.id > baseline).all()
    assert [row.details for row in rows if row.details == {'part': 'detail'}] == [{'part': 'detail'}]


def test_endpoint_is_mounted_and_classified_in_clinic_mode(tmp_path):
    """Real app in a child process with CLINIC_MODE on: the rule exists and the route policy classifies it."""
    code = (
        'from src.app import app\n'
        'from src.clinic.route_policy import ROUTE_POLICY\n'
        'name = "clinic_dictation_detail.get_detail"\n'
        'print("MOUNTED", name in {r.endpoint for r in app.url_map.iter_rules()}, name in ROUTE_POLICY)\n'
    )
    env = {**os.environ, 'CLINIC_MODE': '1', 'SQLALCHEMY_DATABASE_URI': f'sqlite:///{tmp_path / "m.db"}',
           'UPLOAD_FOLDER': str(tmp_path / 'uploads')}
    done = subprocess.run([sys.executable, '-c', code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert 'MOUNTED True True' in done.stdout, done.stdout[-500:] + done.stderr[-1500:]
