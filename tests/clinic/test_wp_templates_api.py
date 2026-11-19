"""Template routes (PAR-CFG-002), HTTP slice.

Positive, unauthorized, stale-version and failure cases against the real
``presets`` template storage and versioning. BT-CFG-002 stays in its own file.
"""

from __future__ import annotations

import base64
import hashlib
import uuid
import pytest
from flask import Flask, jsonify
from flask_login import LoginManager, login_user
from flask_wtf.csrf import CSRFProtect, generate_csrf

from src.app import app as real_app
from src.clinic import pools, presets
from src.clinic.routes_templates import (
    PREFIX,
    apply_template_text,
    assign_template,
    blueprint,
    validate_marker,
)
from src.database import db
from src.models import User

from test_pools import _suffix, _user, pool_db as pool_db

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
ODT_MIME = "application/vnd.oasis.opendocument.text"


def _make_payload(
    scope_type: str,
    scope_id: str | None,
    name: str,
    content_bytes: bytes,
    insertion_mark: str | None = ":Tdns.comfort",
    mime_type: str = DOCX_MIME,
    notes: str | None = "Synthetische Testvorlage",
) -> dict:
    content_b64 = base64.b64encode(content_bytes).decode("ascii")
    content_sha = hashlib.sha256(content_bytes).hexdigest()
    scope_dict = {"scope": scope_type}
    if scope_id is not None:
        scope_dict["scope_id"] = str(scope_id)
    return {
        "scope": scope_dict,
        "name": name,
        "notes": notes,
        "insertion_mark": insertion_mark,
        "mime_type": mime_type,
        "content_base64": content_b64,
        "content_sha256": content_sha,
    }


@pytest.fixture
def world(pool_db, monkeypatch):
    monkeypatch.setenv("CLINIC_MODE", "1")
    presets.ClinicTemplate.__table__.create(db.engine, checkfirst=True)
    admin = _user(admin=True)
    author = _user()
    other = _user()

    own_pool = pools.create_author_pool("Pool-Own-" + _suffix(), actor_id=admin.id)
    foreign_pool = pools.create_author_pool("Pool-Foreign-" + _suffix(), actor_id=admin.id)
    pools.add_author_member(own_pool.id, author.id, actor_id=admin.id)

    created_ids: list[str] = []

    def make_template(name, content, scope="system", scope_id=None, mark=":Tdns.comfort", mime=DOCX_MIME):
        row = presets.save_template(
            name=name,
            content_bytes=content,
            scope=scope,
            scope_id=scope_id,
            insertion_mark=mark,
            mime_type=mime,
            session=db.session,
        )
        db.session.commit()
        created_ids.append(row.id)
        return row

    sys_tmpl = make_template("SysReport-" + _suffix(), b"PK0304_SYS_CONTENT")
    pool_tmpl = make_template("PoolReport-" + _suffix(), b"PK0304_POOL_CONTENT", scope="pool", scope_id=own_pool.id)
    foreign_tmpl = make_template("ForeignReport-" + _suffix(), b"PK0304_FOREIGN_CONTENT", scope="pool", scope_id=foreign_pool.id)
    user_tmpl = make_template("UserReport-" + _suffix(), b"PK0304_USER_CONTENT", scope="user", scope_id=str(author.id))

    flask = Flask(__name__)
    flask.config.update(
        TESTING=True,
        SECRET_KEY="synthetic-templates-secret",
        SQLALCHEMY_DATABASE_URI=real_app.config["SQLALCHEMY_DATABASE_URI"],
    )
    db.init_app(flask)
    login = LoginManager(flask)
    CSRFProtect(flask)
    login.user_loader(lambda uid: db.session.get(User, int(uid)))

    @flask.get("/login/<int:uid>")
    def _login(uid):
        login_user(db.session.get(User, uid))
        return jsonify(csrf=generate_csrf())

    flask.register_blueprint(blueprint)

    def client(user):
        c = flask.test_client()
        csrf_tok = c.get(f"/login/{user.id}").json["csrf"]
        return c, {"X-CSRF-Token": csrf_tok}

    outer = db.session()
    outer.commit()
    with flask.app_context():
        yield dict(
            client=client,
            admin=admin,
            author=author,
            other=other,
            own_pool=own_pool,
            foreign_pool=foreign_pool,
            sys_tmpl=sys_tmpl,
            pool_tmpl=pool_tmpl,
            foreign_tmpl=foreign_tmpl,
            user_tmpl=user_tmpl,
            flask=flask,
            make_template=make_template,
        )
        outer.rollback()
        db.session.rollback()
        if created_ids:
            db.session.query(presets.ClinicTemplate).filter(
                presets.ClinicTemplate.id.in_(created_ids)
            ).delete(synchronize_session=False)
            db.session.commit()


