"""Prepared, disabled Entra OIDC adapter (docs/10_Local_Entra.md sections 8 and 9).

Building blocks only, no Flask route and no network access of its own:

* ``EntraConfig`` is read from the ``auth.hybrid.example.json`` shape. It is
  enabled only for ``mode == "hybrid"``, ``entra_enabled`` and an approved
  real provider. While disabled every entry point raises ``EntraDisabled``
  first: no DNS lookup, no HTTP request, no key fetch, no database access.
* Authorization code flow with PKCE (S256), state, nonce and a strict
  redirect-URI match. Endpoints that need the network (token exchange, JWKS)
  are the caller's job; this module only builds the request parameters and
  validates what the caller hands in.
* ``validate_id_token`` checks a caller supplied JWKS: algorithm allowlist,
  tenant specific issuer, ``tid``, audience, ``exp``/``nbf`` with small skew
  and nonce. The subject is the ``sub`` claim; e-mail, UPN and name are ignored.
* ``resolve_identity`` maps (issuer, tenant, subject) to a ``user.id`` only
  through an active ``ClinicExternalIdentity`` row made by an identity
  administrator. No e-mail matching, no provisioning, no claim based rights.
  Account status and login policy must allow Entra.
* Errors and log lines carry fixed codes only: never a token, code, state,
  nonce, verifier or client secret.

Wiring into routes and the upstream SSO linking is a later package.
Call ``create_tables`` (or import this module before ``db.create_all()``).
"""

import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlencode, urlsplit

from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import RSAKey
from sqlalchemy import CheckConstraint, Index, text
from sqlalchemy.exc import IntegrityError

from src.clinic import identity
from src.database import db

logger = logging.getLogger(__name__)

ALLOWED_HOSTS = ("login.microsoftonline.com", "login.microsoftonline.us", "login.partner.microsoftonline.cn")
ALLOWED_SCOPES = frozenset({"openid", "profile", "email"})
ALLOWED_ALGORITHMS = ("RS256",)
ENTRA_POLICIES = ("entra_only", "local_and_entra")
MAX_TOKEN_CHARS = 16384
PENDING_TTL_SECONDS = 600
DEFAULT_SKEW_SECONDS = 60
MAX_SKEW_SECONDS = 300
_GUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_VERIFIER_RE = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")


# Errors carry fixed codes, never a secret.
class EntraError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class EntraDisabled(EntraError):
    def __init__(self):
        super().__init__("entra_disabled")


class EntraConfigError(EntraError): pass  # noqa: E701
class EntraStateError(EntraError): pass  # noqa: E701
class EntraTokenError(EntraError): pass  # noqa: E701
class LinkError(EntraError): pass  # noqa: E701


class EntraLoginDenied(EntraError):
    """Unknown link, revoked link, disabled user or forbidden policy: not distinguishable."""

    def __init__(self):
        super().__init__("login_denied")


# --- configuration ---

def _norm_guid(value):
    value = value.strip().lower() if isinstance(value, str) else ""
    return value if _GUID_RE.match(value) else None


@dataclass(frozen=True)
class EntraConfig:
    enabled: bool = False
    tenant_id: str = ""
    client_id: str = ""
    secret_file: str = field(default="", repr=False)
    allowed_issuer: str = ""
    discovery_url: str = ""
    redirect_uri: str = ""
    scopes: tuple = ("openid", "profile", "email")
    algorithms: tuple = ALLOWED_ALGORITHMS
    skew_seconds: int = DEFAULT_SKEW_SECONDS

    @classmethod
    def from_dict(cls, data):
        """Read the auth.*.json shape. Missing or false switches mean disabled.

        An enabled profile with incomplete or unsafe settings raises
        EntraConfigError (the local configuration stays untouched by the caller).
        """
        data = data if isinstance(data, dict) else {}
        approved = bool((data.get("activation") or {}).get("real_provider_approved"))
        if not (data.get("mode") == "hybrid" and data.get("entra_enabled") is True and approved):
            return cls()
        entra = data.get("entra") or {}
        tenant = _norm_guid(entra.get("tenant_id"))
        if tenant is None:
            raise EntraConfigError("tenant_id_invalid")  # also rejects common/organizations/consumers
        client_id = entra.get("client_id")
        if not isinstance(client_id, str) or not client_id.strip():
            raise EntraConfigError("client_id_missing")
        secret_file = entra.get("secret_file")
        if not isinstance(secret_file, str) or not secret_file:
            raise EntraConfigError("secret_file_missing")
        issuer = entra.get("allowed_issuer")
        parts = urlsplit(issuer) if isinstance(issuer, str) else None
        if (
            parts is None or parts.scheme != "https" or parts.hostname not in ALLOWED_HOSTS
            or issuer != "https://%s/%s/v2.0" % (parts.hostname, tenant)
        ):
            raise EntraConfigError("issuer_not_tenant_specific")
        if entra.get("discovery_url") != issuer + "/.well-known/openid-configuration":
            raise EntraConfigError("discovery_url_invalid")
        redirect = entra.get("redirect_uri")
        rparts = urlsplit(redirect) if isinstance(redirect, str) else None
        if rparts is None or rparts.scheme != "https" or not rparts.hostname or rparts.fragment:
            raise EntraConfigError("redirect_uri_invalid")
        scopes = tuple(entra.get("scopes") or ())
        if "openid" not in scopes or not set(scopes) <= ALLOWED_SCOPES:
            raise EntraConfigError("scopes_invalid")
        if entra.get("pkce") is not True:
            raise EntraConfigError("pkce_required")
        return cls(True, tenant, client_id.strip(), secret_file, issuer, issuer + "/.well-known/openid-configuration",
                   redirect, scopes)

    def with_skew(self, seconds):
        if not isinstance(seconds, int) or isinstance(seconds, bool) or not 0 <= seconds <= MAX_SKEW_SECONDS:
            raise EntraConfigError("skew_invalid")
        return EntraConfig(**{**self.__dict__, "skew_seconds": seconds})

    @property
    def issuer_authority(self):
        return self.allowed_issuer[: -len("/v2.0")]

    @property
    def authorization_endpoint(self):
        return self.issuer_authority + "/oauth2/v2.0/authorize"

    @property
    def token_endpoint(self):
        return self.issuer_authority + "/oauth2/v2.0/token"


