"""HTTP surface for recipient, selection and workstation preferences.

PAR-ADM-006, PAR-ADM-007, PAR-ADM-011, PAR-CFG-001. A thin layer over
``recipient_defaults``, ``last_selection`` and ``workstation_preferences``.
The actor is always ``current_user``. A body never names an actor, user or
role. Every write checks CSRF and the session. Allowances (``allowed_for``)
and the author pool (``author_pool_for``) come from the caller's central
policy, are read on every request, and so apply at save and at use time.

The app mounts these routes in clinic mode. Central writes are system-wide
and require an active site administrator. Effective workflow presets use
the session user and their server-selected author pool.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from flask import Blueprint, abort, current_app, jsonify, request, session
from flask_login import current_user
from flask_wtf.csrf import CSRFError, validate_csrf
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from wtforms.validators import ValidationError

from src.clinic import correction_modes, pools, presets, recipient_defaults as rd
from src.clinic.admin_dictation_access import _is_active_site_admin
from src.clinic.config import clinic_mode_enabled
from src.clinic.last_selection import (
    Allowed,
    LastSelectionError,
    Selection,
    SelectionStore,
    restore,
)
from src.clinic.workstation_preferences import (
    ALLOWLIST,
    PreferenceError,
    StaleVersion,
    WorkstationPreferences,
)
from src.database import db

PREFIX = "/api/clinic/v1/preferences"
# Identity and authority come from the session, never from the request.
_FORBIDDEN_KEYS = frozenset({
    "actor", "actor_id", "user", "user_id", "author_user_id", "role", "roles",
    "is_admin", "created_by",
})
_STATUS = {
    "unauthorized": 403, "untrusted_workstation": 403, "recipient_not_eligible": 403,
    "choice_disabled": 403, "person_not_allowed": 403, "capability_forbidden": 403,
    "stale_version": 409, "conflict": 409,
    "selection_not_allowed": 422, "not_author_pool": 422, "invalid_mode": 422, "bad_version": 422,
    "preference_read_failed": 503, "preference_write_failed": 503,
}
_TITLES = {400: "Ungültige Anfrage", 401: "Anmeldung erforderlich", 403: "Zugriff verweigert",
           409: "Versionskonflikt", 422: "Auswahl nicht zulässig", 503: "Dienst nicht verfügbar"}


def _problem(code: str, status: int | None = None, **extra: Any):
    status = status or _STATUS.get(code, 400)
    response = jsonify(type="about:blank", title=_TITLES[status], status=status, code=code, **extra)
    response.mimetype = "application/problem+json"
    return response, status


def _body() -> dict[str, Any]:
    body = request.get_json(silent=True)
    if not isinstance(body, dict) or _FORBIDDEN_KEYS & body.keys():
        raise rd.RecipientDefaultError("invalid_payload")
    return body


def _selection_json(selection: Selection) -> dict[str, Any]:
    return {key: value for key, value in vars(selection).items()}


def _require_central_admin() -> None:
    if not _is_active_site_admin(db.session, current_user):
        raise PreferenceError("unauthorized")
    if request.args or (request.method == "DELETE" and request.get_data()):
        raise PreferenceError("invalid_payload")


def _valid_preset_value(key: str, value: Any) -> bool:
    if key == "priority":
        return type(value) is int and value in presets.ALLOWED_PRIORITIES
    if key == "security":
        return isinstance(value, str) and value in presets.ALLOWED_SECURITY_LEVELS
    if key in ("use_speech_recognition", "with_approval"):
        return type(value) is bool
    if key == "typist" and (value is None or type(value) is int):
        return value is None or value > 0
    return isinstance(value, str) and len(value) <= 128 and bool(value.strip())


class PreferenceRoutes:
    """Build the blueprint around injected stores and central allowances."""

    def __init__(
        self,
        *,
        allowed_for: Callable[[int], Allowed],
        author_pool_for: Callable[[int], str | None],
        selections: SelectionStore,
        workstations: WorkstationPreferences,
    ) -> None:
        self._allowed_for = allowed_for
        self._author_pool_for = author_pool_for
        self._selections = selections
        self._workstations = workstations
        bp = self.blueprint = Blueprint("clinic_preferences", __name__)
        bp.before_request(self._guard)
        bp.register_error_handler(CSRFError, lambda _e: _problem("unauthorized"))
        for rule, name, view, methods in (
            ("/recipient-default", "recipient_default", self._recipient_default, ("GET", "PUT")),
            ("/recipient-default/resolve", "recipient_resolve", self._recipient_resolve, ("POST",)),
            ("/last-selection", "last_selection", self._last_selection, ("GET", "PUT")),
            ("/workstations", "workstation_issue", self._workstation_issue, ("POST",)),
            ("/workstations/<workstation_id>", "workstation", self._workstation, ("GET", "PUT")),
            ("/workstation-locks/<key>", "workstation_lock", self._workstation_lock, ("PUT", "DELETE")),
            ("/presets", "presets", self._resolved_presets, ("GET",)),
            ("/presets/<key>", "system_preset", self._system_preset, ("PUT", "DELETE")),
            ("/correction-mode", "correction_mode", self._correction_mode, ("GET", "PUT")),
        ):
            bp.add_url_rule(PREFIX + rule, name, self._wrap(view), methods=list(methods))

    # -- plumbing -----------------------------------------------------------
    def _guard(self):
        if not clinic_mode_enabled():
            abort(404)
        if not (current_user.is_authenticated and current_user.is_active
                and session.get("_user_id") == current_user.get_id()):
            return _problem("unauthenticated", 401)
        if request.method != "GET":
            try:
                validate_csrf(request.headers.get("X-CSRF-Token"))
            except ValidationError:
                return _problem("unauthorized")

    def _wrap(self, view: Callable[..., Any]) -> Callable[..., Any]:
        def run(**kwargs: Any):
            try:
                return view(**kwargs)
            except rd.VersionConflict as error:
                return _problem("stale_version", current_version=error.current_version)
            except StaleVersion as error:
                return _problem("stale_version", current_version=error.version)
            except (rd.RecipientDefaultError, PreferenceError, LastSelectionError) as error:
                return _problem(error.code)
            except correction_modes.ModeError as error:
                return _problem(error.code)
            except SQLAlchemyError as error:
                db.session.rollback()
                current_app.logger.error("Clinic preference %s failed (%s)", request.endpoint, type(error).__name__)
                return _problem("preference_read_failed" if request.method == "GET" else "preference_write_failed")

        run.__name__ = view.__name__
        return run

    # -- recipient default --------------------------------------------------
    def _checked_default_targets(self, actor: int, mode: str, ids: list[str]) -> None:
        """Save-time check that the target is current and inside the author's pool."""
        if mode not in ("person", "staff_pool"):
            return
        pool_id = self._author_pool_for(actor)
        if pool_id is None:
            raise rd.RecipientDefaultError("not_author_pool")
        if mode == "person":
            if not ids or ids[0] not in {str(uid) for uid in rd.eligible_user_ids(pool_id)}:
                raise rd.RecipientDefaultError("recipient_not_eligible")
        else:
            linked = {row[0] for row in db.session.query(pools.ClinicPoolRoute.staff_pool_id)
                      .filter_by(author_pool_id=pool_id)}
            if not ids or ids[0] not in linked:
                raise rd.RecipientDefaultError("recipient_not_eligible")

    def _recipient_default(self):
        actor = current_user.id
        if request.method == "GET":
            row = rd.get_default(actor)
            pool_id = self._author_pool_for(actor)
            overrides = rd.selectable_overrides(actor, pool_id) if pool_id else None
            return jsonify(
                version=0 if row is None else row.version,
                mode=None if row is None else row.mode,
                target_ids=[] if row is None else row.target_ids,
                choice=rd.CHOICE_OPEN if row is None else row.choice,
                overrides=overrides,
            )
        body = _body()
        mode, ids = body.get("mode"), body.get("target_ids") or []
        if isinstance(ids, list):
            self._checked_default_targets(actor, mode, ids)
        row = rd.set_default(
            actor, mode, actor_id=actor, expected_version=body.get("expected_version"),
            target_ids=ids, choice=body.get("choice", rd.CHOICE_OPEN),
        )
        return jsonify(version=row.version, mode=row.mode, target_ids=row.target_ids, choice=row.choice)

    def _recipient_resolve(self):
        """Use time: revalidate the stored default or an override against the pool now."""
        actor = current_user.id
        body = _body()
        sent = rd.submit(
            actor, self._author_pool_for(actor), actor_id=actor,
            override=body.get("override"), expected_version=body.get("expected_version"),
        )
        return jsonify(mode=sent.mode, target_ids=list(sent.target_ids),
                       recipient_user_ids=list(sent.recipient_user_ids),
                       default_version=sent.default_version)

    # -- last selection -----------------------------------------------------
    def _last_selection(self):
        actor = current_user.id
        allowed = self._allowed_for(actor)
        if request.method == "GET":
            result = self._selections.start_context(actor, allowed)
            return jsonify(selection=_selection_json(result.selection), fallback=list(result.fallback))
        raw = _body().get("selection")
        if not isinstance(raw, dict) or raw.keys() - {"role", "author_id", "pool_id", "source_id", "template_id"}:
            raise rd.RecipientDefaultError("invalid_payload")
        selection = Selection(**{k: raw.get(k) for k in ("role", "author_id", "pool_id", "source_id", "template_id")})
        if restore(selection, allowed).fallback:
            raise LastSelectionError("selection_not_allowed")
        self._selections.remember(actor, selection)
        return jsonify(selection=_selection_json(selection))

    # -- workstation --------------------------------------------------------
    def _workstation_issue(self):
        return jsonify(workstation_id=self._workstations.issue(current_user)), 201

    def _workstation(self, workstation_id: str):
        if request.method == "GET":
            effective = self._workstations.effective(current_user, workstation_id)
            return jsonify(values=effective.values, origins=effective.origins, version=effective.version)
        body = _body()
        version = self._workstations.apply(
            current_user, workstation_id, body.get("changes"), expected_version=body.get("expected_version"),
        )
        return jsonify(version=version)

    def _workstation_lock(self, key: str):
        _require_central_admin()
        if key not in ALLOWLIST:
            raise PreferenceError("not_allowlisted")
        if request.method == "DELETE":
            self._workstations.unlock(key)
        else:
            body = _body()
            if body.keys() != {"value"}:
                raise PreferenceError("invalid_payload")
            self._workstations.lock(key, body["value"])
        return "", 204

    def _resolved_presets(self):
        actor = current_user.id
        result = presets.resolve_presets(user_id=actor, pool_id=self._author_pool_for(actor))
        keys = presets.SYSTEM_DEFAULTS
        return jsonify(values={key: result.values[key] for key in keys},
                       origins={key: result.origins[key] for key in keys},
                       locked=sorted(result.locked.intersection(keys)))

    def _system_preset(self, key: str):
        _require_central_admin()
        if key not in presets.SYSTEM_DEFAULTS:
            raise PreferenceError("not_allowlisted")
        if request.method == "DELETE":
            presets.ClinicPreset.query.filter_by(scope="system", scope_id=presets.SYSTEM_SCOPE_ID, key=key).delete()
        else:
            body = _body()
            is_locked = body.get("is_locked", False)
            if "value" not in body or body.keys() - {"value", "is_locked"} or type(is_locked) is not bool:
                raise PreferenceError("invalid_payload")
            if not _valid_preset_value(key, body["value"]):
                raise PreferenceError("bad_value")
            try:
                presets.set_preset(key, body["value"], is_locked=is_locked)
            except IntegrityError:
                db.session.rollback()
                return _problem("conflict")
        db.session.commit()
        return "", 204

    # -- correction playback mode (W6) --------------------------------------
    def _correction_mode(self):
        """Only the signed-in user's own row is read or written."""
        actor = current_user.id
        if request.method == "GET":
            mode, version = correction_modes.get_mode(actor)
            return jsonify(mode=mode, version=version)
        body = _body()
        mode, version = correction_modes.set_mode(actor, body.get("mode"), body.get("expected_version"))
        return jsonify(mode=mode, version=version)
