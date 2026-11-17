"""Local user lifecycle for the clinic fork (docs/10_Local_Entra.md).

Service functions on the existing ``db`` session. The internal key stays
``user.id``; account policy, one-time recovery codes and the bootstrap marker
live in the tables declared here (``src/models/user.py`` is untouched).

* Password policy and scrypt hashing as in auth.local.example.json; upstream
  bcrypt hashes verify and are rehashed after a successful login.
* Every authentication failure is one generic exception.
* Recovery codes: 256 bit, stored only as SHA-256, 30 minutes, single use,
  voided by a newer code, a password change or a disable.
* No password, code or hash in a log line or an exception message.

Login routes, audit events and the login lock are wired in a later package.
Call ``create_tables`` (or import this module before ``db.create_all()``).
"""

import contextlib
import hashlib
import logging
import os
import re
import secrets
import threading
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import bcrypt as _bcrypt
from sqlalchemy import CheckConstraint, update
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash, generate_password_hash

from src.database import db
from src.models import User

logger = logging.getLogger(__name__)

SCRYPT_METHOD = "scrypt:131072:8:1"
PASSWORD_MIN_CHARS = 15
PASSWORD_MAX_CHARS = 128
RECOVERY_TTL = timedelta(minutes=30)
RECOVERY_TOKEN_BYTES = 32  # 256 bit
MAX_CONCURRENT_HASHES = 2
HASH_WAIT_SECONDS = 5.0
BLOCKLIST_FILE_ENV = "CLINIC_PASSWORD_BLOCKLIST_FILE"
LOCAL_POLICIES = ("local_only", "local_and_entra")

# Common passwords of >= 15 characters (shorter ones fail the length rule),
# normalised with NFKC/casefold. A site list can be added through
# CLINIC_PASSWORD_BLOCKLIST_FILE (one entry per line, '#' comments).
_BUILTIN_BLOCKLIST = frozenset({
    "passwordpassword", "password1234567", "password12345678", "123456789012345",
    "1234567890123456", "12345678901234567890", "qwertyuiopasdfgh",
    "qwertzuiopasdfgh", "iloveyouiloveyou", "passwortpasswort", "passwort1234567",
    "administrator123", "administratoradmin", "letmeinletmein1", "changemechangeme",
    "abcdefghijklmnop", "abcdefghijklmnopqrstuvwxyz", "0123456789abcdef",
    "welcomewelcome1", "willkommenwillkommen", "krankenhaus12345", "spital1234567890",
})
_BCRYPT_RE = re.compile(r"^\$2[abxy]\$\d\d\$[./A-Za-z0-9]{53}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# Errors carry fixed codes, never a secret.
class IdentityError(Exception): pass  # noqa: E701
class IdentityConflict(IdentityError): pass  # noqa: E701
class IdentityDenied(IdentityError): pass  # noqa: E701
class VersionConflict(IdentityError): pass  # noqa: E701
class LastAdminProtected(IdentityError): pass  # noqa: E701
class HashBusy(IdentityError): pass  # noqa: E701 - all hash slots of this process are busy


class PasswordPolicyError(IdentityError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class AuthenticationFailed(IdentityError):
    """Unknown, disabled, ineligible or wrong credentials: not distinguishable."""

    def __init__(self):
        super().__init__("authentication_failed")


class InvalidRecoveryCode(IdentityError):
    """Unknown, expired, used or superseded code: not distinguishable."""

    def __init__(self):
        super().__init__("invalid_recovery_code")


def _utcnow():
    return datetime.now(timezone.utc)


def _new_id():
    return str(uuid.uuid4())


def _ts(**kwargs):
    return db.Column(db.DateTime(timezone=True), **kwargs)


class ClinicAccountPolicy(db.Model):
    """Clinic account policy per user. No row means no local login: existing
    accounts get local rights only by an explicit decision, never by default."""

    __tablename__ = "clinic_account_policy"
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), primary_key=True)
    status = db.Column(db.String(24), nullable=False)
    login_policy = db.Column(db.String(24), nullable=False, default="local_only")
    # Session invalidation data: raised by reset, disable and explicit revoke.
    security_version = db.Column(db.Integer, nullable=False, default=0)
    version = db.Column(db.Integer, nullable=False, default=1)
    is_identity_admin = db.Column(db.Boolean, nullable=False, default=False)
    must_change_password = db.Column(db.Boolean, nullable=False, default=False)
    password_changed_at = _ts(nullable=True)
    created_at = _ts(nullable=False, default=_utcnow)
    updated_at = _ts(nullable=False, default=_utcnow, onupdate=_utcnow)
    __table_args__ = (
        CheckConstraint("status IN ('pending_activation','active','disabled')",
                        name="ck_clinic_account_policy_status"),
        CheckConstraint("login_policy IN ('local_only','entra_only','local_and_entra','device_only')",
                        name="ck_clinic_account_policy_login_policy"),
    )


