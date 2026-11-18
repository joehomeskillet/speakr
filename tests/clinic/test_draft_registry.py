"""Service rules for the clinic draft registry.

Acceptance criteria from PAR-LIVE-004: several drafts per user, one active
draft per browser tab, version-checked saves instead of last-write-wins,
and a deliberate, confirmed discard that leaves a readable tombstone.
"""

import itertools
from datetime import datetime, timedelta, timezone
from typing import Callable

import pytest

from src.clinic.draft_registry import (
    Draft,
    DraftConflict,
    DraftError,
    DraftRegistry,
)

T0 = datetime(2026, 11, 18, 8, 0, 0, tzinfo=timezone.utc)


def at(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


def counting_ids(prefix: str = "draft-") -> Callable[[], str]:
    counter = itertools.count(1)
    return lambda: f"{prefix}{next(counter)}"


def make_registry() -> DraftRegistry:
    return DraftRegistry(id_factory=counting_ids())


def test_create_starts_at_version_one_with_empty_text():
    draft = make_registry().create(7, "Entwurf A", T0)
    assert draft.id == "draft-1"
    assert draft.owner_id == 7
    assert draft.title == "Entwurf A"
    assert draft.text == ""
    assert draft.version == 1
    assert draft.created_at == T0
    assert draft.updated_at == T0
    assert draft.discarded_at is None


def test_create_keeps_the_given_text():
    draft = make_registry().create(7, "Entwurf", T0, "Erster Satz.")
    assert draft.text == "Erster Satz."


def test_save_returns_next_version_with_new_text_and_timestamp():
    registry = make_registry()
    draft = registry.create(7, "Entwurf", T0)
    saved = registry.save(7, draft.id, "Neuer Text", 1, "tab-1", at(5))
    assert saved.version == 2
    assert saved.text == "Neuer Text"
    assert saved.updated_at == at(5)
    assert saved.created_at == T0


def test_cross_tab_conflict_reports_current_state_and_keeps_the_text():
    registry = make_registry()
    draft = registry.create(7, "Entwurf", T0)
    text_a = "Tab A hat gespeichert."
    registry.save(7, draft.id, text_a, 1, "tab-a", at(1))
    with pytest.raises(DraftConflict) as exc:
        registry.save(7, draft.id, "Tab B ist später.", 1, "tab-b", at(2))
    conflict = exc.value
    assert isinstance(conflict, DraftError)
    assert conflict.code == "version_conflict"
    assert conflict.current_version == 2
    assert conflict.current_text == text_a
    current = registry.list(7)[0]
    assert current.version == 2
    assert current.text == text_a


def test_list_orders_newest_update_first():
    registry = make_registry()
    first = registry.create(7, "Erster", at(0))
    second = registry.create(7, "Zweiter", at(1))
    assert [draft.id for draft in registry.list(7)] == [second.id, first.id]
    registry.save(7, first.id, "aktualisiert", 1, "tab", at(2))
    assert [draft.id for draft in registry.list(7)] == [first.id, second.id]


def test_list_only_shows_own_drafts():
    registry = make_registry()
    registry.create(7, "Eigener", T0)
    registry.create(8, "Fremder", T0)
    assert [draft.owner_id for draft in registry.list(7)] == [7]


def test_two_tabs_hold_different_active_drafts_and_unknown_tab_is_none():
    registry = make_registry()
    first = registry.create(7, "Entwurf 1", T0)
    second = registry.create(7, "Entwurf 2", T0)
    registry.set_active(7, "tab-a", first.id)
    registry.set_active(7, "tab-b", second.id)
    assert registry.active(7, "tab-a") == first
    assert registry.active(7, "tab-b") == second
    assert registry.active(7, "tab-c") is None


def test_active_selection_is_scoped_per_owner():
    registry = make_registry()
    draft = registry.create(7, "Entwurf", T0)
    registry.set_active(7, "tab-1", draft.id)
    assert registry.active(8, "tab-1") is None


def test_discard_without_confirm_changes_nothing():
    registry = make_registry()
    draft = registry.create(7, "Entwurf", T0, "Wichtiger Text")
    registry.set_active(7, "tab-a", draft.id)
    with pytest.raises(DraftError) as exc:
        registry.discard(7, draft.id, confirm=False, now=at(1))
    assert exc.value.code == "confirmation_required"
    assert registry.list(7) == [draft]
    assert registry.list_discarded(7) == []
    assert registry.active(7, "tab-a") == draft
    assert draft.discarded_at is None


def test_confirmed_discard_leaves_readable_tombstone_and_clears_active():
    registry = make_registry()
    draft = registry.create(7, "Entwurf", T0, "Wichtiger Text")
    registry.set_active(7, "tab-a", draft.id)
    registry.set_active(7, "tab-b", draft.id)
    discarded = registry.discard(7, draft.id, confirm=True, now=at(3))
    assert discarded.discarded_at == at(3)
    assert discarded.text == "Wichtiger Text"
    assert registry.list(7) == []
    tombstones = registry.list_discarded(7)
    assert [entry.id for entry in tombstones] == [draft.id]
    assert tombstones[0].text == "Wichtiger Text"
    assert tombstones[0].owner_id == 7
    assert registry.active(7, "tab-a") is None
    assert registry.active(7, "tab-b") is None


def test_save_to_discarded_draft_raises_discarded():
    registry = make_registry()
    draft = registry.create(7, "Entwurf", T0)
    registry.discard(7, draft.id, confirm=True, now=at(1))
    with pytest.raises(DraftError) as exc:
        registry.save(7, draft.id, "Neu", 1, "tab-a", at(2))
    assert exc.value.code == "discarded"


def test_set_active_to_discarded_draft_raises_discarded():
    registry = make_registry()
    draft = registry.create(7, "Entwurf", T0)
    registry.discard(7, draft.id, confirm=True, now=at(1))
    with pytest.raises(DraftError) as exc:
        registry.set_active(7, "tab-a", draft.id)
    assert exc.value.code == "discarded"


def test_discarding_twice_raises_discarded():
    registry = make_registry()
    draft = registry.create(7, "Entwurf", T0)
    registry.discard(7, draft.id, confirm=True, now=at(1))
    with pytest.raises(DraftError) as exc:
        registry.discard(7, draft.id, confirm=True, now=at(2))
    assert exc.value.code == "discarded"


def test_foreign_owner_draft_id_raises_not_found_for_every_entry_point():
    registry = make_registry()
    draft = registry.create(7, "Entwurf", T0)
    operations = [
        lambda: registry.save(8, draft.id, "x", 1, "tab", at(1)),
        lambda: registry.discard(8, draft.id, confirm=True, now=at(1)),
        lambda: registry.set_active(8, "tab", draft.id),
    ]
    for operation in operations:
        with pytest.raises(DraftError) as exc:
            operation()
        assert exc.value.code == "not_found"


def test_unknown_draft_id_for_the_right_owner_is_not_found():
    registry = make_registry()
    with pytest.raises(DraftError) as exc:
        registry.set_active(7, "tab", "missing-id")
    assert exc.value.code == "not_found"


@pytest.mark.parametrize("owner_id", [0, -1, True, False, "7", None, 7.0])
def test_owner_id_must_be_a_positive_int(owner_id):
    with pytest.raises(DraftError) as exc:
        make_registry().create(owner_id, "Entwurf", T0)
    assert exc.value.code == "invalid_owner"


@pytest.mark.parametrize("title", ["", "   ", "\t", "a" * 201, "Zeile\nZwei"])
def test_invalid_title_raises_invalid_title(title):
    with pytest.raises(DraftError) as exc:
        make_registry().create(7, title, T0)
    assert exc.value.code == "invalid_title"


def test_title_of_exactly_200_chars_is_accepted():
    draft = make_registry().create(7, "x" * 200, T0)
    assert len(draft.title) == 200


def test_title_is_nfc_normalised():
    draft = make_registry().create(7, "A\u0308rztlicher Befund", T0)
    assert draft.title == "Ärztlicher Befund"


def test_text_over_the_limit_raises_too_large():
    registry = make_registry()
    draft = registry.create(7, "Entwurf", T0)
    with pytest.raises(DraftError) as exc:
        registry.save(7, draft.id, "a" * 1_000_001, 1, "tab", at(1))
    assert exc.value.code == "too_large"


def test_text_of_exactly_the_limit_is_accepted():
    registry = make_registry()
    draft = registry.create(7, "Entwurf", T0)
    saved = registry.save(7, draft.id, "a" * 1_000_000, 1, "tab", at(1))
    assert saved.version == 2


@pytest.mark.parametrize("text", [None, 42, ["x"]])
def test_text_must_be_a_str(text):
    registry = make_registry()
    draft = registry.create(7, "Entwurf", T0)
    with pytest.raises(DraftError) as exc:
        registry.save(7, draft.id, text, 1, "tab", at(1))
    assert exc.value.code == "invalid_text"


def test_text_is_nfc_normalised_and_newlines_are_unified():
    registry = make_registry()
    draft = registry.create(7, "Entwurf", T0)
    saved = registry.save(7, draft.id, "Cafe\u0301\r\nZwei", 1, "tab", at(1))
    assert saved.text == "Café\nZwei"


@pytest.mark.parametrize("tab_id", ["", "   ", None, 3])
def test_invalid_tab_id_raises_invalid_tab_id(tab_id):
    registry = make_registry()
    draft = registry.create(7, "Entwurf", T0)
    with pytest.raises(DraftError) as exc:
        registry.set_active(7, tab_id, draft.id)
    assert exc.value.code == "invalid_tab_id"


@pytest.mark.parametrize("base_version", [True, "1", 1.0, None])
def test_base_version_must_be_an_int(base_version):
    registry = make_registry()
    draft = registry.create(7, "Entwurf", T0)
    with pytest.raises(DraftError) as exc:
        registry.save(7, draft.id, "x", base_version, "tab", at(1))
    assert exc.value.code == "invalid_version"


def test_confirm_must_be_a_bool():
    registry = make_registry()
    draft = registry.create(7, "Entwurf", T0)
    with pytest.raises(DraftError) as exc:
        registry.discard(7, draft.id, confirm=1, now=at(1))
    assert exc.value.code == "invalid_confirm"


@pytest.mark.parametrize("bad_now", [datetime(2026, 11, 18, 8, 0, 0), "2026-11-18", None])
def test_naive_or_invalid_now_raises_value_error_not_draft_error(bad_now):
    with pytest.raises(ValueError) as exc:
        make_registry().create(7, "Entwurf", bad_now)
    assert not isinstance(exc.value, DraftError)


def test_save_with_naive_now_raises_value_error():
    registry = make_registry()
    draft = registry.create(7, "Entwurf", T0)
    with pytest.raises(ValueError):
        registry.save(7, draft.id, "x", 1, "tab", datetime(2026, 11, 18, 8, 0, 0))


def test_id_factory_is_used_and_duplicate_ids_are_rejected():
    registry = DraftRegistry(id_factory=lambda: "fixed")
    first = registry.create(7, "Eins", T0)
    assert first.id == "fixed"
    with pytest.raises(DraftError) as exc:
        registry.create(7, "Zwei", T0)
    assert exc.value.code == "duplicate_id"


def test_save_does_not_require_or_change_the_active_selection():
    registry = make_registry()
    first = registry.create(7, "Eins", T0)
    second = registry.create(7, "Zwei", T0)
    registry.set_active(7, "tab-a", first.id)
    saved = registry.save(7, second.id, "Text", 1, "tab-a", at(1))
    assert saved.version == 2
    assert registry.active(7, "tab-a") == first
