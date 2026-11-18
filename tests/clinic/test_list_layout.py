from datetime import datetime, timezone

import pytest

from src.clinic.list_layout import (
    Group,
    LayoutError,
    group_rows,
    layout_from_json,
    layout_to_json,
    merge_layout,
    sort_rows,
    validate_layout,
)


def _column(key, width=140, visible=True):
    return {"key": key, "width": width, "visible": visible}


def _layout(*columns, sort=(), group_by=()):
    return {"version": 1, "columns": list(columns), "sort": list(sort), "group_by": list(group_by)}


def test_sort_uses_german_friendly_keys_and_is_stable():
    rows = [{"title": title} for title in ("Zebra", "Äpfel", "apfel", "Birne")]
    keys = [("title", "asc")]
    assert [row["title"] for row in sort_rows(rows, keys)] == ["apfel", "Äpfel", "Birne", "Zebra"]

    same_title = {"title": "Birne", "id": 2}
    rows.append(same_title)

    result = sort_rows(rows, keys)

    assert [row["title"] for row in result] == ["apfel", "Äpfel", "Birne", "Birne", "Zebra"]
    assert result[2] is rows[3]
    assert result[3] is same_title
    assert [row["title"] for row in rows] == ["Zebra", "Äpfel", "apfel", "Birne", "Birne"]


@pytest.mark.parametrize(
    ("direction", "expected"),
    [("asc", [128, 1, 0, None, None]), ("desc", [0, 1, 128, None, None])],
)
def test_sort_priorities_and_keep_both_kinds_of_null_last(direction, expected):
    rows = [{"priority": None}, {"priority": 0}, {}, {"priority": 128}, {"priority": 1}]
    result = sort_rows(rows, [("priority", direction)])
    assert [row.get("priority") for row in result] == expected
    assert result[-2:] == [rows[0], rows[2]]


def test_sort_breaks_deadline_ties_with_second_key_including_missing_deadlines():
    early = datetime(2026, 11, 18, 10, tzinfo=timezone.utc)
    late = early.replace(hour=11)
    rows = [
        {"deadline": early, "author": "Anton"},
        {"deadline": early, "author": "Berta"},
        {"deadline": late, "author": "Clara"},
        {"author": "Dora"},
        {"deadline": None, "author": "Eva"},
    ]
    result = sort_rows(rows, [("deadline", "asc"), ("author", "desc")])
    assert result == [rows[i] for i in (1, 0, 2, 4, 3)]


@pytest.mark.parametrize(
    ("key", "direction", "code"),
    [("absent", "asc", "unknown_key"), ("author", "up", "bad_direction"), ("", "asc", "bad_key")],
)
def test_sort_rejects_unknown_or_malformed_keys_and_directions(key, direction, code):
    with pytest.raises(LayoutError, match=f"^{code}$") as error:
        sort_rows([{"author": "Berta"}], [(key, direction)])
    assert error.value.code == code


@pytest.mark.parametrize(
    ("key", "value", "code"),
    [
        ("priority", True, "bad_priority"),
        ("priority", 2, "bad_priority"),
        ("duration", float("nan"), "bad_value"),
        ("duration", float("inf"), "bad_value"),
        ("title", " ", "bad_value"),
        ("title", "A\nB", "bad_value"),
        ("deadline", datetime(2026, 11, 18), "bad_value"),
    ],
)
def test_sort_rejects_invalid_projected_values(key, value, code):
    with pytest.raises(LayoutError, match=f"^{code}$"):
        sort_rows([{key: value}], [(key, "asc")])


def test_group_rows_preserves_first_appearance_and_leaf_order():
    rows = [
        {"status": "offen", "author": "Berta", "id": 1},
        {"status": "fertig", "author": "Anton", "id": 2},
        {"status": "offen", "author": "Anton", "id": 3},
        {"status": "offen", "author": "Berta", "id": 4},
        {"status": "offen", "id": 5},
        {"author": "Berta", "id": 6},
        {"status": "offen", "author": None, "id": 7},
    ]

    groups = group_rows(rows, ["status", "author"])

    assert [group.key_path for group in groups] == [("offen",), ("fertig",), (None,)]
    assert groups[0].rows == tuple(rows[i] for i in (0, 2, 3, 4, 6))
    assert groups[0].children == (
        Group(("offen", "Berta"), (rows[0], rows[3]), ()),
        Group(("offen", "Anton"), (rows[2],), ()),
        Group(("offen", None), (rows[4], rows[6]), ()),
    )
    assert groups[1].children == (Group(("fertig", "Anton"), (rows[1],), ()),)
    assert groups[2].children == (Group((None, "Berta"), (rows[5],), ()),)
    assert sum(len(child.rows) for group in groups for child in group.children) == len(rows)


