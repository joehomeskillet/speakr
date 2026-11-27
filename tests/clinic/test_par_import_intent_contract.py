"""Import-intent metadata contract: legacy rows survive a schema upgrade.

Nothing here is evidence for a device, a real agent or a foot pedal.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, inspect, text

from src.app import app, db
from src.clinic import import_centre as ic
from src.clinic.agent_channel import AgentChannelError
from src.clinic.migration_import_intent_metadata import (
    IntentMetadataError, encode_metadata, interpret, legacy_metadata, upgrade, downgrade,
)
from src.models import User

NOW = datetime(2026, 11, 18, 8, 0, 0, tzinfo=timezone.utc)
OLD_ID = "11111111-1111-4111-8111-111111111111"
NEW_ID = "22222222-2222-4222-8222-222222222222"


def _legacy_engine(path):
    engine = create_engine("sqlite:///" + path.as_posix())
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE clinic_import_intent ("
            "id VARCHAR(36) PRIMARY KEY, "
            "created_by_user_id INTEGER NOT NULL, "
            "source_kind VARCHAR(16) NOT NULL, "
            "source_ref VARCHAR(128) NOT NULL, "
            "expected_files TEXT NOT NULL, "
            "target_author_user_id INTEGER NOT NULL, "
            "target_pool_id VARCHAR(36), "
            "language_id INTEGER NOT NULL, "
            "status VARCHAR(16) NOT NULL, "
            "created_at DATETIME NOT NULL, "
            "expires_at DATETIME NOT NULL)"
        ))
        conn.execute(text(
            "INSERT INTO clinic_import_intent ("
            "id, created_by_user_id, source_kind, source_ref, expected_files, "
            "target_author_user_id, target_pool_id, language_id, status, created_at, expires_at"
            ") VALUES ("
            ":id, 7, 'device', 'usb-1', '[]', 7, NULL, 1031, 'OPEN', :created, :expires)"
        ), {"id": OLD_ID, "created": "2026-11-18 08:00:00", "expires": "2026-11-19 08:00:00"})
    return engine


def _row(engine, intent_id):
    with engine.connect() as conn:
        return conn.execute(text(
            "SELECT language_id, target_author_user_id, target_pool_id, status, "
            "metadata_version, metadata_json FROM clinic_import_intent WHERE id = :id"
        ), {"id": intent_id}).mappings().one()


def test_staging_upgrade_keeps_old_intent_and_reads_new_one(tmp_path):
    engine = _legacy_engine(tmp_path / "staging.db")
    assert upgrade(engine)
    assert upgrade(engine) == []
    old = _row(engine, OLD_ID)
    assert (old["language_id"], old["target_author_user_id"], old["target_pool_id"], old["status"]) == (
        1031, 7, None, "OPEN",
    )
    assert old["metadata_version"] is None
    assert interpret(old) == legacy_metadata()

    overrides = {
        "title": {"op": "keep"},
        "priority": {"op": "set", "value": 1},
        "recipient_user_id": {"op": "set", "value": 4},
        "pool_id": {"op": "set", "value": "pool-a"},
        "asr": "enable",
    }
    with pytest.raises(IntentMetadataError) as rejected:
        encode_metadata(overrides, approved_recipients=[9])
    assert rejected.value.code == "unapproved_recipient"
    version, blob = encode_metadata(overrides, approved_recipients=[4])
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO clinic_import_intent ("
            "id, created_by_user_id, source_kind, source_ref, expected_files, "
            "target_author_user_id, target_pool_id, language_id, status, created_at, expires_at, "
            "metadata_version, metadata_json"
            ") VALUES ("
            ":id, 7, 'device', 'usb-1', '[]', 7, NULL, 1031, 'OPEN', :created, :expires, "
            ":version, :blob)"
        ), {
            "id": NEW_ID, "created": "2026-11-18 08:00:00", "expires": "2026-11-19 08:00:00",
            "version": version, "blob": blob,
        })
    fresh = interpret(_row(engine, NEW_ID))
    assert fresh["schema_version"] == 1
    assert fresh["title"] == {"op": "keep"}
    assert fresh["priority"] == {"op": "set", "value": 1}
    assert fresh["recipient_user_id"] == {"op": "set", "value": 4}
    assert fresh["pool_id"] == {"op": "set", "value": "pool-a"}
    assert fresh["asr"] == "enable"
    assert interpret(_row(engine, OLD_ID)) == legacy_metadata()

    assert downgrade(engine)
    names = {column["name"] for column in inspect(engine).get_columns("clinic_import_intent")}
    assert "metadata_version" not in names
    assert upgrade(engine)
    assert interpret(_row(engine, OLD_ID)) == legacy_metadata()


def test_service_legacy_intent_and_unapproved_recipient():
    with app.app_context():
        ic.create_tables()
        user = User(
            username="p015" + uuid.uuid4().hex[:8], email=uuid.uuid4().hex[:8] + "@example.invalid",
            password="x", is_admin=True,
        )
        db.session.add(user)
        db.session.commit()
        legacy = ic.create_import_intent(
            actor_user_id=user.id, source_kind="device", source_ref="usb-1", now=NOW,
        )
        assert legacy["language_id"] == 1031
        assert legacy["target_author_user_id"] == user.id
        assert legacy["metadata"] == legacy_metadata()
        with pytest.raises(AgentChannelError) as err:
            ic.create_import_intent(
                actor_user_id=user.id, source_kind="device", source_ref="usb-1", now=NOW,
                overrides={"recipient_user_id": {"op": "set", "value": user.id}, "asr": "disable"},
            )
        assert err.value.code == "forbidden"
        explicit = ic.create_import_intent(
            actor_user_id=user.id, source_kind="device", source_ref="usb-2",
            now=NOW + timedelta(seconds=1), approved_recipients=(user.id,),
            overrides={
                "title": {"op": "keep"},
                "recipient_user_id": {"op": "set", "value": user.id},
                "asr": "disable",
            },
        )
        assert explicit["metadata"]["schema_version"] == 1
        assert explicit["metadata"]["title"] == {"op": "keep"}
        assert explicit["metadata"]["recipient_user_id"] == {"op": "set", "value": user.id}
        assert explicit["metadata"]["asr"] == "disable"
        listed = ic.list_import_intents(actor_user_id=user.id, now=NOW + timedelta(seconds=1))
        assert [row["metadata"]["schema_version"] for row in listed] == [0, 1]
