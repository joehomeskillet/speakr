import json
import re
from sqlite3 import Connection
from typing import cast

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Query

from src.clinic.presets import SYSTEM_SCOPE_ID, ClinicPreset
from src.database import db

_PREFIX = "firmware:"
_DEVICE_TYPE = re.compile(r"[A-Z0-9][A-Z0-9._-]*")
_VERSION = re.compile(r"[0-9]+(?:\.[0-9]+)*")


class RevisionConflict(Exception):
    pass


def normalize_device_type(value: object) -> str | None:
    if not isinstance(value, str) or not value.isascii():
        return None
    key = value.strip().upper()
    return key if len(key) <= 64 - len(_PREFIX) and _DEVICE_TYPE.fullmatch(key) else None


def normalize_version(value: object) -> str | None:
    if not isinstance(value, str) or not value.isascii():
        return None
    version = value.strip()
    return version if len(version) <= 64 and _VERSION.fullmatch(version) else None


def _system_presets() -> Query:
    return db.session.query(ClinicPreset).filter_by(scope="system", scope_id=SYSTEM_SCOPE_ID)


def list_targets() -> dict[str, dict]:
    rows = _system_presets().with_entities(ClinicPreset.key, ClinicPreset.value_json)
    targets = {}
    for key, value_json in rows.filter(ClinicPreset.key.startswith(_PREFIX)):
        device_type = key.removeprefix(_PREFIX)
        targets[device_type] = {**json.loads(value_json), "device_type": device_type}
    return targets


def set_target(device_type: str, target_version: object, expected_version: object) -> dict:
    key_type = normalize_device_type(device_type)
    target = normalize_version(target_version)
    if key_type is None or target is None or type(expected_version) is not int or expected_version < 0:
        raise ValueError("Invalid device type, target version, or expected revision")

    key = f"{_PREFIX}{key_type}"
    value = {"target_version": target, "version": expected_version + 1}
    value_json = json.dumps(value)
    connection = db.session.connection()
    if connection.dialect.name == "sqlite":
        driver = cast(Connection, connection.connection.driver_connection)
        if not driver.in_transaction:
            connection.exec_driver_sql("BEGIN IMMEDIATE")

    query = _system_presets().filter_by(key=key)
    raw_value = query.with_entities(ClinicPreset.value_json)
    conflict = f"Firmware target revision changed for {key_type}"
    if expected_version == 0:
        savepoint = db.session.begin_nested()
        try:
            with savepoint:
                db.session.add(ClinicPreset(scope="system", scope_id=SYSTEM_SCOPE_ID, key=key, value_json=value_json))
                db.session.flush()
        except IntegrityError as exc:
            if raw_value.scalar() is None:
                raise
            raise RevisionConflict(conflict) from exc
    else:
        original = raw_value.scalar()
        if original is None or json.loads(original)["version"] != expected_version:
            raise RevisionConflict(conflict)
        if query.filter_by(value_json=original).update({ClinicPreset.value_json: value_json}) != 1:
            raise RevisionConflict(conflict)
    return {"device_type": key_type, **value}


def _numeric_version(version: str) -> tuple[int, ...]:
    parts = [int(part) for part in version.split(".")]
    while parts and parts[-1] == 0:
        parts.pop()
    return tuple(parts)


def compare(item: dict, targets: dict) -> dict:
    device_type = normalize_device_type(item.get("device_type"))
    firmware = normalize_version(item.get("firmware_version"))
    target = normalize_version(targets.get(device_type, {}).get("target_version")) if device_type else None
    status = "unknown"
    if firmware is not None and target is not None:
        observed, desired = _numeric_version(firmware), _numeric_version(target)
        status = "older" if observed < desired else "newer" if observed > desired else "equal"
    return {
        "source_id": item.get("source_id"),
        "root_id": item.get("root_id"),
        "snapshot_version": item.get("snapshot_version"),
        "device_type": device_type,
        "firmware_version": firmware,
        "target_version": target,
        "comparison_status": status,
    }
