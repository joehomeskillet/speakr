"""Server-side directory comparison with explicit confirmation.

Acceptance criteria from brief:
1. Matching uses ONLY object_id against LocalUser.external_id
2. Entries without object_id -> unmatchable; duplicate object_id -> ambiguous; duplicate local external_id -> ambiguous
3. changed lists differing fields (display_name NFC/whitespace/case-sensitive, enabled); disabled_in_directory; missing_in_directory
4. confirmed_actions returns actions only for confirmed object_ids; kinds are exactly create_pending_user, update_display_name, deactivate
5. Confirmed id not in diff is ignored; actions sorted by (kind, object_id)
6. Module reads inputs only, no database/network/LDAP imports; inputs not mutated
7. Empty inputs give empty diff; 5000 entries run in under 2 seconds
"""

import time
import unicodedata
from dataclasses import replace

import pytest

from src.clinic.directory_reconcile import (
    Action,
    DirectoryEntry,
    Diff,
    DirectoryReconcileError,
    LocalUser,
    compute_diff,
    confirmed_actions,
    _normalise_display_name,
)


def _entry(
    object_id: str | None = "obj-1",
    account: str = "account1",
    display_name: str = "Display Name",
    email: str | None = "user@example.com",
    enabled: bool = True,
) -> DirectoryEntry:
    return DirectoryEntry(
        object_id=object_id,
        account=account,
        display_name=display_name,
        email=email,
        enabled=enabled,
    )


def _local(
    id: int = 1,
    username: str = "user1",
    external_id: str | None = "obj-1",
    display_name: str = "Display Name",
    active: bool = True,
) -> LocalUser:
    return LocalUser(
        id=id,
        username=username,
        external_id=external_id,
        display_name=display_name,
        active=active,
    )


class TestNormaliseDisplayName:
    def test_nfc_normalisation(self):
        nfd = "Cafe\u0301"
        nfc = "Caf\u00e9"
        assert _normalise_display_name(nfd) == nfc

    def test_whitespace_collapse(self):
        assert _normalise_display_name("  John   Doe  ") == "John Doe"
        assert _normalise_display_name("John\t\tDoe") == "John Doe"
        assert _normalise_display_name("John\nDoe") == "John Doe"
        assert _normalise_display_name("John\r\nDoe") == "John Doe"

    def test_case_sensitive(self):
        assert _normalise_display_name("John Doe") != _normalise_display_name("john doe")

    def test_preserves_non_whitespace(self):
        assert _normalise_display_name("  Ä  ") == "Ä"


