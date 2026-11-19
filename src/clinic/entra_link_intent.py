"""Server-side Entra callback intents for the clinic identity routes.

Callers supply the current user and opaque browser session from trusted session
state, enforce CSRF and recent reauthentication at initiation, and issue/rotate
the browser session only after completion. These service calls own their DB
transaction. The server-side exchange must use a bounded timeout.
"""

from collections.abc import Callable
from hashlib import sha256
from time import time

from sqlalchemy import select, update

from src.clinic import entra, identity
from src.clinic.config import clinic_mode_enabled
from src.database import db
from src.models import User


class ClinicIdentityLinkRequest(db.Model):  # type: ignore[name-defined]
    __tablename__ = "clinic_identity_link_request"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    requested_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    purpose = db.Column(db.String(5), nullable=False)
    expected_tenant_id = db.Column(db.String(36), nullable=False)
    expected_object_id = db.Column(db.String(36), nullable=True)
    account_version = db.Column(db.Integer, nullable=True)
    expires_at = db.Column(db.BigInteger, nullable=False)
    state_hash = db.Column(db.String(64), unique=True)
    browser_hash = db.Column(db.String(64))
    nonce = db.Column(db.String(43))
    code_verifier = db.Column(db.String(64))
    created_at = db.Column(db.BigInteger)
    consumed_at = db.Column(db.BigInteger)


def create_tables(bind=None) -> None:
    ClinicIdentityLinkRequest.__table__.create(bind=bind or db.engine, checkfirst=True)


def _enabled(config: entra.EntraConfig) -> None:
    if not clinic_mode_enabled():
        raise entra.EntraDisabled()
    entra._require_enabled(config)


