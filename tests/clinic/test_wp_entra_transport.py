"""Synthetic T-084 transport evidence; no live provider or browser session."""

import io
import json
from collections import deque
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

from src.app import app
from src.clinic import entra
from src.clinic.entra_transport import EntraTransport

from test_entra_adapter import (
    CLIENT_ID, ISSUER, NOW, REDIRECT, SECRET, T, _auth_dict, _claims, _link, _token, _user,
    keypair as keypair, jwks as jwks,
)

DISCOVERY = f"{ISSUER}/.well-known/openid-configuration"
AUTHORITY = ISSUER.removesuffix("/v2.0")
TOKEN = f"{AUTHORITY}/oauth2/v2.0/token"
KEYS = f"{AUTHORITY}/discovery/v2.0/keys"
CODE = "synthetic-callback-code"
ACCESS = "synthetic-unused-access-token"
METADATA = {
    "issuer": ISSUER, "authorization_endpoint": f"{AUTHORITY}/oauth2/v2.0/authorize",
    "token_endpoint": TOKEN, "jwks_uri": KEYS,
}


@pytest.fixture
def transport(tmp_path):
    secret = tmp_path / "synthetic-client.secret"
    secret.write_text(SECRET, encoding="utf-8")
    return EntraTransport(_auth_dict(secret))


@pytest.fixture
def provider(monkeypatch, transport):
    replies = deque()
    calls = []
    authorization = entra.begin_authorization(transport.config, now=NOW)

    def send(self, request, *, timeout, verify, proxies, stream, **kwargs):
        calls.append((request.method, request.url))
        assert (request.method, request.url) in {("GET", DISCOVERY), ("GET", KEYS), ("POST", TOKEN)}
        assert verify is True and stream and not proxies
        assert all(0 < seconds <= 5 for seconds in timeout)
        assert "Cookie" not in request.headers
        if request.method == "POST":
            assert parse_qs(request.body) == {
                "client_id": [CLIENT_ID], "client_secret": [SECRET], "code": [CODE],
                "code_verifier": [authorization.pending.code_verifier], "redirect_uri": [REDIRECT],
                "grant_type": ["authorization_code"],
            }
        reply = replies.popleft()
        if isinstance(reply, Exception):
            raise reply
        response = requests.Response()
        response.status_code, body, headers = reply
        response.raw = io.BytesIO(body)
        response.headers.update(headers)
        return response

    def enqueue(payload, *, status=200, **headers):
        replies.append((status, payload if isinstance(payload, bytes) else json.dumps(payload).encode(), headers))

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", send)
    monkeypatch.setenv("HTTPS_PROXY", "https://unexpected-proxy.example.invalid")
    return authorization, calls, enqueue, replies


@pytest.fixture
def exchange(transport, provider):
    pending = provider[0].pending

    def callback(**overrides):
        return transport.exchange_code(pending, **{
            "code": CODE, "returned_state": pending.state, "redirect_uri": REDIRECT, "now": NOW, **overrides,
        })

    return callback


@pytest.mark.parametrize("case", ["linked", "rotated", "unknown_key", "wrong_nonce", "unlinked"])
def test_exchange_egress_and_identity(transport, exchange, provider, keypair, jwks, caplog, case):
    authorization, calls, reply, _ = provider
    pending = authorization.pending
    caplog.set_level("DEBUG")
    query = parse_qs(urlsplit(authorization.url).query)
    assert query["scope"] == ["openid profile email"] and query["code_challenge_method"] == ["S256"]
    assert entra.verify_pkce(pending.code_verifier, query["code_challenge"][0])
    nonce = "wrong" if case == "wrong_nonce" else pending.nonce
    token = _token(keypair, _claims(nonce=nonce))
    reply(METADATA)
    reply({"id_token": token, "access_token": ACCESS})
    if case in {"rotated", "unknown_key"}:
        reply({"keys": []})
    reply({"keys": []} if case == "unknown_key" else jwks)

    expected = {"unknown_key": "unknown_kid", "wrong_nonce": "nonce_mismatch", "unlinked": "login_denied"}.get(case)
    with app.app_context():
        if expected:
            with pytest.raises(entra.EntraError, match=f"^{expected}$"):
                entra.resolve_identity(transport.config, exchange())
        else:
            user = _user()
            _link(transport.config, user, _user(admin=True, login_policy="local_only"))
            login = entra.resolve_identity(transport.config, exchange())
            assert (login.user_id, login.security_version) == (user.id, 0)
    assert calls == [("GET", DISCOVERY), ("POST", TOKEN)] + [("GET", KEYS)] * (
        2 if case in {"rotated", "unknown_key"} else 1
    )
    assert all(value not in caplog.text for value in (
        token, ACCESS, CODE, SECRET, pending.code_verifier, pending.nonce, pending.state,
    ))


def test_disabled_and_invalid_callback_never_contact_provider(exchange, provider):
    authorization, calls, _, _ = provider
    pending = authorization.pending
    with pytest.raises(entra.EntraDisabled):
        EntraTransport().exchange_code(pending, code=CODE, returned_state=pending.state, redirect_uri=REDIRECT)
    for overrides, error in (
        ({"returned_state": "unsolicited"}, "state_mismatch"),
        ({"now": T + entra.PENDING_TTL_SECONDS + 1}, "state_expired"),
        ({"redirect_uri": "https://unexpected.example.invalid/callback"}, "redirect_uri_mismatch"),
    ):
        with pytest.raises(entra.EntraStateError, match=f"^{error}$"):
            exchange(**overrides)
    assert calls == []


@pytest.mark.parametrize("field", tuple(METADATA))
def test_discovery_rejects_other_tenant_on_same_host(transport, exchange, provider, field):
    _, calls, reply, _ = provider
    reply({**METADATA, field: METADATA[field].replace(transport.config.tenant_id, "common")})
    with pytest.raises(entra.EntraConfigError, match="^discovery_endpoints_mismatch$"):
        exchange()
    assert calls == [("GET", DISCOVERY)]


@pytest.mark.parametrize("case", ["redirect", "timeout", "invalid_json", "oversize", "unauthorized"])
def test_token_failure_is_not_retried_or_logged(exchange, provider, caplog, case):
    _, calls, reply, replies = provider
    caplog.set_level("DEBUG")
    reply(METADATA)
    error = "provider_http_error"
    if case == "redirect":
        reply({"error": ACCESS}, status=307, Location=f"https://unexpected.example.invalid/{ACCESS}")
    elif case == "timeout":
        replies.append(requests.Timeout(ACCESS))
        error = "provider_unreachable"
    elif case == "invalid_json":
        reply(ACCESS.encode())
        error = "provider_response_invalid"
    elif case == "oversize":
        reply(ACCESS.encode() * 10000)
        error = "provider_response_too_large"
    else:
        reply({"error_description": ACCESS}, status=401)
    with pytest.raises(entra.EntraTokenError, match=f"^{error}$") as caught:
        exchange()
    assert calls == [("GET", DISCOVERY), ("POST", TOKEN)]
    assert ACCESS not in caplog.text + repr(caught.value)
