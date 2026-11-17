"""Specification integrity tests for Clinic Südhang (Speakr Fork V4).

Validates the machine-readable specification catalogs under clinic-spec/
and enforces strict acceptance rules (P00 / X4-021 / X4-022).
"""

import json
import shutil
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CLINIC_SPEC_DIR = REPO_ROOT / "clinic-spec"

ALLOWED_NON_SUCCESS_STATES = {
    "not_run",
    "not_run/blocked",
    "blocked",
    "failed",
    "in_progress",
    "pending",
    "skipped",
    "not_started",
}

FORBIDDEN_EXTENSIONS = (
    ".exe",
    ".dll",
    ".msi",
    ".sys",
    ".gpr",
    ".rep",
    ".gzf",
    ".mdb",
    ".sdf",
    ".sqlite",
    ".db",
)


def _load_spec_json(relative_path: str) -> Any:
    """Load and parse a JSON catalog under clinic-spec/."""
    path = CLINIC_SPEC_DIR / relative_path
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def test_requirements_integrity():
    """Verify planning/requirements.json counts, prefixes, and MUST priority."""
    data = _load_spec_json("planning/requirements.json")
    requirements = data["requirements"]

    assert len(requirements) == 177, f"Expected 177 requirements, got {len(requirements)}"

    par_reqs = [r for r in requirements if r["id"].startswith("PAR-")]
    assert len(par_reqs) == 101, f"Expected 101 PAR requirements, got {len(par_reqs)}"

    v4_reqs = [r for r in requirements if r["id"].startswith("V4-")]
    assert len(v4_reqs) == 14, f"Expected 14 V4 requirements, got {len(v4_reqs)}"

    for r in par_reqs:
        assert r.get("priority") == "MUST", f"Requirement {r['id']} priority is not MUST: {r.get('priority')}"
        assert r.get("mandatory") is True, f"Requirement {r['id']} mandatory flag is not True"


def test_parity_matrix_coverage():
    """Verify planning/parity-matrix.json covers exact 101 PAR IDs mapped to acceptance tests."""
    req_data = _load_spec_json("planning/requirements.json")
    par_req_ids = {r["id"] for r in req_data["requirements"] if r["id"].startswith("PAR-")}

    pm_data = _load_spec_json("planning/parity-matrix.json")
    pm_features = pm_data["features"]
    pm_ids = {f["id"] for f in pm_features}

    assert pm_ids == par_req_ids, "Parity matrix features do not match PAR requirement IDs"
    assert len(pm_ids) == 101, f"Expected 101 parity matrix features, got {len(pm_ids)}"

    acc_data = _load_spec_json("tests/acceptance.json")
    acc_test_ids = {t["id"] for t in acc_data["tests"]}

    for f in pm_features:
        mapped_tests = f.get("tests", [])
        assert len(mapped_tests) >= 1, f"Feature {f['id']} is not mapped to any test"
        for tid in mapped_tests:
            assert tid in acc_test_ids, f"Feature {f['id']} maps to unknown test ID {tid}"


def test_acceptance_tests_integrity():
    """Verify tests/acceptance.json counts, prefixes, and valid non-success/evidence status (X4-022)."""
    acc_data = _load_spec_json("tests/acceptance.json")
    acceptance_tests = acc_data["tests"]

    assert len(acceptance_tests) == 252, f"Expected 252 acceptance tests, got {len(acceptance_tests)}"

    bt_tests = [t for t in acceptance_tests if t["id"].startswith("BT-")]
    assert len(bt_tests) == 101, f"Expected 101 BT- tests, got {len(bt_tests)}"

    x4_tests = [t for t in acceptance_tests if t["id"].startswith("X4-")]
    assert len(x4_tests) == 24, f"Expected 24 X4- tests, got {len(x4_tests)}"

    wb_tests = [t for t in acceptance_tests if t["id"].startswith("WB-")]
    assert len(wb_tests) == 20, f"Expected 20 WB- tests, got {len(wb_tests)}"

    for t in acceptance_tests:
        status = t.get("status")
        if status == "passed":
            evidence = t.get("evidence")
            assert isinstance(evidence, list) and len(evidence) > 0, (
                f"Acceptance test {t['id']} is passed but lacks evidence"
            )
        else:
            assert status in ALLOWED_NON_SUCCESS_STATES, (
                f"Acceptance test {t['id']} has invalid non-success status: {status}"
            )


