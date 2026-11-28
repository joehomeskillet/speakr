"""HTTP surface of the processing overview (V8 P17): job health and controlled retry.

Serves exactly what static/js/clinic/jobs-ui.js reads: `GET /api/clinic/v1/jobs/health`
and `POST /api/clinic/v1/jobs/<id>/retry`. It is a thin layer over the existing
`ProcessingJob` rows, `job_health.summarise` and `JobQueue.enqueue`; no new queue.

Visibility: an active site admin (the fork's operations role) sees every job. Any
other signed-in user sees only jobs of dictations the central policy lets that user
view (`pool_policy.decide`, or `policy.decide` for a recording without a dictation).
The payload holds counts, ids, job type, a redacted error class and timestamps: no
transcript text, no patient data, no error detail, no audio path, no parameters.

Retry needs the operations role, CSRF, an `Idempotency-Key` equal to the body
`request_id`, and an `If-Match` equal to `expected_version` (the job's
`retry_count`). It runs inside the caller's `run_once` receipt, so a replay with the
same key returns the stored result, and it enqueues through `JobQueue.enqueue` with a
stable operation key, so even a second key for the same failed attempt gives one job.
A job whose outcome is UNKNOWN, or that already carries its own operation key (a
release or delivery), is never retried here; it goes to reconciliation.

Mounting `create_blueprint` in src/app.py and listing the two endpoints in
route_policy are left to the consumer package that mounts the view.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from hashlib import sha256
from uuid import UUID

from flask import Blueprint, abort, jsonify, request, session
from flask_login import current_user
from flask_wtf.csrf import CSRFError, validate_csrf
from werkzeug.exceptions import HTTPException
from wtforms.validators import ValidationError

from src.clinic import job_health, policy, pool_policy
from src.clinic.admin_dictation_access import _is_active_site_admin
from src.clinic.config import clinic_mode_enabled
from src.clinic.recording_type import is_private_source
from src.clinic.web_ingest import dictations_for_recordings
from src.clinic.parity_dispatch import CommandProblem
from src.database import db
from src.models import ProcessingJob, Recording

PREFIX = "/api/clinic/v1/jobs"
SCAN_LIMIT = 5000
RETRY_KEY_PREFIX = "jobs-retry:"
_OPERATION_KEYS = ("clinic_release_id", "clinic_operation_key")
_DELIVERY_STARTED = "delivery_started"
_TITLES = {400: "Ungültige Anfrage", 401: "Anmeldung erforderlich", 403: "Zugriff verweigert",
           404: "Auftrag nicht gefunden", 409: "Versionskonflikt", 422: "Aktion nicht zulässig",
           428: "Version erforderlich", 503: "Dienst nicht verfügbar"}


class JobProblem(Exception):
    """A refused retry; `code` is a stable upper-case code, `status` the HTTP status."""

    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status = status
        self.code = code


def _problem(status: int, code: str | None = None):
    code = code or {401: "UNAUTHORIZED", 403: "FORBIDDEN", 404: "NOT_FOUND", 428: "INVALID_PAYLOAD"}.get(
        status, "INVALID_PAYLOAD")
    response = jsonify(type="about:blank", title=_TITLES.get(status, "Fehler"), status=status, code=code)
    response.mimetype = "application/problem+json"
    response.status_code = status
    return response


def is_operations(user) -> bool:
    """The operations role: an authenticated, active site administrator."""
    return _is_active_site_admin(db.session, user)


def _params(job: ProcessingJob) -> dict:
    try:
        value = json.loads(job.params) if job.params else {}
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _recent_jobs() -> list[ProcessingJob]:
    return db.session.query(ProcessingJob).order_by(ProcessingJob.id.desc()).limit(SCAN_LIMIT).all()


def visible_jobs(user, jobs: list[ProcessingJob], *, dictations=None) -> list[ProcessingJob]:
    """The jobs `user` may see: all for operations, else those of viewable dictations."""
    recording_ids = {job.recording_id for job in jobs}
    if dictations is None:
        dictations = dictations_for_recordings(recording_ids)
    if is_operations(user):
        private_sources = {rid for (rid,) in db.session.query(Recording.id).filter(
            Recording.id.in_(recording_ids), Recording.user_id != user.id, is_private_source(Recording))}
        return [job for job in jobs
                if job.recording_id not in private_sources
                and policy.recording_type_denial(dictations.get(job.recording_id), user.id, 'view') is None]
    subject = policy.build_subject(user.id, False)
    owners = {}
    allowed: dict[int, bool] = {}
    for recording_id in recording_ids:
        dictation = dictations.get(recording_id)
        if dictation is not None:
            allowed[recording_id] = pool_policy.decide(subject, "view", dictation).allowed
            continue
        if not owners:
            owners = dict(db.session.query(Recording.id, Recording.user_id).filter(
                Recording.id.in_(recording_ids)))
        owner = owners.get(recording_id)
        allowed[recording_id] = owner is not None and policy.decide(
            subject, "view", policy.build_facts(recording_id, owner)).allowed
    return [job for job in jobs if allowed.get(job.recording_id)]


def _state(job: ProcessingJob) -> str:
    """`UNKNOWN` for a failed delivery that may already have reached its target."""
    if job.status == "failed" and job.job_type == "clinic_output" and _params(job).get(_DELIVERY_STARTED):
        return "UNKNOWN"
    return job.status


def _retried_ids(jobs: list[ProcessingJob]) -> set[int]:
    done: set[int] = set()
    for job in jobs:
        key = _params(job).get("clinic_operation_key")
        if isinstance(key, str) and key.startswith(RETRY_KEY_PREFIX):
            try:
                done.add(int(key[len(RETRY_KEY_PREFIX):].split(":")[0]))
            except ValueError:
                continue
    return done


def health_payload(user, now: datetime | None = None) -> dict:
    """Redacted health summary for `user`; failed rows that already have a retry job are left out."""
    now = now or datetime.now(timezone.utc)
    recent = _recent_jobs()
    dictations = dictations_for_recordings(job.recording_id for job in recent)
    jobs = visible_jobs(user, recent, dictations=dictations)
    retried = _retried_ids(jobs)
    by_id = {job.id: job for job in jobs}
    rows = [{
        "id": job.id, "status": job.status, "job_type": job.job_type,
        "created_at": job.created_at or now, "started_at": job.started_at,
        "completed_at": job.completed_at, "error_message": job.error_message or None,
    } for job in jobs if job.status != "failed" or job.id not in retried]
    summary = job_health.summarise(rows, now)
    failures = []
    for failure in summary.recent_failures:
        job = by_id[failure.id]
        failures.append({
            "dictation_id": (dictations[job.recording_id].id if job.recording_id in dictations else None),
            "id": failure.id, "job_type": failure.job_type, "error_class": failure.error_class,
            "message": failure.message, "state": _state(job), "version": job.retry_count,
            "finished_at": failure.finished_at.isoformat() if failure.finished_at else None,
        })
    return {
        "jobs": [{"id": job.id, "dictation_id": (dictations[job.recording_id].id
                  if job.recording_id in dictations else None), "job_type": job.job_type,
                  "state": _state(job), "version": job.retry_count}
                 for job in jobs if job.status != "failed" or job.id not in retried],
        "counts": summary.counts, "stuck_ids": list(summary.stuck_ids),
        "oldest_queued_age_s": summary.oldest_queued_age_s, "recent_failures": failures,
        "can_retry": is_operations(user),
    }


def retry_job(job_id: int, expected_version: int, *, queue=None) -> dict:
    """Enqueue one new job for the failed attempt; raises `JobProblem` when it is not retryable."""
    from src.services.job_queue import ClinicJobError, job_queue
    queue = queue or job_queue
    job = db.session.get(ProcessingJob, job_id, populate_existing=True, with_for_update=True)
    if job is None:
        raise JobProblem(404, "NOT_FOUND")
    if job.status != "failed" or job.retry_count != expected_version:
        raise JobProblem(409, "REVISION_CONFLICT")
    if _state(job) == "UNKNOWN":
        raise JobProblem(422, "RECONCILE_REQUIRED")
    params = _params(job)
    if any(params.get(name) for name in _OPERATION_KEYS):
        raise JobProblem(422, "NOT_RETRYABLE")
    params["clinic_operation_key"] = f"{RETRY_KEY_PREFIX}{job.id}:{expected_version}"
    try:
        new_id = queue.enqueue(job.user_id, job.recording_id, job.job_type, params)
    except ClinicJobError as error:
        raise JobProblem(409, "REVISION_CONFLICT") from error
    return {"job_id": job_id, "new_job_id": new_id, "status": "queued"}


def create_blueprint(*, run_once, queue=None) -> Blueprint:
    """Build the blueprint; `run_once` is the shared receipt, `queue` defaults to the app queue."""
    bp = Blueprint("clinic_jobs", __name__)

    @bp.before_request
    def authenticate() -> None:
        if not clinic_mode_enabled():
            abort(404)
        if not (current_user.is_authenticated and current_user.is_active
                and session.get("_user_id") == current_user.get_id()):
            abort(401)
        if request.method != "GET":
            try:
                validate_csrf(request.headers.get("X-CSRFToken") or request.headers.get("X-CSRF-Token"))
            except ValidationError:
                abort(403)

    @bp.errorhandler(HTTPException)
    def http_problem(error: HTTPException):
        status = 403 if isinstance(error, CSRFError) else error.code
        return _problem(status) if status in _TITLES else error

    @bp.after_request
    def no_store(response):
        response.headers["Cache-Control"] = "no-store"
        return response

    @bp.get(PREFIX + "/health")
    def health():
        return jsonify(health_payload(current_user))

    @bp.post(PREFIX + "/<int:job_id>/retry")
    def retry(job_id: int):
        # The role is checked before the job is looked up: no existence leak.
        if not is_operations(current_user):
            return _problem(403)
        body = request.get_json(silent=True)
        if (not isinstance(body, dict) or body.keys() != {"request_id", "expected_version"}
                or type(body["expected_version"]) is not int or body["expected_version"] < 0):
            return _problem(400)
        try:
            key = str(UUID(request.headers.get("Idempotency-Key", "")))
            if str(UUID(str(body["request_id"]))) != key:
                return _problem(400)
        except ValueError:
            return _problem(400)
        expected = body["expected_version"]
        match = request.headers.get("If-Match")
        if match is None:
            return _problem(428)
        if match.strip('"') != str(expected):
            return _problem(400)
        job = db.session.get(ProcessingJob, job_id)
        if job is not None:
            dictation = dictations_for_recordings([job.recording_id]).get(job.recording_id)
            if policy.recording_type_denial(dictation, current_user.id, 'edit',
                                            source=db.session.get(Recording, job.recording_id)) is not None:
                return _problem(404)
        digest = sha256(json.dumps(["jobs.retry", job_id, expected], sort_keys=True).encode()).hexdigest()
        try:
            result = run_once((current_user.id, "jobs.retry", str(job_id), key), digest,
                              lambda: retry_job(job_id, expected, queue=queue))
        except CommandProblem as error:
            return _problem(409, "IDEMPOTENCY_CONFLICT" if str(error) == "IDEMPOTENCY_CONFLICT" else "REVISION_CONFLICT")
        except JobProblem as error:
            return _problem(error.status, error.code)
        return jsonify(result)

    return bp
