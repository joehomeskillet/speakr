"""Synthetic IWA contract fixtures only; no Negotiate/AD verification or G-IWA evidence."""

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from src.app import app
from src.clinic import identity, iwa_identity as iwa
from src.database import db
from src.models import User

NOW = int(datetime(2026, 11, 17, tzinfo=timezone.utc).timestamp())
SID = "S-1-5-21-111-222-333-1001"
OTHER_SID = "S-1-5-21-111-222-333-1002"


@pytest.fixture
def synthetic(clinic_on, monkeypatch):
    monkeypatch.setattr(iwa, "time", lambda: NOW)
    with app.app_context():
        users = [User(username=f"synthetic_iwa_{n}", email=f"iwa-{n}@example.invalid") for n in (1, 2)]
        db.session.add_all(users)
        db.session.flush()
        for n, user in enumerate(users, start=1):
            db.session.add(identity.ClinicAccountPolicy(user_id=user.id, status="active", security_version=n))
        db.session.flush()
        yield iwa.IwaConfig(True, "synthetic-ingress", 2, {SID: users[0].id, OTHER_SID: users[1].id})
        db.session.rollback()


@pytest.fixture
def claim_once():
    consumed = set()

    def claim(ingress_id, assertion_id, expires_at):
        key = ingress_id, assertion_id
        if expires_at <= NOW or key in consumed:
            return False
        consumed.add(key)
        return True

    return claim


def environ(**overrides):
    assertion = iwa.VerifiedAssertion("synthetic-ingress", 2, SID, "synthetic_nonce_1", NOW)
    return {iwa.ASSERTION_ENV_KEY: replace(assertion, **overrides)}


def test_synthetic_resolves_only_provisioned_sid_and_rejects_replay(synthetic, claim_once):
    for n, sid in enumerate((SID, OTHER_SID), start=1):
        env = environ(sid=sid, assertion_id=f"synthetic_nonce_{n}")
        assert iwa.resolve_identity(synthetic, env, claim_once=claim_once) == iwa.IwaBinding(synthetic.subjects[sid], n)
        with pytest.raises(identity.AuthenticationFailed):
            iwa.resolve_identity(synthetic, env, claim_once=claim_once)


@pytest.mark.parametrize("headers", [{}, {"X-Remote-User": SID}, {"Clinic.Iwa.Assertion": SID}])
def test_synthetic_x4_018_denies_browser_identity_hints(synthetic, claim_once, headers):
    with app.test_request_context(
        "/?login_hint=synthetic_iwa_1&start=synthetic_iwa_2", headers=headers
    ) as ctx:
        with pytest.raises(identity.AuthenticationFailed):
            iwa.resolve_identity(synthetic, ctx.request.environ, claim_once=claim_once)


def test_synthetic_rejects_browser_header_even_with_verified_assertion(synthetic, claim_once):
    with pytest.raises(identity.AuthenticationFailed):
        iwa.resolve_identity(synthetic, {**environ(), "HTTP_X_IWA_SID": OTHER_SID}, claim_once=claim_once)


@pytest.mark.parametrize("overrides", [
    {"ingress_id": "unknown"}, {"sid": f"{SID},{OTHER_SID}"}, {"sid": "S-1-5-21-111-222-333-9999"},
    {"version": 1}, {"issued_at": NOW - 60}, {"issued_at": NOW + 1},
])
def test_synthetic_denies_untrusted_ambiguous_unmapped_or_stale(synthetic, claim_once, overrides):
    with pytest.raises(identity.AuthenticationFailed):
        iwa.resolve_identity(synthetic, environ(**overrides), claim_once=claim_once)


def test_synthetic_denies_disabled_profile_and_clinic_off(synthetic, monkeypatch):
    def unexpected(*_):
        pytest.fail("Disabled IWA must not call the replay store")

    with pytest.raises(identity.AuthenticationFailed):
        iwa.resolve_identity(iwa.IwaConfig(), environ(), claim_once=unexpected)
    monkeypatch.setenv("CLINIC_MODE", "0")
    with pytest.raises(identity.AuthenticationFailed):
        iwa.resolve_identity(synthetic, environ(), claim_once=unexpected)


def test_synthetic_store_failure_never_falls_back(synthetic, claim_once):
    def unavailable(*args):
        assert claim_once(*args)
        raise TimeoutError

    with pytest.raises(identity.AuthenticationFailed, match="^authentication_failed$"):
        iwa.resolve_identity(synthetic, environ(), claim_once=unavailable)
    with pytest.raises(identity.AuthenticationFailed):
        iwa.resolve_identity(synthetic, environ(), claim_once=claim_once)
    fresh = environ(assertion_id="synthetic_nonce_2")
    assert iwa.resolve_identity(synthetic, fresh, claim_once=claim_once).user_id == synthetic.subjects[SID]


def test_synthetic_denies_disabled_account(synthetic, claim_once):
    user_id = synthetic.subjects[SID]
    identity.set_status(user_id, "disabled")
    with pytest.raises(identity.AuthenticationFailed):
        iwa.resolve_identity(synthetic, environ(), claim_once=claim_once)
