"""State machine for the two correction modes of the web editor.

Public API:
- MODE_STOP_ON_TYPING, MODE_TYPE_WHILE_PLAYING
- Action(kind, seek_to_ms)
- ModeError (ValueError subclass with stable ``code``)
- CorrectionModeEngine(mode, *, rewind_ms=2000, resume_idle_ms=1500)

Persistence (W6): ``get_mode`` / ``set_mode`` store one durable row per user in
``clinic_correction_mode``. The engine itself stays pure Python; time is supplied
by the caller as ``now_ms`` (milliseconds).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from src.database import db

MODE_STOP_ON_TYPING = "stop_on_typing"
MODE_TYPE_WHILE_PLAYING = "type_while_playing"
MODES = (MODE_STOP_ON_TYPING, MODE_TYPE_WHILE_PLAYING)
# Owner decision W6: "Auf zu hörendes Wort zeigen" is the default.
DEFAULT_MODE = MODE_STOP_ON_TYPING


class ModeError(ValueError):
    """The correction mode operation was rejected.

    ``code`` is one of: ``invalid_mode``, ``invalid_rewind``,
    ``invalid_resume_idle``, ``negative_time``, ``time_travel``,
    ``bool_argument``.
    """

    def __init__(self, code: str, message: str | None = None):
        self.code = code
        super().__init__(message or code)


@dataclass(frozen=True)
class Action:
    """Playback action returned by the engine."""

    kind: str  # "none" | "pause" | "resume"
    seek_to_ms: int | None = None


class CorrectionModeEngine:
    """State machine for correction modes."""

    def __init__(
        self,
        mode: str,
        *,
        rewind_ms: int = 2000,
        resume_idle_ms: int = 1500,
    ) -> None:
        if not isinstance(mode, str) or mode not in (
            MODE_STOP_ON_TYPING,
            MODE_TYPE_WHILE_PLAYING,
        ):
            raise ModeError("invalid_mode", f"unknown mode: {mode!r}")
        if isinstance(rewind_ms, bool) or not isinstance(rewind_ms, int) or rewind_ms < 0:
            raise ModeError("invalid_rewind", "rewind_ms must be a non-negative int")
        if (
            isinstance(resume_idle_ms, bool)
            or not isinstance(resume_idle_ms, int)
            or resume_idle_ms <= 0
        ):
            raise ModeError(
                "invalid_resume_idle", "resume_idle_ms must be a positive int"
            )

        self._mode = mode
        self._rewind_ms = rewind_ms
        self._resume_idle_ms = resume_idle_ms

        self._playing = False
        self._last_key_ms: int | None = None
        self._pending_resume = False

    def set_mode(self, mode: str) -> None:
        """Switch mode, clearing any pending resume."""
        if not isinstance(mode, str) or mode not in (
            MODE_STOP_ON_TYPING,
            MODE_TYPE_WHILE_PLAYING,
        ):
            raise ModeError("invalid_mode", f"unknown mode: {mode!r}")
        self._mode = mode
        self._last_key_ms = None
        self._pending_resume = False

    def set_playing(self, playing: bool) -> None:
        """Set playback state. Cancels pending resume when paused."""
        if isinstance(playing, bool) is False:
            raise ModeError("bool_argument", "playing must be a bool")
        if not playing:
            self._pending_resume = False
            self._last_key_ms = None
        self._playing = playing

    def on_key(self, now_ms: int, position_ms: int) -> Action:
        """Handle a key press at ``now_ms`` when playback is at ``position_ms``."""
        self._validate_time(now_ms, position_ms)

        if self._mode == MODE_TYPE_WHILE_PLAYING:
            return Action("none")

        if not self._playing:
            return Action("none")

        if self._last_key_ms is None:
            self._last_key_ms = now_ms
            self._pending_resume = True
            return Action("pause")

        self._last_key_ms = now_ms
        return Action("none")

    def on_tick(self, now_ms: int, position_ms: int) -> Action:
        """Handle a periodic tick at ``now_ms`` when playback is at ``position_ms``."""
        self._validate_time(now_ms, position_ms)

        if self._mode == MODE_TYPE_WHILE_PLAYING:
            return Action("none")

        if not self._pending_resume:
            return Action("none")

        if self._last_key_ms is None:
            self._pending_resume = False
            return Action("none")

        elapsed = now_ms - self._last_key_ms
        if elapsed >= self._resume_idle_ms:
            seek_target = position_ms - self._rewind_ms
            if seek_target < 0:
                seek_target = 0
            self._pending_resume = False
            self._last_key_ms = None
            return Action("resume", seek_to_ms=seek_target)

        return Action("none")

    def _validate_time(self, now_ms: int, position_ms: int) -> None:
        if isinstance(now_ms, bool) or not isinstance(now_ms, int):
            raise ModeError("bool_argument", "now_ms must be an int")
        if isinstance(position_ms, bool) or not isinstance(position_ms, int):
            raise ModeError("bool_argument", "position_ms must be an int")
        if now_ms < 0 or position_ms < 0:
            raise ModeError("negative_time", "time values must be non-negative")
        if self._last_key_ms is not None and now_ms < self._last_key_ms:
            raise ModeError("time_travel", "now_ms must not go backwards")


class ClinicCorrectionMode(db.Model):
    """One correction playback mode per user. version guards stale writes."""

    __tablename__ = "clinic_correction_mode"
    user_id = db.Column(db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"), primary_key=True)
    mode = db.Column(db.String(32), nullable=False)
    version = db.Column(db.Integer, nullable=False)
    updated_at = db.Column(db.DateTime, nullable=False)


def get_mode(user_id: int) -> tuple[str, int]:
    """Return the user's (mode, version); the W6 default with version 0 when unset."""
    row = db.session.execute(
        db.select(ClinicCorrectionMode.mode, ClinicCorrectionMode.version).where(
            ClinicCorrectionMode.user_id == user_id)
    ).first()
    return (DEFAULT_MODE, 0) if row is None else (row[0], row[1])


def set_mode(user_id: int, mode: object, expected_version: object = None) -> tuple[str, int]:
    """Store the user's own mode and return (mode, version).

    Raises ModeError: invalid_mode, bad_version, stale_version.
    """
    if not isinstance(mode, str) or mode not in MODES:
        raise ModeError("invalid_mode", f"unknown mode: {mode!r}")
    if expected_version is not None and (
        type(expected_version) is not int or expected_version < 0
    ):
        raise ModeError("bad_version")
    _, current = get_mode(user_id)
    if expected_version is not None and expected_version != current:
        raise ModeError("stale_version")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        if current == 0:
            db.session.add(ClinicCorrectionMode(
                user_id=user_id, mode=mode, version=1, updated_at=now))
            db.session.flush()
        else:
            result = db.session.execute(
                update(ClinicCorrectionMode)
                .where(ClinicCorrectionMode.user_id == user_id,
                       ClinicCorrectionMode.version == current)
                .values(mode=mode, version=current + 1, updated_at=now)
            )
            if result.rowcount != 1:
                raise ModeError("stale_version")
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        raise ModeError("stale_version") from exc
    except BaseException:
        db.session.rollback()
        raise
    return mode, current + 1