def test_group_depth_boundaries():
    row = {"status": "offen", "author": "Berta", "priority": 1}
    assert group_rows([row], []) == [Group((), (row,), ())]
    assert group_rows([], []) == [Group((), (), ())]
    group = group_rows([row], ["status", "author", "priority"])[0]
    assert group.children[0].children == (Group(("offen", "Berta", 1), (row,), ()),)
    with pytest.raises(LayoutError, match="^too_deep$"):
        group_rows([row], ["status", "author", "priority", "deadline"])


def test_validate_clamps_widths_and_normalises_sequences_without_mutating_input():
    spec = _layout(_column("author", 2), _column("status", 950, False), sort=[("author", "asc")])

    validated = validate_layout(spec, ["author", "status"])

    assert validated == _layout(_column("author", 40), _column("status", 800, False), sort=[["author", "asc"]])
    assert spec["columns"] == [_column("author", 2), _column("status", 950, False)]
    assert spec["sort"] == [("author", "asc")]


@pytest.mark.parametrize(
    ("spec", "code"),
    [
        (_layout(_column("other")), "unknown_column"),
        (_layout(_column("author"), _column("author")), "duplicate_column"),
        (_layout(*[_column(f"field{i}") for i in range(31)]), "too_many_columns"),
        (_layout(_column("author", visible=1)), "bad_visible"),
        (_layout(_column("author", width=True)), "bad_width"),
        (_layout(_column("author", width=float("nan"))), "bad_width"),
        (_layout(_column("author", width=float("inf"))), "bad_width"),
        (_layout(_column("author\x00")), "bad_key"),
        (_layout(sort=[["other", "asc"]]), "unknown_column"),
        (_layout(group_by=["other"]), "unknown_column"),
        (dict(_layout(), group_by="author"), "bad_group"),
        (dict(_layout(), version=2), "bad_version"),
        (dict(_layout(), version=True), "bad_version"),
        (dict(_layout(), extra={}), "bad_layout"),
    ],
)
def test_validate_rejects_invalid_layouts(spec, code):
    with pytest.raises(LayoutError, match=f"^{code}$"):
        validate_layout(spec, ["author"])


def test_merge_keeps_saved_order_and_drops_retired_references():
    base = _layout(_column("author"), _column("status"), _column("deadline", 230, False))
    saved = _layout(
        _column("status", 180, False),
        _column("retired", 90),
        _column("author", 220),
        sort=[["retired", "asc"], ["status", "desc"]],
        group_by=["retired", "author"],
    )
    allowed = ["author", "status", "deadline"]

    assert merge_layout(base, saved, allowed) == _layout(
        _column("status", 180, False),
        _column("author", 220),
        _column("deadline", 230),
        sort=[["status", "desc"]],
        group_by=["author"],
    )
    assert saved["columns"][1] == _column("retired", 90)
    assert base["columns"][-1] == _column("deadline", 230, False)
    assert merge_layout(base, None, allowed) == base


def test_merge_respects_explicit_empty_sort_and_grouping():
    base = _layout(_column("author"), sort=[["author", "asc"]], group_by=["author"])
    saved = _layout(_column("author"))
    assert merge_layout(base, saved, ["author"]) == saved


@pytest.mark.parametrize("text", ["{", "[]", "null", "42", 42])
def test_layout_from_json_rejects_garbage(text):
    with pytest.raises(LayoutError, match="^bad_layout$"):
        layout_from_json(text, ["author"])


def test_layout_json_is_canonical_and_round_trips():
    spec = _layout(_column("Ärztin", 1, False), sort=[("Ärztin", "desc")], group_by=["Ärztin"])
    allowed = ["Ärztin"]
    text = layout_to_json(spec)

    assert text == (
        '{"columns": [{"key": "Ärztin", "visible": false, "width": 40}], '
        '"group_by": ["Ärztin"], "sort": [["Ärztin", "desc"]], "version": 1}'
    )
    assert layout_from_json(text, allowed) == validate_layout(spec, allowed)
    with pytest.raises(LayoutError, match="^unknown_column$"):
        layout_from_json(text, [])
