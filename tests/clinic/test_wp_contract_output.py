"""Contract-only evidence for the proposed Speakr output web extension."""

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.clinic.job_health import summarise
from src.clinic.rich_text_doc import from_plain_text, to_clipboard_flavors


ROOT = Path(__file__).resolve().parents[2]
EXTENSION = "clinic-evidence/contracts/output-web.openapi.json"
BASE = (
    "clinic-spec/contracts/clinic-api.openapi.json",
    "clinic-spec/contracts/parity-api.openapi.json",
    "clinic-evidence/contracts/agent-channel.openapi.json",
)


def load(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def operations(spec):
    for path, methods in spec["paths"].items():
        for method, operation in methods.items():
            if method.lower() in {"get", "put", "post", "patch", "delete"}:
                yield method.upper(), path, operation


def resolve_ref(spec, ref):
    assert ref.startswith("#/"), ref
    value = spec
    for segment in ref[2:].split("/"):
        value = value[segment.replace("~1", "/").replace("~0", "~")]
    return value


def local_refs(value):
    if isinstance(value, dict):
        if "$ref" in value:
            yield value["$ref"]
        for nested in value.values():
            yield from local_refs(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from local_refs(nested)


def request_schema(spec, method, path):
    return spec["paths"][path][method.lower()]["requestBody"]["content"]["application/json"]["schema"]


def test_all_openapi_references_resolve_and_operation_ids_are_unique():
    specs = [load(path) for path in (*BASE, EXTENSION)]
    ids = []
    for spec in specs:
        assert spec["openapi"] in {"3.0.3", "3.1.0"}
        ids.extend(operation["operationId"] for _, _, operation in operations(spec))
        for ref in local_refs(spec):
            assert resolve_ref(spec, ref) is not None, ref
    assert len(ids) == len(set(ids))


def test_exact_existing_method_path_schema_and_handler_mappings():
    spec = load(EXTENSION)
    catalog = {command["command"]: command for command in load(
        "clinic-spec/contracts/parity-command-catalog.json"
    )["commands"]}
    expected = {
        ("POST", "/api/clinic/v1/dictations/{dictation_id}/deliveries"): ("requestDelivery", "DeliveryRequest"),
        ("GET", "/api/clinic/v1/deliveries/{delivery_id}"): ("getDelivery", "Delivery"),
        ("POST", "/api/clinic/v1/deliveries/{delivery_id}/reconcile"): ("reconcileDelivery", "Delivery"),
        ("GET", "/api/clinic/v1/deliveries/{delivery_id}/artifact"): ("downloadArtifact", "inline:application/octet-stream"),
        ("GET", "/api/clinic/v1/dictations/{dictation_id}/revisions/{revision_id}"): ("getRevisionContent", "TranscriptContent"),
        ("GET", "/api/clinic/v1/dictations/{dictation_id}/context"): ("readContext", "ContextView"),
        ("POST", "/api/clinic/v1/dictations/{dictation_id}/context"): ("bindContext", "ContextBind"),
        ("POST", "/api/clinic/v1/parity/commands"): ("executeParityCommand", None),
    }
    assert len(spec["x-reused-operations"]) == 10
    for mapping in spec["x-reused-operations"]:
        key = mapping["method"], mapping["path"]
        operation_id, schema_name = expected[key]
        existing = load(mapping["document"])
        existing_path = mapping["path"]
        if existing_path not in existing["paths"]:
            prefix = existing["servers"][0]["url"]
            assert existing_path.startswith(prefix)
            existing_path = existing_path[len(prefix):]
        operation = existing["paths"][existing_path][mapping["method"].lower()]
        assert operation["operationId"] == mapping["operationId"] == operation_id
        assert mapping["handler"] and mapping["consumer"].startswith("w")
        if command_id := mapping.get("commandId"):
            assert catalog[command_id]["request_schema"] == mapping["schema"]
            assert resolve_ref(existing, f"#/components/schemas/{mapping['schema']}")
        elif schema_name == "inline:application/octet-stream":
            assert "application/octet-stream" in operation["responses"]["200"]["content"]
        else:
            assert mapping["schema"] == schema_name
            assert resolve_ref(existing, f"#/components/schemas/{schema_name}")
    assert {mapping.get("commandId") for mapping in spec["x-reused-operations"] if mapping.get("commandId")} == {
        "render_output", "render_report", "queue_import"
    }


def test_additive_operations_do_not_shadow_existing_paths_or_commands():
    spec = load(EXTENSION)
    prior = [load(path) for path in BASE]
    full_routes = {(method, path) for other in prior for method, path, _ in operations(other)}
    commands = {row["command"] for row in load("clinic-spec/contracts/parity-command-catalog.json")["commands"]}
    own_commands = []
    for method, path, operation in operations(spec):
        assert (method, "/api/clinic/v1" + path) not in full_routes
        assert operation["x-consumer"] in {
            "w106-output-api", "w107-clipboard-api", "w109-statistics-api", "w110-jobs-api"
        }
        assert operation["x-required-role-policy"]
        assert operation.get("x-handler")
        if command_id := operation.get("x-command-id"):
            own_commands.append(command_id)
            assert method in {"PUT", "POST"}
            assert command_id not in commands
    assert len(own_commands) == len(set(own_commands)) == 2
    assert "speakr" in spec["info"]["title"].lower()
    assert "unresolved" in spec["x-manufacturer-interfaces"]
    assert "CLINIC_MODE" in spec["info"]["description"]


def test_positive_clipboard_uses_existing_renderer_and_bounded_schema():
    spec = load(EXTENSION)
    doc = from_plain_text("Beispiel <Diktat>", lambda: "node-1")
    flavors = to_clipboard_flavors(doc)
    schema = spec["components"]["schemas"]["ClipboardFlavors"]
    assert set(flavors) == {"text/plain", "text/html"}
    assert "&lt;Diktat&gt;" in flavors["text/html"]
    assert all(len(value.encode("utf-8")) <= 1048576 for value in flavors.values())
    assert set(schema["required"]) == {"revision_id", *flavors}
    assert all(schema["properties"][name]["maxLength"] == 1048576 for name in flavors)
    assert "409" in spec["paths"]["/dictations/{dictation_id}/clipboard/{revision_id}"]["get"]["responses"]


def test_positive_scoped_statistics_and_preferences_shapes():
    spec = load(EXTENSION)
    stats = spec["paths"]["/statistics/aggregates"]["get"]
    query = {item["name"]: item for item in stats["parameters"]}
    assert all(query[name]["required"] for name in ("start", "end", "scope_version"))
    assert "explicit" in stats["x-required-role-policy"]
    assert "412" in stats["responses"]
    assert spec["components"]["schemas"]["StatisticsResult"]["properties"]["buckets"]["maxItems"] == 4000
    assert spec["components"]["schemas"]["OutputTargets"]["maxItems"] == 8
    assert spec["components"]["schemas"]["OutputTargets"]["x-unique-key"] == ["channel", "target_alias"]
    assert set(spec["components"]["schemas"]["SavePreferences"]["required"]) == {
        "request_id", "expected_version", "targets"
    }
    assert set(spec["components"]["schemas"]["OutputPreferences"]["required"]) == {"version", "targets"}


def test_unauthorized_and_csrf_refusals_are_explicit():
    spec = load(EXTENSION)
    for method, _, operation in operations(spec):
        assert "401" in operation["responses"]
        if method in {"PUT", "POST"}:
            assert "403" in operation["responses"]
            params = [resolve_ref(spec, p["$ref"]) if "$ref" in p else p for p in operation["parameters"]]
            headers = {parameter["name"] for parameter in params if parameter["in"] == "header" and parameter["required"]}
            assert headers == {"X-CSRF-Token", "Idempotency-Key", "If-Match"}
    assert spec["security"] == [{"ClinicSession": []}]
    assert spec["paths"]["/jobs/health"]["get"]["x-required-role-policy"] == "authorized clinic operations administrator"
    for path in ("/dictations/{dictation_id}/output-attempts", "/dictations/{dictation_id}/clipboard/{revision_id}"):
        assert "active admin full read access" in spec["paths"][path]["get"]["x-required-role-policy"]


def test_stale_versions_and_precondition_errors_are_defined():
    spec = load(EXTENSION)
    if_match = spec["components"]["parameters"]["IfMatch"]
    assert re.fullmatch(if_match["schema"]["pattern"], '"12"')
    assert not re.fullmatch(if_match["schema"]["pattern"], 'W/"12"')
    assert "stale=412" in if_match["description"]
    for method, path in (("put", "/users/me/output-preferences"), ("post", "/jobs/{job_id}/retry")):
        operation = spec["paths"][path][method]
        body = resolve_ref(spec, request_schema(spec, method, path)["$ref"])
        assert "expected_version" in body["required"]
        assert {"409", "412", "428"} <= set(operation["responses"])


def test_retry_failure_reporting_reuses_health_projection_and_freezes_unknown():
    spec = load(EXTENSION)
    now = datetime(2026, 11, 18, tzinfo=timezone.utc)
    rows = [{"id": 1, "status": "failed", "job_type": "transcribe",
             "created_at": now - timedelta(hours=1), "completed_at": now,
             "error_message": "ValueError: patient@example.invalid"}]
    summary = summarise(rows, now)
    failure = summary.recent_failures[0]
    assert failure.message == "Fehlerdetails ausgeblendet."
    assert "patient@example.invalid" not in repr(failure)
    assert set(summary.counts) == {"queued", "processing", "completed", "failed"}
    retry = spec["paths"]["/jobs/{job_id}/retry"]["post"]
    assert {"202", "409", "412", "503"} <= set(retry["responses"])
    assert "UNKNOWN" in retry["description"]
    assert "identical replay" in retry["responses"]["202"]["description"]
    assert spec["components"]["schemas"]["JobHealth"]["properties"]["recent_failures"]["maxItems"] == 10
    ledger = spec["paths"]["/dictations/{dictation_id}/output-attempts"]["get"]
    assert "UNKNOWN" in ledger["description"]
    assert "no silent truncation" in ledger["description"]
    assert {item["name"] for item in ledger["parameters"] if item.get("in") == "query"} == {"limit", "cursor"}
    assert spec["components"]["schemas"]["OutputAttempts"]["properties"]["items"]["maxItems"] == 50
    assert "next_cursor" in spec["components"]["schemas"]["OutputAttempts"]["required"]
    assert "UNKNOWN" in spec["components"]["schemas"]["OutputAttempt"]["properties"]["status"]["enum"]


def test_original_behaviour_oracles_remain_unexecuted():
    original = load("clinic-spec/tests/dns-behavior-tests.original.json")
    cases = {case["id"]: case for case in original["test_cases"]}
    for case_id in ("BT-EXP-001", "BT-OPS-008"):
        assert cases[case_id]["status"] == "not_run"
        assert cases[case_id]["oracle_status"] == "specified_not_observed"
