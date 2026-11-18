"""Disabled Windows SSO contract; resolves a binding without creating a session."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from time import time

from src.clinic.config import clinic_mode_enabled
from src.clinic.identity import AuthenticationFailed, ClinicAccountPolicy, LOCAL_POLICIES
from src.database import db

ASSERTION_ENV_KEY = "clinic.iwa.assertion"
_ASSERTION_TTL = 60
_AD_SID = re.compile(r"S-1-5-21(?:-(0|[1-9][0-9]{0,9})){4}")
_ASSERTION_ID = re.compile(r"[A-Za-z0-9_-]{16,128}")


@dataclass(frozen=True)
class IwaConfig:
    """Server-owned settings; advance version when provisioning or trust changes."""

    enabled: bool = False
    ingress_id: str = ""
    version: int = 1
    subjects: Mapping[str, int] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class VerifiedAssertion:
    """Produced only by server middleware after authenticating the peer and AD user.

    The middleware must reject multiple principals and must never populate the
    private WSGI key from HTTP headers, REMOTE_USER, or other browser input.
    """

    ingress_id: str
    version: int
    sid: str = field(repr=False)
    assertion_id: str = field(repr=False)
    issued_at: int


@dataclass(frozen=True)
class IwaBinding:
    user_id: int
    security_version: int


def resolve_identity(
    config: IwaConfig,
    environ: Mapping[str, object],
    *,
    claim_once: Callable[[str, str, int], bool],
) -> IwaBinding:
    """Resolve a verified SID by exact provisioning; never start a login session.

    claim_once(ingress_id, assertion_id, expires_at) must atomically reserve the
    IWA key across workers through expiry. It returns True only on first use
    before expiry; store unavailability is a denial. There is no local fallback.
    """
    if not clinic_mode_enabled() or config.enabled is not True or not config.ingress_id:
        raise AuthenticationFailed()
    if any(key.startswith("HTTP_X_IWA_") for key in environ) or (
        "HTTP_X_REMOTE_USER" in environ or "HTTP_X_FORWARDED_USER" in environ
    ):
        raise AuthenticationFailed()
    assertion = environ.get(ASSERTION_ENV_KEY)
    if (
        not isinstance(assertion, VerifiedAssertion) or assertion.ingress_id != config.ingress_id
        or type(assertion.version) is not int or assertion.version != config.version
        or not isinstance(assertion.sid, str) or not _AD_SID.fullmatch(assertion.sid)
        or any(int(part) >= 2**32 for part in assertion.sid.split("-")[4:])
        or not isinstance(assertion.assertion_id, str) or not _ASSERTION_ID.fullmatch(assertion.assertion_id)
        or type(assertion.issued_at) is not int
        or not 0 <= int(time()) - assertion.issued_at < _ASSERTION_TTL
    ):
        raise AuthenticationFailed()
    user_id = config.subjects.get(assertion.sid)
    if type(user_id) is not int or user_id <= 0:
        raise AuthenticationFailed()
    policy = db.session.get(ClinicAccountPolicy, user_id, populate_existing=True)
    if policy is None or policy.status != "active" or policy.login_policy not in LOCAL_POLICIES:
        raise AuthenticationFailed()
    try:
        claimed = claim_once(config.ingress_id, assertion.assertion_id, assertion.issued_at + _ASSERTION_TTL)
    except OSError:
        raise AuthenticationFailed() from None
    if claimed is not True:
        raise AuthenticationFailed()
    return IwaBinding(user_id, policy.security_version)
