"""Exercise scoped text blocks through the mounted clinic API and a real DB."""

import hashlib

import pytest
from flask import Flask, session
from flask_login import LoginManager
from flask_wtf.csrf import generate_csrf
from test_audit_richtext_graphics import png

from src.app import load_user, register_clinic_resources
from src.clinic import route_policy
from src.clinic.guard import clinic_guard
from src.clinic.identity import ClinicAccountPolicy
from src.clinic.pools import ClinicAuthorPool, ClinicAuthorPoolMember
from src.clinic.registry import create_clinic_tables
from src.database import db
from src.models import User

BLOCKS = "/api/clinic/v1/resources/text-blocks"


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("CLINIC_MODE", "1")
    monkeypatch.setenv("CLINIC_ORIGINALS_ROOT", str(tmp_path))
    monkeypatch.setattr(clinic_guard.__module__ + ".ROUTE_POLICY", route_policy._build())
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="synthetic-text-blocks",
                      SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path / 'blocks.db'}")
    db.init_app(app)
    LoginManager(app).user_loader(load_user)
    app.before_request(clinic_guard)
    register_clinic_resources(app)

    with app.app_context():
        db.metadata.create_all(db.engine, tables=[table for table in db.metadata.tables.values()
                                                 if not table.name.startswith("clinic_")])
        create_clinic_tables()
        for uid in range(1, 6):
            db.session.add(User(id=uid, username=f"block-{uid}", email=f"block-{uid}@test.invalid",
                                is_admin=(uid == 4)))
        db.session.flush()
        db.session.add_all([ClinicAccountPolicy(user_id=uid, status="active") for uid in range(1, 6)])
        db.session.add_all([ClinicAuthorPool(id="pool-a", name="A", created_by=1),
                            ClinicAuthorPool(id="pool-b", name="B", created_by=3)])
        db.session.flush()
        db.session.add_all([ClinicAuthorPoolMember(pool_id="pool-a", user_id=2, created_by=1),
                            ClinicAuthorPoolMember(pool_id="pool-b", user_id=3, created_by=3)])
        db.session.commit()
    return app


def browser(app, uid):
    client = app.test_client()
    with app.test_request_context():
        token = generate_csrf()
        raw_token = session["csrf_token"]
    with client.session_transaction() as state:
        state.update(_user_id=str(uid), csrf_token=raw_token)
    return client, {"X-CSRFToken": token}


def create(client, headers, scope="pool", **fields):
    payload = {"scope": scope, "title": "Baustein", "text": "Synthetischer Text."}
    if scope == "pool":
        payload["pool_id"] = "pool-a"
    return client.post(BLOCKS, json={**payload, **fields}, headers=headers)


def test_pool_visibility_and_write_authority(world):
    manager, headers = browser(world, 1)
    member, member_headers = browser(world, 2)
    outsider, outsider_headers = browser(world, 3)
    created = create(manager, headers)
    assert created.status_code == 201
    block = created.json
    path = f"{BLOCKS}/{block['id']}"
    assert [row["id"] for row in member.get(BLOCKS).json["items"]] == [block["id"]]
    detail = member.get(path)
    assert detail.json["insert_text"] == "Synthetischer Text."
    assert detail.cache_control.no_store
    assert outsider.get(BLOCKS).json["items"] == []
    assert outsider.get(path).status_code == 404
    assert create(outsider, outsider_headers).status_code == 403
    assert create(manager, headers, pool_id="pool-b").status_code == 403
    assert create(member, member_headers).status_code == 403

    update = {"title": "Geändert", "text": "Neuer Inhalt.", "revision": block["revision"]}
    assert outsider.put(path, json=update, headers=outsider_headers).status_code == 404
    assert member.put(path, json=update, headers=member_headers).status_code == 403
    assert manager.put(path, json={**update, "pool_id": "pool-b"}, headers=headers).status_code == 400
    assert member.get(path).json["text"] == "Synthetischer Text."
    assert manager.put(path, json=update, headers=headers).status_code == 200
    assert manager.put(path, json=update, headers=headers).status_code == 409
    assert member.get(path).json["text"] == "Neuer Inhalt."

    with world.app_context():
        ClinicAuthorPoolMember.query.filter_by(pool_id="pool-a", user_id=2).delete()
        db.session.commit()
    assert member.get(BLOCKS).json["items"] == []
    assert member.get(path).status_code == 404


