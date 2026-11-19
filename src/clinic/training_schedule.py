"""Schedule ASR training intents on the existing job queue (CLINIC_MODE only).

Input is a sealed training manifest (``training_manifest.build_manifest``). This
module decides *whether and when* an intent may be queued and queues exactly one
``ProcessingJob`` per manifest hash and profile version. It trains nothing,
records no ``ClinicAsrTrainingRun`` and never claims learning.

Outcomes are separate states, never one generic failure:
``scheduled``, ``already_scheduled``, ``deferred`` (outside the optimisation
window), ``insufficient_data`` (below the audio/utterance thresholds) and
``failed`` (the queue refused). ``training_state`` reports an intent's job later:
``queued``, ``running``, ``cancelled``, ``completed``, ``failed``. Unauthorised
callers and stale manifests/profiles raise ``TrainingScheduleError``.

All window arithmetic is wall-clock time in one configured zone
(``Europe/Zurich``); instants are compared as aware datetimes, so a DST shift
never moves a window.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from src.clinic.config import clinic_mode_enabled
from src.clinic.models import ClinicDictation
from src.clinic.release import active_release
from src.clinic.training_manifest import _seal
from src.database import db

JOB_TYPE = "asr_training"
PARAM = "clinic_training"
TIMEZONE = "Europe/Zurich"

SCHEDULED = "scheduled"
ALREADY_SCHEDULED = "already_scheduled"
DEFERRED = "deferred"
INSUFFICIENT_DATA = "insufficient_data"
FAILED = "failed"

_lock = threading.Lock()


class TrainingScheduleError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Window:
    """Daily optimisation window in local time. ``end <= start`` wraps midnight."""

    start: time
    end: time
    weekdays: frozenset[int] = frozenset(range(7))  # Monday=0, day the window starts


@dataclass(frozen=True)
class ScheduleConfig:
    windows: tuple[Window, ...] = ()  # empty: no window restriction
    min_audio_seconds: float = 0.0  # acoustic only
    min_utterances: int = 1
    timezone: str = TIMEZONE


def parse_window(start: str, end: str, weekdays=None) -> Window:
    try:
        window = Window(
            time.fromisoformat(start),
            time.fromisoformat(end),
            frozenset(range(7)) if weekdays is None else frozenset(weekdays),
        )
    except (TypeError, ValueError):
        raise TrainingScheduleError("window_invalid") from None
    if window.start == window.end or not window.weekdays or not window.weekdays <= set(range(7)):
        raise TrainingScheduleError("window_invalid")
    return window


def schedule_training(
    actor_id: int,
    manifest: dict,
    *,
    config: ScheduleConfig,
    now: datetime,
    stats: dict[str, dict] | None = None,
    expected_profile_version: int,
    queue=None,
) -> dict:
    """Queue one training intent for ``manifest`` at ``expected_profile_version``.

    ``stats`` maps ``transcript_revision_id`` to ``{"audio_seconds", "utterances"}``;
    a missing entry counts one utterance and no audio. Repeating the call, also
    concurrently, returns the one existing active job as ``already_scheduled``.
    """
    if not clinic_mode_enabled():
        raise TrainingScheduleError("clinic_mode_required")
    if now.tzinfo is None:
        raise TrainingScheduleError("now_naive")
    items = _verified_items(actor_id, manifest)
    profile_version = _profile_version(manifest["subject_user_id"], expected_profile_version)
    _check_fresh(items)

    audio, utterances = _volume(manifest, items, stats or {})
    short = utterances < config.min_utterances or (
        manifest["kind"] == "acoustic" and audio < config.min_audio_seconds
    )
    base = {"manifest_hash": manifest["manifest_hash"], "profile_version": profile_version}
    if not items or short:
        return {**base, "state": INSUFFICIENT_DATA, "audio_seconds": audio, "utterances": utterances}
    opens = _next_open(config, now)
    if opens is not None:
        return {**base, "state": DEFERRED, "reason": "outside_window", "next_window_start": opens}

    return _enqueue(manifest, items, profile_version, queue, base)


def training_state(manifest_hash: str, profile_version: int, user_id: int) -> str:
    """Latest job state of an intent: queued, running, cancelled, completed, failed, none."""
    job = _latest_job(manifest_hash, profile_version, user_id)
    if job is None:
        return "none"
    if job.cancel_requested_at is not None and job.status in ("queued", "processing"):
        return "cancelled"
    return {"queued": "queued", "processing": "running"}.get(job.status, job.status)


def _verified_items(actor_id, manifest) -> list[dict]:
    if not isinstance(manifest, dict) or "manifest_hash" not in manifest:
        raise TrainingScheduleError("manifest_invalid")
    body = {k: v for k, v in manifest.items() if k != "manifest_hash"}
    if _seal(body)["manifest_hash"] != manifest["manifest_hash"]:
        raise TrainingScheduleError("manifest_invalid")
    if (
        isinstance(actor_id, bool)
        or not isinstance(actor_id, int)
        or actor_id != manifest.get("actor_id")
        or actor_id != manifest.get("subject_user_id")
    ):
        raise TrainingScheduleError("unauthorized")
    return list(manifest["items"])


def _profile_version(user_id: int, expected: int) -> int:
    from src.clinic.asr_profiles import ClinicAsrProfile

    profile = ClinicAsrProfile.query.filter_by(user_id=user_id).first()
    if profile is None:
        raise TrainingScheduleError("profile_missing")
    if profile.version != expected:
        raise TrainingScheduleError("stale_version")
    return profile.version


def _check_fresh(items) -> None:
    """Every approved release the manifest froze must still be the active one."""
    for item in items:
        dictation = db.session.get(ClinicDictation, item["dictation_id"])
        release = None if dictation is None else active_release(dictation)
        if release is None or release.id != item["approval"]["release_id"]:
            raise TrainingScheduleError("stale_version")


def _volume(manifest, items, stats) -> tuple[float, int]:
    audio, utterances = 0.0, 0
    for item in items:
        row = stats.get(item["transcript_revision_id"], {})
        audio += max(float(row.get("audio_seconds", 0.0)), 0.0)
        utterances += max(int(row.get("utterances", 1)), 0)
    return audio, utterances


def _next_open(config: ScheduleConfig, now: datetime):
    """None when ``now`` is inside a window (or none is set), else the next start (zone-local)."""
    if not config.windows:
        return None
    zone = ZoneInfo(config.timezone)
    local = now.astimezone(zone)
    upcoming = []
    for window in config.windows:
        for offset in range(-1, 9):
            day = local.date() + timedelta(days=offset)
            if day.weekday() not in window.weekdays:
                continue
            start = datetime.combine(day, window.start, tzinfo=zone)
            end_day = day if window.end > window.start else day + timedelta(days=1)
            end = datetime.combine(end_day, window.end, tzinfo=zone)
            if start <= local < end:
                return None
            if start > local:
                upcoming.append(start)
    return min(upcoming).isoformat()


def _intent_params(manifest, profile_version, item_count) -> dict:
    return {
        PARAM: {
            "manifest_hash": manifest["manifest_hash"],
            "profile_version": profile_version,
            "kind": manifest["kind"],
            "scope": manifest["scope"],
            "pool_id": manifest["pool_id"],
            "items": item_count,
        }
    }


def _latest_job(manifest_hash, profile_version, user_id):
    from src.models import ProcessingJob

    rows = (
        ProcessingJob.query.filter(
            ProcessingJob.job_type == JOB_TYPE,
            ProcessingJob.user_id == user_id,
            ProcessingJob.params.contains(manifest_hash),
        )
        .order_by(ProcessingJob.id.desc())
        .all()
    )
    for job in rows:
        intent = json.loads(job.params or "{}").get(PARAM, {})
        if intent.get("manifest_hash") == manifest_hash and intent.get("profile_version") == profile_version:
            return job
    return None


def _enqueue(manifest, items, profile_version, queue, base) -> dict:
    from src.models import ProcessingJob, Recording

    if queue is None:
        from src.services.job_queue import job_queue as queue
    user_id = manifest["subject_user_id"]
    # The queue needs a recording: anchor on the first frozen dictation, so the
    # same manifest always maps to the same (recording, job type) slot.
    anchor = db.session.get(ClinicDictation, items[0]["dictation_id"]).recording_id
    params = _intent_params(manifest, profile_version, len(items))
    recording = db.session.get(Recording, anchor)
    status_before = None if recording is None else recording.status
    with _lock:
        existing = _latest_job(manifest["manifest_hash"], profile_version, user_id)
        if existing is not None and existing.status in ("queued", "processing"):
            return {**base, "state": ALREADY_SCHEDULED, "job_id": existing.id}
        try:
            job_id = queue.enqueue(user_id, anchor, JOB_TYPE, params=params)
        except Exception as exc:  # the queue refused: report, never raise a half state
            db.session.rollback()
            return {**base, "state": FAILED, "reason": type(exc).__name__}
        _restore_status(anchor, status_before)
        job = db.session.get(ProcessingJob, job_id)
        intent = json.loads(job.params or "{}").get(PARAM, {})
        if intent.get("manifest_hash") != manifest["manifest_hash"] or (
            intent.get("profile_version") != profile_version
        ):
            # The one active training slot of this recording belongs to another intent.
            return {**base, "state": DEFERRED, "reason": "other_training_active", "job_id": job_id}
        # A concurrent caller in another process won the insert: same job id, same intent.
        state = SCHEDULED if existing is None or existing.id != job_id else ALREADY_SCHEDULED
        return {**base, "state": state, "job_id": job_id}


def _restore_status(recording_id, status_before) -> None:
    """The queue marks a recording QUEUED for non-background jobs; training must not."""
    from src.models import Recording

    recording = db.session.get(Recording, recording_id)
    if recording is not None and status_before is not None and recording.status != status_before:
        recording.status = status_before
        db.session.commit()
