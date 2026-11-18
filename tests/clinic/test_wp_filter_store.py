"""Named worklist filters: cap, persistence, versions, visibility order."""

from __future__ import annotations

import queue
import threading

import pytest

from src.clinic.policy import Grant, Subject
from src.clinic.saved_filters import FilterError
from src.clinic.worklist_filters import WorklistFilterStore, query_worklist

OPEN = {
    "all": [
        {"field": "pool", "op": "eq", "value": "Pool A"},
        {"field": "status", "op": "ne", "value": "fertiggestellt"},
    ]
}
NOTE = {
    "all": [{"field": "meta.anmerkung", "op": "contains", "value": "sonnenblume"}],
}


def _row(row_id, owner_id, **fields):
    row = {"id": row_id, "owner_id": owner_id}
    row.update(fields)
    return row


def test_create_keeps_stable_ids_and_metadata_operators(tmp_path):
    path = tmp_path / "filters.json"
    store = WorklistFilterStore(path)
    first = store.create(1, "  Zebra  ", {"all": []})
    second = store.create(1, "Alpha", OPEN)
    third = store.create(1, "Notiz", NOTE)
    renamed = store.rename(1, first.id, "Mitte", expected_version=1)
    store.delete(1, second.id, expected_version=1)

    assert renamed.id == first.id == "f1"
    assert renamed.version == 2
    assert third.id == "f3"
    assert [item.name for item in store.list(1)] == ["Mitte", "Notiz"]

    loaded = WorklistFilterStore(path)
    by_name = {item.name: item for item in loaded.list(1)}
    assert set(by_name) == {"Mitte", "Notiz"}
    assert by_name["Mitte"].id == "f1"
    assert by_name["Notiz"].id == "f3"
    assert by_name["Notiz"].spec == {
        "all": [{"field": "meta.anmerkung", "op": "contains", "value": "sonnenblume"}],
        "any": [],
    }

    rows = [
        _row(1, 1, title="CT", pool="Pool A", status="zu_schreiben", **{"meta.anmerkung": "Sonnenblume"}),
        _row(2, 1, title="Sonnenblume", pool="Pool A", status="zu_schreiben", **{"meta.anmerkung": "leer"}),
    ]
    page = loaded.apply(1, "f3", rows, Subject(1))
    assert [row["id"] for row in page.items] == [1]
    assert page.total == 1


def test_unknown_field_and_operator_are_rejected():
    store = WorklistFilterStore()
    with pytest.raises(FilterError) as unknown:
        store.create(1, "Kaputt", {"all": [{"field": "secret", "op": "eq", "value": 1}]})
    assert unknown.value.code == "unknown_field"
    with pytest.raises(FilterError) as regex:
        store.create(1, "Muster", {"all": [{"field": "title", "op": "regex", "value": "a"}]})
    assert regex.value.code == "unknown_op"
    assert store.list(1) == []

    with pytest.raises(FilterError) as queried:
        query_worklist([_row(1, 1, title="A")], Subject(1), spec={"all": [{"field": "nope", "op": "eq", "value": 1}]})
    assert queried.value.code == "unknown_field"


def test_twenty_first_create_is_atomic_per_user(tmp_path):
    path = tmp_path / "filters.json"
    store = WorklistFilterStore(path)
    for index in range(20):
        store.create(1, f"A{index}", {"all": []})
        store.create(2, f"B{index}", {"all": []})
    raw = path.read_text(encoding="utf-8")
    names = [item.name for item in store.list(1)]
    ids = [item.id for item in store.list(1)]

    with pytest.raises(FilterError) as extra:
        store.create(1, "A20", {"all": []})
    assert extra.value.code == "limit_reached"
    assert [item.name for item in store.list(1)] == names
    assert [item.id for item in store.list(1)] == ids
    assert path.read_text(encoding="utf-8") == raw
    assert len(store.list(2)) == 20
    assert len(WorklistFilterStore(path).list(1)) == 20


def test_concurrent_creates_stop_at_twenty():
    store = WorklistFilterStore()
    barrier = threading.Barrier(25)
    results: queue.Queue = queue.Queue()

    def worker(index):
        barrier.wait()
        try:
            saved = store.create(1, f"F{index}", {"all": []})
        except FilterError as exc:
            results.put(("err", exc.code))
        else:
            results.put(("ok", saved.id))

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(25)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()

    outcomes = [results.get_nowait() for _ in range(25)]
    saved_ids = [value for kind, value in outcomes if kind == "ok"]
    errors = [value for kind, value in outcomes if kind == "err"]
    assert len(saved_ids) == 20
    assert len(set(saved_ids)) == 20
    assert errors == ["limit_reached"] * 5
    assert len(store.list(1)) == 20


