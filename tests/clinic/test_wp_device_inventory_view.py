"""Tests for device inventory view and firmware comparison.

This package covers the read-only web-view slice of PAR-IMP-001, PAR-IMP-009
and PAR-IMP-010: approved source inventory, provenance/observation time, known
firmware version comparison, and the open WG-IMP-009/WG-IMP-010 status. No real
device or firmware is exercised; synthetic fixtures are not evidence for Philips
hardware.
"""

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from src.app import app, db
from src.clinic import agent_channel as ch
from src.clinic.agent_channel import ChannelPolicy
from src.clinic.device_inventory_view import (
    compare_firmware_versions,
    filter_inventory_by_date,
    get_config_action_status,
    get_device_inventory,
    get_firmware_action_status,
    get_inventory_summary,
)
from src.models import User


NOW = datetime(2026, 11, 17, 8, 0, 0, tzinfo=timezone.utc)
SECRET = b"s" * 32
DATA = b"synthetic dictation bytes " * 400


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _policy(authorise=None, **kwargs):
    return ChannelPolicy(
        master_secret=SECRET,
        authorise=authorise or (lambda user_id, action: True),
        **kwargs,
    )


@pytest.fixture(autouse=True)
def _env():
    with app.app_context():
        ch.create_tables()
        yield


@pytest.fixture
def user():
    row = User(
        username="div" + uuid.uuid4().hex[:8],
        email=uuid.uuid4().hex[:8] + "@example.invalid",
        password="x",
    )
    db.session.add(row)
    db.session.commit()
    return row


def _register(policy, user, fingerprint="aa" * 20, roots=("usb-1",)):
    reg = ch.register_agent(
        policy,
        actor_user_id=user.id,
        display_name="Agent",
        allowed_sources=list(roots),
        credential_fingerprint=fingerprint,
    )
    return reg, ch.authenticate_agent(fingerprint)


def _item(data=DATA, snapshot="v1", root="usb-1", source="src-1"):
    return {
        "source_id": source,
        "root_id": root,
        "relative_path": "a/b.dss",
        "size": len(data),
        "mtime_utc": "2026-11-17T07:00:00.1234567+00:00",
        "sha256": _sha(data),
        "snapshot_version": snapshot,
    }


@pytest.fixture
def setup(user):
    policy = _policy()
    reg, principal = _register(policy, user)
    ch.report_inventory(principal, policy, {"items": [_item()]}, now=NOW)
    return policy, reg, principal, user


# --- positive cases -------------------------------------------------------------------------


def test_get_device_inventory_exposes_provenance_and_observation_time(setup):
    policy, reg, _, user = setup
    view = get_device_inventory(policy, user.id, reg["agent_id"])
    assert view["agent_id"] == reg["agent_id"]
    assert view["count"] == 1
    item = view["items"][0]
    assert item["source_id"] == "src-1"
    assert item["root_id"] == "usb-1"
    assert item["relative_path"] == "a/b.dss"
    assert item["sha256"] == _sha(DATA)
    assert item["snapshot_version"] == "v1"
    assert item["mtime_utc"] == "2026-11-17T07:00:00Z"
    assert "queried_at" in view


def test_get_inventory_summary_aggregates_sources_and_versions(setup, user):
    policy, reg, principal, _ = setup
    ch.report_inventory(
        principal,
        policy,
        {
            "items": [
                _item(data=b"a", snapshot="v1", source="src-1"),
                _item(data=b"b", snapshot="v2", source="src-1"),
                _item(data=b"c", snapshot="v1", source="src-2"),
            ]
        },
        now=NOW,
    )
    summary = get_inventory_summary(policy, user.id, reg["agent_id"])
    assert summary["total_items"] == 3
    assert summary["unique_sources"] == 2
    assert summary["total_size_bytes"] == 3
    assert summary["firmware_versions"] == {
        "src-1": ["v1", "v2"],
        "src-2": ["v1"],
    }


def test_compare_firmware_versions_known_and_unique(setup, user):
    policy, reg, principal, _ = setup
    ch.report_inventory(
        principal,
        policy,
        {
            "items": [
                # snapshot_version "v1" updates the setup row; "v1.0.1" is new.
                _item(data=b"a", snapshot="v1", source="src-1"),
                _item(data=b"c", snapshot="v1.0.1", source="src-1"),
            ]
        },
        now=NOW,
    )
    rows = ch.list_inventory(policy, actor_user_id=user.id, agent_id=reg["agent_id"])
    result = compare_firmware_versions(rows)
    assert result["status"] == "known"
    assert result["known_count"] == 2
    assert result["unique_versions"] == ["v1", "v1.0.1"]
    assert result["unknown_count"] == 0