def test_list_templates_shows_only_visible_scopes(world):
    c, _ = world["client"](world["author"])
    res = c.get(PREFIX)
    assert res.status_code == 200
    items = res.json["items"]
    ids = {item["id"] for item in items}

    # Author sees system, own pool, and own user templates
    assert world["sys_tmpl"].id in ids
    assert world["pool_tmpl"].id in ids
    assert world["user_tmpl"].id in ids
    # Author does NOT see foreign pool template
    assert world["foreign_tmpl"].id not in ids

    # TemplateSummary fields match specification
    summary = next(item for item in items if item["id"] == world["sys_tmpl"].id)
    assert set(summary.keys()) == {"id", "name", "scope", "version", "mime_type", "content_sha256"}
    assert summary["scope"] == "system"
    assert summary["version"] == 1

    # User 'other' (not in pool) does NOT see pool_tmpl or author's user_tmpl
    c2, _ = world["client"](world["other"])
    res2 = c2.get(PREFIX)
    ids2 = {item["id"] for item in res2.json["items"]}
    assert world["sys_tmpl"].id in ids2
    assert world["pool_tmpl"].id not in ids2
    assert world["user_tmpl"].id not in ids2


def test_save_template_positive_and_versioning(world):
    c, h = world["client"](world["author"])
    tmpl_name = "Klinikbericht-" + _suffix()
    content_v1 = b"PK0304_DOCX_V1_CONTENT_WITH_:Tdns.comfort_MARKER"

    payload_v1 = _make_payload("user", None, tmpl_name, content_v1, insertion_mark=":Tdns.comfort")
    headers = {
        **h,
        "If-Match": '"0"',
        "Idempotency-Key": str(uuid.uuid4()),
    }

    # 1. First save creates version 1
    post1 = c.post(PREFIX, headers=headers, json=payload_v1)
    assert post1.status_code == 201
    data1 = post1.json
    assert data1["name"] == tmpl_name
    assert data1["version"] == 1
    assert data1["scope"] == "user"
    assert post1.headers.get("ETag") == '"1"'

    # 2. Second save with If-Match: 1 increments to version 2
    content_v2 = b"PK0304_DOCX_V2_CONTENT_UPDATED_:Tdns.comfort"
    payload_v2 = _make_payload("user", None, tmpl_name, content_v2, insertion_mark=":Tdns.comfort")
    headers_v2 = {
        **h,
        "If-Match": '"1"',
        "Idempotency-Key": str(uuid.uuid4()),
    }
    post2 = c.post(PREFIX, headers=headers_v2, json=payload_v2)
    assert post2.status_code == 201
    data2 = post2.json
    assert data2["name"] == tmpl_name
    assert data2["version"] == 2
    assert post2.headers.get("ETag") == '"2"'

    # 3. Verify history is preserved in database (both v1 and v2 exist)
    all_versions = (
        db.session.query(presets.ClinicTemplate)
        .filter_by(scope="user", scope_id=str(world["author"].id), name=tmpl_name)
        .order_by(presets.ClinicTemplate.version.asc())
        .all()
    )
    assert len(all_versions) == 2
    assert all_versions[0].version == 1 and all_versions[0].content_bytes == content_v1
    assert all_versions[1].version == 2 and all_versions[1].content_bytes == content_v2


