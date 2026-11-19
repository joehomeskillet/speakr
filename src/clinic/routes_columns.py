"""Clinic worklist layout routes and policy-scoped XML column projection.

PAR-CFG-005: Expose configurable metadata columns with safe bounded XML extraction.
Provides:
  - project_metadata_columns: Safe projection of XML-defined metadata fields.
  - create_columns_blueprint: Blueprint for own worklist layout (/api/clinic/v1/worklist/layout).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from hashlib import sha256
from uuid import UUID, uuid4

from flask import Blueprint, jsonify, request, session
from flask_login import current_user
from flask_wtf.csrf import CSRFError, validate_csrf
from sqlalchemy.exc import OperationalError
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge
from wtforms.validators import ValidationError as CSRFValidationError

from src.clinic import policy
from src.clinic.config import clinic_mode_enabled
from src.clinic.list_layout import LayoutError, validate_layout
from src.clinic.parity_dispatch import CommandProblem, RunOnce
from src.clinic.presets import ClinicMetadataField, ValidationError, extract_xml_metadata
from src.clinic.worklist_layout import MAX_STORED_BYTES, LayoutStore, WorklistLayoutError

PREFIX = "/api/clinic/v1/worklist/layout"
_FIELD_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}")
DISPLAY_TYPES = frozenset({"string", "integer", "decimal", "boolean", "date", "datetime"})
MAX_XML_BYTES = 64 * 1024

_TITLES = {
    400: "Ungültige Anfrage",
    401: "Anmeldung erforderlich",
    403: "Zugriff verweigert",
    404: "Nicht gefunden",
    409: "Anfrageschlüssel bereits verwendet",
    412: "Versionskonflikt",
    428: "Version erforderlich",
    503: "Dienst nicht verfügbar",
}


def project_metadata_columns(
    subject: policy.Subject,
    recording: policy.RecordingFacts,
    definitions: Sequence[ClinicMetadataField],
    names: Sequence[str],
    *,
    can_read_field: Callable[[policy.Subject, policy.RecordingFacts, ClinicMetadataField], bool],
    load_xml: Callable[[], str | bytes | None],
) -> list[dict]:
    """Project server-defined fields only; field policy must cover the XML source/context.

    Rejects XXE, invalid field identifiers, and fields revealing inaccessible clinical context.
    """
    if not clinic_mode_enabled() or not policy.decide(subject, "view", recording).allowed:
        raise WorklistLayoutError("unauthorized")
    if len(names) > 30 or any(
        not isinstance(name, str)
        or not _FIELD_NAME.fullmatch(name)
        or name in {"constructor", "prototype", "__proto__"}
        for name in names
    ) or len(set(names)) != len(names):
        raise LayoutError("bad_key")

    available = {field.name: field for field in definitions if field.is_active and field.xml_path}
    fields = []
    for name in names:
        field = available.get(name)
        if field is None or can_read_field(subject, recording, field) is not True:
            raise WorklistLayoutError("unauthorized")
        if field.field_type not in DISPLAY_TYPES:
            raise LayoutError("bad_display_type")
        fields.append(field)
    if not fields:
        return []

    values: dict[str, str | None] = {}
    xml = load_xml()
    if xml is not None:
        try:
            if isinstance(xml, str):
                if len(xml) > MAX_XML_BYTES:
                    raise LayoutError("invalid_xml")
                data = xml.encode("utf-8")
            elif isinstance(xml, (bytes, bytearray)):
                data = bytes(xml)
            else:
                raise LayoutError("invalid_xml")

            if len(data) > MAX_XML_BYTES or b"\0" in data:
                raise LayoutError("invalid_xml")

            data_upper = data.upper()
            if b"<!DOCTYPE" in data_upper or b"<!ENTITY" in data_upper or b"%ENTITY" in data_upper:
                raise LayoutError("invalid_xml")

            values = extract_xml_metadata(data, fields)
        except (ValidationError, LookupError, SyntaxError, ValueError) as exc:
            raise LayoutError("invalid_xml") from exc

    return [
        dict(
            key=field.name,
            label=field.label,
            display_type=field.field_type,
            display_format=field.display_format,
            value=values.get(field.name),
        )
        for field in fields
    ]


def create_columns_blueprint(
    store: LayoutStore,
    layout_policy: Callable[[int], tuple[Mapping, Sequence[str]]],
    run_once: RunOnce,
) -> Blueprint:
    """Mount w272's own-layout contract using server defaults and currently allowed keys.

    run_once must serialize and commit the write, audit and receipt together,
    with the same replay/error semantics as the shared parity dispatcher.
    """
    bp = Blueprint("clinic_columns", __name__)

    def problem(status: int, code: str, title: str | None = None):
        title_text = title or _TITLES.get(status, "Fehler")
        response = jsonify(
            type="about:blank",
            status=status,
            code=code,
            title=title_text,
            correlation_id=str(uuid4()),
        )
        response.mimetype = "application/problem+json"
        return response, status

    @bp.after_request
    def private(response):
        response.headers["Cache-Control"] = "private, no-store"
        return response

    @bp.errorhandler(CSRFError)
    def csrf_error(_error):
        return problem(403, "FORBIDDEN", "Zugriff verweigert")

    @bp.route(PREFIX, methods=["GET", "PUT"])
    def own_layout():
        if not clinic_mode_enabled():
            return problem(404, "NOT_FOUND", "Nicht gefunden")
        if not (
            current_user.is_authenticated
            and current_user.is_active
            and session.get("_user_id") == current_user.get_id()
        ):
            return problem(401, "UNAUTHORIZED", "Anmeldung erforderlich")
        actor = current_user.id
        try:
            if request.method in {"GET", "HEAD"}:
                default, allowed = layout_policy(actor)
                found = store.load(actor, actor, default, allowed)
                return jsonify(version=found.version, layout=found.layout)

            csrf_token = request.headers.get("X-CSRFToken") or request.headers.get("X-CSRF-Token")
            try:
                validate_csrf(csrf_token)
            except (CSRFValidationError, CSRFError):
                return csrf_error(None)

            tag = request.headers.get("If-Match")
            if tag is None:
                return problem(428, "PRECONDITION_REQUIRED", "Version erforderlich")
            if not re.fullmatch(r'"(?:0|[1-9][0-9]{0,18})"', tag):
                return problem(400, "INVALID_PAYLOAD", "Ungültige Anfrage")

            idempotency_hdr = request.headers.get("Idempotency-Key")
            if not idempotency_hdr:
                return problem(400, "INVALID_PAYLOAD", "Ungültige Anfrage")
            try:
                key = str(UUID(idempotency_hdr))
            except (ValueError, TypeError, AttributeError):
                return problem(400, "INVALID_PAYLOAD", "Ungültige Anfrage")

            if not request.is_json:
                return problem(400, "INVALID_PAYLOAD", "Ungültige Anfrage")
            raw = request.get_data()
            if len(raw) > MAX_STORED_BYTES:
                return problem(400, "INVALID_PAYLOAD", "Ungültige Anfrage")

            payload = request.get_json(silent=True)
            if payload is None or not isinstance(payload, dict):
                return problem(400, "INVALID_PAYLOAD", "Ungültige Anfrage")

            _, allowed = layout_policy(actor)
            spec = validate_layout(payload, allowed)
            if len(spec["sort"]) > 30 or spec["columns"] != payload.get("columns"):
                raise LayoutError("bad_layout")

            expected_version = int(tag[1:-1])

            def save() -> dict:
                written = store.save(actor, actor, spec, allowed, expected_version)
                return {"version": written.version, "layout": written.layout}

            digest = sha256(tag.encode("ascii") + b"\0" + raw).hexdigest()
            return jsonify(run_once((actor, "saveOwnWorklistLayout", str(actor), key), digest, save))
        except WorklistLayoutError as exc:
            if exc.code == "stale_version":
                return problem(412, "REVISION_CONFLICT", "Versionskonflikt")
            if exc.code == "unauthorized":
                return problem(403, "FORBIDDEN", "Zugriff verweigert")
            return problem(400, "INVALID_PAYLOAD", "Ungültige Anfrage")
        except (LayoutError, ValueError, BadRequest, RequestEntityTooLarge, RecursionError):
            return problem(400, "INVALID_PAYLOAD", "Ungültige Anfrage")
        except CommandProblem as exc:
            code = str(exc)
            if code == "IDEMPOTENCY_CONFLICT":
                return problem(409, "IDEMPOTENCY_CONFLICT", "Anfrageschlüssel bereits verwendet")
            if code == "POLICY_DENIED":
                return problem(403, "POLICY_DENIED", "Aktion nicht zulässig")
            return problem(400, "INVALID_PAYLOAD", "Ungültige Anfrage")
        except (OSError, OperationalError):
            return problem(503, "DEPENDENCY_UNAVAILABLE", "Dienst nicht verfügbar")

    return bp