class TestValidation:
    def test_entries_must_be_sequence(self):
        with pytest.raises(DirectoryReconcileError) as exc:
            compute_diff("not a sequence", [])
        assert exc.value.code == "invalid_type"

    def test_locals_must_be_sequence(self):
        with pytest.raises(DirectoryReconcileError) as exc:
            compute_diff([], "not a sequence")
        assert exc.value.code == "invalid_type"

    def test_entry_must_be_directory_entry(self):
        with pytest.raises(DirectoryReconcileError) as exc:
            compute_diff([{"object_id": "1"}], [])
        assert exc.value.code == "invalid_type"

    def test_local_must_be_local_user(self):
        with pytest.raises(DirectoryReconcileError) as exc:
            compute_diff([], [{"id": 1}])
        assert exc.value.code == "invalid_type"

    def test_account_non_empty_string(self):
        entry = replace(_entry(), account="")
        with pytest.raises(DirectoryReconcileError) as exc:
            compute_diff([entry], [])
        assert exc.value.code == "invalid_value"

    def test_display_name_non_empty_string(self):
        entry = replace(_entry(), display_name="")
        with pytest.raises(DirectoryReconcileError) as exc:
            compute_diff([entry], [])
        assert exc.value.code == "invalid_value"

    def test_object_id_string_or_none(self):
        entry = replace(_entry(), object_id=123)
        with pytest.raises(DirectoryReconcileError) as exc:
            compute_diff([entry], [])
        assert exc.value.code == "invalid_type"

    def test_email_string_or_none(self):
        entry = replace(_entry(), email=123)
        with pytest.raises(DirectoryReconcileError) as exc:
            compute_diff([entry], [])
        assert exc.value.code == "invalid_type"

    def test_enabled_bool(self):
        entry = replace(_entry(), enabled="true")
        with pytest.raises(DirectoryReconcileError) as exc:
            compute_diff([entry], [])
        assert exc.value.code == "invalid_type"

    def test_control_characters_in_account(self):
        entry = replace(_entry(), account="account\x01")
        with pytest.raises(DirectoryReconcileError) as exc:
            compute_diff([entry], [])
        assert exc.value.code == "invalid_value"

    def test_control_characters_in_display_name(self):
        entry = replace(_entry(), display_name="Name\x02")
        with pytest.raises(DirectoryReconcileError) as exc:
            compute_diff([entry], [])
        assert exc.value.code == "invalid_value"

    def test_local_id_int_not_bool(self):
        local = replace(_local(), id=True)
        with pytest.raises(DirectoryReconcileError) as exc:
            compute_diff([], [local])
        assert exc.value.code == "invalid_type"

    def test_local_username_non_empty(self):
        local = replace(_local(), username="")
        with pytest.raises(DirectoryReconcileError) as exc:
            compute_diff([], [local])
        assert exc.value.code == "invalid_value"

    def test_local_external_id_string_or_none(self):
        local = replace(_local(), external_id=123)
        with pytest.raises(DirectoryReconcileError) as exc:
            compute_diff([], [local])
        assert exc.value.code == "invalid_type"

    def test_local_display_name_non_empty(self):
        local = replace(_local(), display_name="")
        with pytest.raises(DirectoryReconcileError) as exc:
            compute_diff([], [local])
        assert exc.value.code == "invalid_value"

    def test_local_active_bool(self):
        local = replace(_local(), active="true")
        with pytest.raises(DirectoryReconcileError) as exc:
            compute_diff([], [local])
        assert exc.value.code == "invalid_type"

    def test_confirmed_object_ids_must_be_collection(self):
        diff = compute_diff([], [])
        with pytest.raises(DirectoryReconcileError) as exc:
            confirmed_actions(diff, "not a collection")
        assert exc.value.code == "invalid_type"


class TestMatching:
    def test_match_by_object_id_only(self):
        entry = _entry(object_id="obj-1", email="different@example.com")
        local = _local(external_id="obj-1", username="other")
        diff = compute_diff([entry], [local])
        assert diff.new == ()
        assert len(diff.changed) == 0

    def test_same_email_no_object_id_is_new_not_match(self):
        entry = _entry(object_id=None, email="user@example.com")
        local = _local(external_id="obj-1", username="user1")
        diff = compute_diff([entry], [local])
        assert len(diff.new) == 0
        assert len(diff.unmatchable) == 1
        assert diff.unmatchable[0].object_id is None

    def test_same_account_no_object_id_is_new_not_match(self):
        entry = _entry(object_id=None, account="account1")
        local = _local(external_id="obj-1", username="account1")
        diff = compute_diff([entry], [local])
        assert len(diff.new) == 0
        assert len(diff.unmatchable) == 1

    def test_same_display_name_no_object_id_is_new_not_match(self):
        entry = _entry(object_id=None, display_name="Display Name")
        local = _local(external_id="obj-1", display_name="Display Name")
        diff = compute_diff([entry], [local])
        assert len(diff.new) == 0
        assert len(diff.unmatchable) == 1


class TestUnmatchable:
    def test_entry_without_object_id_is_unmatchable(self):
        entry = _entry(object_id=None)
        diff = compute_diff([entry], [])
        assert diff.unmatchable == (entry,)
        assert diff.new == ()

    def test_multiple_entries_without_object_id_all_unmatchable(self):
        entries = [_entry(object_id=None, account="a1"), _entry(object_id=None, account="a2")]
        diff = compute_diff(entries, [])
        assert len(diff.unmatchable) == 2


