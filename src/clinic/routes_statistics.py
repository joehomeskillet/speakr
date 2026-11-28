"""Scoped dictation aggregates and optional synthetic statistics (PAR-OPS-005/006).

Thin HTTP layer over ``statistics_access.read_aggregates``: the scoped query
runs before aggregation, so counts never include dictations outside the
caller's explicit author or author-pool grants. The response is the
``StatisticsResult.as_dict()`` shape: ids, counts and seconds only, no
transcript text, audio or patient data.

Rules:

* The actor always comes from the session; the request never names an actor or
  role. The statistics role is held only by an active clinic account (a
  disabled or policy-less account gets 403); the scope itself is the grant
  store, and an account without a grant gets 403 as well.
* Date bounds: ``YYYY-MM-DD`` is local midnight in Europe/Zurich, a full ISO
  value needs an offset. The window is half-open [from, to).
* The response header ``X-Grant-Version`` carries the grant-set version; send
  it back as ``expected_version`` to detect moved grants (409).
The collection, period and retention routes are for active site admins in
synthetic staging. Optional counts come from successful browser actions.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from flask import Blueprint, abort, current_app, jsonify, request, session
from flask_login import current_user
from flask_wtf.csrf import CSRFError, ValidationError, validate_csrf
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.clinic import statistics_retention as retention
from src.clinic.admin_dictation_access import _is_active_site_admin
from src.clinic.config import clinic_mode_enabled
from src.clinic.identity import ClinicAccountPolicy
from src.clinic.parity_dispatch import _problem
from src.clinic.statistics import StatisticsError, StatisticsQuery
from src.clinic.statistics_access import StatisticsAccessError, read_aggregates, version_of
from src.clinic.statistics_export import StatisticsExportError, _parse_bound
from src.database import db

PREFIX = "/api/clinic/v1/statistics"
PATH = PREFIX + "/aggregates"
ROLE = "statistics"
_ARGS = frozenset({"time_basis", "from", "to", "grain", "group_by", "expected_version"})
_REFUSED = frozenset({"not_statistics", "no_grant", "capability_denied", "clinic_mode_off"})
_SAMPLE_ACTIONS = {
    ("GET", "clinic_recordings.list_recordings", 200): retention.USAGE,
    ("GET", "clinic_worklist.rows", 200): retention.USAGE,
    ("GET", "clinic_history.get_history", 200): retention.HISTORY,
    ("POST", "clinic_workflow.release", 201): retention.HISTORY,
}
_COVERAGE_STATUS = {retention.UNCOLLECTED: "nicht erfasst", retention.COLLECTED: "erfasst",
                    retention.PARTIAL: "teilweise erfasst"}

blueprint = Blueprint("clinic_statistics", __name__)


def _reply(response, status=200):
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    return response


def _fail(code):
    body, status = _problem(code)
    return _reply(body, status)


def _signed_in():
    return (current_user.is_authenticated and current_user.is_active
            and session.get("_user_id") == current_user.get_id())


def _synthetic_staging():
    return clinic_mode_enabled() and os.environ.get("CLINIC_PROFILE", "").strip().lower() == "staging"


def _now():
    return datetime.now(timezone.utc)


def _query(args) -> tuple[StatisticsQuery, int | None]:
    if args.keys() - _ARGS:
        raise StatisticsError("arguments")
    expected = args.get("expected_version")
    if expected is not None:
        try:
            expected = int(expected, 10)
        except ValueError:
            raise StatisticsError("version") from None
    query = StatisticsQuery(
        time_basis=args.get("time_basis", "created"),
        start=_parse_bound(args.get("from"), "start"),
        end=_parse_bound(args.get("to"), "end"),
        grain=args.get("grain") or None,
        group_by=tuple(name for name in args.get("group_by", "").split(",") if name),
    )
    return query, expected


@blueprint.get(PATH)
def aggregates():
    if not clinic_mode_enabled():
        abort(404)
    if not _signed_in():
        return _fail("UNAUTHORIZED")
    account = db.session.get(ClinicAccountPolicy, current_user.id)
    if account is None or account.status != "active":
        return _fail("FORBIDDEN")
    try:
        query, expected = _query(request.args)
        # The scope wraps the session it is given; use the request's own session, not the shared proxy.
        result = read_aggregates(db.session(), current_user.id, query, role=ROLE, expected_version=expected)
    except (StatisticsError, StatisticsExportError):
        return _fail("INVALID_PAYLOAD")
    except StatisticsAccessError as error:
        if error.code == "stale_version":
            return _fail("REVISION_CONFLICT")
        return _fail("FORBIDDEN" if error.code in _REFUSED else "INVALID_PAYLOAD")
    response = _reply(jsonify(result.as_dict()))
    response.headers["X-Grant-Version"] = str(version_of(current_user.id))
    return response


@blueprint.before_request
def require_collection_admin():
    if request.endpoint == "clinic_statistics.aggregates":
        return None
    if not _synthetic_staging():
        abort(404)
    if not _signed_in():
        return _fail("UNAUTHORIZED")
    if not _is_active_site_admin(db.session, current_user):
        return _fail("FORBIDDEN")
    if request.method in {"PUT", "POST"}:
        try:
            validate_csrf(request.headers.get("X-CSRFToken") or request.headers.get("X-CSRF-Token"))
        except ValidationError:
            return _fail("FORBIDDEN")
    return None


blueprint.register_error_handler(CSRFError, lambda _error: _fail("FORBIDDEN"))


@blueprint.after_app_request
def collect_browser_action(response):
    kind = _SAMPLE_ACTIONS.get((request.method, request.endpoint, response.status_code))
    if kind is not None and _synthetic_staging() and _signed_in():
        try:
            with Session(db.engine) as store, store.begin():
                retention.lock_collection(store)
                retention.collect_sample(store, kind, _now())
        except SQLAlchemyError:
            current_app.logger.warning("Clinic statistics sample could not be collected", exc_info=True)
    return response


@blueprint.route(PREFIX + "/collection", methods=["GET", "PUT"])
def collection_policy():
    if request.method != "PUT":
        switches = {kind: retention.current_switch(db.session, kind) for kind in sorted(retention.KINDS)}
        return _reply(jsonify({kind: {"enabled": bool(switch and switch.enabled),
                                     "retention_days": switch.retention_days if switch else None}
                               for kind, switch in switches.items()}))
    body = request.get_json(silent=True)
    if (not isinstance(body, dict) or body.keys() != {"kind", "enabled", "retention_days"}
            or not isinstance(body["kind"], str) or body["kind"] not in retention.KINDS
            or type(body["enabled"]) is not bool or type(body["retention_days"]) is not int
            or not 1 <= body["retention_days"] <= 3650):
        return _fail("INVALID_PAYLOAD")
    with Session(db.engine) as store, store.begin():
        retention.lock_collection(store)
        retention.change_switch(store, **body, now=_now())
    return _reply(jsonify(body))


@blueprint.get(PREFIX + "/<any(usage,history):kind>")
def collection_period(kind):
    if request.args.keys() - {"from", "to"}:
        return _fail("INVALID_PAYLOAD")
    now = _now()
    try:
        start = _parse_bound(request.args.get("from"), "start")
        end = _parse_bound(request.args.get("to"), "end") or now
        period = retention.collected_period(db.session, kind, start, end, now)
    except (StatisticsExportError, retention.CollectionError, OverflowError):
        return _fail("INVALID_PAYLOAD")
    return _reply(jsonify(**period, status=_COVERAGE_STATUS[period["coverage"]]))


@blueprint.route(PREFIX + "/retention", methods=["GET", "POST"])
def collection_retention():
    if request.args:
        return _fail("INVALID_PAYLOAD")
    now = _now()
    if request.method != "POST":
        return _reply(jsonify(expired=retention.expire_samples(db.session, now, apply=False)))
    with Session(db.engine) as store, store.begin():
        deleted = retention.expire_samples(store, now, apply=True)
    return _reply(jsonify(deleted=deleted))