class ClinicLocalRecovery(db.Model):
    """Activation or reset code; only its SHA-256 is stored."""

    __tablename__ = "clinic_local_recovery"
    id = db.Column(db.String(36), primary_key=True, default=_new_id)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    purpose = db.Column(db.String(24), nullable=False)
    token_hash = db.Column(db.String(64), nullable=False, unique=True)
    expires_at = _ts(nullable=False)
    consumed_at = _ts(nullable=True)
    revoked_at = _ts(nullable=True)  # superseded or voided, never redeemable
    issued_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    version = db.Column(db.Integer, nullable=False, default=1)
    created_at = _ts(nullable=False, default=_utcnow)
    __table_args__ = (
        CheckConstraint("purpose IN ('activate_local','reset_local')",
                        name="ck_clinic_local_recovery_purpose"),
    )


class ClinicBootstrapMarker(db.Model):
    """Single row (id = 1); the primary key makes a second bootstrap fail."""

    __tablename__ = "clinic_bootstrap_marker"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    created_at = _ts(nullable=False, default=_utcnow)
    __table_args__ = (CheckConstraint("id = 1", name="ck_clinic_bootstrap_marker_single"),)


IDENTITY_TABLES = (ClinicAccountPolicy, ClinicLocalRecovery, ClinicBootstrapMarker)


def create_tables(bind=None):
    """Create this module's tables if they are missing."""
    db.metadata.create_all(
        bind=bind or db.engine,
        tables=[model.__table__ for model in IDENTITY_TABLES],
        checkfirst=True,
    )


# --- password policy and hashing ---

def _normalise(value):
    return unicodedata.normalize("NFKC", value).casefold()


_site_blocklist_cache = {}


def _site_blocklist():
    path = os.environ.get(BLOCKLIST_FILE_ENV)
    if not path:
        return frozenset()
    mtime = os.stat(path).st_mtime
    cached = _site_blocklist_cache.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    with open(path, encoding="utf-8") as handle:
        entries = frozenset(
            _normalise(line.rstrip("\r\n")) for line in handle
            if line.strip() and not line.startswith("#")
        )
    _site_blocklist_cache[path] = (mtime, entries)
    return entries


def validate_password(password):
    """Raise PasswordPolicyError(code) if the policy rejects the password.

    Length counts Unicode code points, 15 to 128. No trimming, truncation or
    composition rules. The local blocklist matches exactly after NFKC/casefold,
    also with all whitespace removed. No external lookup.
    """
    if not isinstance(password, str):
        raise PasswordPolicyError("invalid_type")
    if len(password) < PASSWORD_MIN_CHARS:
        raise PasswordPolicyError("too_short")
    if len(password) > PASSWORD_MAX_CHARS:
        raise PasswordPolicyError("too_long")
    plain = _normalise(password)
    blocked = _BUILTIN_BLOCKLIST | _site_blocklist()
    if plain in blocked or re.sub(r"\s+", "", plain) in blocked:
        raise PasswordPolicyError("blocklisted")


_hash_slots = threading.BoundedSemaphore(MAX_CONCURRENT_HASHES)


@contextlib.contextmanager
def _hash_slot():
    if not _hash_slots.acquire(timeout=HASH_WAIT_SECONDS):
        raise HashBusy("hash_busy")
    try:
        yield
    finally:
        _hash_slots.release()


