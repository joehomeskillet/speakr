"""HTTP surface for versioned document templates and assignment (PAR-CFG-002).

A thin layer over ``presets`` (``ClinicTemplate``, ``save_template``, ``list_templates``).
The actor is always ``current_user``; a request body never names an actor, user or role.
Central templates store a durable server copy (DOCX/ODT) that remains usable without
the original client/workstation file. Append-only versioning preserves template history.
Visibility covers system templates, templates of the caller's active author pools, and
the caller's own user templates.
"""

from __future__ import annotations

import base64
import hashlib
import io
import uuid
import zipfile
from typing import Any

from flask import Blueprint, Response, abort, jsonify, request, session
from flask_login import current_user
from flask_wtf.csrf import CSRFError, validate_csrf
from wtforms.validators import ValidationError

from src.clinic import pool_policy, presets
from src.clinic.config import clinic_mode_enabled
from src.database import db
from src.models import User

PREFIX = "/api/clinic/v1/templates"
MAX_CONTENT_BYTES = 2 * 1024 * 1024  # 2 MiB decoded content limit
ALLOWED_MIME_TYPES = frozenset({
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.oasis.opendocument.text",
})
_FORBIDDEN_KEYS = frozenset({
    "actor", "actor_id", "user", "user_id", "author_user_id", "role", "roles",
    "is_admin", "created_by",
})
_STATUS = {
    "invalid_payload": 400,
    "unauthenticated": 401,
    "unauthorized": 403,
    "forbidden": 403,
    "foreign_pool": 403,
    "foreign_user": 403,
    "unknown_template": 404,
    "not_found": 404,
    "idempotency_conflict": 409,
    "stale_version": 412,
    "payload_too_large": 413,
    "invalid_marker": 422,
    "precondition_required": 428,
    "dependency_unavailable": 503,
}
_TITLES = {
    400: "Ungültige Anfrage",
    401: "Anmeldung erforderlich",
    403: "Zugriff verweigert",
    404: "Nicht gefunden",
    409: "Versionskonflikt",
    412: "Versionskonflikt",
    413: "Anfrage zu gross",
    422: "Ungültige Einfügemarke",
    428: "Voraussetzung erforderlich",
    503: "Dienst nicht verfügbar",
}

# In-memory idempotency receipts: (actor_id, op, idempotency_key) -> (digest, response_json, status, etag)
_RECEIPTS: dict[tuple[int, str, str], tuple[str, dict[str, Any], int, str]] = {}


class TemplateError(presets.PresetError):
    def __init__(self, code: str, status: int | None = None, **extra: Any) -> None:
        super().__init__(code, code=code)
        self.code = code
        self.status = status or _STATUS.get(code, 400)
        self.extra = extra


def _problem(code: str, status: int | None = None, **extra: Any):
    status = status or _STATUS.get(code, 400)
    title = _TITLES.get(status, "Fehler")
    response = jsonify(type="about:blank", title=title, status=status, code=code, **extra)
    response.mimetype = "application/problem+json"
    response.status_code = status
    return response, status


def validate_marker(marker: str | None) -> None:
    """Validate template insertion marker syntax."""
    if marker is None:
        return
    if not isinstance(marker, str):
        raise TemplateError("invalid_marker", 422)
    stripped = marker.strip()
    if not stripped or len(marker) > 128:
        raise TemplateError("invalid_marker", 422)
    if marker != stripped:
        raise TemplateError("invalid_marker", 422)
    if any(ord(c) < 32 for c in marker):
        raise TemplateError("invalid_marker", 422)
    if "<script" in marker.lower() or "</script" in marker.lower():
        raise TemplateError("invalid_marker", 422)


def visible_templates(user_id: int, include_system: bool = True) -> list[presets.ClinicTemplate]:
    """Active templates the user may read: system, own author pools, own user templates."""
    pool_ids = list(pool_policy.author_pool_ids(user_id))
    return presets.list_templates(
        user_id=str(user_id),
        pool_ids=pool_ids,
        include_system=include_system,
        session=db.session,
    )


