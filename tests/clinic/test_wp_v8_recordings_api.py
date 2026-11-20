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
from src.clinic import dictation_roles, policy, recordings_list, routes_worklist, workflow, worklist_entries
from src.clinic.archive_policy import ClinicArchiveEntry
from src.clinic.identity import ClinicAccountPolicy, create_tables as _create_identity_tables
from src.clinic.models import ClinicContextRevision, ClinicDictation, ClinicReviewLease
from src.clinic.pools import ClinicAuthorPool, ClinicPoolRoute, ClinicStaffPool
from src.clinic.routes_recordings import PREFIX, create_blueprint
from src.clinic.worklist_layout import LayoutStore
from src.models import AuditEvent, Group, GroupMembership, Recording, User
from src.services import audit

ROOT = Path(__file__).resolve().parents[2]
ITEM_KEYS = {"dictation_id", "recording_id", "recorded_at", "time_source", "sort_time", "day", "dictated_by",
             "staff_pools", "lease_holder", "processing_status", "workflow_status", "duration_ms",
             "case_display", "archived_at"}


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
               seconds=12, rec_status="COMPLETED"):
    rec = Recording(user_id=author.id, title="PATIENT-TITLE-SECRET", status=rec_status,
                    original_filename="x.wav", meeting_date=meeting)
    db.session.add(rec)
    db.session.flush()
    kwargs = {} if created is None else {"created_at": created}
    dictation = ClinicDictation(recording_id=rec.id, author_user_id=author.id, pool_id=pool_id, **kwargs)
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
        item = _dictation(author, pool_id=scene.pool_a, meeting=datetime(2026, 7, 1, 8, 30), seconds=42)
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
    assert row["recorded_at"] == "2026-07-01T08:30:00Z" == row["sort_time"]
    assert row["time_source"] == "recorded" and row["day"] == "2026-07-01"
    assert row["dictated_by"] == {"id": str(scene.a), "display_name": "Dr. Anna Autorin"}
    assert row["staff_pools"] == [{"id": scene.staff, "name": scene.staff_name}]
    assert row["lease_holder"] is None and row["case_display"] == "FALL-77"
    assert row["processing_status"] == "COMPLETED" and row["workflow_status"] == workflow.TO_WRITE
    assert row["duration_ms"] == 42000
    assert row["archived_at"] is None
    text = json.dumps(body)
    assert "PATIENT-TITLE-SECRET" not in text and "PAT-SECRET" not in text and "patient" not in text.lower()


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
                         status=workflow.TO_ARCHIVE)
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
    """Same loader and same row policy as the worklist: the list never adds a second rule."""
    with app.app_context():
        own = _dictation(db.session.get(User, scene.admin), pool_id=scene.pool_a, meeting=datetime(2026, 5, 1, 9))
        foreign = _dictation(db.session.get(User, scene.b), pool_id=scene.pool_b, meeting=datetime(2026, 5, 2, 9))
        own_id, foreign_recording = own.id, foreign.recording_id
        baseline = db.session.execute(select(func.max(AuditEvent.id))).scalar_one() or 0
    listed = {row["dictation_id"] for row in get(client_for(scene, scene.admin)).json["items"]}
    assert own_id in listed
    audit.flush()
    with app.app_context():
        snapshot = routes_worklist.get_worklist(
            worklist_entries.load_entries(scene.admin), policy.build_subject(scene.admin), LayoutStore(),
            page_size=100)
        assert listed == {row["dictation_id"] for row in snapshot.page.items}
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