def hash_password(password):
    """Current scheme (scrypt:131072:8:1, library salt). The caller checks the policy."""
    with _hash_slot():
        return generate_password_hash(password, method=SCRYPT_METHOD)


def verify_password(stored, password):
    """Return (matches, needs_rehash). Unknown hash formats never match."""
    if not stored or not isinstance(password, str):
        return False, False
    if stored.startswith("scrypt:"):
        with _hash_slot():
            ok = check_password_hash(stored, password)  # constant-time compare inside
        return ok, ok and not stored.startswith(SCRYPT_METHOD + "$")
    if _BCRYPT_RE.match(stored):
        try:
            with _hash_slot():
                ok = _bcrypt.checkpw(password.encode("utf-8"), stored.encode("ascii"))
        except ValueError:  # over 72 bytes: the legacy scheme cannot have produced it
            return False, False
        return ok, ok
    return False, False


_dummy_hash = []


def _burn_hash_time(password):
    """Pay for one verification so unknown and ineligible accounts cost the same."""
    if not _dummy_hash:
        _dummy_hash.append(hash_password(secrets.token_urlsafe(24)))
    verify_password(_dummy_hash[0], password if isinstance(password, str) else "")


# --- helpers ---

def _policy(user_id):
    return db.session.get(ClinicAccountPolicy, user_id)


def _local_eligible(policy, user):
    return bool(
        policy and user and user.password
        and policy.status == "active" and policy.login_policy in LOCAL_POLICIES
    )


def _account(user_id):
    policy, user = _policy(user_id), db.session.get(User, user_id)
    if policy is None or user is None:
        raise IdentityDenied("no_account_policy")
    return user, policy


def _check_version(policy, expected_version):
    if expected_version is not None and policy.version != expected_version:
        raise VersionConflict("version_conflict")


def _revoke_open_codes(user_id, now):
    db.session.execute(
        update(ClinicLocalRecovery)
        .where(
            ClinicLocalRecovery.user_id == user_id,
            ClinicLocalRecovery.consumed_at.is_(None),
            ClinicLocalRecovery.revoked_at.is_(None),
        )
        .values(revoked_at=now),
        execution_options={"synchronize_session": False},
    )


def _bump_security(user, policy):
    policy.security_version = (policy.security_version or 0) + 1
    policy.version = (policy.version or 1) + 1
    user.end_all_sessions()


def _hash_code(code):
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _checked_email(username, email):
    """Normalised email; IdentityConflict for invalid or taken username/email."""
    if not isinstance(username, str) or not 2 <= len(username) <= 20:
        raise IdentityConflict("invalid_username")
    email = User.normalize_email(email)
    if not email or len(email) > 120 or not _EMAIL_RE.match(email):
        raise IdentityConflict("invalid_email")
    if User.query.filter_by(username=username).first() or User.find_by_email(email):
        raise IdentityConflict("duplicate")
    return email


def _new_user(username, email, name, password_hash, is_admin):
    from src.config.ui_themes import apply_new_user_defaults

    user = User(username=username, email=email, name=name, password=password_hash,
                is_admin=is_admin, email_verified=True)
    apply_new_user_defaults(user)
    db.session.add(user)
    db.session.flush()
    return user


# --- user lifecycle ---

def create_local_user(username, email, *, name=None, actor_id=None, now=None):
    """Administrator creates a user: pending_activation, local_only, no password.

    Activation runs through ``issue_recovery`` and ``complete_recovery``.
    Raises IdentityConflict for invalid or duplicate username/email.
    """
    now = now or _utcnow()
    email = _checked_email(username, email)
    try:
        user = _new_user(username, email, name, None, False)
        db.session.add(ClinicAccountPolicy(
            user_id=user.id, status="pending_activation", login_policy="local_only",
            created_at=now, updated_at=now,
        ))
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        raise IdentityConflict("duplicate") from None
    logger.info("clinic identity: user %s created by %s", user.id, actor_id)
    return user


def _other_local_admins(user_id):
    rows = (
        db.session.query(ClinicAccountPolicy, User)
        .join(User, User.id == ClinicAccountPolicy.user_id)
        .filter(ClinicAccountPolicy.is_identity_admin.is_(True), ClinicAccountPolicy.user_id != user_id)
        .all()
    )
    return [u.id for p, u in rows if _local_eligible(p, u)]


