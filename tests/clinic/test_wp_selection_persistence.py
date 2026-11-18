"""User-scoped persistence for the last selection (PAR-ADM-011).

BT-ADM-011 stays in test_last_selection.py. This file covers restart,
access revalidation, account switch, a stale preference version and one retry.
"""

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.clinic.last_selection import (
    Allowed,
    LastSelectionError,
    PreferenceUnavailable,
    PresetUserPreferences,
    Selection,
    SelectionStore,
    decode_preference,
)
from src.clinic.presets import create_tables


def _allowed(**overrides) -> Allowed:
    fields = dict(
        roles=frozenset(["author", "typist"]),
        author_ids=frozenset([5, 6]),
        pool_ids=frozenset(["pool-a", "pool-b"]),
        own_author_id=5,
        source_ids=frozenset(["src-a", "src-b"]),
        template_ids=frozenset(["tmpl-a", "tmpl-b"]),
    )
    fields.update(overrides)
    return Allowed(**fields)


def _selection(**overrides) -> Selection:
    fields = dict(
        role="author", author_id=5, pool_id="pool-a", source_id="src-a", template_id="tmpl-a",
    )
    fields.update(overrides)
    return Selection(**fields)


class FlakyPreferences:
    """In-memory user preferences that fail a configured number of calls."""

    def __init__(self) -> None:
        self.rows: dict[int, str] = {}
        self.fail_gets = 0
        self.fail_puts = 0
        self.gets = 0
        self.puts = 0
        self.store: SelectionStore | None = None
        self.client_at_get: tuple | None = None

    def get(self, user_id: int) -> str | None:
        self.gets += 1
        if self.store is not None:
            self.client_at_get = (self.store.client_user_id(), self.store.client_selection())
        if self.gets <= self.fail_gets:
            raise PreferenceUnavailable("read failed")
        return self.rows.get(user_id)

    def put(self, user_id: int, payload: str) -> None:
        self.puts += 1
        if self.puts <= self.fail_puts:
            raise PreferenceUnavailable("write failed")
        self.rows[user_id] = payload


def test_preference_survives_store_restart():
    prefs = FlakyPreferences()
    store = SelectionStore(preferences=prefs)
    selection = _selection()
    store.remember(5, selection)

    restarted = SelectionStore(preferences=prefs)
    result = restarted.start_context(5, _allowed())
    assert result.selection == selection
    assert result.fallback == ()
    assert restarted.client_user_id() == 5
    assert restarted.client_selection() == selection


def test_preset_rows_survive_restart_per_user(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'selection.db').as_posix()}")
    create_tables(bind=engine)
    session = sessionmaker(bind=engine)()
    try:
        prefs = PresetUserPreferences(session=session)
        store = SelectionStore(preferences=prefs)
        alpha = _selection()
        beta = _selection(
            role="typist", author_id=6, pool_id="pool-b", source_id="src-b", template_id="tmpl-b",
        )
        store.remember(5, alpha)
        store.remember(6, beta)
        session.commit()
        session.expire_all()

        restarted = SelectionStore(preferences=PresetUserPreferences(session=session))
        assert restarted.start_context(5, _allowed()).selection == alpha
        assert restarted.start_context(6, _allowed()).selection == beta
    finally:
        session.close()
        engine.dispose()


