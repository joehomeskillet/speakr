"""Scoped statistics reports and site-admin grant management.

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
"""

from __future__ import annotations

from flask import Blueprint, Response, abort, jsonify, request, session
from flask_login import current_user
from flask_wtf.csrf import CSRFError, validate_csrf
from wtforms.validators import ValidationError

from src.clinic.admin_dictation_access import _is_active_site_admin
from src.clinic.config import clinic_mode_enabled
from src.clinic.identity import ClinicAccountPolicy
from src.clinic.parity_dispatch import _problem
from src.clinic.pools import ClinicAuthorPool
from src.clinic.statistics import StatisticsError, StatisticsQuery
from src.clinic.statistics_access import (
    StatisticsAccessError, grant_aggregate, read_aggregates, revoke_aggregate, version_of,
)
from src.clinic.statistics_export import StatisticsExportError, parse_query, render_csv
from src.clinic.statistics_grants import grants_for
from src.database import db
from src.models import User

PATH = "/api/clinic/v1/statistics/aggregates"
EXPORT_PATH = "/api/clinic/v1/statistics/export.csv"
GRANTS_PATH = "/api/clinic/v1/statistics/users/<int:user_id>/grants"
ROLE = "statistics"
_ARGS = frozenset({"time_basis", "from", "to", "grain", "group_by", "expected_version"})
_REFUSED = frozenset({"not_statistics", "no_grant", "capability_denied", "clinic_mode_off"})
_GRANT_KEYS = frozenset({"expected_version", "scope", "author_ids", "pool_ids"})
_MAX_USER_ID = 2**31 - 1

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
    return (current_user.is_authenticated and current_user.is_active
            and session.get("_user_id") == current_user.get_id())


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
