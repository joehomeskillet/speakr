"""Disabled LDAP federation profile (PAR-ADM-009, PAR-ADM-010).

Synthetic configuration only. BT-ADM-009 and BT-ADM-010 stay not_run; this
file does not replace their oracles. G-LDAP and WG-ADM-009 stay open.
"""

import json
import socket
import uuid
from pathlib import Path

import pytest

from src.app import app, db
from src.clinic import config, identity, ldap_profile
from src.models import User

ISSUER = "https://idp.example.invalid/ldap"
SUBJECT = "11111111-2222-3333-4444-555555555555"
ROOT = Path(__file__).resolve().parents[2]
AUTH_DIR = ROOT / "clinic-spec" / "config"


def _disabled(**extra):
    data = {
        "provider": "ldaps_federation",
        "enabled": False,
        "ldap_enabled": False,
        "issuer": ISSUER,
        "subject_attributes": ["issuer", "subject"],
        "lifecycle_source": "application",
        "claim_role_sync": False,
        "server_side": True,
    }
    data.update(extra)
    return data


def _user(admin=False, status="active", with_policy=True):
    suffix = uuid.uuid4().hex[:10]
    user = User(username="ldap" + suffix, email=suffix + "@example.invalid", password=None)
    db.session.add(user)
    db.session.flush()
    if with_policy:
        db.session.add(identity.ClinicAccountPolicy(
            user_id=user.id, status=status, login_policy="local_only",
            is_identity_admin=admin,
        ))
    db.session.commit()
    return user


@pytest.fixture(autouse=True)
def ctx():
    ldap_profile.clear_subject_links()
    with app.app_context():
        identity.create_tables()
        yield
    ldap_profile.clear_subject_links()


def test_auth_examples_validate_disabled_profile():
    for name in ("auth.local.example.json", "auth.hybrid.example.json"):
        data = json.loads((AUTH_DIR / name).read_text(encoding="utf-8"))
        profile = ldap_profile.validate_profile(data)
        assert profile.enabled is False
        assert profile.provider == ldap_profile.SUPPORTED_PROVIDER
        assert profile.server_side is True
        assert profile.subject_attributes == ldap_profile.SUBJECT_ATTRIBUTES
        assert profile.lifecycle_source == ldap_profile.LIFECYCLE_SOURCE
        assert "application_identity" in profile.lifecycle_note
        assert "profile_off_does_not_grant_local_password" in profile.lifecycle_note
        assert profile.claim_role_sync is False
        assert not hasattr(profile, "password")


def test_explicit_profile_keeps_lifecycle_source():
    profile = ldap_profile.validate_profile(_disabled())
    assert profile.issuer == ISSUER
    assert profile.lifecycle_source == "application"
    assert profile.enabled is False


@pytest.mark.parametrize("payload", [
    {"bind_password": "directory-secret"},
    {"userPassword": "directory-secret"},
    {"password": "directory-secret"},
    {"ldap": {"ad_password": "directory-secret"}},
    {"password": {"min_characters": 15, "plaintext": "directory-secret"}},
    {"password": {"min_characters": 15, "external_password_check": True}},
])
def test_end_user_password_is_rejected_and_not_stored(payload):
    data = _disabled()
    data.update(payload)
    with pytest.raises(ldap_profile.LdapProfileError) as raised:
        ldap_profile.validate_profile(data)
    assert raised.value.code == "end_user_password_rejected"
    assert ldap_profile.lookup_subject(ISSUER, SUBJECT) is None
    retried = ldap_profile.validate_profile(_disabled())
    assert retried.enabled is False
    assert not hasattr(retried, "password")


@pytest.mark.parametrize("provider", [
    "direct_ldap_bind", "sam", "windows_sso", "negotiate", "keycloak",
])
def test_unsupported_provider_is_a_named_capability_error(provider):
    with pytest.raises(ldap_profile.LdapCapabilityError) as raised:
        ldap_profile.validate_profile(_disabled(provider=provider))
    assert raised.value.code == ldap_profile.CAPABILITY_CODE
    assert type(raised.value).__name__ == "LdapCapabilityError"


@pytest.mark.parametrize("extra,code", [
    ({"ldap_enabled": True}, "ldap_capability_unavailable"),
    ({"enabled": True}, "ldap_capability_unavailable"),
    ({"server_side": False}, "ldap_capability_unavailable"),
    ({"subject_attributes": ["email"]}, "subject_mapping_unstable"),
    ({"lifecycle_source": "directory"}, "lifecycle_source_invalid"),
    ({"claim_role_sync": True}, "claim_rights_rejected"),
])
def test_profile_rejections(extra, code):
    with pytest.raises(ldap_profile.LdapProfileError) as raised:
        ldap_profile.validate_profile(_disabled(**extra))
    assert raised.value.code == code


def test_local_startup_stays_independent(monkeypatch):
    def refuse_socket(*_args, **_kwargs):
        raise AssertionError("directory socket")

    monkeypatch.setattr(socket, "socket", refuse_socket)
    monkeypatch.setattr(socket, "create_connection", refuse_socket)
    monkeypatch.delenv("CLINIC_MODE", raising=False)
    missing = ldap_profile.local_startup_ready(None)
    assert missing["ready"] is True
    assert missing["directory_contacted"] is False
    assert missing["clinic_mode"] is config.clinic_mode_enabled()
    assert missing["profile_enabled"] is False
    assert missing["profile_error"] is None

    monkeypatch.setenv("CLINIC_MODE", "1")
    broken = ldap_profile.local_startup_ready({"provider": "sam", "bind_password": "secret"})
    assert broken["ready"] is True
    assert broken["directory_contacted"] is False
    assert broken["clinic_mode"] is True
    assert broken["profile_error"] == "end_user_password_rejected"
    capability = ldap_profile.local_startup_ready({"provider": "windows_sso"})
    assert capability["ready"] is True
    assert capability["profile_error"] == "ldap_capability_unavailable"
    assert ldap_profile.validate_profile(_disabled()).enabled is False


