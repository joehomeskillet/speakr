"""Synthetic OpenAPI contract checks. No HTTP route or engine is exercised here."""

import ast
from datetime import datetime
import json
from pathlib import Path
import re
from uuid import UUID

import pytest


ROOT = Path(__file__).resolve().parents[2]
SUPPLEMENT = ROOT / "clinic-evidence/contracts/training-web.openapi.json"
PARITY = ROOT / "clinic-spec/contracts/parity-api.openapi.json"
CLINIC = ROOT / "clinic-spec/contracts/clinic-api.openapi.json"
CATALOG = ROOT / "clinic-spec/contracts/parity-command-catalog.json"
AGENT = ROOT / "clinic-evidence/contracts/agent-channel.openapi.json"
def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def resolve(ref, origin):
    filename, pointer = ref.split("#", 1)
    path = (origin.parent / filename).resolve() if filename else origin
    assert path.is_relative_to(ROOT), ref
    node = load(path)
    for part in pointer.removeprefix("/").split("/"):
        node = node[part.replace("~1", "/").replace("~0", "~")]
    return node, path


def validate(value, schema, origin=SUPPLEMENT):
    """Minimal assertion validator for the JSON Schema keywords used by these fixtures."""
    if "$ref" in schema:
        target, path = resolve(schema["$ref"], origin)
        return validate(value, target, path)
    if "const" in schema:
        assert value == schema["const"]
    if "enum" in schema:
        assert value in schema["enum"]
    kind = schema.get("type")
    if kind is not None:
        valid = {
            "null": value is None,
            "boolean": type(value) is bool,
            "integer": type(value) is int,
            "string": isinstance(value, str),
            "array": isinstance(value, list),
            "object": isinstance(value, dict),
        }
        assert any(valid[t] for t in ([kind] if isinstance(kind, str) else kind))
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        assert value >= schema.get("minimum", value)
        assert value <= schema.get("maximum", value)
    if isinstance(value, str):
        assert len(value) >= schema.get("minLength", 0)
        assert len(value) <= schema.get("maxLength", len(value))
        if "pattern" in schema:
            assert re.fullmatch(schema["pattern"], value)
        if schema.get("format") == "uuid":
            UUID(value)
        if schema.get("format") == "date-time":
            assert datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None
    if isinstance(value, list):
        assert len(value) >= schema.get("minItems", 0)
        assert len(value) <= schema.get("maxItems", len(value))
        for item in value:
            validate(item, schema.get("items", {}), origin)
    if isinstance(value, dict):
        assert set(schema.get("required", ())) <= value.keys()
        if schema.get("additionalProperties") is False:
            assert value.keys() <= schema.get("properties", {}).keys()
        for key, item in value.items():
            validate(item, schema.get("properties", {}).get(key, {}), origin)


def response_schema(operation, code):
    response = operation["responses"][str(code)]
    if "$ref" in response:
        response, _ = resolve(response["$ref"], SUPPLEMENT)
    return response["content"][
        "application/problem+json" if code >= 400 else "application/json"
    ]["schema"]


def test_existing_operation_command_and_schema_mapping_are_exact():
    supplement, parity, catalog = load(SUPPLEMENT), load(PARITY), load(CATALOG)
    entries = {row["command"]: row for row in catalog["commands"]}
    expected = {"request_adaptation", "save_profile", "propose_vocabulary_change", "review_resource_change"}
    assert {row["command"] for row in supplement["x-existing-mappings"]} == expected
    for row in supplement["x-existing-mappings"]:
        assert (row["method"], row["path"], row["handler"]) == (
            "POST", "/api/clinic/v1/parity/commands", "executeParityCommand"
        )
        assert parity["paths"]["/parity/commands"]["post"]["operationId"] == row["operationId"]
        command = entries[row["command"]]
        assert (command["permission"], command["request_schema"]) == (
            row["permission"], row["request_schema"]
        )
        schema = parity["components"]["schemas"][row["request_schema"]]
        assert schema["properties"]["command"]["const"] == row["command"]
        assert {"command", "request_id", "expected_revision", "payload"} <= set(schema["required"])
    capabilities = parity["components"]["schemas"]["RequestAdaptation"]["properties"]["payload"]["properties"]["capability"]["enum"]
    assert set(capabilities) == {"pronunciation_training", "language_adaptation", "acoustic_adaptation"}


