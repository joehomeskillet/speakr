"""Admin HTTP surface for ASR optimisation runs (V8 P15), CLINIC_MODE only.

A thin layer over ``training_manifest``, ``training_schedule`` and ``job_cancel``;
no rule is re-implemented. Active site admins only (403 otherwise). A run is one
``ProcessingJob`` of type ``asr_training`` queued by ``schedule_training``: the job
key is the manifest hash plus the profile version, so a repeated start returns the
same job. The training itself runs in the queue worker through ``training_runner``
with the pinned local provider; this module never calls a network service and never
handles audio. Voice profiles stay opt-in: acoustic manifests need the author consent
that ``build_manifest`` already checks.

The services bind a manifest to its own author (actor == subject), so a manifest is
always built for the calling admin's own dictations; an admin cannot start a run for
another user.

POST /runs may also send one window (local ``start``/``end``), ``weekdays``
(Monday is 0), ``min_utterances`` and ``min_audio_seconds``. Those fields go
to ``ScheduleConfig``. Each included item counts as one utterance; audio
seconds are the stored recording duration, and a missing duration counts as
none. Outside the window the answer is ``deferred`` and names the next start;
too little material is ``insufficient_data``. Neither outcome queues a job.

Mounting needs the route-policy rows in ``ENDPOINTS`` (admin) and one
``register_blueprint(routes_training.blueprint)`` in clinic mode; both belong to the
consumer package (see handover).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from flask import Blueprint, abort, current_app, jsonify, request, session
from flask_login import current_user
from flask_wtf.csrf import CSRFError, ValidationError, validate_csrf

from src.clinic.admin_dictation_access import _is_active_site_admin
from src.clinic.config import clinic_mode_enabled
from src.clinic.models import ClinicDictation
from src.clinic.revisions import current_revision
from src.clinic.training_manifest import TrainingManifestError, build_manifest
from src.clinic.training_schedule import (
    JOB_TYPE,
    PARAM,
    ScheduleConfig,
    TrainingScheduleError,
    parse_window,
    schedule_training,
    training_state,
)
from src.database import db

PREFIX = "/api/admin/clinic/training"
MAX_BODY_BYTES = 4 * 1024
_START_KEYS = {
    "kind", "scope", "pool_id", "expected_profile_version",
    "window", "weekdays", "min_utterances", "min_audio_seconds",
}
_STATUS = {
    "unauthorized": 403, "stale_version": 409, "profile_missing": 404,
    "clinic_mode_required": 404, "not_found": 404,
}

blueprint = Blueprint("clinic_training", __name__)

ENDPOINTS = (
    "clinic_training.manifest", "clinic_training.schedule", "clinic_training.start",
    "clinic_training.status", "clinic_training.cancel",
)


def _reply(payload: dict, status: int = 200):
    response = jsonify(payload)
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    return response


def _csrf_refusal():
    return _reply({"error": "csrf", "code": "csrf_failed"}, 403)


def _refuse(code: str):
    return _reply({"error": code.replace("_", " "), "code": code}, _STATUS.get(code, 400))


@blueprint.before_request
def _guard():
    if not clinic_mode_enabled():
        abort(404)
    if not (current_user.is_authenticated and current_user.is_active
            and session.get("_user_id") == current_user.get_id()):
        return _reply({"error": "unauthenticated", "code": "unauthenticated"}, 401)
    if not _is_active_site_admin(db.session, current_user):
        return _refuse("unauthorized")
    if request.method != "GET" and current_app.config.get("WTF_CSRF_ENABLED", True):
        try:
            validate_csrf(request.headers.get("X-CSRFToken") or request.headers.get("X-CSRF-Token"))
        except ValidationError:
            return _csrf_refusal()
    return None


@blueprint.errorhandler(CSRFError)
def _csrf_failed(_error):
    return _csrf_refusal()


def _own_manifest(kind, scope, pool_id) -> dict:
    """Manifest of the calling admin's own dictations at their current heads."""
    query = ClinicDictation.query.filter_by(author_user_id=current_user.id).filter(ClinicDictation.is_report)
    if scope == "pool":
        query = query.filter_by(pool_id=pool_id)
    candidates = []
    for dictation in query.order_by(ClinicDictation.id).all():
        head = current_revision(dictation)
        if head is not None:
            candidates.append({"dictation": dictation, "expected_revision_id": head.id})
    return build_manifest(
        current_user.id, scope=scope, subject_user_id=current_user.id, kind=kind,
        candidates=candidates, pool_id=pool_id,
    )


def _intent_row(job) -> dict:
    intent = json.loads(job.params or "{}").get(PARAM, {})
    return {
        "job_id": job.id,
        "user_id": job.user_id,
        "manifest_hash": intent.get("manifest_hash"),
        "profile_version": intent.get("profile_version"),
        "kind": intent.get("kind"),
        "scope": intent.get("scope"),
        "pool_id": intent.get("pool_id"),
        "items": intent.get("items"),
        "state": training_state(
            intent.get("manifest_hash", ""), intent.get("profile_version"), job.user_id),
    }


