"""Service rules for the clinic last selection and user switch.

Acceptance criteria from PAR-ADM-011 'Benutzerstart, letzte Auswahl und Wechsel'.
"""

import pytest

from src.clinic.last_selection import (
    Allowed,
    LastSelectionError,
    Restored,
    Selection,
    SelectionStore,
    deserialize,
    restore,
    serialize,
)


def test_restore_fully_allowed_returns_unchanged():
    """A saved selection fully inside allowed is returned unchanged with fallback == ()."""
    saved = Selection(role="author", author_id=1, pool_id="pool-a")
    allowed = Allowed(
        roles=frozenset(["author", "typist"]),
        author_ids=frozenset([1, 2]),
        pool_ids=frozenset(["pool-a", "pool-b"]),
        own_author_id=1,
    )
    result = restore(saved, allowed)
    assert result.selection == saved
    assert result.fallback == ()


def test_restore_author_no_longer_allowed_falls_back_to_own_author():
    """Author no longer allowed: falls back to own_author_id and reports author_not_allowed."""
    saved = Selection(role="author", author_id=99, pool_id="pool-a")
    allowed = Allowed(
        roles=frozenset(["author", "typist"]),
        author_ids=frozenset([1, 2]),
        pool_ids=frozenset(["pool-a", "pool-b"]),
        own_author_id=1,
    )
    result = restore(saved, allowed)
    assert result.selection.role == "author"
    assert result.selection.author_id == 1
    assert result.selection.pool_id == "pool-a"
    assert "author_not_allowed" in result.fallback


def test_restore_author_no_longer_allowed_no_own_author_becomes_none():
    """If there is no own author it becomes None and reports author_not_allowed."""
    saved = Selection(role="author", author_id=99, pool_id="pool-a")
    allowed = Allowed(
        roles=frozenset(["author", "typist"]),
        author_ids=frozenset([1, 2]),
        pool_ids=frozenset(["pool-a", "pool-b"]),
        own_author_id=None,
    )
    result = restore(saved, allowed)
    assert result.selection.author_id is None
    assert "author_not_allowed" in result.fallback


def test_restore_pool_no_longer_allowed_becomes_none():
    """A pool no longer allowed becomes None and reports pool_not_allowed."""
    saved = Selection(role="author", author_id=1, pool_id="pool-x")
    allowed = Allowed(
        roles=frozenset(["author", "typist"]),
        author_ids=frozenset([1, 2]),
        pool_ids=frozenset(["pool-a", "pool-b"]),
        own_author_id=1,
    )
    result = restore(saved, allowed)
    assert result.selection.pool_id is None
    assert "pool_not_allowed" in result.fallback


def test_restore_role_no_longer_held_falls_back_to_lexicographically_first():
    """A role the user no longer holds falls back to the lexicographically first held role."""
    saved = Selection(role="typist", author_id=1, pool_id="pool-a")
    allowed = Allowed(
        roles=frozenset(["author"]),  # only "author" now
        author_ids=frozenset([1, 2]),
        pool_ids=frozenset(["pool-a", "pool-b"]),
        own_author_id=1,
    )
    result = restore(saved, allowed)
    assert result.selection.role == "author"
    assert "role_not_allowed" in result.fallback


def test_restore_no_roles_raises_value_error():
    """No roles at all raises ValueError."""
    saved = Selection(role="author", author_id=1, pool_id="pool-a")
    allowed = Allowed(
        roles=frozenset(),
        author_ids=frozenset([1, 2]),
        pool_ids=frozenset(["pool-a", "pool-b"]),
        own_author_id=1,
    )
    with pytest.raises(ValueError):
        restore(saved, allowed)