def test_subject_maps_stably_to_one_user():
    profile = ldap_profile.validate_profile(_disabled())
    admin = _user(admin=True)
    user = _user()
    other = _user()
    first = ldap_profile.map_subject(
        profile, user_id=user.id, issuer=ISSUER, subject=SUBJECT, actor_id=admin.id,
    )
    second = ldap_profile.map_subject(
        profile, user_id=user.id, issuer=ISSUER, subject=SUBJECT, actor_id=admin.id,
    )
    assert first == second
    assert first.user_id == user.id
    assert first.version == 1
    assert first.lifecycle_source == "application"
    with pytest.raises(identity.IdentityConflict, match="subject_taken"):
        ldap_profile.map_subject(
            profile, user_id=other.id, issuer=ISSUER, subject=SUBJECT, actor_id=admin.id,
        )
    assert ldap_profile.lookup_subject(ISSUER, SUBJECT).user_id == user.id
    with pytest.raises(ldap_profile.LdapProfileError) as raised:
        ldap_profile.map_subject(
            profile, user_id=user.id, issuer=ISSUER, subject="person@example.invalid",
            actor_id=admin.id,
        )
    assert raised.value.code == "subject_mapping_unstable"


def test_unauthorized_actor_does_not_write_then_admin_retries():
    profile = ldap_profile.validate_profile(_disabled())
    user = _user()
    stranger = _user(admin=False)
    disabled_admin = _user(admin=True, status="disabled")
    bare = _user(with_policy=False)
    admin = _user(admin=True)
    for actor in (stranger, disabled_admin):
        with pytest.raises(identity.IdentityDenied, match="not_identity_admin"):
            ldap_profile.map_subject(
                profile, user_id=user.id, issuer=ISSUER, subject=SUBJECT, actor_id=actor.id,
            )
    assert ldap_profile.lookup_subject(ISSUER, SUBJECT) is None
    with pytest.raises(identity.IdentityDenied, match="no_account_policy"):
        ldap_profile.map_subject(
            profile, user_id=bare.id, issuer=ISSUER, subject=SUBJECT, actor_id=admin.id,
        )
    link = ldap_profile.map_subject(
        profile, user_id=user.id, issuer=ISSUER, subject=SUBJECT, actor_id=admin.id,
    )
    assert link.user_id == user.id
    assert link.version == 1


def test_stale_version_leaves_the_link_then_retry_advances_it():
    profile = ldap_profile.validate_profile(_disabled())
    admin = _user(admin=True)
    user = _user()
    created = ldap_profile.map_subject(
        profile, user_id=user.id, issuer=ISSUER, subject=SUBJECT, actor_id=admin.id,
    )
    with pytest.raises(identity.VersionConflict, match="version_conflict"):
        ldap_profile.map_subject(
            profile, user_id=user.id, issuer=ISSUER, subject=SUBJECT, actor_id=admin.id,
            expected_version=0,
        )
    assert ldap_profile.lookup_subject(ISSUER, SUBJECT).version == created.version
    retried = ldap_profile.map_subject(
        profile, user_id=user.id, issuer=ISSUER, subject=SUBJECT, actor_id=admin.id,
        expected_version=created.version,
    )
    assert retried.version == 2
    assert retried.user_id == user.id
    with pytest.raises(identity.VersionConflict, match="version_conflict"):
        ldap_profile.map_subject(
            profile, user_id=user.id, issuer=ISSUER, subject="fresh-subject",
            actor_id=admin.id, expected_version=1,
        )
    assert ldap_profile.lookup_subject(ISSUER, "fresh-subject") is None


def test_login_claims_cannot_assign_roles_or_pools():
    profile = ldap_profile.validate_profile(_disabled())
    admin = _user(admin=True)
    user = _user()
    other = _user()
    ldap_profile.map_subject(
        profile, user_id=user.id, issuer=ISSUER, subject=SUBJECT, actor_id=admin.id,
    )
    result = ldap_profile.apply_claims(profile, {
        "iss": ISSUER,
        "sub": SUBJECT,
        "email": other.email,
        "preferred_username": other.username,
        "user_id": other.id,
        "roles": ["admin", "clinician"],
        "groups": ["writers"],
        "pools": ["pool-a"],
        "staff_pools": ["all"],
    })
    assert result.user_id == user.id
    assert result.user_id != other.id
    assert result.roles == ()
    assert result.pools == ()
    assert result.lifecycle_source == ldap_profile.LIFECYCLE_SOURCE
    with pytest.raises(identity.IdentityDenied, match="login_denied"):
        ldap_profile.apply_claims(profile, {"iss": ISSUER, "sub": "missing", "roles": ["admin"]})
    with pytest.raises(identity.IdentityDenied, match="login_denied"):
        ldap_profile.apply_claims(profile, {"email": user.email, "roles": ["admin"], "pools": ["all"]})
    with pytest.raises(ldap_profile.LdapProfileError) as raised:
        ldap_profile.apply_claims(profile, {"iss": ISSUER, "sub": SUBJECT, "password": "secret"})
    assert raised.value.code == "end_user_password_rejected"
    assert ldap_profile.lookup_subject(ISSUER, SUBJECT).user_id == user.id


def test_module_does_not_import_a_directory_client():
    text = (ROOT / "src" / "clinic" / "ldap_profile.py").read_text(encoding="utf-8")
    assert "import ldap" not in text
    assert "ldap3" not in text
