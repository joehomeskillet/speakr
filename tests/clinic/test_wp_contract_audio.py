"""Synthetic contract checks for the proposed Speakr web audio extensions."""

import ast
import copy
import hashlib
import json
from pathlib import Path

import pytest

from src.clinic.metadata_changes import ChangeError, plan_changes
from src.clinic.recorder_chunks import ChunkError, ChunkSession
from src.clinic.sdict_bundle import BundleError, BundleLimits, read_bundle


ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "clinic-evidence/contracts/audio-web.openapi.json"
ORIGINAL = ROOT / "clinic-spec/contracts/clinic-api.openapi.json"
PARITY = ROOT / "clinic-spec/contracts/parity-api.openapi.json"
AGENT = ROOT / "clinic-evidence/contracts/agent-channel.openapi.json"
CATALOG = ROOT / "clinic-spec/contracts/parity-command-catalog.json"
METHODS = {"get", "post", "patch", "put", "delete"}


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def operations(document):
    for path, item in document["paths"].items():
        for method, operation in item.items():
            if method in METHODS:
                yield method, path, operation


def resolve_ref(source, ref):
    external, separator, pointer = ref.partition("#")
    assert separator and pointer.startswith("/"), ref
    target = (source.parent / external).resolve() if external else source
    assert target.is_file(), ref
    value = load(target)
    for key in pointer[1:].split("/"):
        value = value[key.replace("~1", "/").replace("~0", "~")]
    return value


def validate(document):
    existing = [load(path) for path in (ORIGINAL, PARITY, AGENT)]
    known = [op["operationId"] for base in existing for _, _, op in operations(base)]
    own = [op["operationId"] for _, _, op in operations(document)]
    assert len(known + own) == len(set(known + own)), "duplicate operationId"

    def walk(value):
        if isinstance(value, dict):
            if "type" in value:
                assert isinstance(value["type"], str), "OpenAPI 3.0 requires a single schema type"
            if "$ref" in value:
                assert resolve_ref(CONTRACT, value["$ref"]), "unresolved schema"
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(document)


def parameter_names(document, item, operation):
    names = set()
    for param in item.get("parameters", []) + operation.get("parameters", []):
        source = CONTRACT
        while "$ref" in param:
            reference = param["$ref"]
            external = reference.partition("#")[0]
            param = resolve_ref(source, reference)
            if external:
                source = (source.parent / external).resolve()
        assert param["required"] if param["in"] == "path" else True
        names.add(param["name"])
    return names


def assert_handler_exists(handler):
    head, *additional = handler.split("/")
    module_name, entry = head[len("src.clinic."):].split(".", 1)
    source = ROOT / "src/clinic" / f"{module_name}.py"
    assert source.is_file(), handler
    tree = ast.parse(source.read_text(encoding="utf-8"))
    for name in [entry, *additional]:
        parts = name.split(".")
        nodes = tree.body
        for part in parts:
            matching = [node for node in nodes if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == part]
            assert len(matching) == 1, handler
            nodes = matching[0].body


def test_unique_ids_and_all_local_and_external_schema_references():
    contract = load(CONTRACT)
    validate(contract)
    duplicates = copy.deepcopy(contract)
    next(iter(operations(duplicates)))[2]["operationId"] = "getDictation"
    with pytest.raises(AssertionError, match="duplicate operationId"):
        validate(duplicates)
    broken = copy.deepcopy(contract)
    broken["components"]["schemas"]["TtsPreview"]["properties"]["text_version"] = {"$ref": "#/components/schemas/Unspecified"}
    with pytest.raises(KeyError, match="Unspecified"):
        validate(broken)
    incompatible = copy.deepcopy(contract)
    incompatible["components"]["schemas"]["Properties"]["properties"]["deadline_date"]["type"] = ["string", "null"]
    with pytest.raises(AssertionError, match="single schema type"):
        validate(incompatible)


def test_reused_exact_methods_paths_parity_ids_schemas_and_handlers():
    contract = load(CONTRACT)
    baselines = {"clinic-api.openapi.json": load(ORIGINAL), "parity-api.openapi.json": load(PARITY)}
    commands = {item["command"]: item for item in load(CATALOG)["commands"]}
    groups = set()
    for reused in contract["x-existing-operations"]:
        groups.add(reused["group"])
        original = baselines[reused["source"]]
        operation = original["paths"][reused["path"]][reused["method"].lower()]
        assert operation["operationId"] == reused["operationId"]
        assert reused["schema"] in original["components"]["schemas"]
        if "command" in reused:
            assert commands[reused["command"]]["request_schema"] == reused["schema"]
            assert reused["operationId"] == "executeParityCommand"
        assert_handler_exists(reused["handler"])
    assert groups == {"audio-api", "dictation-properties-api", "live-api", "correction-api"}
    assert {"bundle-api", "tts-api"} == {op["x-consumer"].split("-", 1)[1] for _, _, op in operations(contract) if op["x-consumer"] in {"w095-bundle-api", "w097-tts-api"}}


