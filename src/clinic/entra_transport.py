"""Server-side HTTP transport for the prepared Entra OIDC adapter."""

import json
from datetime import datetime
from typing import Any

import requests

from src.clinic import entra

_TIMEOUT = (3.05, 5.0)  # connect, read
_MAX_RESPONSE_BYTES = 128 * 1024


class _ProviderSession(requests.Session):
    def get_redirect_target(self, response: requests.Response) -> None:
        # Requests otherwise prepares the next hop and reads the redirect body
        # even with allow_redirects=False. No redirect is valid here.
        return None


def _provider_json(
    session: requests.Session, method: str, url: str, *, data: dict[str, str] | None = None,
) -> dict[str, Any]:
    try:
        with session.request(
            method, url, data=data, timeout=_TIMEOUT, verify=True,
            allow_redirects=False, stream=True,
            headers={"Accept": "application/json", "Accept-Encoding": "identity"},
        ) as response:
            if response.status_code != 200:
                raise entra.EntraTokenError("provider_http_error")
            if response.headers.get("Content-Encoding", "identity").lower() != "identity":
                raise entra.EntraTokenError("provider_response_invalid")
            body = bytearray()
            for chunk in response.iter_content(chunk_size=8192):
                body.extend(chunk)
                if len(body) > _MAX_RESPONSE_BYTES:
                    raise entra.EntraTokenError("provider_response_too_large")
    except requests.RequestException:
        raise entra.EntraTokenError("provider_unreachable") from None
    try:
        value = json.loads(body)
    except (ValueError, RecursionError):
        raise entra.EntraTokenError("provider_response_invalid") from None
    if not isinstance(value, dict):
        raise entra.EntraTokenError("provider_response_invalid")
    return value


class EntraTransport:
    """Use a trusted server profile; callers keep and consume pending logins server-side."""

    def __init__(self, profile: dict[str, Any] | None = None) -> None:
        self._config = entra.EntraConfig.from_dict(profile)

    @property
    def config(self) -> entra.EntraConfig:
        """Validated settings for the existing authorization and account-link APIs."""
        return self._config

    def exchange_code(
        self, pending: entra.PendingLogin, *, code: str, returned_state: str,
        redirect_uri: str, now: datetime | int | None = None,
    ) -> dict[str, Any]:
        """Return verified ID claims only; use entra.resolve_identity for login."""
        config = self._config
        try:
            entra.check_state(config, pending, returned_state, now=now)
            entra.check_redirect_uri(config, redirect_uri)
        except TypeError:  # compare_digest cannot compare non-ASCII strings
            raise entra.EntraStateError("callback_invalid") from None
        params = entra.token_request_params(config, pending, code)
        jwks_url = f"{config.issuer_authority}/discovery/v2.0/keys"

        with _ProviderSession() as session:
            session.trust_env = False  # no environment proxies, CA overrides or netrc auth
            discovery = _provider_json(session, "GET", config.discovery_url)
            if any(discovery.get(name) != endpoint for name, endpoint in (
                ("issuer", config.allowed_issuer),
                ("authorization_endpoint", config.authorization_endpoint),
                ("token_endpoint", config.token_endpoint),
                ("jwks_uri", jwks_url),
            )):
                raise entra.EntraConfigError("discovery_endpoints_mismatch")

            token = _provider_json(session, "POST", config.token_endpoint, data=params).get("id_token")
            if not isinstance(token, str) or not 0 < len(token) <= entra.MAX_TOKEN_CHARS:
                raise entra.EntraTokenError("token_malformed")
            # No code replay, and at most one key refresh per exchange.
            for attempt in range(2):
                jwks = _provider_json(session, "GET", jwks_url)
                if not isinstance(jwks.get("keys"), list):
                    raise entra.EntraTokenError("provider_response_invalid")
                try:
                    return entra.validate_id_token(config, token, jwks, expected_nonce=pending.nonce, now=now)
                except entra.EntraTokenError as error:
                    if error.code != "unknown_kid" or attempt:
                        raise
                except TypeError:  # malformed claim types or non-ASCII nonce
                    raise entra.EntraTokenError("token_malformed") from None
        raise AssertionError("unreachable: validation returns or rejects on the second attempt")
