"""Identity provider endpoints (w113): disabled inert, explicit linking, forged callbacks.

Synthetic users, a synthetic OIDC signer and a synthetic IWA assertion only.
These cases are mock evidence for T-083, T-085 and T-095, not proof of real Entra.
"""

import secrets
from time import time
from urllib.parse import parse_qs, urlsplit

import pytest
from flask import Flask, jsonify
from flask_login import LoginManager, current_user, login_user
from flask_wtf.csrf import CSRFProtect, generate_csrf
from joserfc import jwt
from joserfc.jwk import RSAKey

from src.app import app as main_app, db
from src.clinic import entra, entra_link_intent as intents, identity, iwa_identity
from src.clinic.routes_identity_providers import IdentityProvidersApi
from src.models import AuditEvent, User

TENANT = "11111111-2222-3333-4444-555555555555"
OID, OTHER_OID = "aaaaaaaa-0000-0000-0000-000000000001", "aaaaaaaa-0000-0000-0000-000000000002"
PASSWORD = "synthetic provider phrase 42"
BASE = "/api/clinic/v1/identity/providers"
SID = "S-1-5-21-1-2-3-1001"


@pytest.fixture(scope="module")
def password_hash():
    return identity.hash_password(PASSWORD)


@pytest.fixture(scope="module")
def key():
    return RSAKey.generate_key(2048, {"kid": "test-key"})


@pytest.fixture
def config():
    return entra.EntraConfig(True, TENANT, "synthetic-client",
                             allowed_issuer=f"https://login.microsoftonline.com/{TENANT}/v2.0",
                             redirect_uri="https://clinic.example.invalid/callback")


class Idp:
    """Synthetic provider; the exchange of a disabled setup must never run."""

    def __init__(self, config, key):
        self.config, self.key, self.signer = config, key, key
        self.sub, self.oid, self.overrides, self.calls = "synthetic-sub", OID, {}, 0

    def __call__(self, pending, code):
        self.calls += 1
        claims = dict(iss=self.config.allowed_issuer, aud=self.config.client_id, tid=self.config.tenant_id,
                      sub=self.sub, oid=self.oid, nonce=pending.nonce, exp=int(time()) + 120)
        claims.update(self.overrides)
        token = jwt.encode({"alg": "RS256", "kid": "test-key"}, claims, self.signer)
        return token, {"keys": [self.key.as_dict(private=False)]}


def make_app(clinic_mode, config, exchange, **options):
    monkey_env = clinic_mode
    monkey_env.setenv("CLINIC_MODE", "1")
    app = Flask("idp-api-test")
    app.config.update(TESTING=True, SECRET_KEY="synthetic-test-key",
                      SQLALCHEMY_DATABASE_URI=main_app.config["SQLALCHEMY_DATABASE_URI"])
    db.init_app(app)
    CSRFProtect(app)

    def load(uid):
        base, _, version = uid.partition(":")
        user = db.session.get(User, int(base))
        return user if user and (user.session_version or 0) == int(version or 0) else None

    LoginManager(app).user_loader(load)

    @app.get("/test-session/<int:uid>")
    def browser_session(uid):
        login_user(db.session.get(User, uid))
        return jsonify(csrf=generate_csrf())

    @app.get("/test-csrf")
    def anonymous_csrf():
        return jsonify(csrf=generate_csrf())

    @app.get("/test-whoami")
    def whoami():
        return jsonify(user_id=current_user.id if current_user.is_authenticated else None)

    app.register_blueprint(IdentityProvidersApi(config, exchange, **options).blueprint)
    return app


@pytest.fixture
def world(monkeypatch, password_hash):
    with main_app.app_context():
        identity.create_tables()
        entra.create_tables()
        intents.create_tables()
    monkeypatch.setenv("CLINIC_MODE", "1")
    return monkeypatch, password_hash


def mk_user(password_hash, *, admin=False, ident=False, policy="local_only", status="active"):
    with main_app.app_context():
        tag = secrets.token_hex(5)
        row = User(username=f"u{tag}", email=f"{tag}@example.invalid", password=password_hash, is_admin=admin)
        db.session.add(row)
        db.session.flush()
        db.session.add(identity.ClinicAccountPolicy(user_id=row.id, status=status, login_policy=policy,
                                                    is_identity_admin=ident))
        db.session.commit()
        return row.id, row.email


def login(app, uid):
    client = app.test_client()
    return client, {"X-CSRF-Token": client.get(f"/test-session/{uid}").json["csrf"]}


