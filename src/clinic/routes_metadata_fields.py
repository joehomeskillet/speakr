"""Metadata field definition routes (PAR-CFG-005).

GET lists the field definitions, PUT creates, updates or archives one. There is no
version column on ``ClinicMetadataField`` (no schema change on the existing table),
so the version is an opaque sha256 over the canonical JSON of the editable fields.
The blueprint is registered by x29; this module does not touch app.py or route_policy.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from hashlib import sha256
from typing import Any
from uuid import uuid4

from flask import Blueprint, jsonify, request, session
from flask_login import current_user
from flask_wtf.csrf import CSRFError, validate_csrf
from sqlalchemy.exc import IntegrityError, OperationalError
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge
from wtforms.validators import ValidationError as CsrfValidationError

from src.clinic.config import clinic_mode_enabled
from src.clinic.presets import ClinicMetadataField, ValidationError, extract_xml_metadata
from src.clinic.routes_columns import DISPLAY_TYPES

PREFIX = "/api/clinic/v1/metadata-fields"
MAX_BODY_BYTES = 16 * 1024
MAX_XML_PATH = 256
SOURCE_TYPES = frozenset({"form", "xml"})
# Plain element names only: no axis, wildcard, predicate, attribute or parent syntax.
_FIELD_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}")
_XML_PATH = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*(?:/[A-Za-z_][A-Za-z0-9_.-]*){0,7}")
_RESERVED = frozenset({"constructor", "prototype", "__proto__"})
_EDITABLE = frozenset({"label", "field_type", "source_type", "xml_path", "display_format",
                       "is_required", "position", "is_active"})
_BODY_KEYS = _EDITABLE | {"expected_version"}

# Endpoint names for the registering package (x29).
ENDPOINTS = ("clinic_metadata_fields.list_fields", "clinic_metadata_fields.save_field")

_TITLES = {
    400: "Ungültige Anfrage",
    401: "Anmeldung erforderlich",
    403: "Zugriff verweigert",
    404: "Nicht gefunden",
    409: "Versionskonflikt",
    503: "Dienst nicht verfügbar",
}

SessionFactory = Callable[[], Any]
IsSiteAdmin = Callable[[Any, Any], bool]
AuditWrite = Callable[[int, str, str], None]


class _Invalid(Exception):
    """A validation refusal; the message is the machine reason."""


def field_version(field: ClinicMetadataField) -> str:
    """Opaque content version over the editable fields."""
    content = {key: getattr(field, key) for key in sorted(_EDITABLE)}
    return sha256(json.dumps(content, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def serialize(field: ClinicMetadataField) -> dict[str, Any]:
    """Definition only; never a clinical value or the default value."""
    return {
        "name": field.name,
        "label": field.label,
        "field_type": field.field_type,
        "source_type": field.source_type,
        "xml_path": field.xml_path,
        "display_format": field.display_format,
        "is_required": bool(field.is_required),
        "position": field.position,
        "is_active": bool(field.is_active),
        "version": field_version(field),
    }


def validate_xml_path(path: Any) -> str | None:
    """Return a path ``extract_xml_metadata`` evaluates safely, or None for no path."""
    if path is None or path == "":
        return None
    if not isinstance(path, str) or len(path) > MAX_XML_PATH or not _XML_PATH.fullmatch(path):
        raise _Invalid("bad_xml_path")
    probe = ClinicMetadataField(name="probe", label="probe", xml_path=path, field_type="string")
    try:
        # Evaluate through the shared extractor on a document without DOCTYPE or entities.
        extract_xml_metadata("<r><a/></r>", [probe])
    except (ValidationError, SyntaxError, ValueError, KeyError):
        raise _Invalid("bad_xml_path")
    return path


def _clean(body: dict[str, Any], name: str, current: ClinicMetadataField | None) -> dict[str, Any]:
    """Merge the supplied editable values over the current ones and validate them."""
    if not isinstance(name, str) or not _FIELD_NAME.fullmatch(name) or name in _RESERVED:
        raise _Invalid("bad_key")
    values: dict[str, Any] = {
        "label": None, "field_type": "string", "source_type": None, "xml_path": None,
        "display_format": None, "is_required": False, "position": 0, "is_active": True,
    }
    if current is not None:
        values.update({key: getattr(current, key) for key in _EDITABLE})
    values.update({key: body[key] for key in _EDITABLE if key in body})

    label = values["label"]
    if not isinstance(label, str) or not label.strip() or len(label.strip()) > 128:
        raise _Invalid("bad_label")
    values["label"] = label.strip()
    if values["field_type"] not in DISPLAY_TYPES:
        raise _Invalid("bad_display_type")
    values["xml_path"] = validate_xml_path(values["xml_path"])
    if values["source_type"] is None:
        values["source_type"] = "xml" if values["xml_path"] else "form"
    if values["source_type"] not in SOURCE_TYPES:
        raise _Invalid("bad_source_type")
    fmt = values["display_format"]
    if fmt is not None and not (isinstance(fmt, str) and len(fmt) <= 64):
        raise _Invalid("bad_display_format")
    if type(values["is_required"]) is not bool or type(values["is_active"]) is not bool:
        raise _Invalid("bad_flag")
    position = values["position"]
    if type(position) is not int or not 0 <= position <= 10000:
        raise _Invalid("bad_position")
    return values


def _default_session() -> Any:
    from src.database import db
    return db.session


def _default_is_site_admin(db_session: Any, user: Any) -> bool:
    """Active site administrator: is_admin and an active clinic account policy."""
    from src.clinic.identity import ClinicAccountPolicy
    if not getattr(user, "is_admin", False):
        return False
    account = db_session.get(ClinicAccountPolicy, user.id)
    return account is not None and account.status == "active"


def _default_audit(actor_id: int, action: str, name: str) -> None:
    """Existing event ``admin.setting.changed`` (no new event in this package).

    The key is 'other', so neither the field name nor any value reaches the log.
    """
    from src.services import audit
    audit.record_in_tx("admin.setting.changed", details={"key": "other"},
                       actor_kind="session", actor_user_id=actor_id)


def create_metadata_fields_blueprint(
    session_factory: SessionFactory = _default_session,
    is_site_admin: IsSiteAdmin = _default_is_site_admin,
    audit_write: AuditWrite = _default_audit,
) -> Blueprint:
    """Build the blueprint.

    ``session_factory()`` returns the SQLAlchemy session, ``is_site_admin(session,
    user)`` the active-admin decision, ``audit_write(actor_id, action, name)``
    queues the audit row in the same transaction (the route commits).
    """
    bp = Blueprint("clinic_metadata_fields", __name__)

    def problem(status: int, code: str, reason: str | None = None):
        extra = {"reason": reason} if reason else {}
        response = jsonify(type="about:blank", status=status, code=code, title=_TITLES[status],
                           correlation_id=str(uuid4()), **extra)
        response.mimetype = "application/problem+json"
        return response, status

    @bp.after_request
    def private(response):
        response.headers["Cache-Control"] = "private, no-store"
        return response

    @bp.errorhandler(CSRFError)
    def csrf_error(_error):
        return problem(403, "FORBIDDEN")

    def authenticated() -> bool:
        return bool(current_user.is_authenticated and current_user.is_active
                    and session.get("_user_id") == current_user.get_id())

    @bp.get(PREFIX, endpoint="list_fields")
    def list_fields():
        if not clinic_mode_enabled():
            return problem(404, "NOT_FOUND")
        if not authenticated():
            return problem(401, "UNAUTHORIZED")
        db_session = session_factory()
        try:
            query = db_session.query(ClinicMetadataField)
            if not is_site_admin(db_session, current_user):
                query = query.filter(ClinicMetadataField.is_active.is_(True))
            rows = query.order_by(ClinicMetadataField.position, ClinicMetadataField.name).all()
            return jsonify(fields=[serialize(row) for row in rows])
        except OperationalError:
            return problem(503, "DEPENDENCY_UNAVAILABLE")

    @bp.put(PREFIX + "/<name>", endpoint="save_field")
    def save_field(name: str):
        if not clinic_mode_enabled():
            return problem(404, "NOT_FOUND")
        if not authenticated():
            return problem(401, "UNAUTHORIZED")
        try:
            validate_csrf(request.headers.get("X-CSRFToken") or request.headers.get("X-CSRF-Token"))
        except (CsrfValidationError, CSRFError):
            return problem(403, "FORBIDDEN")
        db_session = session_factory()
        try:
            if not is_site_admin(db_session, current_user):
                return problem(403, "POLICY_DENIED")
            if not request.is_json or (request.content_length or 0) > MAX_BODY_BYTES:
                return problem(400, "INVALID_PAYLOAD", "bad_body")
            body = request.get_json(silent=True)
            if not isinstance(body, dict) or body.keys() - _BODY_KEYS:
                return problem(400, "INVALID_PAYLOAD", "bad_body")
            expected = body.get("expected_version")
            if expected is not None and not isinstance(expected, str):
                return problem(400, "INVALID_PAYLOAD", "bad_version")

            current = db_session.query(ClinicMetadataField).filter_by(name=name).one_or_none()
            if current is None:
                if expected is not None:
                    return problem(409, "stale_version")
            elif expected is None:
                return problem(400, "INVALID_PAYLOAD", "expected_version_required")
            elif expected != field_version(current):
                return problem(409, "stale_version")

            values = _clean(body, name, current)
            if current is None:
                current = ClinicMetadataField(name=name, **values)
                db_session.add(current)
                action = "create"
            else:
                was_active = bool(current.is_active)
                for key, value in values.items():
                    setattr(current, key, value)
                action = "archive" if was_active and not values["is_active"] else "update"
            audit_write(current_user.id, action, name)
            db_session.commit()
            result = serialize(current)
            return jsonify(result), (201 if action == "create" else 200)
        except _Invalid as error:
            db_session.rollback()
            return problem(400, "INVALID_PAYLOAD", str(error))
        except IntegrityError:
            db_session.rollback()
            return problem(409, "stale_version")
        except (BadRequest, RequestEntityTooLarge):
            db_session.rollback()
            return problem(400, "INVALID_PAYLOAD", "bad_body")
        except OperationalError:
            db_session.rollback()
            return problem(503, "DEPENDENCY_UNAVAILABLE")

    return bp
