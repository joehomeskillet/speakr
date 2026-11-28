import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import uuid4

import pytest

from src.app import app, db
from src.clinic import agent_channel as channel
from src.clinic import agent_channel_ingest, import_centre, import_source_config, policy, pools
from src.clinic.identity import ClinicAccountPolicy
from src.models import AuditEvent, Group, GroupMembership

from test_import_centre import _env as _env, _sha, env as env
from test_stub_asr import FIXTURE
from test_wp_v8_import_routes import _client

PREFIX = "/api/clinic/v1/imports/"


def _call(client, path, *, body=None, key="synthetic-send-1", csrf=True):
    with app.app_context():
        if body is None:
            return client.get(PREFIX + path)
        headers = {"Idempotency-Key": key}
        if csrf:
            headers["X-CSRF-Token"] = client.get("/api/csrf-token").json["csrf_token"]
        return client.post(PREFIX + path, json=body, headers=headers)


@pytest.fixture
def scene(env, monkeypatch):
    admin, author, other = (env[k] for k in ("admin", "author", "other"))
    admin.is_admin = True
    db.session.add_all(ClinicAccountPolicy(user_id=u.id, status="active") for u in (admin, author, other))
    db.session.commit()
    monkeypatch.setenv("CLINIC_IMPORT_ROOT_ALLOWLIST", "/synthetic-imports")
    import_source_config.create_source_config(
        actor_user_id=admin.id, source_kind="device", source_ref="usb-1", approved_root="/synthetic-imports",
        target_author_user_id=author.id, may_assign_author=lambda *_: True,
    )
    data = FIXTURE.read_bytes()
    channel.report_inventory(env["principal"], env["policy"], {"items": [
        dict(source_id=f"source-{i}", root_id="usb-1", relative_path=f"file-{i}.wav", size=len(data),
             sha256=_sha(data), mtime_utc="2026-11-27T09:00:00Z", snapshot_version="v1")
        for i in range(3)
    ]})
    client = _client(admin)
    rows = _call(client, "inventory").json["items"]
    assert len(rows) == 3
    assert all(row["agent_id"] == env["agent_id"] for row in rows)
    return client, rows, data


def _verify_batches(env, scene):
    client, rows, _ = scene
    batch = dict(agent_id=env["agent_id"], source_snapshot_ids=[row["snapshot_id"] for row in rows[:2]],
                 overrides={"title": {"op": "keep"}, "asr": "disable"})
    assert _call(app.test_client(), "device-send", body=batch).status_code == 401
    assert _call(_client(env["author"]), "device-send", body=batch).status_code == 403
    assert _call(client, "device-send", body=batch, csrf=False).status_code == 400
    assert _call(client, "device-send", body=batch, key="").status_code == 400
    assert import_centre.ClinicImportIntent.query.count() == channel.ClinicAgentIntent.query.count() == 0

    response = _call(client, "device-send", body=batch)
    assert response.status_code == 200
    assert response.cache_control.no_store
    results = response.json["items"]
    assert [item["snapshot_id"] for item in results] == batch["source_snapshot_ids"]
    assert [item["status"] for item in results] == ["OPEN", "OPEN"]
    ids = {item["intent_id"] for item in results}
    clinic = import_centre.ClinicImportIntent.query.all()
    machine = channel.ClinicAgentIntent.query.all()
    assert {row.id for row in clinic} == {row.id for row in machine} == ids
    assert all(row.status == "OPEN" for row in clinic + machine)
    assert {row.source_id for row in machine} == {row["source_id"] for row in rows[:2]}
    assert all(row.target_author_user_id == env["author"].id for row in clinic)
    assert all(row.post_step == "none" for row in machine)
    assert all(row["metadata"]["asr"] == "disable" for row in _call(client, "intents").json["items"])
    assert _call(client, "device-send", body=batch).json == response.json
    assert _call(client, "device-send", body=batch | {"overrides": {}}).json["code"] == "IDEMPOTENCY_CONFLICT"
    assert AuditEvent.query.filter_by(event="recording.edited").count() == 2

    policy.register_grant_provider(lambda uid: [policy.Grant("own", frozenset({"import"}))]
                                   if uid == env["other"].id else [])
    mixed = batch | {"source_snapshot_ids": [rows[0]["snapshot_id"], str(uuid4()), rows[2]["snapshot_id"]]}
    hidden = _call(_client(env["other"]), "intents", body=mixed)
    assert [item["code"] for item in hidden.json["items"]] == ["NOT_FOUND"] * 3
    partial = _call(client, "intents", body=mixed)
    assert partial.status_code == 200
    assert [(item["status"], item.get("code")) for item in partial.json["items"]] == [
        ("REJECTED", "CONFLICT"), ("REJECTED", "NOT_FOUND"), ("OPEN", None),
    ]
    assert import_centre.ClinicImportIntent.query.count() == channel.ClinicAgentIntent.query.count() == 3


