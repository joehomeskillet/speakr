"""Inventory hint schema for PAR-IMP-004.

Pre-change rows stay unknown. A stable file is not Philips EOL. Synthetic marks
only: nothing here is evidence for a Philips device.
"""

import hashlib
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, inspect, text

from src.app import app, db
from src.clinic import agent_channel as ch
from src.clinic.agent_channel import ChannelPolicy
from src.clinic.migration_inventory_hints import (
    PHILIPS_EOL,
    downgrade,
    normalize_hints,
    replay_hints,
    upgrade,
)
from src.models import User

NOW = datetime(2026, 11, 17, 8, 0, 0, tzinfo=timezone.utc)
SECRET = b"s" * 32
SHA = "ab" * 32

_OLD_TABLE = """
CREATE TABLE clinic_agent_inventory (
    id VARCHAR(36) PRIMARY KEY,
    agent_id VARCHAR(36) NOT NULL,
    source_id VARCHAR(128) NOT NULL,
    root_id VARCHAR(128) NOT NULL,
    relative_path VARCHAR(1024) NOT NULL,
    size BIGINT NOT NULL,
    mtime_utc DATETIME NOT NULL,
    sha256 VARCHAR(64) NOT NULL,
    snapshot_version VARCHAR(128) NOT NULL,
    reported_at DATETIME NOT NULL
)
"""
_INSERT_OLD = """
INSERT INTO clinic_agent_inventory (
    id, agent_id, source_id, root_id, relative_path, size, mtime_utc, sha256, snapshot_version, reported_at
) VALUES (
    'old', 'agent', 'src', 'usb', 'a.dss', 100, '2026-11-17 07:00:00', :sha, 'v-old', '2026-11-17 08:00:00'
)
"""
_INSERT_MARKED = """
INSERT INTO clinic_agent_inventory (
    id, agent_id, source_id, root_id, relative_path, size, mtime_utc, sha256, snapshot_version, reported_at,
    claimed_author_code, completion_source, evidence_level
) VALUES (
    'new', 'agent', 'src', 'usb', 'b.dss', 100, '2026-11-17 07:00:00', :sha, 'v-new', '2026-11-17 08:00:00',
    :author, :source, :evidence
)
"""
_READ = """
SELECT snapshot_version, claimed_author_code, completion_source, evidence_level, size, sha256
FROM clinic_agent_inventory
"""


def test_upgrade_keeps_old_rows_unknown_and_stores_explicit_marks(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "pre.db"))
    with engine.begin() as connection:
        connection.execute(text(_OLD_TABLE))
        connection.execute(text(_INSERT_OLD), {"sha": SHA})
    ran = upgrade(engine)
    assert len(ran) == 5  # three columns, then the two unknown sentinels
    assert upgrade(engine) == []
    marked = normalize_hints({
        "claimed_author_code": "QA-A", "completion_source": "philips_eol", "evidence_level": "fixture",
    })
    with engine.begin() as connection:
        connection.execute(text(_INSERT_MARKED), {
            "sha": "cd" * 32, "author": marked["claimed_author_code"],
            "source": marked["completion_source"], "evidence": marked["evidence_level"],
        })
        rows = {row.snapshot_version: row for row in connection.execute(text(_READ))}
    old, new = rows["v-old"], rows["v-new"]
    assert (old.claimed_author_code, old.completion_source, old.evidence_level) == (None, "unknown", "unverified")
    assert old.size == 100 and old.sha256 == SHA
    assert old.completion_source != PHILIPS_EOL
    assert (new.claimed_author_code, new.completion_source, new.evidence_level) == ("QA-A", "philips_eol", "fixture")
    stable = {
        "claimed_author_code": None, "completion_source": "unknown", "evidence_level": "unverified",
        "size": old.size, "mtime_utc": "2026-11-17 07:00:00", "sha256": old.sha256,
    }
    replayed = replay_hints(stable, {
        **stable, "observed_completion": "completed", "completion_source": "file_stability",
        "evidence_level": "observed",
    })
    sneaky = replay_hints(stable, {
        **stable, "completion_source": "philips_eol", "file_stable": True, "basis": "mtime",
        "observed_completion": "completed", "evidence_level": "validated",
    })
    missing = replay_hints(stable, {})
    unknown = normalize_hints({
        "completion_source": "unknown", "evidence_level": "unknown", "claimed_author_code": "",
    })
    assert replayed["completion_source"] == "file_stability"
    assert sneaky["completion_source"] != PHILIPS_EOL
    assert missing == {"claimed_author_code": None, "completion_source": "unknown", "evidence_level": "unverified"}
    assert unknown == missing
    assert len(downgrade(engine)) == 3
    assert not {"claimed_author_code", "completion_source", "evidence_level"} & {
        column["name"] for column in inspect(engine).get_columns("clinic_agent_inventory")
    }
    with engine.connect() as connection:
        kept = connection.execute(text("SELECT snapshot_version, size, sha256 FROM clinic_agent_inventory")).all()
    assert {row.snapshot_version: (row.size, row.sha256) for row in kept} == {
        "v-old": (100, SHA), "v-new": (100, "cd" * 32),
    }
    assert downgrade(engine) == []


@pytest.fixture(autouse=True)
def _tables():
    with app.app_context():
        ch.create_tables()
        yield


@pytest.fixture
def user(_tables):
    row = User(username="hint" + uuid.uuid4().hex[:8], email=uuid.uuid4().hex[:8] + "@example.invalid", password="x")
    db.session.add(row)
    db.session.commit()
    return row


def _item(snapshot="v1"):
    return {
        "source_id": "src-1", "root_id": "usb-1", "relative_path": "a/b.dss", "size": 4,
        "mtime_utc": "2026-11-17T07:00:00+00:00", "sha256": hashlib.sha256(b"data").hexdigest(),
        "snapshot_version": snapshot,
    }


def test_channel_replay_does_not_turn_file_stability_into_philips_eol(user):
    with app.app_context():
        ch.create_tables()
        policy = ChannelPolicy(master_secret=SECRET, authorise=lambda actor, action: True)
        reg = ch.register_agent(
            policy, actor_user_id=user.id, display_name="Agent", allowed_sources=["usb-1"],
            credential_fingerprint="ee" * 20,
        )
        principal = ch.authenticate_agent("ee" * 20)
        ch.report_inventory(principal, policy, {"items": [_item()]}, now=NOW)
        ch.report_inventory(principal, policy, {"items": [{
            **_item(), "observed_completion": "completed", "completion_source": "file_stability",
            "evidence_level": "observed", "file_stable": True,
        }]}, now=NOW)
        ch.report_inventory(principal, policy, {"items": [{
            **_item("v2"), "claimed_author_code": "QA-A", "completion_source": "philips_eol",
            "evidence_level": "fixture",
        }]}, now=NOW)
        rows = {
            row["snapshot_version"]: row
            for row in ch.list_inventory(policy, actor_user_id=user.id, agent_id=reg["agent_id"])
        }
    assert rows["v1"]["claimed_author_code"] is None
    assert rows["v1"]["completion_source"] == "file_stability"
    assert rows["v1"]["completion_source"] != PHILIPS_EOL
    assert rows["v1"]["evidence_level"] == "observed"
    assert rows["v2"]["claimed_author_code"] == "QA-A"
    assert rows["v2"]["completion_source"] == PHILIPS_EOL
    assert rows["v2"]["evidence_level"] == "fixture"
