"""ASR wait policy for the clinic dictation editor.

Decides what the editor shows while speech recognition runs and when the
user may start manual transcription instead. A late ASR result is kept as a
side revision and never replaces manual work. Approval is independent:
nothing here knows about it.

This module is pure Python. No Flask, no routes, no database access,
no ``datetime.now()``. State lives in plain dataclasses; persistence and
routes are a later wiring package.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


ASR_STATES = ("pending", "running", "succeeded", "failed", "skipped")


class WaitError(ValueError):
    """The wait policy rejected the input before computing a view."""

    def __init__(self, message: str, *, code: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class WaitConfig:
    """Configuration thresholds for the wait policy.

    offer_manual_after_s -- seconds until the editor offers manual entry
    hard_timeout_s      -- seconds until ASR is considered timed out
    wait_disabled       -- while true, a running recognition never offers manual entry
    """
    offer_manual_after_s: int = 120
    hard_timeout_s: int = 900
    wait_disabled: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.offer_manual_after_s, int) or isinstance(self.offer_manual_after_s, bool):
            raise WaitError("offer_manual_after_s must be an int", code="bad_config")
        if not isinstance(self.hard_timeout_s, int) or isinstance(self.hard_timeout_s, bool):
            raise WaitError("hard_timeout_s must be an int", code="bad_config")
        if not isinstance(self.wait_disabled, bool):
            raise WaitError("wait_disabled must be a bool", code="bad_config")
        if self.hard_timeout_s <= self.offer_manual_after_s:
            raise WaitError("hard_timeout_s must be greater than offer_manual_after_s", code="bad_config")
        if self.offer_manual_after_s < 0 or self.hard_timeout_s < 0:
            raise WaitError("thresholds must be non-negative", code="bad_config")


@dataclass(frozen=True)
class WaitView:
    """What the editor shows for the current ASR state and wait time.

    state                -- one of: waiting, offer_manual, timed_out, manual_active, asr_done, asr_failed, skipped
    can_start_manual     -- whether the user may start manual transcription now
    late_result_policy   -- always 'side_revision' (never 'replace')
    message              -- non-empty German user-facing text, no raw state tokens
    """
    state: str
    can_start_manual: bool
    late_result_policy: str
    message: str


def view_state(
    asr_status: str,
    waited_s: int,
    *,
    manual_started: bool,
    config: WaitConfig = WaitConfig(),
) -> WaitView:
    """Compute the wait view for the given ASR status and elapsed time.

    Args:
        asr_status: one of ASR_STATES (pending, running, succeeded, failed, skipped)
        waited_s: seconds elapsed since ASR started (non-negative int, not bool)
        manual_started: whether the user has already started manual transcription (bool, not int)
        config: WaitConfig with offer_manual_after_s and hard_timeout_s

    Returns:
        WaitView with state, can_start_manual, late_result_policy, message

    Raises:
        WaitError: on invalid input (unknown status, negative/bool waited_s,
            hard_timeout_s <= offer_manual_after_s, non-bool manual_started)
    """
    _validate_inputs(asr_status, waited_s, manual_started, config)

    if asr_status == "failed":
        return WaitView(
            state="asr_failed",
            can_start_manual=True,
            late_result_policy="side_revision",
            message="Spracherkennung fehlgeschlagen. Manuell schreiben?",
        )

    if asr_status == "skipped":
        return WaitView(
            state="skipped",
            can_start_manual=True,
            late_result_policy="side_revision",
            message="Spracherkennung übersprungen. Manuell schreiben?",
        )

    if asr_status == "succeeded":
        if manual_started:
            return WaitView(
                state="manual_active",
                can_start_manual=False,
                late_result_policy="side_revision",
                message="Manuelle Abschrift aktiv. Spracherkennungsergebnis als Nebenversion gespeichert.",
            )
        return WaitView(
            state="asr_done",
            can_start_manual=False,
            late_result_policy="side_revision",
            message="Spracherkennung abgeschlossen.",
        )

    # asr_status is "pending" or "running"
    if manual_started:
        return WaitView(
            state="manual_active",
            can_start_manual=True,
            late_result_policy="side_revision",
            message="Manuelle Abschrift aktiv. Spracherkennung läuft weiter.",
        )

    # The deadline is switched off: no manual offer for the whole run, including timeout.
    if config.wait_disabled:
        return WaitView(
            state="waiting",
            can_start_manual=False,
            late_result_policy="side_revision",
            message="Spracherkennung läuft …",
        )

    if waited_s >= config.hard_timeout_s:
        return WaitView(
            state="timed_out",
            can_start_manual=True,
            late_result_policy="side_revision",
            message="Spracherkennung dauert sehr lange. Manuell schreiben?",
        )

    if waited_s >= config.offer_manual_after_s:
        return WaitView(
            state="offer_manual",
            can_start_manual=True,
            late_result_policy="side_revision",
            message="Spracherkennung dauert länger. Manuell schreiben?",
        )

    return WaitView(
        state="waiting",
        can_start_manual=False,
        late_result_policy="side_revision",
        message="Spracherkennung läuft …",
    )


def config_from_options(options: dict | None) -> WaitConfig:
    """Build the wait config from one profile's stored options.

    ``wait_disabled`` wins over ``skip_wait_minutes``. A minute value the
    config rejects (it would meet or pass the hard timeout) falls back to
    the defaults; the stored minutes stay untouched.
    """
    data = options or {}
    if data.get("wait_disabled") is True:
        return WaitConfig(wait_disabled=True)
    minutes = data.get("skip_wait_minutes")
    if isinstance(minutes, int) and not isinstance(minutes, bool) and minutes > 0:
        try:
            return WaitConfig(offer_manual_after_s=minutes * 60)
        except WaitError:
            return WaitConfig()
    return WaitConfig()


def profile_wait_view(options: dict | None) -> dict[str, Any]:
    """GET payload: the effective deadline, or the shutdown with no offer while ASR runs."""
    data = options or {}
    config = config_from_options(data)
    at_timeout = view_state("running", config.hard_timeout_s, manual_started=False, config=config)
    if config.wait_disabled:
        view: dict[str, Any] = {
            "wait_disabled": True,
            "offer_manual_after_s": None,
            "hard_timeout_s": config.hard_timeout_s,
            "manual_on_timeout": at_timeout.can_start_manual,
            "during_run": {
                "state": at_timeout.state,
                "can_start_manual": at_timeout.can_start_manual,
                "message": at_timeout.message,
            },
        }
    else:
        view = {
            "offer_manual_after_s": config.offer_manual_after_s,
            "hard_timeout_s": config.hard_timeout_s,
            "manual_on_timeout": at_timeout.can_start_manual,
        }
    minutes = data.get("skip_wait_minutes")
    if isinstance(minutes, int) and not isinstance(minutes, bool) and minutes > 0:
        view["skip_wait_minutes"] = minutes
    return view


def _validate_inputs(
    asr_status: str,
    waited_s: int,
    manual_started: bool,
    config: WaitConfig,
) -> None:
    if asr_status not in ASR_STATES:
        raise WaitError(f"unknown ASR status: {asr_status!r}", code="unknown_status")
    if isinstance(waited_s, bool) or not isinstance(waited_s, int):
        raise WaitError("waited_s must be an int", code="bad_waited_s")
    if waited_s < 0:
        raise WaitError("waited_s must be non-negative", code="bad_waited_s")
    if not isinstance(manual_started, bool):
        raise WaitError("manual_started must be a bool", code="bad_manual_started")
    # config validation happens in WaitConfig.__post_init__