def anonymous(app):
    client = app.test_client()
    return client, {"X-CSRF-Token": client.get("/test-csrf").json["csrf"]}


def state_of(response):
    return parse_qs(urlsplit(response.json["authorization_url"]).query)["state"][0]


def counts():
    with main_app.app_context():
        return (User.query.count(), entra.ClinicExternalIdentity.query.count(),
                intents.ClinicIdentityLinkRequest.query.count())


def link_through_api(app, admin_id, user_id, idp, *, oid=OID):
    admin, admin_headers = login(app, admin_id)
    with main_app.app_context():
        version = identity._policy(user_id).version
    created = admin.post("/api/clinic/v1/identity/users/%d/entra-link-requests" % user_id, headers=admin_headers,
                         json={"object_id": oid, "expected_version": version, "password": PASSWORD})
    assert created.status_code == 201
    client, headers = login(app, user_id)
    started = client.post(BASE + "/entra/link/start", headers=headers,
                          json={"request_id": created.json["request_id"], "password": PASSWORD})
    assert started.status_code == 200
    return client, state_of(started)


def test_t085_explicit_link_binds_only_the_approved_identity(world, config, key):
    monkeypatch, password_hash = world
    idp = Idp(config, key)
    app = make_app(monkeypatch, config, idp)
    admin, _ = mk_user(password_hash, admin=True, ident=True)
    user, email = mk_user(password_hash)
    users_before = counts()[0]

    client, state = link_through_api(app, admin, user, idp)
    idp.oid = OTHER_OID  # unmatched Entra account: refused, intent stays usable
    refused = client.get(BASE + "/entra/callback", query_string={"state": state, "code": "c"})
    assert (refused.status_code, refused.json["code"]) == (403, "FORBIDDEN")
    assert counts()[1] == 0
    idp.oid = OID
    assert client.get(BASE + "/entra/callback", query_string={"state": state, "code": "c"}).json == {"linked": True}
    replay = client.get(BASE + "/entra/callback", query_string={"state": state, "code": "c"})
    assert replay.status_code in (401, 403)
    with main_app.app_context():
        link = entra.ClinicExternalIdentity.query.filter_by(user_id=user).one()
        policy = identity._policy(user)
        assert (link.subject, link.object_id, policy.login_policy) == ("synthetic-sub", OID, "entra_only")
        assert db.session.get(User, user).password is None
        assert AuditEvent.query.filter_by(event="auth.sso.linked", target_user_id=user).count() == 1

    # Same e-mail, no intent: an unknown subject never logs in and creates no account.
    idp.sub, idp.overrides = "other-sub", {"email": email}
    stranger, headers = anonymous(app)
    begun = stranger.post(BASE + "/entra/login", headers=headers)
    denied = stranger.get(BASE + "/entra/callback", query_string={"state": state_of(begun), "code": "c"})
    assert denied.status_code == 403 and stranger.get("/test-whoami").json == {"user_id": None}
    assert counts()[0] == users_before

    # The linked subject signs in as the same internal user.id.
    idp.sub, idp.overrides = "synthetic-sub", {}
    begun = stranger.post(BASE + "/entra/login", headers=headers)
    assert stranger.get(BASE + "/entra/callback", query_string={"state": state_of(begun), "code": "c"}).json == {
        "signed_in": True}
    assert stranger.get("/test-whoami").json == {"user_id": user}


@pytest.mark.parametrize("forgery", [
    {"iss": "https://login.microsoftonline.com/other/v2.0"}, {"aud": "another-client"},
    {"tid": "99999999-2222-3333-4444-555555555555"}, {"nonce": "forged-nonce"}, {"exp": 1}, "signature",
    "state", "code"])