def _hash(value: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 256 or not value.isascii():
        raise entra.EntraStateError("intent_invalid")
    return sha256(value.encode("ascii")).hexdigest()


def _claim(statement):
    row = db.session.scalars(statement.returning(ClinicIdentityLinkRequest)).one_or_none()
    if row is None:
        raise entra.EntraStateError("intent_invalid")
    return row


def approve_link(config: entra.EntraConfig, *, user_id: int, requested_by: int,
                 object_id: str, expected_version: int) -> int:
    """Record the object ID verified by a recently reauthenticated identity admin."""
    _enabled(config)
    entra._identity_admin(requested_by)
    policy = identity._policy(user_id)
    oid = entra._norm_guid(object_id)
    if not oid or not policy or policy.status != "active" or policy.login_policy not in identity.LOCAL_POLICIES:
        raise entra.LinkError("link_not_permitted")
    if policy.version != expected_version:
        raise identity.VersionConflict("version_conflict")
    row = ClinicIdentityLinkRequest(
        purpose="link", user_id=user_id, requested_by=requested_by, account_version=policy.version,
        expected_tenant_id=config.tenant_id, expected_object_id=oid,
        expires_at=int(time()) + entra.PENDING_TTL_SECONDS,
    )
    db.session.add(row)
    db.session.flush()
    request_id = row.id
    db.session.commit()
    return request_id


def begin(config: entra.EntraConfig, *, browser_session: str, current_user_id: int | None,
          purpose: str, request_id: int | None = None,
          fresh_local_auth: identity.AuthResult | None = None) -> str:
    """Return the authorization URL; keep the pending PKCE verifier server-side."""
    _enabled(config)
    browser_hash = _hash(browser_session)
    authorization = entra.begin_authorization(config)
    pending = authorization.pending
    values = dict(state_hash=_hash(pending.state), browser_hash=browser_hash, nonce=pending.nonce,
                  code_verifier=pending.code_verifier, created_at=pending.created_at)
    try:
        if purpose == "login" and request_id is None:
            version = None if current_user_id is None else identity.security_version_of(current_user_id)
            if current_user_id is not None and version is None:
                raise entra.EntraLoginDenied()
            db.session.add(ClinicIdentityLinkRequest(
                purpose=purpose, user_id=current_user_id, expected_tenant_id=config.tenant_id,
                account_version=version, expires_at=pending.created_at + entra.PENDING_TTL_SECONDS, **values,
            ))
        elif purpose == "link" and current_user_id is not None and fresh_local_auth is not None:
            row = _claim(update(ClinicIdentityLinkRequest).where(
                ClinicIdentityLinkRequest.id == request_id, ClinicIdentityLinkRequest.purpose == purpose,
                ClinicIdentityLinkRequest.user_id == current_user_id,
                ClinicIdentityLinkRequest.expected_tenant_id == config.tenant_id,
                ClinicIdentityLinkRequest.state_hash.is_(None),
                ClinicIdentityLinkRequest.expires_at > pending.created_at,
            ).values(**values))
            policy = identity._policy(current_user_id)
            if (not policy or policy.status != "active" or policy.login_policy not in identity.LOCAL_POLICIES
                    or fresh_local_auth.user_id != current_user_id or fresh_local_auth.must_change_password
                    or fresh_local_auth.security_version != policy.security_version):
                raise entra.LinkError("link_not_permitted")
            identity._check_version(policy, row.account_version)
        else:
            raise entra.EntraStateError("intent_invalid")
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return authorization.url


def _bind(config, row, claims):
    # Lock target and identity admins in a consistent order. The last local
    # administrator must remain usable even during concurrent link transitions.
    policies = identity.ClinicAccountPolicy
    locked = db.session.scalars(select(policies).where(
        (policies.user_id == row.user_id) | policies.is_identity_admin.is_(True),
    ).order_by(policies.user_id).with_for_update().execution_options(populate_existing=True)).all()
    policy = next((item for item in locked if item.user_id == row.user_id), None)
    if (not policy or policy.status != "active" or policy.login_policy not in identity.LOCAL_POLICIES
            or entra._norm_guid(claims.get("oid")) != row.expected_object_id
            or claims["sub"] != claims["sub"].strip()):
        raise entra.LinkError("link_not_permitted")
    identity._check_version(policy, row.account_version)
    user = db.session.get(User, row.user_id, populate_existing=True)
    if policy.login_policy == "local_only":
        if policy.is_identity_admin and not identity._other_local_admins(user.id):
            raise identity.LastAdminProtected("last_local_identity_admin")
        policy.login_policy = "entra_only"
        user.password = None
        identity._revoke_open_codes(user.id, identity._utcnow())
    identity._bump_security(user, policy)
    result = entra.EntraLogin(user.id, policy.security_version, config.allowed_issuer, claims["sub"])
    # create_link commits the binding, intent claim and session revocation together.
    entra.create_link(config, user.id, subject=claims["sub"], created_by=row.requested_by,
                      object_id=row.expected_object_id)
    return result


def complete(config: entra.EntraConfig, *, purpose: str, browser_session: str,
             current_user_id: int | None, returned_state: str, redirect_uri: str,
             exchange: Callable[[entra.PendingLogin], tuple[str, dict]]) -> entra.EntraLogin:
    """Exchange and validate once; a failed callback rolls back the intent claim."""
    _enabled(config)
    entra.check_redirect_uri(config, redirect_uri)
    now = int(time())
    intent = ClinicIdentityLinkRequest
    try:
        row = _claim(update(intent).where(
            intent.state_hash == _hash(returned_state), intent.browser_hash == _hash(browser_session),
            intent.purpose == purpose, intent.user_id == current_user_id,
            intent.expected_tenant_id == config.tenant_id, intent.expires_at > now,
            intent.consumed_at.is_(None),
        ).values(consumed_at=now))
        pending = entra.PendingLogin(returned_state, row.nonce, row.code_verifier, row.created_at)
        id_token, jwks = exchange(pending)
        if int(time()) >= row.expires_at:
            raise entra.EntraStateError("state_expired")
        row.code_verifier = row.nonce = None
        if purpose == "link":
            claims = entra.validate_id_token(config, id_token, jwks, expected_nonce=pending.nonce)
            return _bind(config, row, claims)
        result = entra.complete_login(config, pending, returned_state=returned_state, id_token=id_token, jwks=jwks)
        if current_user_id is not None and (result.user_id != current_user_id
                                            or result.security_version != row.account_version):
            raise entra.EntraLoginDenied()
        db.session.commit()
        return result
    except Exception:
        db.session.rollback()
        raise


def unlink(link_id: int, *, user_id: int, revoked_by: int,
           expected_version: int) -> entra.ClinicExternalIdentity:
    """Keep history and login policy, even when the provider is unavailable."""
    if not clinic_mode_enabled():
        raise entra.EntraDisabled()
    try:
        entra._identity_admin(revoked_by)
        policy = db.session.get(identity.ClinicAccountPolicy, user_id, with_for_update=True,
                                populate_existing=True)
        if user_id != revoked_by:
            target = db.session.get(User, user_id)
            if target is not None and (target.is_admin or (policy is not None and policy.is_identity_admin)):
                raise entra.LinkError("not_authorized")  # x07: no web path onto another admin
        link = db.session.get(entra.ClinicExternalIdentity, link_id, populate_existing=True)
        if link is None or link.user_id != user_id or not policy:
            raise entra.LinkError("link_not_active")
        if link.version != expected_version:
            raise entra.LinkError("version_conflict")
        if policy.status == "active" and policy.login_policy == "entra_only":
            raise entra.LinkError("unlink_would_disable_login")
        return entra.revoke_link(link_id, revoked_by=revoked_by, expected_version=expected_version)
    except Exception:
        db.session.rollback()
        raise