def set_status(user_id, new_status, *, actor_id=None, expected_version=None, now=None):
    """Disable or reactivate; users are never deleted.

    Disabling raises the security version, ends sessions, voids open codes and
    is refused for the last active, locally usable identity administrator.
    Reactivation gives ``active``, or ``pending_activation`` if no password was set.
    """
    if new_status not in ("active", "disabled"):
        raise IdentityDenied("invalid_status")
    now = now or _utcnow()
    user, policy = _account(user_id)
    _check_version(policy, expected_version)
    if new_status == "disabled":
        if policy.status == "disabled":
            return policy
        if policy.is_identity_admin and _local_eligible(policy, user) and not _other_local_admins(user_id):
            raise LastAdminProtected("last_local_identity_admin")
        policy.status = "disabled"
        _revoke_open_codes(user_id, now)
        _bump_security(user, policy)
    else:
        if policy.status != "disabled":
            raise IdentityDenied("not_disabled")
        policy.status = "active" if user.password else "pending_activation"
        policy.version += 1
    db.session.commit()
    logger.info("clinic identity: user %s set to %s by %s", user_id, policy.status, actor_id)
    return policy


def revoke_sessions(user_id, *, actor_id=None, expected_version=None):
    """Raise the security version; later session checks compare against it."""
    user, policy = _account(user_id)
    _check_version(policy, expected_version)
    _bump_security(user, policy)
    db.session.commit()
    logger.info("clinic identity: sessions of user %s revoked by %s", user_id, actor_id)
    return policy.security_version


def security_version_of(user_id):
    """Current security version, None without account policy."""
    policy = _policy(user_id)
    return None if policy is None else policy.security_version


# --- recovery: administrator issued one-time codes ---

@dataclass(frozen=True)
class IssuedCode:
    """The plain code exists only here; the repr hides it."""

    code: str = field(repr=False)
    purpose: str
    expires_at: datetime


def issue_recovery(user_id, *, issued_by, expected_version=None, now=None):
    """One-time code for a pending (activate_local) or active local (reset_local) user.

    Status and login policy never change; disabled, entra_only and
    device_only accounts are refused. A reset also raises the security
    version (sessions end) and sets must_change_password. Older open codes
    become void.
    """
    now = now or _utcnow()
    user, policy = _account(user_id)
    if policy.login_policy not in LOCAL_POLICIES or policy.status not in ("pending_activation", "active"):
        raise IdentityDenied("recovery_not_permitted")
    _check_version(policy, expected_version)
    purpose = "activate_local" if policy.status == "pending_activation" else "reset_local"
    code = secrets.token_urlsafe(RECOVERY_TOKEN_BYTES)
    _revoke_open_codes(user_id, now)
    db.session.add(ClinicLocalRecovery(
        user_id=user_id, purpose=purpose, token_hash=_hash_code(code),
        expires_at=now + RECOVERY_TTL, issued_by=issued_by, created_at=now,
    ))
    if purpose == "reset_local":
        policy.must_change_password = True
        _bump_security(user, policy)
    else:
        policy.version += 1
    db.session.commit()
    logger.info("clinic identity: %s code issued for user %s by %s", purpose, user_id, issued_by)
    return IssuedCode(code=code, purpose=purpose, expires_at=now + RECOVERY_TTL)


def _apply_new_password(user, policy, password, now):
    validate_password(password)
    user.password = hash_password(password)
    policy.password_changed_at = now
    policy.must_change_password = False
    _revoke_open_codes(user.id, now)
    _bump_security(user, policy)