def test_t083_forged_callback_is_blocked_then_valid_one_works_once(world, config, key, forgery):
    monkeypatch, password_hash = world
    idp = Idp(config, key)
    app = make_app(monkeypatch, config, idp)
    user, _ = mk_user(password_hash, policy="local_and_entra")
    with main_app.app_context():
        db.session.add(entra.ClinicExternalIdentity(user_id=user, issuer=config.allowed_issuer, tenant_id=TENANT,
                                                    subject="synthetic-sub", object_id=OID, created_by=user))
        db.session.commit()
    client, headers = anonymous(app)
    state = state_of(client.post(BASE + "/entra/login", headers=headers))
    query = {"state": "forged-state" if forgery == "state" else state, "code": "" if forgery == "code" else "c"}
    if forgery == "signature":
        idp.signer = RSAKey.generate_key(2048, {"kid": "test-key"})
    elif isinstance(forgery, dict):
        idp.overrides = forgery
    blocked = client.get(BASE + "/entra/callback", query_string=query)
    assert blocked.status_code == 403 and client.get("/test-whoami").json == {"user_id": None}
    idp.signer, idp.overrides = key, {}
    valid = {"state": state, "code": "c"}
    assert client.get(BASE + "/entra/callback", query_string=valid).json == {"signed_in": True}
    anonymous_again, _ = anonymous(app)  # a replayed code with the used state is dead
    assert anonymous_again.get(BASE + "/entra/callback", query_string=valid).status_code == 403


def test_disabled_providers_are_inert_and_never_touch_the_network(world, key):
    env, password_hash = world

    def never(*_args, **_kwargs):
        raise AssertionError("outbound call from a disabled provider")

    class Never:
        def __call__(self, *_args):
            never()

    import requests
    env.setattr(requests.Session, "request", never)
    env.setattr(entra, "begin_authorization", never)
    app = make_app(env, entra.EntraConfig(), Never())
    admin, _ = mk_user(password_hash, admin=True, ident=True)
    user, _ = mk_user(password_hash)
    before = counts()
    client, headers = anonymous(app)
    assert client.get(BASE).json["providers"] == [{"provider": "entra", "enabled": False},
                                                  {"provider": "iwa", "enabled": False},
                                                  {"provider": "ldap", "enabled": False}]
    admin_client, admin_headers = login(app, admin)
    calls = [(client.post(BASE + "/entra/login", headers=headers)),
             client.get(BASE + "/entra/callback", query_string={"state": "x", "code": "y"}),
             client.post(BASE + "/iwa/login", headers=headers),
             admin_client.post(f"/api/clinic/v1/identity/users/{user}/entra-link-requests", headers=admin_headers,
                               json={"object_id": OID, "expected_version": 1, "password": PASSWORD}),
             admin_client.post(BASE + "/entra/link/start", headers=admin_headers,
                               json={"request_id": 1, "password": PASSWORD})]
    assert [(r.status_code, r.json["code"]) for r in calls] == [(422, "CAPABILITY_UNAVAILABLE")] * len(calls)
    assert counts() == before


def test_t095_incomplete_hybrid_overlay_is_refused_and_changes_nothing(world):
    _, password_hash = world
    user, _ = mk_user(password_hash)
    complete = {"mode": "hybrid", "entra_enabled": True, "activation": {"real_provider_approved": True},
                "entra": {"tenant_id": TENANT, "client_id": "synthetic-client", "secret_file": "/nonexistent/secret",
                          "allowed_issuer": f"https://login.microsoftonline.com/{TENANT}/v2.0",
                          "discovery_url": f"https://login.microsoftonline.com/{TENANT}/v2.0/.well-known/openid-configuration",
                          "redirect_uri": "https://clinic.example.invalid/callback", "scopes": ["openid"], "pkce": True}}
    assert entra.EntraConfig.from_dict(complete).enabled
    with main_app.app_context():
        before = (identity._policy(user).login_policy, identity._policy(user).version, counts())
    for omitted in ("secret_file", "allowed_issuer", "redirect_uri"):
        overlay = {**complete, "entra": {k: v for k, v in complete["entra"].items() if k != omitted}}
        with pytest.raises(entra.EntraConfigError):
            entra.EntraConfig.from_dict(overlay)
    assert not entra.EntraConfig.from_dict({**complete, "activation": {}}).enabled
    with main_app.app_context():
        assert (identity._policy(user).login_policy, identity._policy(user).version, counts()) == before


