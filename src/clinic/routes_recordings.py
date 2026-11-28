"""HTTP adapter for the V8 recordings list (P01): GET /api/clinic/v1/recordings."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from uuid import uuid4

from flask import Blueprint, abort, jsonify, request, session
from flask_login import current_user
from werkzeug.exceptions import HTTPException

from src.clinic import policy, recordings_list, workflow
from src.clinic.config import clinic_mode_enabled
from src.clinic.routes_worklist import WorklistEntry

PREFIX = "/api/clinic/v1/recordings"
_PROBLEMS = {400: ("INVALID_PAYLOAD", "Ungültige Anfrage"),
             401: ("UNAUTHORIZED", "Anmeldung erforderlich"),
             404: ("NOT_FOUND", "Nicht gefunden")}


def create_blueprint(load_entries: Callable[[int], Iterable[WorklistEntry]]) -> Blueprint:
    """Mount with the worklist loader; the worklist row policy still decides every row."""
    blueprint = Blueprint("clinic_recordings", __name__, url_prefix=PREFIX)

    def problem(error: HTTPException):
        status = error.code or 400
        code, title = _PROBLEMS[status]
        response = jsonify(type="about:blank", status=status, code=code, title=title,
                           correlation_id=str(uuid4()))
        response.mimetype = "application/problem+json"
        return response, status

    for status in _PROBLEMS:
        blueprint.register_error_handler(status, problem)

    @blueprint.before_request
    def require_browser_session():
        if not clinic_mode_enabled():
            abort(404)
        if not (current_user.is_authenticated and current_user.is_active
                and session.get("_user_id") == current_user.get_id()):
            abort(401)

    @blueprint.after_request
    def no_store(response):
        response.headers["Cache-Control"] = "private, no-store"
        return response

    @blueprint.get("")
    def list_recordings():
        args = request.args
        actor = current_user.id
        try:
            query = dict(
                limit=recordings_list.parse_limit(args.get("limit")),
                cursor=args.get("cursor") or None,
                date_from=recordings_list.parse_day(args["from"], "from") if args.get("from") else None,
                date_to=recordings_list.parse_day(args["to"], "to") if args.get("to") else None,
                author_id=args.get("author_id") or None,
                pool_id=args.get("pool_id") or None,
                status=args.get("status") or None,
                pending_first=args.get("pending_first") == "1",
            )
            entries = load_entries(actor)
            # P08 opts in with its existing status filter. The default is Aufnahmen.
            if query["status"] is None:
                entries = (entry for entry in entries if entry.row["status"] != workflow.TO_ARCHIVE)
            body = recordings_list.list_recordings(entries, policy.build_subject(actor), **query)
        except recordings_list.RecordingsQueryError:
            abort(400)
        return jsonify(body)

    return blueprint
