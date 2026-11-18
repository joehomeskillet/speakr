"""Synthetic OIDC callback and concurrent binding evidence for T-087."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from time import time
from urllib.parse import parse_qs, urlsplit

import pytest
from joserfc import jwt
from joserfc.jwk import RSAKey

from src.app import app
from src.clinic import entra, entra_link_intent as intents, identity
from src.database import db
from src.models import User

OID = "aaaaaaaa-0000-0000-0000-000000000001"
OTHER_OID = "aaaaaaaa-0000-0000-0000-000000000002"


@pytest.fixture(scope="module")
def config():
    tenant = "11111111-2222-3333-4444-555555555555"
    return entra.EntraConfig(True, tenant, "synthetic-client",
                             allowed_issuer=f"https://login.microsoftonline.com/{tenant}/v2.0",
                             redirect_uri="https://clinic.example.invalid/callback")


@pytest.fixture(scope="module")
def key():
    return RSAKey.generate_key(2048, {"kid": "test-key"})


@pytest.fixture
def users(clinic_on):
    with app.app_context():
        identity.create_tables()
        entra.create_tables()
        intents.create_tables()
        ids = []
        for name, policy in (("wp9admin", "local_only"), ("wp9first", "local_only"),
                             ("wp9other", "local_and_entra"), ("wp9device", "device_only")):
            user = User(username=name, email=f"{name}@example.invalid", password="synthetic-old-hash")
            db.session.add(user)
            db.session.flush()
            ids.append(user.id)
            db.session.add(identity.ClinicAccountPolicy(user_id=user.id, login_policy=policy,
                                                        status="active", is_identity_admin=name == "wp9admin"))
        db.session.commit()
        yield ids


def start(config, user, admin=None, *, browser="browser-a", oid=OID):
    args = {}
    if admin is not None:
        policy = db.session.get(identity.ClinicAccountPolicy, user)
        args = dict(request_id=intents.approve_link(config, user_id=user, requested_by=admin,
                                                   object_id=oid, expected_version=policy.version),
                    fresh_local_auth=identity.AuthResult(user, policy.security_version, False))
    url = intents.begin(config, purpose="link" if args else "login", browser_session=browser,
                        current_user_id=user, **args)
    return parse_qs(urlsplit(url).query)["state"][0]


def callback(config, key, state, user, *, purpose="link", browser="browser-a", subject="synthetic-sub",
             oid=OID, exchange=None):
    def signed(pending):
        claims = dict(iss=config.allowed_issuer, aud=config.client_id, tid=config.tenant_id,
                      sub=subject, oid=oid, nonce=pending.nonce, exp=int(time()) + 120,
                      email="wp9other@example.invalid")
        token = jwt.encode({"alg": "RS256", "kid": "test-key"}, claims, key)
        return token, {"keys": [key.as_dict(private=False)]}

    return intents.complete(config, purpose=purpose, browser_session=browser, current_user_id=user,
                            returned_state=state, redirect_uri=config.redirect_uri, exchange=exchange or signed)


@pytest.mark.parametrize("race", ["same_state", "same_subject", "same_object"])
def test_t087_parallel_callbacks(users, config, key, race):
    admin, first, other, _ = users
    state = start(config, first, admin)
    first_case = (state, first, "browser-a", "synthetic-sub", OID)
    if race == "same_state":
        second_case = first_case
    else:
        subject = "synthetic-sub" if race == "same_subject" else "another-sub"
        oid = OTHER_OID if race == "same_subject" else OID
        second_case = (start(config, other, admin, browser="browser-b", oid=oid), other, "browser-b", subject, oid)
    barrier = Barrier(2, timeout=10)

    def attempt(case):
        state, owner, browser, subject, oid = case
        with app.app_context():
            barrier.wait()
            try:
                return callback(config, key, state, owner, browser=browser, subject=subject, oid=oid)
            except entra.EntraError as exc:
                return exc.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, (first_case, second_case)))
    successes = [result for result in results if isinstance(result, entra.EntraLogin)]
    assert len(successes) == 1
    assert ("intent_invalid" if race == "same_state" else "link_conflict") in results
    winner = successes[0]
    assert winner.user_id in (first, other)
    link = entra.ClinicExternalIdentity.query.one()
    assert (link.user_id, link.subject) == (winner.user_id, winner.subject)
    assert db.session.get(User, winner.user_id).get_id() == f"{winner.user_id}:1"
    assert identity.security_version_of(winner.user_id) == winner.security_version == 1
    case = first_case if winner.user_id == first else second_case
    with pytest.raises(entra.EntraStateError, match="intent_invalid"):
        callback(config, key, case[0], winner.user_id, browser=case[2], subject=case[3], oid=case[4])
    assert entra.ClinicExternalIdentity.query.count() == 1


def test_callback_binding_policy_and_provider_retry(users, config, key, monkeypatch):
    admin, first, other, device = users
    for user, actor in ((first, other), (device, admin)):
        with pytest.raises(entra.LinkError):
            intents.approve_link(config, user_id=user, requested_by=actor, object_id=OID, expected_version=1)
    state = start(config, first, admin)
    for provider, user, purpose, browser in (
        (config, other, "link", "browser-a"), (config, first, "link", "browser-b"),
        (config, first, "login", "browser-a"), (replace(config, tenant_id=OTHER_OID), first, "link", "browser-a"),
    ):
        with pytest.raises(entra.EntraStateError, match="intent_invalid"):
            callback(provider, key, state, user, purpose=purpose, browser=browser)
    with pytest.raises(entra.LinkError, match="link_not_permitted"):
        callback(config, key, state, first, oid=OTHER_OID)

    def outage(_pending):
        raise TimeoutError("synthetic provider timeout")

    with pytest.raises(TimeoutError, match="synthetic provider timeout"):
        callback(config, key, state, first, exchange=outage)
    assert entra.ClinicExternalIdentity.query.count() == 0
    assert callback(config, key, state, first).user_id == first
    assert db.session.get(User, first).password is None
    assert db.session.get(identity.ClinicAccountPolicy, first).login_policy == "entra_only"

    login_state = start(config, None)
    with pytest.raises(entra.EntraLoginDenied):
        callback(config, key, login_state, None, purpose="login", subject="unknown-subject")
    assert callback(config, key, login_state, None, purpose="login").user_id == first
    with pytest.raises(entra.EntraStateError, match="intent_invalid"):
        callback(config, key, login_state, None, purpose="login")
    with pytest.raises(entra.EntraLoginDenied):
        callback(config, key, start(config, other), other, purpose="login")

    down = replace(config, enabled=False)
    with pytest.raises(entra.EntraDisabled):
        start(down, None)
    with pytest.raises(entra.EntraDisabled):
        callback(down, key, login_state, None, purpose="login")
    for user in (first, device):
        with pytest.raises(identity.IdentityDenied, match="recovery_not_permitted"):
            identity.issue_recovery(user, issued_by=admin)
    link_id = entra.ClinicExternalIdentity.query.one().id
    with pytest.raises(entra.LinkError, match="version_conflict"):
        intents.unlink(link_id, user_id=first, revoked_by=admin, expected_version=2)
    with pytest.raises(entra.LinkError, match="unlink_would_disable_login"):
        intents.unlink(link_id, user_id=first, revoked_by=admin, expected_version=1)
    identity.set_status(first, "disabled", actor_id=admin)
    assert intents.unlink(link_id, user_id=first, revoked_by=admin, expected_version=1).status == "revoked"
    assert identity.security_version_of(first) == 3
    assert db.session.get(identity.ClinicAccountPolicy, first).login_policy == "entra_only"
    monkeypatch.delenv("CLINIC_MODE")
    with pytest.raises(entra.EntraDisabled):
        start(config, None)


def test_stale_version_and_expired_intent(users, config, key, monkeypatch):
    admin, first, _, _ = users
    state = start(config, first, admin)
    identity.revoke_sessions(first, actor_id=admin)
    with pytest.raises(identity.VersionConflict, match="version_conflict"):
        callback(config, key, state, first)
    fresh = start(config, first, admin)
    monkeypatch.setattr(intents, "time", lambda: time() + entra.PENDING_TTL_SECONDS + 1)
    with pytest.raises(entra.EntraStateError, match="intent_invalid"):
        callback(config, key, fresh, first)
    assert entra.ClinicExternalIdentity.query.count() == 0
