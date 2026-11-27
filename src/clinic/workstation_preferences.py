"""Workstation overrides below server ceilings (PAR-CFG-001, R-048, R-050).

Persists allowlisted nonclinical presentation settings for one authenticated
user (``User.id`` from ``src/models/user.py``) and one server-issued
workstation id. A central lock and ``setting_ceiling`` win on every read,
including after the policy changes. A client profile or workstation token
never selects a capability.

Inside a Flask app context with the SQLAlchemy extension, issued
workstations, versioned overrides and central locks live in
``clinic_workstation``, ``clinic_workstation_override`` and
``clinic_workstation_lock`` (durable, shared across workers; writes use a
compare-and-set on ``version``). Outside an app context, state stays in
process memory.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from typing import Any, Mapping

from flask import current_app, has_app_context
from sqlalchemy import CheckConstraint, select, update
from sqlalchemy.exc import IntegrityError

from src.clinic.config import (
    SETTING_CEILINGS,
    client_capabilities,
    client_profile,
    setting_ceiling,
)
from src.config.ui_themes import AUDIO_PLAYER_POSITIONS, COLOR_SCHEMES, is_available
from src.database import db
from src.utils.localization import match_language

# Existing account presentation fields only. Clinical, profile and ceiling
# keys are absent on purpose.
_ENUMS: dict[str, frozenset[str]] = {
    "theme_color_scheme": frozenset(COLOR_SCHEMES),
    "audio_player_position": frozenset(AUDIO_PLAYER_POSITIONS),
}
_BOOLS = frozenset({
    "theme_dark_mode",
    "keyboard_shortcuts",
    "show_timestamps_simple_view",
    "editor_autosave",
})
ALLOWLIST = frozenset(_ENUMS) | _BOOLS | frozenset({"ui_theme", "ui_language"})

_CLIENT_KEYS = frozenset({
    "client_profile", "profile", "channel", "workstation_token", "workstation_id",
    "audio_capture", "persistent_audio", "document_export", "native_bridge",
    "capabilities",
})


class PreferenceError(ValueError):
    """The change was rejected before it was stored."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class StaleVersion(PreferenceError):
    """``expected_version`` is not the stored version. Nothing was written."""

    def __init__(self, version: int):
        self.version = version
        super().__init__("stale_version")


@dataclass(frozen=True)
class Effective:
    """Values after ceilings and locks, with the origin of each key."""

    values: dict[str, Any]
    origins: dict[str, str]
    version: int
    profile: str

    @property
    def capabilities(self) -> dict[str, Any]:
        """Capability map of the server profile. Never the client's claim."""
        profiles = client_capabilities().get("profiles") or {}
        return dict(profiles[self.profile])


def _profile_names() -> frozenset[str]:
    profiles = client_capabilities().get("profiles") or {}
    if not isinstance(profiles, dict):
        return frozenset()
    return frozenset(profiles)


def _user_id(user: Any) -> int:
    """Authenticated ``User.id``. Anonymous and non-positive ids are refused."""
    if user is None or getattr(user, "is_authenticated", True) is False:
        raise PreferenceError("unauthorized")
    raw = getattr(user, "id", user)
    if type(raw) is not int or raw <= 0:
        raise PreferenceError("unauthorized")
    return raw


def _version(value: Any) -> int:
    if type(value) is not int or value < 0:
        raise PreferenceError("bad_version")
    return value


