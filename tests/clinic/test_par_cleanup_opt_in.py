"""Exercise the deployed machine cleanup policy with synthetic, non-deleting imports."""

import pytest
from flask import Flask

from src.app import app, db, register_clinic_operations
from src.clinic import agent_channel as channel, agent_channel_ingest, import_centre, import_source_config, routes_agent
from src.clinic.config import dev_defaults
from src.clinic.models import ClinicOriginal

from test_import_centre import NOW, _data, _env as _env, _intent, _upload, env as env
from test_wp_v8_agent_routes import FINGERPRINT, PROXY, _headers


@pytest.mark.parametrize("deployment,source,backup,post_step,permitted", [
    (False, True, True, "delete", False),
    (True, False, True, "delete", False),
    (True, True, False, "delete", False),
    (True, True, None, "delete", False),
    (True, True, "true", "delete", False),
    (True, True, True, "none", False),
    (True, True, True, "delete", True),
])
def test_machine_receipt_requires_all_cleanup_opt_ins(
    env, monkeypatch, tmp_path, deployment, source, backup, post_step, permitted,
):
    monkeypatch.setenv("CLINIC_MODE", "1")
    monkeypatch.setenv("CLINIC_AGENT_PROXY_SECRET", PROXY)
    monkeypatch.setenv("CLINIC_AGENT_MASTER_SECRET", "synthetic-agent-test-key" * 2)
    monkeypatch.setenv("CLINIC_ORIGINALS_ROOT", str(tmp_path / "machine-store"))
    monkeypatch.setenv("CLINIC_IMPORT_ROOT_ALLOWLIST", "/synthetic-imports;/synthetic-backups")
    monkeypatch.setitem(dev_defaults()["features"], "device_delete_runtime_enabled", deployment)
    monkeypatch.setitem(dev_defaults()["privacy"], "retention_policy_approved", True)
    monkeypatch.setattr(agent_channel_ingest, "utcnow", lambda: NOW)
    monkeypatch.setattr(import_centre, "utcnow", lambda: NOW)

    operator = env["admin"]
    operator.is_admin = True
    db.session.commit()
    config = import_source_config.create_source_config(
        actor_user_id=operator.id, source_kind="device", source_ref="usb-1", approved_root="/synthetic-imports",
        target_author_user_id=operator.id, allow_source_delete=source, may_manage_deletion=lambda _: True,
        backup_root="/synthetic-backups", backup_enabled=True, backup_retention_days=30,
    )
    _intent(env, actor_user_id=operator.id)
    data = _data("cleanup fixture")
    ingest_id, body, begin_key = _upload(env, data)
    ingest = db.session.get(channel.ClinicAgentIngest, ingest_id)
    db.session.get(channel.ClinicAgentIntent, ingest.intent_id).post_step = post_step
    db.session.commit()

    flask = Flask(__name__)
    flask.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI=app.config["SQLALCHEMY_DATABASE_URI"],
                        SQLALCHEMY_ENGINE_OPTIONS=app.config.get("SQLALCHEMY_ENGINE_OPTIONS", {}))
    flask.extensions["clinic_run_once"] = None
    db.init_app(flask)
    register_clinic_operations(flask)
    client = flask.test_client()
    path = f"{routes_agent.PREFIX}/ingests/{ingest_id}"
    headers = _headers(fingerprint=FINGERPRINT, **{"Idempotency-Key": "cleanup-finalize"})
    if backup is not None:
        body["backup_verified"] = backup
    response = client.post(path + "/finalize", json=body, headers=headers)

    assert response.status_code == 200, response.json
    receipt = response.json
    assert receipt["state"] == "STORED" and receipt["dictation_id"]
    assert receipt["deletion_permitted"] is permitted
    assert receipt["permitted_post_step"] == ("delete" if permitted else "none")
    if permitted:
        ingest = db.session.get(channel.ClinicAgentIngest, ingest_id, populate_existing=True)
        dictation_id = ingest.dictation_id
        ingest.dictation_id = None
        db.session.commit()
        assert client.get(path, headers=headers).json["deletion_permitted"] is False
        ingest.dictation_id = dictation_id
        item = channel.inventory_row(env["agent_id"], ingest.source_id, ingest.snapshot_version)
        relative_path = item.relative_path
        item.relative_path = "different/dict1.ds2"
        db.session.commit()
        assert client.get(path, headers=headers).json["deletion_permitted"] is False
        item.relative_path = relative_path
        db.session.commit()
        import_source_config.update_source_config(
            actor_user_id=operator.id, config_id=config["id"], expected_version=1, allow_source_delete=False,
        )
        assert client.get(path, headers=headers).json["deletion_permitted"] is False
        assert client.post(path + "/finalize", json=body, headers=headers).json["deletion_permitted"] is False
        replay_body = {"intent_id": ingest.intent_id, "declared_size": len(data), "declared_sha256": ingest.declared_sha256}
        replay_headers = headers | {"Idempotency-Key": begin_key}
        replay = client.post(f"{routes_agent.PREFIX}/ingests", json=replay_body, headers=replay_headers)
        assert replay.status_code == 201 and replay.json["deletion_permitted"] is False

    original = db.session.get(ClinicOriginal, receipt["original_id"])
    assert (tmp_path / "machine-store" / original.storage_key).read_bytes() == data
