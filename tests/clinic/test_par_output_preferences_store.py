"""PAR-EXP-001: versioned per-account output targets and open-after-export."""

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.app import app, db
from src.clinic import registry
from src.clinic.output_preferences import (
    ClinicOutputPreference,
    PreferenceError,
    get_preferences,
    save_preferences,
)

CLIPBOARD = {
    "channel": "clipboard",
    "target_alias": "zwischenablage",
    "format": "plain",
    "selected_by_default": True,
}
RTF = {
    "channel": "download",
    "target_alias": "rtf-ablage",
    "format": "rtf",
    "selected_by_default": True,
}


def test_model_is_registered():
    assert "output_preferences" in registry.MODEL_MODULES
    assert ClinicOutputPreference.__tablename__ == "clinic_output_preference"


def test_old_account_gets_safe_defaults(world):
    with app.app_context():
        got = get_preferences(world["user_a"])
        assert (got.version, got.targets, got.open_after_export) == (0, (), False)
        assert db.session.get(ClinicOutputPreference, world["user_a"]) is None


def test_stale_write_preserves_the_current_selection(world):
    with app.app_context():
        uid = world["user_a"]
        save_preferences(uid, [CLIPBOARD, RTF], False, expected_version=0)
        with pytest.raises(PreferenceError) as stale:
            save_preferences(uid, [CLIPBOARD], True, expected_version=0)
        assert stale.value.code == "stale_version"
        got = get_preferences(uid)
        assert got.version == 1
        assert got.open_after_export is False
        assert [item.target_alias for item in got.targets] == ["zwischenablage", "rtf-ablage"]


def test_second_session_reopens_the_saved_preferences(world):
    uid = world["user_a"]
    with app.app_context():
        save_preferences(uid, [CLIPBOARD, RTF], True, expected_version=0)
        db.session.remove()
        with Session(db.engine) as second:
            row = second.execute(
                select(
                    ClinicOutputPreference.version,
                    ClinicOutputPreference.open_after_export,
                    ClinicOutputPreference.targets_json,
                ).where(ClinicOutputPreference.user_id == uid)
            ).one()
        assert row[0] == 1
        assert row[1] is True or row[1] == 1
        assert "rtf-ablage" in row[2]
        reopened = get_preferences(uid)
        assert reopened.version == 1
        assert reopened.open_after_export is True
        assert [(item.channel, item.format) for item in reopened.targets] == [
            ("clipboard", "plain"),
            ("download", "rtf"),
        ]
        other = get_preferences(world["user_b"])
        assert (other.version, other.targets, other.open_after_export) == (0, (), False)


def test_illegal_target_does_not_replace_the_selection(world):
    with app.app_context():
        uid = world["user_a"]
        save_preferences(uid, [CLIPBOARD], False, expected_version=0)
        path = dict(CLIPBOARD, target_alias="../secret")
        docx = dict(CLIPBOARD, format="docx")
        for bad in (path, docx):
            with pytest.raises(PreferenceError) as refused:
                save_preferences(uid, [bad], True, expected_version=1)
            assert refused.value.code == "bad_target"
        got = get_preferences(uid)
        assert got.version == 1
        assert got.open_after_export is False
        assert got.targets[0].format == "plain"


def test_corrupt_row_is_not_treated_as_the_default(world):
    with app.app_context():
        uid = world["user_a"]
        db.session.add(ClinicOutputPreference(
            user_id=uid, version=2, targets_json="{", open_after_export=False,
        ))
        db.session.commit()
        with pytest.raises(PreferenceError) as corrupt:
            get_preferences(uid)
        assert corrupt.value.code == "corrupt"
        with pytest.raises(PreferenceError) as stale:
            save_preferences(uid, [], False, expected_version=0)
        assert stale.value.code == "stale_version"
        row = db.session.get(ClinicOutputPreference, uid)
        assert row.version == 2 and row.targets_json == "{"