def test_get_template_content_positive_and_usable_without_original(world, tmp_path):
    c, _ = world["client"](world["author"])

    # Simulate external file that gets renamed/deleted on the workstation
    local_file = tmp_path / "original_template.docx"
    docx_bytes = b"PK\x03\x04FAKE_WORD_CONTENT_:Tdns.comfort_END"
    local_file.write_bytes(docx_bytes)

    tmpl = world["make_template"](
        "StationaerBericht-" + _suffix(),
        docx_bytes,
        scope="user",
        scope_id=str(world["author"].id),
        mark=":Tdns.comfort",
    )

    # Rename original local file (BT-CFG-002: "Ursprungsdateien im Testordner umbenennen")
    renamed_file = tmp_path / "renamed_away.docx"
    local_file.rename(renamed_file)
    assert not local_file.exists()

    # Content route fetches server copy; remains 100% usable without original file
    content_res = c.get(f"{PREFIX}/{tmpl.id}/content")
    assert content_res.status_code == 200
    assert content_res.data == docx_bytes
    assert content_res.mimetype == DOCX_MIME
    assert content_res.headers.get("Cache-Control") == "no-store"

    # Detail route returns full metadata
    detail_res = c.get(f"{PREFIX}/{tmpl.id}")
    assert detail_res.status_code == 200
    assert detail_res.json["insertion_mark"] == ":Tdns.comfort"
    assert detail_res.json["scope"] == "user"

    # Verify text insertion at defined insertion mark
    applied = apply_template_text(tmpl.content_bytes, tmpl.insertion_mark, "Patient ist wohlauf.")
    assert b"Patient ist wohlauf." in applied
    assert b":Tdns.comfort" not in applied


def test_reject_invalid_markers(world):
    c, h = world["client"](world["author"])

    invalid_markers = [
        "",                     # empty string
        "   ",                  # whitespace only
        " :Tmark ",             # untrimmed whitespace
        ":Tmark\n",             # newline / control character
        ":Tmark\r\n",           # carriage return
        ":Tmark\0null",         # null byte
        "M" * 150,              # exceeds 128 characters
        "<script>alert(1)</script>",  # script injection attempt
    ]

    for bad_mark in invalid_markers:
        payload = _make_payload("user", None, "BadMark-" + _suffix(), b"CONTENT", insertion_mark=bad_mark)
        headers = {**h, "If-Match": '"0"', "Idempotency-Key": str(uuid.uuid4())}
        r = c.post(PREFIX, headers=headers, json=payload)
        assert (r.status_code, r.json["code"]) == (422, "invalid_marker"), (bad_mark, r.json)

    # validate_marker directly
    for bad in invalid_markers:
        with pytest.raises(presets.PresetError if hasattr(presets, "ValidationError") else Exception):
            validate_marker(bad)


def test_reject_foreign_pools_and_untrusted_users(world):
    c, h = world["client"](world["author"])

    # 1. Author attempts to save to foreign pool -> 403 foreign_pool
    foreign_payload = _make_payload(
        "pool",
        world["foreign_pool"].id,
        "ForeignPoolTmpl-" + _suffix(),
        b"CONTENT",
    )
    h_foreign = {**h, "If-Match": '"0"', "Idempotency-Key": str(uuid.uuid4())}
    r_foreign = c.post(PREFIX, headers=h_foreign, json=foreign_payload)
    assert (r_foreign.status_code, r_foreign.json["code"]) == (403, "foreign_pool")

    # 2. Author attempts to supply foreign user ID in scope -> 403 foreign_user
    bad_user_payload = _make_payload(
        "user",
        str(world["other"].id),
        "OtherUserTmpl-" + _suffix(),
        b"CONTENT",
    )
    r_user = c.post(PREFIX, headers={**h, "If-Match": '"0"', "Idempotency-Key": str(uuid.uuid4())}, json=bad_user_payload)
    assert (r_user.status_code, r_user.json["code"]) == (403, "foreign_user")

    # 3. Non-admin author attempts to save system template -> 403 unauthorized
    sys_payload = _make_payload("system", None, "SysTmpl-" + _suffix(), b"CONTENT")
    r_sys = c.post(PREFIX, headers={**h, "If-Match": '"0"', "Idempotency-Key": str(uuid.uuid4())}, json=sys_payload)
    assert (r_sys.status_code, r_sys.json["code"]) == (403, "unauthorized")

    # 4. Request body containing forbidden identity/role keys -> 400 invalid_payload
    ok_payload = _make_payload("user", None, "OK-" + _suffix(), b"CONTENT")
    for forbidden_key in ("actor", "actor_id", "user_id", "role", "is_admin", "created_by"):
        bad_body = {**ok_payload, forbidden_key: world["other"].id}
        r_f = c.post(PREFIX, headers={**h, "If-Match": '"0"', "Idempotency-Key": str(uuid.uuid4())}, json=bad_body)
        assert (r_f.status_code, r_f.json["code"]) == (400, "invalid_payload")


