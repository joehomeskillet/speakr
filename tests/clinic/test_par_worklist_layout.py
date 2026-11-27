"""P-052: the V8 role column layout remains valid through server persistence."""

import pytest

from src.clinic.list_layout import LayoutError, group_rows, sort_rows
from src.clinic.routes_worklist import LAYOUT_COLUMNS, _DEFAULT_LAYOUT
from src.clinic.worklist_layout import LayoutStore


def test_role_columns_survive_save_reload_and_keep_account_isolation():
    spec = {"version": 1, "columns": [
        {"key": key, "width": 140, "visible": True}
        for key in ("priority", "dictated_by", "staff_pools", "lease_holder")
    ], "sort": [["duration_seconds", "desc"]], "group_by": ["dictated_by", "priority"]}
    store = LayoutStore()
    store.save(1, 1, spec, LAYOUT_COLUMNS, 0)
    fresh = LayoutStore()
    fresh.restore_snapshot(1, 1, *store.snapshot(1, 1))
    restored = fresh.load(1, 1, _DEFAULT_LAYOUT, LAYOUT_COLUMNS).layout
    assert [c["key"] for c in restored["columns"]][:4] == [c["key"] for c in spec["columns"]]
    assert restored["group_by"] == ["dictated_by", "priority"]
    assert restored["sort"] == [["duration_seconds", "desc"]]
    assert fresh.load(2, 2, _DEFAULT_LAYOUT, LAYOUT_COLUMNS).layout == _DEFAULT_LAYOUT
    spec["columns"].append({"key": "unknown", "width": 140, "visible": True})
    with pytest.raises(LayoutError, match="unknown_column"):
        fresh.save(1, 1, spec, LAYOUT_COLUMNS, 1)


def test_role_dtos_group_by_identity_and_preserve_sort_within_priority():
    rows = [
        {"id": 1, "dictated_by": {"id": "a", "display_name": "Zoe"}, "priority": 1},
        {"id": 2, "dictated_by": {"id": "a", "display_name": "Zoe"}, "priority": 1},
        {"id": 3, "dictated_by": {"id": "b", "display_name": "Zoe"}, "priority": 0},
    ]
    groups = group_rows(sort_rows(rows, [("id", "desc")]), ["dictated_by", "priority"])
    assert [g.key_path for g in groups] == [("b",), ("a",)]
    assert groups[1].children[0].key_path == ("a", 1)
    assert [r["id"] for r in groups[1].children[0].rows] == [2, 1]
    rows[2]["dictated_by"]["display_name"] = "Ada"
    assert [r["id"] for r in sort_rows(rows, [("dictated_by", "asc")])] == [3, 1, 2]


def test_pool_sets_and_lease_holder_dtos_use_stable_group_keys():
    rows = [
        {"id": 1, "staff_pools": [{"id": "b", "name": "West"}, {"id": "a", "name": "Ost"}],
         "lease_holder": {"id": "7", "display_name": "Ada", "until": "one"}},
        {"id": 2, "staff_pools": [{"id": "a", "name": "Ost"}, {"id": "b", "name": "West"}],
         "lease_holder": {"id": "7", "display_name": "Ada", "until": "two"}},
        {"id": 3, "staff_pools": [], "lease_holder": None},
    ]
    groups = group_rows(sort_rows(rows, [("staff_pools", "asc")]), ["staff_pools", "lease_holder"])
    assert len(groups) == 2
    assert [r["id"] for r in groups[0].rows] == [3]
    assert len(groups[1].children) == 1
    assert [r["id"] for r in groups[1].children[0].rows] == [1, 2]
    assert [r["id"] for r in sort_rows(rows, [("lease_holder", "asc")])] == [1, 2, 3]
