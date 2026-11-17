"""Recording ids named by a request, in every channel a handler can read.

A handler may take a recording id from the URL, the query string, a form or a
JSON body, at the top level or inside a nested filter object. The guard must
see all of them, so this module does not look for a few known keys: any key
whose name contains "recording" and "id" counts (recording_id, recording_ids,
filter_recording_ids, filters.recording_ids, recordingId ...), at any depth,
plus the keys a route rule declares (for example ``id`` or
``notes_source_id``).

An id value that cannot be read raises ValueError: the request is refused, it
is never passed on unchecked.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from flask import request

# Name of a key that carries a recording id (case-insensitive).
RECORDING_KEY = re.compile(r'recording.*id', re.IGNORECASE)

MAX_DEPTH = 16
MAX_IDS = 1000


def is_recording_key(name: str) -> bool:
    return bool(RECORDING_KEY.search(name))


def as_id(value: Any) -> int:
    if isinstance(value, bool) or value is None or isinstance(value, (dict, float)):
        raise ValueError(value)
    return int(value)


def ids_from_value(value: Any) -> list[int]:
    """Ids in a value: one id, a list of ids or a comma separated string."""
    if value is None:
        return []
    items: Iterable[Any]
    if isinstance(value, list):
        items = value
    elif isinstance(value, str) and ',' in value:
        items = value.split(',')
    else:
        items = [value]
    ids = []
    for item in items:
        if isinstance(item, str) and not item.strip():
            continue
        ids.append(as_id(item.strip() if isinstance(item, str) else item))
    return ids


def _walk_json(node: Any, declared: frozenset[str], depth: int, out: list[int]) -> None:
    if depth > MAX_DEPTH:
        raise ValueError('nesting too deep')
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str) and (is_recording_key(key) or (depth == 0 and key in declared)):
                out.extend(ids_from_value(value))
            if isinstance(value, (dict, list)):
                _walk_json(value, declared, depth + 1, out)
    elif isinstance(node, list):
        for item in node:
            _walk_json(item, declared, depth + 1, out)


def _walk_multidict(source: Any, declared: frozenset[str], out: list[int]) -> None:
    for key in source.keys():
        if is_recording_key(key) or key in declared:
            for value in source.getlist(key):
                out.extend(ids_from_value(value))


def request_ids(declared: tuple[str, ...] = ()) -> list[int]:
    """Every recording id in the query string, the form and the JSON body."""
    keys = frozenset(declared)
    out: list[int] = []
    _walk_multidict(request.args, keys, out)
    _walk_multidict(request.form, keys, out)
    _walk_json(request.get_json(silent=True), keys, 0, out)
    if len(out) > MAX_IDS:
        raise ValueError('too many ids')
    return list(dict.fromkeys(out))


def body_flag(names: tuple[str, ...]) -> bool:
    """True when the request sets one of the flags the way the handler reads
    it: any present, non-empty top-level value counts (Python truthiness, so
    the string "false" is set)."""
    payload = request.get_json(silent=True)
    sources: list[Any] = [request.args, request.form]
    if isinstance(payload, dict):
        sources.append(payload)
    for source in sources:
        for name in names:
            if bool(source.get(name)):
                return True
    return False