def test_restore_result_always_subset_of_allowed():
    """The result is always a subset of allowed (property check over combinations)."""
    # Test 12 combinations of saved vs allowed
    test_cases = [
        # (saved_role, saved_author, saved_pool, allowed_roles, allowed_authors, allowed_pools, own_author)
        ("author", 1, "p1", frozenset(["author"]), frozenset([1]), frozenset(["p1"]), 1),
        ("author", 1, "p1", frozenset(["author"]), frozenset([2]), frozenset(["p1"]), 1),
        ("author", 1, "p1", frozenset(["author"]), frozenset([1]), frozenset(["p2"]), 1),
        ("author", 1, "p1", frozenset(["typist"]), frozenset([1]), frozenset(["p1"]), 1),
        ("typist", 1, "p1", frozenset(["author", "typist"]), frozenset([1]), frozenset(["p1"]), 1),
        ("author", 99, "p1", frozenset(["author"]), frozenset([1]), frozenset(["p1"]), 1),
        ("author", 1, "p99", frozenset(["author"]), frozenset([1]), frozenset(["p1"]), 1),
        ("author", None, None, frozenset(["author"]), frozenset([1]), frozenset(["p1"]), 1),
        ("typist", None, None, frozenset(["typist"]), frozenset([1]), frozenset(["p1"]), 1),
        ("author", 1, "p1", frozenset(["author", "typist"]), frozenset([1, 2]), frozenset(["p1", "p2"]), 1),
        ("typist", 2, "p2", frozenset(["author", "typist"]), frozenset([1, 2]), frozenset(["p1", "p2"]), 1),
        ("author", 99, "p99", frozenset(["author", "typist"]), frozenset([1, 2]), frozenset(["p1", "p2"]), None),
    ]

    for saved_role, saved_author, saved_pool, allowed_roles, allowed_authors, allowed_pools, own_author in test_cases:
        saved = Selection(role=saved_role, author_id=saved_author, pool_id=saved_pool)
        allowed = Allowed(
            roles=allowed_roles,
            author_ids=allowed_authors,
            pool_ids=allowed_pools,
            own_author_id=own_author,
        )
        result = restore(saved, allowed)

        # Check result is subset of allowed
        assert result.selection.role in allowed.roles
        if result.selection.author_id is not None:
            assert result.selection.author_id in allowed.author_ids
        if result.selection.pool_id is not None:
            assert result.selection.pool_id in allowed.pool_ids


def test_restore_none_gives_default():
    """restore(None, allowed) gives the default: role 'author' if held else 'typist', own author, no pool."""
    # Default when 'author' role is held
    allowed_with_author = Allowed(
        roles=frozenset(["author", "typist"]),
        author_ids=frozenset([1, 2]),
        pool_ids=frozenset(["pool-a", "pool-b"]),
        own_author_id=5,
    )
    result = restore(None, allowed_with_author)
    assert result.selection.role == "author"
    assert result.selection.author_id == 5
    assert result.selection.pool_id is None
    assert result.fallback == ()

    # Default when only 'typist' role is held
    allowed_without_author = Allowed(
        roles=frozenset(["typist"]),
        author_ids=frozenset([1, 2]),
        pool_ids=frozenset(["pool-a", "pool-b"]),
        own_author_id=5,
    )
    result = restore(None, allowed_without_author)
    assert result.selection.role == "typist"
    assert result.selection.author_id == 5
    assert result.selection.pool_id is None
    assert result.fallback == ()


def test_deserialize_returns_none_for_various_invalid_inputs():
    """deserialize returns None for None, empty string, invalid JSON, a list, unknown role, wrong types."""
    assert deserialize(None) is None
    assert deserialize("") is None
    assert deserialize("not json") is None
    assert deserialize("[]") is None
    assert deserialize('{"role": "unknown"}') is None
    assert deserialize('{"role": "author", "author_id": "not int"}') is None
    assert deserialize('{"role": "author", "pool_id": ""}') is None
    assert deserialize('{"role": "author", "author_id": true}') is None
    assert deserialize('{"role": "author", "extra": "key"}') is None


def test_deserialize_serialize_roundtrip():
    """deserialize(serialize(s)) == s."""
    original = Selection(role="author", author_id=42, pool_id="pool-x")
    text = serialize(original)
    restored = deserialize(text)
    assert restored == original

    # Also test with None values
    original2 = Selection(role="typist", author_id=None, pool_id=None)
    text2 = serialize(original2)
    restored2 = deserialize(text2)
    assert restored2 == original2

    original3 = Selection(role="author", author_id=1, pool_id=None)
    text3 = serialize(original3)
    restored3 = deserialize(text3)
    assert restored3 == original3