class TestAmbiguous:
    def test_duplicate_object_id_in_directory_is_ambiguous(self):
        entries = [_entry(object_id="obj-1", account="a1"), _entry(object_id="obj-1", account="a2")]
        diff = compute_diff(entries, [])
        assert len(diff.ambiguous) == 2
        assert diff.new == ()

    def test_duplicate_external_id_in_locals_makes_entry_ambiguous(self):
        entry = _entry(object_id="obj-1")
        locals_ = [_local(id=1, external_id="obj-1"), _local(id=2, external_id="obj-1")]
        diff = compute_diff([entry], locals_)
        assert len(diff.ambiguous) == 1
        assert diff.ambiguous[0].object_id == "obj-1"
        assert diff.new == ()
        assert diff.changed == ()
        assert diff.missing_in_directory == ()


class TestChanged:
    def test_display_name_change_detected(self):
        entry = _entry(object_id="obj-1", display_name="New Name")
        local = _local(external_id="obj-1", display_name="Old Name")
        diff = compute_diff([entry], [local])
        assert len(diff.changed) == 1
        local_u, entry_u, fields = diff.changed[0]
        assert "display_name" in fields

    def test_enabled_change_detected(self):
        entry = _entry(object_id="obj-1", enabled=False)
        local = _local(external_id="obj-1", active=True)
        diff = compute_diff([entry], [local])
        assert len(diff.changed) == 1
        _, _, fields = diff.changed[0]
        assert "enabled" in fields

    def test_both_changes_detected(self):
        entry = _entry(object_id="obj-1", display_name="New", enabled=False)
        local = _local(external_id="obj-1", display_name="Old", active=True)
        diff = compute_diff([entry], [local])
        assert len(diff.changed) == 1
        _, _, fields = diff.changed[0]
        assert fields == ("display_name", "enabled")

    def test_no_change_when_fields_match(self):
        entry = _entry(object_id="obj-1", display_name="Same", enabled=True)
        local = _local(external_id="obj-1", display_name="Same", active=True)
        diff = compute_diff([entry], [local])
        assert diff.changed == ()
        assert diff.disabled_in_directory == ()

    def test_display_name_nfc_and_whitespace_collapse(self):
        # NFD vs NFC
        entry = _entry(object_id="obj-1", display_name="Cafe\u0301")
        local = _local(external_id="obj-1", display_name="Caf\u00e9")
        diff = compute_diff([entry], [local])
        assert "display_name" not in [f for _, _, fields in diff.changed for f in fields]

        # Whitespace collapse
        entry = _entry(object_id="obj-1", display_name="  John   Doe  ")
        local = _local(external_id="obj-1", display_name="John Doe")
        diff = compute_diff([entry], [local])
        assert "display_name" not in [f for _, _, fields in diff.changed for f in fields]

    def test_display_name_case_sensitive(self):
        entry = _entry(object_id="obj-1", display_name="John Doe")
        local = _local(external_id="obj-1", display_name="john doe")
        diff = compute_diff([entry], [local])
        assert len(diff.changed) == 1
        _, _, fields = diff.changed[0]
        assert "display_name" in fields


class TestDisabledInDirectory:
    def test_enabled_false_matched_to_active_local_is_disabled_in_directory(self):
        entry = _entry(object_id="obj-1", enabled=False)
        local = _local(external_id="obj-1", active=True)
        diff = compute_diff([entry], [local])
        assert len(diff.disabled_in_directory) == 1
        assert diff.disabled_in_directory[0].id == local.id

    def test_enabled_true_not_in_disabled_in_directory(self):
        entry = _entry(object_id="obj-1", enabled=True)
        local = _local(external_id="obj-1", active=True)
        diff = compute_diff([entry], [local])
        assert diff.disabled_in_directory == ()

    def test_local_already_inactive_not_in_disabled_in_directory(self):
        entry = _entry(object_id="obj-1", enabled=False)
        local = _local(external_id="obj-1", active=False)
        diff = compute_diff([entry], [local])
        assert diff.disabled_in_directory == ()


class TestMissingInDirectory:
    def test_local_with_external_id_not_in_directory_is_missing(self):
        local = _local(external_id="obj-1")
        diff = compute_diff([], [local])
        assert len(diff.missing_in_directory) == 1
        assert diff.missing_in_directory[0].id == local.id

    def test_local_without_external_id_never_reported(self):
        local = _local(external_id=None)
        diff = compute_diff([], [local])
        assert diff.missing_in_directory == ()
        assert diff.new == ()
        assert diff.changed == ()
        assert diff.disabled_in_directory == ()

    def test_multiple_locals_with_external_id_all_reported(self):
        locals_ = [_local(id=1, external_id="obj-1"), _local(id=2, external_id="obj-2")]
        diff = compute_diff([], locals_)
        assert len(diff.missing_in_directory) == 2


