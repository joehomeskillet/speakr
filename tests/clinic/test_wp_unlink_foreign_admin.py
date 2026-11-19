"""x32: no identity admin unlinks another admin's Entra link (x07 consistency).

Synthetic users only; mock evidence, not proof of real Entra.
"""

import pytest

from src.app import app as main_app, db
from src.clinic import entra, entra_link_intent as intents
from src.models import AuditEvent

from tests.clinic.test_wp_identity_providers_api import (  # noqa: F401  (fixtures reused)
    TENANT, key, login, make_app, mk_user, password_hash, world, anonymous,
)


def mk_link(owner, creator):
    with main_app.app_context():
        link = entra.ClinicExternalIdentity(user_id=owner, issuer="https://issuer.example.invalid",
                                            tenant_id=TENANT, subject="s-" + str(owner), created_by=creator)
        db.session.add(link)
        db.session.commit()
        return link.id


def url(user, link_id):
    return f"/api/clinic/v1/identity/users/{user}/entra-links/{link_id}/unlink"


def audit_count(user):
    with main_app.app_context():
        return AuditEvent.query.filter_by(event="auth.sso.unlinked", target_user_id=user).count()


def link_status(link_id):
    with main_app.app_context():
        return db.session.get(entra.ClinicExternalIdentity, link_id).status


@pytest.fixture
def app_and_actor(world):
    monkeypatch, password_hash = world
    app = make_app(monkeypatch, entra.EntraConfig(), lambda *_: pytest.fail("exchange called"))
    actor, _ = mk_user(password_hash, admin=True, ident=True)
    return monkeypatch, app, actor, password_hash


@pytest.mark.parametrize("kind", ["site_admin", "identity_admin"])
def test_foreign_admin_target_refused(app_and_actor, kind):
    _, app, actor, password_hash = app_and_actor
    target, _ = mk_user(password_hash, admin=kind == "site_admin", ident=kind == "identity_admin",
                        policy="local_and_entra")
    link_id = mk_link(target, actor)
    client, headers = login(app, actor)
    response = client.post(url(target, link_id), headers=headers, json={"expected_version": 1})
    assert response.status_code == 403
    assert link_status(link_id) == "active"
    assert audit_count(target) == 0
    with main_app.app_context():
        with pytest.raises(entra.LinkError) as err:
            intents.unlink(link_id, user_id=target, revoked_by=actor, expected_version=1)
        assert err.value.code == "not_authorized"
    assert link_status(link_id) == "active"


def test_own_link_and_non_admin_target_still_work(app_and_actor):
    _, app, actor, password_hash = app_and_actor
    own = mk_link(actor, actor)
    client, headers = login(app, actor)
    assert client.post(url(actor, own), headers=headers, json={"expected_version": 1}).status_code == 204
    assert link_status(own) == "revoked"
    assert audit_count(actor) == 1
    plain, _ = mk_user(password_hash, policy="local_and_entra")
    link_id = mk_link(plain, actor)
    client, headers = login(app, actor)  # revoking an own link rotates the session version
    assert client.post(url(plain, link_id), headers=headers, json={"expected_version": 1}).status_code == 204
    assert link_status(link_id) == "revoked"
    assert audit_count(plain) == 1


def test_csrf_unauthenticated_and_clinic_mode_off_unchanged(app_and_actor):
    monkeypatch, app, actor, password_hash = app_and_actor
    target, _ = mk_user(password_hash, admin=True, policy="local_and_entra")
    link_id = mk_link(target, actor)
    client, headers = login(app, actor)
    assert client.post(url(target, link_id), json={"expected_version": 1}).status_code == 403  # no CSRF
    anon, anon_headers = anonymous(app)
    assert anon.post(url(target, link_id), headers=anon_headers, json={"expected_version": 1}).status_code == 401
    assert link_status(link_id) == "active"
    monkeypatch.setenv("CLINIC_MODE", "0")
    assert client.post(url(target, link_id), headers=headers, json={"expected_version": 1}).status_code == 404
