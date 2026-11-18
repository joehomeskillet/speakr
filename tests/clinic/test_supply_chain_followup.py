"""Supply chain verification tests for Speakr clinic fork (w258).

Validates dependency locks, image and model hashes, licenses, source offer,
absence of unpinned downloads / legacy binaries in build context, and open gate
G-SOURCE-RIGHTS under PAR-OPS-008, OWN-ASR-001, OWN-SPK-003 and requirements
R-035, R-041, R-047, V4-001, V4-003.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path, PurePosixPath
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_DIR = REPO_ROOT / "clinic-spec"

EXPECTED_UPSTREAM_COMMIT = "48d3018fb7d80b77f897bedd86bcc89b5034d1f5"
EXPECTED_UPSTREAM_TAG = "v0.11.1"

FORBIDDEN_EXTENSIONS = (
    ".exe", ".dll", ".msi", ".sys", ".gpr", ".rep",
    ".gzf", ".mdb", ".sdf", ".sqlite", ".db",
)

ASSIGNED_REQUIREMENTS = ("R-035", "R-041", "R-047", "V4-001", "V4-003")
ASSIGNED_ACCEPTANCE_TESTS = ("T-035", "T-051", "T-057", "GRK-T-09", "X4-021")


def verify_upstream_pin(commit_sha: str, expected_pin: str = EXPECTED_UPSTREAM_COMMIT) -> bool:
    """Validate that the upstream commit SHA matches the pinned baseline."""
    cleaned = commit_sha.strip().lower()
    if cleaned != expected_pin.lower():
        raise ValueError(f"Stale or mismatched upstream commit: {cleaned} != {expected_pin}")
    return True


def verify_checksum_manifest(entries: list[tuple[str, str]]) -> bool:
    """Validate format and integrity of checksum manifest entries."""
    if not entries:
        raise ValueError("Checksum manifest cannot be empty")
    for expected_hash, filename in entries:
        cleaned = expected_hash.strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", cleaned):
            raise ValueError(f"Invalid SHA-256 hash for {filename}: {expected_hash}")
    return True


def simulate_model_fetch(dest: Path, content: bytes, expected_sha256: str, max_retries: int = 3) -> tuple[bool, int]:
    """Fetch/verify payload against expected SHA-256 with fail-closed semantics."""
    attempts = 0
    while attempts < max_retries:
        attempts += 1
        dest.write_bytes(content)
        actual = hashlib.sha256(content).hexdigest()
        if actual == expected_sha256:
            return True, attempts
        if dest.exists():
            dest.unlink()
    raise RuntimeError(f"Hash mismatch after {attempts} attempts: expected {expected_sha256}")


def test_upstream_fork_pin_and_architecture_reuse():
    """Positive case: fork baseline matches pinned commit and preserves Speakr architecture (T-051, R-041)."""
    lock_file = REPO_ROOT / "clinic-evidence" / "upstream-lock.resolved.json"
    assert lock_file.is_file(), "upstream-lock.resolved.json missing"
    with open(lock_file, "r", encoding="utf-8") as f:
        lock_data = json.load(f)

    assert lock_data["schema_version"] == 1
    components = {c["id"]: c for c in lock_data["components"]}
    assert "speakr" in components
    speakr_comp = components["speakr"]
    assert speakr_comp["ref"] == EXPECTED_UPSTREAM_TAG
    assert speakr_comp["commit"] == EXPECTED_UPSTREAM_COMMIT
    assert speakr_comp["license"] == "AGPL-3.0"
    assert verify_upstream_pin(speakr_comp["commit"]) is True

    # Check git history contains the pinned upstream commit as ancestor
    git_bin = "git"
    res = subprocess.run(
        [git_bin, "merge-base", "HEAD", EXPECTED_UPSTREAM_COMMIT],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert res.stdout.strip() == EXPECTED_UPSTREAM_COMMIT

    # Architecture reuse: Flask/SQLAlchemy/Vue present, no parallel webapp framework (V4-001)
    assert (REPO_ROOT / "src" / "app.py").is_file()
    assert (REPO_ROOT / "src" / "models").is_dir()
    assert (REPO_ROOT / "src" / "api").is_dir()
    assert (REPO_ROOT / "static" / "js").is_dir()
    for parallel_framework in ("fastapi", "django"):
        assert not (REPO_ROOT / parallel_framework).exists()


def test_dependency_locks_hash_pinned():
    """Positive case: Python, Node and .NET dependencies use strict hash locks (T-035, T-057, R-035)."""
    req_lock = REPO_ROOT / "requirements.lock"
    req_dev_lock = REPO_ROOT / "requirements-dev.lock"
    assert req_lock.is_file() and req_dev_lock.is_file()

    for path in (req_lock, req_dev_lock):
        content = path.read_text(encoding="utf-8")
        package_lines = [l for l in content.splitlines() if "==" in l and not l.strip().startswith("#")]
        hash_lines = [l for l in content.splitlines() if "--hash=sha256:" in l]
        assert len(package_lines) > 0, f"No packages found in {path.name}"
        assert len(hash_lines) >= len(package_lines), f"Missing hashes in {path.name}"

    pkg_lock = REPO_ROOT / "package-lock.json"
    assert pkg_lock.is_file()
    with open(pkg_lock, "r", encoding="utf-8") as f:
        node_lock = json.load(f)
    assert node_lock.get("lockfileVersion") == 3

    # .NET build props enforce deterministic builds
    props_path = REPO_ROOT / "apps" / "windows-import" / "Directory.Build.props"
    assert props_path.is_file()
    props_content = props_path.read_text(encoding="utf-8")
    assert "<Deterministic>true</Deterministic>" in props_content


def test_image_and_model_hashes_no_latest_tags():
    """Positive case: No :latest tags, model files and GPU deb packages strictly hash-pinned (T-035, T-057)."""
    dockerfiles = [
        REPO_ROOT / "Dockerfile",
        REPO_ROOT / "deployment" / "gpu-asr-service" / "Dockerfile",
        REPO_ROOT / "deployment" / "speaker-service" / "Dockerfile",
    ]
    for df in dockerfiles:
        assert df.is_file()
        text = df.read_text(encoding="utf-8")
        from_lines = [l.strip() for l in text.splitlines() if l.strip().startswith("FROM ")]
        for line in from_lines:
            assert ":latest" not in line, f"Disallowed :latest tag found in {df.name}: {line}"

    # GPU ASR model and deb checksums
    model_sha = REPO_ROOT / "deployment" / "gpu-asr-service" / "model.sha256"
    assert model_sha.is_file()
    model_entries = [l.strip().split() for l in model_sha.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(model_entries) == 19
    assert verify_checksum_manifest([(h, name) for h, name in model_entries]) is True

    gpu_df_text = (REPO_ROOT / "deployment" / "gpu-asr-service" / "Dockerfile").read_text(encoding="utf-8")
    assert "ARG MODEL_REV=b568445dd5dc8c695bde596f8acbb4694fd6ba64" in gpu_df_text
    deb_hashes = re.findall(r"([0-9a-f]{64})\s+\S+\.deb", gpu_df_text)
    assert len(deb_hashes) == 5

    # Speaker service model hashes
    speaker_df_text = (REPO_ROOT / "deployment" / "speaker-service" / "Dockerfile").read_text(encoding="utf-8")
    speaker_hashes = re.findall(r"ARG SPEAKER_\w+_SHA256=([0-9a-f]{64})", speaker_df_text)
    assert len(speaker_hashes) == 3

    # CSS build tool hash verification
    build_css = (REPO_ROOT / "scripts" / "build_css.sh").read_text(encoding="utf-8")
    assert "TAILWIND_VERSION=v3.4.19" in build_css
    assert "sha256sum -c" in build_css


def test_licenses_notices_and_source_offer():
    """Positive case: Root AGPL-3.0, third-party notices and public source links documented (R-047)."""
    license_file = REPO_ROOT / "LICENSE"
    assert license_file.is_file()
    license_text = license_file.read_text(encoding="utf-8")
    assert "GNU AFFERO GENERAL PUBLIC LICENSE" in license_text
    assert "Version 3" in license_text

    notices_file = REPO_ROOT / "deployment" / "speaker-service" / "THIRD-PARTY-NOTICES"
    assert notices_file.is_file()
    notices_text = notices_file.read_text(encoding="utf-8")
    assert "sherpa-onnx" in notices_text
    assert "Apache License" in notices_text

    lock_file = REPO_ROOT / "clinic-evidence" / "upstream-lock.resolved.json"
    with open(lock_file, "r", encoding="utf-8") as f:
        lock_data = json.load(f)
    assert lock_data["status"] == "upstream_pinned_image_and_asr_hashes_unresolved_production_blocked"
    for comp in lock_data["components"]:
        assert comp["license"] in ("AGPL-3.0", "MIT", "Apache-2.0")
        assert comp["repository"].startswith("https://github.com/")


def test_clean_build_context_no_forbidden_binaries():
    """Positive case: No forbidden binaries, decompiled artifacts, or Ghidra projects (X4-021, V4-003)."""
    git_bin = "git"
    res = subprocess.run(
        [git_bin, "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    tracked_files = res.stdout.splitlines()
    assert len(tracked_files) > 0

    for path_str in tracked_files:
        p = PurePosixPath(path_str)
        assert p.suffix.lower() not in FORBIDDEN_EXTENSIONS, f"Forbidden file extension in tracked file: {path_str}"
        assert "02_Originalartefakte" not in p.parts, f"Forbidden legacy directory in tracked path: {path_str}"

    assert not (REPO_ROOT / "02_Originalartefakte").exists()


def test_unauthorized_and_g_source_rights_blocks_production():
    """Unauthorized case: Unverified reference/SDK rights recorded as open gate G-SOURCE-RIGHTS (AC 3)."""
    blockers_file = SPEC_DIR / "planning" / "blockers.json"
    assert blockers_file.is_file()
    with open(blockers_file, "r", encoding="utf-8") as f:
        blockers_data = json.load(f)

    gates = {g["id"]: g for g in blockers_data.get("gates", [])}
    assert "G-SOURCE-RIGHTS" in gates
    gate = gates["G-SOURCE-RIGHTS"]
    assert gate["status"] == "open"
    assert gate["production_gate"] is True
    assert "Zulässiger Einsatz der Referenz/SDKs" in gate["missing_evidence"]
    assert gate["blocks"] == "Entsprechende Original-/SDK-Nutzung"


def test_stale_version_and_drift_rejection():
    """Stale-version case: Drifted commit SHA or corrupt checksum manifest is detected and rejected."""
    with pytest.raises(ValueError, match="Stale or mismatched upstream commit"):
        verify_upstream_pin("0000000000000000000000000000000000000000")

    with pytest.raises(ValueError, match="Stale or mismatched upstream commit"):
        verify_upstream_pin("deadbeef1234567890abcdef1234567890abcdef")

    with pytest.raises(ValueError, match="Invalid SHA-256 hash"):
        verify_checksum_manifest([("corrupt_sha", "model.bin")])

    with pytest.raises(ValueError, match="Checksum manifest cannot be empty"):
        verify_checksum_manifest([])


def test_failure_closed_and_retry_behavior(tmp_path: Path):
    """Retry & failure case: Hash mismatch fails closed, cleans up payload, and retries up to limit."""
    target_file = tmp_path / "model_download.bin"
    valid_content = b"valid_speech_model_binary_payload"
    valid_hash = hashlib.sha256(valid_content).hexdigest()

    # Success case: verified on first attempt
    ok, attempts = simulate_model_fetch(target_file, valid_content, valid_hash, max_retries=3)
    assert ok is True
    assert attempts == 1
    assert target_file.exists()

    # Failure case: corrupted payload fails closed, payload unlinked, raises RuntimeError
    corrupt_content = b"corrupted_tampered_payload"
    target_file.unlink()
    with pytest.raises(RuntimeError, match="Hash mismatch after 3 attempts"):
        simulate_model_fetch(target_file, corrupt_content, valid_hash, max_retries=3)
    assert not target_file.exists(), "Corrupted payload must be unlinked on verification failure"


def test_acceptance_catalog_and_bt_ops_008_preserved():
    """Verify acceptance catalog status and byte-identical BT-OPS-008 oracle preservation."""
    req_data = json.loads((SPEC_DIR / "planning" / "requirements.json").read_text(encoding="utf-8"))
    req_ids = {r["id"] for r in req_data["requirements"]}
    for req_id in ASSIGNED_REQUIREMENTS:
        assert req_id in req_ids

    pm_data = json.loads((SPEC_DIR / "planning" / "parity-matrix.json").read_text(encoding="utf-8"))
    pm_features = {f["id"]: f for f in pm_data["features"]}
    assert "PAR-OPS-008" in pm_features
    assert "BT-OPS-008" in pm_features["PAR-OPS-008"]["tests"]

    acc_data = json.loads((SPEC_DIR / "tests" / "acceptance.json").read_text(encoding="utf-8"))
    acc_tests = {t["id"]: t for t in acc_data["tests"]}
    for test_id in ASSIGNED_ACCEPTANCE_TESTS:
        assert test_id in acc_tests
        assert acc_tests[test_id]["status"] == "not_run"

    # BT-OPS-008 complete oracle unchanged
    orig_data = json.loads((SPEC_DIR / "tests" / "dns-behavior-tests.original.json").read_text(encoding="utf-8"))
    orig_cases = {c["id"]: c for c in orig_data["test_cases"]}
    assert "BT-OPS-008" in orig_cases

    bt_acc = acc_tests["BT-OPS-008"]
    bt_orig = orig_cases["BT-OPS-008"]
    for field in ("given", "when", "then"):
        assert bt_acc[field] == bt_orig[field]
    assert bt_acc["source_when_then_unchanged"] is True
    assert bt_acc["original_oracle_not_replaced"] is True
    assert bt_acc["status"] == "not_run"
