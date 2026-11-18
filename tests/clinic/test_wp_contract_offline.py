"""Synthetic contract evidence for the additive managed-browser offline read."""

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from src.clinic import config
from src.clinic.offline_policy import (
    MANAGED_OFFLINE_PROFILE,
    USER_UNLOCK_KEY_POLICY,
    OfflineAuthorization,
    OfflineVersion,
    VerifiedBrowser,
    offline_capability,
)
from src.clinic.recorder_chunks import ChunkError, ChunkSession


ROOT = Path(__file__).resolve().parents[2]
SUPPLEMENT = ROOT / "clinic-evidence/contracts/offline-web.openapi.json"
CLINIC = ROOT / "clinic-spec/contracts/clinic-api.openapi.json"
PARITY = ROOT / "clinic-spec/contracts/parity-api.openapi.json"
CATALOG = ROOT / "clinic-spec/contracts/parity-command-catalog.json"
AGENT = ROOT / "clinic-evidence/contracts/agent-channel.openapi.json"


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def operations(document):
    for path, methods in document["paths"].items():
        for method, operation in methods.items():
            if method.lower() in {"get", "put", "post", "delete", "patch"}:
                yield method.upper(), path, operation


def refs(value):
    if isinstance(value, dict):
        if "$ref" in value:
            yield value["$ref"]
        for child in value.values():
            yield from refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from refs(child)


def resolve(origin, reference):
    location, pointer = reference.split("#", 1)
    target = (origin.parent / location).resolve() if location else origin
    assert target.is_relative_to(ROOT), reference
    node = load(target)
    for segment in pointer.lstrip("/").split("/"):
        node = node[segment.replace("~1", "/").replace("~0", "~")]
    return node


def test_all_refs_resolve_and_operation_ids_are_unique():
    contract = load(SUPPLEMENT)
    for reference in refs(contract):
        assert resolve(SUPPLEMENT, reference), reference

    seen = {}
    for source in (CLINIC, PARITY, AGENT, SUPPLEMENT):
        for method, path, operation in operations(load(source)):
            operation_id = operation["operationId"]
            assert operation_id not in seen, (operation_id, seen.get(operation_id), source)
            seen[operation_id] = (source.name, method, path)
    assert seen["getOfflineWebCapability"][1:] == ("GET", "/api/clinic/v1/offline-web/capability")
    assert not set(load(SUPPLEMENT)["paths"]) & set(load(CLINIC)["paths"])


def test_reused_method_path_handler_and_schema_mappings():
    contract, existing = load(SUPPLEMENT), load(CLINIC)
    mappings = contract["x-existing-operation-mappings"]
    assert {row["operationId"] for row in mappings} == {
        "beginIngest", "getIngest", "putChunk", "finalizeIngest",
        "listDictations", "getDictation", "submitDictation", "saveRevision",
        "getRevisionContent", "streamAudio",
    }
    for row in mappings:
        operation = existing["paths"][row["path"]][row["method"].lower()]
        assert operation["operationId"] == row["operationId"]
        assert row["handler"].startswith("w263-offline-api:")
        assert row["reuse"].startswith("src.clinic.")
        response = next(value for code, value in operation["responses"].items() if code.startswith("2"))
        if "response_schema" in row:
            assert response["content"]["application/json"]["schema"]["$ref"].endswith(
                "/" + row["response_schema"]
            )
            assert row["response_schema"] in existing["components"]["schemas"]
        else:
            assert row["response_media_type"] in response["content"]
        if "request_schema" in row:
            actual = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
            assert actual.endswith("/" + row["request_schema"])
            assert row["request_schema"] in existing["components"]["schemas"]
        elif "request_media_type" in row:
            assert row["request_media_type"] in operation["requestBody"]["content"]
        else:
            assert "requestBody" not in operation
    assert next(row for row in mappings if row["operationId"] == "putChunk")["max_body_bytes"] == 4194304

    parity, catalog = load(PARITY), load(CATALOG)
    commands = {entry["command"]: entry for entry in catalog["commands"]}
    for row in contract["x-existing-parity-command-mappings"]:
        assert row["path"] == parity["servers"][0]["url"] + "/parity/commands"
        assert row["method"] == "POST"
        assert row["operationId"] == parity["paths"]["/parity/commands"]["post"]["operationId"]
        assert commands[row["command"]]["request_schema"] == row["request_schema"]
        assert row["request_schema"] in parity["components"]["schemas"]