def test_personal_and_global_visibility_and_paging(world):
    owner, headers = browser(world, 2)
    admin, admin_headers = browser(world, 4)
    assert create(owner, headers, "global").status_code == 403
    assert create(owner, headers, "personal", user_id=3).status_code == 400
    personal = create(owner, headers, "personal")
    shared = create(admin, admin_headers, "global")
    assert personal.status_code == shared.status_code == 201

    first = owner.get(BLOCKS, query_string={"limit": 1}).json
    second = owner.get(BLOCKS, query_string={"limit": 1, "cursor": first["next_cursor"]}).json
    assert {first["items"][0]["id"], second["items"][0]["id"]} == {personal.json["id"], shared.json["id"]}
    assert second["next_cursor"] is None
    assert admin.get(f"{BLOCKS}/{personal.json['id']}").status_code == 404
    assert [row["id"] for row in admin.get(BLOCKS).json["items"]] == [shared.json["id"]]


def test_graphics_require_scoped_allowlist_and_current_read_access(world, tmp_path):
    manager, headers = browser(world, 1)
    member, _ = browser(world, 2)
    outsider, _ = browser(world, 3)
    data = png()
    object_id = hashlib.sha256(data).hexdigest()
    refs = [{"object_id": object_id, "alt_text": "Testgrafik"}]
    root = tmp_path / "clinic/text-block-graphics/v1/pool"
    foreign = root / "pool-b" / object_id
    foreign.parent.mkdir(parents=True)
    foreign.write_bytes(data)
    assert create(manager, headers, graphics=refs).status_code == 400
    local = root / "pool-a" / object_id
    local.parent.mkdir()
    local.symlink_to(foreign)
    assert create(manager, headers, graphics=refs).status_code == 400
    local.unlink()
    local.write_bytes(data)

    for bad in ("https://example.invalid/image.png", "../file", "data:image/png;base64,AA"):
        assert create(manager, headers, graphics=[{"object_id": bad, "alt_text": "Test"}]).status_code == 400
    created = create(manager, headers, graphics=refs)
    assert created.status_code == 201
    detail_path = f"{BLOCKS}/{created.json['id']}"
    assert member.get(detail_path).json["insert_text"] == "Synthetischer Text.\n[graphic: Testgrafik]"
    update = {"title": "Geändert", "text": "Darf nicht gespeichert werden.", "revision": created.json["revision"],
              "graphics": [{"object_id": "0" * 64, "alt_text": "Fehlt"}]}
    assert manager.put(detail_path, json=update, headers=headers).status_code == 400
    assert member.get(detail_path).json == created.json
    path = f"{detail_path}/graphics/{object_id}"
    served = member.get(path)
    assert served.status_code == 200 and served.data == data
    assert served.mimetype == "image/png" and served.cache_control.no_store
    assert served.headers["X-Content-Type-Options"] == "nosniff"
    assert outsider.get(path).status_code == 404
    with world.app_context():
        db.session.get(ClinicAuthorPool, "pool-a").active = False
        db.session.commit()
    assert member.get(path).status_code == 404
    assert member.get(BLOCKS).json["items"] == []


def test_session_csrf_and_resource_limits(world, monkeypatch):
    actor, headers = browser(world, 2)
    assert world.test_client().get(BLOCKS).status_code == 401
    assert create(actor, {}, "personal").status_code == 403
    assert create(actor, headers, "personal", recording_id=1).status_code == 400
    assert create(actor, headers, "personal", text="x" * 4001).status_code == 400
    assert create(actor, headers, "personal", text="x" * 32769).status_code == 413
    assert create(actor, headers, "personal", text="\ud800").status_code == 400
    assert actor.get(BLOCKS, query_string={"limit": "abc"}).status_code == 400
    assert actor.get(BLOCKS, query_string={"cursor": "invalid"}).status_code == 400
    with world.app_context():
        db.session.get(ClinicAccountPolicy, 2).status = "disabled"
        db.session.commit()
    assert actor.get(BLOCKS).status_code == 401
    monkeypatch.delenv("CLINIC_MODE")
    assert actor.get(BLOCKS).status_code == 404