def _verify_recipients(env, scene):
    client, rows, _ = scene
    admin, author, typist = (env[key] for key in ("admin", "author", "other"))
    typist.name = "Synthetischer Schreibdienst"
    author_pool = pools.ClinicAuthorPool(name="Geräteversand", created_by=admin.id)
    group = Group(name="Schreibdienst", created_by=admin.id)
    db.session.add_all([author_pool, group])
    db.session.flush()
    staff = pools.ClinicStaffPool(group_id=group.id, created_by=admin.id)
    db.session.add_all([staff, GroupMembership(group_id=group.id, user_id=typist.id, role="member")])
    db.session.flush()
    db.session.add(pools.ClinicPoolRoute(author_pool_id=author_pool.id, staff_pool_id=staff.id, created_by=admin.id))
    db.session.commit()
    pools.add_author_member(author_pool.id, author.id, actor_id=admin.id)

    inventory = _call(client, "inventory").json
    assert inventory["recipients"] == [{"id": typist.id, "name": typist.name}]
    assert [item["device_recipient_ids"] for item in inventory["items"]] == [[typist.id]] * 3
    assert _call(_client(typist), "inventory").status_code == 403
    batch = dict(agent_id=env["agent_id"], source_snapshot_ids=[row["snapshot_id"] for row in rows[:2]],
                 overrides={"recipient_user_id": {"op": "set", "value": typist.id}, "asr": "disable"})
    assert [item["status"] for item in _call(client, "device-send", body=batch).json["items"]] == ["OPEN"] * 2
    queued = _call(client, "intents").json["items"]
    assert {item["source_ref"] for item in queued} == {row["source_id"] for row in rows[:2]}
    assert [item["metadata"]["recipient_user_id"] for item in queued] == [{"op": "set", "value": typist.id}] * 2
    assert [item["metadata"]["asr"] for item in queued] == ["disable"] * 2


