"""Workstation overrides below server ceilings (PAR-CFG-001, R-048, R-050).

Persists allowlisted nonclinical presentation settings for one authenticated
user (``User.id`` from ``src/models/user.py``) and one server-issued
workstation id. A central lock and ``setting_ceiling`` win on every read,
including after the policy changes. A client profile or workstation token
never selects a capability.

shortcut: process memory plus ``dump``/``load``, not a database row. Upgrade
when a route package stores the snapshot per user.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any, Mapping

from src.clinic.config import (
    SETTING_CEILINGS,
    client_capabilities,
    client_profile,
    setting_ceiling,
)
from src.config.ui_themes import AUDIO_PLAYER_POSITIONS, COLOR_SCHEMES, is_available
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
        elif value not in _ENUMS[key]:
            raise PreferenceError("bad_value")
        else:
            clean[key] = value
    return clean


@dataclass
class _Row:
    values: dict[str, Any]
    version: int


class WorkstationPreferences:
    """Allowlisted overrides keyed by authenticated user and issued workstation."""

    def __init__(self) -> None:
        self._issued: dict[str, int] = {}
        self._rows: dict[tuple[int, str], _Row] = {}
        self._locks: dict[str, Any] = {}

    def issue(self, user: Any) -> str:
        """Mint a workstation id. The client does not choose it."""
        workstation_id = secrets.token_urlsafe(18)
        self._issued[workstation_id] = _user_id(user)
        return workstation_id

    def lock(self, key: str, value: Any) -> None:
        """Fix one allowlisted key from the server. A client payload cannot call this."""
        self._locks.update(_check_changes({key: value}))

    def unlock(self, key: str) -> None:
        self._locks.pop(key, None)

    def _bind(self, user: Any, workstation_id: str) -> tuple[int, str]:
        uid = _user_id(user)
        if (
            not isinstance(workstation_id, str)
            or workstation_id == ""
            or workstation_id in _profile_names()
            or self._issued.get(workstation_id) != uid
        ):
            raise PreferenceError("untrusted_workstation")
        return uid, workstation_id

    def _current(self, uid: int, workstation_id: str) -> _Row | None:
        return self._rows.get((uid, workstation_id))

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
        self._rows[(uid, wid)] = _Row(merged, current + 1)
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

    def effective(
        self, user: Any, workstation_id: str, client: Mapping[str, Any] | None = None,
    ) -> Effective:
        """Stored overrides with the current ceiling and lock applied.

        ``client`` is ignored: the profile is ``client_profile()`` (R-048).
        """
        del client
        uid, wid = self._bind(user, workstation_id)
        row = self._current(uid, wid)
        stored = {} if row is None else row.values
        version = 0 if row is None else row.version
        values: dict[str, Any] = {}
        origins: dict[str, str] = {}
        for key in set(stored) | set(self._locks):
            if key not in ALLOWLIST:
                continue
            locked, forced = setting_ceiling(key)
            if locked:
                values[key] = forced
                origins[key] = "ceiling"
            elif key in self._locks:
                values[key] = self._locks[key]
                origins[key] = "central"
            else:
                values[key] = stored[key]
                origins[key] = "workstation"
        return Effective(values, origins, version, client_profile())

    def dump(self) -> dict[str, Any]:
        """Snapshot of issued ids, stored overrides and locks. Pending input is absent."""
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
        return store