def test_filter_inventory_by_date_keeps_recent_and_drops_stale(setup, user):
    policy, reg, principal, _ = setup
    old_item = _item(data=b"old", snapshot="v0")
    old_item["mtime_utc"] = "2026-11-16T06:00:00Z"
    ch.report_inventory(principal, policy, {"items": [old_item]}, now=NOW)
    rows = ch.list_inventory(policy, actor_user_id=user.id, agent_id=reg["agent_id"])
    cutoff = datetime(2026, 11, 17, 0, 0, 0, tzinfo=timezone.utc)
    filtered = filter_inventory_by_date(rows, cutoff)
    assert len(filtered) == 1
    assert filtered[0]["snapshot_version"] == "v1"


# --- unauthorized / failure cases -----------------------------------------------------------


def test_get_device_inventory_denied_without_authorisation(setup, user):
    policy, reg, _, _ = setup
    denied = _policy(authorise=lambda user_id, action: False)
    with pytest.raises(ch.AgentChannelError) as err:
        get_device_inventory(denied, user.id, reg["agent_id"])
    assert err.value.code == "forbidden"


def test_get_device_inventory_empty_for_unknown_agent(setup, user):
    policy, _, _, _ = setup
    view = get_device_inventory(policy, user.id, "does-not-exist")
    assert view["count"] == 0
    assert view["items"] == []


def test_compare_firmware_versions_unknown_when_missing():
    result = compare_firmware_versions(
        [
            {"source_id": "src-1"},
            {"source_id": "src-1", "snapshot_version": ""},
        ]
    )
    assert result["status"] == "unknown"
    assert result["unknown_count"] == 2
    assert result["unique_versions"] == []


def test_filter_inventory_by_date_tolerates_bad_mtime():
    inventory = [
        {"mtime_utc": "not-a-date", "source_id": "src-1"},
        {"source_id": "src-2"},
        {"mtime_utc": "2026-11-17T07:00:00Z", "source_id": "src-3"},
    ]
    cutoff = datetime(2026, 11, 17, 0, 0, 0, tzinfo=timezone.utc)
    assert filter_inventory_by_date(inventory, cutoff) == [
        {"mtime_utc": "2026-11-17T07:00:00Z", "source_id": "src-3"}
    ]


# --- WG-IMP-009 / WG-IMP-010 status ---------------------------------------------------------


def test_firmware_action_status_shows_open_wg_imp_010():
    status = get_firmware_action_status()
    assert status["action"] == "firmware_flash"
    assert status["status"] == "blocked"
    assert status["web_gap"] == "WG-IMP-010"
    assert status["control_command_issued"] is False


def test_config_action_status_shows_open_wg_imp_009():
    status = get_config_action_status()
    assert status["action"] == "device_config_write"
    assert status["status"] == "blocked"
    assert status["web_gap"] == "WG-IMP-009"
    assert status["control_command_issued"] is False


def test_device_inventory_view_does_not_emit_control_command(setup, user):
    policy, reg, _, _ = setup
    get_device_inventory(policy, user.id, reg["agent_id"])
    get_inventory_summary(policy, user.id, reg["agent_id"])
    get_firmware_action_status()
    get_config_action_status()
    # The view must never ask the agent to do anything.
    assert (
        ch.ClinicAgentIntent.query.filter_by(agent_id=reg["agent_id"]).count() == 0
    )


def test_retry_after_inventory_report_sees_latest_version(setup, user):
    policy, reg, principal, _ = setup
    first = get_device_inventory(policy, user.id, reg["agent_id"])
    assert first["count"] == 1
    ch.report_inventory(
        principal, policy, {"items": [_item(data=b"retry", snapshot="v2")]}, now=NOW
    )
    second = get_device_inventory(policy, user.id, reg["agent_id"])
    assert second["count"] == 2
    versions = {r["snapshot_version"] for r in second["items"]}
    assert versions == {"v1", "v2"}


@pytest.mark.parametrize(("bad_report", "code"), [
    ({"root_id": "foreign-root", "devices": []}, "forbidden"),
    ({"root_id": "usb-2", "devices": [{"device_id": "device", "capabilities": {"pin_lock": 0}}]}, "invalid"),
    ({"root_id": "usb-2", "devices": [{"device_id": "device", "driver_status": "installed?"}]}, "invalid"),
    ({"root_id": "usb-2", "devices": [{"device_id": "same"}, {"device_id": "same"}]}, "invalid"),
])
def test_invalid_device_report_rolls_back_file_snapshots(user, bad_report, code):
    policy = _policy()
    reg, principal = _register(policy, user, roots=("usb-1", "usb-2"))
    with pytest.raises(ch.AgentChannelError) as err:
        ch.report_inventory(principal, policy, {
            "items": [_item()],
            "device_reports": [{"root_id": "usb-1", "devices": []}, bad_report],
        }, now=NOW)
    assert err.value.code == code
    assert ch.list_inventory(policy, actor_user_id=user.id, agent_id=reg["agent_id"]) == []
    assert ch.ClinicAgentDeviceReport.query.filter_by(agent_id=reg["agent_id"]).count() == 0
