"""Read-only statistics route (V8 P18, PAR-OPS-005/006).

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
* Nothing is written. A refused request returns before any query runs.

Mount ``blueprint`` once; registration in app.py and the route-policy entry
(AUTH, endpoint ``clinic_statistics.aggregates``) belong to a consumer package.
"""

from __future__ import annotations

from flask import Blueprint, abort, jsonify, request, session
from flask_login import current_user

from src.clinic.config import clinic_mode_enabled
from src.clinic.identity import ClinicAccountPolicy
from src.clinic.parity_dispatch import _problem
from src.clinic.statistics import StatisticsError, StatisticsQuery
from src.clinic.statistics_access import StatisticsAccessError, read_aggregates, version_of
from src.clinic.statistics_export import StatisticsExportError, _parse_bound
from src.database import db

PATH = "/api/clinic/v1/statistics/aggregates"
ROLE = "statistics"
_ARGS = frozenset({"time_basis", "from", "to", "grain", "group_by", "expected_version"})
_REFUSED = frozenset({"not_statistics", "no_grant", "capability_denied", "clinic_mode_off"})

blueprint = Blueprint("clinic_statistics", __name__)


def _reply(response, status=200):
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    return response


def _fail(code):
    body, status = _problem(code)
    return _reply(body, status)


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
    if not (current_user.is_authenticated and current_user.is_active
            and session.get("_user_id") == current_user.get_id()):
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