def _require_enabled(config):
    if not isinstance(config, EntraConfig) or not config.enabled:
        raise EntraDisabled()


# --- PKCE, state, nonce ---

def _b64url(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def pkce_challenge(verifier):
    """S256 code challenge for a verifier of 43 to 128 unreserved characters."""
    if not isinstance(verifier, str) or not _VERIFIER_RE.match(verifier):
        raise EntraStateError("pkce_verifier_invalid")
    return _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


def verify_pkce(verifier, challenge):
    """True if verifier and challenge belong together (constant time)."""
    try:
        expected = pkce_challenge(verifier)
    except EntraStateError:
        return False
    return isinstance(challenge, str) and hmac.compare_digest(expected, challenge)


@dataclass(frozen=True)
class PendingLogin:
    """Per login secrets; the caller keeps them server side (session store), never in a cookie or URL."""

    state: str = field(repr=False)
    nonce: str = field(repr=False)
    code_verifier: str = field(repr=False)
    created_at: int = 0


@dataclass(frozen=True)
class AuthorizationRequest:
    url: str = field(repr=False)
    pending: PendingLogin


def begin_authorization(config, *, now=None):
    """Fresh state, nonce and PKCE pair plus the authorization URL. No network."""
    _require_enabled(config)
    pending = PendingLogin(
        state=secrets.token_urlsafe(32), nonce=secrets.token_urlsafe(32),
        code_verifier=secrets.token_urlsafe(48), created_at=_epoch(now),
    )
    query = urlencode({
        "client_id": config.client_id, "response_type": "code", "redirect_uri": config.redirect_uri,
        "response_mode": "query", "scope": " ".join(config.scopes), "state": pending.state,
        "nonce": pending.nonce, "code_challenge": pkce_challenge(pending.code_verifier),
        "code_challenge_method": "S256",
    })
    return AuthorizationRequest(config.authorization_endpoint + "?" + query, pending)


def check_redirect_uri(config, candidate):
    """Strict, exact string match with the configured redirect URI."""
    _require_enabled(config)
    if not isinstance(candidate, str) or not hmac.compare_digest(candidate, config.redirect_uri):
        raise EntraStateError("redirect_uri_mismatch")


def check_state(config, pending, returned_state, *, now=None):
    """State must match the pending login and be fresh."""
    _require_enabled(config)
    if not isinstance(pending, PendingLogin) or not isinstance(returned_state, str) \
            or not hmac.compare_digest(pending.state, returned_state):
        raise EntraStateError("state_mismatch")
    if _epoch(now) - pending.created_at > PENDING_TTL_SECONDS:
        raise EntraStateError("state_expired")


def token_request_params(config, pending, code):
    """Form parameters for the server side code exchange; reads the secret file now.

    The caller posts them over TLS to ``config.token_endpoint`` and must not log them.
    """
    _require_enabled(config)
    if not isinstance(code, str) or not code:
        raise EntraStateError("code_missing")
    try:
        with open(config.secret_file, encoding="utf-8") as handle:
            secret = handle.read().strip()
    except OSError:
        raise EntraConfigError("secret_file_unreadable") from None
    if not secret:
        raise EntraConfigError("secret_file_empty")
    return {
        "grant_type": "authorization_code", "client_id": config.client_id, "client_secret": secret,
        "code": code, "redirect_uri": config.redirect_uri, "code_verifier": pending.code_verifier,
    }


# --- ID token validation ---

def _epoch(now):
    if now is None:
        return int(time.time())
    return int((now if now.tzinfo else now.replace(tzinfo=timezone.utc)).timestamp()) if isinstance(now, datetime) else int(now)


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _header(token):
    parts = token.split(".")
    if len(parts) != 3:
        raise EntraTokenError("token_malformed")
    try:
        header = json.loads(base64.urlsafe_b64decode(parts[0] + "=" * (-len(parts[0]) % 4)))
    except ValueError:
        raise EntraTokenError("token_malformed") from None
    if not isinstance(header, dict):
        raise EntraTokenError("token_malformed")
    return header


def _key_for(jwks, kid):
    keys = jwks.get("keys") if isinstance(jwks, dict) else None
    for jwk in keys if isinstance(keys, list) else ():
        if isinstance(jwk, dict) and jwk.get("kty") == "RSA" and jwk.get("kid") == kid \
                and jwk.get("use", "sig") == "sig" and "d" not in jwk:
            try:
                return RSAKey.import_key(jwk)
            except (JoseError, ValueError):
                break
    raise EntraTokenError("unknown_kid")


def validate_id_token(config, id_token, jwks, *, expected_nonce, now=None):
    """Verified claims dict or EntraTokenError(code).

    ``jwks`` is the caller supplied key set ({"keys": [...]}); this function
    never fetches one. ``alg`` must be in the allowlist (no ``none``, no HS*),
    the key must be an RSA public key selected by ``kid``.
    """
    _require_enabled(config)
    if not isinstance(id_token, str) or not 0 < len(id_token) <= MAX_TOKEN_CHARS:
        raise EntraTokenError("token_malformed")
    header = _header(id_token)
    alg = header.get("alg")
    if not isinstance(alg, str) or alg not in config.algorithms or alg.lower() == "none":
        raise EntraTokenError("alg_not_allowed")
    kid = header.get("kid")
    if not isinstance(kid, str) or not kid:
        raise EntraTokenError("unknown_kid")
    key = _key_for(jwks, kid)
    try:
        claims = jwt.decode(id_token, key, algorithms=list(config.algorithms)).claims
    except (JoseError, ValueError, KeyError, TypeError):
        raise EntraTokenError("signature_invalid") from None
    if not isinstance(claims, dict):
        raise EntraTokenError("token_malformed")

    if claims.get("iss") != config.allowed_issuer:
        raise EntraTokenError("issuer_mismatch")
    tid = _norm_guid(claims.get("tid"))
    if tid is None or tid != config.tenant_id:
        raise EntraTokenError("tenant_mismatch")
    aud = claims.get("aud")
    if not (aud == config.client_id if isinstance(aud, str) else isinstance(aud, list) and config.client_id in aud):
        raise EntraTokenError("audience_mismatch")
    current, skew = _epoch(now), config.skew_seconds
    exp, nbf, iat = _number(claims.get("exp")), _number(claims.get("nbf")), claims.get("iat")
    if exp is None:
        raise EntraTokenError("exp_missing")
    if current > exp + skew:
        raise EntraTokenError("token_expired")
    if "nbf" in claims and (nbf is None or nbf > current + skew):
        raise EntraTokenError("token_not_yet_valid")
    if iat is not None and (_number(iat) is None or iat > current + skew):
        raise EntraTokenError("token_not_yet_valid")
    nonce = claims.get("nonce")
    if not isinstance(nonce, str) or not isinstance(expected_nonce, str) \
            or not hmac.compare_digest(nonce, expected_nonce):
        raise EntraTokenError("nonce_mismatch")
    subject = claims.get("sub")
    if not isinstance(subject, str) or not subject.strip() or len(subject) > 255:
        raise EntraTokenError("subject_missing")
    return claims


# --- explicit external identity links ---

def _utcnow():
    return datetime.now(timezone.utc)


class ClinicExternalIdentity(db.Model):
    """Administrator made binding (issuer, tenant, subject) -> user.id.

    Revoked rows stay as history. Uniqueness holds among active rows only, so a
    revoked identity can be re-linked by a new, separate administrative act.
    """

    __tablename__ = "clinic_external_identity"
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    issuer = db.Column(db.String(255), nullable=False)
    tenant_id = db.Column(db.String(36), nullable=False)
    subject = db.Column(db.String(255), nullable=False)
    object_id = db.Column(db.String(36), nullable=True)  # Entra oid; checked on every login when set
    status = db.Column(db.String(12), nullable=False, default="active")
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow)
    revoked_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    revoked_at = db.Column(db.DateTime(timezone=True), nullable=True)
    version = db.Column(db.Integer, nullable=False, default=1)
    __table_args__ = (
        CheckConstraint("status IN ('active','revoked')", name="ck_clinic_external_identity_status"),
        Index("uq_clinic_external_identity_subject", "issuer", "subject", unique=True,
              sqlite_where=text("status = 'active'"), postgresql_where=text("status = 'active'")),
        Index("uq_clinic_external_identity_user", "user_id", unique=True,
              sqlite_where=text("status = 'active'"), postgresql_where=text("status = 'active'")),
        Index("uq_clinic_external_identity_object", "tenant_id", "object_id", unique=True,
              sqlite_where=text("status = 'active' AND object_id IS NOT NULL"),
              postgresql_where=text("status = 'active' AND object_id IS NOT NULL")),
    )