def _verify_retry(env, scene):
    client, rows, data = scene
    batch = dict(agent_id=env["agent_id"], source_snapshot_ids=[rows[0]["snapshot_id"]])
    queued = _call(client, "intents", body=batch)
    intent_id = queued.json["items"][0]["intent_id"]
    path = f"intents/{intent_id}/retry"
    assert _call(client, path, body={}).status_code == 409
    wire, = channel.poll_intents(env["principal"], env["policy"])["intents"]
    key = channel.derive_agent_key(env["policy"], env["agent_id"], 1)
    assert channel.verify_intent_wire(key, wire, expected_agent_id=env["agent_id"], now=channel.utcnow())
    channel_outcome = agent_channel_ingest.record_outcome(env["principal"], intent_id=intent_id, outcome="conflict")
    assert channel_outcome["recorded"]
    assert _call(client, "intents").json["items"][0]["status"] == "FAILED"
    assert _call(client, path, body={}, key="synthetic-retry-failed").json["status"] == "OPEN"
    begin = dict(intent_id=intent_id, declared_size=len(data), declared_sha256=_sha(data), idempotency_key="device-begin")
    receipt = agent_channel_ingest.begin_ingest(env["principal"], env["policy"], **begin)
    clinic = db.session.get(import_centre.ClinicImportIntent, intent_id)
    machine = db.session.get(channel.ClinicAgentIntent, intent_id)
    ingest = db.session.get(channel.ClinicAgentIngest, receipt["ingest_id"])
    now = channel.utcnow()
    clinic.expires_at = machine.expires_at = ingest.expires_at = now - timedelta(seconds=1)
    db.session.commit()
    assert _call(client, "intents").json["items"][0]["status"] == "EXPIRED"
    policy.register_grant_provider(lambda uid: [policy.Grant("own", frozenset({"import"}))]
                                   if uid == env["author"].id else [])
    assert _call(_client(env["author"]), path, body={}).status_code == 404
    retry = _call(client, path, body={})
    assert retry.json == {"intent_id": intent_id, "status": "OPEN"}
    assert _call(client, path, body={}).json == retry.json
    assert _call(client, path, body={}, key="synthetic-retry-2").status_code == 409
    assert agent_channel_ingest.begin_ingest(env["principal"], env["policy"], **begin)["ingest_id"] == ingest.id
    agent_channel_ingest.put_chunk(env["principal"], env["policy"], ingest_id=ingest.id,
                                  sequence=0, offset=0, data=data, chunk_sha256=_sha(data))
    assert import_centre.ClinicImportIntent.query.count() == channel.ClinicAgentIntent.query.count() == 1
    assert AuditEvent.query.filter_by(event="recording.edited").count() == 3
    db.session.add(import_centre.ClinicImportIntent(
        source_kind="device", source_ref="usb-1", created_by_user_id=env["admin"].id,
        target_author_user_id=env["author"].id, created_at=now - timedelta(hours=1), expires_at=clinic.expires_at,
    ))
    db.session.commit()
    result = import_centre.finalize_delivery(
        env["principal"], env["policy"], ingest_id=ingest.id, ingest_service=env["service"],
        body={"chunks": [{"sequence": 0, "bytes": len(data), "sha256": _sha(data)}],
              "total_bytes": len(data), "sha256": _sha(data)},
    )
    assert result["state"] == "STORED"
    assert result["import"]["intent_id"] == intent_id
    assert _call(client, path, body={}, key="synthetic-retry-stored").status_code == 409


def _verify_concurrent(env, scene):
    _, rows, _ = scene
    clients = [_client(env["admin"]) for _ in range(2)]
    batch = dict(agent_id=env["agent_id"], source_snapshot_ids=[rows[0]["snapshot_id"]])
    start = Barrier(2, timeout=10)

    def send(client, key):
        start.wait()
        return _call(client, "device-send", body=batch, key=key)

    with ThreadPoolExecutor(2) as executor:
        results = list(executor.map(send, clients, ("concurrent-1", "concurrent-2")))
    assert [response.status_code for response in results] == [200, 200]
    assert sorted(response.json["items"][0]["status"] for response in results) == ["OPEN", "REJECTED"]
    assert import_centre.ClinicImportIntent.query.count() == channel.ClinicAgentIntent.query.count() == 1


@pytest.mark.parametrize("verify", [_verify_batches, _verify_recipients, _verify_retry, _verify_concurrent])
def test_device_send(env, request, verify):
    if "clinic_imports_web.device_send" not in app.view_functions:
        for path in ("device-send", "intents", f"intents/{uuid4()}/retry"):
            assert _call(app.test_client(), path, body={}, csrf=False).status_code == 404
        result = subprocess.run(
            [sys.executable, "-m", "pytest", request.node.nodeid, "-q", "-p", "no:cacheprovider", "--timeout=120"],
            env={**os.environ, "CLINIC_MODE": "1", "PYTEST_DATABASE_URI": ""},
            capture_output=True, text=True, timeout=110, check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return
    verify(env, request.getfixturevalue("scene"))
