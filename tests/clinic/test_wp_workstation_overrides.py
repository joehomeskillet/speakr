"""Workstation overrides below server ceilings (PAR-CFG-001, R-048, R-050).

The original BT-CFG-001 oracle is not executed here and is not marked passed.
"""

import pytest

from src.clinic import config
from src.clinic.workstation_preferences import (
    ALLOWLIST,
    Effective,
    PreferenceError,
    StaleVersion,
    WorkstationPreferences,
)
from src.models.user import User

_CLIENT = {
    "profile": "managed_web_offline",
    "client_profile": "managed_web_offline",
    "workstation_token": "managed_web_offline",
    "persistent_audio": True,
    "native_bridge": True,
}


class _Account:
    def __init__(self, user_id, authenticated=True):
        self.id = user_id
        self.is_authenticated = authenticated


@pytest.fixture
def clinic(monkeypatch):
    monkeypatch.setenv("CLINIC_MODE", "1")


def _store_for(user_id=1):
    store = WorkstationPreferences()
    user = _Account(user_id)
    return store, user, store.issue(user)


def test_allowlist_is_nonclinical_presentation_only():
    assert "ui_theme" in ALLOWLIST
    assert "api_query_tokens_enabled" not in ALLOWLIST
    assert "profile" not in ALLOWLIST
    assert "priority" not in ALLOWLIST


def test_apply_is_scoped_to_the_authenticated_user_and_issued_workstation():
    store, user, first = _store_for(1)
    other = store.issue(user)
    version = store.apply(user, first, {"ui_theme": "cards", "ui_language": "de-CH"}, expected_version=0)
    assert version == 1
    assert store.stored(user, first) == {"ui_theme": "cards", "ui_language": "de"}
    assert store.stored(user, other) == {}
    stranger = _Account(2)
    with pytest.raises(PreferenceError) as denied:
        store.effective(stranger, first)
    assert denied.value.code == "untrusted_workstation"


def test_identity_is_the_user_model_id():
    user = User(id=3, username="wpw006", email="wpw006@example.test")
    assert user.is_authenticated is True
    store = WorkstationPreferences()
    workstation_id = store.issue(user)
    store.apply(user, workstation_id, {"keyboard_shortcuts": False}, expected_version=0)
    assert store.stored(user, workstation_id) == {"keyboard_shortcuts": False}


def test_unauthorized_and_client_workstation_token_are_refused():
    store = WorkstationPreferences()
    with pytest.raises(PreferenceError) as anonymous:
        store.issue(_Account(1, authenticated=False))
    assert anonymous.value.code == "unauthorized"
    with pytest.raises(PreferenceError) as missing:
        store.issue(None)
    assert missing.value.code == "unauthorized"
    with pytest.raises(PreferenceError) as bad_id:
        store.issue(_Account(True))
    assert bad_id.value.code == "unauthorized"
    user = _Account(1)
    issued = store.issue(user)
    with pytest.raises(PreferenceError) as token:
        store.apply(user, "managed_web_offline", {"ui_theme": "cards"}, expected_version=0)
    assert token.value.code == "untrusted_workstation"
    assert store.stored(user, issued) == {}


def test_illegal_payload_does_not_persist_and_a_clean_retry_does(clinic):
    store, user, workstation_id = _store_for()
    store.apply(user, workstation_id, {"ui_theme": "cards"}, expected_version=0)
    illegal = {"ui_theme": "focus", "profile": "managed_web_offline", "api_query_tokens_enabled": True}
    with pytest.raises(PreferenceError) as failed:
        store.apply(user, workstation_id, illegal, expected_version=1)
    assert failed.value.code == "capability_forbidden"
    assert store.stored(user, workstation_id) == {"ui_theme": "cards"}
    with pytest.raises(PreferenceError) as clinical:
        store.apply(user, workstation_id, {"priority": 128}, expected_version=1)
    assert clinical.value.code == "not_allowlisted"
    with pytest.raises(PreferenceError) as palette:
        store.apply(user, workstation_id, {"theme_color_scheme": "red"}, expected_version=1)
    assert palette.value.code == "bad_value"
    assert store.effective(user, workstation_id).version == 1
    version = store.apply(user, workstation_id, {"theme_dark_mode": True}, expected_version=1)
    assert version == 2
    assert store.stored(user, workstation_id) == {"ui_theme": "cards", "theme_dark_mode": True}