def _check_changes(changes: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(changes, Mapping) or not changes:
        raise PreferenceError("empty")
    blocked = _CLIENT_KEYS | _profile_names() | frozenset(SETTING_CEILINGS)
    clean: dict[str, Any] = {}
    for key, value in changes.items():
        if not isinstance(key, str) or key in blocked:
            raise PreferenceError("capability_forbidden")
        if key not in ALLOWLIST:
            raise PreferenceError("not_allowlisted")
        if key in _BOOLS:
            if type(value) is not bool:
                raise PreferenceError("bad_value")
            clean[key] = value
        elif key == "ui_theme":
            if not is_available(value):
                raise PreferenceError("bad_value")
            clean[key] = value
        elif key == "ui_language":
            code = match_language(value)
            if code is None:
                raise PreferenceError("bad_value")
            clean[key] = code
        elif not isinstance(value, str) or value not in _ENUMS[key]:
            raise PreferenceError("bad_value")
        else:
            clean[key] = value
    return clean


@dataclass
class _Row:
    values: dict[str, Any]
    version: int


def _db_ready() -> bool:
    return has_app_context() and "sqlalchemy" in current_app.extensions


def _json_dump(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _json_load(text: str) -> Any:
    try:
        loaded = json.loads(text)
    except (TypeError, ValueError, RecursionError) as exc:
        raise PreferenceError("bad_snapshot") from exc
    return loaded


def _id_ok(workstation_id: object) -> bool:
    """Server-issued ids only. Profile names and client tokens are never ids."""
    return (
        isinstance(workstation_id, str)
        and 0 < len(workstation_id) <= 64
        and workstation_id not in _CLIENT_KEYS
        and workstation_id not in _profile_names()
    )


class ClinicWorkstation(db.Model):
    """Server-issued workstation bound to one authenticated user id."""

    __tablename__ = "clinic_workstation"
    # shortcut: no FK to user.id, add it when every writer shares the user table
    workstation_id = db.Column(db.String(64), primary_key=True)
    user_id = db.Column(db.Integer, nullable=False)


class ClinicWorkstationOverride(db.Model):
    """One versioned allowlisted override blob per user and workstation."""

    __tablename__ = "clinic_workstation_override"
    user_id = db.Column(db.Integer, primary_key=True)
    workstation_id = db.Column(db.String(64), primary_key=True)
    version = db.Column(db.Integer, nullable=False)
    values_json = db.Column(db.Text, nullable=False)
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_clinic_workstation_override_version"),
    )


class ClinicWorkstationLock(db.Model):
    """Central lock for one allowlisted key. A ceiling still wins on read."""

    __tablename__ = "clinic_workstation_lock"
    key = db.Column(db.String(64), primary_key=True)
    value_json = db.Column(db.Text, nullable=False)


_TABLES = (ClinicWorkstation, ClinicWorkstationOverride, ClinicWorkstationLock)


def create_tables(bind=None) -> None:
    """Create the workstation tables. An existing table is left alone."""
    db.metadata.create_all(
        bind=bind or db.engine,
        tables=[model.__table__ for model in _TABLES],
        checkfirst=True,
    )


class WorkstationPreferences:
    """Allowlisted overrides keyed by authenticated user and issued workstation."""

    def __init__(self) -> None:
        self._issued: dict[str, int] = {}
        self._rows: dict[tuple[int, str], _Row] = {}
        self._locks: dict[str, Any] = {}

    def _durable(self) -> bool:
        return _db_ready()

    def issue(self, user: Any) -> str:
        """Mint a workstation id. The client does not choose it."""
        uid = _user_id(user)
        workstation_id = secrets.token_urlsafe(18)
        if not self._durable():
            self._issued[workstation_id] = uid
            return workstation_id
        try:
            db.session.add(ClinicWorkstation(workstation_id=workstation_id, user_id=uid))
            db.session.commit()
        except BaseException:
            db.session.rollback()
            raise
        return workstation_id

    def lock(self, key: str, value: Any) -> None:
        """Fix one allowlisted key from the server. A client payload cannot call this."""
        clean = _check_changes({key: value})
        if not self._durable():
            self._locks.update(clean)
            return
        for locked_key, locked_value in clean.items():
            self._save_lock(locked_key, locked_value)

    def unlock(self, key: str) -> None:
        if not self._durable():
            self._locks.pop(key, None)
            return
        try:
            db.session.execute(
                ClinicWorkstationLock.__table__.delete().where(ClinicWorkstationLock.key == key)
            )
            db.session.commit()
        except BaseException:
            db.session.rollback()
            raise

    def _bind(self, user: Any, workstation_id: str) -> tuple[int, str]:
        uid = _user_id(user)
        if not _id_ok(workstation_id) or self._owner(workstation_id) != uid:
            raise PreferenceError("untrusted_workstation")
        return uid, workstation_id

    def _owner(self, workstation_id: str) -> int | None:
        if not self._durable():
            return self._issued.get(workstation_id)
        return db.session.execute(
            select(ClinicWorkstation.user_id).where(
                ClinicWorkstation.workstation_id == workstation_id
            )
        ).scalar_one_or_none()

    def _current(self, uid: int, workstation_id: str) -> _Row | None:
        if not self._durable():
            return self._rows.get((uid, workstation_id))
        found = db.session.execute(
            select(
                ClinicWorkstationOverride.version, ClinicWorkstationOverride.values_json,
            ).where(
                ClinicWorkstationOverride.user_id == uid,
                ClinicWorkstationOverride.workstation_id == workstation_id,
            )
        ).first()
        if found is None:
            return None
        loaded = _json_load(found[1])
        if not isinstance(loaded, dict):
            raise PreferenceError("bad_snapshot")
        return _Row(loaded, found[0])

    def _save_lock(self, key: str, value: Any) -> None:
        payload = _json_dump(value)
        try:
            updated = db.session.execute(
                update(ClinicWorkstationLock)
                .where(ClinicWorkstationLock.key == key)
                .values(value_json=payload)
            )
            if updated.rowcount == 0:
                db.session.add(ClinicWorkstationLock(key=key, value_json=payload))
                db.session.flush()
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            db.session.execute(
                update(ClinicWorkstationLock)
                .where(ClinicWorkstationLock.key == key)
                .values(value_json=payload)
            )
            db.session.commit()
        except BaseException:
            db.session.rollback()
            raise

    def _save_override(
        self, uid: int, wid: str, merged: dict[str, Any], new_version: int, previous: int | None,
    ) -> None:
        if not self._durable():
            self._rows[(uid, wid)] = _Row(dict(merged), new_version)
            return
        payload = _json_dump(merged)
        try:
            if previous is None:
                db.session.add(ClinicWorkstationOverride(
                    user_id=uid, workstation_id=wid, version=new_version, values_json=payload,
                ))
                db.session.flush()
            else:
                result = db.session.execute(
                    update(ClinicWorkstationOverride)
                    .where(
                        ClinicWorkstationOverride.user_id == uid,
                        ClinicWorkstationOverride.workstation_id == wid,
                        ClinicWorkstationOverride.version == previous,
                    )
                    .values(version=new_version, values_json=payload)
                )
                if result.rowcount != 1:
                    raise StaleVersion(0)
            db.session.commit()
        except IntegrityError as exc:
            db.session.rollback()
            raise StaleVersion(self._stored_version(uid, wid)) from exc
        except StaleVersion:
            db.session.rollback()
            raise StaleVersion(self._stored_version(uid, wid))
        except BaseException:
            db.session.rollback()
            raise

    def _stored_version(self, uid: int, wid: str) -> int:
        row = self._current(uid, wid)
        return 0 if row is None else row.version

    def apply(
        self, user: Any, workstation_id: str, changes: Mapping[str, Any], *, expected_version: int,
    ) -> int:
        """Store ``changes`` (Übernehmen). A stale or illegal write changes nothing."""
        uid, wid = self._bind(user, workstation_id)
        version = _version(expected_version)
        row = self._current(uid, wid)
        current = 0 if row is None else row.version
        if version != current:
            raise StaleVersion(current)
        clean = _check_changes(changes)
        merged = {} if row is None else dict(row.values)
        merged.update(clean)
        self._save_override(uid, wid, merged, current + 1, None if row is None else current)
        return current + 1

    def cancel(
        self, user: Any, workstation_id: str, changes: Mapping[str, Any], *, expected_version: int,
    ) -> int:
        """Drop ``changes`` (Abbrechen). The stored row stays as it was."""
        uid, wid = self._bind(user, workstation_id)
        version = _version(expected_version)
        row = self._current(uid, wid)
        current = 0 if row is None else row.version
        if version != current:
            raise StaleVersion(current)
        _check_changes(changes)
        return current

    def stored(self, user: Any, workstation_id: str) -> dict[str, Any]:
        uid, wid = self._bind(user, workstation_id)
        row = self._current(uid, wid)
        return {} if row is None else dict(row.values)

    def _lock_map(self) -> dict[str, Any]:
        if not self._durable():
            return self._locks
        rows = db.session.execute(
            select(ClinicWorkstationLock.key, ClinicWorkstationLock.value_json)
        ).all()
        loaded: dict[str, Any] = {}
        for key, raw in rows:
            value = _json_load(raw)
            loaded[key] = value
        return loaded

    def effective(
        self, user: Any, workstation_id: str, client: Mapping[str, Any] | None = None,
    ) -> Effective:
        """Stored overrides with the current ceiling and lock applied.

        ``client`` is ignored: the profile is ``client_profile()`` (R-048).
        A missing override row is version 0. Locked ceilings still apply.
        """
        del client
        uid, wid = self._bind(user, workstation_id)
        row = self._current(uid, wid)
        stored = {} if row is None else row.values
        version = 0 if row is None else row.version
        locks = self._lock_map()
        keys = set(stored) | set(locks)
        for key in SETTING_CEILINGS:
            if key in ALLOWLIST and setting_ceiling(key)[0]:
                keys.add(key)
        values: dict[str, Any] = {}
        origins: dict[str, str] = {}
        for key in keys:
            if key not in ALLOWLIST:
                continue
            locked, forced = setting_ceiling(key)
            if locked:
                values[key] = forced
                origins[key] = "ceiling"
            elif key in locks:
                values[key] = locks[key]
                origins[key] = "central"
            else:
                values[key] = stored[key]
                origins[key] = "workstation"
        return Effective(values, origins, version, client_profile())

    def dump(self) -> dict[str, Any]:
        """Snapshot of issued ids, stored overrides and locks. Pending input is absent."""
        if not self._durable():
            return {
                "issued": dict(self._issued),
                "rows": [
                    {
                        "user_id": uid,
                        "workstation_id": wid,
                        "values": dict(row.values),
                        "version": row.version,
                    }
                    for (uid, wid), row in self._rows.items()
                ],
                "locks": dict(self._locks),
            }
        issued = db.session.execute(
            select(ClinicWorkstation.workstation_id, ClinicWorkstation.user_id)
        ).all()
        overrides = db.session.execute(
            select(
                ClinicWorkstationOverride.user_id,
                ClinicWorkstationOverride.workstation_id,
                ClinicWorkstationOverride.values_json,
                ClinicWorkstationOverride.version,
            )
        ).all()
        locks = db.session.execute(
            select(ClinicWorkstationLock.key, ClinicWorkstationLock.value_json)
        ).all()
        return {
            "issued": {wid: uid for wid, uid in issued},
            "rows": [
                {
                    "user_id": uid,
                    "workstation_id": wid,
                    "values": _json_load(raw),
                    "version": version,
                }
                for uid, wid, raw, version in overrides
            ],
            "locks": {key: _json_load(raw) for key, raw in locks},
        }

    def _write_snapshot(self) -> None:
        """Copy the in-memory snapshot into the shared tables."""
        try:
            for wid, uid in self._issued.items():
                owner = db.session.execute(
                    select(ClinicWorkstation.user_id).where(
                        ClinicWorkstation.workstation_id == wid
                    )
                ).scalar_one_or_none()
                if owner is None:
                    db.session.add(ClinicWorkstation(workstation_id=wid, user_id=uid))
                elif owner != uid:
                    raise PreferenceError("bad_snapshot")
            for (uid, wid), row in self._rows.items():
                if row.version < 1:
                    continue
                payload = _json_dump(row.values)
                existing = db.session.execute(
                    select(ClinicWorkstationOverride.version).where(
                        ClinicWorkstationOverride.user_id == uid,
                        ClinicWorkstationOverride.workstation_id == wid,
                    )
                ).first()
                if existing is None:
                    db.session.add(ClinicWorkstationOverride(
                        user_id=uid, workstation_id=wid, version=row.version, values_json=payload,
                    ))
                else:
                    db.session.execute(
                        update(ClinicWorkstationOverride)
                        .where(
                            ClinicWorkstationOverride.user_id == uid,
                            ClinicWorkstationOverride.workstation_id == wid,
                        )
                        .values(version=row.version, values_json=payload)
                    )
            for key, value in self._locks.items():
                payload = _json_dump(value)
                updated = db.session.execute(
                    update(ClinicWorkstationLock)
                    .where(ClinicWorkstationLock.key == key)
                    .values(value_json=payload)
                )
                if updated.rowcount == 0:
                    db.session.add(ClinicWorkstationLock(key=key, value_json=payload))
            db.session.commit()
        except PreferenceError:
            db.session.rollback()
            raise
        except BaseException:
            db.session.rollback()
            raise

    @classmethod
    def load(cls, payload: Mapping[str, Any]) -> WorkstationPreferences:
        """Rebuild a snapshot. A client token or capability key is refused."""
        if not isinstance(payload, Mapping):
            raise PreferenceError("bad_snapshot")
        issued = payload.get("issued")
        rows = payload.get("rows")
        locks = payload.get("locks")
        if not isinstance(issued, Mapping) or not isinstance(rows, list) or not isinstance(locks, Mapping):
            raise PreferenceError("bad_snapshot")
        profiles = _profile_names()
        store = cls()
        for wid, uid in issued.items():
            if not isinstance(wid, str) or wid == "" or wid in profiles or wid in _CLIENT_KEYS:
                raise PreferenceError("untrusted_workstation")
            if type(uid) is not int or uid <= 0:
                raise PreferenceError("unauthorized")
            store._issued[wid] = uid
        for item in rows:
            if not isinstance(item, Mapping):
                raise PreferenceError("bad_snapshot")
            uid = item.get("user_id")
            wid = item.get("workstation_id")
            version = item.get("version")
            values = item.get("values")
            if store._issued.get(wid) != uid or type(version) is not int or version < 0:
                raise PreferenceError("bad_snapshot")
            if not isinstance(values, Mapping):
                raise PreferenceError("bad_snapshot")
            clean = _check_changes(values) if values else {}
            store._rows[(uid, wid)] = _Row(clean, version)
        for key, value in locks.items():
            store._locks.update(_check_changes({key: value}))
        if store._durable():
            store._write_snapshot()
        return store