def test_stale_version_and_precondition_required(world):
    c, h = world["client"](world["author"])
    tmpl_name = "StaleTest-" + _suffix()
    payload = _make_payload("user", None, tmpl_name, b"CONTENT")

    # Missing If-Match -> 428
    r_no_match = c.post(PREFIX, headers={**h, "Idempotency-Key": str(uuid.uuid4())}, json=payload)
    assert (r_no_match.status_code, r_no_match.json["code"]) == (428, "precondition_required")

    # Missing Idempotency-Key -> 428
    r_no_idemp = c.post(PREFIX, headers={**h, "If-Match": '"0"'}, json=payload)
    assert (r_no_idemp.status_code, r_no_idemp.json["code"]) == (428, "precondition_required")

    # Invalid Idempotency-Key (not a UUID) -> 400
    r_bad_idemp = c.post(PREFIX, headers={**h, "If-Match": '"0"', "Idempotency-Key": "not-a-uuid"}, json=payload)
    assert (r_bad_idemp.status_code, r_bad_idemp.json["code"]) == (400, "invalid_payload")

    # Template does not exist yet (version is 0), but caller claims expected_version 1 -> 412 stale_version
    r_stale_new = c.post(PREFIX, headers={**h, "If-Match": '"1"', "Idempotency-Key": str(uuid.uuid4())}, json=payload)
    assert (r_stale_new.status_code, r_stale_new.json["code"], r_stale_new.json["current_version"]) == (412, "stale_version", 0)

    # Save initial version 1 successfully
    r_ok = c.post(PREFIX, headers={**h, "If-Match": '"0"', "Idempotency-Key": str(uuid.uuid4())}, json=payload)
    assert r_ok.status_code == 201

    # Now current version is 1; caller submits with stale If-Match: 0 -> 412
    r_stale_old = c.post(PREFIX, headers={**h, "If-Match": '"0"', "Idempotency-Key": str(uuid.uuid4())}, json=payload)
    assert (r_stale_old.status_code, r_stale_old.json["code"], r_stale_old.json["current_version"]) == (412, "stale_version", 1)


def test_idempotency_replay_and_conflict(world):
    c, h = world["client"](world["author"])
    tmpl_name = "IdempTest-" + _suffix()
    payload = _make_payload("user", None, tmpl_name, b"CONTENT")
    idemp_key = str(uuid.uuid4())
    headers = {**h, "If-Match": '"0"', "Idempotency-Key": idemp_key}

    # 1. First execution
    first = c.post(PREFIX, headers=headers, json=payload)
    assert first.status_code == 201
    first_data = first.json

    # 2. Replay with identical payload and Idempotency-Key -> 201 with same response
    replay = c.post(PREFIX, headers=headers, json=payload)
    assert replay.status_code == 201
    assert replay.json == first_data
    assert replay.headers.get("ETag") == first.headers.get("ETag")

    # 3. Same Idempotency-Key but altered payload -> 409 idempotency_conflict
    altered_payload = _make_payload("user", None, tmpl_name + "-other", b"CONTENT_ALTERED")
    conflict = c.post(PREFIX, headers=headers, json=altered_payload)
    assert (conflict.status_code, conflict.json["code"]) == (409, "idempotency_conflict")