def _training_jobs():
    """Queued optimisation intents only (job type asr_training); no recording rows are read."""
    from src.models import ProcessingJob

    return ProcessingJob.query.filter_by(job_type=JOB_TYPE).order_by(ProcessingJob.id).all()


def _training_job(job_id):
    from src.models import ProcessingJob

    job = db.session.get(ProcessingJob, job_id)
    return job if job is not None and job.job_type == JOB_TYPE else None


def _query_manifest_args():
    kind = request.args.get("kind", "language_model")
    scope = request.args.get("scope", "author")
    pool_id = request.args.get("pool_id") or None
    return kind, scope, pool_id


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _whole_number(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _audio_seconds(value) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    number = float(value)
    return number >= 0.0 and number != float("inf")


def _weekday_list(value) -> bool:
    return (
        isinstance(value, list)
        and len(value) > 0
        and all(_whole_number(day) for day in value)
    )


def _material_stats(manifest: dict) -> dict:
    """Stored duration per revision. One utterance per item; unknown duration is no audio."""
    from src.models import Recording

    stats = {}
    for item in manifest["items"]:
        dictation = db.session.get(ClinicDictation, item["dictation_id"])
        recording = None if dictation is None else db.session.get(Recording, dictation.recording_id)
        seconds = None if recording is None else recording.audio_duration_seconds
        stats[item["transcript_revision_id"]] = {
            "audio_seconds": 0.0 if seconds is None else max(float(seconds), 0.0),
            "utterances": 1,
        }
    return stats


def _schedule_config(body: dict) -> ScheduleConfig:
    """Forward the optional window and data floors. Shapes that do not fit are refused."""
    utterances = body["min_utterances"] if "min_utterances" in body else 1
    audio_seconds = body["min_audio_seconds"] if "min_audio_seconds" in body else 0
    if not _whole_number(utterances) or not _audio_seconds(audio_seconds):
        raise TrainingScheduleError("invalid_payload")
    if "weekdays" in body and "window" not in body:
        raise TrainingScheduleError("invalid_payload")
    windows = ()
    if "window" in body:
        window = body["window"]
        if not isinstance(window, dict) or set(window) != {"start", "end"}:
            raise TrainingScheduleError("invalid_payload")
        start, end = window["start"], window["end"]
        if not isinstance(start, str) or not isinstance(end, str):
            raise TrainingScheduleError("window_invalid")
        days = None
        if "weekdays" in body:
            days = body["weekdays"]
            if not _weekday_list(days):
                raise TrainingScheduleError("window_invalid")
        windows = (parse_window(start, end, days),)
    return ScheduleConfig(
        windows=windows, min_utterances=utterances, min_audio_seconds=float(audio_seconds),
    )


@blueprint.route(PREFIX + "/manifest", methods=["GET"])
def manifest():
    """Sealed manifest of the admin's own approved corrections (read only, starts nothing)."""
    kind, scope, pool_id = _query_manifest_args()
    try:
        return _reply(_own_manifest(kind, scope, pool_id))
    except TrainingManifestError as error:
        return _refuse(error.code)


@blueprint.route(PREFIX + "/schedule", methods=["GET"])
def schedule():
    """Every queued optimisation intent with its job state."""
    return _reply({"runs": [_intent_row(job) for job in _training_jobs()]})


@blueprint.route(PREFIX + "/runs", methods=["POST"])
def start():
    """Queue one run. The same manifest and profile version always give the same job."""
    body = request.get_json(silent=True) if request.is_json else None
    if (
        not isinstance(body, dict)
        or (request.content_length or 0) > MAX_BODY_BYTES
        or body.keys() - _START_KEYS
    ):
        return _refuse("invalid_payload")
    version = body.get("expected_profile_version")
    if isinstance(version, bool) or not isinstance(version, int):
        return _refuse("invalid_payload")
    try:
        config = _schedule_config(body)
    except TrainingScheduleError as error:
        return _refuse(error.code)
    pool_id = body.get("pool_id")
    try:
        sealed = _own_manifest(body.get("kind", "language_model"), body.get("scope", "author"), pool_id)
        result = schedule_training(
            current_user.id, sealed, config=config, stats=_material_stats(sealed),
            now=_now(), expected_profile_version=version,
        )
    except (TrainingManifestError, TrainingScheduleError) as error:
        db.session.rollback()
        return _refuse(error.code)
    return _reply(result, 201 if result["state"] == "scheduled" else 200)


@blueprint.route(PREFIX + "/runs/<int:job_id>", methods=["GET"])
def status(job_id):
    job = _training_job(job_id)
    if job is None:
        return _refuse("not_found")
    return _reply(_intent_row(job))


@blueprint.route(PREFIX + "/runs/<int:job_id>/cancel", methods=["POST"])
def cancel(job_id):
    """Cancel a waiting run (row removed) or mark a running one (stops at the next safe point)."""
    from src.services.job_cancel import cancel_job

    if _training_job(job_id) is None:
        return _refuse("not_found")
    outcome = cancel_job(job_id)
    return _reply({"job_id": job_id, "outcome": outcome})
