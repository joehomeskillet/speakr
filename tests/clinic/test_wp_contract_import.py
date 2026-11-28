"""Static web-import contract checks using public sources and synthetic payloads.

HTTP route implementation and device acceptance belong to scheduled consumers.
"""

import ast
from collections import Counter
import json
from pathlib import Path
import re
from uuid import UUID

import pytest


ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "clinic-evidence/contracts/import-web.openapi.json"
SOURCES = (
    ROOT / "clinic-spec/contracts/clinic-api.openapi.json",
    ROOT / "clinic-spec/contracts/parity-api.openapi.json",
    ROOT / "clinic-evidence/contracts/agent-channel.openapi.json",
    CONTRACT,
)


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve(source, ref):
    filename, separator, pointer = ref.partition("#")
    assert separator and pointer.startswith("/"), ref
    target = (source.parent / filename).resolve() if filename else source
    assert target.is_relative_to(ROOT), target
    value = _read(target)
    for component in pointer[1:].split("/"):
        value = value[component.replace("~1", "/").replace("~0", "~")]
    return value


def _refs(value):
    if isinstance(value, dict):
        if "$ref" in value:
            yield value["$ref"]
        for item in value.values():
            yield from _refs(item)
    elif isinstance(value, list):
        for item in value:
            yield from _refs(item)


def _operations(document):
    return {
        (method.upper(), path): operation
        for path, methods in document["paths"].items()
        for method, operation in methods.items()
        if isinstance(operation, dict) and "operationId" in operation
    }


def _op(operation_id):
    return next(
        op for op in _operations(_read(CONTRACT)).values()
        if op["operationId"] == operation_id
    )


def _validate(schema, payload):
    """Exercise supported OpenAPI constraints without adding a test dependency."""
    if "$ref" in schema:
        return _validate(_resolve(CONTRACT, schema["$ref"]), payload)
    if payload is None and schema.get("nullable"):
        return
    if "enum" in schema:
        assert payload in schema["enum"]
    kind = schema.get("type")
    if kind == "object":
        assert isinstance(payload, dict)
        assert set(schema.get("required", ())) <= payload.keys()
        if schema.get("additionalProperties") is False:
            assert payload.keys() <= schema["properties"].keys()
        assert len(payload) >= schema.get("minProperties", 0)
        for name, value in payload.items():
            if name in schema.get("properties", {}):
                _validate(schema["properties"][name], value)
    elif kind == "array":
        assert isinstance(payload, list)
        assert len(payload) <= schema.get("maxItems", float("inf"))
        if schema.get("uniqueItems"):
            assert len({json.dumps(x, sort_keys=True) for x in payload}) == len(payload)
        for item in payload:
            _validate(schema["items"], item)
    elif kind == "string":
        assert isinstance(payload, str)
        assert schema.get("minLength", 0) <= len(payload) <= schema.get("maxLength", float("inf"))
        if "pattern" in schema:
            assert re.fullmatch(schema["pattern"], payload)
        if schema.get("format") == "uuid":
            UUID(payload)
    elif kind == "integer":
        assert type(payload) is int
        assert schema.get("minimum", -float("inf")) <= payload <= schema.get("maximum", float("inf"))
    elif kind == "boolean":
        assert type(payload) is bool


def test_refs_and_operation_ids_across_all_contracts():
    documents = {path: _read(path) for path in SOURCES}
    for source, document in documents.items():
        for ref in _refs(document) if source == CONTRACT else ():
            assert isinstance(_resolve(source, ref), dict), (source, ref)
    ids = [operation["operationId"]
           for document in documents.values()
           for operation in _operations(document).values()]
    assert len(ids) == len(set(ids)), Counter(ids)
    operations = _operations(documents[CONTRACT])
    assert len(operations) == 12
    assert {method for method, path in operations if path == "/api/clinic/v1/imports/devices"} == {"GET"}
    assert documents[CONTRACT]["x-contract-status"] == "specified_not_implemented"
    assert documents[CONTRACT]["x-manufacturer-interfaces"] == "unresolved"


def test_existing_method_path_command_and_service_mappings():
    document = _read(CONTRACT)
    catalog = _read(ROOT / "clinic-spec/contracts/parity-command-catalog.json")
    commands = {entry["command"]: entry for entry in catalog["commands"]}
    mappings = document["x-existing-operations"]
    assert len(mappings) == 13
    assert {m.get("commandId") for m in mappings if m.get("commandId")} == {
        "queue_import", "request_source_cleanup",
    }
    for mapping in mappings:
        other = (CONTRACT.parent / mapping["contract"]).resolve()
        existing = _operations(_read(other))[(mapping["method"], mapping["path"])]
        assert existing["operationId"] == mapping["operationId"]
        if "commandId" in mapping:
            command = commands[mapping["commandId"]]
            assert command["request_schema"] == mapping["schema"].split("/")[-1]
            assert isinstance(_resolve(other, mapping["schema"]), dict)
        if mapping["handler"]:
            _assert_public_callable(mapping["handler"])
    for operation in _operations(document).values():
        for handler in operation["x-handler"].split(" + "):
            _assert_public_callable(handler)


def _assert_public_callable(handler):
    module, function = handler.rsplit(".", 1)
    path = ROOT / (module.replace(".", "/") + ".py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    assert any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
               and node.name == function for node in tree.body), handler