def test_selection_store_remember_and_start_context():
    """SelectionStore.remember stores and start_context restores against allowances."""
    store = SelectionStore()
    user_id = 10
    selection = Selection(role="author", author_id=5, pool_id="pool-a")
    allowed = Allowed(
        roles=frozenset(["author", "typist"]),
        author_ids=frozenset([5, 6]),
        pool_ids=frozenset(["pool-a", "pool-b"]),
        own_author_id=5,
    )

    store.remember(user_id, selection)
    result = store.start_context(user_id, allowed)
    assert result.selection == selection
    assert result.fallback == ()


def test_selection_store_start_context_without_stored_selection_uses_default():
    """start_context with no stored selection uses the default."""
    store = SelectionStore()
    user_id = 10
    allowed = Allowed(
        roles=frozenset(["author", "typist"]),
        author_ids=frozenset([5, 6]),
        pool_ids=frozenset(["pool-a", "pool-b"]),
        own_author_id=5,
    )

    result = store.start_context(user_id, allowed)
    assert result.selection.role == "author"
    assert result.selection.author_id == 5
    assert result.selection.pool_id is None
    assert result.fallback == ()


def test_selection_store_switch_user_forgets_nothing_about_previous_user():
    """switch_user forgets nothing about the previous user but new user starts from their own."""
    store = SelectionStore()
    user_a = 1
    user_b = 2

    # User A has a selection
    sel_a = Selection(role="author", author_id=10, pool_id="pool-a")
    allowed_a = Allowed(
        roles=frozenset(["author", "typist"]),
        author_ids=frozenset([10, 11]),
        pool_ids=frozenset(["pool-a", "pool-b"]),
        own_author_id=10,
    )
    store.remember(user_a, sel_a)

    # User B has a different selection
    sel_b = Selection(role="typist", author_id=20, pool_id="pool-b")
    allowed_b = Allowed(
        roles=frozenset(["author", "typist"]),
        author_ids=frozenset([20, 21]),
        pool_ids=frozenset(["pool-a", "pool-b"]),
        own_author_id=20,
    )
    store.remember(user_b, sel_b)

    # Switch from A to B
    result = store.switch_user(user_a, user_b, allowed_b)
    assert result.selection == sel_b
    assert result.fallback == ()

    # User A's selection is still there
    result_a = store.start_context(user_a, allowed_a)
    assert result_a.selection == sel_a


def test_selection_store_switch_user_new_user_different_allowed():
    """Switch to a user with different allowed set - new user never gets previous user's selection."""
    store = SelectionStore()
    user_a = 1
    user_b = 2

    # User A has author role and author_id 10
    sel_a = Selection(role="author", author_id=10, pool_id="pool-a")
    allowed_a = Allowed(
        roles=frozenset(["author"]),
        author_ids=frozenset([10]),
        pool_ids=frozenset(["pool-a"]),
        own_author_id=10,
    )
    store.remember(user_a, sel_a)

    # User B has only typist role, different author_ids, no pool_a
    allowed_b = Allowed(
        roles=frozenset(["typist"]),
        author_ids=frozenset([20]),
        pool_ids=frozenset(["pool-b"]),
        own_author_id=20,
    )
    # User B has no stored selection

    # Switch from A to B
    result = store.switch_user(user_a, user_b, allowed_b)
    # Should get default for B: role 'typist' (only role), own_author_id 20, no pool
    assert result.selection.role == "typist"
    assert result.selection.author_id == 20
    assert result.selection.pool_id is None
    assert "role_not_allowed" not in result.fallback  # default, not a fallback from saved

    # User A's selection unchanged
    result_a = store.start_context(user_a, allowed_a)
    assert result_a.selection == sel_a


def test_user_ids_must_be_positive_ints_no_bool():
    """User ids are positive ints (no bool)."""
    store = SelectionStore()
    selection = Selection(role="author", author_id=1, pool_id=None)
    allowed = Allowed(
        roles=frozenset(["author"]),
        author_ids=frozenset([1]),
        pool_ids=frozenset(),
        own_author_id=1,
    )

    # Valid positive ints
    store.remember(1, selection)
    store.start_context(1, allowed)
    store.switch_user(1, 2, allowed)

    # Invalid: zero
    with pytest.raises(LastSelectionError) as exc:
        store.remember(0, selection)
    assert exc.value.code == "invalid_user_id"

    # Invalid: negative
    with pytest.raises(LastSelectionError) as exc:
        store.remember(-1, selection)
    assert exc.value.code == "invalid_user_id"

    # Invalid: bool (True == 1 but is bool)
    with pytest.raises(LastSelectionError) as exc:
        store.remember(True, selection)
    assert exc.value.code == "invalid_user_id"

    with pytest.raises(LastSelectionError) as exc:
        store.remember(False, selection)
    assert exc.value.code == "invalid_user_id"

    # Invalid: string
    with pytest.raises(LastSelectionError) as exc:
        store.remember("1", selection)  # type: ignore[arg-type]
    assert exc.value.code == "invalid_user_id"


