"""PAR-COR-001: template list and content follow the authorized dictation.

An assigned Schreibkraft reads the dictation author's user template and that
case's pool template without joining the author pool. A foreign case or an
unrelated template is refused. The unscoped list stays the caller's own.
"""

from __future__ import annotations

import uuid

import pytest
from flask import Flask, jsonify
from flask_login import LoginManager, login_user
from flask_wtf.csrf import CSRFProtect, generate_csrf

from src.app import app as real_app
from src.clinic import pool_policy, pools, presets, workflow
from src.clinic.models import ClinicDictation
from src.clinic.routes_templates import PREFIX, blueprint
from src.database import db
from src.models import Recording, User

from test_pools import _suffix, _user, pool_db as pool_db

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _template(name, content, scope, scope_id):
    row = presets.save_template(
        name=name,
        content_bytes=content,
        scope=scope,
        scope_id=scope_id,
        insertion_mark=":Tdns.comfort",
        mime_type=DOCX_MIME,
        session=db.session,
    )
    db.session.commit()
    return row


def _case(author, pool_id, assignee_id):
    recording = Recording(user_id=author.id, title="d", status="PENDING")
    db.session.add(recording)
    db.session.flush()
    row = ClinicDictation(
        recording_id=recording.id, author_user_id=author.id, pool_id=pool_id,
    )
    db.session.add(row)
    db.session.flush()
    db.session.add(workflow.ClinicWorkflowDictation(
        id=row.id,
        author_user_id=author.id,
        pool_id=pool_id,
        assignee_user_id=assignee_id,
        status=workflow.TO_WRITE,
    ))
    db.session.commit()
    return row


