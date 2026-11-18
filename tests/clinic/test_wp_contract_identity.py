"""Synthetic identity-web contract checks; no deployed HTTP/SSO oracle is claimed."""

import json
import re
import uuid
from pathlib import Path

import pytest

from src.clinic.directory_reconcile import DirectoryEntry, LocalUser, compute_diff


ROOT = Path(__file__).resolve().parents[2]
SOURCES = (
    "clinic-spec/contracts/clinic-api.openapi.json",
    "clinic-spec/contracts/parity-api.openapi.json",
    "clinic-evidence/contracts/agent-channel.openapi.json",
    "clinic-evidence/contracts/identity-web.openapi.json",
)


def load(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def resolve(document, pointer):
    assert pointer.startswith("#/"), pointer
    value = document
    for part in pointer[2:].split("/"):
        value = value[part.replace("~1", "/").replace("~0", "~")]
    return value


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def operations(doc):
    for path, item in doc["paths"].items():
        for method, operation in item.items():
            if method.lower() in {"get", "put", "post", "patch", "delete"}:
                yield method.upper(), path, operation


def test_internal_references_and_no_duplicate_operation_ids():
    seen = {}
    for source in SOURCES:
        document = load(source)
        for node in walk(document):
            if "$ref" in node:
                assert resolve(document, node["$ref"]), (source, node["$ref"])
        for method, path, operation in operations(document):
            operation_id = operation["operationId"]
            assert operation_id not in seen, (operation_id, seen.get(operation_id), source)
            seen[operation_id] = (source, method, path)


def test_reuse_maps_to_existing_operations_schemas_and_flask_handlers():
    contract = load(SOURCES[-1])
    reuse = contract["x-reused-operations"]
    assert {row["slice"] for row in reuse} == {
        "identity-providers-api", "directory-api", "pools-api", "grants-api", "preferences-api"
    }
    catalog = load("clinic-spec/contracts/parity-command-catalog.json")["commands"]
    catalog_by_id = {entry["command"]: entry for entry in catalog}
    for row in reuse:
        source = row["source"]
        if source.endswith(".json"):
            document = load(source)
            op_by_path = {(method, path): op for method, path, op in operations(document)}
            key = (row["method"], row["path"])
            if key not in op_by_path:
                key = (row["method"], row["path"].removeprefix("/api/clinic/v1"))
            assert op_by_path[key]["operationId"] == row["operationId"]
            for field in ("request_schema", "response_schema"):
                if field in row:
                    assert resolve(document, row[field])
            if "command" in row:
                assert catalog_by_id[row["command"]]["request_schema"] == row["request_schema"].rsplit("/", 1)[-1]
        else:
            content = (ROOT / source).read_text(encoding="utf-8")
            flask_path = re.sub(r"\{(\w+)\}", r"<int:\1>", row["path"])
            if source == "src/api/api_v1.py":
                assert "url_prefix='/api/v1'" in content
                flask_path = flask_path.removeprefix("/api/v1")
            route_pattern = r"@\w+_bp\.route\(['\"]" + re.escape(flask_path) + r"['\"]"
            assert any(
                re.search(r"\bdef " + row["handler"] + r"\(", content[route.start():route.start() + 180])
                for route in re.finditer(route_pattern, content)
            ), (source, flask_path, row["handler"])
    assert set(contract["x-reused-parity-command-ids"]) == {"save_author_pool", "set_author_scope"}
    assert {row["handler"] for row in reuse if row["source"] == "src/api/auth.py"} == {
        "sso_login", "sso_callback", "sso_link", "sso_unlink"
    }
    assert {row["handler"] for row in reuse if row["source"] == "src/api/admin.py"} == {
        "admin_get_users", "admin_add_user", "admin_update_user", "admin_delete_user", "admin_toggle_admin"
    }


def _matches(schema, value, definitions):
    if "$ref" in schema:
        return _matches(resolve({"components": {"schemas": definitions}}, schema["$ref"]), value, definitions)
    if "oneOf" in schema:
        return sum(_matches(child, value, definitions) for child in schema["oneOf"]) == 1
    if "const" in schema and value != schema["const"]:
        return False
    if "enum" in schema and value not in schema["enum"]:
        return False
    types = schema.get("type")
    if isinstance(types, str):
        types = [types]
    check = {
        "object": lambda v: isinstance(v, dict),
        "array": lambda v: isinstance(v, list),
        "string": lambda v: isinstance(v, str),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "boolean": lambda v: isinstance(v, bool),
        "null": lambda v: v is None,
    }
    if types and not any(check[t](value) for t in types):
        return False
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        if not set(schema.get("required", [])).issubset(value):
            return False
        if schema.get("additionalProperties") is False and set(value) - set(properties):
            return False
        return all(_matches(properties[key], item, definitions) for key, item in value.items() if key in properties)
    if isinstance(value, list):
        if len(value) > schema.get("maxItems", float("inf")):
            return False
        if schema.get("uniqueItems") and len(set(map(str, value))) != len(value):
            return False
        return all(_matches(schema["items"], item, definitions) for item in value) if "items" in schema else True
    if isinstance(value, str):
        return schema.get("minLength", 0) <= len(value) <= schema.get("maxLength", float("inf"))
    if isinstance(value, int):
        return schema.get("minimum", float("-inf")) <= value <= schema.get("maximum", float("inf"))
    return True


def test_positive_and_invalid_payloads_cover_owned_commands_and_preview():
    definitions = load(SOURCES[-1])["components"]["schemas"]
    request_id = str(uuid.uuid4())
    create = {"command": "create_local_user", "request_id": request_id,
              "payload": {"username": "example", "email": "user@example.invalid"}}
    update = {"command": "set_user_status", "request_id": request_id,
              "expected_version": 2, "payload": {"user_id": 1, "status": "disabled", "reason": "Test"}}
    revoke = {"command": "revoke_user_sessions", "request_id": request_id,
              "expected_version": 2, "payload": {"user_id": 1}}
    for command in (create, update, revoke):
        assert _matches(definitions["IdentityCommand"], command, definitions)
    assert not _matches(definitions["IdentityCommand"], {**update, "expected_version": 0}, definitions)
    assert not _matches(definitions["IdentityCommand"], {**update, "payload": {"user_id": 1}}, definitions)
    assert not _matches(definitions["IdentityCommand"], {**create, "payload": {**create["payload"], "password": "secret"}}, definitions)
    entry = {"object_id": "immutable-1", "account": "demo", "display_name": "Demo", "enabled": True}
    assert _matches(definitions["PreviewRequest"], {"entries": [entry]}, definitions)
    assert not _matches(definitions["PreviewRequest"], {"entries": [entry] * 5001}, definitions)
    assert not _matches(definitions["PreviewRequest"], {"entries": [], "locals": []}, definitions)
    diff = compute_diff([DirectoryEntry("immutable-1", "demo", "Demo", None, True)],
                        [LocalUser(1, "demo", "immutable-1", "Demo", True)])
    assert not diff.new and not diff.changed and not diff.ambiguous


def test_unauthorized_stale_revision_retry_and_failure_are_explicit():
    doc = load(SOURCES[-1])
    paths = doc["paths"]
    commands = paths["/api/clinic/v1/identity/commands"]["post"]
    preview = paths["/api/clinic/v1/identity/directory/preview"]["post"]
    grants = paths["/api/clinic/v1/identity/users/{user_id}/grants"]["get"]
    assert "identity_admin" in preview["x-permission"]
    assert "own user.id" in grants["x-permission"] and "identity_admin" in grants["x-permission"]
    assert {"401", "403", "409", "422", "429", "503"} <= commands["responses"].keys()
    assert {"401", "403", "413"} <= preview["responses"].keys()
    assert {"401", "403", "404"} <= grants["responses"].keys()
    assert [resolve(doc, item["$ref"])["name"] for item in commands["parameters"]] == [
        "X-CSRF-Token", "Idempotency-Key"
    ]
    assert [resolve(doc, item["$ref"])["name"] for item in preview["parameters"]] == ["X-CSRF-Token"]
    semantics = doc["x-adapter-rules"]
    assert "Stale updates return 409 with no side effect" in semantics["revisions"]
    assert "Identical retry returns the original result" in semantics["idempotency"]
    assert "conflicting reuse returns 409" in semantics["idempotency"]
    assert "No partial writes" in semantics["failure"]
    assert "last active locally usable identity administrator" in semantics["last_admin"]
    assert "409 and no side effect" in semantics["last_admin"]
    assert "CLINIC_MODE deny legacy DELETE" in semantics["legacy_delete"]
    assert doc["x-status"] == "proposed_not_implemented"
    assert doc["x-manufacturer-interfaces"] == "unresolved"
    assert doc["x-original-oracles"] == ["BT-ADM-001", "BT-ADM-010"]
