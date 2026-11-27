"""Parity and structural tests for the partitioned clinic route catalogs (P-018)."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.clinic import (
    policy,
    route_policy,
    route_policy_admin,
    route_policy_editor,
    route_policy_imports,
    route_policy_output,
)

ROOT = Path(__file__).resolve().parents[2]
CATALOGS = (
    route_policy_output,
    route_policy_editor,
    route_policy_imports,
    route_policy_admin,
)

# Full tables from 7acabaf4 plus the P-189, P-036, P-083, P-042 and P-093 clinic endpoints,
# serialized with json.dumps(sort_keys=True).
# Intentional route changes must update the snapshot together with their registration tests.
BASELINE_TABLE_SHA256 = {
    False: "88f25130a4c4514b82b166e06829ec5427fb26343a2cba42f37b0614fb03502e",
    True: "ba4230b2da5f9095599a76704e4c1f57d074e238f4f28d420725c31982a43aef",
}

_CHILD_INSPECT = r'''
import json
from dataclasses import asdict
from src.app import app
from src.clinic.route_policy import ROUTE_POLICY

rules = [
    {"endpoint": r.endpoint, "arguments": sorted(r.arguments)}
    for r in app.url_map.iter_rules()
]
print("RESULT " + json.dumps({
    "rules": rules,
    "table": {endpoint: asdict(rule) for endpoint, rule in ROUTE_POLICY.items()},
}))
'''


def _inspect_app(tmp_path: Path, clinic: bool) -> dict:
    env = {
        **os.environ,
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'catalog.db'}",
        "UPLOAD_FOLDER": str(tmp_path / "uploads"),
        "RATELIMIT_ENABLED": "false",
        "DISABLE_VOICE_EMBEDDING_CHECK": "true",
        "ENABLE_AUTO_PROCESSING": "false",
    }
    env.pop("CLINIC_MODE", None)
    if clinic:
        env["CLINIC_MODE"] = "1"
    done = subprocess.run(
        [sys.executable, "-c", _CHILD_INSPECT],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert done.returncode == 0, done.stderr[-3000:]
    line = next((x for x in done.stdout.splitlines() if x.startswith("RESULT ")), None)
    assert line, done.stderr[-3000:]
    return json.loads(line[len("RESULT "):])


@pytest.fixture(scope="module")
def clinic_on_snapshot(tmp_path_factory):
    return _inspect_app(tmp_path_factory.mktemp("catalog_on"), clinic=True)


@pytest.fixture(scope="module")
def clinic_off_snapshot(tmp_path_factory):
    return _inspect_app(tmp_path_factory.mktemp("catalog_off"), clinic=False)


def test_catalogs_are_strictly_data_only():
    for module in CATALOGS:
        path = Path(module.__file__)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                continue
            assert isinstance(node, (ast.Assign, ast.AnnAssign)), (
                f"{path.name} must remain data-only, found {type(node).__name__}"
            )
            assert isinstance(ast.literal_eval(node.value), tuple)
        for attr in ("PUBLIC", "AUTH", "ADMIN"):
            entries = getattr(module, attr)
            assert isinstance(entries, tuple), f"{path.name}.{attr} must be a tuple"
            assert all(isinstance(e, str) and "." in e and e == e.strip() for e in entries), (
                f"{path.name}.{attr} contains invalid endpoint names"
            )


def test_catalogs_have_no_duplicate_endpoints():
    seen: dict[str, str] = {}
    for module in CATALOGS:
        name = module.__name__.rsplit(".", 1)[-1]
        for attr in ("PUBLIC", "AUTH", "ADMIN"):
            for endpoint in getattr(module, attr):
                location = f"{name}.{attr}"
                assert endpoint not in seen, f"duplicate endpoint {endpoint} in {seen[endpoint]} and {location}"
                seen[endpoint] = location


def test_catalog_endpoints_match_built_clinic_table(monkeypatch):
    monkeypatch.delenv("CLINIC_MODE", raising=False)
    off_table = route_policy._build()
    monkeypatch.setenv("CLINIC_MODE", "1")
    on_table = route_policy._build()

    catalog_entries: dict[str, str] = {}
    expected_kinds = {
        "PUBLIC": route_policy.PUBLIC,
        "AUTH": route_policy.AUTH,
        "ADMIN": route_policy.ADMIN,
    }
    for module in CATALOGS:
        for attr, kind in expected_kinds.items():
            for endpoint in getattr(module, attr):
                catalog_entries[endpoint] = kind

    assert len(catalog_entries) == 155
    assert not (set(catalog_entries) & set(off_table))
    assert set(on_table) - set(off_table) == set(catalog_entries)
    for endpoint, kind in catalog_entries.items():
        assert on_table[endpoint] == route_policy.RouteRule(kind)


@pytest.mark.parametrize("clinic", [False, True])
def test_inventory_missing_stale_and_recording_scope(clinic, clinic_off_snapshot, clinic_on_snapshot):
    snap = clinic_on_snapshot if clinic else clinic_off_snapshot
    rules = snap["rules"]
    table = snap["table"]
    app_endpoints = {r["endpoint"] for r in rules}

    missing = sorted(app_endpoints - set(table))
    assert not missing, f"unclassified endpoints: {missing}"

    stale = sorted(set(table) - app_endpoints)
    assert not stale, f"stale/speculative endpoints: {stale}"

    wrong_scope = sorted(
        r["endpoint"]
        for r in rules
        if "recording_id" in r["arguments"]
        and table[r["endpoint"]]["kind"] in (route_policy.PUBLIC, route_policy.AUTH, route_policy.ADMIN)
    )
    assert not wrong_scope, f"recording_id routes must not be public/auth/admin: {wrong_scope}"

    by_endpoint: dict[str, set[str]] = {}
    for r in rules:
        by_endpoint.setdefault(r["endpoint"], set()).update(r["arguments"])
    for endpoint, rule in table.items():
        assert rule["kind"] in (
            route_policy.PUBLIC,
            route_policy.AUTH,
            route_policy.ADMIN,
            route_policy.RECORDING,
            route_policy.DISABLED,
        )
        if rule["kind"] in (route_policy.RECORDING, route_policy.DISABLED):
            assert rule["action"] in policy.ACTIONS, endpoint
        if rule["kind"] == route_policy.DISABLED:
            assert rule["feature"], endpoint
        if rule["kind"] == route_policy.RECORDING and not rule["body_keys"]:
            assert rule["arg"] in by_endpoint[endpoint], endpoint


@pytest.mark.parametrize("clinic", [False, True])
def test_complete_table_matches_before_partition(clinic, clinic_off_snapshot, clinic_on_snapshot):
    snapshot = clinic_on_snapshot if clinic else clinic_off_snapshot
    serialized = json.dumps(snapshot["table"], sort_keys=True).encode()
    assert hashlib.sha256(serialized).hexdigest() == BASELINE_TABLE_SHA256[clinic]


def test_duplicate_endpoint_across_catalogs_is_rejected(monkeypatch):
    monkeypatch.setenv("CLINIC_MODE", "1")
    monkeypatch.setattr(route_policy_output, "AUTH", route_policy_output.AUTH + ("clinic_audio.get_current",))
    with pytest.raises(ValueError, match="classified twice"):
        route_policy._build()