@pytest.fixture
def world(pool_db, monkeypatch):
    monkeypatch.setenv("CLINIC_MODE", "1")
    presets.ClinicTemplate.__table__.create(db.engine, checkfirst=True)
    workflow.create_tables()
    admin = _user(admin=True)
    author = _user()
    secretary = _user()
    stranger = _user()

    own_pool = pools.create_author_pool("Pool-Own-" + _suffix(), actor_id=admin.id)
    other_pool = pools.create_author_pool("Pool-Other-" + _suffix(), actor_id=admin.id)
    pools.add_author_member(own_pool.id, author.id, actor_id=admin.id)

    sys_tmpl = _template("Sys-" + _suffix(), b"PK0304_SYS", "system", None)
    author_tmpl = _template("Author-" + _suffix(), b"PK0304_AUTHOR", "user", author.id)
    pool_tmpl = _template("Pool-" + _suffix(), b"PK0304_POOL", "pool", own_pool.id)
    other_pool_tmpl = _template("OtherPool-" + _suffix(), b"PK0304_OTHER", "pool", other_pool.id)
    stranger_tmpl = _template("Stranger-" + _suffix(), b"PK0304_STRANGER", "user", stranger.id)
    own_tmpl = _template("Secretary-" + _suffix(), b"PK0304_OWN", "user", secretary.id)

    assigned = _case(author, own_pool.id, secretary.id)
    foreign = _case(stranger, other_pool.id, stranger.id)

    flask = Flask(__name__)
    flask.config.update(
        TESTING=True,
        SECRET_KEY="synthetic-cor001-templates",
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
        http = flask.test_client()
        token = http.get(f"/login/{user.id}").json["csrf"]
        return http, {"X-CSRF-Token": token}

    with flask.app_context():
        yield dict(
            client=client,
            admin=admin,
            author=author,
            secretary=secretary,
            own_pool=own_pool,
            sys_tmpl=sys_tmpl,
            author_tmpl=author_tmpl,
            pool_tmpl=pool_tmpl,
            other_pool_tmpl=other_pool_tmpl,
            stranger_tmpl=stranger_tmpl,
            own_tmpl=own_tmpl,
            assigned=assigned,
            foreign=foreign,
        )


def _ids(response):
    assert response.status_code == 200, response.data
    return {item["id"] for item in response.json["items"]}


def test_assigned_secretary_receives_author_and_pool_templates(world):
    secretary = world["secretary"]
    assert world["own_pool"].id not in pool_policy.author_pool_ids(secretary.id)

    http, _headers = world["client"](secretary)
    query = f"{PREFIX}?dictation_id={world['assigned'].id}"
    found = _ids(http.get(query))
    assert found == {
        world["sys_tmpl"].id,
        world["author_tmpl"].id,
        world["pool_tmpl"].id,
    }

    content = http.get(f"{PREFIX}/{world['author_tmpl'].id}/content?dictation_id={world['assigned'].id}")
    assert content.status_code == 200
    assert content.data == b"PK0304_AUTHOR"
    detail = http.get(f"{PREFIX}/{world['pool_tmpl'].id}?dictation_id={world['assigned'].id}")
    assert detail.status_code == 200
    assert detail.json["scope"] == "pool"
    assert detail.json["scope_id"] == world["own_pool"].id

    author_menu = _ids(world["client"](world["author"])[0].get(query))
    assert author_menu == found


def test_foreign_dictation_and_unrelated_template_are_refused(world):
    http, headers = world["client"](world["secretary"])
    assigned = world["assigned"].id
    foreign = world["foreign"].id

    missing = http.get(f"{PREFIX}?dictation_id={foreign}")
    assert (missing.status_code, missing.json["code"]) == (404, "not_found")
    unknown = http.get(f"{PREFIX}?dictation_id={uuid.uuid4()}")
    assert (unknown.status_code, unknown.json["code"]) == (404, "not_found")
    malformed = http.get(f"{PREFIX}?dictation_id=not-a-case")
    assert (malformed.status_code, malformed.json["code"]) == (400, "invalid_payload")

    for template in (world["stranger_tmpl"], world["other_pool_tmpl"]):
        refused = http.get(f"{PREFIX}/{template.id}/content?dictation_id={assigned}")
        assert refused.status_code == 404
        assert refused.json["code"] == "not_found"

    bare = http.get(f"{PREFIX}/{world['author_tmpl'].id}/content")
    assert bare.status_code == 404

    removed = http.delete(
        f"{PREFIX}/{world['author_tmpl'].id}?dictation_id={assigned}", headers=headers,
    )
    assert removed.status_code == 404
    kept = db.session.get(presets.ClinicTemplate, world["author_tmpl"].id)
    assert kept.is_active is True


def test_own_template_management_stays_unscoped(world):
    secretary_ids = _ids(world["client"](world["secretary"])[0].get(PREFIX))
    assert secretary_ids == {world["sys_tmpl"].id, world["own_tmpl"].id}
    own = world["client"](world["secretary"])[0].get(f"{PREFIX}/{world['own_tmpl'].id}/content")
    assert own.status_code == 200
    assert own.data == b"PK0304_OWN"

    author_ids = _ids(world["client"](world["author"])[0].get(PREFIX))
    assert author_ids == {
        world["sys_tmpl"].id,
        world["author_tmpl"].id,
        world["pool_tmpl"].id,
    }


@pytest.mark.parametrize("actor", ["author", "secretary"])
def test_central_deny_overrides_owner_and_assignment(world, actor):
    user = world[actor]
    http, _headers = world["client"](user)
    query = f"?dictation_id={world['assigned'].id}"
    assert http.get(PREFIX + query).status_code == 200
    pools.deny(
        user.id, "pool", actor_id=world["admin"].id,
        pool_id=world["own_pool"].id, actions=["view"],
    )
    for path in (PREFIX, f"{PREFIX}/{world['author_tmpl'].id}",
                 f"{PREFIX}/{world['author_tmpl'].id}/content"):
        response = http.get(path + query)
        assert (response.status_code, response.json["code"]) == (404, "not_found")
    assert http.get(PREFIX).status_code == 200


@pytest.mark.parametrize("suffix", ["", "/content"])
def test_scoped_reads_cannot_fall_back_to_own_template_access(world, suffix):
    http, _headers = world["client"](world["secretary"])
    for case_id in (world["foreign"].id, str(uuid.uuid4())):
        for template in (world["own_tmpl"], world["sys_tmpl"]):
            response = http.get(f"{PREFIX}/{template.id}{suffix}?dictation_id={case_id}")
            assert (response.status_code, response.json["code"]) == (404, "not_found")
    unrelated = http.get(
        f"{PREFIX}/{world['own_tmpl'].id}{suffix}?dictation_id={world['assigned'].id}",
    )
    assert (unrelated.status_code, unrelated.json["code"]) == (404, "not_found")


def test_revoked_assignment_removes_author_template_access(world):
    http, _headers = world["client"](world["secretary"])
    query = f"?dictation_id={world['assigned'].id}"
    assert http.get(PREFIX + query).status_code == 200
    flow = db.session.get(workflow.ClinicWorkflowDictation, world["assigned"].id)
    flow.assignee_user_id = None
    db.session.commit()
    for path in (PREFIX, f"{PREFIX}/{world['author_tmpl'].id}",
                 f"{PREFIX}/{world['author_tmpl'].id}/content"):
        response = http.get(path + query)
        assert (response.status_code, response.json["code"]) == (404, "not_found")
