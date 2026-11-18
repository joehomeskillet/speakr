"""Synthetic, read-only directory reconciliation and explicit application tests."""

from dataclasses import replace

import pytest

from src.clinic.directory_reconcile import Action, DirectoryEntry, LocalUser
from src.clinic.directory_diff import (
    DirectoryDiffError,
    DirectoryMembership,
    DirectorySnapshot,
    LocalMembership,
    MembershipChange,
    apply_preview,
    build_preview,
)


def entry(subject, *, name="Original", email="shared@example.test", enabled=True):
    return DirectoryEntry(subject, f"account-{subject}", name, email, enabled)


def user(user_id, subject, *, name="Original", active=True, username=None):
    return LocalUser(user_id, username or f"local-{user_id}", subject, name, active)


def snapshot(*entries, version="v1", memberships=()):
    return DirectorySnapshot("synthetic-directory", version, tuple(entries), tuple(memberships))


def allow(_actor, _change):
    return True


def test_adapter_is_disabled_by_default_and_preview_has_no_side_effects():
    source = snapshot(entry("sub-1", name="Updated"))
    locals_ = (user(10, "sub-1"),)

    with pytest.raises(DirectoryDiffError, match="disabled") as error:
        build_preview(source, locals_)
    assert error.value.code == "disabled"

    preview = build_preview(source, locals_, enabled=True)
    assert preview.source_version == "v1"
    assert preview.actions[0].kind == "update_display_name"
    assert locals_[0].display_name == "Original"

    with pytest.raises(DirectoryDiffError) as error:
        apply_preview(preview, source, locals_, (), actor_id=2, authorize=allow)
    assert error.value.code == "disabled"

    cancelled = apply_preview(preview, source, locals_, (), actor_id=2, authorize=allow, enabled=True)
    assert cancelled.actions == ()
    assert cancelled.membership_changes == ()
    assert cancelled.local_users == locals_


def test_stable_subject_ids_drive_versioned_add_change_disable_and_never_email():
    source = snapshot(
        entry("new", email="same@example.test"),
        entry("renamed", name="Neuer Anzeigename"),
        entry("disabled", enabled=False),
    )
    locals_ = (
        user(10, None, username="same@example.test"),
        user(20, "renamed"),
        user(30, "disabled"),
    )
    preview = build_preview(source, locals_, enabled=True)

    assert [item.object_id for item in preview.differences.new] == ["new"]
    assert [item[1].object_id for item in preview.differences.changed] == ["renamed", "disabled"]
    assert [item.id for item in preview.differences.disabled_in_directory] == [30]
    assert [(action.kind, action.object_id, action.local_user_id) for action in preview.actions] == [
        ("create_pending_user", "new", None),
        ("deactivate", "disabled", 30),
        ("update_display_name", "renamed", 20),
    ]
    assert locals_[0].external_id is None
    assert locals_[0].username == "same@example.test"


def test_only_selected_actions_change_local_projection_preserving_history_and_ids():
    source = snapshot(
        entry("one", name="Neuer Name", enabled=False),
        entry("new", email="same@example.test"),
    )
    locals_ = (user(40, "one"), user(41, None, username="same@example.test"))
    clinical_history = {40: ["recording-01", "released-transcript-02"]}
    preview = build_preview(source, locals_, enabled=True)
    selected = tuple(action for action in preview.actions if action.kind != "deactivate")

    result = apply_preview(
        preview, source, locals_, (), selected_actions=selected,
        actor_id=7, authorize=allow, enabled=True,
    )

    assert {row.id for row in result.local_users} == {40, 41}
    assert result.local_users[0].display_name == "Neuer Name"
    assert result.local_users[0].active is True
    assert result.local_users[1] == locals_[1]
    assert any(action.kind == "create_pending_user" for action in result.actions)
    assert clinical_history == {40: ["recording-01", "released-transcript-02"]}
    assert locals_[0].display_name == "Original"


def test_membership_changes_are_scoped_selected_and_auditable():
    source = snapshot(
        entry("one"),
        memberships=(DirectoryMembership("one", "staff", "new-staff"),),
    )
    locals_ = (user(40, "one"), user(41, None))
    current = (
        LocalMembership(40, "author", "old-author"),
        LocalMembership(40, "author", "unmanaged"),
        LocalMembership(41, "author", "old-author"),
    )
    preview = build_preview(
        source, locals_, current,
        managed_pools=(("author", "old-author"), ("staff", "new-staff")), enabled=True,
    )
    assert set(preview.membership_changes) == {
        MembershipChange("remove", "one", 40, "author", "old-author"),
        MembershipChange("add", "one", 40, "staff", "new-staff"),
    }

    removed = next(row for row in preview.membership_changes if row.kind == "remove")
    seen = []

    def authorize(actor, change):
        seen.append((actor, change))
        return actor == 5 and change == removed

    result = apply_preview(
        preview, source, locals_, current, selected_memberships=(removed,),
        actor_id=5, authorize=authorize, enabled=True,
    )
    assert seen == [(5, removed)]
    assert result.actor_id == 5
    assert result.membership_changes == (removed,)
    assert result.local_memberships == (
        LocalMembership(41, "author", "old-author"),
        LocalMembership(40, "author", "unmanaged"),
    )
    assert current[0] == LocalMembership(40, "author", "old-author")