def test_capability_requires_bound_browser_and_error_semantics():
    contract = load(SUPPLEMENT)
    operation = contract["paths"]["/api/clinic/v1/offline-web/capability"]["get"]
    assert operation["security"] == [{"browserSession": []}]
    assert set(operation["responses"]) == {"200", "400", "401", "403", "412", "428", "503"}
    assert operation["responses"]["200"]["headers"]["Cache-Control"]["schema"]["const"] == "no-store"
    assert {item["name"] for item in operation["parameters"]} == {
        "X-Offline-Previous-Version", "X-Offline-Previous-Browser-Id",
        "X-Offline-Previous-Account-Version",
    }
    schemas = contract["components"]["schemas"]
    assert schemas["OfflineGranted"]["properties"]["quota_bytes"]["maximum"] == 134217728
    assert schemas["OfflineGranted"]["properties"]["key_policy"]["const"] == USER_UNLOCK_KEY_POLICY
    assert all(schemas[name]["additionalProperties"] is False for name in ("OfflineGranted", "OfflineDenied"))
    assert contract["x-manufacturer-interfaces"] == "unresolved"
    assert "X-CSRFToken" in contract["x-reused-write-guards"]
    assert "If-Match" in contract["x-reused-write-guards"]
    assert "Idempotency-Key" in contract["x-reused-write-guards"]


def test_policy_positive_unauthorized_stale_and_retry(monkeypatch):
    monkeypatch.setenv("CLINIC_MODE", "1")
    monkeypatch.setitem(config.dev_defaults()["web_client"], "managed_offline_enabled", True)
    browser = VerifiedBrowser("synthetic-account-version", "synthetic-browser", MANAGED_OFFLINE_PROFILE)
    reference = OfflineVersion(browser, 7)
    approval = OfflineAuthorization(reference, 4096, USER_UNLOCK_KEY_POLICY)
    granted = offline_capability(browser, approval, previous=None)
    assert granted.available and granted.authorization == approval
    assert offline_capability(browser, approval, previous=reference) == granted
    assert not offline_capability(None, approval, previous=reference).available
    assert not offline_capability(replace(browser, browser_id="foreign"), approval, previous=reference).available
    assert not offline_capability(browser, approval, previous=replace(reference, version=6)).available
    assert not offline_capability(browser, None, previous=reference).available
    assert "nicht verfügbar" in offline_capability(browser, None, previous=reference).message
    replacement = replace(approval, reference=replace(reference, version=8))
    assert not offline_capability(browser, replacement, previous=reference).available
    with monkeypatch.context() as disabled:
        disabled.setitem(config.dev_defaults()["web_client"], "managed_offline_enabled", False)
        assert not offline_capability(browser, approval, previous=None).available


def test_reused_chunk_guard_handles_retry_and_failed_upload():
    audio = b"RIFF\x08\x00\x00\x00WAVE" + b"\x00" * 8
    upload = ChunkSession("synthetic-offline", 2, "audio/wav", max_bytes=32)
    assert not upload.accept(2, 0, audio).duplicate
    assert upload.accept(2, 0, audio).duplicate
    with pytest.raises(ChunkError, match="conflict"):
        upload.accept(2, 0, audio + b"x")
    with pytest.raises(ChunkError, match="stale_generation"):
        upload.accept(1, 1, b"x")
    with pytest.raises(ChunkError, match="gap"):
        upload.accept(2, 2, b"x")
    digest = hashlib.sha256(audio).hexdigest()
    assert upload.finalize(total_chunks=1, total_bytes=len(audio), sha256=digest) == audio
    assert upload.finalize(total_chunks=1, total_bytes=len(audio), sha256=digest) == audio
    with pytest.raises(ChunkError, match="digest_mismatch"):
        upload.finalize(total_chunks=1, total_bytes=len(audio), sha256="0" * 64)


def test_original_behavior_oracle_remains_open():
    tests = load(ROOT / "clinic-spec/tests/dns-behavior-tests.original.json")
    oracle = next(case for case in tests["test_cases"] if case["id"] == "BT-REC-007")
    assert oracle["then"] == [
        "Lokale Aufnahme ist wieder spielbar.",
        "Fehlender Versand bleibt erkennbar und wiederholbar.",
        "Neubau hängt nicht von der historischen 30-Tage-Lizenzfrist ab.",
    ]
    assert oracle["status"] == "not_run"
    assert oracle["oracle_status"] == "specified_not_observed"
