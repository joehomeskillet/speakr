"""Named worklist filters, persisted per user.

``saved_filters.FilterStore`` validates operators, names, the 20-filter cap
and identifier assignment. This module adds a snapshot, a lock so simultaneous
creates cannot pass that cap, optimistic versions, and the work-list query
order: central visibility (``policy.decide``), then the saved filter and
metadata search, then the total, then the page.

No Flask routes and no clinic tables. The snapshot is JSON the caller stores.
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

from src.clinic.policy import RecordingFacts, Subject, decide
from src.clinic.saved_filters import FilterError, FilterStore, apply_filter, search_metadata, validate_filter

_ITEM_KEYS = {"user_id", "id", "name", "spec", "version"}
_ID_PREFIX = "f"


@dataclass(frozen=True)
class SavedFilter:
    """One user's named filter. ``version`` starts at 1 and grows on each write."""

    id: str
    name: str
    spec: dict
    version: int


@dataclass(frozen=True)
class WorklistPage:
    """One page. ``total`` is the visible match count, not the page length."""

    items: tuple[Mapping, ...]
    total: int
    page: int
    page_size: int


def query_worklist(
    rows: Iterable[Mapping],
    subject: Subject,
    *,
    spec: Mapping | None = None,
    query: str = "",
    keys: Sequence[str] = ("title",),
    page: int = 1,
    page_size: int = 20,
) -> WorklistPage:
    """Visibility first, then filter and search, then total, then page."""
    if not isinstance(subject, Subject):
        raise FilterError("unauthorized", "subject is required")
    page_n = _positive(page, "page")
    size = _positive(page_size, "page_size")
    if isinstance(keys, (str, bytes)) or not isinstance(keys, Sequence):
        raise FilterError("bad_value", "keys must be a sequence of field names")
    if isinstance(rows, (str, bytes)) or not isinstance(rows, Iterable):
        raise FilterError("bad_shape", "rows must be an iterable of mappings")

    visible = [row for row in rows if _visible(subject, row)]
    narrowed = apply_filter(spec, visible) if spec is not None else visible
    found = search_metadata(narrowed, query, keys)
    start = (page_n - 1) * size
    return WorklistPage(
        items=tuple(found[start : start + size]),
        total=len(found),
        page=page_n,
        page_size=size,
    )


