"""Scoped aggregates, grant management and synthetic statistics (PAR-OPS-005/006).

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
* Grant management needs an active site admin. Changes need a CSRF token
  and the last observed per-user version. Only explicit authors and author
  pools can be granted.

The collection, period and retention routes are for active site admins in
synthetic staging. Optional counts come from successful browser actions.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

from flask import Blueprint, Response, abort, current_app, jsonify, request, session
from flask_login import current_user
from flask_wtf.csrf import CSRFError, validate_csrf
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from wtforms.validators import ValidationError

from src.clinic import statistics_retention as retention
from src.clinic.admin_dictation_access import _is_active_site_admin
from src.clinic.config import clinic_mode_enabled
from src.clinic.identity import ClinicAccountPolicy
from src.clinic.parity_dispatch import _problem
from src.clinic.pools import ClinicAuthorPool
from src.clinic.statistics import StatisticsError, StatisticsQuery
from src.clinic.statistics_access import (
    StatisticsAccessError, grant_aggregate, read_aggregates, revoke_aggregate, version_of,
)
from src.clinic.statistics_export import StatisticsExportError, _parse_bound, parse_query, render_csv
from src.clinic.statistics_grants import grants_for
from src.database import db
from src.models import User

PREFIX = "/api/clinic/v1/statistics"
PATH = PREFIX + "/aggregates"
EXPORT_PATH = PREFIX + "/export.csv"
GRANTS_PATH = PREFIX + "/users/<int:user_id>/grants"
ROLE = "statistics"
_ARGS = frozenset({"time_basis", "from", "to", "grain", "group_by", "expected_version"})
_REFUSED = frozenset({"not_statistics", "no_grant", "capability_denied", "clinic_mode_off"})
_GRANT_KEYS = frozenset({"expected_version", "scope", "author_ids", "pool_ids"})
_MAX_USER_ID = 2**31 - 1
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


def _versioned(response, version, status=200):
    response.headers["X-Grant-Version"] = str(version)
    return _reply(response, status)


def _browser_session():
    if not clinic_mode_enabled():
        abort(404)
    return _signed_in()


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
        if expected < 0:
            raise StatisticsError("version")
    query = parse_query(
        time_basis=args.get("time_basis", "created"),
        start=args.get("from"),
        end=args.get("to"),
        grain=args.get("grain"),
        group_by=args.get("group_by", ""),
    )
    return query, expected


def _aggregate_response(build_response):
    if not _browser_session():
        return _fail("UNAUTHORIZED")
    account = db.session.get(ClinicAccountPolicy, current_user.id)
    if account is None or account.status != "active":
        return _fail("FORBIDDEN")
    try:
        query, expected = _query(request.args)
        # The scope wraps the session it is given; use the request's own session, not the shared proxy.
        db_session = db.session()
        if expected is None:
            expected = version_of(current_user.id, session=db_session)
        result = read_aggregates(db_session, current_user.id, query, role=ROLE, expected_version=expected)
    except (StatisticsError, StatisticsExportError):
        return _fail("INVALID_PAYLOAD")
    except StatisticsAccessError as error:
        if error.code == "stale_version":
            return _fail("REVISION_CONFLICT")
        return _fail("FORBIDDEN" if error.code in _REFUSED else "INVALID_PAYLOAD")
    response = build_response(result)
    # Avoid pairing an aggregate computed under an old scope with a newer version.
    if version_of(current_user.id, session=db_session) != expected:
        return _fail("REVISION_CONFLICT")
    return _versioned(response, expected)


@blueprint.get(PATH)
def aggregates():
    return _aggregate_response(lambda result: jsonify(result.as_dict()))


@blueprint.get(EXPORT_PATH)
def export_csv():
    def download(result):
        return Response(render_csv(result), content_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="statistics.csv"'})

    return _aggregate_response(download)


def _grant_admin_refusal():
    if not _browser_session():
        return _fail("UNAUTHORIZED")
    if not _is_active_site_admin(db.session, current_user):
        return _fail("FORBIDDEN")
    return None


def _grant_not_found():
    response = jsonify(type="about:blank", title="Nicht gefunden", status=404, code="NOT_FOUND")
    response.mimetype = "application/problem+json"
    return _reply(response, 404)


def _grant_item(grant):
    return {"id": grant.id, "scope": grant.scope, "version": grant.version,
            "author_ids": sorted(grant.author_ids), "pool_ids": sorted(grant.pool_ids)}


@blueprint.get(GRANTS_PATH)
def list_grants(user_id):
    refusal = _grant_admin_refusal()
    if refusal is not None:
        return refusal
    if not 0 < user_id <= _MAX_USER_ID or db.session.get(User, user_id) is None:
        return _grant_not_found()
    version = version_of(user_id)
    items = [_grant_item(grant) for grant in grants_for(user_id)]
    if version_of(user_id) != version:
        return _fail("REVISION_CONFLICT")
    return _versioned(jsonify(user_id=user_id, version=version, items=items), version)


def _valid_grant_targets(author_ids, pool_ids):
    if not isinstance(author_ids, list) or any(
        type(uid) is not int or not 0 < uid <= _MAX_USER_ID for uid in author_ids
    ):
        return False
    if not isinstance(pool_ids, list) or any(
        not isinstance(pid, str) or not 0 < len(pid) <= 36 or not pid.isprintable() for pid in pool_ids
    ):
        return False
    # Match stored IDs so a staff pool or an unknown author cannot become a grant target.
    if author_ids and db.session.query(User.id).filter(User.id.in_(author_ids)).count() != len(author_ids):
        return False
    pool_column = ClinicAuthorPool.id
    return not pool_ids or db.session.query(pool_column).filter(pool_column.in_(pool_ids)).count() == len(pool_ids)


@blueprint.errorhandler(CSRFError)
def _csrf_error(_error):
    return _fail("FORBIDDEN")


@blueprint.post(GRANTS_PATH)
@blueprint.delete(GRANTS_PATH + "/<uuid:grant_id>")
def change_grant(user_id, grant_id=None):
    refusal = _grant_admin_refusal()
    if refusal is not None:
        return refusal
    try:
        validate_csrf(request.headers.get("X-CSRF-Token") or request.headers.get("X-CSRFToken"))
    except ValidationError:
        return _fail("FORBIDDEN")
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or type(body.get("expected_version")) is not int or body["expected_version"] < 0:
        return _fail("INVALID_PAYLOAD")
    expected = body["expected_version"]
    if not 0 < user_id <= _MAX_USER_ID or db.session.get(User, user_id) is None:
        return _grant_not_found()
    try:
        if grant_id is not None:
            if body.keys() != {"expected_version"}:
                return _fail("INVALID_PAYLOAD")
            version = revoke_aggregate(user_id, str(grant_id), expected_version=expected)
            return _versioned(jsonify(user_id=user_id, version=version), version)
        author_ids, pool_ids = body.get("author_ids", []), body.get("pool_ids", [])
        if body.keys() - _GRANT_KEYS or not _valid_grant_targets(author_ids, pool_ids):
            return _fail("INVALID_PAYLOAD")
        grant = grant_aggregate(user_id, body.get("scope"), author_ids=author_ids,
                                pool_ids=pool_ids, expected_version=expected)
    except StatisticsAccessError as error:
        if error.code == "stale_version":
            return _fail("REVISION_CONFLICT")
        if error.code in {"unknown_user", "unknown_grant"}:
            return _grant_not_found()
        return _fail("INVALID_PAYLOAD")
    response = jsonify(user_id=user_id, version=grant.version, grant=_grant_item(grant))
    return _versioned(response, grant.version, 201)


@blueprint.before_request
def require_collection_admin():
    if request.endpoint not in {
        "clinic_statistics.collection_policy",
        "clinic_statistics.collection_period",
        "clinic_statistics.collection_retention",
    }:
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
