"""Exercise the cleanup policy and receipts with synthetic inventory and stored bytes."""

import os
import subprocess
import sys
from datetime import timedelta
from uuid import uuid4

from src.app import app, db
from src.clinic import agent_channel, import_source_config, policy
from src.clinic.config import clinic_mode_enabled, dev_defaults
from src.clinic.identity import ClinicAccountPolicy
from src.clinic.models import ClinicOriginal
from src.clinic.parity_receipts import ClinicParityReceipt

from test_import_centre import NOW, _data, _env as _env, _finalize, _intent, _upload, env as env
from test_wp_v8_import_routes import _client


PREFIX = "/api/clinic/v1/imports/"


def _call(client, method, path, body, *, key=None, csrf=True):
    headers = {"Idempotency-Key": key or str(uuid4())}
    if csrf:
        with app.app_context():
            headers["X-CSRF-Token"] = client.get("/api/csrf-token").json["csrf_token"]
    with app.app_context():
        return client.open(PREFIX + path, method=method, json=body, headers=headers)


def test_manual_source_cleanup_is_a_bound_non_deleting_decision(env, monkeypatch, request):
    if not clinic_mode_enabled():
        assert _call(app.test_client(), "POST", "source-cleanup", {}, csrf=False).status_code == 404
        result = subprocess.run(
            [sys.executable, "-m", "pytest", request.node.nodeid, "-q", "-p", "no:cacheprovider", "--timeout=120"],
            env={**os.environ, "CLINIC_MODE": "1", "PYTEST_DATABASE_URI": ""},
            capture_output=True, text=True, timeout=110, check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return

    admin, operator, outsider = (env[name] for name in ("admin", "author", "other"))
    admin.is_admin = True
    db.session.add_all(ClinicAccountPolicy(user_id=user.id, status="active") for user in (admin, operator, outsider))
    db.session.commit()
    policy.register_grant_provider(lambda uid: {
        operator.id: [policy.Grant("author", frozenset({"clinic.import.cleanup"}), frozenset({admin.id})),
                      policy.Grant("own", frozenset({"manage_import_sources", "clinic.import.cleanup"}))],
        outsider.id: [policy.Grant("own", frozenset({"manage_import_sources"}))],
    }.get(uid, []))
    monkeypatch.setenv("CLINIC_IMPORT_ROOT_ALLOWLIST", "/synthetic-imports;/synthetic-backups")
    monkeypatch.setitem(dev_defaults()["features"], "device_delete_runtime_enabled", False)
    monkeypatch.setitem(dev_defaults()["privacy"], "retention_policy_approved", False)

    def approve_source(user, root):
        return import_source_config.create_source_config(
            actor_user_id=user.id, source_kind="device", source_ref=root, approved_root="/synthetic-imports",
            target_author_user_id=user.id, backup_root="/synthetic-backups", backup_enabled=True,
            backup_retention_days=30,
        )

    config = approve_source(admin, "usb-1")
    delegated = approve_source(operator, "usb-2")
    ordinary = approve_source(outsider, "usb-3")
    admin_client, operator_client, outsider_client = (_client(user) for user in (admin, operator, outsider))

    def enable(client, source):
        return _call(client, "PUT", f"sources/{source['id']}", {"expected_version": 1, "allow_source_delete": True})

    assert config["allow_source_delete"] is False
    assert enable(admin_client, config).status_code == 403
    monkeypatch.setitem(dev_defaults()["features"], "device_delete_runtime_enabled", True)
    assert enable(outsider_client, ordinary).status_code == 403
    assert enable(operator_client, delegated).json["allow_source_delete"] is True

    data = _data("stored")
    _intent(env, actor_user_id=admin.id)
    ingest_id, finalize_body, _ = _upload(env, data)
    ingest = db.session.get(agent_channel.ClinicAgentIngest, ingest_id)
    item = agent_channel.inventory_row(env["agent_id"], ingest.source_id, ingest.snapshot_version)
    target = {"agent_id": env["agent_id"], "source_snapshot_id": item.id, "receipt_id": ingest_id}

    def preview(client=operator_client, **changes):
        return _call(client, "POST", "source-cleanup/preview", target | changes)

    def submit(version=2, *, key=None, **changes):
        return _call(operator_client, "POST", "source-cleanup",
                     target | {"expected_revision": version, "confirmation": True} | changes, key=key)

    assert preview(outsider_client).status_code == 403
    assert preview().json["code"] == "SOURCE_DELETE_DISABLED"
    enabled = enable(admin_client, config)
    assert enabled.status_code == 200
    assert enabled.json["allow_source_delete"] is True
    assert preview().json["code"] == "BACKUP_RETENTION_NOT_READY"
    monkeypatch.setitem(dev_defaults()["privacy"], "retention_policy_approved", True)
    assert preview().json["code"] == "STORED_RECEIPT_REQUIRED"
    denied = submit()
    assert denied.status_code == 409
    assert denied.json["status"] == "DENIED"
    assert denied.json["code"] == "STORED_RECEIPT_REQUIRED"

    stored = _finalize(env, ingest_id, finalize_body)
    assert stored["state"] == "STORED" and stored["dictation_id"]
    original = db.session.get(ClinicOriginal, stored["original_id"])
    ingest.dictation_id = None
    db.session.commit()
    assert submit().json["code"] == "STORED_RECEIPT_REQUIRED"
    ingest.dictation_id = stored["dictation_id"]
    db.session.commit()
    confirmed = target | {"expected_revision": 2, "confirmation": True}
    assert _call(operator_client, "POST", "source-cleanup", confirmed, csrf=False).status_code in (400, 403)
    assert submit(confirmation=1).status_code == 400
    assert submit(version=1).json["code"] == "POLICY_CHANGED"
    assert preview(receipt_id=str(uuid4())).json["code"] == "STORED_RECEIPT_REQUIRED"
    eligible = preview()
    assert eligible.json["status"] == "ELIGIBLE", eligible.json
    route = PREFIX + "inventory"
    def get(client, path):
        with app.app_context():
            return client.get(path)

    rows = get(admin_client, route).json["items"]
    assert next(row for row in rows if row["snapshot_id"] == item.id)["cleanup"] == {
        "receipt_id": ingest_id, "policy_version": 2,
    }
    assert get(outsider_client, route).status_code == 403
    configs = get(admin_client, PREFIX + "sources").json["items"]
    assert next(row for row in configs if row["id"] == config["id"])["can_set_source_delete"] is True
    configs = get(outsider_client, PREFIX + "sources").json["items"]
    assert next(row for row in configs if row["id"] == ordinary["id"])["can_set_source_delete"] is False
    key = str(uuid4())
    accepted = submit(key=key)
    assert accepted.status_code == 200
    assert accepted.json["status"] == "ACCEPTED"
    assert accepted.json["action"] == "delete"
    assert accepted.json["snapshot_version"] == item.snapshot_version
    assert accepted.json["receipt_id"] == ingest_id
    assert submit(key=key).json == accepted.json
    assert submit(key=key, receipt_id=str(uuid4())).status_code == 409
    monkeypatch.setitem(dev_defaults()["features"], "device_delete_runtime_enabled", False)
    assert submit().json["code"] == "SOURCE_DELETE_DISABLED"
    monkeypatch.setitem(dev_defaults()["features"], "device_delete_runtime_enabled", True)

    report = dict(source_id=item.source_id, root_id=item.root_id, relative_path=item.relative_path,
                  size=item.size, sha256=item.sha256, mtime_utc="2026-11-18T07:00:01+00:00",
                  snapshot_version="new-version")
    agent_channel.report_inventory(env["principal"], env["policy"], {"items": [report]}, now=NOW + timedelta(seconds=1))
    assert preview().json["code"] == "SNAPSHOT_CHANGED"
    assert all("cleanup" not in row for row in get(admin_client, route).json["items"])
    changed = submit()
    assert changed.status_code == 409 and changed.json["code"] == "SNAPSHOT_CHANGED"
    current = agent_channel.inventory_row(env["agent_id"], item.source_id, report["snapshot_version"])
    assert preview(source_snapshot_id=current.id).json["code"] == "RECEIPT_NOT_BOUND"

    results = [row.result for row in ClinicParityReceipt.query.all()]
    assert all(response.json in results for response in (denied, accepted, changed))
    assert agent_channel.ClinicAgentIntent.query.one().post_step == "none"
    assert env["service"].file_path(original.storage_key).read_bytes() == data
