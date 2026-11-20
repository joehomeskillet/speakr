"""P09 reads through the real Flask app and synthetic import services."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.app import app, db
from src.clinic import import_centre, import_source_config, policy, pools
from src.clinic.config import clinic_mode_enabled
from src.clinic.identity import ClinicAccountPolicy
from src.models import GroupMembership

from test_import_centre import _data, _env as _env, _finalize, _upload, env as env


PREFIX = "/api/clinic/v1/imports/"
PATHS = ("intents", "inventory", "unassigned")
CLINIC_AT_STARTUP = clinic_mode_enabled()
clinic_routes = pytest.mark.skipif(not CLINIC_AT_STARTUP, reason="run with CLINIC_MODE=1 at app startup")
stock_routes = pytest.mark.skipif(CLINIC_AT_STARTUP, reason="run with CLINIC_MODE off at app startup")


def _client(user):
    client = app.test_client()
    with client.session_transaction() as session:
        session["_user_id"] = user.get_id()
        session["_fresh"] = True
        session["clinic_provider"] = "local"
        session["clinic_security_version"] = db.session.get(ClinicAccountPolicy, user.id).security_version
    return client


def _get(client, path):
    # The synthetic import fixture holds a DB context. Each request needs its own Flask g.
    with app.app_context():
        return client.get(PREFIX + path)


def _assert_problem(response, status):
    assert response.status_code == status
    assert response.mimetype == "application/problem+json"
    assert response.json["status"] == status
    assert response.cache_control.no_store


def _items(client, path):
    response = _get(client, path)
    assert response.status_code == 200
    assert response.cache_control.no_store
    assert response.json["count"] == len(response.json["items"])
    return response.json["items"]


@pytest.fixture
def scene(env, monkeypatch):
    admin, author, lead = (env[key] for key in ("admin", "author", "other"))
    admin.is_admin = True
    db.session.add_all([ClinicAccountPolicy(user_id=user.id, status="active") for user in (admin, author, lead)])
    db.session.commit()
    author_pool = pools.create_author_pool("Synthetic authors", actor_id=admin.id)
    staff_pool = pools.create_staff_pool(f"Synthetic typists {admin.username}", actor_id=admin.id)
    pools.link_staff_pool(author_pool.id, staff_pool.id, actor_id=admin.id)
    membership = GroupMembership(group_id=staff_pool.group_id, user_id=lead.id, role="admin")
    db.session.add(membership)
    db.session.commit()
    monkeypatch.setenv("CLINIC_IMPORT_ROOT_ALLOWLIST", "/synthetic-imports")
    import_source_config.create_source_config(
        actor_user_id=admin.id, source_kind="device", source_ref="usb-1", approved_root="/synthetic-imports",
        target_author_user_id=author.id, target_pool_id=author_pool.id,
        may_assign_author=lambda actor, target: (actor, target) == (admin.id, author.id),
    )
    for root in ("usb-1", "usb-2"):
        _finalize(env, *_upload(env, _data(root), root=root)[:2])
    return {**env, "lead_membership": membership}


@clinic_routes
@pytest.mark.parametrize("path", PATHS)
def test_real_app_requires_browser_session_and_import_rights(path, env):
    _assert_problem(_get(app.test_client(), path), 401)
    author = env["author"]
    db.session.add(ClinicAccountPolicy(user_id=author.id, status="active"))
    db.session.commit()
    _assert_problem(_get(_client(author), path), 403)


@clinic_routes
def test_admin_reads_own_intents_inventory_and_clarification(scene):
    admin, author = scene["admin"], scene["author"]
    policy.register_grant_provider(lambda uid: [policy.Grant("own", frozenset({"import"}))] if uid == author.id else [])
    own = import_centre.create_import_intent(actor_user_id=admin.id, source_kind="device", source_ref="usb-1")
    other = import_centre.create_import_intent(actor_user_id=author.id, source_kind="device", source_ref="usb-1")
    client = _client(admin)
    assert _items(client, "intents") == [own]
    author_client = _client(author)
    assert _items(author_client, "intents") == [other]
    assert [row["root_id"] for row in _items(author_client, "inventory")] == ["usb-1"]
    _assert_problem(_get(author_client, "unassigned"), 403)
    inventory = _items(client, "inventory")
    deliveries = _items(client, "unassigned")
    assert {row["root_id"] for row in inventory} == {"usb-1", "usb-2"}
    assert {row["root_id"] for row in deliveries} == {"usb-1", "usb-2"}
    assert {row["snapshot_version"] for row in inventory} == {row["snapshot_version"] for row in deliveries}
    assert all(row["relative_path"] == "a/dict1.ds2" for row in inventory + deliveries)
    assert all("storage_key" not in row for row in deliveries)


@clinic_routes
def test_pool_lead_only_reads_its_configured_source_and_loses_access_on_demotion(scene):
    lead = scene["other"]
    client = _client(lead)
    for path in ("inventory", "unassigned"):
        assert [row["root_id"] for row in _items(client, path)] == ["usb-1"]

    scene["lead_membership"].role = "member"
    db.session.commit()
    for path in ("inventory", "unassigned"):
        _assert_problem(_get(client, path), 403)


@stock_routes
@pytest.mark.parametrize("path", PATHS)
def test_import_routes_are_absent_with_clinic_mode_off(path):
    assert app.test_client().get(PREFIX + path).status_code == 404


@stock_routes
def test_clinic_routes_in_a_fresh_app_process():
    # The stock app is already imported during collection. Start over with an isolated test DB.
    result = subprocess.run(
        [sys.executable, "-m", "pytest", __file__, "-q", "-p", "no:cacheprovider"],
        cwd=Path(__file__).resolve().parents[2],
        env={**os.environ, "CLINIC_MODE": "1", "PYTEST_DATABASE_URI": ""},
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@clinic_routes
def test_ambiguous_registered_source_is_visible_only_for_admin_clarification(scene):
    from src.clinic import agent_channel

    lead, admin = scene["other"], scene["admin"]
    peer = agent_channel.register_agent(scene["policy"], actor_user_id=admin.id, display_name="Second lab station",
                                        allowed_sources=["usb-1"], credential_fingerprint="bb" * 20)
    other_machine = {**scene, "agent_id": peer["agent_id"], "principal": agent_channel.authenticate_agent("bb" * 20)}
    ingest_id, body, _ = _upload(other_machine, _data("replacement-station"))
    old = _finalize(other_machine, ingest_id, body)
    delivery_id = old["import"]["delivery_id"]

    def roots(user, path):
        return {row["root_id"] for row in _items(_client(user), path)}

    agent_channel.set_agent_enabled(scene["policy"], actor_user_id=admin.id, agent_id=scene["agent_id"], enabled=False)
    assert old["import"]["status"] == "UNASSIGNED"
    assert _finalize(other_machine, ingest_id, body)["import"] == old["import"]
    assert delivery_id in {row["id"] for row in _items(_client(admin), "unassigned")}
    assert delivery_id not in {row["id"] for row in _items(_client(lead), "unassigned")}
    agent_channel.set_agent_enabled(scene["policy"], actor_user_id=admin.id, agent_id=scene["agent_id"], enabled=True)
    for path in ("inventory", "unassigned"):
        assert "usb-1" not in roots(lead, path)
        assert "usb-1" in roots(admin, path)
    agent_channel.set_agent_enabled(scene["policy"], actor_user_id=admin.id, agent_id=peer["agent_id"], enabled=False)
    for path in ("inventory", "unassigned"):
        assert "usb-1" not in roots(lead, path)
        assert "usb-1" in roots(admin, path)