def test_behavior_tests_unchanged():
    """Verify original behavior test catalogs match each other exactly in IDs and content."""
    dns_orig = _load_spec_json("tests/dns-behavior-tests.original.json")
    dns_func = _load_spec_json("sources/dns-functional/specs/behavior_tests.json")

    tc_orig = dns_orig.get("test_cases", [])
    tc_func = dns_func.get("test_cases", [])

    assert len(tc_orig) == 101, f"Expected 101 original behavior test cases, got {len(tc_orig)}"
    assert len(tc_func) == 101, f"Expected 101 functional source test cases, got {len(tc_func)}"

    ids_orig = [t["id"] for t in tc_orig]
    ids_func = [t["id"] for t in tc_func]

    assert ids_orig == ids_func, "Test case IDs differ between original and functional source catalogs"
    assert all(tid.startswith("BT-") for tid in ids_orig), "Not all original test IDs start with BT-"
    assert tc_orig == tc_func, "Content mismatch between original and functional behavior test cases"


def test_web_parity_gaps():
    """Verify planning/web-parity-gaps.json open gaps, flags, and that covered tests cannot be passed."""
    req_data = _load_spec_json("planning/requirements.json")
    par_req_ids = {r["id"] for r in req_data["requirements"] if r["id"].startswith("PAR-")}

    gaps_data = _load_spec_json("planning/web-parity-gaps.json")
    gaps = gaps_data.get("gaps", [])

    assert len(gaps) == 13, f"Expected exactly 13 gaps, got {len(gaps)}"
    assert all(g.get("status") == "open" for g in gaps), "Not all web parity gaps have status 'open'"
    assert all(g.get("automatically_waived") is False for g in gaps), "Found gap with automatically_waived != False"
    assert gaps_data.get("scope_waivers") == [], f"Expected empty scope_waivers, got {gaps_data.get('scope_waivers')}"
    assert gaps_data.get("full_parity_claim_allowed") is False, "full_parity_claim_allowed must be False"

    for g in gaps:
        assert g.get("requirement_id") in par_req_ids, (
            f"Gap requirement_id {g.get('requirement_id')} is not a known PAR requirement"
        )

    open_gap_req_ids = {g["requirement_id"] for g in gaps if g.get("status") == "open"}

    acc_data = _load_spec_json("tests/acceptance.json")
    acceptance_tests = acc_data["tests"]
    acceptance_by_id = {t["id"]: t for t in acceptance_tests}

    # Verify via test requirements references
    for t in acceptance_tests:
        if any(req_id in open_gap_req_ids for req_id in t.get("requirements", [])):
            assert t.get("status") != "passed", (
                f"Test {t['id']} covers open gap requirement but is marked 'passed'"
            )

    # Verify via parity matrix test mapping
    pm_data = _load_spec_json("planning/parity-matrix.json")
    for f in pm_data["features"]:
        if f["id"] in open_gap_req_ids:
            for tid in f.get("tests", []):
                mapped_test = acceptance_by_id.get(tid)
                if mapped_test:
                    assert mapped_test.get("status") != "passed", (
                        f"Test {tid} mapped to open gap {f['id']} is marked 'passed'"
                    )


def test_no_forbidden_tracked_artifacts():
    """Verify acceptance test X4-021: git-tracked files contain no forbidden binaries or 02_Originalartefakte."""
    git_bin = shutil.which("git") or "git"
    res = subprocess.run(
        [git_bin, "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    tracked_files = res.stdout.splitlines()
    assert len(tracked_files) > 0, "git ls-files returned empty list"

    for file_path in tracked_files:
        p = PurePosixPath(file_path)
        assert p.suffix.lower() not in FORBIDDEN_EXTENSIONS, (
            f"Forbidden file extension {p.suffix} found in tracked file: {file_path}"
        )
        assert "02_Originalartefakte" not in p.parts, (
            f"Forbidden directory '02_Originalartefakte' found in tracked path: {file_path}"
        )