def complete_recovery(code, new_password, *, now=None):
    """Redeem a code and set the password: no login, no re-enabling, no policy change.

    Consuming is one conditional UPDATE, so of two parallel redemptions only
    one succeeds. Every code problem raises the same InvalidRecoveryCode. A
    rejected password raises PasswordPolicyError and leaves the code unused.
    """
    now = now or _utcnow()
    if not isinstance(code, str) or not 20 <= len(code) <= 128:
        raise InvalidRecoveryCode()
    row = ClinicLocalRecovery.query.filter_by(token_hash=_hash_code(code)).first()
    if row is None:
        raise InvalidRecoveryCode()
    consumed = db.session.execute(
        update(ClinicLocalRecovery)
        .where(
            ClinicLocalRecovery.id == row.id,
            ClinicLocalRecovery.consumed_at.is_(None),
            ClinicLocalRecovery.revoked_at.is_(None),
            ClinicLocalRecovery.expires_at > now,
        )
        .values(consumed_at=now),
        execution_options={"synchronize_session": False},
    ).rowcount
    policy, user = _policy(row.user_id), db.session.get(User, row.user_id)
    wanted = {"activate_local": "pending_activation", "reset_local": "active"}[row.purpose]
    if consumed != 1 or policy is None or user is None or policy.status != wanted \
            or policy.login_policy not in LOCAL_POLICIES:
        db.session.rollback()
        raise InvalidRecoveryCode()
    try:
        _apply_new_password(user, policy, new_password, now)
    except IdentityError:
        db.session.rollback()
        raise
    if row.purpose == "activate_local":
        policy.status = "active"
    db.session.commit()
    logger.info("clinic identity: %s completed for user %s", row.purpose, user.id)


def change_password(user_id, current_password, new_password, *, now=None):
    """Self service change: needs the current password; voids codes, ends sessions."""
    now = now or _utcnow()
    user, policy = db.session.get(User, user_id), _policy(user_id)
    if not _local_eligible(policy, user):
        raise AuthenticationFailed()
    if not verify_password(user.password, current_password)[0]:
        raise AuthenticationFailed()
    _apply_new_password(user, policy, new_password, now)
    db.session.commit()
    logger.info("clinic identity: password changed for user %s", user_id)


# --- authentication ---

@dataclass(frozen=True)
class AuthResult:
    user_id: int
    security_version: int
    must_change_password: bool


def _find_user(login_id):
    if not isinstance(login_id, str) or not login_id or len(login_id) > 120:
        return None
    return User.query.filter_by(username=login_id).first() or User.find_by_email(login_id)


def authenticate_local(login_id, password):
    """Check a local login: AuthResult or AuthenticationFailed.

    Needs an active account, a local login policy and a known hash format.
    Every other case, unknown users included, costs one hash verification and
    ends in the same exception. A legacy or weaker hash is replaced by the
    current scheme after a successful check.
    """
    user = _find_user(login_id)
    policy = _policy(user.id) if user else None
    if (
        not _local_eligible(policy, user)
        or not isinstance(password, str)
        or len(password) > PASSWORD_MAX_CHARS
    ):
        _burn_hash_time(password)
        raise AuthenticationFailed()
    ok, needs_rehash = verify_password(user.password, password)
    if not ok:
        raise AuthenticationFailed()
    if needs_rehash:
        user.password = hash_password(password)
        db.session.commit()
    return AuthResult(user.id, policy.security_version, bool(policy.must_change_password))


# --- bootstrap ---

def bootstrap_first_admin(username, email, password, *, name=None, now=None):
    """Create the first identity administrator once; True if created, False if one exists.

    The account gets technical and identity rights (``is_admin``,
    ``is_identity_admin``) and no clinical role. The marker row (primary key 1)
    makes parallel runs safe: the loser rolls back and returns False.
    """
    now = now or _utcnow()
    if ClinicBootstrapMarker.query.first() or ClinicAccountPolicy.query.filter_by(
        is_identity_admin=True
    ).first():
        return False
    email = _checked_email(username, email)
    validate_password(password)
    password_hash = hash_password(password)
    try:
        user = _new_user(username, email, name, password_hash, True)
        db.session.add(ClinicAccountPolicy(
            user_id=user.id, status="active", login_policy="local_only",
            is_identity_admin=True, password_changed_at=now, created_at=now, updated_at=now,
        ))
        db.session.add(ClinicBootstrapMarker(id=1, user_id=user.id, created_at=now))
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        if ClinicBootstrapMarker.query.first():
            return False
        raise IdentityConflict("duplicate") from None
    logger.info("clinic identity: first identity administrator %s created", user.id)
    return True