class TestNew:
    def test_entry_without_matching_local_is_new(self):
        entry = _entry(object_id="obj-1")
        diff = compute_diff([entry], [])
        assert diff.new == (entry,)

    def test_entry_with_object_id_not_in_locals_is_new(self):
        entry = _entry(object_id="obj-1")
        local = _local(external_id="obj-2")
        diff = compute_diff([entry], [local])
        assert diff.new == (entry,)
        assert diff.missing_in_directory == (local,)


class TestEmptyInputs:
    def test_empty_entries_and_locals_gives_empty_diff(self):
        diff = compute_diff([], [])
        assert diff == Diff(
            new=(),
            changed=(),
            disabled_in_directory=(),
            missing_in_directory=(),
            ambiguous=(),
            unmatchable=(),
        )


class TestConfirmedActions:
    def test_empty_confirmation_returns_empty_list(self):
        diff = compute_diff([_entry(object_id="obj-1")], [])
        actions = confirmed_actions(diff, [])
        assert actions == []

    def test_confirmed_new_creates_create_pending_user(self):
        entry = _entry(object_id="obj-1")
        diff = compute_diff([entry], [])
        actions = confirmed_actions(diff, ["obj-1"])
        assert len(actions) == 1
        assert actions[0].kind == "create_pending_user"
        assert actions[0].object_id == "obj-1"
        assert actions[0].local_user_id is None

    def test_confirmed_display_name_change_creates_update_display_name(self):
        entry = _entry(object_id="obj-1", display_name="New Name")
        local = _local(external_id="obj-1", display_name="Old Name")
        diff = compute_diff([entry], [local])
        actions = confirmed_actions(diff, ["obj-1"])
        assert len(actions) == 1
        assert actions[0].kind == "update_display_name"
        assert actions[0].object_id == "obj-1"
        assert actions[0].local_user_id == local.id

    def test_confirmed_disabled_creates_deactivate(self):
        entry = _entry(object_id="obj-1", enabled=False)
        local = _local(external_id="obj-1", active=True)
        diff = compute_diff([entry], [local])
        actions = confirmed_actions(diff, ["obj-1"])
        assert len(actions) == 1
        assert actions[0].kind == "deactivate"
        assert actions[0].object_id == "obj-1"
        assert actions[0].local_user_id == local.id

    def test_unconfirmed_ids_ignored(self):
        entry = _entry(object_id="obj-1")
        diff = compute_diff([entry], [])
        actions = confirmed_actions(diff, ["obj-999"])
        assert actions == []

    def test_confirmed_id_not_in_diff_ignored_no_error(self):
        entry = _entry(object_id="obj-1")
        diff = compute_diff([entry], [])
        actions = confirmed_actions(diff, ["obj-1", "obj-999"])
        assert len(actions) == 1

    def test_actions_sorted_by_kind_then_object_id(self):
        entry1 = _entry(object_id="obj-a", display_name="New A")
        entry2 = _entry(object_id="obj-b", enabled=False)
        local2 = _local(id=2, external_id="obj-b", active=True)
        diff = compute_diff([entry1, entry2], [local2])
        actions = confirmed_actions(diff, ["obj-a", "obj-b"])
        # create_pending_user < deactivate < update_display_name alphabetically
        assert actions[0].kind == "create_pending_user"
        assert actions[1].kind == "deactivate"
        assert actions[0].object_id == "obj-a"
        assert actions[1].object_id == "obj-b"

    def test_action_kinds_tuple_has_exactly_three_members(self):
        # This tests that only the three documented action kinds are produced
        entry_new = _entry(object_id="obj-new")
        entry_changed = _entry(object_id="obj-changed", display_name="New")
        local_changed = _local(external_id="obj-changed", display_name="Old")
        entry_disabled = _entry(object_id="obj-disabled", enabled=False)
        local_disabled = _local(external_id="obj-disabled", active=True)
        diff = compute_diff([entry_new, entry_changed, entry_disabled], [local_changed, local_disabled])
        actions = confirmed_actions(diff, ["obj-new", "obj-changed", "obj-disabled"])
        kinds = {a.kind for a in actions}
        assert kinds == {"create_pending_user", "update_display_name", "deactivate"}

    def test_no_action_for_missing_in_directory(self):
        local = _local(external_id="obj-1")
        diff = compute_diff([], [local])
        actions = confirmed_actions(diff, ["obj-1"])
        assert actions == []

    def test_no_action_for_ambiguous(self):
        entries = [_entry(object_id="obj-1"), _entry(object_id="obj-1")]
        diff = compute_diff(entries, [])
        actions = confirmed_actions(diff, ["obj-1"])
        assert actions == []

    def test_no_action_for_unmatchable(self):
        entry = _entry(object_id=None)
        diff = compute_diff([entry], [])
        actions = confirmed_actions(diff, [])
        assert actions == []

    def test_confirmed_actions_does_not_mutate_diff(self):
        entry = _entry(object_id="obj-1")
        diff = compute_diff([entry], [])
        original_new = diff.new
        confirmed_actions(diff, ["obj-1"])
        assert diff.new is original_new


