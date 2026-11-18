"""Server policy for managed browser persistence, independent of online recording."""

from __future__ import annotations

from dataclasses import dataclass

from src.clinic.config import clinic_mode_enabled, dev_defaults

MANAGED_OFFLINE_PROFILE = 'managed_web_offline'
USER_UNLOCK_KEY_POLICY = 'encrypted_user_unlock'


@dataclass(frozen=True)
class VerifiedBrowser:
    """Authenticated account and profile verified by trusted ingress.

    Use the current authenticated User.get_id() (which includes the account's
    session version when set) for account_version. A default profile, header,
    or request-body field is not verification.
    """

    account_version: str
    browser_id: str
    profile: str


@dataclass(frozen=True)
class OfflineVersion:
    browser: VerifiedBrowser
    version: int


@dataclass(frozen=True)
class OfflineAuthorization:
    """An explicit server approval; key_policy is a label, never key material."""

    reference: OfflineVersion
    quota_bytes: int
    key_policy: str


@dataclass(frozen=True)
class OfflineCapability:
    authorization: OfflineAuthorization | None = None

    @property
    def available(self) -> bool:
        return self.authorization is not None

    @property
    def message(self) -> str | None:
        if not self.available:
            return 'Offline-Aufnahme nicht verfügbar. Bereits lokal gespeicherte Daten können verbleiben.'
        return None


def offline_capability(
    browser: VerifiedBrowser | None,
    current: OfflineAuthorization | None,
    *,
    previous: OfflineVersion | None,
) -> OfflineCapability:
    """Decide against fresh server facts; absent proof or approval denies access.

    The caller supplies the current grant from server policy on every check,
    never from the client or a previously returned capability. Revocation
    removes the grant; a replacement or policy change must use a new version.
    Pass previous=None only for discovery. On reconnect require the draft's
    bound version for comparison; that reference is not a credential.
    """
    if not clinic_mode_enabled() or browser is None or current is None:
        return OfflineCapability()
    if (browser.profile != MANAGED_OFFLINE_PROFILE or not browser.browser_id.strip()
            or not browser.account_version.strip() or current.reference.browser != browser):
        return OfflineCapability()
    if (type(current.reference.version) is not int or current.reference.version <= 0
            or type(current.quota_bytes) is not int or current.quota_bytes <= 0
            or current.key_policy != USER_UNLOCK_KEY_POLICY):
        return OfflineCapability()
    if previous is not None and (type(previous.version) is not int or previous != current.reference):
        return OfflineCapability()
    web_policy = dev_defaults().get('web_client')
    if not isinstance(web_policy, dict) or web_policy.get('managed_offline_enabled') is not True:
        return OfflineCapability()
    return OfflineCapability(current)
