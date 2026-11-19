"""Import source config: approved sources, author routing, autosend and EOL filter.

SQLite only; this is service-layer evidence, not device/Entra/KIS proof.
"""

import uuid

import pytest

from src.app import app, db
from src.clinic import import_source_config as isc
from src.clinic.agent_channel import AgentChannelError
from src.models import User


def _user(prefix="isc", admin=False):
    row = User(username=prefix + uuid.uuid4().hex[:8], email=uuid.uuid4().hex[:8] + "@example.invalid",
               password="x", is_admin=admin)
    db.session.add(row)
    db.session.commit()
    return row


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    # Approved roots are server-owned: only paths under the allowlist may be approved (x03).
    monkeypatch.setenv("CLINIC_IMPORT_ROOT_ALLOWLIST", "/srv/in;/srv/dropfolders")
    with app.app_context():
        isc.create_tables()
        yield


@pytest.fixture
def world():
    with app.app_context():
        admin = _user("adm", admin=True)
        author = _user("aut")
        other = _user("oth")
        yield {"admin": admin, "author": author, "other": other}


DEVICE_ROOT = "/srv/in/usb-1"


def _create(actor, author=None, **kwargs):
    target = author if author is not None else actor
    base = dict(
        actor_user_id=actor.id, source_kind="device", source_ref="usb-1",
        approved_root=DEVICE_ROOT, target_author_user_id=target.id,
    )
    base.update(kwargs)
    return isc.create_source_config(**base)


# --- positive lifecycle -------------------------------------------------------------------

def test_create_defaults(world):
    cfg = _create(world["admin"])
    assert cfg["source_kind"] == "device"
    assert cfg["source_ref"] == "usb-1"
    assert cfg["approved_root"] == DEVICE_ROOT
    assert cfg["target_author_user_id"] == world["admin"].id
    assert cfg["autosend_enabled"] is False
    assert cfg["eol_filter_enabled"] is True
    assert cfg["file_pattern"] == "*"
    assert cfg["priority"] == 1
    assert cfg["language_id"] == 1031
    assert cfg["policy_version"] == 1
    assert cfg["allow_source_delete"] is False


def test_create_folder_with_routing(world):
    cfg = _create(world["admin"],
                  source_kind="folder", source_ref="drop-in",
                  approved_root="/srv/dropfolders/incoming",
                  autosend_enabled=True, eol_filter_enabled=False,
                  file_pattern="*.ds2", priority=128, language_id=1033,
                  asr_profile_id="prof-1")
    assert cfg["autosend_enabled"] is True
    assert cfg["eol_filter_enabled"] is False
    assert cfg["priority"] == 128
    assert cfg["language_id"] == 1033


def test_update_increments_version(world):
    cfg = _create(world["admin"])
    updated = isc.update_source_config(
        actor_user_id=world["admin"].id, config_id=cfg["id"], expected_version=1,
        eol_filter_enabled=False, autosend_enabled=True,
    )
    assert updated["policy_version"] == 2
    assert updated["eol_filter_enabled"] is False
    assert updated["autosend_enabled"] is True
    assert isc.get_source_config(actor_user_id=world["admin"].id, config_id=cfg["id"])["policy_version"] == 2


def test_list_and_delete(world):
    a = _create(world["admin"], source_ref="usb-a")
    b = _create(world["admin"], source_kind="folder", source_ref="drop-b",
                approved_root="/srv/in")
    assert {c["id"] for c in isc.list_source_configs(actor_user_id=world["admin"].id)} == {a["id"], b["id"]}
    isc.delete_source_config(actor_user_id=world["admin"].id, config_id=a["id"])
    assert [c["id"] for c in isc.list_source_configs(actor_user_id=world["admin"].id)] == [b["id"]]


# --- unauthorized / stale / forged --------------------------------------------------------

def test_unknown_actor(world):
    with pytest.raises(AgentChannelError) as err:
        _create(world["admin"], actor_user_id=99999)
    assert err.value.code == "forbidden"


def test_foreign_author_without_permission(world):
    with pytest.raises(AgentChannelError) as err:
        _create(world["admin"], world["other"])
    assert err.value.code == "forbidden"


def test_foreign_author_with_permission(world):
    cfg = _create(world["admin"], world["other"], may_assign_author=lambda actor, author: True)
    assert cfg["target_author_user_id"] == world["other"].id