def test_restore_revalidates_author_pool_source_and_template():
    prefs = FlakyPreferences()
    saved = _selection(author_id=99, pool_id="pool-x", source_id="src-x", template_id="tmpl-x")
    SelectionStore(preferences=prefs).remember(5, saved)
    allowed = _allowed()

    result = SelectionStore(preferences=prefs).start_context(5, allowed)
    assert result.selection.role == "author"
    assert result.selection.author_id == 5
    assert result.selection.pool_id is None
    assert result.selection.source_id == "src-a"
    assert result.selection.template_id == "tmpl-a"
    assert result.selection.source_id in allowed.source_ids
    assert result.selection.template_id in allowed.template_ids
    for code in ("author_not_allowed", "pool_not_allowed", "source_not_allowed", "template_not_allowed"):
        assert code in result.fallback
    stored = decode_preference(5, prefs.rows[5])
    assert stored is not None and stored.author_id == 99

    empty = _allowed(source_ids=frozenset(), template_ids=frozenset())
    fallen = SelectionStore(preferences=prefs).start_context(5, empty)
    assert fallen.selection.source_id is None
    assert fallen.selection.template_id is None
    assert "source_not_allowed" in fallen.fallback
    assert "template_not_allowed" in fallen.fallback


def test_stale_version_and_foreign_envelope_are_ignored():
    prefs = FlakyPreferences()
    prefs.rows[5] = json.dumps({
        "version": 0,
        "user_id": 5,
        "selection": {"role": "typist", "author_id": 6, "pool_id": "pool-b"},
    })
    prefs.rows[7] = json.dumps({
        "version": 1,
        "user_id": 6,
        "selection": {"role": "typist", "author_id": 6, "pool_id": "pool-b", "source_id": "src-b"},
    })
    prefs.rows[8] = "{not-json"
    store = SelectionStore(preferences=prefs)
    for user_id in (5, 7, 8):
        result = store.start_context(user_id, _allowed())
        assert result.fallback == ()
        assert result.selection.role == "author"
        assert result.selection.author_id == 5
        assert result.selection.pool_id is None
        assert result.selection.source_id is None
        assert result.selection.template_id is None


def test_switch_clears_client_and_does_not_restore_the_other_user():
    prefs = FlakyPreferences()
    store = SelectionStore(preferences=prefs)
    prefs.store = store
    alpha = _selection()
    store.remember(5, alpha)
    store.start_context(5, _allowed())
    assert store.client_selection() == alpha

    allowed_b = _allowed(
        author_ids=frozenset([8]),
        own_author_id=8,
        pool_ids=frozenset(["pool-b"]),
        source_ids=frozenset(["src-b"]),
        template_ids=frozenset(["tmpl-b"]),
    )
    result = store.switch_user(5, 8, allowed_b)
    assert prefs.client_at_get == (None, None)
    assert result.selection != alpha
    assert result.selection.author_id == 8
    assert result.selection.pool_id is None
    assert store.client_user_id() == 8
    assert store.client_selection() == result.selection
    assert 8 not in prefs.rows

    restarted = SelectionStore(preferences=prefs)
    assert restarted.start_context(5, _allowed()).selection == alpha


def test_write_retries_once_then_leaves_state_unchanged():
    prefs = FlakyPreferences()
    prefs.fail_puts = 1
    store = SelectionStore(preferences=prefs)
    selection = _selection()
    store.remember(5, selection)
    assert prefs.puts == 2
    assert decode_preference(5, prefs.rows[5]) == selection

    prefs.puts = 0
    prefs.fail_puts = 2
    with pytest.raises(LastSelectionError) as exc:
        store.remember(5, _selection(pool_id="pool-b"))
    assert exc.value.code == "preference_write_failed"
    assert decode_preference(5, prefs.rows[5]) == selection
    assert store.start_context(5, _allowed()).selection == selection


def test_read_retries_once_then_fails_closed():
    prefs = FlakyPreferences()
    SelectionStore(preferences=prefs).remember(5, _selection())
    prefs.fail_gets = 1
    restarted = SelectionStore(preferences=prefs)
    assert restarted.start_context(5, _allowed()).selection == _selection()
    assert prefs.gets == 2

    prefs.gets = 0
    prefs.fail_gets = 2
    fresh = SelectionStore(preferences=prefs)
    with pytest.raises(LastSelectionError) as exc:
        fresh.start_context(5, _allowed())
    assert exc.value.code == "preference_read_failed"
    assert fresh.client_selection() is None
    assert fresh.client_user_id() is None