def test_membership_changes_need_explicit_application_authority():
    source = snapshot(entry("one"), memberships=(DirectoryMembership("one", "staff", "s1"),))
    locals_ = (user(10, "one"),)
    preview = build_preview(source, locals_, managed_pools=(("staff", "s1"),), enabled=True)

    with pytest.raises(DirectoryDiffError) as error:
        apply_preview(
            preview, source, locals_, (), selected_memberships=preview.membership_changes,
            actor_id=3, enabled=True,
        )
    assert error.value.code == "unauthorized"

    with pytest.raises(DirectoryDiffError) as error:
        apply_preview(
            preview, source, locals_, (), selected_memberships=preview.membership_changes,
            actor_id=3, authorize=lambda _actor, _change: False, enabled=True,
        )
    assert error.value.code == "unauthorized"
    assert preview.local_memberships == ()


def test_last_pool_member_removal_requires_selection_and_rejects_stale_retry():
    source = snapshot(entry("one"))
    locals_ = (user(10, "one"),)
    current = (LocalMembership(10, "author", "only-pool"),)
    preview = build_preview(
        source, locals_, current, managed_pools=(("author", "only-pool"),), enabled=True,
    )
    assert preview.membership_changes == (
        MembershipChange("remove", "one", 10, "author", "only-pool"),
    )

    unchanged = apply_preview(
        preview, source, locals_, current, actor_id=6, authorize=allow, enabled=True,
    )
    assert unchanged.local_memberships == current
    applied = apply_preview(
        preview, source, locals_, current, selected_memberships=preview.membership_changes,
        actor_id=6, authorize=allow, enabled=True,
    )
    assert applied.actor_id == 6
    assert applied.membership_changes == preview.membership_changes
    assert applied.local_memberships == ()

    with pytest.raises(DirectoryDiffError) as error:
        apply_preview(
            preview, source, locals_, applied.local_memberships,
            selected_memberships=preview.membership_changes,
            actor_id=6, authorize=allow, enabled=True,
        )
    assert error.value.code == "stale_state"


@pytest.mark.parametrize(
    ("source_change", "local_change", "expected"),
    [
        (lambda s: replace(s, version="v2"), None, "stale_version"),
        (lambda s: replace(s, source="other"), None, "stale_version"),
        (lambda s: replace(s, entries=(entry("one", name="Different"),)), None, "stale_state"),
        (None, lambda u: (replace(u[0], display_name="Other"),), "stale_state"),
    ],
)
def test_stale_source_versions_contents_and_local_state_are_rejected(source_change, local_change, expected):
    source = snapshot(entry("one", name="Changed"))
    locals_ = (user(10, "one"),)
    preview = build_preview(source, locals_, enabled=True)

    with pytest.raises(DirectoryDiffError) as error:
        apply_preview(
            preview, source_change(source) if source_change else source,
            local_change(locals_) if local_change else locals_, (),
            selected_actions=preview.actions, actor_id=4, authorize=allow, enabled=True,
        )
    assert error.value.code == expected


def test_failed_authorization_is_atomic_and_retry_checks_optimistic_state():
    source = snapshot(entry("one", name="Updated"))
    locals_ = (user(8, "one"),)
    preview = build_preview(source, locals_, enabled=True)

    with pytest.raises(DirectoryDiffError, match="authority denied"):
        apply_preview(
            preview, source, locals_, (), selected_actions=preview.actions,
            actor_id=1, authorize=lambda _actor, _change: False, enabled=True,
        )
    assert locals_[0].display_name == "Original"

    arguments = dict(
        selected_actions=preview.actions, actor_id=1, authorize=allow, enabled=True,
    )
    first = apply_preview(preview, source, locals_, (), **arguments)
    retry = apply_preview(preview, source, locals_, (), **arguments)
    assert first == retry
    assert first.local_users[0].id == 8
    assert first.local_users[0].display_name == "Updated"

    with pytest.raises(DirectoryDiffError) as error:
        apply_preview(preview, source, first.local_users, (), **arguments)
    assert error.value.code == "stale_state"


def test_ambiguous_and_missing_subjects_never_remove_memberships():
    locals_ = (user(8, "duplicate"), user(9, "missing"))
    current = (
        LocalMembership(8, "author", "pool-1"),
        LocalMembership(9, "author", "pool-1"),
    )
    source = snapshot(entry("duplicate"), entry("duplicate", name="Second"))
    preview = build_preview(
        source, locals_, current, managed_pools=(("author", "pool-1"),), enabled=True,
    )
    assert len(preview.differences.ambiguous) == 2
    assert {row.id for row in preview.differences.missing_in_directory} == {8, 9}
    assert preview.membership_changes == ()
    assert preview.actions == ()


def test_unlisted_and_tampered_selections_are_rejected():
    source = snapshot(entry("one", name="Changed"))
    locals_ = (user(8, "one"),)
    preview = build_preview(source, locals_, enabled=True)

    with pytest.raises(DirectoryDiffError) as error:
        apply_preview(
            preview, source, locals_, (),
            selected_actions=(Action("deactivate", "one", 8, "invented"),),
            actor_id=3, authorize=allow, enabled=True,
        )
    assert error.value.code == "invalid_selection"

    with pytest.raises(DirectoryDiffError) as error:
        apply_preview(
            preview, source, locals_, (),
            selected_memberships=(MembershipChange("remove", "one", 8, "author", "x"),),
            actor_id=3, authorize=allow, enabled=True,
        )
    assert error.value.code == "invalid_selection"
