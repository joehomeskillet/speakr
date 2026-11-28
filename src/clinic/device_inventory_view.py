"""Device inventory and firmware comparison view for the clinic.

This module provides a read-only view of the import agent's reported inventory.
It exposes only approved source inventory with provenance and observation time,
compares known firmware versions without downloading or writing firmware, and
surfaces the open WG-IMP-009/WG-IMP-010 status for configuration and firmware
actions. It never emits an import-agent control command.
"""

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List

from sqlalchemy import tuple_

from src.clinic import agent_channel
from src.clinic.agent_channel import AgentChannelError, ChannelPolicy, list_inventory
from src.clinic.import_source_config import ClinicImportSourceConfig


def _utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_device_inventory(
    policy: ChannelPolicy, actor_user_id: int, agent_id: str
) -> Dict[str, Any]:
    """Read-only view of the device inventory reported by an agent.

    Exposes only approved source inventory with provenance (``source_id``,
    ``root_id``, ``relative_path``, ``sha256``) and observation time
    (``mtime_utc``). The underlying channel already filters to the agent's
    registered sources.
    """
    rows = list_inventory(policy, actor_user_id=actor_user_id, agent_id=agent_id)
    return {
        "agent_id": agent_id,
        "items": rows,
        "count": len(rows),
        "queried_at": _utc_iso(),
    }


def get_device_report_view(*, site_admin, own_author_id, led_pools):
    """Project the latest observations for roots visible to the authenticated actor."""
    registry = agent_channel.registered_agent_roots()
    if site_admin:
        scopes = [(agent_id, root) for agent_id, (enabled, roots) in registry.items() if enabled for root in roots]
    else:
        owners = {root: agent_id for root, agent_id in agent_channel.unique_root_owners(registry).items() if agent_id}
        bindings = defaultdict(list)
        for config in ClinicImportSourceConfig.query.filter(ClinicImportSourceConfig.source_ref.in_(owners)):
            bindings[config.source_ref].append(config)
        scopes = []
        for root, configs in bindings.items():
            if len(configs) != 1 or configs[0].source_kind != "device":
                continue
            config = configs[0]
            if config.target_author_user_id == own_author_id or config.target_pool_id in led_pools:
                scopes.append((owners[root], root))

    report = agent_channel.ClinicAgentDeviceReport
    reports = report.query.filter(tuple_(report.agent_id, report.root_id).in_(scopes)).order_by(
        report.agent_id, report.root_id,
    ).all() if scopes else []
    items = [
        dict(device, agent_id=row.agent_id, root_id=row.root_id, last_report_at=agent_channel.stamp(row.reported_at))
        for row in reports for device in row.devices
    ]
    return {
        "items": items,
        "device_count": len(items) if reports else None,
        "last_report_at": max(agent_channel.stamp(row.reported_at) for row in reports) if reports else None,
    }


def filter_inventory_by_date(
    inventory: List[Dict[str, Any]], after_date: datetime
) -> List[Dict[str, Any]]:
    """Return inventory items whose ``mtime_utc`` is at or after ``after_date``."""
    result = []
    for item in inventory:
        raw = item.get("mtime_utc", "")
        if not raw:
            continue
        try:
            item_date = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if item_date >= after_date:
                result.append(item)
        except (ValueError, TypeError):
            continue
    return result


def compare_firmware_versions(
    inventory_items: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Compare known firmware versions from inventory ``snapshot_version`` fields.

    Unknown or missing versions are counted but never treated as known. No
    firmware is downloaded or written; only the already-reported metadata is
    inspected.
    """
    known: List[str] = []
    unknown = 0
    for item in inventory_items:
        version = item.get("snapshot_version")
        if version:
            known.append(version)
        else:
            unknown += 1
    unique = sorted(set(known))
    return {
        "known_versions": known,
        "unique_versions": unique,
        "known_count": len(known),
        "unknown_count": unknown,
        "status": "known" if unique else "unknown",
        "compared_at": _utc_iso(),
    }


def get_inventory_summary(
    policy: ChannelPolicy, actor_user_id: int, agent_id: str
) -> Dict[str, Any]:
    """Summary of reported inventory for an agent."""
    rows = list_inventory(policy, actor_user_id=actor_user_id, agent_id=agent_id)
    sources: set = set()
    versions_by_source: Dict[str, List[str]] = {}
    total_size = 0
    for item in rows:
        source_id = item.get("source_id", "")
        if source_id:
            sources.add(source_id)
        version = item.get("snapshot_version", "")
        if source_id and version:
            versions_by_source.setdefault(source_id, []).append(version)
        total_size += item.get("size", 0) or 0
    return {
        "agent_id": agent_id,
        "total_items": len(rows),
        "unique_sources": len(sources),
        "total_size_bytes": total_size,
        "firmware_versions": {
            source: sorted(set(versions))
            for source, versions in versions_by_source.items()
        },
        "last_updated": _utc_iso(),
    }


def get_firmware_action_status() -> Dict[str, Any]:
    """Status of firmware write actions.

    WG-IMP-010 is open: firmware flashing remains blocked until a verified,
    permitted direct browser path exists. This function never emits a control
    command to the import agent.
    """
    return {
        "action": "firmware_flash",
        "status": "blocked",
        "web_gap": "WG-IMP-010",
        "reason": "Firmware flash is not available without a verified direct browser path",
        "control_command_issued": False,
    }


def get_config_action_status() -> Dict[str, Any]:
    """Status of device configuration write actions.

    WG-IMP-009 is open: writing device configuration (author lists, PIN,
    encryption, power saving) remains blocked until a verified direct browser
    hardware contract exists. This function never emits a control command to the
    import agent.
    """
    return {
        "action": "device_config_write",
        "status": "blocked",
        "web_gap": "WG-IMP-009",
        "reason": "Device configuration write is not available without a verified direct browser hardware contract",
        "control_command_issued": False,
    }


__all__ = [
    "AgentChannelError",
    "ChannelPolicy",
    "compare_firmware_versions",
    "filter_inventory_by_date",
    "get_config_action_status",
    "get_device_inventory",
    "get_device_report_view",
    "get_firmware_action_status",
    "get_inventory_summary",
]