def test_references_resolve_and_new_operation_ids_are_unique():
    docs = (SUPPLEMENT, PARITY, CLINIC, AGENT)
    operation_ids = []
    for path in docs:
        for item in load(path)["paths"].values():
            operation_ids.extend(op["operationId"] for method, op in item.items()
                                 if method.lower() in {"get", "post", "put", "patch", "delete"})
    assert len(set(operation_ids)) == len(operation_ids)

    def check_refs(node):
        if isinstance(node, dict):
            if "$ref" in node:
                target, _ = resolve(node["$ref"], SUPPLEMENT)
                assert isinstance(target, dict)
            for child in node.values():
                check_refs(child)
        elif isinstance(node, list):
            for child in node:
                check_refs(child)

    check_refs(load(SUPPLEMENT))
    for op in load(SUPPLEMENT)["paths"].values():
        for method, detail in op.items():
            if method in {"get", "post", "put"}:
                assert "200" in detail["responses"]


def test_declared_service_entrypoints_exist_and_are_reused():
    for item in load(SUPPLEMENT)["paths"].values():
        for op in item.values():
            if not isinstance(op, dict) or "operationId" not in op:
                continue
            for entry in op.get("x-service-entrypoints", [op.get("x-service-entrypoint")]):
                if entry is None:
                    continue
                parts = entry.split(".")
                module = ".".join(parts[:3])
                symbols = parts[3:]
                source = ROOT / (module.replace(".", "/") + ".py")
                syntax = ast.parse(source.read_text(encoding="utf-8"))
                nodes = syntax.body
                for symbol in symbols:
                    matches = [node for node in nodes if isinstance(node, (ast.FunctionDef, ast.ClassDef))
                               and node.name == symbol]
                    assert len(matches) == 1, entry
                    nodes = matches[0].body if isinstance(matches[0], ast.ClassDef) else []


def test_positive_synthetic_read_and_command_payloads():
    doc = load(SUPPLEMENT)
    policy = {"revision": 1, "timezone": "Europe/Zurich", "minimum_audio_seconds": 90,
              "windows": [{"weekday": 2, "start_local": "22:00", "end_local": "23:00"}]}
    update = {"request_id": "b21d1f7e-2ef0-4e6d-9971-6667f71908f2", "expected_revision": 0,
              "minimum_audio_seconds": 90, "windows": policy["windows"]}
    hint = {"id": "f22039a1-d1df-4428-a7d6-bb12d21ae073", "written_form": "Beispiel",
            "phonetic_hint": "baɪʃpiːl", "source": "user", "revision_token": "synthetic-v1"}
    command = {"command": "save_pronunciation_hint", "request_id": update["request_id"],
               "expected_revision_token": "new", "payload": {"scope_type": "user", "scope_id": "4",
               "written_form": "Beispiel", "phonetic_hint": "baɪʃpiːl"}}
    status = {"profile_configured": False, "profile_version": None, "training_proven": False,
              "training_runs": 0, "adaptation_state": "capability_unavailable",
              "capability": "CAPABILITY_UNAVAILABLE"}
    schemas = doc["components"]["schemas"]
    for value, name in ((policy, "TrainingPolicy"), (update, "TrainingPolicyWrite"),
                        (command, "PronunciationWrite"), (status, "TrainingStatus"),
                        ({"items": [hint], "has_more": False}, "PronunciationList")):
        validate(value, schemas[name])
    with pytest.raises(AssertionError):
        validate({**update, "minimum_audio_seconds": -1}, schemas["TrainingPolicyWrite"])
    with pytest.raises(AssertionError):
        validate({**command, "payload": {**command["payload"], "unexpected": True}},
                 schemas["PronunciationWrite"])


