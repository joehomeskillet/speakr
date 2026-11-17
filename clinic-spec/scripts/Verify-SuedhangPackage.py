#!/usr/bin/env python3
"""Read-only specification/integrity validator. Does not execute application/vendor code.
Python 3.10+. Standard library only. Exit 0 = package checks pass, NOT product parity.
Usage: python Verify-SuedhangPackage.py --root PATH [--deep-originals] [--report result.json]
"""
from __future__ import annotations
import argparse
import collections
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import zipfile
from typing import Any

def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))

def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

def safe_child(root: Path, relative: str) -> Path:
    # Manifests always contain slash-separated relative names, never drive paths.
    rel = PurePosixPath(relative)
    if rel.is_absolute() or ".." in rel.parts or "\\" in relative or ":" in relative:
        raise ValueError(f"Unsafe manifest path: {relative}")
    path = root.joinpath(*rel.parts)
    if not path.resolve().is_relative_to(root.resolve()) or path.is_symlink():
        raise ValueError(f"Escaping/symlink path: {relative}")
    return path

def validate(root: Path, verify_integrity: bool = True, deep_originals: bool = False) -> dict[str, Any]:
    root = root.resolve()
    full = root if (root / "01_Claude_Bauauftrag").is_dir() else None
    b = root / "01_Claude_Bauauftrag" if full else root
    errors: list[str] = []
    checks: list[str] = []
    counts: dict[str, Any] = {}
    def check(condition: bool, message: str) -> None:
        (checks if condition else errors).append(message)
    try:
        features = read_json(b / "sources/dns-functional/specs/feature_parity.json")["features"]
        source_tests = read_json(b / "sources/dns-functional/specs/behavior_tests.json")["test_cases"]
        requirements = read_json(b / "planning/requirements.json")["requirements"]
        matrix = read_json(b / "planning/parity-matrix.json")["features"]
        tests = read_json(b / "tests/acceptance.json")["tests"]
        tasks = read_json(b / "planning/backlog.json")["tasks"]
        gaps = read_json(b / "planning/web-parity-gaps.json")
        def keyed(rows: list[dict[str, Any]], label: str) -> dict[str, dict[str, Any]]:
            result = {x["id"]: x for x in rows}
            check(len(result) == len(rows), f"Unique {label} IDs")
            return result
        fs = keyed(features, "source feature"); ss = keyed(source_tests, "source test")
        rs = keyed(requirements, "requirement"); ms = keyed(matrix, "parity"); ts = keyed(tests, "test"); tasks_by_id = keyed(tasks, "task")
        check(len(fs) == 101 and len(ss) == 101, "101 functional source requirements and tests")
        check(set(fs) == set(ms) == {x for x in rs if x.startswith("PAR-")}, "Exact 101-feature parity coverage")
        check(len(rs) == 177 and len(ts) == 252, "Expected V4 requirement/test counts")
        check({f"R-{n:03d}" for n in range(1, 63)} <= rs.keys(), "62 baseline requirement IDs retained")
        expected_base = {f"T-{n:03d}" for n in list(range(1, 41)) + list(range(51, 99))} | {f"X-{n:03d}" for n in range(1, 11)}
        check(expected_base <= ts.keys(), "98 baseline test IDs retained")
        check({f"V4-{n:03d}" for n in range(1, 15)} <= rs.keys(), "14 V4 boundary requirements")
        for fid, source in fs.items():
            r, m = rs[fid], ms[fid]
            check(r["requirement"] == source["observable_behavior"] == m["requirement"], f"Unchanged source behavior {fid}")
            check(r.get("mandatory") is True and m.get("mandatory") is True and r.get("priority") == m.get("priority") == "MUST", f"MUST priority {fid}")
            tid = source["acceptance_test"]["id"]
            check(tid in ts and tid in r.get("tests", []) and tid in m.get("tests", []), f"Linked original test {fid}")
            check(m.get("task_id") in tasks_by_id and fid in tasks_by_id[m["task_id"]].get("requirements", []), f"Task coverage {fid}")
            check(m.get("automatic_waiver_allowed") is False and m.get("scope_waived") is False, f"No automatic waiver {fid}")
            if m.get("parity_result") == "passed":
                check(bool(m.get("reference_evidence")) and bool(m.get("target_evidence")), f"Dual-run evidence {fid}")
            active_text = m.get("target_modules", "") + " " + m.get("implementation_rule", "")
            check(not any(token in active_text for token in ("apps/windows-companion", "apps/word-addin", "NAudio/Player", "Player im Companion")), f"No native implementation route {fid}")
        for tid, src in ss.items():
            check(tid in ts, f"Original behavior test present {tid}")
            for field in ("given", "when", "then", "fixtures", "manual_pages"):
                check(ts[tid].get(field) == src.get(field), f"Original {field} retained {tid}")
        for r in requirements:
            check(bool(r.get("tests")) and all(t in ts for t in r["tests"]), f"Requirement has valid tests {r['id']}")
        for t in tests:
            check(all(r in rs for r in t.get("requirements", [])), f"Known requirements for test {t['id']}")
            if t.get("status") == "passed":
                check(bool(t.get("evidence")), f"Evidence for passed test {t['id']}")
        for t in tasks:
            check(all(r in rs for r in t.get("requirements", [])), f"Known task requirements {t['id']}")
            check(all(i in ts for i in t.get("tests", [])), f"Known task tests {t['id']}")
            check(all(d in tasks_by_id for d in t.get("depends_on", [])), f"Known task dependencies {t['id']}")
        visiting: set[str] = set(); visited: set[str] = set()
        def visit(tid: str) -> None:
            if tid in visiting:
                raise ValueError(f"Dependency cycle at {tid}")
            if tid in visited:
                return
            visiting.add(tid)
            for dep in tasks_by_id[tid].get("depends_on", []): visit(dep)
            visiting.remove(tid); visited.add(tid)
        for tid in tasks_by_id: visit(tid)
        checks.append("Acyclic implementation plan")
        check(len(gaps["gaps"]) == 13 and not gaps.get("scope_waivers"), "13 open clause conflicts; no scope waiver")
        check(gaps.get("full_parity_claim_allowed") is False, "No unsupported full-parity claim")
        for g in gaps["gaps"]:
            check(g["requirement_id"] in rs and g.get("automatically_waived") is False, f"Gap requirement retained {g['id']}")
        dev = read_json(b / "config/dev.defaults.json")
        check(dev["identity"]["mode"] == "local" and dev["identity"]["entra_enabled"] is False and dev["identity"]["keycloak_required"] is False and dev["identity"]["smtp_required"] is False, "Local-first without external identity/SMTP")
        check(dev["web_client"]["required"] is True and all(dev["web_client"][x] is False for x in ("native_dictation_client", "native_wrapper", "native_host", "global_os_hotkeys", "foreign_window_automation", "byod_persistence_enabled")), "Web-only client boundary")
        check(dev["agent"]["purpose"] == "import_only" and all(dev["agent"][x] is False for x in ("capture_audio", "play_audio", "hid_control", "global_hotkeys", "office_automation", "local_rpc_listener", "firmware_flash_enabled", "device_configuration_writes_enabled")), "Import-only agent boundary")
        check(dev["asr"]["cloud_fallback"] is False and dev["asr"]["realtime_mode"] == "transcription_only", "Local transcription-only ASR")
        check(dev["privacy"]["allow_real_data"] is False and dev["privacy"]["production_approved"] is False, "No implicit production/real-data approval")
        boundary = read_json(b / "config/import-agent-boundary.json")
        check(not set(boundary["allowed_operations"]) & set(boundary["denied_operations"]), "No contradictory agent command allowlist")
        for name in ("microphone_capture", "audio_playback", "global_hotkeys", "office_automation", "firmware_flash", "generic_shell"):
            check(name in boundary["denied_operations"], f"Agent denial {name}")
        api = read_json(b / "contracts/parity-api.openapi.json")
        schemas = api["components"]["schemas"]
        check(len(api["x-command-authorization"]) == 21, "21 typed parity command contracts")
        for command in api["x-command-authorization"]:
            check(command["request_schema"] in schemas and bool(command["permission"]), f"Schema and permission {command['command']}")
            check(all(r in rs for r in command["requirements"]), f"Command requirement references {command['command']}")
        def check_refs(value: Any, document: dict[str, Any]) -> None:
            if isinstance(value, dict):
                if isinstance(value.get("$ref"), str) and value["$ref"].startswith("#/"):
                    node: Any = document
                    for part in value["$ref"][2:].split("/"):
                        node = node[part.replace("~1", "/").replace("~0", "~")]
                for child in value.values(): check_refs(child, document)
            elif isinstance(value, list):
                for child in value: check_refs(child, document)
        for api_name in ("clinic-api.openapi.json", "parity-api.openapi.json"):
            document = read_json(b / "contracts" / api_name); check_refs(document, document)
        checks.append("OpenAPI local references resolve")
        # Source handoff verifies functional extracts were not rewritten to fit the new architecture.
        for item in read_json(b / "evidence/dns-functional-handoff.json")["selected_files"]:
            path = safe_child(b, item["path"])
            check(path.is_file() and digest(path) == item["sha256"], f"Functional source SHA256 {item['path']}")
        if verify_integrity:
            manifest = read_json(b / "evidence/build-manifest.json")
            expected = {item["path"] for item in manifest["files"]}
            excluded = set(manifest.get("excluded", []))
            actual = {str(p.relative_to(b)).replace("\\", "/") for p in b.rglob("*") if p.is_file() and "__pycache__" not in p.parts} - excluded
            check(actual == expected, "Build manifest enumerates every deliverable")
            for item in manifest["files"]:
                path = safe_child(b, item["path"])
                check(path.is_file() and path.stat().st_size == item["bytes"] and digest(path) == item["sha256"], f"Build SHA256 {item['path']}")
        if full:
            originals = read_json(full / "03_Provenienz/originalartefakte.json")["artifacts"]
            check(len(originals) == 24, "24 mounted conversation originals included")
            counts["original_artifacts"] = len(originals)
            for item in originals:
                path = safe_child(full, item["path"])
                check(path.is_file() and path.stat().st_size == item["bytes"] and digest(path) == item["sha256"], f"Original SHA256 {item['path']}")
                if deep_originals and path.suffix.lower() == ".zip":
                    with zipfile.ZipFile(path) as z:
                        unsafe = [i.filename for i in z.infolist() if PurePosixPath(i.filename).is_absolute() or ".." in PurePosixPath(i.filename).parts]
                        check(not unsafe, f"Original ZIP safe member names {path.name}")
                        check(z.testzip() is None, f"Original ZIP CRC {path.name}")
                        if path.name == "DnsComfort_Claude_Gesamtpaket(1).zip":
                            counts["dns_archive_entries"] = len(z.infolist())
        counts.update(requirements=len(rs), tests=len(ts), original_parity_functions=len(fs), original_behavior_tests=len(ss), tasks=len(tasks), open_web_clause_conflicts=len(gaps["gaps"]), test_states=dict(collections.Counter(x.get("status", "missing") for x in tests)))
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile) as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
    return {"scope": "Specification/integrity only; not application, hardware, medical, KIS or parity validation.", "status": "passed" if not errors else "failed", "passed_check_count": len(checks), "errors": errors, "counts": counts, "product_tests_executed": False, "full_parity_validated": False, "production_approved": False}

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--deep-originals", action="store_true", help="Also stream/CRC-check original ZIPs; never execute/extract binaries.")
    parser.add_argument("--report", type=Path, help="Explicit output path for this validation report; parent must exist.")
    args = parser.parse_args()
    report = validate(args.root, verify_integrity=True, deep_originals=args.deep_originals)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.report:
        try:
            args.report.write_text(rendered + "\n", encoding="utf-8")
        except OSError as exc:
            print(f"Cannot write report: {exc}", file=sys.stderr); return 2
    return 0 if report["status"] == "passed" else 1

if __name__ == "__main__":
    sys.exit(main())
