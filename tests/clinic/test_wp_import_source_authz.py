"""Service authorization and server-owned root boundaries for import sources."""

import uuid

import pytest

from src.app import app, db
from src.clinic import import_source_config as sources, policy
from src.clinic.agent_channel import AgentChannelError
from src.clinic.identity import ClinicAccountPolicy
from src.models import User


@pytest.fixture(autouse=True)
def source_environment(monkeypatch):
    monkeypatch.setenv("CLINIC_MODE", "1")
    monkeypatch.setenv("CLINIC_IMPORT_ROOT_ALLOWLIST", r"C:\Diktate;\\files\clinic\Inbox;/srv/in")
    with app.app_context():
        ClinicAccountPolicy.__table__.create(db.engine, checkfirst=True)
        sources.create_tables()
        yield


def _user(*, admin=False, status=None):
    key = uuid.uuid4().hex
    user = User(username=key, email=f"{key}@example.invalid", password="x", is_admin=admin)
    db.session.add(user)
    db.session.flush()
    if status is not None:
        db.session.add(ClinicAccountPolicy(user_id=user.id, status=status))
    db.session.commit()
    return user


def _create(user, root=r"C:\Diktate\Inbox"):
    return sources.create_source_config(
        actor_user_id=user.id, source_kind="folder", source_ref=user.username,
        approved_root=root, target_author_user_id=user.id,
    )


def _grant(monkeypatch, user, *entries):
    user_id = user.id
    monkeypatch.setattr(policy, "_grant_providers", [])
    policy.register_grant_provider(lambda uid: entries if uid == user_id else ())


@pytest.mark.parametrize("has_recording_grant", [False, True])
def test_non_admin_without_import_grant_cannot_create(monkeypatch, has_recording_grant):
    user = _user(status="active")
    if has_recording_grant:
        _grant(monkeypatch, user, policy.Grant("all", policy.ACTIONS))
    with pytest.raises(AgentChannelError) as exc:
        _create(user)
    assert exc.value.code == "forbidden"
    assert not sources.ClinicImportSourceConfig.query.filter_by(created_by_user_id=user.id).count()


@pytest.mark.parametrize("status", [None, "active"])
def test_site_admin_can_manage_own_configuration(status):
    admin = _user(admin=True, status=status)
    config = _create(admin, r"c:\DIKTATE\Inbox")
    assert config["approved_root"] == "c:/DIKTATE/Inbox"
    assert sources.resolve_source_config("folder", config["source_ref"], "test.wav")["id"] == config["id"]
    for version, enabled in enumerate((True, False), start=1):
        config = sources.update_source_config(
            actor_user_id=admin.id, config_id=config["id"], expected_version=version,
            autosend_enabled=enabled,
        )
        assert config["autosend_enabled"] is enabled
    sources.delete_source_config(actor_user_id=admin.id, config_id=config["id"])
    assert db.session.get(sources.ClinicImportSourceConfig, config["id"]) is None


@pytest.mark.parametrize("denied", [False, True])
def test_explicit_import_grant_is_evaluated_by_central_policy(monkeypatch, denied):
    user = _user(status="active")
    grant = policy.Grant("all", frozenset({"manage_import_sources"}))
    if denied:
        _grant(monkeypatch, user, grant, policy.Deny(author_ids=frozenset({user.id})))
        with pytest.raises(AgentChannelError) as exc:
            _create(user)
        assert exc.value.code == "forbidden"
    else:
        _grant(monkeypatch, user, grant)
        config = _create(user, r"\\FILES\Clinic\inbox\Team")
        assert config["approved_root"] == "//FILES/Clinic/inbox/Team"


@pytest.mark.parametrize("revocation", ["role_removed", "disabled", "pending_activation"])
def test_admin_no_longer_authorized_to_mutate_existing_config(revocation):
    admin = _user(admin=True, status="active")
    config = _create(admin)
    if revocation == "role_removed":
        admin.is_admin = False
    else:
        db.session.get(ClinicAccountPolicy, admin.id).status = revocation
    db.session.commit()

    with pytest.raises(AgentChannelError) as exc:
        _create(admin)
    assert exc.value.code == "forbidden"
    with pytest.raises(AgentChannelError) as exc:
        sources.update_source_config(
            actor_user_id=admin.id, config_id=config["id"], expected_version=1, autosend_enabled=True,
        )
    assert exc.value.code == "forbidden"
    with pytest.raises(AgentChannelError) as exc:
        sources.delete_source_config(actor_user_id=admin.id, config_id=config["id"])
    assert exc.value.code == "forbidden"
    row = db.session.get(sources.ClinicImportSourceConfig, config["id"])
    assert row.policy_version == 1 and not row.autosend_enabled


@pytest.mark.parametrize(("root", "code"), [
    (r"D:\Diktate\Inbox", "forbidden"),
    (r"C:\Diktate\..\other", "invalid"),
    (r"C:\Diktate2", "forbidden"),
    (r"C:Diktate\Inbox", "invalid"),
    (r"\\files\clinic\Inbox2", "forbidden"),
    (r"\\?\C:\Diktate\Inbox", "invalid"),
    ("/srv/In", "forbidden"),
])
def test_create_and_update_reject_unapproved_roots(root, code):
    admin = _user(admin=True)
    with pytest.raises(AgentChannelError) as exc:
        _create(admin, root)
    assert exc.value.code == code
    config = _create(admin, "/srv/in")
    with pytest.raises(AgentChannelError) as exc:
        sources.update_source_config(
            actor_user_id=admin.id, config_id=config["id"], expected_version=1, approved_root=root,
        )
    assert exc.value.code == code
    row = db.session.get(sources.ClinicImportSourceConfig, config["id"])
    assert (row.approved_root, row.policy_version) == ("/srv/in", 1)


@pytest.mark.parametrize("allowlist", [None, "", " ; "])
def test_missing_or_empty_allowlist_rejects_new_and_stored_roots(monkeypatch, allowlist):
    admin = _user(admin=True)
    config = _create(admin)
    if allowlist is None:
        monkeypatch.delenv("CLINIC_IMPORT_ROOT_ALLOWLIST")
    else:
        monkeypatch.setenv("CLINIC_IMPORT_ROOT_ALLOWLIST", allowlist)
    with pytest.raises(AgentChannelError) as exc:
        _create(_user(admin=True))
    assert exc.value.code == "forbidden"
    with pytest.raises(AgentChannelError) as exc:
        sources.update_source_config(
            actor_user_id=admin.id, config_id=config["id"], expected_version=1, autosend_enabled=True,
        )
    assert exc.value.code == "forbidden"
    with pytest.raises(AgentChannelError) as exc:
        sources.resolve_source_config("folder", config["source_ref"], "test.wav")
    assert exc.value.code == "forbidden"