def test_payload_too_large_and_sha_mismatch(world):
    c, h = world["client"](world["author"])

    # 1. Payload > 2 MiB -> 413 payload_too_large
    huge_bytes = b"X" * (2 * 1024 * 1024 + 10)
    huge_payload = _make_payload("user", None, "Huge-" + _suffix(), huge_bytes)
    r_huge = c.post(PREFIX, headers={**h, "If-Match": '"0"', "Idempotency-Key": str(uuid.uuid4())}, json=huge_payload)
    assert (r_huge.status_code, r_huge.json["code"]) == (413, "payload_too_large")

    # 2. SHA-256 mismatch -> 400 invalid_payload
    bad_sha_payload = _make_payload("user", None, "BadSha-" + _suffix(), b"GOOD_CONTENT")
    bad_sha_payload["content_sha256"] = "0" * 64
    r_sha = c.post(PREFIX, headers={**h, "If-Match": '"0"', "Idempotency-Key": str(uuid.uuid4())}, json=bad_sha_payload)
    assert (r_sha.status_code, r_sha.json["code"]) == (400, "invalid_payload")

    # 3. Disallowed MIME type -> 400 invalid_payload
    bad_mime_payload = _make_payload("user", None, "BadMime-" + _suffix(), b"CONTENT", mime_type="application/pdf")
    r_mime = c.post(PREFIX, headers={**h, "If-Match": '"0"', "Idempotency-Key": str(uuid.uuid4())}, json=bad_mime_payload)
    assert (r_mime.status_code, r_mime.json["code"]) == (400, "invalid_payload")


def test_unauthorized_csrf_and_clinic_mode(world, monkeypatch):
    anon = world["flask"].test_client()
    assert anon.get(PREFIX).status_code == 401

    c, h = world["client"](world["author"])
    payload = _make_payload("user", None, "NoCsrf-" + _suffix(), b"CONTENT")
    no_csrf = c.post(PREFIX, headers={"If-Match": '"0"', "Idempotency-Key": str(uuid.uuid4())}, json=payload)
    assert (no_csrf.status_code, no_csrf.mimetype) == (403, "application/problem+json")

    monkeypatch.delenv("CLINIC_MODE")
    assert c.get(PREFIX).status_code == 404


def test_assign_template_and_delete_template(world):
    c, h = world["client"](world["author"])

    # 1. Author assigns existing system template to their author pool
    assign_url = f"{PREFIX}/{world['sys_tmpl'].id}/assign"
    assign_payload = {"scope": {"scope": "pool", "scope_id": world["own_pool"].id}}
    r_assign = c.post(assign_url, headers=h, json=assign_payload)
    assert r_assign.status_code == 201
    assigned_data = r_assign.json
    assert assigned_data["scope"] == "pool"
    assert assigned_data["name"] == world["sys_tmpl"].name

    # 2. Attempt to assign to a foreign pool -> 403 foreign_pool
    foreign_assign = {"scope": {"scope": "pool", "scope_id": world["foreign_pool"].id}}
    r_foreign = c.post(assign_url, headers=h, json=foreign_assign)
    assert (r_foreign.status_code, r_foreign.json["code"]) == (403, "foreign_pool")

    # 3. Direct service assign_template entry point
    tmpl = assign_template(
        world["sys_tmpl"].id,
        target_scope="user",
        target_scope_id=world["author"].id,
        actor_id=world["author"].id,
        session=db.session,
    )
    assert tmpl.scope == "user"
    assert tmpl.scope_id == str(world["author"].id)

    # 4. Delete/deactivate template (BT-CFG-002: "eine Vorlage entfernen")
    del_url = f"{PREFIX}/{world['user_tmpl'].id}"
    r_del = c.delete(del_url, headers=h)
    assert (r_del.status_code, r_del.json) == (200, {"ok": True})

    # Verify template is deactivated in database and omitted from active list
    db.session.expire_all()
    deleted_row = db.session.get(presets.ClinicTemplate, world["user_tmpl"].id)
    assert deleted_row.is_active is False
    c_list = c.get(PREFIX).json["items"]
    assert world["user_tmpl"].id not in {item["id"] for item in c_list}