@pytest.mark.parametrize("schema_name,payload", [
    ("ImportIntentCreate", {"source_kind": "device", "source_ref": "device-1",
                            "expected_files": ["synthetic.ds2"], "language_id": 1031}),
    ("SourceConfigCreate", {"source_kind": "folder", "source_ref": "drop-1",
                            "approved_root": "/safe/drop", "target_author_user_id": 7,
                            "priority": 128, "allow_source_delete": False}),
    ("SourceConfigPatch", {"expected_version": 3, "eol_filter_enabled": True}),
    ("AssignImport", {"intent_id": "11111111-1111-4111-8111-111111111111",
                      "expected_delivery_status": "UNASSIGNED",
                      "expected_intent_status": "OPEN"}),
    ("GrantConsent", {"expected_consent_id": 0, "statement_text": "Ich willige ein."}),
    ("RevokeConsent", {"expected_consent_id": 9}),
])
def test_positive_synthetic_commands(schema_name, payload):
    _validate(_read(CONTRACT)["components"]["schemas"][schema_name], payload)


def test_positive_synthetic_service_response():
    result = {
        "id": "11111111-1111-4111-8111-111111111111",
        "source_kind": "folder", "source_ref": "drop-1", "approved_root": "/safe/drop",
        "target_author_user_id": 7, "target_pool_id": None, "autosend_enabled": False,
        "eol_filter_enabled": True, "file_pattern": "*", "priority": 1,
        "language_id": 1031, "asr_profile_id": None, "policy_version": 1,
        "allow_source_delete": False, "created_at": "2026-11-18T12:00:00+00:00",
        "updated_at": "2026-11-18T12:00:00+00:00",
    }
    _validate(_read(CONTRACT)["components"]["schemas"]["SourceConfig"], result)


@pytest.mark.parametrize("schema_name,payload", [
    ("ImportIntentCreate", {"source_kind": "device", "source_ref": "ok", "actor_user_id": 99}),
    ("SourceConfigCreate", {"source_kind": "folder", "source_ref": "ok",
                            "approved_root": "/safe", "target_author_user_id": 2,
                            "policy_version": 55}),
    ("SourceConfigCreate", {"source_kind": "device", "source_ref": "ok",
                            "approved_root": "/safe", "target_author_user_id": 2,
                            "allow_source_delete": "yes"}),
    ("SourceConfigPatch", {"expected_version": 2}),
    ("SourceConfigPatch", {"expected_version": 0, "priority": 1}),
    ("SourceConfigPatch", {"expected_version": 2, "priority": 4}),
    ("GrantConsent", {"expected_consent_id": -1, "statement_text": "Nein"}),
    ("RevokeConsent", {"expected_consent_id": 0}),
    ("AssignImport", {"intent_id": "11111111-1111-4111-8111-111111111111",
                      "expected_delivery_status": "MATCHED", "expected_intent_status": "OPEN"}),
])
def test_invalid_or_forged_payloads_rejected_by_schema(schema_name, payload):
    with pytest.raises((AssertionError, ValueError)):
        _validate(_read(CONTRACT)["components"]["schemas"][schema_name], payload)


def test_auth_csrf_object_scope_and_response_limits():
    document = _read(CONTRACT)
    assert document["security"] == [{"browserSession": []}]
    policy = document["x-http-policy"]
    assert "session" in policy["actor"]
    assert "on every operation" in policy["object_permissions"]
    for (method, _path), operation in _operations(document).items():
        assert operation["x-permission"]
        assert {"401", "403"} <= operation["responses"].keys()
        if method != "GET":
            params = {_resolve(CONTRACT, p["$ref"])["name"] for p in operation["parameters"]}
            assert params >= {"X-CSRF-Token", "Idempotency-Key"}
            assert {"409", "503"} <= operation["responses"].keys()
            assert operation["requestBody"]["required"] is True
        for response in operation["responses"].values():
            if "content" in response and "application/json" in response["content"]:
                schema = response["content"]["application/json"]["schema"]
                if schema.get("type") == "array":
                    assert schema["maxItems"] <= 500
    assert "admin or lead" in _op("listUnassignedImports")["x-permission"]
    assert "foreign author" in _op("assignUnassignedImport")["x-permission"]
    assert "own internal user.id" in _op("grantOwnVoiceConsent")["x-permission"]


def test_stale_revision_duplicate_retry_and_ambiguous_failure_contracts():
    schemas = _read(CONTRACT)["components"]["schemas"]
    for name, field in [("SourceConfigPatch", "expected_version"),
                        ("GrantConsent", "expected_consent_id"),
                        ("RevokeConsent", "expected_consent_id")]:
        assert field in schemas[name]["required"]
    assert schemas["SourceConfigPatch"]["properties"]["expected_version"]["minimum"] == 1
    assert schemas["GrantConsent"]["properties"]["expected_consent_id"]["minimum"] == 0
    assert "atomically" in _op("assignUnassignedImport")["x-revision"]
    assert "increments exactly once" in _op("updateImportSourceConfig")["x-revision"]
    policy = _read(CONTRACT)["x-http-policy"]
    for text in ("Same key and payload returns identical status/body",
                 "changed payload is 409", "reconcile before replay",
                 "Stale state/version returns 409"):
        assert text in (policy["idempotency"] + policy["revision"])


def test_original_behaviour_oracle_stays_open():
    original = _read(ROOT / "clinic-spec/tests/dns-behavior-tests.original.json")
    case = next(test for test in original["test_cases"] if test["id"] == "BT-IMP-001")
    assert case["status"] == "not_run"
    assert case["oracle_status"] == "specified_not_observed"
    assert case["capture"] == ["UI vor/nach Aktion", "relevante Datei-/Ereignisdifferenz",
                               "sichtbarer Fehler oder Erfolg"]
    assert _read(CONTRACT)["x-original-oracle"]["web_gaps"] == "remain_open"