def test_cancel_discards_the_new_value_and_apply_keeps_it():
    store, user, workstation_id = _store_for()
    store.apply(user, workstation_id, {"audio_player_position": "bottom"}, expected_version=0)
    version = store.cancel(
        user, workstation_id, {"audio_player_position": "top"}, expected_version=1,
    )
    assert version == 1
    assert store.stored(user, workstation_id) == {"audio_player_position": "bottom"}
    store.apply(user, workstation_id, {"audio_player_position": "top"}, expected_version=1)
    assert store.stored(user, workstation_id) == {"audio_player_position": "top"}


def test_stale_version_leaves_the_row_unchanged_until_retry():
    store, user, workstation_id = _store_for()
    store.apply(user, workstation_id, {"editor_autosave": True}, expected_version=0)
    with pytest.raises(StaleVersion) as stale:
        store.apply(user, workstation_id, {"editor_autosave": False}, expected_version=0)
    assert stale.value.version == 1
    assert stale.value.code == "stale_version"
    assert store.stored(user, workstation_id) == {"editor_autosave": True}
    store.apply(user, workstation_id, {"editor_autosave": False}, expected_version=stale.value.version)
    assert store.stored(user, workstation_id) == {"editor_autosave": False}


def test_central_lock_wins_over_a_stored_override_after_it_changes():
    store, user, workstation_id = _store_for()
    store.apply(user, workstation_id, {"audio_player_position": "top"}, expected_version=0)
    store.lock("audio_player_position", "bottom")
    locked = store.effective(user, workstation_id)
    assert locked.values["audio_player_position"] == "bottom"
    assert locked.origins["audio_player_position"] == "central"
    assert store.stored(user, workstation_id)["audio_player_position"] == "top"
    store.unlock("audio_player_position")
    released = store.effective(user, workstation_id)
    assert released.values["audio_player_position"] == "top"
    assert released.origins["audio_player_position"] == "workstation"


def test_setting_ceiling_wins_after_a_policy_change_and_is_off_without_clinic_mode(clinic, monkeypatch):
    store, user, workstation_id = _store_for()
    store.apply(user, workstation_id, {"ui_theme": "cards"}, expected_version=0)
    store.lock("ui_theme", "desk")
    monkeypatch.setitem(config.SETTING_CEILINGS, "ui_theme", ("public_sharing", "focus"))
    forced = store.effective(user, workstation_id)
    assert forced.values["ui_theme"] == "focus"
    assert forced.origins["ui_theme"] == "ceiling"
    assert store.stored(user, workstation_id)["ui_theme"] == "cards"
    monkeypatch.delenv("CLINIC_MODE")
    inert = store.effective(user, workstation_id)
    assert inert.values["ui_theme"] == "desk"
    assert inert.origins["ui_theme"] == "central"


def test_client_cannot_select_managed_or_offline_capabilities(clinic):
    store, user, workstation_id = _store_for()
    seen = store.effective(user, workstation_id, client=_CLIENT)
    assert isinstance(seen, Effective)
    assert seen.profile == "managed_web_online"
    assert seen.profile == config.client_profile()
    assert seen.capabilities == config.client_capabilities()["profiles"]["managed_web_online"]
    assert seen.capabilities["persistent_audio"] is False
    assert seen.capabilities["native_bridge"] is False
    offline = config.client_capabilities()["profiles"]["managed_web_offline"]
    assert "requires" not in seen.capabilities
    assert seen.capabilities != offline
    with pytest.raises(PreferenceError) as failed:
        store.load({"issued": {"managed_web_offline": 1}, "rows": [], "locks": {}})
    assert failed.value.code == "untrusted_workstation"


def test_restart_keeps_the_local_value_and_reapplies_the_lock():
    store, user, workstation_id = _store_for()
    store.apply(user, workstation_id, {"show_timestamps_simple_view": True}, expected_version=0)
    store.lock("show_timestamps_simple_view", False)
    restarted = WorkstationPreferences.load(store.dump())
    seen = restarted.effective(user, workstation_id)
    assert seen.values["show_timestamps_simple_view"] is False
    assert seen.origins["show_timestamps_simple_view"] == "central"
    assert restarted.stored(user, workstation_id)["show_timestamps_simple_view"] is True
    restarted.unlock("show_timestamps_simple_view")
    assert restarted.effective(user, workstation_id).values["show_timestamps_simple_view"] is True
