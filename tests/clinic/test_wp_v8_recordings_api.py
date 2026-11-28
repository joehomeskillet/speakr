"""V8 P01 recordings list: GET /api/clinic/v1/recordings and the shared role projection (v8-01).

Synthetic rows only. The blueprint is mounted on a small Flask app over the shared
test database with the real worklist loader, so visibility is the worklist's own.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from flask import Flask
from flask_login import LoginManager, login_user
from sqlalchemy import func, select

from src.app import app, db
from src.clinic import dictation_roles, policy, recording_type, recordings_list, routes_worklist, workflow, worklist_entries
from src.clinic.acceptance_policy import SCOPE_DICTATION, AcceptanceLedger
from src.clinic.archive_policy import ClinicArchiveEntry
from src.clinic.identity import ClinicAccountPolicy, create_tables as _create_identity_tables
from src.clinic.models import (
    ClinicContextRevision, ClinicDictation, ClinicOriginal, ClinicReviewLease, ClinicTranscriptRevision,
)
from src.clinic.pools import ClinicAuthorPool, ClinicPoolRoute, ClinicStaffPool
from src.clinic.routes_recordings import PREFIX, create_blueprint
from src.clinic.worklist_layout import LayoutStore
from src.models import AuditEvent, Group, GroupMembership, Recording, User
from src.services import audit

ROOT = Path(__file__).resolve().parents[2]
ITEM_KEYS = {"dictation_id", "recording_id", "recording_type", "recorded_at", "time_source", "sort_time", "day", "dictated_by",
             "staff_pools", "lease_holder", "processing_status", "workflow_status", "duration_ms",
             "case_display", "archived_at", "own_title"}


@pytest.fixture(autouse=True)
def _clinic_mode(monkeypatch):
    monkeypatch.setenv("CLINIC_MODE", "1")
    with app.app_context():
        _create_identity_tables()
    audit.flush()
    audit.reset_memory()
    yield
    audit.flush()
    audit.reset_memory()


def _user(name, *, is_admin=False, status=None, full_name=None):
    tag = uuid.uuid4().hex[:8]
    user = User(username=f"v801_{name}_{tag}"[:20], email=f"v801_{name}_{tag}@example.invalid",
                password="x", is_admin=is_admin, name=full_name)
    db.session.add(user)
    db.session.flush()
    if status is not None:
        db.session.add(ClinicAccountPolicy(user_id=user.id, status=status, login_policy="device_only"))
    db.session.commit()
    return user


def _dictation(author, *, pool_id, meeting=None, created=None, status=workflow.TO_WRITE, assignee=None,
               seconds=12, rec_status="COMPLETED", title="PATIENT-TITLE-SECRET", notes=None, kind=None):
    rec = Recording(user_id=author.id, title=title, status=rec_status, notes=notes,
                    original_filename="x.wav", meeting_date=meeting)
    db.session.add(rec)
    db.session.flush()
    kwargs = {} if created is None else {"created_at": created}
    dictation = ClinicDictation(recording_id=rec.id, author_user_id=author.id, pool_id=pool_id,
                                configured_recording_type=kind, **kwargs)
    db.session.add(dictation)
    db.session.flush()
    db.session.add(workflow.ClinicWorkflowDictation(
        id=dictation.id, author_user_id=author.id, pool_id=pool_id, status=status, audio_seconds=seconds,
        assignee_user_id=None if assignee is None else assignee.id))
    db.session.commit()
    return dictation


@pytest.fixture
def scene():
    """Two authors in two author pools; pool A routes to one staff pool; plain ids only."""
    with app.app_context():
        admin = _user("admin", is_admin=True, status="active")
        author_a = _user("author_a", full_name="Dr. Anna Autorin")
        author_b = _user("author_b")
        typist = _user("typist")
        pool_a = ClinicAuthorPool(name=f"v801 A {uuid.uuid4().hex[:6]}", created_by=admin.id)
        pool_b = ClinicAuthorPool(name=f"v801 B {uuid.uuid4().hex[:6]}", created_by=admin.id)
        group = Group(name=f"v801 staff {uuid.uuid4().hex[:6]}", created_by=admin.id)
        db.session.add_all([pool_a, pool_b, group])
        db.session.flush()
        staff = ClinicStaffPool(group_id=group.id, created_by=admin.id)
        db.session.add(staff)
        db.session.flush()
        db.session.add(ClinicPoolRoute(author_pool_id=pool_a.id, staff_pool_id=staff.id, created_by=admin.id))
        db.session.add(GroupMembership(group_id=group.id, user_id=typist.id, role="member"))
        db.session.commit()
        flask = Flask(__name__)
        flask.config.update(TESTING=True, SECRET_KEY="synthetic-v801",
                            SQLALCHEMY_DATABASE_URI=app.config["SQLALCHEMY_DATABASE_URI"],
                            SQLALCHEMY_ENGINE_OPTIONS=app.config.get("SQLALCHEMY_ENGINE_OPTIONS", {}))
        db.init_app(flask)
        policy.install_pool_policy(flask)  # as at startup: live pool decisions
        login = LoginManager(flask)
        login.user_loader(lambda uid: db.session.get(User, int(uid)))
        flask.register_blueprint(create_blueprint(worklist_entries.load_entries))

        @flask.get("/synthetic-login/<int:uid>")
        def browser(uid):
            login_user(db.session.get(User, uid))
            return {}

        return SimpleNamespace(
            flask=flask, admin=admin.id, a=author_a.id, b=author_b.id, typist=typist.id,
            pool_a=pool_a.id, pool_b=pool_b.id, staff=staff.id, staff_name=group.name)


def client_for(scene, uid):
    client = scene.flask.test_client()
    assert client.get(f"/synthetic-login/{uid}").status_code == 200
    return client


def get(client, query=""):
    return client.get(PREFIX + query)


def test_unauthenticated_is_401_and_contract_shape(scene):
    assert scene.flask.test_client().get(PREFIX).status_code == 401
    with app.app_context():
        author = db.session.get(User, scene.a)
        item = _dictation(author, pool_id=scene.pool_a, meeting=datetime(2026, 7, 1, 8, 30), seconds=42,
                          title="Eigener Titel")
        db.session.add(ClinicContextRevision(dictation_id=item.id, case_ref="FALL-77", patient_ref="PAT-SECRET"))
        db.session.commit()
        dictation_id, recording_id = item.id, item.recording_id
    response = get(client_for(scene, scene.a))
    assert response.status_code == 200 and response.headers["Cache-Control"] == "private, no-store"
    body = response.json
    assert set(body) == {"items", "next_cursor"} and body["next_cursor"] is None
    [row] = body["items"]
    assert set(row) == ITEM_KEYS
    assert row["dictation_id"] == dictation_id and row["recording_id"] == recording_id
    assert row["recording_type"] == recording_type.REPORT
    assert row["recorded_at"] == "2026-07-01T08:30:00Z" == row["sort_time"]
    assert row["time_source"] == "recorded" and row["day"] == "2026-07-01"
    assert row["dictated_by"] == {"id": str(scene.a), "display_name": "Dr. Anna Autorin"}
    assert row["staff_pools"] == [{"id": scene.staff, "name": scene.staff_name}]
    assert row["lease_holder"] is None and row["case_display"] == "FALL-77"
    assert row["processing_status"] == "COMPLETED" and row["workflow_status"] == workflow.TO_WRITE
    assert row["duration_ms"] == 42000
    assert row["archived_at"] is None and row["own_title"] == "Eigener Titel"
    text = json.dumps(body)
    assert "PATIENT-TITLE-SECRET" not in text and "PAT-SECRET" not in text and "patient" not in text.lower()


def test_pending_first_is_owned_filter_independent_and_pageable(scene):
    with app.app_context():
        author = db.session.get(User, scene.a)
        other = db.session.get(User, scene.b)
        normal = _dictation(author, pool_id=scene.pool_a, status=workflow.TO_WRITE,
                            meeting=datetime(2026, 11, 20)).id
        unsent = _dictation(author, pool_id=scene.pool_a, status=workflow.DRAFT,
                            meeting=datetime(2026, 1, 1)).id
        unconfirmed = _dictation(author, pool_id=scene.pool_a, status=workflow.PREP_OFFLINE,
                                 meeting=datetime(2026, 1, 2)).id
        confirmed = _dictation(author, pool_id=scene.pool_a, status=workflow.PREP_OFFLINE,
                               meeting=datetime(2026, 11, 19)).id
        workflow.store_routing_target(db.session(), confirmed, actor_id=scene.a)
        foreign = _dictation(other, pool_id=scene.pool_b, status=workflow.DRAFT,
                             meeting=datetime(2026, 11, 21)).id
    client = client_for(scene, scene.a)
    assert get(client, "?limit=1").json["items"][0]["dictation_id"] == normal
    query = "?pending_first=1&limit=1&from=2026-11-01"
    first = get(client, query).json
    assert (first["pending_count"], first["unsent_count"]) == (2, 1)
    assert [(r["dictation_id"], r["send_attention"]) for r in first["items"]] == [(unconfirmed, "unconfirmed")]
    second = get(client, f"{query}&cursor={first['next_cursor']}").json
    assert [(r["dictation_id"], r["send_attention"]) for r in second["items"]] == [(unsent, "unsent")]
    third = get(client, f"{query}&cursor={second['next_cursor']}").json
    assert third["items"][0]["dictation_id"] == normal
    fourth = get(client, f"{query}&cursor={third['next_cursor']}").json
    assert fourth["items"][0]["dictation_id"] == confirmed
    assert "send_attention" not in fourth["items"][0] and fourth["next_cursor"] is None
    admin = get(client_for(scene, scene.admin), "?pending_first=1").json
    assert admin["pending_count"] == 0
    assert any(row["dictation_id"] == foreign and "send_attention" not in row for row in admin["items"])


def test_recording_type_filters_before_pending_and_paging(scene):
    with app.app_context():
        author = db.session.get(User, scene.a)
        report = _dictation(author, pool_id=scene.pool_a, status=workflow.DRAFT,
                            meeting=datetime(2026, 1, 1))
        assert report.configured_recording_type is None
        report_id = report.id
        sent_report = _dictation(author, pool_id=scene.pool_a, kind=recording_type.REPORT,
                                 meeting=datetime(2026, 11, 10)).id
        private_ids = {
            kind: _dictation(author, pool_id=scene.pool_a, kind=kind, status=status,
                             meeting=datetime(2026, 11, day)).id
            for day, (kind, status) in enumerate([
                ("notiz", workflow.DRAFT), ("sitzung", workflow.PREP_OFFLINE),
                ("patientengespraech", workflow.DRAFT),
            ], start=11)
        }
        unknown = _dictation(author, pool_id=scene.pool_a, kind="future-type", status=workflow.DRAFT,
                             meeting=datetime(2026, 1, 2)).id
    client = client_for(scene, scene.a)
    assert {row["dictation_id"]: row["recording_type"] for row in get(client).json["items"]} == {
        report_id: recording_type.REPORT, sent_report: recording_type.REPORT, unknown: "future-type",
        **{did: kind for kind, did in private_ids.items()},
    }
    query = "?pending_first=1&limit=1&from=2026-11-01"
    unfiltered = get(client, query).json
    assert (unfiltered["pending_count"], unfiltered["unsent_count"]) == (1, 1)
    assert unfiltered["items"][0]["dictation_id"] == report_id
    for kind, did in private_ids.items():
        body = get(client, f"{query}&recording_type={kind}").json
        [row] = body["items"]
        assert (row["dictation_id"], row["recording_type"]) == (did, kind)
        assert "send_attention" not in row and body["next_cursor"] is None
        assert (body["pending_count"], body["unsent_count"]) == (0, 0)
    query += f"&recording_type={recording_type.REPORT}"
    first = get(client, query).json
    [row] = first["items"]
    assert row["dictation_id"] == report_id and row["send_attention"] == "unsent"
    assert first["next_cursor"].startswith("p.")
    second = get(client, f"{query}&cursor={first['next_cursor']}").json
    assert [row["dictation_id"] for row in second["items"]] == [sent_report]
    assert (second["pending_count"], second["unsent_count"]) == (1, 1)
    assert second["next_cursor"] is None


def test_private_recordings_are_hidden_even_from_admin_and_assignee(scene):
    with app.app_context():
        author = db.session.get(User, scene.a)
        admin = db.session.get(User, scene.admin)
        assignee = db.session.get(User, scene.typist)
        report = _dictation(author, pool_id=scene.pool_a, assignee=assignee).id
        for kind in ("notiz", "sitzung", "patientengespraech"):
            _dictation(author, pool_id=scene.pool_a, assignee=assignee, kind=kind)
        archived = _dictation(author, pool_id=scene.pool_a, kind="notiz", status=workflow.TO_ARCHIVE).id
        admin_own = _dictation(admin, pool_id=scene.pool_a, kind="notiz").id
    query = f"?author_id={scene.a}&limit=1&pending_first=1"
    for viewer in (scene.admin, scene.typist):
        client = client_for(scene, viewer)
        body = get(client, query).json
        assert [row["dictation_id"] for row in body["items"]] == [report]
        assert body["next_cursor"] is None
        assert (body["pending_count"], body["unsent_count"]) == (0, 0)
        for kind in ("notiz", "sitzung", "patientengespraech"):
            assert get(client, f"{query}&recording_type={kind}").json["items"] == []
    archives = f"?status={workflow.TO_ARCHIVE}&recording_type=notiz"
    [row] = get(client_for(scene, scene.a), archives).json["items"]
    assert row["dictation_id"] == archived and row["recording_type"] == "notiz"
    admin_client = client_for(scene, scene.admin)
    assert get(admin_client, archives + f"&author_id={scene.a}").json["items"] == []
    [row] = get(admin_client, f"?recording_type=notiz&author_id={scene.admin}").json["items"]
    assert row["dictation_id"] == admin_own


@pytest.mark.parametrize("kind", ["", "future-type", "BERICHT"])
def test_invalid_recording_type_filter_returns_problem(scene, kind):
    response = get(client_for(scene, scene.a), f"?recording_type={kind}&pending_first=1&limit=1")
    assert response.status_code == 400
    assert response.mimetype == "application/problem+json"
    assert response.json["code"] == "INVALID_PAYLOAD"


def test_assignee_and_asr_confirmed_text_are_optional_list_fields(scene):
    with app.app_context():
        author = db.session.get(User, scene.a)
        typist = db.session.get(User, scene.typist)
        typist.name = "Synthetic Typist"
        confirmed = _dictation(author, pool_id=scene.pool_a, assignee=typist)
        legacy = _dictation(author, pool_id=scene.pool_a)
        original = ClinicOriginal(dictation_id=confirmed.id, content_hash="a" * 64, size_bytes=1,
                                  storage_key="synthetic", receipt="{}", origin_ref="synthetic")
        db.session.add(original)
        db.session.flush()
        db.session.add(ClinicTranscriptRevision(
            dictation_id=confirmed.id, kind="ASR", body="Synthetic transcript",
            normalised_text_hash="b" * 64, original_version_id=original.id, model_ref="local-test",
        ))
        db.session.commit()
        confirmed_id, legacy_id = confirmed.id, legacy.id

    response = get(client_for(scene, scene.a))
    assert response.status_code == 200
    rows = {row["dictation_id"]: row for row in response.json["items"]}
    assert rows[confirmed_id]["processing_status"] == rows[legacy_id]["processing_status"] == "COMPLETED"
    assert rows[confirmed_id]["assignee"] == {"id": str(scene.typist), "display_name": "Synthetic Typist"}
    assert rows[confirmed_id]["text_created"] is True
    assert "assignee" not in rows[legacy_id]
    assert "text_created" not in rows[legacy_id]


def test_review_turn_requires_current_acceptance_rule_and_release_grant(scene):
    from src.clinic.pools import allow

    with app.app_context():
        author = db.session.get(User, scene.a)
        waiting = _dictation(author, pool_id=scene.pool_a, status=workflow.TO_ACCEPT)
        other = _dictation(author, pool_id=scene.pool_a, status=workflow.TO_ACCEPT)
        waiting_id = waiting.id
        now = datetime.now(timezone.utc)
        ledger = AcceptanceLedger()
        ledger.publish(SCOPE_DICTATION, waiting.id, required=True, approver_user_ids=(scene.a,), now=now)
        ledger.publish(SCOPE_DICTATION, other.id, required=True, approver_user_ids=(scene.typist,), now=now)
    client = client_for(scene, scene.a)
    assert all('viewer_review_turn' not in row for row in get(client).json['items'])
    with app.app_context():
        allow(scene.a, 'own', actor_id=scene.admin, actions=('release',))
    rows = get(client).json['items']
    assert len(rows) == 2
    assert [row['dictation_id'] for row in rows if row.get('viewer_review_turn')] == [waiting_id]


def test_title_is_projected_only_on_own_rows_and_notes_never(scene):
    with app.app_context():
        own = _dictation(db.session.get(User, scene.a), pool_id=scene.pool_a, meeting=datetime(2026, 6, 1, 9),
                         title="Eigener Titel", notes="NOTE-SECRET")
        db.session.add(ClinicContextRevision(dictation_id=own.id, case_ref="FALL-5"))
        db.session.commit()
        own_id = own.id
        flow = db.session.get(workflow.ClinicWorkflowDictation, own_id)
        flow.assignee_user_id = scene.typist
        db.session.commit()
    seen = {uid: get(client_for(scene, uid)) for uid in (scene.a, scene.typist, scene.admin, scene.b)}
    [mine] = seen[scene.a].json["items"]
    assert mine["own_title"] == "Eigener Titel" and mine["case_display"] == "FALL-5"
    for uid in (scene.typist, scene.admin):  # granted, but not the author
        [row] = [r for r in seen[uid].json["items"] if r["dictation_id"] == own_id]
        assert row["own_title"] is None and row["case_display"] == "FALL-5"
    assert seen[scene.b].json["items"] == []  # ungranted: no row, so no title and no note
    for response in seen.values():
        assert "NOTE-SECRET" not in json.dumps(response.json)
    for uid in (scene.typist, scene.admin, scene.b):
        assert "Eigener Titel" not in json.dumps(seen[uid].json)


def test_foreign_dictation_is_never_listed_for_an_unauthorised_user(scene):
    with app.app_context():
        own_a = _dictation(db.session.get(User, scene.a), pool_id=scene.pool_a, meeting=datetime(2026, 5, 1, 9))
        own_b = _dictation(db.session.get(User, scene.b), pool_id=scene.pool_b, meeting=datetime(2026, 5, 2, 9))
        ids = (own_a.id, own_b.id)
    seen = {uid: {row["dictation_id"] for row in get(client_for(scene, uid)).json["items"]}
            for uid in (scene.a, scene.b, scene.typist)}
    assert seen[scene.a] == {ids[0]} and seen[scene.b] == {ids[1]}
    assert seen[scene.typist] == set()  # in the staff pool, but nothing assigned and not a lead
    with app.app_context():
        flow = db.session.get(workflow.ClinicWorkflowDictation, ids[0])
        flow.assignee_user_id = scene.typist
        db.session.commit()
    assert {row["dictation_id"] for row in get(client_for(scene, scene.typist)).json["items"]} == {ids[0]}


@pytest.mark.parametrize("manual, retained, registered, expected", [
    (datetime(2026, 11, 20, 9, 15), None, True, "2026-11-20T09:15:00Z"),
    (None, datetime(2026, 11, 19, 8), False, "2026-11-19T08:00:00Z"),
    (datetime(2026, 11, 20, 9, 15), datetime(2026, 11, 19, 8), True, "2026-11-19T08:00:00Z"),
    (None, None, False, None),
    (None, None, True, None),
])
def test_archive_dates_use_durable_sources_without_widening_visibility(scene, manual, retained, registered, expected):
    with app.app_context():
        author = db.session.get(User, scene.a)
        own = _dictation(author, pool_id=scene.pool_a, meeting=datetime(2026, 7, 1, 8),
                         status=workflow.TO_ARCHIVE, title="Eigener Titel")
        newer = _dictation(author, pool_id=scene.pool_a, meeting=datetime(2026, 7, 2, 8),
                           status=workflow.TO_ARCHIVE)
        foreign = _dictation(db.session.get(User, scene.b), pool_id=scene.pool_b,
                             meeting=datetime(2026, 7, 3, 8), status=workflow.TO_ARCHIVE)
        ids = own.id, newer.id, foreign.id
        db.session.get(Recording, own.recording_id).retention_archived_at = retained
        db.session.get(Recording, foreign.recording_id).retention_archived_at = datetime(2026, 11, 21, 8)
        if registered:
            db.session.add(ClinicArchiveEntry(
                dictation_id=own.id, user_id=scene.a, original_id="private-original",
                audio_revision_id="private-audio", transcript_revision_id="private-text",
                context_revision_id="private-context", release_id="private-release",
                data_class="audio", archived_at=manual))
        db.session.commit()

    client = client_for(scene, scene.a)
    assert get(client).json["items"] == []  # P01 still excludes archived workflow rows.
    query = "?status=zu_archivieren&limit=1"
    first = get(client, query)
    assert first.status_code == 200 and first.headers["Cache-Control"] == "private, no-store"
    # P08 explicitly promises recording-time order, independent of the archive date.
    assert first.json["items"][0]["dictation_id"] == ids[1]
    assert first.json["items"][0]["archived_at"] is None
    second = get(client, query + "&cursor=" + first.json["next_cursor"])
    assert second.status_code == 200 and second.json["next_cursor"] is None
    [row] = second.json["items"]
    assert row["dictation_id"] == ids[0] and row["archived_at"] == expected
    assert row["sort_time"] == "2026-07-01T08:00:00Z" and row["time_source"] == "recorded"
    assert set(row) == ITEM_KEYS
    assert get(client, f"?status=zu_archivieren&author_id={scene.b}").json["items"] == []
    payload = json.dumps(second.json)
    assert "PATIENT-TITLE-SECRET" not in payload and "private-" not in payload and ids[2] not in payload


def test_admin_list_equals_the_worklist_and_foreign_loads_are_audited(scene):
    """Reports still use the worklist loader, row policy and foreign-read audit."""
    with app.app_context():
        own = _dictation(db.session.get(User, scene.admin), pool_id=scene.pool_a, meeting=datetime(2026, 5, 1, 9))
        foreign = _dictation(db.session.get(User, scene.b), pool_id=scene.pool_b, meeting=datetime(2026, 5, 2, 9))
        own_id, foreign_recording = own.id, foreign.recording_id
        baseline = db.session.execute(select(func.max(AuditEvent.id))).scalar_one() or 0
    response = get(client_for(scene, scene.admin), f"?recording_type={recording_type.REPORT}")
    listed = {row["dictation_id"] for row in response.json["items"]}
    assert own_id in listed
    audit.flush()
    with app.app_context():
        snapshot = routes_worklist.get_worklist(
            worklist_entries.load_entries(scene.admin), policy.build_subject(scene.admin), LayoutStore(),
            page_size=100)
        assert listed == {row["dictation_id"] for row in snapshot.page.items
                          if db.session.get(ClinicDictation, row["dictation_id"]).recording_type
                          == recording_type.REPORT}
        rows = AuditEvent.query.filter(AuditEvent.id > baseline, AuditEvent.actor_user_id == scene.admin,
                                       AuditEvent.event == "recording.content.read").all()
        assert foreign_recording in {row.recording_id for row in rows}  # W12 audit of the foreign load


def test_three_pages_with_equal_sort_time_return_every_row_once(scene):
    same = datetime(2026, 6, 10, 7, 0)
    with app.app_context():
        author = db.session.get(User, scene.a)
        created = {_dictation(author, pool_id=scene.pool_a, meeting=same).id for _ in range(7)}
    client = client_for(scene, scene.a)
    pages, cursor = [], None
    while True:
        response = get(client, "?limit=3" + (f"&cursor={cursor}" if cursor else ""))
        assert response.status_code == 200
        pages.append([row["dictation_id"] for row in response.json["items"]])
        cursor = response.json["next_cursor"]
        if cursor is None:
            break
    assert [len(page) for page in pages] == [3, 3, 1]
    flat = [dictation_id for page in pages for dictation_id in page]
    assert len(flat) == len(set(flat)) == 7 and set(flat) == created
    assert flat == sorted(flat, reverse=True)  # tie-break: dictation_id DESC


def test_recording_time_leads_and_created_is_a_marked_fallback(scene):
    now = datetime(2026, 8, 1, 12, tzinfo=timezone.utc)
    with app.app_context():
        author = db.session.get(User, scene.a)
        old_recorded = _dictation(author, pool_id=scene.pool_a, meeting=datetime(2026, 1, 5, 10), created=now)
        no_time = _dictation(author, pool_id=scene.pool_a, meeting=None, created=now - timedelta(days=1))
        ids = (old_recorded.id, no_time.id)
    rows = {row["dictation_id"]: row for row in get(client_for(scene, scene.a)).json["items"]}
    assert rows[ids[0]]["time_source"] == "recorded" and rows[ids[0]]["sort_time"] == "2026-01-05T10:00:00Z"
    assert rows[ids[1]]["time_source"] == "created" and rows[ids[1]]["recorded_at"] is None
    assert rows[ids[1]]["sort_time"] == "2026-07-31T12:00:00Z"
    order = [row["dictation_id"] for row in get(client_for(scene, scene.a)).json["items"]]
    assert order == [ids[1], ids[0]]  # the real recording time sorts, not created_at


@pytest.mark.parametrize("utc, day", [
    (datetime(2026, 7, 1, 23, 30), "2026-07-02"),    # summer, UTC+2
    (datetime(2026, 1, 15, 23, 30), "2026-01-16"),   # winter, UTC+1
    (datetime(2026, 3, 28, 23, 30), "2026-03-29"),   # last night before the spring change, UTC+1
    (datetime(2026, 3, 29, 22, 30), "2026-03-30"),   # after the change, UTC+2
    (datetime(2026, 10, 24, 22, 30), "2026-10-25"),  # before the autumn change, UTC+2
    (datetime(2026, 10, 25, 23, 30), "2026-10-26"),  # after the change, UTC+1
    (datetime(2026, 7, 1, 21, 30), "2026-07-01"),    # 23:30 local still the same day
])
def test_day_is_computed_in_europe_zurich(scene, utc, day):
    assert recordings_list.day_of(utc.replace(tzinfo=timezone.utc)) == day
    with app.app_context():
        _dictation(db.session.get(User, scene.a), pool_id=scene.pool_a, meeting=utc)
    [row] = get(client_for(scene, scene.a)).json["items"]
    assert row["day"] == day and row["sort_time"].endswith("Z")


def test_filters_take_ids_and_days_in_the_local_zone(scene):
    with app.app_context():
        a, b = db.session.get(User, scene.a), db.session.get(User, scene.b)
        late = _dictation(a, pool_id=scene.pool_a, meeting=datetime(2026, 7, 1, 23, 30), status=workflow.DONE)
        early = _dictation(a, pool_id=scene.pool_a, meeting=datetime(2026, 7, 1, 21, 30))
        other = _dictation(b, pool_id=scene.pool_b, meeting=datetime(2026, 7, 1, 21, 45))
        ids = (late.id, early.id, other.id)
        owner_client = client_for(scene, scene.a)

    def listed(query):
        response = get(owner_client, query)
        assert response.status_code == 200, query
        return {row["dictation_id"] for row in response.json["items"]}

    assert listed("?from=2026-07-02&to=2026-07-02") == {ids[0]}  # 23:30 UTC is 01:30 on the 2nd in Zurich
    assert listed("?from=2026-07-01&to=2026-07-01") == {ids[1]}
    assert listed(f"?author_id={scene.b}") == set()  # filters narrow, never widen visibility
    assert listed(f"?author_id={scene.a}") == {ids[0], ids[1]}
    assert listed(f"?pool_id={scene.pool_a}") == {ids[0], ids[1]}
    assert listed(f"?pool_id={scene.staff}") == {ids[0], ids[1]}  # a staff pool matches its routed author pools
    assert listed(f"?status={workflow.DONE}") == {ids[0]}
    for bad in ("?limit=0", "?limit=x", "?limit=201", "?from=2026-13-01", "?to=1.7.2026", "?status=nope",
                "?cursor=%25%25", "?cursor=" + "e30"):
        assert get(owner_client, bad).status_code == 400, bad


def test_role_projection_lease_pools_and_no_pool(scene):
    now = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)
    with app.app_context():
        author = db.session.get(User, scene.a)
        typist = db.session.get(User, scene.typist)
        routed = _dictation(author, pool_id=scene.pool_a, meeting=datetime(2026, 9, 1, 8))
        bare = _dictation(db.session.get(User, scene.b), pool_id=scene.pool_b, meeting=datetime(2026, 9, 1, 8))
        expired = _dictation(author, pool_id=scene.pool_a, meeting=datetime(2026, 9, 1, 8))
        db.session.add_all([
            ClinicReviewLease(dictation_id=routed.id, holder_user_id=typist.id,
                              expires_at=now + timedelta(minutes=5), generation=1),
            ClinicReviewLease(dictation_id=expired.id, holder_user_id=typist.id,
                              expires_at=now - timedelta(minutes=5), generation=1),
        ])
        db.session.commit()
        roles = dictation_roles.project_roles([routed, bare, expired], now=now)
        assert roles[routed.id]["lease_holder"] == {
            "id": str(typist.id), "display_name": typist.username, "until": "2026-09-01T12:05:00Z"}
        assert roles[routed.id]["staff_pools"] == [{"id": scene.staff, "name": scene.staff_name}]
        assert roles[routed.id]["dictated_by"]["display_name"] == "Dr. Anna Autorin"
        assert roles[bare.id]["staff_pools"] == [] and roles[bare.id]["lease_holder"] is None
        assert roles[expired.id]["lease_holder"] is None  # an expired lease is not a holder
        assert set(roles[bare.id]) == {"dictated_by", "staff_pools", "lease_holder"}
        assert dictation_roles.project_roles([]) == {}


def test_clinic_mode_off_answers_404_and_registers_no_route(scene, monkeypatch):
    client = client_for(scene, scene.a)
    monkeypatch.delenv("CLINIC_MODE")
    assert get(client).status_code == 404
    # The real app mounts nothing under the prefix with clinic mode off (the mount itself is
    # delivered together with its route_policy entry, see the handover of v8-01).
    assert _real_url_map(clinic=False)["recordings_rules"] == []


SCRIPT = r'''
import json
from src.app import app
print("RESULT:" + json.dumps({"recordings_rules": sorted(
    r.rule for r in app.url_map.iter_rules() if r.rule.startswith("/api/clinic/v1/recordings"))}))
'''


def _real_url_map(*, clinic: bool) -> dict:
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp}/v801.db",
               "UPLOAD_FOLDER": f"{tmp}/uploads", "RATELIMIT_ENABLED": "false",
               "DISABLE_VOICE_EMBEDDING_CHECK": "true", "ENABLE_AUTO_PROCESSING": "false"}
        env.pop("CLINIC_MODE", None)
        if clinic:
            env["CLINIC_MODE"] = "1"
        done = subprocess.run([sys.executable, "-c", SCRIPT], cwd=ROOT, env=env,
                              capture_output=True, text=True, timeout=300)
    lines = [line for line in done.stdout.splitlines() if line.startswith("RESULT:")]
    assert lines, done.stderr[-3000:]
    return json.loads(lines[-1][len("RESULT:"):])
