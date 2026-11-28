"""Tests for configurable metadata columns API and XML projection (PAR-CFG-005, w089).

Covers:
- Safe bounded XML extraction and allowed display types (BT-CFG-005 oracle preserved).
- Rejection of XXE (DOCTYPE, ENTITY, parameter entities, NUL bytes, oversized XML).
- Rejection of invalid field identifiers (bad regex, proto pollution, duplicates, >30 fields).
- Rejection of inaccessible clinical context (view deny, unpermitted field).
- HTTP API (/api/clinic/v1/worklist/layout):
  - GET: positive own layout, unauthenticated 401, private Cache-Control.
  - PUT: positive commit, CSRF guard, If-Match precondition (428 missing, 412 stale),
         idempotency replay and conflict (409), schema validation (400),
         dependency failure (503), CLINIC_MODE gating (404).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from flask import Flask, jsonify, session
from flask_login import LoginManager, UserMixin, login_user
from flask_wtf.csrf import CSRFProtect, generate_csrf

from src.clinic import policy
from src.clinic.list_layout import LayoutError
from src.clinic.parity_dispatch import CommandProblem
from src.clinic.presets import ClinicMetadataField
from src.clinic.routes_columns import (
    MAX_XML_BYTES,
    PREFIX,
    create_columns_blueprint,
    project_metadata_columns,
)
from src.clinic.worklist_layout import LayoutStore, WorklistLayoutError


# ---------------------------------------------------------------------------
# Fixtures & Helpers
# ---------------------------------------------------------------------------


class SyntheticUser(UserMixin):
    def __init__(self, uid: int) -> None:
        self.id = uid

    def get_id(self) -> str:
        return str(self.id)


@pytest.fixture
def clinic_env(monkeypatch):
    monkeypatch.setenv("CLINIC_MODE", "1")


@pytest.fixture
def fields():
    return [
        ClinicMetadataField(
            name="station",
            label="Station",
            field_type="string",
            xml_path="Station",
            is_active=True,
        ),
        ClinicMetadataField(
            name="department",
            label="Abteilung",
            field_type="string",
            xml_path="Abteilung",
            default_value="Allgemein",
            is_active=True,
        ),
        ClinicMetadataField(
            name="case_count",
            label="Fallzahl",
            field_type="integer",
            xml_path="Meta/Cases",
            is_active=True,
        ),
        ClinicMetadataField(
            name="exam_date",
            label="Untersuchungsdatum",
            field_type="date",
            xml_path="Meta/Date",
            display_format="YYYY-MM-DD",
            is_active=True,
        ),
        ClinicMetadataField(
            name="is_urgent",
            label="Dringlich",
            field_type="boolean",
            xml_path="Meta/Urgent",
            is_active=True,
        ),
        ClinicMetadataField(
            name="score",
            label="Score",
            field_type="decimal",
            xml_path="Meta/Score",
            is_active=True,
        ),
        ClinicMetadataField(
            name="inactive_field",
            label="Inaktiv",
            field_type="string",
            xml_path="Inactive",
            is_active=False,
        ),
        ClinicMetadataField(
            name="no_xml_field",
            label="Kein XML",
            field_type="string",
            xml_path=None,
            is_active=True,
        ),
    ]


@pytest.fixture
def http_app(clinic_env):
    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        SECRET_KEY="synthetic-columns-api-test-key",
        WTF_CSRF_ENABLED=True,
    )
    login_manager = LoginManager(app)
    CSRFProtect(app)

    users = {7: SyntheticUser(7), 8: SyntheticUser(8)}
    login_manager.user_loader(lambda uid: users.get(int(uid)))

    allowed_cols = ["author", "status", "deadline", "priority", "station"]
    default_layout = {
        "version": 1,
        "columns": [
            {"key": "author", "width": 140, "visible": True},
            {"key": "status", "width": 140, "visible": True},
            {"key": "station", "width": 160, "visible": True},
        ],
        "sort": [["status", "asc"]],
        "group_by": [],
    }

    state = {
        "fail_write": False,
        "receipts": {},
    }

    def writer(_user_id: int, _version: int, _text: str) -> None:
        if state["fail_write"]:
            raise OSError("synthetic database offline")

    store = LayoutStore(writer=writer)

    def layout_policy(actor_id: int):
        return default_layout, allowed_cols

    def run_once(scope, digest: str, operation):
        if scope in state["receipts"]:
            old_digest, result = state["receipts"][scope]
            if old_digest != digest:
                raise CommandProblem("IDEMPOTENCY_CONFLICT")
            return result
        result = operation()
        state["receipts"][scope] = (digest, result)
        return result

    bp = create_columns_blueprint(store, layout_policy, run_once)
    app.register_blueprint(bp)

    @app.get("/test-login/<int:uid>")
    def test_login(uid: int):
        user = users[uid]
        login_user(user)
        session["_user_id"] = str(user.id)
        return jsonify(csrf=generate_csrf())

    client = app.test_client()
    return app, client, state, users


# ---------------------------------------------------------------------------
# Unit Tests: project_metadata_columns
# ---------------------------------------------------------------------------


def test_project_metadata_columns_positive(clinic_env, fields):
    subject = policy.Subject(user_id=7)
    recording = policy.RecordingFacts(recording_id=101, owner_id=7)

    xml_data = """
    <DictationMeta>
        <Station>Lab-A</Station>
        <Abteilung>Kardiologie</Abteilung>
        <Meta>
            <Cases>12</Cases>
            <Date>2026-11-19</Date>
            <Urgent>true</Urgent>
            <Score>98.6</Score>
        </Meta>
    </DictationMeta>
    """

    res = project_metadata_columns(
        subject,
        recording,
        fields,
        ["station", "department", "case_count", "exam_date", "is_urgent", "score"],
        can_read_field=lambda s, r, f: True,
        load_xml=lambda: xml_data,
    )

    assert len(res) == 6
    by_key = {col["key"]: col for col in res}

    assert by_key["station"] == {
        "key": "station",
        "label": "Station",
        "display_type": "string",
        "display_format": None,
        "value": "Lab-A",
    }
    assert by_key["department"]["value"] == "Kardiologie"
    assert by_key["case_count"]["value"] == "12"
    assert by_key["exam_date"]["value"] == "2026-11-19"
    assert by_key["exam_date"]["display_format"] == "YYYY-MM-DD"
    assert by_key["is_urgent"]["value"] == "true"
    assert by_key["score"]["value"] == "98.6"


def test_bt_cfg_005_oracle_missing_xml_and_empty_field(clinic_env, fields):
    """Preserves BT-CFG-005 oracle:

    - Dictation with Station=Lab-A displays Zusatzwert.
    - Dictation with missing XML or absent tag leaves value cleanly empty (None).
    """
    subject = policy.Subject(user_id=7)
    rec1 = policy.RecordingFacts(recording_id=1, owner_id=7)
    rec2 = policy.RecordingFacts(recording_id=2, owner_id=7)
    rec3 = policy.RecordingFacts(recording_id=3, owner_id=7)

    # Rec 1: Station is present
    res1 = project_metadata_columns(
        subject,
        rec1,
        fields,
        ["station"],
        can_read_field=lambda s, r, f: True,
        load_xml=lambda: "<DictationMeta><Station>Lab-A</Station></DictationMeta>",
    )
    assert res1[0]["value"] == "Lab-A"

    # Rec 2: XML is completely absent (None)
    res2 = project_metadata_columns(
        subject,
        rec2,
        fields,
        ["station"],
        can_read_field=lambda s, r, f: True,
        load_xml=lambda: None,
    )
    assert res2[0]["value"] is None

    # Rec 3: XML exists but Station tag is omitted
    res3 = project_metadata_columns(
        subject,
        rec3,
        fields,
        ["station"],
        can_read_field=lambda s, r, f: True,
        load_xml=lambda: "<DictationMeta><Other>Value</Other></DictationMeta>",
    )
    assert res3[0]["value"] is None


def test_project_metadata_columns_default_value_when_tag_missing(clinic_env, fields):
    subject = policy.Subject(user_id=7)
    rec = policy.RecordingFacts(recording_id=1, owner_id=7)

    res = project_metadata_columns(
        subject,
        rec,
        fields,
        ["department"],
        can_read_field=lambda s, r, f: True,
        load_xml=lambda: "<DictationMeta><Station>Lab-A</Station></DictationMeta>",
    )
    assert res[0]["value"] == "Allgemein"


@pytest.mark.parametrize(
    "xxe_payload",
    [
        '<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><Station>&xxe;</Station>',
        '<!DOCTYPE root [<!ENTITY % pe "test"> %pe;]><Station>1</Station>',
        '<!doctype foo [<!entity xxe "val">]><Station>&xxe;</Station>',
        b"<DictationMeta>\x00<Station>Lab</Station></DictationMeta>",
        "<unclosed><Station>broken",
        "A" * (MAX_XML_BYTES + 1),
    ],
)
def test_project_metadata_columns_rejects_xxe_and_malformed(clinic_env, fields, xxe_payload):
    subject = policy.Subject(user_id=7)
    rec = policy.RecordingFacts(recording_id=1, owner_id=7)

    with pytest.raises(LayoutError) as exc:
        project_metadata_columns(
            subject,
            rec,
            fields,
            ["station"],
            can_read_field=lambda s, r, f: True,
            load_xml=lambda: xxe_payload,
        )
    assert exc.value.code == "invalid_xml"


@pytest.mark.parametrize(
    "bad_name",
    [
        "123station",
        "station-name",
        "station.name",
        "station name",
        "",
        "constructor",
        "prototype",
        "__proto__",
        "a" * 65,
    ],
)
def test_project_metadata_columns_rejects_invalid_identifiers(clinic_env, fields, bad_name):
    subject = policy.Subject(user_id=7)
    rec = policy.RecordingFacts(recording_id=1, owner_id=7)

    with pytest.raises(LayoutError) as exc:
        project_metadata_columns(
            subject,
            rec,
            fields,
            [bad_name],
            can_read_field=lambda s, r, f: True,
            load_xml=lambda: None,
        )
    assert exc.value.code == "bad_key"


def test_project_metadata_columns_rejects_duplicates_and_excessive_names(clinic_env, fields):
    subject = policy.Subject(user_id=7)
    rec = policy.RecordingFacts(recording_id=1, owner_id=7)

    with pytest.raises(LayoutError) as exc:
        project_metadata_columns(
            subject,
            rec,
            fields,
            ["station", "station"],
            can_read_field=lambda s, r, f: True,
            load_xml=lambda: None,
        )
    assert exc.value.code == "bad_key"

    too_many = [f"field_{i}" for i in range(31)]
    with pytest.raises(LayoutError) as exc:
        project_metadata_columns(
            subject,
            rec,
            fields,
            too_many,
            can_read_field=lambda s, r, f: True,
            load_xml=lambda: None,
        )
    assert exc.value.code == "bad_key"


def test_project_metadata_columns_rejects_unsupported_display_type(clinic_env):
    subject = policy.Subject(user_id=7)
    rec = policy.RecordingFacts(recording_id=1, owner_id=7)
    bad_field = ClinicMetadataField(
        name="custom_blob",
        label="Blob",
        field_type="binary_blob",
        xml_path="Blob",
        is_active=True,
    )

    with pytest.raises(LayoutError) as exc:
        project_metadata_columns(
            subject,
            rec,
            [bad_field],
            ["custom_blob"],
            can_read_field=lambda s, r, f: True,
            load_xml=lambda: None,
        )
    assert exc.value.code == "bad_display_type"


def test_project_metadata_columns_protects_inaccessible_clinical_context(clinic_env, fields):
    subject = policy.Subject(user_id=7)
    # Foreign recording without grant
    foreign_rec = policy.RecordingFacts(recording_id=999, owner_id=8)

    with pytest.raises(WorklistLayoutError) as exc:
        project_metadata_columns(
            subject,
            foreign_rec,
            fields,
            ["station"],
            can_read_field=lambda s, r, f: True,
            load_xml=lambda: None,
        )
    assert exc.value.code == "unauthorized"

    # Own recording, but field policy denies this field
    own_rec = policy.RecordingFacts(recording_id=1, owner_id=7)
    with pytest.raises(WorklistLayoutError) as exc:
        project_metadata_columns(
            subject,
            own_rec,
            fields,
            ["station"],
            can_read_field=lambda s, r, f: False,
            load_xml=lambda: None,
        )
    assert exc.value.code == "unauthorized"

    # Inactive field or field without xml_path (no existence leak)
    with pytest.raises(WorklistLayoutError) as exc:
        project_metadata_columns(
            subject,
            own_rec,
            fields,
            ["inactive_field"],
            can_read_field=lambda s, r, f: True,
            load_xml=lambda: None,
        )
    assert exc.value.code == "unauthorized"

    # Explicit deny in subject
    denied_subject = policy.Subject(user_id=7, denies=(policy.Deny(recording_ids=frozenset({1})),))
    with pytest.raises(WorklistLayoutError) as exc:
        project_metadata_columns(
            denied_subject,
            own_rec,
            fields,
            ["station"],
            can_read_field=lambda s, r, f: True,
            load_xml=lambda: None,
        )
    assert exc.value.code == "unauthorized"


def test_project_metadata_columns_empty_names_returns_empty(clinic_env, fields):
    subject = policy.Subject(user_id=7)
    rec = policy.RecordingFacts(recording_id=1, owner_id=7)
    assert project_metadata_columns(
        subject,
        rec,
        fields,
        [],
        can_read_field=lambda s, r, f: True,
        load_xml=lambda: None,
    ) == []


def test_project_metadata_columns_disabled_in_non_clinic_mode(fields, monkeypatch):
    monkeypatch.delenv("CLINIC_MODE", raising=False)
    subject = policy.Subject(user_id=7)
    rec = policy.RecordingFacts(recording_id=1, owner_id=7)

    with pytest.raises(WorklistLayoutError) as exc:
        project_metadata_columns(
            subject,
            rec,
            fields,
            ["station"],
            can_read_field=lambda s, r, f: True,
            load_xml=lambda: None,
        )
    assert exc.value.code == "unauthorized"


# ---------------------------------------------------------------------------
# HTTP Route Tests: /api/clinic/v1/worklist/layout
# ---------------------------------------------------------------------------


def test_http_get_layout_positive_and_private_cache(http_app):
    _, client, _, _ = http_app
    login_resp = client.get("/test-login/7")
    assert login_resp.status_code == 200

    resp = client.get(PREFIX)
    assert resp.status_code == 200
    assert resp.headers.get("Cache-Control") == "private, no-store"

    data = resp.get_json()
    assert data["version"] == 0
    assert len(data["layout"]["columns"]) == 3
    assert [c["key"] for c in data["layout"]["columns"]] == ["author", "status", "station"]


def test_http_get_layout_unauthenticated(http_app):
    _, client, _, _ = http_app
    resp = client.get(PREFIX)
    assert resp.status_code == 401
    assert resp.mimetype == "application/problem+json"
    problem = resp.get_json()
    assert problem["code"] == "UNAUTHORIZED"
    assert problem["status"] == 401
    assert UUID(problem["correlation_id"])


def test_http_put_layout_positive_and_idempotency_replay(http_app):
    _, client, state, _ = http_app
    login = client.get("/test-login/7").get_json()
    csrf = login["csrf"]
    key = str(uuid4())

    new_layout = {
        "version": 1,
        "columns": [
            {"key": "status", "width": 180, "visible": True},
            {"key": "author", "width": 140, "visible": True},
            {"key": "station", "width": 200, "visible": False},
        ],
        "sort": [["status", "desc"]],
        "group_by": ["status"],
    }

    headers = {
        "X-CSRFToken": csrf,
        "If-Match": '"0"',
        "Idempotency-Key": key,
    }

    # First write
    resp = client.put(PREFIX, headers=headers, json=new_layout)
    assert resp.status_code == 200
    res = resp.get_json()
    assert res["version"] == 1
    assert res["layout"]["columns"][0]["key"] == "status"
    assert res["layout"]["columns"][0]["width"] == 180
    assert res["layout"]["columns"][2]["visible"] is False
    assert res["layout"]["sort"] == [["status", "desc"]]
    assert res["layout"]["group_by"] == ["status"]

    # Identical replay: same headers & body -> returns cached result without rewriting
    replay = client.put(PREFIX, headers=headers, json=new_layout)
    assert replay.status_code == 200
    assert replay.get_json() == res


def test_http_put_layout_idempotency_conflict(http_app):
    _, client, _, _ = http_app
    login = client.get("/test-login/7").get_json()
    csrf = login["csrf"]
    key = str(uuid4())

    layout_a = {
        "version": 1,
        "columns": [{"key": "status", "width": 140, "visible": True}],
        "sort": [],
        "group_by": [],
    }
    layout_b = {
        "version": 1,
        "columns": [{"key": "author", "width": 140, "visible": True}],
        "sort": [],
        "group_by": [],
    }

    headers = {
        "X-CSRFToken": csrf,
        "If-Match": '"0"',
        "Idempotency-Key": key,
    }

    resp1 = client.put(PREFIX, headers=headers, json=layout_a)
    assert resp1.status_code == 200

    # Same key, different body -> 409 conflict
    resp2 = client.put(PREFIX, headers=headers, json=layout_b)
    assert resp2.status_code == 409
    assert resp2.get_json()["code"] == "IDEMPOTENCY_CONFLICT"


def test_http_put_layout_stale_version_412(http_app):
    _, client, _, _ = http_app
    login = client.get("/test-login/7").get_json()
    csrf = login["csrf"]

    valid_payload = {
        "version": 1,
        "columns": [{"key": "status", "width": 140, "visible": True}],
        "sort": [],
        "group_by": [],
    }

    # Save version 1
    resp1 = client.put(
        PREFIX,
        headers={"X-CSRFToken": csrf, "If-Match": '"0"', "Idempotency-Key": str(uuid4())},
        json=valid_payload,
    )
    assert resp1.status_code == 200

    # Stale version: sending If-Match "0" when current version is 1 -> 412
    resp2 = client.put(
        PREFIX,
        headers={"X-CSRFToken": csrf, "If-Match": '"0"', "Idempotency-Key": str(uuid4())},
        json=valid_payload,
    )
    assert resp2.status_code == 412
    assert resp2.get_json()["code"] == "REVISION_CONFLICT"


def test_http_put_layout_precondition_required_428(http_app):
    _, client, _, _ = http_app
    csrf = client.get("/test-login/7").get_json()["csrf"]

    resp = client.put(
        PREFIX,
        headers={"X-CSRFToken": csrf, "Idempotency-Key": str(uuid4())},
        json={"version": 1, "columns": [], "sort": [], "group_by": []},
    )
    assert resp.status_code == 428
    assert resp.get_json()["code"] == "PRECONDITION_REQUIRED"


@pytest.mark.parametrize(
    ("hdr_if_match", "hdr_idempotency"),
    [
        ("0", "11111111-1111-4111-8111-111111111111"),  # unquoted If-Match
        ('"-1"', "22222222-2222-4222-8222-222222222222"),  # negative version
        ('"abc"', "33333333-3333-4333-8333-333333333333"),  # non-numeric version
        ('"0"', "not-a-uuid"),  # invalid idempotency key
        ('"0"', ""),  # empty idempotency key
    ],
)
def test_http_put_layout_invalid_headers_400(http_app, hdr_if_match, hdr_idempotency):
    _, client, _, _ = http_app
    csrf = client.get("/test-login/7").get_json()["csrf"]

    headers = {"X-CSRFToken": csrf}
    if hdr_if_match is not None:
        headers["If-Match"] = hdr_if_match
    if hdr_idempotency is not None:
        headers["Idempotency-Key"] = hdr_idempotency

    resp = client.put(
        PREFIX,
        headers=headers,
        json={"version": 1, "columns": [], "sort": [], "group_by": []},
    )
    assert resp.status_code == 400
    assert resp.get_json()["code"] == "INVALID_PAYLOAD"


def test_http_put_layout_csrf_forbidden(http_app):
    _, client, _, _ = http_app
    client.get("/test-login/7")

    headers = {
        "If-Match": '"0"',
        "Idempotency-Key": str(uuid4()),
        "X-CSRFToken": "invalid-csrf-token",
    }
    resp = client.put(
        PREFIX,
        headers=headers,
        json={"version": 1, "columns": [], "sort": [], "group_by": []},
    )
    assert resp.status_code == 403
    assert resp.get_json()["code"] == "FORBIDDEN"


@pytest.mark.parametrize(
    "bad_payload",
    [
        {"version": 2, "columns": [], "sort": [], "group_by": []},  # bad version
        {"version": 1, "columns": [{"key": "unknown_col", "width": 140, "visible": True}], "sort": [], "group_by": []},
        {"version": 1, "columns": [{"key": "status", "width": 20, "visible": True}], "sort": [], "group_by": []},  # width < 40
        {"version": 1, "columns": [{"key": "status", "width": 900, "visible": True}], "sort": [], "group_by": []},  # width > 800
        {"version": 1, "columns": [], "sort": [["status", "asc"]] * 31, "group_by": []},  # sort > 30
        {"version": 1, "columns": [], "sort": [], "group_by": ["status", "author", "station", "deadline"]},  # group > 3
        {"version": 1, "columns": [], "sort": [], "group_by": [], "actor": 99},  # forbidden extra key
    ],
)
def test_http_put_layout_bad_payload_400(http_app, bad_payload):
    _, client, _, _ = http_app
    csrf = client.get("/test-login/7").get_json()["csrf"]

    headers = {
        "X-CSRFToken": csrf,
        "If-Match": '"0"',
        "Idempotency-Key": str(uuid4()),
    }
    resp = client.put(PREFIX, headers=headers, json=bad_payload)
    assert resp.status_code == 400
    assert resp.get_json()["code"] == "INVALID_PAYLOAD"


def test_http_put_layout_dependency_unavailable_503(http_app):
    _, client, state, _ = http_app
    csrf = client.get("/test-login/7").get_json()["csrf"]
    state["fail_write"] = True

    payload = {
        "version": 1,
        "columns": [{"key": "status", "width": 140, "visible": True}],
        "sort": [],
        "group_by": [],
    }
    headers = {
        "X-CSRFToken": csrf,
        "If-Match": '"0"',
        "Idempotency-Key": str(uuid4()),
    }

    resp = client.put(PREFIX, headers=headers, json=payload)
    assert resp.status_code == 503
    assert resp.get_json()["code"] == "DEPENDENCY_UNAVAILABLE"


def test_http_routes_disabled_when_clinic_mode_off(http_app, monkeypatch):
    monkeypatch.delenv("CLINIC_MODE", raising=False)
    _, client, _, _ = http_app
    resp = client.get(PREFIX)
    assert resp.status_code == 404


@pytest.fixture
def mounted_worklist(clinic_env):
    from src.app import app, register_clinic_resources, register_clinic_worklist
    from src.clinic import workflow
    from src.clinic.identity import ClinicAccountPolicy
    from src.clinic.models import ClinicDictation, ClinicOriginal
    from src.clinic.routes_metadata_fields import create_metadata_fields_blueprint
    from src.database import db
    from src.models import Recording, User

    mounted = Flask(__name__)
    mounted.config.update(TESTING=True, SECRET_KEY="synthetic-worklist-test-key",
                          SQLALCHEMY_DATABASE_URI=app.config["SQLALCHEMY_DATABASE_URI"])
    db.init_app(mounted)
    LoginManager(mounted).user_loader(lambda uid: db.session.get(User, int(uid.partition(":")[0])))
    policy.install_pool_policy(mounted)
    register_clinic_resources(mounted)
    register_clinic_worklist(mounted)
    mounted.register_blueprint(create_metadata_fields_blueprint())
    mounted.add_url_rule("/test-csrf", view_func=lambda: jsonify(csrf=generate_csrf()))

    with mounted.app_context():
        users = [User(username=kind, email=f"p245-{kind}@example.invalid", password="x", is_admin=admin)
                 for kind, admin in (("admin", True), ("author", False), ("other", False))]
        db.session.add_all(users)
        db.session.flush()
        admin, author, other = users
        db.session.add(ClinicAccountPolicy(user_id=admin.id, status="active", login_policy="local_only"))
        originals = {
            "XML": (author, "<meta><patient><station>Lab-A</station><name>Private</name></patient></meta>"),
            "Legacy": (author, None),
            "Malformed": (author, "<meta><patient>"),
            "Foreign": (other, "<meta><patient><station>Foreign</station></patient></meta>"),
        }
        ids = {}
        for title, (owner, xml) in originals.items():
            rec = Recording(user_id=owner.id, title=title, status="COMPLETED", original_filename="test.wav")
            db.session.add(rec)
            db.session.flush()
            item = ClinicDictation(recording_id=rec.id, author_user_id=owner.id)
            db.session.add(item)
            db.session.flush()
            db.session.add(workflow.ClinicWorkflowDictation(
                id=item.id, author_user_id=owner.id, pool_id=str(uuid4()), status=workflow.DRAFT,
                created_at=datetime(2026, 11, 27, 8, 30, tzinfo=timezone.utc), audio_seconds=10))
            if xml is not None:
                db.session.add(ClinicOriginal(
                    dictation_id=item.id, content_hash="hash", size_bytes=1, storage_key="synthetic",
                    receipt=json.dumps({"origin": "web-upload", "metadata_xml": xml}), origin_ref="test"))
            ids[title] = item.id
        admin_id, author_id = admin.get_id(), author.get_id()
        db.session.commit()

    def login(user_id):
        client = mounted.test_client()
        with client.session_transaction() as browser:
            browser["_user_id"] = user_id
        return client, {"X-CSRFToken": client.get("/test-csrf").json["csrf"]}

    return login(admin_id), login(author_id), ids


def test_mounted_rows_project_only_active_xml_for_visible_dictations(mounted_worklist):
    (admin, csrf), (author, _), ids = mounted_worklist
    fields = "/api/clinic/v1/metadata-fields"
    for name, properties in (("Station", {"xml_path": "patient/station"}),
                             ("Inactive", {"xml_path": "patient/name", "is_active": False}),
                             ("FormOnly", {}),
                             ("title", {"xml_path": "patient/name"})):
        saved = admin.put(f"{fields}/{name}", json={"label": name, **properties}, headers=csrf)
        assert saved.status_code == 201

    response = author.get("/api/clinic/v1/worklist/rows")
    assert response.status_code == 200
    assert response.json["total"] == 3
    rows = {row["title"]: row for row in response.json["items"]}
    assert {title: row["Station"] for title, row in rows.items()} == {
        "XML": "Lab-A", "Legacy": None, "Malformed": None,
    }
    assert rows["XML"]["dictation_id"] == ids["XML"]
    assert rows["XML"]["created_at"] == "2026-11-27T08:30:00Z"
    assert all({"Inactive", "FormOnly", "metadata_xml"}.isdisjoint(row) for row in rows.values())
    assert "Private" not in response.get_data(as_text=True)


def test_mounted_layout_accepts_and_retires_xml_column(mounted_worklist):
    (admin, csrf), (author, author_csrf), ids = mounted_worklist
    field = "/api/clinic/v1/metadata-fields/Station"
    created = admin.put(field, json={"label": "Station", "xml_path": "patient/station"}, headers=csrf)
    assert created.status_code == 201
    default = author.get(PREFIX).json
    layout = default["layout"]
    layout["columns"].append({"key": "Station", "width": 140, "visible": True})
    layout["sort"], layout["group_by"] = [["Station", "asc"]], ["Station"]
    headers = {**author_csrf, "If-Match": '"0"', "Idempotency-Key": str(uuid4())}
    saved = author.put(PREFIX, json=layout, headers=headers)
    assert saved.status_code == 200
    assert author.get(PREFIX).json == saved.json

    url = "/api/clinic/v1/worklist/rows?page_size=1"
    first = author.get(url)
    assert first.status_code == 200
    assert first.json["items"][0]["dictation_id"] == ids["XML"]
    retired = admin.put(field, json={"is_active": False, "expected_version": created.json["version"]},
                        headers=csrf)
    assert retired.status_code == 200
    updated = author.get(url, headers={"If-None-Match": first.headers["ETag"]})
    assert updated.status_code == 200
    assert "Station" not in updated.json["items"][0]
    assert "Station" not in {column["key"] for column in author.get(PREFIX).json["layout"]["columns"]}


def test_mounted_layout_loads_after_saving_more_than_fifteen_xml_columns(mounted_worklist):
    (admin, csrf), (author, author_csrf), _ = mounted_worklist
    columns = []
    for index in range(16):
        name = f"Station{index}"
        saved = admin.put(f"/api/clinic/v1/metadata-fields/{name}",
                          json={"label": name, "xml_path": "patient/station"}, headers=csrf)
        assert saved.status_code == 201
        columns.append({"key": name, "width": 140, "visible": True})
    layout = {"version": 1, "columns": columns, "sort": [], "group_by": []}
    headers = {**author_csrf, "If-Match": '"0"', "Idempotency-Key": str(uuid4())}
    assert author.put(PREFIX, json=layout, headers=headers).status_code == 200
    loaded = author.get(PREFIX)
    assert loaded.status_code == 200
    assert loaded.json["layout"]["columns"][:16] == columns
    assert len(loaded.json["layout"]["columns"]) == 30
