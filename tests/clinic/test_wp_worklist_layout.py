"""Per-user worklist layout: sort, groups, columns, and account isolation."""

import pytest

from src.clinic.list_layout import LayoutError, group_rows, sort_rows, validate_layout
from src.clinic.worklist_layout import LayoutStore, WorklistLayoutError

ALLOWED = ["author", "status", "deadline", "priority"]


def _column(key, width=140, visible=True):
    return {"key": key, "width": width, "visible": visible}


def _layout(*columns, sort=(), group_by=()):
    return {"version": 1, "columns": list(columns), "sort": list(sort), "group_by": list(group_by)}


def _default():
    return _layout(
        _column("author"),
        _column("status"),
        _column("deadline"),
        _column("priority"),
        sort=[["author", "asc"]],
    )


def _spec():
    return _layout(
        _column("status", 180, False),
        _column("author", 12),
        _column("priority", 900),
        _column("deadline", 40),
        sort=[("author", "desc"), ("priority", "asc")],
        group_by=["author", "priority"],
    )


def _rows():
    return [
        {"id": 3, "author": "apfel", "priority": 0},
        {"id": 2, "author": "Äpfel", "priority": 1},
        {"id": 4, "author": "Äpfel", "priority": 1},
        {"id": 1, "author": "Zebra", "priority": 1},
    ]


def _forbid(user_id, version, text):
    raise AssertionError("wrote")


def test_persists_sort_groups_column_order_and_bounded_widths():
    spec = _spec()
    expected = validate_layout(spec, ALLOWED)
    assert [column["width"] for column in expected["columns"]] == [180, 40, 800, 40]
    assert [column["key"] for column in expected["columns"]] == [
        "status", "author", "priority", "deadline",
    ]

    store = LayoutStore()
    stored = store.save(1, 1, spec, ALLOWED, 0)

    assert stored.version == 1
    assert stored.layout == expected
    assert spec["columns"][1]["width"] == 12
    assert spec["sort"] == [("author", "desc"), ("priority", "asc")]
    stored.layout["columns"][0]["width"] = 1
    loaded = store.load(1, 1, _default(), ALLOWED)
    assert loaded.version == 1
    assert loaded.layout == expected
    assert loaded.layout["sort"] == [["author", "desc"], ["priority", "asc"]]
    assert loaded.layout["group_by"] == ["author", "priority"]
    assert loaded.layout != validate_layout(_default(), ALLOWED)


def test_refresh_keeps_stable_tie_order():
    store = LayoutStore()
    store.save(1, 1, _spec(), ALLOWED, 0)
    rows = _rows()

    stored, ordered, groups = store.refresh(1, 1, rows, _default(), ALLOWED)

    assert ordered == sort_rows(rows, stored.layout["sort"])
    assert groups == group_rows(ordered, stored.layout["group_by"])
    assert [row["id"] for row in ordered] == [1, 2, 4, 3]
    assert [group.key_path[0] for group in groups] == ["Zebra", "Äpfel", "apfel"]
    assert [row["id"] for row in groups[1].rows] == [2, 4]
    assert groups[1].children[0].key_path == ("Äpfel", 1)
    _, ordered_again, groups_again = store.refresh(1, 1, rows, _default(), ALLOWED)
    assert ordered_again == ordered
    assert groups_again == groups
    assert [row["id"] for row in rows] == [3, 2, 4, 1]


def test_save_rejects_unknown_columns_and_oversized_settings():
    wide = _layout(*[_column(str(i)) for i in range(31)])
    obsolete = _layout(_column("author"))
    obsolete["version"] = 2
    huge = "k" * 9000
    cases = [
        (_layout(_column("other")), ALLOWED, LayoutError, "unknown_column"),
        (_layout(_column("author"), sort=[["other", "asc"]]), ALLOWED, LayoutError, "unknown_column"),
        (_layout(_column("author"), group_by=["other"]), ALLOWED, LayoutError, "unknown_column"),
        (wide, [str(i) for i in range(31)], LayoutError, "too_many_columns"),
        (
            _layout(_column("author"), group_by=["author", "status", "deadline", "priority"]),
            ALLOWED,
            LayoutError,
            "too_deep",
        ),
        (_layout(_column(huge)), [huge], WorklistLayoutError, "oversized"),
        (obsolete, ALLOWED, LayoutError, "bad_version"),
    ]
    for spec, allowed, exc, code in cases:
        store = LayoutStore(_forbid)
        with pytest.raises(exc, match=f"^{code}$") as error:
            store.save(1, 1, spec, allowed, 0)
        assert error.value.code == code
        assert store.snapshot(1, 1) is None


def test_schema_change_drops_unavailable_columns_and_keeps_the_rest():
    store = LayoutStore()
    store.save(1, 1, _spec(), ALLOWED, 0)
    allowed = ["author", "status", "pool"]
    default = _layout(
        _column("author", 111),
        _column("status", 140, False),
        _column("pool", 160, False),
    )

    loaded = store.load(1, 1, default, allowed)

    assert loaded.version == 1
    assert loaded.layout == _layout(
        _column("status", 180, False),
        _column("author", 40),
        _column("pool", 160, True),
        sort=[["author", "desc"]],
        group_by=["author"],
    )
    blob = store.snapshot(1, 1)[1]
    assert "priority" in blob
    assert "deadline" in blob