def test_link_request_authorization_and_retry_cases(world, config, key):
    monkeypatch, password_hash = world
    app = make_app(monkeypatch, config, Idp(config, key))
    admin, _ = mk_user(password_hash, admin=True, ident=True)
    other_admin, _ = mk_user(password_hash, admin=True, ident=True)
    typist, _ = mk_user(password_hash)
    target, _ = mk_user(password_hash)
    url = "/api/clinic/v1/identity/users/%d/entra-link-requests"
    body = {"object_id": OID, "expected_version": 1, "password": PASSWORD}
    before = counts()

    assert app.test_client().post(url % target, json=body).status_code == 403  # no CSRF token
    client, headers = anonymous(app)
    assert client.post(url % target, headers=headers, json=body).status_code == 401
    admin_client, admin_headers = login(app, admin)
    for extra in ({"actor_id": admin}, {"role": "admin"}):
        assert admin_client.post(url % target, headers=admin_headers, json={**body, **extra}).status_code == 400
    assert admin_client.post(url % target, headers=admin_headers, json={**body, "password": "wrong"}).status_code == 403
    assert admin_client.post(url % other_admin, headers=admin_headers, json=body).status_code == 403  # x07
    typist_client, typist_headers = login(app, typist)
    assert typist_client.post(url % target, headers=typist_headers, json=body).status_code == 403
    stale = admin_client.post(url % target, headers=admin_headers, json={**body, "expected_version": 7})
    assert (stale.status_code, stale.json["code"]) == (409, "REVISION_CONFLICT")
    assert counts() == before

    with monkeypatch.context() as patch:
        patch.setattr(intents, "approve_link", lambda *a, **k: (_ for _ in ()).throw(TimeoutError("down")))
        failed = admin_client.post(url % target, headers=admin_headers, json=body)
    assert (failed.status_code, failed.json["code"]) == (503, "DEPENDENCY_UNAVAILABLE")
    assert admin_client.post(url % target, headers=admin_headers, json=body).status_code == 201
    monkeypatch.setenv("CLINIC_MODE", "0")
    assert client.get(BASE).status_code == 404


def test_unlink_works_while_disabled_and_checks_version_and_role(world, password_hash):
    monkeypatch, _ = world
    app = make_app(monkeypatch, entra.EntraConfig(), lambda *_: pytest.fail("exchange called"))
    admin, _ = mk_user(password_hash, admin=True, ident=True)
    typist, _ = mk_user(password_hash)
    user, _ = mk_user(password_hash, policy="local_and_entra")
    with main_app.app_context():
        link = entra.ClinicExternalIdentity(user_id=user, issuer="https://issuer.example.invalid", tenant_id=TENANT,
                                            subject="s", created_by=admin)
        db.session.add(link)
        db.session.commit()
        link_id = link.id
    url = f"/api/clinic/v1/identity/users/{user}/entra-links/{link_id}/unlink"
    typist_client, typist_headers = login(app, typist)
    assert typist_client.post(url, headers=typist_headers, json={"expected_version": 1}).status_code == 403
    client, headers = login(app, admin)
    assert client.post(url, headers=headers, json={"expected_version": 9}).status_code == 409
    assert client.post(url, headers=headers, json={"expected_version": 1}).status_code == 204
    with main_app.app_context():
        assert db.session.get(entra.ClinicExternalIdentity, link_id).status == "revoked"
        assert AuditEvent.query.filter_by(event="auth.sso.unlinked", target_user_id=user).count() == 1
    assert client.post(url, headers=headers, json={"expected_version": 2}).status_code == 403  # no longer active


def test_trusted_ingress_binds_provisioned_sid_once(world, config, key):
    monkeypatch, password_hash = world
    user, _ = mk_user(password_hash)
    used = set()

    def claim_once(ingress, assertion_id, _expires):
        return (ingress, assertion_id) not in used and not used.add((ingress, assertion_id))

    iwa = iwa_identity.IwaConfig(True, "ingress-a", 1, {SID: user})
    app = make_app(monkeypatch, config, Idp(config, key), iwa_config=iwa, claim_once=claim_once)

    def assertion(sid=SID, assertion_id="assertion-0000000001"):
        return iwa_identity.VerifiedAssertion("ingress-a", 1, sid, assertion_id, int(time()))

    def attempt(**kwargs):
        client, headers = anonymous(app)
        extra = kwargs.pop("headers", {})
        response = client.post(BASE + "/iwa/login", headers={**headers, **extra},
                               environ_overrides={iwa_identity.ASSERTION_ENV_KEY: assertion(**kwargs)})
        return client, response

    assert attempt(sid="S-1-5-21-1-2-3-4004")[1].status_code == 403  # unprovisioned SID
    assert attempt(headers={"X-Iwa-User": "someone"})[1].status_code == 403  # browser supplied identity
    client, ok = attempt()
    assert ok.json == {"signed_in": True} and client.get("/test-whoami").json == {"user_id": user}
    replay_client, replay = attempt()
    assert replay.status_code == 403 and replay_client.get("/test-whoami").json == {"user_id": None}