def test_new_operations_are_additive_and_browser_only():
    contract = load(CONTRACT)
    baseline_paths = {(method, path) for source in (ORIGINAL, PARITY, AGENT) for method, path, _ in operations(load(source))}
    assert contract["x-clinic-mode-only"] is True
    assert contract["security"] == [{"browserSession": []}]
    assert "13" in contract["x-open-web-gaps"]
    assert len(list(operations(contract))) == 11
    for method, path, operation in operations(contract):
        assert (method, path) not in baseline_paths
        assert path.startswith("/api/clinic/v1/")
        assert operation["x-handler"].startswith("src.clinic.")
        assert_handler_exists(operation["x-handler"])
        assert operation["x-permission"]
        assert {"401", "403"} <= operation["responses"].keys(), path


def test_write_commands_require_csrf_idempotency_and_current_revision():
    contract = load(CONTRACT)
    for method, path, operation in operations(contract):
        if method == "get":
            continue
        params = parameter_names(contract, contract["paths"][path], operation)
        assert {"X-CSRFToken", "Idempotency-Key"} <= params
        assert {"401", "403", "409"} <= operation["responses"].keys()
        if "{dictation_id}" in path:
            assert "If-Match" in params
            assert {"412", "428"} <= operation["responses"].keys()
    csrf = contract["components"]["parameters"]["Csrf"]
    assert csrf["in"] == "header" and csrf["required"] is True
    assert "stale 412" in contract["x-compatibility"]["writes"]
    assert "changed payload under the same key is 409" in contract["x-compatibility"]["writes"]


def test_request_response_bounds_and_local_only_semantics():
    contract = load(CONTRACT)
    schemas = contract["components"]["schemas"]
    assert schemas["PropertiesPatch"]["properties"]["priority"]["enum"] == [0, 1, 128]
    assert schemas["PropertiesPatch"]["properties"]["title"]["maxLength"] == 200
    assert schemas["AudioLinkPage"]["properties"]["links"]["maxItems"] == 200
    assert schemas["AudioRevisionView"]["properties"]["segments"]["maxItems"] == 5000
    assert schemas["TtsPreview"]["properties"]["pronunciation"]["maxLength"] == 2000
    assert schemas["AcknowledgeLive"]["required"] == ["seq", "audio_offset_ms"]
    assert BundleLimits().max_entries == 200
    assert BundleLimits().max_entry_bytes == 268435456
    assert BundleLimits().max_total_bytes == 536870912
    for _, path, operation in operations(contract):
        if path.endswith("/bundle"):
            assert operation["responses"]["200"]["content"]["application/zip"]["schema"]["maxLength"] == 536870912
        if path.endswith("/tts/preview"):
            assert operation["responses"]["200"]["content"]["audio/wav"]["schema"]["maxLength"] == 8388608
            assert "503" in operation["responses"]
    assert "No cloud" in contract["x-compatibility"]["local_only"]


def test_word_audio_sync_review_markers_and_administrator_policy_are_explicit():
    rules = load(CONTRACT)["x-compatibility"]
    for clause in ("EVERY saveRevision", "unchanged word timestamps", "neighbouring times", "without an audio anchor", "review markers blocking final release"):
        assert clause in rules["text_audio"]
    for clause in ("active admin full read", "audited access"):
        assert clause in rules["browser"]
    assert "typist-pool leads" in rules["permissions"]
    assert "Ordinary typists access their assigned dictations only" in rules["permissions"]


def test_reused_recorder_positive_stale_retry_and_failure_are_real_service_cases():
    data = b"\x1a\x45\xdf\xa3synthetic-audio"
    chunk = ChunkSession("synthetic", 4, "audio/webm", max_bytes=128)
    assert chunk.accept(4, 0, data).accepted
    assert chunk.accept(4, 0, data).duplicate
    with pytest.raises(ChunkError, match="conflict"):
        chunk.accept(4, 0, data + b"different")
    with pytest.raises(ChunkError, match="stale_generation"):
        chunk.accept(3, 1, b"additional")
    with pytest.raises(ChunkError, match="gap"):
        chunk.accept(4, 2, b"additional")
    sha = hashlib.sha256(data).hexdigest()
    assert chunk.finalize(total_chunks=1, total_bytes=len(data), sha256=sha) == data
    assert chunk.finalize(total_chunks=1, total_bytes=len(data), sha256=sha) == data
    with pytest.raises(ChunkError, match="digest_mismatch"):
        chunk.finalize(total_chunks=1, total_bytes=len(data), sha256="0" * 64)


def test_reused_properties_permission_and_invalid_payload_fail_closed():
    old = {"title": "Alt", "priority": 1}
    changed = plan_changes(old, {"title": "Neu", "priority": 128}, allowed_actions={"rename", "priority"})
    assert [(field.field, field.new) for field in changed.changes] == [("title", "Neu"), ("priority", 128)]
    with pytest.raises(ChangeError) as denied:
        plan_changes(old, {"priority": 128}, allowed_actions={"rename"})
    assert denied.value.code == "forbidden"
    with pytest.raises(ChangeError) as invalid:
        plan_changes(old, {"priority": 2}, allowed_actions={"priority"})
    assert invalid.value.code == "invalid_priority"
    with pytest.raises(BundleError) as bad:
        read_bundle(b"synthetic-not-a-zip")
    assert bad.value.code == "bad_archive"
