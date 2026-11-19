"""Disabled-by-default identity provider endpoints (PAR-ADM-009/010, R-056/057/058/061).

Thin HTTP layer over ``entra``, ``entra_link_intent`` and ``iwa_identity``.
Local accounts never depend on this module. Rules:

* A disabled provider answers 422 ``CAPABILITY_UNAVAILABLE`` before any
  database access, intent write or outbound call. It never creates or
  changes a user.
* Linking is explicit: an identity admin approves one (user, object id)
  intent, the target user starts it after fresh local authentication, and the
  single-use intent binds the callback to that browser session. There is no
  e-mail matching. Wrong issuer, tenant, object, state, replays and expired
  intents all end in the same generic 403.
* The actor comes from the session only. A body with unknown keys is refused.
  Every cookie-writing POST needs CSRF. The OAuth callback is a GET: the
  state bound to the browser session is its CSRF protection.
* Owner decision x07: no web path links (and so resets the password of) another
  administrator. That belongs to the operator CLI.

The code exchange is injected (``exchange(pending, code) -> (id_token, jwks)``)
so the production binding to ``entra_transport`` and the registration in
app.py are done by the routes-identity consumer package.
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import Callable

from flask import Blueprint, abort, jsonify, request, session
from flask_login import current_user, login_user
from flask_wtf.csrf import CSRFError, validate_csrf
from wtforms.validators import ValidationError as CsrfValidationError

from src.clinic import entra, entra_link_intent as intents, identity, iwa_identity
from src.clinic.config import clinic_mode_enabled
from src.clinic.parity_dispatch import _problem
from src.database import db
from src.models import User
from src.services import audit

logger = logging.getLogger(__name__)

BASE = "/api/clinic/v1/identity/providers"
BROWSER_KEY = "clinic_idp_browser"
Exchange = Callable[[entra.PendingLogin, str], tuple[str, dict]]


class _BadBody(ValueError):
    pass


class _Body:
    """Strict JSON object: unknown keys (an actor, a role) are refused."""

    def __init__(self, allowed: set[str]):
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or body.keys() - allowed:
            raise _BadBody
        self.data = body

    def get(self, key: str, kind: type, *, required: bool = True):
        value = self.data.get(key)
        if value is None and not required:
            return None
        if type(value) is not kind or (kind is int and value < 0) or (kind is str and not 0 < len(value) <= 256):
            raise _BadBody
        return value


class IdentityProvidersApi:
    """Mount ``blueprint`` once; registration in app.py belongs to a consumer package."""

    def __init__(self, entra_config: entra.EntraConfig, exchange: Exchange, *,
                 iwa_config: iwa_identity.IwaConfig | None = None,
                 claim_once: Callable[[str, str, int], bool] | None = None,
                 start_session: Callable[[int, int], None] | None = None):
        self._entra, self._exchange = entra_config, exchange
        self._iwa, self._claim_once = iwa_config or iwa_identity.IwaConfig(), claim_once
        self._start_session = start_session or self._login_user
        self.blueprint = Blueprint("clinic_identity_providers", __name__)
        add = self.blueprint.add_url_rule
        add(BASE, "discovery", self._discovery, methods=["GET"])
        add(BASE + "/entra/login", "entra_login", self._entra_login, methods=["POST"])
        add(BASE + "/entra/callback", "entra_callback", self._entra_callback, methods=["GET"])
        add(BASE + "/entra/link/start", "entra_link_start", self._link_start, methods=["POST"])
        add(BASE + "/iwa/login", "iwa_login", self._iwa_login, methods=["POST"])
        add("/api/clinic/v1/identity/users/<int:user_id>/entra-link-requests", "link_request",
            self._link_request, methods=["POST"])
        add("/api/clinic/v1/identity/users/<int:user_id>/entra-links/<int:link_id>/unlink", "unlink",
            self._unlink, methods=["POST"])
        self.blueprint.register_error_handler(CSRFError, lambda _error: _problem("FORBIDDEN"))

    # --- shared guards ---

    @staticmethod
    def _inert():
        response = jsonify(type="about:blank", title="Anbieter nicht aktiviert", status=422,
                           code="CAPABILITY_UNAVAILABLE")
        response.mimetype = "application/problem+json"
        return response, 422

    @staticmethod
    def _actor():
        if not (current_user.is_authenticated and current_user.is_active
                and session.get("_user_id") == current_user.get_id()):
            return None
        return current_user._get_current_object()

    @staticmethod
    def _csrf_ok() -> bool:
        try:
            validate_csrf(request.headers.get("X-CSRF-Token"))
        except CsrfValidationError:
            return False
        return True

    @staticmethod
    def _login_user(user_id: int, security_version: int) -> None:
        user = db.session.get(User, user_id)
        if user is None or identity.security_version_of(user_id) != security_version:
            raise entra.EntraLoginDenied()
        session.clear()  # new session content for the new identity
        login_user(user)

    def _fail(self, error: Exception):
        db.session.rollback()
        if isinstance(error, _BadBody):
            return _problem("INVALID_PAYLOAD")
        if isinstance(error, entra.EntraDisabled):
            return self._inert()
        if isinstance(error, (identity.VersionConflict, identity.LastAdminProtected)) or (
                isinstance(error, entra.LinkError) and error.code in (
                    "version_conflict", "link_conflict", "unlink_would_disable_login")):
            return _problem("REVISION_CONFLICT")
        if isinstance(error, (entra.EntraError, identity.IdentityError)):
            logger.warning("clinic idp: refused (%s)", getattr(error, "code", type(error).__name__))
            return _problem("FORBIDDEN")
        logger.exception("clinic idp: dependency failure")
        return _problem("DEPENDENCY_UNAVAILABLE")

    @staticmethod
    def _browser() -> str:
        if BROWSER_KEY not in session:
            session[BROWSER_KEY] = secrets.token_urlsafe(32)
        return session[BROWSER_KEY]

    def _guard(self, enabled: bool, *, csrf: bool = True):
        """None to continue, else the response; the actor is read by the caller."""
        if not clinic_mode_enabled():
            abort(404)
        if not enabled:
            return self._inert()
        if csrf and not self._csrf_ok():
            return _problem("FORBIDDEN")
        return None

    # --- endpoints ---

    def _discovery(self):
        if not clinic_mode_enabled():
            abort(404)
        return jsonify(providers=[{"provider": "entra", "enabled": self._entra.enabled},
                                  {"provider": "iwa", "enabled": self._iwa.enabled is True},
                                  {"provider": "ldap", "enabled": False}])

    def _entra_login(self):
        if (refused := self._guard(self._entra.enabled)) is not None:
            return refused
        try:
            url = intents.begin(self._entra, browser_session=self._browser(), current_user_id=None,
                                purpose="login")
        except Exception as error:
            return self._fail(error)
        return jsonify(authorization_url=url)

    def _link_request(self, user_id: int):
        if (refused := self._guard(self._entra.enabled)) is not None:
            return refused
        actor = self._actor()
        if actor is None:
            return _problem("UNAUTHORIZED")
        try:
            body = _Body({"object_id", "expected_version", "password"})
            object_id, version = body.get("object_id", str), body.get("expected_version", int)
            fresh = identity.authenticate_local(actor.username, body.get("password", str))
            policy = identity._policy(user_id)
            target = db.session.get(User, user_id)
            if fresh.user_id != actor.id or fresh.must_change_password:
                raise entra.LinkError("not_authorized")
            if target is None or policy is None:
                raise entra.LinkError("link_not_permitted")
            if user_id != actor.id and (target.is_admin or policy.is_identity_admin):
                raise entra.LinkError("not_authorized")  # x07: no web path onto another admin
            request_id = intents.approve_link(self._entra, user_id=user_id, requested_by=actor.id,
                                              object_id=object_id, expected_version=version)
        except identity.AuthenticationFailed:
            return _problem("FORBIDDEN")
        except Exception as error:
            return self._fail(error)
        return jsonify(request_id=request_id), 201

    def _link_start(self):
        if (refused := self._guard(self._entra.enabled)) is not None:
            return refused
        actor = self._actor()
        if actor is None:
            return _problem("UNAUTHORIZED")
        try:
            body = _Body({"request_id", "password"})
            request_id = body.get("request_id", int)
            fresh = identity.authenticate_local(actor.username, body.get("password", str))
            url = intents.begin(self._entra, browser_session=self._browser(), current_user_id=actor.id,
                                purpose="link", request_id=request_id, fresh_local_auth=fresh)
        except identity.AuthenticationFailed:
            return _problem("FORBIDDEN")
        except Exception as error:
            return self._fail(error)
        return jsonify(authorization_url=url)

    def _entra_callback(self):
        if (refused := self._guard(self._entra.enabled, csrf=False)) is not None:
            return refused
        state, code = request.args.get("state"), request.args.get("code")
        browser = session.get(BROWSER_KEY)
        actor = self._actor()
        try:
            if not (isinstance(state, str) and isinstance(code, str) and code and isinstance(browser, str)):
                raise entra.EntraStateError("callback_invalid")
            row = intents.ClinicIdentityLinkRequest.query.filter_by(
                state_hash=intents._hash(state), browser_hash=intents._hash(browser)).first()
            if row is None or (row.purpose == "link" and actor is None):
                raise entra.EntraStateError("intent_invalid")
            link = row.purpose == "link"
            result = intents.complete(
                self._entra, purpose=row.purpose, browser_session=browser,
                current_user_id=actor.id if link else None, returned_state=state,
                redirect_uri=self._entra.redirect_uri, exchange=lambda pending: self._exchange(pending, code))
            if link:
                audit.record_in_tx("auth.sso.linked", actor_user_id=result.user_id, target_user_id=result.user_id)
                db.session.commit()
                return jsonify(linked=True)
            self._start_session(result.user_id, result.security_version)
            audit.record_in_tx("auth.login.succeeded", actor_user_id=result.user_id,
                               target_user_id=result.user_id, details={"method": "entra"})
            db.session.commit()
        except Exception as error:
            return self._fail(error)
        return jsonify(signed_in=True)

    def _iwa_login(self):
        if (refused := self._guard(self._iwa.enabled is True)) is not None:
            return refused
        try:
            if self._claim_once is None:
                raise iwa_identity.AuthenticationFailed()
            binding = iwa_identity.resolve_identity(self._iwa, request.environ, claim_once=self._claim_once)
            self._start_session(binding.user_id, binding.security_version)
            audit.record_in_tx("auth.login.succeeded", actor_user_id=binding.user_id,
                               target_user_id=binding.user_id, details={"method": "iwa"})
            db.session.commit()
        except Exception as error:
            return self._fail(error)
        return jsonify(signed_in=True)

    def _unlink(self, user_id: int, link_id: int):
        # Unlinking stays possible while the provider is disabled.
        if not clinic_mode_enabled():
            abort(404)
        actor = self._actor()
        if actor is None:
            return _problem("UNAUTHORIZED")
        if not self._csrf_ok():
            return _problem("FORBIDDEN")
        try:
            version = _Body({"expected_version"}).get("expected_version", int)
            intents.unlink(link_id, user_id=user_id, revoked_by=actor.id, expected_version=version)
            audit.record_in_tx("auth.sso.unlinked", actor_user_id=actor.id, target_user_id=user_id)
            db.session.commit()
        except Exception as error:
            return self._fail(error)
        return "", 204