def test_validate_selection_rejects_invalid_role():
    """Selection with invalid role raises LastSelectionError."""
    with pytest.raises(LastSelectionError) as exc:
        Selection(role="invalid", author_id=1, pool_id=None)
    assert exc.value.code == "invalid_role"


def test_validate_selection_rejects_invalid_author_id_type():
    """Selection with invalid author_id type raises LastSelectionError."""
    with pytest.raises(LastSelectionError) as exc:
        Selection(role="author", author_id="not int", pool_id=None)
    assert exc.value.code == "invalid_author_id"

    with pytest.raises(LastSelectionError) as exc:
        Selection(role="author", author_id=True, pool_id=None)
    assert exc.value.code == "invalid_author_id"


def test_validate_selection_rejects_empty_pool_id():
    """Selection with empty pool_id raises LastSelectionError."""
    with pytest.raises(LastSelectionError) as exc:
        Selection(role="author", author_id=1, pool_id="")
    assert exc.value.code == "invalid_pool_id"


def test_validate_allowed_rejects_invalid_structure():
    """Allowed with invalid structure raises LastSelectionError."""
    with pytest.raises(LastSelectionError) as exc:
        Allowed(
            roles=set(["author"]),  # not frozenset
            author_ids=frozenset([1]),
            pool_ids=frozenset(),
            own_author_id=1,
        )
    assert exc.value.code == "invalid_roles"

    with pytest.raises(LastSelectionError) as exc:
        Allowed(
            roles=frozenset(["author"]),
            author_ids=set([1]),  # not frozenset
            pool_ids=frozenset(),
            own_author_id=1,
        )
    assert exc.value.code == "invalid_author_ids"

    with pytest.raises(LastSelectionError) as exc:
        Allowed(
            roles=frozenset(["author"]),
            author_ids=frozenset([1]),
            pool_ids=set(["p1"]),  # not frozenset
            own_author_id=1,
        )
    assert exc.value.code == "invalid_pool_ids"

    with pytest.raises(LastSelectionError) as exc:
        Allowed(
            roles=frozenset(["invalid_role"]),
            author_ids=frozenset([1]),
            pool_ids=frozenset(),
            own_author_id=1,
        )
    assert exc.value.code == "invalid_role"

    with pytest.raises(LastSelectionError) as exc:
        Allowed(
            roles=frozenset(["author"]),
            author_ids=frozenset([True]),  # bool in author_ids
            pool_ids=frozenset(),
            own_author_id=1,
        )
    assert exc.value.code == "invalid_author_id_in_allowed"

    with pytest.raises(LastSelectionError) as exc:
        Allowed(
            roles=frozenset(["author"]),
            author_ids=frozenset([1]),
            pool_ids=frozenset([""]),  # empty string in pool_ids
            own_author_id=1,
        )
    assert exc.value.code == "invalid_pool_id_in_allowed"


def test_serialize_rejects_invalid_selection():
    """serialize rejects invalid Selection."""
    with pytest.raises(LastSelectionError):
        serialize("not a selection")  # type: ignore[arg-type]


def test_restore_rejects_invalid_allowed():
    """restore rejects invalid Allowed."""
    with pytest.raises(LastSelectionError):
        restore(None, "not allowed")  # type: ignore[arg-type]


def test_restore_rejects_invalid_saved():
    """restore rejects invalid saved Selection."""
    allowed = Allowed(
        roles=frozenset(["author"]),
        author_ids=frozenset([1]),
        pool_ids=frozenset(),
        own_author_id=1,
    )
    with pytest.raises(LastSelectionError):
        restore("not a selection", allowed)  # type: ignore[arg-type]