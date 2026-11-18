"""Disabled server-side LDAP federation profile (R-010, R-059, R-061).

The clinical app never binds to a directory and never stores an end-user
directory password. A later federation, if G-LDAP is approved, is an identity
provider in front of LDAPS. This module only checks that disabled profile,
the stable ``(issuer, subject)`` map onto an existing ``user.id``, and the
documented lifecycle source. Account lifecycle stays in ``src.clinic.identity``.

Login claims cannot assign clinical roles or pools. Local startup does not
call a directory and does not depend on this profile. WG-ADM-009 stays open:
workstation accounts are not read. Windows SSO and a direct directory bind
are unsupported and raise ``LdapCapabilityError``.

BT-ADM-009 and BT-ADM-010 keep their original oracles. Nothing here is a
live directory or Windows-SSO proof.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.clinic import config, identity
from src.database import db

LIFECYCLE_SOURCE = "application"
LIFECYCLE_NOTE = (
    "application_identity; directory_lock_is_not_app_revocation; "
    "profile_off_does_not_grant_local_password"
)
SUBJECT_ATTRIBUTES = ("issuer", "subject")
SUPPORTED_PROVIDER = "ldaps_federation"
CAPABILITY_CODE = "ldap_capability_unavailable"

# A directory password must not sit in application configuration. The local
# password-policy object (rules, not a secret) is the only "password" value
# that is allowed, and only with these keys.
_SECRET_KEYS = frozenset({
    "bind_password", "user_password", "ad_password", "directory_password",
    "userpassword", "passwd", "bindpassword",
})
_LOCAL_PASSWORD_POLICY_KEYS = frozenset({
    "min_characters", "max_characters", "trim", "truncate", "hash_method",
    "hash_storage", "legacy_hash_rehash_on_success", "local_blocklist_required",
    "external_password_check", "concurrent_hash_checks_per_web_process",
})
_MAX_ID_CHARS = 512

# shortcut: links live in this process only, move to a table when a route
# needs them across restarts. G-LDAP stays open either way.
_LINKS = {}


class LdapProfileError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class LdapCapabilityError(LdapProfileError):
    """Named capability error. The real directory and workstation SAM stay unavailable."""

    def __init__(self):
        super().__init__(CAPABILITY_CODE)


@dataclass(frozen=True)
class FederationProfile:
    enabled: bool
    provider: str
    issuer: str
    subject_attributes: tuple
    lifecycle_source: str
    lifecycle_note: str
    server_side: bool
    claim_role_sync: bool


@dataclass(frozen=True)
class SubjectLink:
    user_id: int
    issuer: str
    subject: str
    version: int
    lifecycle_source: str


@dataclass(frozen=True)
class ClaimResult:
    user_id: int
    roles: tuple
    pools: tuple
    lifecycle_source: str


def _flag(data: dict[str, Any], name: str) -> Any:
    if name in data:
        return data[name]
    ldap = data.get("ldap")
    if isinstance(ldap, dict) and name in ldap:
        return ldap[name]
    return None


def _reject_password_value(item: Any) -> None:
    if not isinstance(item, dict) or not set(item) <= _LOCAL_PASSWORD_POLICY_KEYS:
        raise LdapProfileError("end_user_password_rejected")
    if item.get("external_password_check") is True:
        raise LdapProfileError("end_user_password_rejected")
    _reject_passwords(item)


def _reject_passwords(value: Any) -> None:
    """Walk a document and refuse any end-user directory password."""
    if isinstance(value, dict):
        for key, item in value.items():
            name = key.lower().replace("-", "_") if isinstance(key, str) else ""
            if name in _SECRET_KEYS:
                raise LdapProfileError("end_user_password_rejected")
            if name == "password":
                _reject_password_value(item)
            else:
                _reject_passwords(item)
    elif isinstance(value, list):
        for item in value:
            _reject_passwords(item)


def _require_off(value: Any) -> None:
    if value is True:
        raise LdapCapabilityError()
    if value is not None and value is not False:
        raise LdapProfileError("profile_invalid")


def _issuer(data: dict[str, Any]) -> str:
    raw = _flag(data, "issuer")
    if raw is None or raw == "":
        return ""
    if not isinstance(raw, str) or len(raw) > _MAX_ID_CHARS or any(ch.isspace() for ch in raw):
        raise LdapProfileError("issuer_invalid")
    return raw


def _subject_attributes(data: dict[str, Any]) -> tuple[str, ...]:
    raw = _flag(data, "subject_attributes")
    if raw is None:
        return SUBJECT_ATTRIBUTES
    if isinstance(raw, list):
        raw = tuple(raw)
    if raw != SUBJECT_ATTRIBUTES:
        raise LdapProfileError("subject_mapping_unstable")
    return SUBJECT_ATTRIBUTES


def _lifecycle(data: dict[str, Any]) -> str:
    raw = _flag(data, "lifecycle_source")
    if raw is None:
        return LIFECYCLE_SOURCE
    if raw != LIFECYCLE_SOURCE:
        raise LdapProfileError("lifecycle_source_invalid")
    return LIFECYCLE_SOURCE


def validate_profile(data: dict[str, Any]) -> FederationProfile:
    """Validate a disabled server-side federation profile.

    A prepared profile (including the auth example documents with
    ``ldap_enabled: false``) returns ``enabled=False`` and the application
    lifecycle source. Turning the provider on, binding directly, or pointing
    at a workstation account raises ``LdapCapabilityError``. A directory
    password raises ``LdapProfileError`` and is not copied onto the result.
    """
    if not isinstance(data, dict):
        raise LdapProfileError("profile_invalid")
    _reject_passwords(data)
    named = _flag(data, "provider")
    if named is None:
        if "ldap_enabled" not in data and "mode" not in data:
            raise LdapProfileError("profile_invalid")
        named = SUPPORTED_PROVIDER
    elif named != SUPPORTED_PROVIDER:
        raise LdapCapabilityError()
    _require_off(_flag(data, "enabled"))
    _require_off(data["ldap_enabled"] if "ldap_enabled" in data else _flag(data, "ldap_enabled"))
    server_side = _flag(data, "server_side")
    if server_side is False:
        raise LdapCapabilityError()
    if server_side is not None and server_side is not True:
        raise LdapProfileError("profile_invalid")
    claim_sync = _flag(data, "claim_role_sync")
    if claim_sync is not None and claim_sync is not False:
        raise LdapProfileError("claim_rights_rejected")
    return FederationProfile(
        False, named, _issuer(data), _subject_attributes(data), _lifecycle(data),
        LIFECYCLE_NOTE, True, False,
    )


def local_startup_ready(data: dict[str, Any] | None = None) -> dict[str, Any]:
    """Local startup does not contact a directory and does not require this profile.

    A missing, disabled or rejected document leaves ``ready`` true. Clinic mode
    is only reported through ``config.clinic_mode_enabled``.
    """
    mode = config.clinic_mode_enabled()
    enabled = False
    error = None
    if data is not None:
        try:
            enabled = validate_profile(data).enabled
        except LdapProfileError as exc:
            error = exc.code
    return {
        "ready": True,
        "directory_contacted": False,
        "clinic_mode": mode,
        "profile_enabled": enabled,
        "profile_error": error,
    }


def clear_subject_links() -> None:
    _LINKS.clear()


def lookup_subject(issuer: str, subject: str) -> SubjectLink | None:
    if not isinstance(issuer, str) or not isinstance(subject, str):
        return None
    return _LINKS.get((issuer, subject))


def _stable_token(value: Any, code: str) -> str:
    if (
        not isinstance(value, str) or not value or value != value.strip()
        or len(value) > _MAX_ID_CHARS or any(ch.isspace() for ch in value)
    ):
        raise LdapProfileError(code)
    if "@" in value:
        raise LdapProfileError("subject_mapping_unstable")
    return value


def _require_admin(actor_id: Any) -> None:
    if isinstance(actor_id, bool) or not isinstance(actor_id, int):
        raise identity.IdentityDenied("not_identity_admin")
    policy = db.session.get(identity.ClinicAccountPolicy, actor_id)
    if policy is None or not policy.is_identity_admin or policy.status != "active":
        raise identity.IdentityDenied("not_identity_admin")


def _require_profile(profile: Any) -> None:
    if not isinstance(profile, FederationProfile):
        raise LdapProfileError("profile_invalid")
    if profile.enabled or profile.provider != SUPPORTED_PROVIDER:
        raise LdapCapabilityError()
    if profile.lifecycle_source != LIFECYCLE_SOURCE:
        raise LdapProfileError("lifecycle_source_invalid")


def map_subject(
    profile: FederationProfile, *, user_id: int, issuer: str, subject: str,
    actor_id: int, expected_version: int | None = None,
) -> SubjectLink:
    """Bind a stable subject to an existing internal user id.

    Needs an active identity administrator. The same subject always stays on
    the same ``user.id``. A wrong ``expected_version`` leaves the link unchanged.
    E-mail is not a subject. No directory password is accepted.
    """
    _require_profile(profile)
    issuer = _stable_token(issuer, "issuer_invalid")
    subject = _stable_token(subject, "subject_invalid")
    if profile.issuer and issuer != profile.issuer:
        raise LdapProfileError("issuer_mismatch")
    _require_admin(actor_id)
    if isinstance(user_id, bool) or not isinstance(user_id, int):
        raise identity.IdentityDenied("no_account_policy")
    if identity.security_version_of(user_id) is None:
        raise identity.IdentityDenied("no_account_policy")
    current = _LINKS.get((issuer, subject))
    if current is None:
        if expected_version is not None:
            raise identity.VersionConflict("version_conflict")
        link = SubjectLink(user_id, issuer, subject, 1, LIFECYCLE_SOURCE)
        _LINKS[(issuer, subject)] = link
        return link
    if expected_version is None:
        if current.user_id != user_id:
            raise identity.IdentityConflict("subject_taken")
        return current
    if current.version != expected_version:
        raise identity.VersionConflict("version_conflict")
    if current.user_id != user_id:
        raise identity.IdentityConflict("subject_taken")
    link = SubjectLink(user_id, issuer, subject, current.version + 1, LIFECYCLE_SOURCE)
    _LINKS[(issuer, subject)] = link
    return link


def apply_claims(profile: FederationProfile, claims: dict[str, Any]) -> ClaimResult:
    """Preview a federation login. Claims never grant roles or pools.

    The internal user id comes only from an explicit subject link. E-mail,
    display name and a claim ``user_id`` are ignored. A directory password in
    the claim set is rejected and not stored.
    """
    _require_profile(profile)
    if not isinstance(claims, dict):
        raise LdapProfileError("claims_invalid")
    _reject_passwords(claims)
    issuer = claims.get("iss", claims.get("issuer"))
    subject = claims.get("sub", claims.get("subject"))
    if not isinstance(issuer, str) or not isinstance(subject, str):
        raise identity.IdentityDenied("login_denied")
    if profile.issuer and issuer != profile.issuer:
        raise identity.IdentityDenied("login_denied")
    link = _LINKS.get((issuer, subject))
    if link is None:
        raise identity.IdentityDenied("login_denied")
    return ClaimResult(link.user_id, (), (), LIFECYCLE_SOURCE)
