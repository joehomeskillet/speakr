"""Prepared Entra adapter with synthetic keys only (docs/10_Local_Entra.md section 8).

Backs the synthetic parts of T-082, T-085 and T-087 (service level), T-088
(policy part) and T-090 (claims grant nothing). No real tenant, no HTTP, no
route. Acceptance entries stay not_run: a real Entra login is unproven.
"""

import base64
import hashlib
import hmac
import json
import logging
import socket
import uuid
from datetime import datetime, timezone

import httpx
import pytest
import requests
from joserfc import jwt
from joserfc.jwk import RSAKey

from src.app import app, db
from src.clinic import entra, identity
from src.models import User

TENANT = "11111111-2222-3333-4444-555555555555"
OTHER_TENANT = "99999999-2222-3333-4444-555555555555"
CLIENT_ID = "cccccccc-0000-1111-2222-333333333333"
ISSUER = "https://login.microsoftonline.com/%s/v2.0" % TENANT
REDIRECT = "https://dictation.example.invalid/auth/entra/callback"
NOW = datetime(2026, 11, 17, 8, 0, 0, tzinfo=timezone.utc)
T = int(NOW.timestamp())
SECRET = "client-secret-value-9f3a"


def _b64(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _auth_dict(secret_file):
    return {
        "mode": "hybrid", "entra_enabled": True, "activation": {"real_provider_approved": True},
        "entra": {
            "tenant_id": TENANT, "client_id": CLIENT_ID, "secret_file": str(secret_file),
            "allowed_issuer": ISSUER, "discovery_url": ISSUER + "/.well-known/openid-configuration",
            "redirect_uri": REDIRECT, "scopes": ["openid", "profile", "email"], "pkce": True,
        },
    }


@pytest.fixture(scope="module")
def keypair():
    return RSAKey.generate_key(2048, {"kid": "k1", "use": "sig"})


@pytest.fixture
def jwks(keypair):
    return {"keys": [keypair.as_dict(private=False)]}


@pytest.fixture
def config(tmp_path):
    secret = tmp_path / "entra.secret"
    secret.write_text(SECRET + "\n", encoding="utf-8")
    return entra.EntraConfig.from_dict(_auth_dict(secret))


@pytest.fixture(autouse=True)
def ctx():
    with app.app_context():
        identity.create_tables()
        entra.create_tables()
        db.session.rollback()
        for model in (entra.ClinicExternalIdentity, identity.ClinicLocalRecovery,
                      identity.ClinicBootstrapMarker, identity.ClinicAccountPolicy):
            db.session.query(model).delete()
        db.session.commit()
        yield
        db.session.rollback()


def _user(status="active", login_policy="entra_only", admin=False, email=None):
    suffix = uuid.uuid4().hex[:10]
    user = User(username="en" + suffix, email=email or suffix + "@example.invalid", password=None)
    db.session.add(user)
    db.session.flush()
    db.session.add(identity.ClinicAccountPolicy(
        user_id=user.id, status=status, login_policy=login_policy, is_identity_admin=admin))
    db.session.commit()
    return user


def _claims(**over):
    claims = {"iss": ISSUER, "aud": CLIENT_ID, "tid": TENANT, "sub": "sub-abc", "oid": "aaaaaaaa-0000-0000-0000-000000000001",
              "exp": T + 600, "nbf": T - 5, "iat": T - 5, "nonce": "n0nce", "email": "x@example.invalid"}
    claims.update(over)
    return {k: v for k, v in claims.items() if v is not None}


def _token(keypair, claims=None, header=None):
    return jwt.encode({"alg": "RS256", "kid": "k1", **(header or {})}, claims or _claims(), keypair)


def _validate(config, token, jwks, nonce="n0nce"):
    return entra.validate_id_token(config, token, jwks, expected_nonce=nonce, now=NOW)


def _code(excinfo):
    return excinfo.value.code


# --- disabled: fail closed, no network ---

@pytest.fixture
def no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network access")

    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(requests.sessions.Session, "request", boom)
    monkeypatch.setattr(httpx.Client, "send", boom)
    monkeypatch.setattr(httpx.AsyncClient, "send", boom)


@pytest.mark.parametrize("data", [
    None, {}, {"mode": "local", "entra_enabled": True, "activation": {"real_provider_approved": True}},
    {"mode": "hybrid", "entra_enabled": False, "activation": {"real_provider_approved": True}},
    {"mode": "hybrid", "entra_enabled": True, "activation": {"real_provider_approved": False}},
])
def test_default_and_partial_config_is_disabled(data):
    assert entra.EntraConfig.from_dict(data).enabled is False
    assert entra.EntraConfig().enabled is False


def test_disabled_entry_points_fail_closed_without_network(no_network, keypair, jwks):
    cfg = entra.EntraConfig()
    pending = entra.PendingLogin("s" * 43, "n" * 43, "v" * 43, T)
    calls = [
        lambda: entra.begin_authorization(cfg),
        lambda: entra.check_redirect_uri(cfg, REDIRECT),
        lambda: entra.check_state(cfg, pending, "s" * 43),
        lambda: entra.token_request_params(cfg, pending, "code"),
        lambda: entra.validate_id_token(cfg, _token(keypair), jwks, expected_nonce="n0nce", now=NOW),
        lambda: entra.resolve_identity(cfg, _claims()),
        lambda: entra.complete_login(cfg, pending, returned_state="s" * 43, id_token="x", jwks=jwks),
        lambda: entra.create_link(cfg, 1, subject="sub", created_by=1),
    ]
    for call in calls:
        with pytest.raises(entra.EntraDisabled) as err:
            call()
        assert err.value.code == "entra_disabled"


@pytest.mark.parametrize("patch", [
    {"tenant_id": "common"}, {"tenant_id": "organizations"}, {"tenant_id": None},
    {"allowed_issuer": "https://login.microsoftonline.com/common/v2.0"},
    {"allowed_issuer": "https://evil.example.invalid/%s/v2.0" % TENANT},
    {"redirect_uri": "http://dictation.example.invalid/cb"}, {"client_id": None}, {"secret_file": None},
    {"scopes": ["openid", "offline_access"]}, {"scopes": ["profile"]}, {"pkce": False},
    {"discovery_url": "https://login.microsoftonline.com/common/v2.0/.well-known/openid-configuration"},
])
def test_enabled_config_must_be_tenant_bound_and_minimal(tmp_path, patch):
    data = _auth_dict(tmp_path / "s")
    data["entra"].update(patch)
    with pytest.raises(entra.EntraConfigError):
        entra.EntraConfig.from_dict(data)


def test_template_with_null_settings_fails_validation():
    data = {"mode": "hybrid", "entra_enabled": True, "activation": {"real_provider_approved": True},
            "entra": {"tenant_id": None, "client_id": None, "secret_file": None, "scopes": ["openid"]}}
    with pytest.raises(entra.EntraConfigError):
        entra.EntraConfig.from_dict(data)


# --- authorization code flow building blocks ---

def test_authorization_request_has_pkce_s256_state_nonce(config):
    from urllib.parse import parse_qs, urlsplit

    req = entra.begin_authorization(config, now=NOW)
    parts = urlsplit(req.url)
    query = {k: v[0] for k, v in parse_qs(parts.query).items()}
    assert parts.netloc == "login.microsoftonline.com" and parts.path == "/%s/oauth2/v2.0/authorize" % TENANT
    assert query["code_challenge_method"] == "S256" and query["response_type"] == "code"
    assert query["scope"] == "openid profile email" and query["redirect_uri"] == REDIRECT
    assert query["state"] == req.pending.state and query["nonce"] == req.pending.nonce
    expected = _b64(hashlib.sha256(req.pending.code_verifier.encode()).digest())
    assert query["code_challenge"] == expected
    other = entra.begin_authorization(config, now=NOW).pending
    assert other.state != req.pending.state and other.nonce != req.pending.nonce
    assert req.pending.code_verifier not in repr(req) and req.pending.state not in repr(req)


def test_pkce_verifier_mismatch(config):
    req = entra.begin_authorization(config, now=NOW)
    challenge = entra.pkce_challenge(req.pending.code_verifier)
    assert entra.verify_pkce(req.pending.code_verifier, challenge)
    assert not entra.verify_pkce(entra.begin_authorization(config).pending.code_verifier, challenge)
    assert not entra.verify_pkce("short", challenge)
    with pytest.raises(entra.EntraStateError):
        entra.pkce_challenge("too short")


def test_state_mismatch_and_expiry(config):
    pending = entra.begin_authorization(config, now=NOW).pending
    entra.check_state(config, pending, pending.state, now=NOW)
    with pytest.raises(entra.EntraStateError) as err:
        entra.check_state(config, pending, "forged", now=NOW)
    assert _code(err) == "state_mismatch"
    with pytest.raises(entra.EntraStateError) as err:
        entra.check_state(config, pending, None, now=NOW)
    assert _code(err) == "state_mismatch"
    with pytest.raises(entra.EntraStateError) as err:
        entra.check_state(config, pending, pending.state, now=T + entra.PENDING_TTL_SECONDS + 1)
    assert _code(err) == "state_expired"


def test_redirect_uri_is_exact(config):
    entra.check_redirect_uri(config, REDIRECT)
    for bad in (REDIRECT + "/", REDIRECT + "?x=1", REDIRECT.upper(), "https://evil.example.invalid/cb", None):
        with pytest.raises(entra.EntraStateError):
            entra.check_redirect_uri(config, bad)


def test_token_request_params_read_secret_lazily(config):
    pending = entra.begin_authorization(config, now=NOW).pending
    params = entra.token_request_params(config, pending, "the-code")
    assert params["client_secret"] == SECRET and params["code_verifier"] == pending.code_verifier
    assert params["redirect_uri"] == REDIRECT and params["grant_type"] == "authorization_code"
    assert SECRET not in repr(config)


# --- ID token validation ---

def test_valid_token_passes(config, keypair, jwks):
    claims = _validate(config, _token(keypair), jwks)
    assert claims["sub"] == "sub-abc"


@pytest.mark.parametrize("over,code", [
    ({"iss": "https://login.microsoftonline.com/common/v2.0"}, "issuer_mismatch"),
    ({"iss": "https://login.microsoftonline.com/%s/v2.0" % OTHER_TENANT}, "issuer_mismatch"),
    ({"tid": OTHER_TENANT}, "tenant_mismatch"),
    ({"tid": None}, "tenant_mismatch"),
    ({"aud": "someone-else"}, "audience_mismatch"),
    ({"aud": None}, "audience_mismatch"),
    ({"exp": T - 3600}, "token_expired"),
    ({"exp": None}, "exp_missing"),
    ({"nbf": T + 3600}, "token_not_yet_valid"),
    ({"nonce": "other"}, "nonce_mismatch"),
    ({"nonce": None}, "nonce_mismatch"),
    ({"sub": None}, "subject_missing"),
    ({"sub": "  "}, "subject_missing"),
])
def test_claim_rejections(config, keypair, jwks, over, code):
    with pytest.raises(entra.EntraTokenError) as err:
        _validate(config, _token(keypair, _claims(**over)), jwks)
    assert _code(err) == code


def test_skew_is_small_and_configurable(config, keypair, jwks):
    _validate(config, _token(keypair, _claims(exp=T - 30)), jwks)  # inside the default 60 s
    with pytest.raises(entra.EntraTokenError):
        _validate(config, _token(keypair, _claims(exp=T - 90)), jwks)
    _validate(config.with_skew(120), _token(keypair, _claims(exp=T - 90)), jwks)
    with pytest.raises(entra.EntraTokenError):
        _validate(config.with_skew(0), _token(keypair, _claims(exp=T - 5)), jwks)
    with pytest.raises(entra.EntraConfigError):
        config.with_skew(entra.MAX_SKEW_SECONDS + 1)


def test_audience_list_must_contain_client(config, keypair, jwks):
    _validate(config, _token(keypair, _claims(aud=[CLIENT_ID, "x"])), jwks)
    with pytest.raises(entra.EntraTokenError):
        _validate(config, _token(keypair, _claims(aud=["x", "y"])), jwks)


def test_alg_none_rejected(config, jwks):
    head = _b64(json.dumps({"alg": "none", "kid": "k1"}).encode())
    body = _b64(json.dumps(_claims()).encode())
    for token in (head + "." + body + ".", head + "." + body + "." + _b64(b"sig")):
        with pytest.raises(entra.EntraTokenError) as err:
            _validate(config, token, jwks)
        assert _code(err) == "alg_not_allowed"


def test_hs256_key_confusion_rejected(config, keypair, jwks):
    pem = keypair.as_pem(private=False)  # public key bytes used as HMAC secret
    head = _b64(json.dumps({"alg": "HS256", "kid": "k1", "typ": "JWT"}).encode())
    body = _b64(json.dumps(_claims()).encode())
    sig = _b64(hmac.new(pem, (head + "." + body).encode(), hashlib.sha256).digest())
    with pytest.raises(entra.EntraTokenError) as err:
        _validate(config, head + "." + body + "." + sig, jwks)
    assert _code(err) == "alg_not_allowed"


def test_unknown_kid_and_missing_kid(config, keypair, jwks):
    with pytest.raises(entra.EntraTokenError) as err:
        _validate(config, _token(keypair, header={"kid": "nope"}), jwks)
    assert _code(err) == "unknown_kid"
    head = _b64(json.dumps({"alg": "RS256"}).encode())
    with pytest.raises(entra.EntraTokenError):
        _validate(config, head + "." + _b64(b"{}") + "." + _b64(b"x"), jwks)
    with pytest.raises(entra.EntraTokenError):
        _validate(config, _token(keypair), {"keys": []})


def test_wrong_signature_and_garbage(config, keypair, jwks):
    attacker = RSAKey.generate_key(2048, {"kid": "k1"})
    with pytest.raises(entra.EntraTokenError) as err:
        _validate(config, _token(attacker), jwks)
    assert _code(err) == "signature_invalid"
    good = _token(keypair).split(".")
    tampered = good[0] + "." + _b64(json.dumps(_claims(sub="victim")).encode()) + "." + good[2]
    with pytest.raises(entra.EntraTokenError):
        _validate(config, tampered, jwks)
    for junk in ("", "a.b", "a.b.c", "....", None, "x" * (entra.MAX_TOKEN_CHARS + 1)):
        with pytest.raises(entra.EntraTokenError):
            _validate(config, junk, jwks)


def test_private_jwk_in_set_is_not_used(config, keypair):
    with pytest.raises(entra.EntraTokenError):
        _validate(config, _token(keypair), {"keys": [keypair.as_dict(private=True)]})


# --- identity resolution ---

def _link(config, user, admin, sub="sub-abc", oid=None):
    return entra.create_link(config, user.id, subject=sub, created_by=admin.id, object_id=oid)


def test_happy_path_with_prelinked_subject(config, keypair, jwks):
    admin, user = _user(admin=True, login_policy="local_only"), _user()
    _link(config, user, admin, oid="aaaaaaaa-0000-0000-0000-000000000001")
    req = entra.begin_authorization(config, now=NOW)
    token = _token(keypair, _claims(nonce=req.pending.nonce))
    result = entra.complete_login(config, req.pending, returned_state=req.pending.state, id_token=token,
                                  jwks=jwks, redirect_uri=REDIRECT, now=NOW)
    assert result.user_id == user.id and result.security_version == 0 and result.issuer == ISSUER


def test_email_match_without_link_is_refused(config, keypair, jwks):
    local = _user(login_policy="local_and_entra", email="doc@example.invalid")
    claims = _validate(config, _token(keypair, _claims(sub="new-sub", email="doc@example.invalid",
                                                       preferred_username="doc@example.invalid")), jwks)
    users_before = User.query.count()
    with pytest.raises(entra.EntraLoginDenied):
        entra.resolve_identity(config, claims)
    assert User.query.count() == users_before  # no provisioning
    assert entra.ClinicExternalIdentity.query.count() == 0  # no auto-link
    assert local.id  # existing user untouched


def test_link_is_keyed_by_issuer_tenant_subject_not_email(config, keypair, jwks):
    admin, user = _user(admin=True, login_policy="local_only"), _user(email="a@example.invalid")
    _link(config, user, admin, sub="sub-A")
    claims = _validate(config, _token(keypair, _claims(sub="sub-B", email="a@example.invalid")), jwks)
    with pytest.raises(entra.EntraLoginDenied):
        entra.resolve_identity(config, claims)
    foreign = dict(claims, sub="sub-A", iss="https://login.microsoftonline.com/%s/v2.0" % OTHER_TENANT)
    with pytest.raises(entra.EntraLoginDenied):
        entra.resolve_identity(config, foreign)
    mismatch_tid = dict(claims, sub="sub-A", tid=OTHER_TENANT)
    with pytest.raises(entra.EntraLoginDenied):
        entra.resolve_identity(config, mismatch_tid)


def test_object_id_is_checked_when_link_has_one(config, keypair, jwks):
    admin, user = _user(admin=True, login_policy="local_only"), _user()
    _link(config, user, admin, oid="aaaaaaaa-0000-0000-0000-000000000001")
    good = _validate(config, _token(keypair), jwks)
    entra.resolve_identity(config, good)
    with pytest.raises(entra.EntraLoginDenied):
        entra.resolve_identity(config, dict(good, oid="bbbbbbbb-0000-0000-0000-000000000002"))
    with pytest.raises(entra.EntraLoginDenied):
        entra.resolve_identity(config, {k: v for k, v in good.items() if k != "oid"})


def test_revoked_link_is_refused_and_sessions_end(config, keypair, jwks):
    admin, user = _user(admin=True, login_policy="local_only"), _user()
    row = _link(config, user, admin)
    claims = _validate(config, _token(keypair), jwks)
    entra.resolve_identity(config, claims)
    entra.revoke_link(row.id, revoked_by=admin.id)
    with pytest.raises(entra.EntraLoginDenied):
        entra.resolve_identity(config, claims)
    assert identity.security_version_of(user.id) == 1
    kept = db.session.get(entra.ClinicExternalIdentity, row.id)
    assert kept.status == "revoked" and kept.revoked_by == admin.id  # history kept
    with pytest.raises(entra.LinkError):
        entra.revoke_link(row.id, revoked_by=admin.id)


def test_revoke_works_while_provider_is_disabled(config):
    admin, user = _user(admin=True, login_policy="local_only"), _user()
    row = _link(config, user, admin)
    assert entra.revoke_link(row.id, revoked_by=admin.id).status == "revoked"


@pytest.mark.parametrize("policy", ["local_only", "device_only"])
def test_policy_forbidding_entra_is_refused(config, keypair, jwks, policy):
    admin, user = _user(admin=True, login_policy="local_only"), _user(login_policy=policy)
    _link(config, user, admin)
    with pytest.raises(entra.EntraLoginDenied):
        entra.resolve_identity(config, _validate(config, _token(keypair), jwks))


def test_dual_policy_allowed_and_disabled_or_pending_user_refused(config, keypair, jwks):
    admin, user = _user(admin=True, login_policy="local_only"), _user(login_policy="local_and_entra")
    _link(config, user, admin)
    claims = _validate(config, _token(keypair), jwks)
    assert entra.resolve_identity(config, claims).user_id == user.id
    for status in ("disabled", "pending_activation"):
        identity.ClinicAccountPolicy.query.filter_by(user_id=user.id).update({"status": status})
        db.session.commit()
        with pytest.raises(entra.EntraLoginDenied):
            entra.resolve_identity(config, claims)


def test_claims_grant_no_rights_or_admin(config, keypair, jwks):
    admin, user = _user(admin=True, login_policy="local_only"), _user()
    _link(config, user, admin)
    claims = _validate(config, _token(keypair, _claims(roles=["Admin"], groups=["g"], wids=["x"])), jwks)
    result = entra.resolve_identity(config, claims)
    assert result.user_id == user.id
    policy = db.session.get(identity.ClinicAccountPolicy, user.id)
    assert policy.is_identity_admin is False and db.session.get(User, user.id).is_admin is not True


# --- link administration ---

def test_only_identity_admin_can_link_and_conflicts_fail(config):
    admin = _user(admin=True, login_policy="local_only")
    plain, other = _user(), _user()
    with pytest.raises(entra.LinkError) as err:
        entra.create_link(config, other.id, subject="s", created_by=plain.id)
    assert _code(err) == "not_authorized"
    row = _link(config, other, admin, sub="same", oid="aaaaaaaa-0000-0000-0000-000000000001")
    assert row.issuer == ISSUER and row.tenant_id == TENANT and row.created_by == admin.id
    with pytest.raises(entra.LinkError):  # same identity, second user
        _link(config, plain, admin, sub="same")
    with pytest.raises(entra.LinkError):  # same tenant/object, other subject
        _link(config, plain, admin, sub="different", oid="aaaaaaaa-0000-0000-0000-000000000001")
    with pytest.raises(entra.LinkError):  # second identity for the same user
        _link(config, other, admin, sub="again")
    with pytest.raises(entra.LinkError):  # invalid input
        _link(config, plain, admin, sub="", oid=None)
    with pytest.raises(entra.LinkError):
        _link(config, plain, admin, sub="x", oid="not-a-guid")
    entra.revoke_link(row.id, revoked_by=admin.id)
    assert _link(config, plain, admin, sub="same").user_id == plain.id  # separate act after revoke


def test_disabled_admin_cannot_link(config):
    admin = _user(admin=True, login_policy="local_only")
    user = _user()
    identity.ClinicAccountPolicy.query.filter_by(user_id=admin.id).update({"status": "disabled"})
    db.session.commit()
    with pytest.raises(entra.LinkError):
        _link(config, user, admin)


# --- no secrets in logs or messages ---

def test_no_secret_in_logs_or_error_messages(config, keypair, jwks, caplog):
    caplog.set_level(logging.DEBUG)
    admin, user = _user(admin=True, login_policy="local_only"), _user()
    _link(config, user, admin, sub="sub-abc")
    req = entra.begin_authorization(config, now=NOW)
    pending = req.pending
    good = _token(keypair, _claims(nonce=pending.nonce))
    bad = _token(keypair, _claims(nonce="wrong"))
    entra.complete_login(config, pending, returned_state=pending.state, id_token=good, jwks=jwks, now=NOW)
    params = entra.token_request_params(config, pending, "authz-code-xyz")
    messages = []
    for call in (
        lambda: entra.complete_login(config, pending, returned_state=pending.state, id_token=bad, jwks=jwks, now=NOW),
        lambda: entra.complete_login(config, pending, returned_state="forged-state", id_token=good, jwks=jwks, now=NOW),
        lambda: entra.resolve_identity(config, _claims(sub="unknown")),
        lambda: entra.token_request_params(config, pending, ""),
    ):
        with pytest.raises(entra.EntraError) as err:
            call()
        messages += [str(err.value), repr(err.value)]
    haystack = caplog.text + "\n".join(messages) + repr(config) + repr(req) + repr(pending)
    for secret in (SECRET, good, bad, "authz-code-xyz", pending.state, pending.nonce, pending.code_verifier,
                   params["client_secret"], "x@example.invalid"):
        assert secret not in haystack
