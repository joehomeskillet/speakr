"""Per-account notification event and channel choices (PAR-WF-014).

A missing row is the in-app default: every known event is on for ``in_app``.
A write names the version it read. A stale version changes nothing and is
not committed. No SMTP channel is created here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from src.clinic.notification_rules import EVENTS
from src.database import db
from src.models import User

CHANNEL_IN_APP = "in_app"
CHANNELS = (CHANNEL_IN_APP,)


class PreferenceError(ValueError):
    """The choice was rejected before it was stored."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class StaleVersion(PreferenceError):
    """``expected_version`` is not the stored version. Nothing was written."""

    def __init__(self, version: int):
        self.version = version
        super().__init__("stale_version")


@dataclass(frozen=True)
class Preference:
    """One account's choices. ``channels`` always lists every known channel."""

    user_id: int
    version: int
    channels: dict[str, dict[str, bool]]


class ClinicNotificationPreference(db.Model):
    """Current event/channel choices of one account. ``version`` guards writes."""

    __tablename__ = "clinic_notification_preference"
    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), primary_key=True
    )
    version = db.Column(db.Integer, nullable=False)
    choices_json = db.Column(db.Text, nullable=False)
    updated_at = db.Column(db.DateTime, nullable=False)


def create_tables(bind=None) -> None:
    db.metadata.create_all(
        bind=bind or db.engine,
        tables=[ClinicNotificationPreference.__table__],
        checkfirst=True,
    )


def read(user_id: int) -> Preference:
    """Return the account's choices. Version 0 and in-app on when unset."""
    uid = _account(user_id)
    row = db.session.get(ClinicNotificationPreference, uid, populate_existing=True)
    if row is None:
        return Preference(uid, 0, _channels(None))
    return Preference(uid, row.version, _channels(_loads(row.choices_json)))


def set_event(
    user_id: int,
    event: str,
    enabled: bool,
    expected_version: int,
    channel: str = CHANNEL_IN_APP,
) -> Preference:
    """Store one event on one channel and return the account's new choices.

    ``expected_version`` is 0 before the first write. Raises PreferenceError
    (``bad_user``, ``unknown_event``, ``unknown_channel``, ``bad_value``,
    ``bad_version``) or StaleVersion.
    """
    uid = _account(user_id)
    chosen = _event(event)
    flag = _flag(enabled)
    name = _channel(channel)
    version = _version(expected_version)
    row = db.session.get(ClinicNotificationPreference, uid, populate_existing=True)
    if row is None:
        if version != 0:
            raise StaleVersion(0)
        events = _events(name, None)
        events[chosen] = flag
        db.session.add(ClinicNotificationPreference(
            user_id=uid, version=1, choices_json=_dumps({name: events}), updated_at=_now(),
        ))
    else:
        if row.version != version:
            raise StaleVersion(row.version)
        choices = _loads(row.choices_json)
        events = _events(name, choices.get(name))
        events[chosen] = flag
        choices[name] = events
        result = db.session.execute(
            update(ClinicNotificationPreference)
            .where(
                ClinicNotificationPreference.user_id == uid,
                ClinicNotificationPreference.version == version,
            )
            .values(choices_json=_dumps(choices), version=version + 1, updated_at=_now())
        )
        if result.rowcount != 1:
            db.session.rollback()
            raise StaleVersion(_stored_version(uid))
    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        raise StaleVersion(_stored_version(uid)) from exc
    except SQLAlchemyError:
        db.session.rollback()
        raise
    return read(uid)


def _account(user_id: object) -> int:
    if type(user_id) is not int or user_id <= 0:
        raise PreferenceError("bad_user")
    if db.session.get(User, user_id) is None:
        raise PreferenceError("bad_user")
    return user_id


def _event(event: object) -> str:
    if not isinstance(event, str) or event not in EVENTS:
        raise PreferenceError("unknown_event")
    return event


def _channel(channel: object) -> str:
    if not isinstance(channel, str) or channel not in CHANNELS:
        raise PreferenceError("unknown_channel")
    return channel


def _flag(enabled: object) -> bool:
    if type(enabled) is not bool:
        raise PreferenceError("bad_value")
    return enabled


def _version(value: object) -> int:
    if type(value) is not int or value < 0:
        raise PreferenceError("bad_version")
    return value


def _events(channel: str, stored: object) -> dict[str, bool]:
    # Only the in-app channel starts enabled. A stored bool overrides that.
    events = {event: channel == CHANNEL_IN_APP for event in EVENTS}
    if isinstance(stored, dict):
        for event, value in stored.items():
            if event in events and type(value) is bool:
                events[event] = value
    return events


def _channels(stored: dict | None) -> dict[str, dict[str, bool]]:
    stored = stored or {}
    return {name: _events(name, stored.get(name)) for name in CHANNELS}


def _loads(raw: str) -> dict:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PreferenceError("bad_store") from exc
    if not isinstance(data, dict):
        raise PreferenceError("bad_store")
    return data


def _dumps(choices: dict) -> str:
    return json.dumps(choices, sort_keys=True)


def _stored_version(user_id: int) -> int:
    row = db.session.get(ClinicNotificationPreference, user_id, populate_existing=True)
    return 0 if row is None else row.version


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
