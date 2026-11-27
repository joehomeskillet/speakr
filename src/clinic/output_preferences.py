"""Versioned per-account output targets and the open-after-export flag (PAR-EXP-001).

One ``clinic_output_preference`` row per account. An account with no row reads
version 0, no targets and open-after-export off: nothing is exported or opened
until that account chooses. ``save_preferences`` is a compare-and-set on
``expected_version``. A mismatch leaves the stored selection unchanged. The
write is committed, so a later database session sees it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from src.database import db

MAX_TARGETS = 8
MAX_ALIAS = 128
_TARGET_KEYS = frozenset({"channel", "target_alias", "format", "selected_by_default"})
# Text flavours stay on text channels. File flavours stay on file channels.
_CHANNEL_FORMATS = {
    "clipboard": frozenset({"plain", "formatted"}),
    "kis_draft": frozenset({"plain", "formatted"}),
    "download": frozenset({"txt", "rtf", "docx"}),
    "server_alias": frozenset({"txt", "rtf", "docx"}),
}


class PreferenceError(ValueError):
    """The preference was refused. ``code`` is stable for callers."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class OutputTarget:
    channel: str
    target_alias: str
    format: str
    selected_by_default: bool


@dataclass(frozen=True)
class OutputPreferences:
    """Stored selection. ``version`` 0 means the account has never saved one."""

    user_id: int
    version: int
    targets: tuple[OutputTarget, ...]
    open_after_export: bool


class ClinicOutputPreference(db.Model):
    """One output selection per account. ``version`` guards stale writes."""

    __tablename__ = "clinic_output_preference"
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), primary_key=True)
    version = db.Column(db.Integer, nullable=False)
    targets_json = db.Column(db.Text, nullable=False)
    open_after_export = db.Column(db.Boolean, nullable=False)


def _user_id(value: object) -> int:
    if type(value) is not int or value <= 0:
        raise PreferenceError("bad_user")
    return value


def _version(value: object) -> int:
    if type(value) is not int or value < 0:
        raise PreferenceError("bad_version")
    return value


def _alias(value: object) -> str:
    if not isinstance(value, str) or value == "" or value != value.strip() or len(value) > MAX_ALIAS:
        raise PreferenceError("bad_target")
    # An alias is a name, never a path the browser or the server could open.
    if "/" in value or "\\" in value or "\x00" in value or ".." in value:
        raise PreferenceError("bad_target")
    return value


def _targets(value: object) -> tuple[OutputTarget, ...]:
    if not isinstance(value, list) or len(value) > MAX_TARGETS:
        raise PreferenceError("bad_targets")
    seen: set[tuple[str, str]] = set()
    clean: list[OutputTarget] = []
    for item in value:
        if not isinstance(item, Mapping) or set(item) != _TARGET_KEYS:
            raise PreferenceError("bad_target")
        channel = item["channel"]
        fmt = item["format"]
        allowed = _CHANNEL_FORMATS.get(channel) if isinstance(channel, str) else None
        if allowed is None or not isinstance(fmt, str) or fmt not in allowed:
            raise PreferenceError("bad_target")
        if type(item["selected_by_default"]) is not bool:
            raise PreferenceError("bad_target")
        alias = _alias(item["target_alias"])
        if (channel, alias) in seen:
            raise PreferenceError("duplicate_target")
        seen.add((channel, alias))
        clean.append(OutputTarget(channel, alias, fmt, item["selected_by_default"]))
    return tuple(clean)


def _stored_flag(value: object) -> bool:
    if value is True or value == 1:
        return True
    if value is False or value == 0:
        return False
    raise PreferenceError("corrupt")


def _loads(text: object) -> tuple[OutputTarget, ...]:
    if not isinstance(text, str):
        raise PreferenceError("corrupt")
    try:
        data = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise PreferenceError("corrupt") from exc
    try:
        return _targets(data)
    except PreferenceError as exc:
        raise PreferenceError("corrupt") from exc


def _require_account(user_id: int) -> None:
    from src.models.user import User

    if db.session.get(User, user_id) is None:
        raise PreferenceError("unknown_user")


def _read(user_id: int):
    # Column select bypasses the identity map, so another session's commit is visible.
    return db.session.execute(
        select(
            ClinicOutputPreference.version,
            ClinicOutputPreference.targets_json,
            ClinicOutputPreference.open_after_export,
        ).where(ClinicOutputPreference.user_id == user_id)
    ).first()


def _dump(targets: tuple[OutputTarget, ...]) -> str:
    payload = [
        {
            "channel": item.channel,
            "target_alias": item.target_alias,
            "format": item.format,
            "selected_by_default": item.selected_by_default,
        }
        for item in targets
    ]
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def get_preferences(user_id: object) -> OutputPreferences:
    """Return the account's selection, or the safe default when none is stored."""
    uid = _user_id(user_id)
    _require_account(uid)
    row = _read(uid)
    if row is None:
        return OutputPreferences(uid, 0, (), False)
    return OutputPreferences(uid, row[0], _loads(row[1]), _stored_flag(row[2]))


def save_preferences(
    user_id: object,
    targets: object,
    open_after_export: object,
    *,
    expected_version: object,
) -> OutputPreferences:
    """Store the selection when ``expected_version`` is still current.

    The first save uses version 0. A mismatch raises
    ``PreferenceError('stale_version')`` and does not change the row.
    """
    uid = _user_id(user_id)
    _require_account(uid)
    if type(open_after_export) is not bool:
        raise PreferenceError("bad_value")
    clean = _targets(targets)
    version = _version(expected_version)
    row = _read(uid)
    current = 0 if row is None else row[0]
    if version != current:
        raise PreferenceError("stale_version")
    text = _dump(clean)
    try:
        if row is None:
            db.session.add(ClinicOutputPreference(
                user_id=uid, version=1, targets_json=text, open_after_export=open_after_export,
            ))
            db.session.flush()
        else:
            result = db.session.execute(
                update(ClinicOutputPreference)
                .where(
                    ClinicOutputPreference.user_id == uid,
                    ClinicOutputPreference.version == current,
                )
                .values(version=current + 1, targets_json=text, open_after_export=open_after_export)
            )
            if result.rowcount != 1:
                raise PreferenceError("stale_version")
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        raise PreferenceError("stale_version") from exc
    except PreferenceError:
        db.session.rollback()
        raise
    except BaseException:
        db.session.rollback()
        raise
    return OutputPreferences(uid, current + 1, clean, open_after_export)