ENTRA_TABLES = (ClinicExternalIdentity,)


def create_tables(bind=None):
    """Create this module's tables if they are missing."""
    db.metadata.create_all(
        bind=bind or db.engine, tables=[model.__table__ for model in ENTRA_TABLES], checkfirst=True,
    )


def _identity_admin(actor_id):
    policy = identity._policy(actor_id) if isinstance(actor_id, int) else None
    if policy is None or not policy.is_identity_admin or policy.status != "active":
        raise LinkError("not_authorized")


def create_link(config, user_id, *, subject, created_by, object_id=None):
    """Bind (issuer, tenant, subject) of the configured tenant to an existing user.

    Only an active identity administrator may do this, and only while the
    provider is enabled. The login policy is not changed here: the user can
    log in with Entra only if the policy already allows it. A conflict with an
    active binding of the same identity or of the same user raises LinkError.
    """
    _require_enabled(config)
    _identity_admin(created_by)
    subject = subject.strip() if isinstance(subject, str) else ""
    oid = _norm_guid(object_id) if object_id is not None else None
    if not subject or len(subject) > 255 or (object_id is not None and oid is None):
        raise LinkError("identity_invalid")
    if identity._policy(user_id) is None:
        raise LinkError("no_account_policy")
    row = ClinicExternalIdentity(
        user_id=user_id, issuer=config.allowed_issuer, tenant_id=config.tenant_id,
        subject=subject, object_id=oid, created_by=created_by,
    )
    try:
        db.session.add(row)
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise LinkError("link_conflict") from None
    logger.info("clinic entra: link %s for user %s created by %s", row.id, user_id, created_by)
    return row