@pytest.mark.parametrize("code,reason", [(401, "UNAUTHORIZED"), (403, "FORBIDDEN"),
                                         (409, "REVISION_CONFLICT"), (409, "IDEMPOTENCY_CONFLICT"),
                                         (429, "RATE_LIMITED"), (503, "DEPENDENCY_UNAVAILABLE")])
def test_unauthorized_stale_version_and_failure_are_typed(code, reason):
    doc = load(SUPPLEMENT)
    payload = {"type": "about:blank", "title": "Request denied", "status": code, "code": reason}
    for method, path in (("put", "/training/users/{user_id}/policy"),
                         ("post", "/pronunciations/commands")):
        operation = doc["paths"][path][method]
        validate(payload, response_schema(operation, code))
        assert code in {int(status) for status in operation["responses"]}
    if code == 403:
        assert "object permission" in doc["x-contract-semantics"]["authentication"]


def test_reads_require_authorization_and_have_bounded_results():
    doc = load(SUPPLEMENT)
    assert doc["security"] == [{"ClinicSession": []}]
    for path, method in (("/training/users/{user_id}/status", "get"),
                         ("/training/users/{user_id}/policy", "get"),
                         ("/pronunciations/users/{user_id}/hints", "get")):
        operation = doc["paths"][path][method]
        assert {"200", "401", "403", "404"} <= operation["responses"].keys()
        assert "requestBody" not in operation
    assert "each pool membership" in doc["x-contract-semantics"]["reads"]
    assert "65536 bytes" in doc["x-contract-semantics"]["limits"]


def test_csrf_revision_limits_replay_and_failure_guards():
    doc = load(SUPPLEMENT)
    for path, method, version in (
        ("/training/users/{user_id}/policy", "put", "expected_revision"),
        ("/pronunciations/commands", "post", "expected_revision_token"),
    ):
        operation = doc["paths"][path][method]
        headers = {doc["components"]["parameters"][ref["$ref"].rsplit("/", 1)[-1]]["name"]
                   for ref in operation["parameters"]}
        assert headers == {"X-CSRF-Token", "Idempotency-Key"}
        body = resolve(operation["requestBody"]["content"]["application/json"]["schema"]["$ref"], SUPPLEMENT)[0]
        assert {"request_id", version} <= set(body["required"])
        assert {"401", "403", "409", "413", "429", "503"} <= operation["responses"].keys()
    assert doc["components"]["parameters"]["Idempotency"]["schema"]["maxLength"] == 128
    assert doc["components"]["schemas"]["PronunciationList"]["properties"]["items"]["maxItems"] == 100
    semantics = doc["x-contract-semantics"]
    assert all(needle in semantics["writes"] for needle in (
        "409 REVISION_CONFLICT", "409 IDEMPOTENCY_CONFLICT", "identical committed response",
        "reconcile the original request_id",
    ))
    assert "8192 bytes" in semantics["limits"]
    assert all(needle in semantics["adaptation"] for needle in (
        "waiting_data", "waiting_window", "CAPABILITY_UNAVAILABLE", "evaluated model artifact",
    ))
    assert "422" in load(PARITY)["paths"]["/parity/commands"]["post"]["responses"]


def test_original_oracles_remain_open_and_unchanged_in_contract():
    doc = load(SUPPLEMENT)
    assert doc["x-implementation-status"] == "specified_not_implemented"
    assert set(doc["x-original-tests"]) == {"BT-ASR-004", "BT-ASR-005", "BT-VOC-007"}
    originals = load(ROOT / "clinic-spec/tests/dns-behavior-tests.original.json")
    matched = [entry for entry in originals["test_cases"] if entry["id"] in doc["x-original-tests"]]
    assert {entry["id"] for entry in matched} == set(doc["x-original-tests"])
    assert all(entry["status"] == "not_run" and entry["oracle_status"] == "specified_not_observed"
               for entry in matched)
    assert "Manufacturer interfaces" in doc["info"]["description"]