class WorklistFilterStore:
    """Process-local store. The lock covers the cap check and the snapshot write."""

    def __init__(
        self,
        path: str | Path | None = None,
        writer: Callable[[str], None] | None = None,
    ) -> None:
        self._path = Path(path) if path is not None else None
        self._writer = writer
        self._lock = threading.Lock()
        self._store = FilterStore()
        self._versions: dict[tuple[int, str], int] = {}
        self._users: set[int] = set()
        if self._path is not None and self._path.is_file():
            self._swap(*_materialise(self._path.read_text(encoding="utf-8")))

    def create(self, user_id: int, name: str, spec: Mapping) -> SavedFilter:
        """Save one filter. The 21st raises ``limit_reached`` and changes nothing."""
        with self._lock:
            holder: dict[str, str] = {}

            def change() -> None:
                holder["id"] = self._store.save(user_id, name, spec)
                self._versions[(user_id, holder["id"])] = 1
                self._users.add(user_id)

            self._mutate(change)
            return self._saved(user_id, holder["id"])

    def rename(self, user_id: int, filter_id: str, name: str, *, expected_version: int) -> SavedFilter:
        with self._lock:
            self._expect(user_id, filter_id, expected_version)

            def change() -> None:
                self._store.rename(user_id, filter_id, name)
                self._versions[(user_id, filter_id)] = expected_version + 1

            self._mutate(change)
            return self._saved(user_id, filter_id)

    def replace(
        self, user_id: int, filter_id: str, spec: Mapping, *, expected_version: int
    ) -> SavedFilter:
        """Replace the spec. A wrong version or a failed write leaves the old spec."""
        with self._lock:
            self._expect(user_id, filter_id, expected_version)
            validated = validate_filter(spec)
            payload = json.loads(self._dump())
            matched = False
            for item in payload["filters"]:
                if item["user_id"] == user_id and item["id"] == filter_id:
                    item["spec"] = validated
                    item["version"] = expected_version + 1
                    matched = True
            if not matched:
                raise FilterError("not_found", "filter not found")
            text = _encode(payload)
            loaded = _materialise(text)
            self._publish(text)
            self._swap(*loaded)
            return self._saved(user_id, filter_id)

    def delete(self, user_id: int, filter_id: str, *, expected_version: int) -> None:
        with self._lock:
            self._expect(user_id, filter_id, expected_version)

            def change() -> None:
                self._store.delete(user_id, filter_id)
                del self._versions[(user_id, filter_id)]
                if not self._store.list(user_id):
                    self._users.discard(user_id)

            self._mutate(change)

    def get(self, user_id: int, filter_id: str) -> SavedFilter:
        with self._lock:
            return self._saved(user_id, filter_id)

    def list(self, user_id: int) -> list[SavedFilter]:
        with self._lock:
            return [
                self._to_saved(user_id, filter_id, name, spec)
                for filter_id, name, spec in self._store.list(user_id)
            ]

    def apply(
        self,
        user_id: int,
        filter_id: str,
        rows: Iterable[Mapping],
        subject: Subject,
        *,
        query: str = "",
        keys: Sequence[str] = ("title",),
        page: int = 1,
        page_size: int = 20,
    ) -> WorklistPage:
        """Apply one saved filter. Another user's id is ``not_found``."""
        if not isinstance(subject, Subject) or subject.user_id != user_id:
            raise FilterError("unauthorized", "filter owner and subject differ")
        spec = self.get(user_id, filter_id).spec
        return query_worklist(
            rows, subject, spec=spec, query=query, keys=keys, page=page, page_size=page_size
        )

    def _expect(self, user_id: int, filter_id: str, expected_version: int) -> None:
        current = self._saved(user_id, filter_id)
        if isinstance(expected_version, bool) or not isinstance(expected_version, int):
            raise FilterError("bad_value", "expected_version must be an int")
        if current.version != expected_version:
            raise FilterError("stale_version", "filter version does not match")

    def _saved(self, user_id: int, filter_id: str) -> SavedFilter:
        if not isinstance(filter_id, str):
            raise FilterError("not_found", "filter not found")
        for found_id, name, spec in self._store.list(user_id):
            if found_id == filter_id:
                return self._to_saved(user_id, found_id, name, spec)
        raise FilterError("not_found", "filter not found")

    def _to_saved(self, user_id: int, filter_id: str, name: str, spec: Mapping) -> SavedFilter:
        return SavedFilter(filter_id, name, deepcopy(dict(spec)), self._versions[(user_id, filter_id)])

    def _mutate(self, change: Callable[[], None]) -> None:
        before = self._dump()
        try:
            change()
            self._publish(self._dump())
        except Exception:
            # Any failure, including a full disk or a rejected spec, restores the snapshot.
            self._swap(*_materialise(before))
            raise

    def _publish(self, text: str) -> None:
        if self._writer is not None:
            self._writer(text)
        if self._path is not None:
            _atomic_write(self._path, text)

    def _dump(self) -> str:
        filters = []
        for user_id in sorted(self._users):
            for filter_id, name, spec in self._store.list(user_id):
                filters.append(
                    {
                        "user_id": user_id,
                        "id": filter_id,
                        "name": name,
                        "spec": spec,
                        "version": self._versions[(user_id, filter_id)],
                    }
                )
        filters.sort(key=lambda item: (item["user_id"], int(item["id"][1:])))
        return _encode({"filters": filters})

    def _swap(self, store: FilterStore, versions: dict[tuple[int, str], int], users: set[int]) -> None:
        self._store = store
        self._versions = versions
        self._users = users


def _encode(payload: Mapping) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    os.replace(temporary, path)