def test_stale_version_rejected(world):
    cfg = _create(world["admin"])
    with pytest.raises(AgentChannelError) as err:
        isc.update_source_config(
            actor_user_id=world["admin"].id, config_id=cfg["id"], expected_version=99,
            autosend_enabled=True,
        )
    assert err.value.code == "conflict"


def test_sequential_update_requires_fresh_version(world):
    cfg = _create(world["admin"])
    isc.update_source_config(
        actor_user_id=world["admin"].id, config_id=cfg["id"], expected_version=1,
        autosend_enabled=True,
    )
    with pytest.raises(AgentChannelError) as err:
        isc.update_source_config(
            actor_user_id=world["admin"].id, config_id=cfg["id"], expected_version=1,
            eol_filter_enabled=False,
        )
    assert err.value.code == "conflict"


# --- path safety --------------------------------------------------------------------------

def test_path_traversal_rejected(world):
    cfg = _create(world["admin"], source_kind="folder",
                  approved_root="/srv/in")
    for bad in ("../etc/passwd", "a/../../b", "/etc/passwd", "a/.."):
        with pytest.raises(AgentChannelError) as err:
            isc.resolve_source_config("folder", "usb-1", bad)
        assert err.value.code in ("forbidden", "invalid")


def test_unconfigured_source_rejected(world):
    _create(world["admin"], source_kind="folder", source_ref="drop-in",
            approved_root="/srv/in")
    assert isc.resolve_source_config("folder", "unapproved-source", "dict.ds2") is None


def test_valid_path_under_folder(world):
    _create(world["admin"], source_kind="folder", source_ref="drop-in",
            approved_root="/srv/in", file_pattern="*.ds2")
    found = isc.resolve_source_config("folder", "drop-in", "dict.ds2")
    assert found is not None
    assert found["source_ref"] == "drop-in"


# --- client-controlled deletion rights ----------------------------------------------------

def test_deletion_right_default_off(world):
    cfg = _create(world["admin"])
    assert cfg["allow_source_delete"] is False


def test_deletion_right_requires_authorization(world):
    with pytest.raises(AgentChannelError) as err:
        _create(world["admin"], allow_source_delete=True)
    assert err.value.code == "forbidden"


def test_deletion_right_can_be_authorized(world):
    cfg = _create(world["admin"], allow_source_delete=True,
                  may_manage_deletion=lambda actor: True)
    assert cfg["allow_source_delete"] is True


def test_client_cannot_enable_deletion_in_update(world):
    cfg = _create(world["admin"])
    with pytest.raises(AgentChannelError) as err:
        isc.update_source_config(
            actor_user_id=world["admin"].id, config_id=cfg["id"], expected_version=1,
            allow_source_delete=True,
        )
    assert err.value.code == "forbidden"


def test_delete_blocked_when_deletion_enabled(world):
    cfg = _create(world["admin"], allow_source_delete=True,
                  may_manage_deletion=lambda actor: True)
    with pytest.raises(AgentChannelError) as err:
        isc.delete_source_config(actor_user_id=world["admin"].id, config_id=cfg["id"])
    assert err.value.code == "forbidden"


# --- resolve matching ---------------------------------------------------------------------

def test_resolve_pattern(world):
    _create(world["admin"], source_ref="usb-1", file_pattern="*.ds2")
    assert isc.resolve_source_config("device", "usb-1", "a.DS2") is not None
    assert isc.resolve_source_config("device", "usb-1", "a.wav") is None


def test_resolve_no_traversal_on_device(world):
    _create(world["admin"], source_ref="usb-1", file_pattern="*")
    with pytest.raises(AgentChannelError) as err:
        isc.resolve_source_config("device", "usb-1", "../x.ds2")
    assert err.value.code == "forbidden"


def test_validation_rejects_bad_inputs(world):
    for bad_kind in ("cloud",):
        with pytest.raises(AgentChannelError) as err:
            _create(world["admin"], source_kind=bad_kind)
        assert err.value.code == "invalid"
    with pytest.raises(AgentChannelError) as err:
        _create(world["admin"], source_ref="bad ref")
    assert err.value.code == "invalid"
    with pytest.raises(AgentChannelError) as err:
        _create(world["admin"], approved_root="/etc/../tmp")
    assert err.value.code == "invalid"