class TestInputsNotMutated:
    def test_entries_not_mutated(self):
        entry = _entry(object_id="obj-1", display_name="Original")
        local = _local(external_id="obj-1", display_name="Local")
        compute_diff([entry], [local])
        assert entry.display_name == "Original"

    def test_locals_not_mutated(self):
        entry = _entry(object_id="obj-1", display_name="Entry")
        local = _local(external_id="obj-1", display_name="Original")
        compute_diff([entry], [local])
        assert local.display_name == "Original"


class TestNoForbiddenImports:
    def test_module_has_no_database_imports(self):
        import src.clinic.directory_reconcile as mod
        import inspect

        source = inspect.getsource(mod)
        # Check for actual import statements, not string literals in forbidden set
        lines = source.split('\n')
        import_lines = [line.strip() for line in lines if line.strip().startswith(('import ', 'from '))]
        import_text = '\n'.join(import_lines)
        assert "import sqlalchemy" not in import_text
        assert "from sqlalchemy" not in import_text
        assert "import flask" not in import_text
        assert "from flask" not in import_text
        assert "import ldap" not in import_text
        assert "from ldap" not in import_text
        assert "import ldap3" not in import_text
        assert "from ldap3" not in import_text
        assert "import requests" not in import_text
        assert "from requests" not in import_text
        # urllib and http.client could be stdlib but check for actual imports
        assert "import urllib" not in import_text
        assert "from urllib" not in import_text
        assert "import http.client" not in import_text
        assert "from http.client" not in import_text


class TestPerformance:
    def test_5000_entries_under_2_seconds(self):
        entries = [_entry(object_id=f"obj-{i}", account=f"account{i}") for i in range(5000)]
        locals_ = [_local(id=i, external_id=f"obj-{i}", username=f"user{i}") for i in range(5000)]

        start = time.perf_counter()
        diff = compute_diff(entries, locals_)
        elapsed = time.perf_counter() - start

        assert elapsed < 2.0
        assert diff.new == ()
        assert diff.changed == ()
        assert diff.disabled_in_directory == ()
        assert diff.missing_in_directory == ()
        assert diff.ambiguous == ()
        assert diff.unmatchable == ()

    def test_5000_new_entries_under_2_seconds(self):
        entries = [_entry(object_id=f"obj-{i}", account=f"account{i}") for i in range(5000)]

        start = time.perf_counter()
        diff = compute_diff(entries, [])
        elapsed = time.perf_counter() - start

        assert elapsed < 2.0
        assert len(diff.new) == 5000

    def test_5000_locals_missing_under_2_seconds(self):
        locals_ = [_local(id=i, external_id=f"obj-{i}", username=f"user{i}") for i in range(5000)]

        start = time.perf_counter()
        diff = compute_diff([], locals_)
        elapsed = time.perf_counter() - start

        assert elapsed < 2.0
        assert len(diff.missing_in_directory) == 5000