def _positive(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise FilterError("bad_value", f"{label} must be a positive int")
    return value


def _visible(subject: Subject, row: Mapping) -> bool:
    if not isinstance(row, Mapping):
        raise FilterError("bad_shape", "row must be a mapping")
    recording_id = row.get("id")
    owner_id = row.get("owner_id")
    if isinstance(recording_id, bool) or not isinstance(recording_id, int) or recording_id < 1:
        raise FilterError("bad_value", "row id must be a positive int")
    if isinstance(owner_id, bool) or not isinstance(owner_id, int) or owner_id < 1:
        raise FilterError("bad_value", "owner_id must be a positive int")
    raw_pools = row.get("pool_ids") or ()
    if isinstance(raw_pools, (str, bytes)) or not isinstance(raw_pools, Collection):
        raise FilterError("bad_value", "pool_ids must be a collection of ints")
    pools: list[int] = []
    for pool_id in raw_pools:
        if isinstance(pool_id, bool) or not isinstance(pool_id, int):
            raise FilterError("bad_value", "pool_ids must be a collection of ints")
        pools.append(pool_id)
    facts = RecordingFacts(recording_id, owner_id, frozenset(pools))
    return decide(subject, "view", facts).allowed


def _materialise(text: str) -> tuple[FilterStore, dict[tuple[int, str], int], set[int]]:
    try:
        data = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise FilterError("bad_value", "snapshot is not json") from exc
    if not isinstance(data, dict) or set(data) != {"filters"} or not isinstance(data["filters"], list):
        raise FilterError("bad_value", "snapshot shape")
    grouped: dict[int, list[dict]] = {}
    for item in data["filters"]:
        parsed = _parse_item(item)
        grouped.setdefault(parsed["user_id"], []).append(parsed)
    store = FilterStore()
    versions: dict[tuple[int, str], int] = {}
    for user_id, items in grouped.items():
        _restore_user(store, user_id, items, versions)
    return store, versions, set(grouped)


def _parse_item(item: object) -> dict:
    if not isinstance(item, dict) or set(item) != _ITEM_KEYS:
        raise FilterError("bad_value", "snapshot item shape")
    user_id = item["user_id"]
    filter_id = item["id"]
    name = item["name"]
    spec = item["spec"]
    version = item["version"]
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id < 1:
        raise FilterError("bad_value", "snapshot user_id")
    if not isinstance(filter_id, str) or not _filter_id_ok(filter_id):
        raise FilterError("bad_value", "snapshot id")
    if not isinstance(name, str) or isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise FilterError("bad_value", "snapshot name or version")
    if not isinstance(spec, dict):
        raise FilterError("bad_value", "snapshot spec")
    return item


def _filter_id_ok(filter_id: str) -> bool:
    number = filter_id[1:]
    return filter_id.startswith(_ID_PREFIX) and number.isascii() and number.isdigit() and number[0] != "0"


def _restore_user(store: FilterStore, user_id: int, items: list[dict], versions: dict[tuple[int, str], int]) -> None:
    """Replay through ``FilterStore.save`` so ids stay the ones that were issued.

    shortcut: FilterStore has no insert-at-id, so a missing number is a temporary
    row, deleted before return. Upgrade when FilterStore can restore an id.
    """
    ordered = sorted(items, key=lambda item: int(item["id"][1:]))
    numbers = [int(item["id"][1:]) for item in ordered]
    if len(numbers) != len(set(numbers)):
        raise FilterError("bad_value", "duplicate filter id")
    present = {number: item for number, item in zip(numbers, ordered)}
    taken = {item["name"].casefold() for item in ordered}
    gaps: list[str] = []
    for number in range(1, numbers[-1] + 1):
        item = present.get(number)
        if item is None:
            name = _gap_name(taken)
            taken.add(name.casefold())
            got = store.save(user_id, name, {"all": [], "any": []})
            gaps.append(got)
        else:
            got = store.save(user_id, item["name"], item["spec"])
        if got != f"f{number}":
            raise FilterError("bad_value", "filter id was not stable")
    for gap_id in gaps:
        store.delete(user_id, gap_id)
    for item in ordered:
        versions[(user_id, item["id"])] = item["version"]


def _gap_name(taken: set[str]) -> str:
    while True:
        name = f"gap-{uuid.uuid4().hex}"
        if name.casefold() not in taken:
            return name