def test_account_switch_never_reuses_the_previous_layout():
    store = LayoutStore()
    first = store.save(1, 1, _spec(), ALLOWED, 0)
    second_spec = _layout(
        _column("author", 200),
        _column("status", 100, False),
        _column("deadline"),
        _column("priority"),
        sort=[["status", "asc"]],
        group_by=["status"],
    )
    second = store.save(2, 2, second_spec, ALLOWED, 0)
    before = store.snapshot(1, 1)

    opened = store.switch_account(1, 2, 1, _default(), ALLOWED)
    other = store.switch_account(2, 1, 2, _default(), ALLOWED)
    fresh = store.switch_account(3, 1, 3, _default(), ALLOWED)

    assert opened.layout == first.layout
    assert other.layout == second.layout
    assert opened.layout != other.layout
    assert fresh.version == 0
    assert fresh.layout == validate_layout(_default(), ALLOWED)
    assert fresh.layout != first.layout
    assert store.snapshot(3, 3) is None
    assert store.snapshot(1, 1) == before


def test_unauthorized_actor_cannot_read_or_write():
    store = LayoutStore()
    saved = store.save(1, 1, _spec(), ALLOWED, 0)
    blob = store.snapshot(1, 1)
    calls = (
        lambda: store.load(2, 1, _default(), ALLOWED),
        lambda: store.save(2, 1, _default(), ALLOWED, 1),
        lambda: store.snapshot(2, 1),
        lambda: store.refresh(2, 1, _rows(), _default(), ALLOWED),
        lambda: store.switch_account(2, 2, 1, _default(), ALLOWED),
        lambda: store.restore_snapshot(2, 1, blob[0], blob[1]),
    )
    for call in calls:
        with pytest.raises(WorklistLayoutError, match="^unauthorized$") as error:
            call()
        assert error.value.code == "unauthorized"
    assert store.snapshot(1, 1) == blob
    assert store.load(1, 1, _default(), ALLOWED).layout == saved.layout
    with pytest.raises(WorklistLayoutError, match="^bad_user$"):
        store.save(True, True, _spec(), ALLOWED, 0)
    with pytest.raises(WorklistLayoutError, match="^bad_user$"):
        store.load(0, 0, _default(), ALLOWED)


def test_stale_version_does_not_overwrite():
    seen = []
    store = LayoutStore(lambda *args: seen.append(args))
    with pytest.raises(WorklistLayoutError, match="^stale_version$"):
        store.save(1, 1, _spec(), ALLOWED, 1)
    assert seen == []
    first = store.save(1, 1, _spec(), ALLOWED, 0)
    with pytest.raises(WorklistLayoutError, match="^stale_version$") as error:
        store.save(1, 1, _default(), ALLOWED, 0)
    assert error.value.code == "stale_version"
    assert len(seen) == 1
    assert store.load(1, 1, _default(), ALLOWED).layout == first.layout
    second = store.save(1, 1, _default(), ALLOWED, 1)
    assert second.version == 2
    assert second.layout == validate_layout(_default(), ALLOWED)
    with pytest.raises(WorklistLayoutError, match="^bad_version$"):
        store.save(1, 1, _default(), ALLOWED, True)
    assert store.snapshot(1, 1)[0] == 2


def test_writer_failure_retries_with_the_same_version():
    calls = {"n": 0}

    def writer(user_id, version, text):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("full")

    store = LayoutStore(writer)
    first = store.save(1, 1, _spec(), ALLOWED, 0)
    with pytest.raises(OSError, match="^full$"):
        store.save(1, 1, _default(), ALLOWED, 1)
    assert store.load(1, 1, _default(), ALLOWED) == first
    retried = store.save(1, 1, _default(), ALLOWED, 1)
    assert retried.version == 2
    assert retried.layout == validate_layout(_default(), ALLOWED)
    assert calls["n"] == 3


def test_restart_restores_only_the_owning_user():
    store = LayoutStore()
    store.save(1, 1, _spec(), ALLOWED, 0)
    version, text = store.snapshot(1, 1)
    second_spec = _layout(
        _column("author", 200),
        _column("status"),
        _column("deadline"),
        _column("priority"),
        sort=[["status", "asc"]],
        group_by=["status"],
    )
    store.save(2, 2, second_spec, ALLOWED, 0)
    version2, text2 = store.snapshot(2, 2)

    fresh = LayoutStore()
    fresh.restore_snapshot(1, 1, version, text)
    fresh.restore_snapshot(1, 1, version, text)
    with pytest.raises(WorklistLayoutError, match="^stale_version$"):
        fresh.restore_snapshot(1, 1, version, text2)
    fresh.restore_snapshot(2, 2, version2, text2)

    assert fresh.load(1, 1, _default(), ALLOWED).layout == store.load(1, 1, _default(), ALLOWED).layout
    assert fresh.load(2, 2, _default(), ALLOWED).layout == validate_layout(second_spec, ALLOWED)
    _, ordered, _groups = fresh.refresh(1, 1, _rows(), _default(), ALLOWED)
    assert [row["id"] for row in ordered] == [1, 2, 4, 3]
    assert fresh.snapshot(1, 1)[1] != fresh.snapshot(2, 2)[1]