def revoke_link(link_id, *, revoked_by, expected_version=None):
    """Revoke a link, keep the row, end the user's sessions. Works while the provider is disabled."""
    _identity_admin(revoked_by)
    row = db.session.get(ClinicExternalIdentity, link_id)
    if row is None or row.status != "active":
        raise LinkError("link_not_active")
    if expected_version is not None and row.version != expected_version:
        raise LinkError("version_conflict")
    row.status, row.revoked_by, row.revoked_at, row.version = "revoked", revoked_by, _utcnow(), row.version + 1
    user, policy = identity._account(row.user_id)
    identity._bump_security(user, policy)  # same transaction as the revoke
    db.session.commit()
    logger.info("clinic entra: link %s revoked by %s", link_id, revoked_by)
    return row


@dataclass(frozen=True)
class EntraLogin:
    user_id: int
    security_version: int
    issuer: str
    subject: str = field(repr=False)


def resolve_identity(config, claims):
    """Map verified claims to a user through an active link or raise EntraLoginDenied.

    The e-mail is never looked at. Roles, groups and every other claim are ignored.
    """
    _require_enabled(config)
    row = ClinicExternalIdentity.query.filter_by(
        issuer=claims.get("iss"), subject=claims.get("sub"), status="active").first()
    reason = None
    if row is None:
        reason = "no_link"
    elif row.tenant_id != _norm_guid(claims.get("tid")):
        reason = "tenant_mismatch"
    elif row.object_id and row.object_id != _norm_guid(claims.get("oid")):
        reason = "object_mismatch"
    else:
        policy = identity._policy(row.user_id)
        if policy is None or policy.status != "active":
            reason = "account_not_active"
        elif policy.login_policy not in ENTRA_POLICIES:
            reason = "policy_forbids_entra"
    if reason:
        logger.warning("clinic entra: login denied (%s)", reason)
        raise EntraLoginDenied()
    return EntraLogin(row.user_id, policy.security_version, row.issuer, row.subject)


def complete_login(config, pending, *, returned_state, id_token, jwks, redirect_uri=None, now=None):
    """Callback steps after the caller exchanged the code: state, redirect, token, identity."""
    _require_enabled(config)
    check_state(config, pending, returned_state, now=now)
    if redirect_uri is not None:
        check_redirect_uri(config, redirect_uri)
    claims = validate_id_token(config, id_token, jwks, expected_nonce=pending.nonce, now=now)
    return resolve_identity(config, claims)