def test_other_users_filter_id_is_rejected():
    store = WorklistFilterStore()
    saved = store.create(1, "Mein Filter", OPEN)
    rows = [_row(1, 1, title="CT", pool="Pool A", status="zu_schreiben")]

    with pytest.raises(FilterError) as missing:
        store.apply(2, saved.id, rows, Subject(2))
    assert missing.value.code == "not_found"
    with pytest.raises(FilterError) as foreign:
        store.delete(2, saved.id, expected_version=1)
    assert foreign.value.code == "not_found"
    with pytest.raises(FilterError) as denied:
        store.apply(1, saved.id, rows, Subject(2))
    assert denied.value.code == "unauthorized"

    assert store.get(1, saved.id).name == "Mein Filter"
    page = store.apply(1, saved.id, rows, Subject(1))
    assert page.total == 1
    assert page.items[0]["id"] == 1


def test_stale_version_then_retry():
    store = WorklistFilterStore()
    saved = store.create(1, "Offen", OPEN)
    replacement = {"all": [{"field": "priority", "op": "eq", "value": 128}]}

    with pytest.raises(FilterError) as stale:
        store.replace(1, saved.id, replacement, expected_version=0)
    assert stale.value.code == "stale_version"
    with pytest.raises(FilterError) as stale_delete:
        store.delete(1, saved.id, expected_version=4)
    assert stale_delete.value.code == "stale_version"
    assert store.get(1, saved.id).version == 1
    assert store.get(1, saved.id).spec["all"][0]["field"] == "pool"

    updated = store.replace(1, saved.id, replacement, expected_version=1)
    assert updated.id == saved.id
    assert updated.version == 2
    assert updated.spec["all"] == [{"field": "priority", "op": "eq", "value": 128}]


def test_failed_write_rolls_back_and_retry_succeeds():
    calls = {"n": 0}

    def writer(_text):
        calls["n"] += 1
        if calls["n"] in (1, 3):
            raise OSError("full")

    store = WorklistFilterStore(writer=writer)
    with pytest.raises(OSError, match="full"):
        store.create(1, "Offen", OPEN)
    assert store.list(1) == []

    saved = store.create(1, "Offen", OPEN)
    assert saved.id == "f1"
    assert saved.version == 1

    replacement = {"all": [{"field": "status", "op": "eq", "value": "zu_schreiben"}]}
    with pytest.raises(OSError, match="full"):
        store.replace(1, saved.id, replacement, expected_version=1)
    assert store.get(1, saved.id).version == 1
    assert store.get(1, saved.id).spec["all"][0]["field"] == "pool"

    updated = store.replace(1, saved.id, replacement, expected_version=1)
    assert updated.version == 2
    assert updated.spec["all"][0]["value"] == "zu_schreiben"


def test_visibility_runs_before_search_total_and_page():
    rows = [
        _row(1, 7, title="Eins", pool="Pool A", status="zu_schreiben"),
        _row(2, 8, title="Geheimterm", pool="Pool A", status="zu_schreiben"),
        _row(3, 7, title="Zwei", pool="Pool A", status="fertiggestellt"),
        _row(4, 7, title="Drei", pool="Pool A", status="zu_schreiben"),
    ]
    owner = Subject(7)

    hidden = query_worklist(rows, owner, query="Geheimterm", keys=("title",))
    assert hidden.total == 0
    assert hidden.items == ()

    opened = query_worklist(rows, owner, spec=OPEN, page=2, page_size=1)
    assert opened.total == 2
    assert [row["id"] for row in opened.items] == [4]

    granted = Subject(7, grants=(Grant("author", frozenset({"view"}), frozenset({8})),))
    visible = query_worklist(rows, granted, query="Geheimterm", keys=("title",))
    assert visible.total == 1
    assert visible.items[0]["id"] == 2

    with pytest.raises(FilterError) as denied:
        query_worklist(rows, {"user_id": 7})
    assert denied.value.code == "unauthorized"