def template_summary(row: presets.ClinicTemplate) -> dict[str, Any]:
    """Strict TemplateSummary projection matching worklist-web.openapi.json."""
    return {
        "id": row.id,
        "name": row.name,
        "scope": row.scope,
        "version": row.version,
        "mime_type": row.mime_type,
        "content_sha256": row.content_hash,
    }


def template_detail(row: presets.ClinicTemplate) -> dict[str, Any]:
    """Full metadata projection for detail views and inspections."""
    return {
        "id": row.id,
        "name": row.name,
        "scope": row.scope,
        "scope_id": row.scope_id,
        "version": row.version,
        "mime_type": row.mime_type,
        "content_sha256": row.content_hash,
        "notes": row.notes,
        "insertion_mark": row.insertion_mark,
        "is_active": row.is_active,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _check_scope_permission(actor_id: int, scope: str, scope_id: str | None) -> str:
    """Validate write authority for the requested scope. Never trusts unverified IDs."""
    if scope == "user":
        if scope_id is not None and str(scope_id) != str(actor_id):
            raise TemplateError("foreign_user", 403)
        return str(actor_id)
    if scope == "pool":
        if not scope_id:
            raise TemplateError("invalid_payload", 400)
        allowed_pools = {str(p) for p in pool_policy.author_pool_ids(actor_id)}
        if str(scope_id) not in allowed_pools:
            raise TemplateError("foreign_pool", 403)
        return str(scope_id)
    if scope == "system":
        user = db.session.get(User, actor_id)
        if not (user and (getattr(user, "is_admin", False) or getattr(user, "is_identity_admin", False))):
            raise TemplateError("unauthorized", 403)
        if scope_id is not None and scope_id != presets.SYSTEM_SCOPE_ID:
            raise TemplateError("invalid_payload", 400)
        return presets.SYSTEM_SCOPE_ID
    raise TemplateError("invalid_payload", 400)


def _can_read_template(actor_id: int, row: presets.ClinicTemplate) -> bool:
    """Check if the user is authorized to read the template content."""
    if not row.is_active:
        return False
    user = db.session.get(User, actor_id)
    if user is None or not user.is_active:
        return False
    if user.is_admin:
        return True
    if row.scope == "system":
        return True
    if row.scope == "user":
        return row.scope_id == str(actor_id)
    if row.scope == "pool":
        allowed_pools = {str(p) for p in pool_policy.author_pool_ids(actor_id)}
        return str(row.scope_id) in allowed_pools
    return False


def assign_template(
    template_id: str,
    target_scope: str,
    target_scope_id: str | int | None = None,
    *,
    actor_id: int,
    session=None,
) -> presets.ClinicTemplate:
    """Assign an existing server template to another authorized scope (pool or user)."""
    s = session or db.session
    source = s.get(presets.ClinicTemplate, str(template_id))
    if source is None or not source.is_active:
        raise TemplateError("unknown_template", 404)
    if not _can_read_template(actor_id, source):
        raise TemplateError("unknown_template", 404)

    target_sid = _check_scope_permission(actor_id, target_scope, str(target_scope_id) if target_scope_id else None)
    validate_marker(source.insertion_mark)

    return presets.save_template(
        name=source.name,
        content_bytes=source.content_bytes,
        scope=target_scope,
        scope_id=target_sid,
        notes=source.notes,
        insertion_mark=source.insertion_mark,
        mime_type=source.mime_type,
        original_source=source.original_source,
        session=s,
    )


def apply_template_text(
    content_bytes: bytes,
    insertion_mark: str | None,
    text_to_insert: str,
    mime_type: str | None = None,
) -> bytes:
    """Insert dictation text at the defined insertion marker or documented replacement point."""
    if not content_bytes:
        return text_to_insert.encode("utf-8")
    if not insertion_mark:
        # Fallback position: append text
        return content_bytes + b"\n" + text_to_insert.encode("utf-8")

    mark_bytes = insertion_mark.encode("utf-8")
    insert_bytes = text_to_insert.encode("utf-8")

    # Attempt ZIP / OpenXML / ODT modification if archive format
    if content_bytes.startswith(b"PK\x03\x04"):
        try:
            in_buf = io.BytesIO(content_bytes)
            out_buf = io.BytesIO()
            with zipfile.ZipFile(in_buf, "r") as zin, zipfile.ZipFile(out_buf, "w") as zout:
                for item in zin.infolist():
                    data = zin.read(item.filename)
                    if item.filename in ("word/document.xml", "content.xml"):
                        if mark_bytes in data:
                            data = data.replace(mark_bytes, insert_bytes, 1)
                        else:
                            # Documented replacement position: append inside main body tag
                            close_tag = b"</w:body>" if b"</w:body>" in data else b"</office:text>"
                            if close_tag in data:
                                replacement = mark_bytes + b" " + insert_bytes + close_tag
                                data = data.replace(close_tag, replacement, 1)
                    zout.writestr(item, data)
            return out_buf.getvalue()
        except (zipfile.BadZipFile, UnicodeError):
            pass

    # Direct substitution for plain or synthetic payload
    if mark_bytes in content_bytes:
        return content_bytes.replace(mark_bytes, insert_bytes, 1)
    return content_bytes + b"\n" + insert_bytes


blueprint = Blueprint("clinic_templates", __name__)


@blueprint.before_request
def _guard():
    if not clinic_mode_enabled():
        abort(404)
    if not (current_user.is_authenticated and current_user.is_active
            and session.get("_user_id") == current_user.get_id()):
        return _problem("unauthenticated", 401)
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        token = request.headers.get("X-CSRFToken") or request.headers.get("X-CSRF-Token")
        try:
            validate_csrf(token)
        except (ValidationError, Exception):
            return _problem("unauthorized", 403)


blueprint.register_error_handler(CSRFError, lambda _e: _problem("unauthorized", 403))


@blueprint.after_request
def _no_cache(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store"
    return response


@blueprint.get(PREFIX)
def list_templates():
    rows = visible_templates(current_user.id)
    return jsonify(items=[template_summary(row) for row in rows])


@blueprint.get(PREFIX + "/<uuid:template_id>")
def get_template(template_id: uuid.UUID):
    tid = str(template_id)
    row = db.session.get(presets.ClinicTemplate, tid)
    if row is None or not _can_read_template(current_user.id, row):
        return _problem("not_found", 404)
    return jsonify(template_detail(row))


@blueprint.get(PREFIX + "/<uuid:template_id>/content")
def get_template_content(template_id: uuid.UUID):
    tid = str(template_id)
    row = db.session.get(presets.ClinicTemplate, tid)
    if row is None or not _can_read_template(current_user.id, row):
        return _problem("not_found", 404)

    return Response(
        row.content_bytes,
        mimetype=row.mime_type or "application/octet-stream",
        headers={
            "Cache-Control": "no-store",
            "Content-Length": str(len(row.content_bytes)),
        },
    )


@blueprint.post(PREFIX)
def save_template():
    # 1. Preconditions
    if_match = request.headers.get("If-Match")
    if if_match is None:
        return _problem("precondition_required", 428)
    try:
        expected_version = int(if_match.strip().strip('"'))
    except ValueError:
        return _problem("stale_version", 412)

    idempotency_key = request.headers.get("Idempotency-Key")
    if not idempotency_key:
        return _problem("precondition_required", 428)
    try:
        uuid_val = str(uuid.UUID(idempotency_key))
    except (ValueError, TypeError):
        return _problem("invalid_payload", 400)

    # 2. Payload validation
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or _FORBIDDEN_KEYS & body.keys():
        return _problem("invalid_payload", 400)

    for req in ("scope", "name", "mime_type", "content_base64", "content_sha256"):
        if req not in body:
            return _problem("invalid_payload", 400)

    name = body["name"]
    if not isinstance(name, str) or not name.strip() or len(name) > 128:
        return _problem("invalid_payload", 400)
    name = name.strip()

    mime_type = body["mime_type"]
    if mime_type not in ALLOWED_MIME_TYPES:
        return _problem("invalid_payload", 400)

    insertion_mark = body.get("insertion_mark")
    try:
        validate_marker(insertion_mark)
    except TemplateError as err:
        return _problem(err.code, err.status)

    notes = body.get("notes")
    if notes is not None and (not isinstance(notes, str) or len(notes) > 4000):
        return _problem("invalid_payload", 400)

    # 3. Scope validation & authority
    scope_obj = body["scope"]
    if not isinstance(scope_obj, dict) or "scope" not in scope_obj:
        return _problem("invalid_payload", 400)
    scope_type = scope_obj["scope"]
    try:
        sid = _check_scope_permission(current_user.id, scope_type, scope_obj.get("scope_id"))
    except TemplateError as err:
        return _problem(err.code, err.status)

    # 4. Binary decode and SHA-256 check
    content_b64 = body["content_base64"]
    content_sha = body["content_sha256"]
    if not isinstance(content_b64, str) or not isinstance(content_sha, str):
        return _problem("invalid_payload", 400)

    try:
        raw_bytes = base64.b64decode(content_b64, validate=True)
    except Exception:
        return _problem("invalid_payload", 400)

    if len(raw_bytes) > MAX_CONTENT_BYTES:
        return _problem("payload_too_large", 413)

    computed_sha = hashlib.sha256(raw_bytes).hexdigest()
    if computed_sha.lower() != content_sha.lower():
        return _problem("invalid_payload", 400)

    # 5. Idempotency receipt check (replays before mutating state)
    req_digest = hashlib.sha256(request.get_data()).hexdigest()
    idemp_slot = (current_user.id, "save_template", uuid_val)
    if idemp_slot in _RECEIPTS:
        old_digest, old_resp, old_status, old_etag = _RECEIPTS[idemp_slot]
        if old_digest != req_digest:
            return _problem("idempotency_conflict", 409)
        resp = jsonify(old_resp)
        resp.status_code = old_status
        resp.headers["ETag"] = old_etag
        return resp

    # 6. Stale version check against DB
    latest = (
        db.session.query(presets.ClinicTemplate)
        .filter_by(scope=scope_type, scope_id=sid, name=name, is_active=True)
        .order_by(presets.ClinicTemplate.version.desc())
        .first()
    )
    current_version = latest.version if latest else 0
    if expected_version != current_version:
        return _problem("stale_version", 412, current_version=current_version)

    # 7. Persist new append-only version
    try:
        tmpl = presets.save_template(
            name=name,
            content_bytes=raw_bytes,
            scope=scope_type,
            scope_id=sid,
            notes=notes,
            insertion_mark=insertion_mark,
            mime_type=mime_type,
            session=db.session,
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        return _problem("dependency_unavailable", 503)

    summary = template_summary(tmpl)
    etag = f'"{tmpl.version}"'
    _RECEIPTS[idemp_slot] = (req_digest, summary, 201, etag)

    resp = jsonify(summary)
    resp.status_code = 201
    resp.headers["ETag"] = etag
    return resp


@blueprint.post(PREFIX + "/<uuid:template_id>/assign")
def assign_template_route(template_id: uuid.UUID):
    tid = str(template_id)
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or _FORBIDDEN_KEYS & body.keys():
        return _problem("invalid_payload", 400)
    scope_obj = body.get("scope")
    if not isinstance(scope_obj, dict) or "scope" not in scope_obj:
        return _problem("invalid_payload", 400)

    try:
        assigned = assign_template(
            tid,
            target_scope=scope_obj["scope"],
            target_scope_id=scope_obj.get("scope_id"),
            actor_id=current_user.id,
            session=db.session,
        )
        db.session.commit()
    except TemplateError as err:
        db.session.rollback()
        return _problem(err.code, err.status)
    except Exception:
        db.session.rollback()
        return _problem("dependency_unavailable", 503)

    return jsonify(template_summary(assigned)), 201


@blueprint.delete(PREFIX + "/<uuid:template_id>")
def delete_template(template_id: uuid.UUID):
    tid = str(template_id)
    row = db.session.get(presets.ClinicTemplate, tid)
    if row is None or not row.is_active:
        return _problem("not_found", 404)

    # Permission check for deactivation
    if not _can_read_template(current_user.id, row):
        return _problem("not_found", 404)
    if row.scope == "user" and row.scope_id != str(current_user.id):
        return _problem("unauthorized", 403)
    if row.scope == "system":
        user = db.session.get(User, current_user.id)
        if not (user and getattr(user, "is_admin", False)):
            return _problem("unauthorized", 403)

    row.is_active = False
    db.session.commit()
    return jsonify(ok=True)
