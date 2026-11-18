"""Contract checks for the additive clinic web worklist proposal (w272)."""

from __future__ import annotations

import ast
import json
import re
import unittest
from pathlib import Path

from src.clinic.worklist_filters import query_worklist
from src.clinic.policy import Subject
from src.clinic.worklist_layout import LayoutStore, WorklistLayoutError


ROOT = Path(__file__).resolve().parents[2]
SUPPLEMENT = ROOT / "clinic-evidence/contracts/worklist-web.openapi.json"
CLINIC = ROOT / "clinic-spec/contracts/clinic-api.openapi.json"
PARITY = ROOT / "clinic-spec/contracts/parity-api.openapi.json"
CATALOG = ROOT / "clinic-spec/contracts/parity-command-catalog.json"
ORACLE = ROOT / "clinic-spec/tests/dns-behavior-tests.original.json"
METHODS = {"get", "put", "post", "patch", "delete"}
AREAS = {
    "worklist-api", "workflow-api", "history-api", "templates-api",
    "categories-api", "regex-api", "columns-api",
}


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def operations(doc: dict):
    for path, item in doc["paths"].items():
        for method, op in item.items():
            if method in METHODS:
                yield method.upper(), path, op


def refs(node):
    if isinstance(node, dict):
        if "$ref" in node:
            yield node["$ref"]
        for value in node.values():
            yield from refs(value)
    elif isinstance(node, list):
        for value in node:
            yield from refs(value)


def resolve(base: Path, reference: str):
    file_part, sep, pointer = reference.partition("#")
    assert sep == "#" and pointer.startswith("/"), reference
    target_file = (base.parent / file_part).resolve() if file_part else base
    assert target_file.is_file(), reference
    node = read(target_file)
    for token in pointer.lstrip("/").split("/"):
        node = node[token.replace("~1", "/").replace("~0", "~")]
    return node


def source_symbol(qualified: str):
    module, symbol = qualified.removeprefix("src.clinic.").split(".", 1)
    file = ROOT / "src" / "clinic" / f"{module}.py"
    assert file.is_file(), qualified
    tree = ast.parse(file.read_text(encoding="utf-8"))
    part = symbol.split(".")
    for name in part:
        tree = next(node for node in tree.body if isinstance(
            node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ) and node.name == name)


class WorklistWebContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = read(SUPPLEMENT)
        cls.sources = {"clinic-api": read(CLINIC), "parity-api": read(PARITY)}

    def test_existing_operations_and_parity_commands_are_reused_exactly(self):
        existing_ids = [op["operationId"] for doc in self.sources.values() for _, _, op in operations(doc)]
        proposed = list(operations(self.doc))
        proposed_ids = [op["operationId"] for _, _, op in proposed]
        self.assertEqual(len(existing_ids), len(set(existing_ids)))
        self.assertEqual(len(proposed_ids), len(set(proposed_ids)))
        self.assertTrue(set(existing_ids).isdisjoint(proposed_ids))
        existing_paths = {(method, path) for doc in self.sources.values() for method, path, _ in operations(doc)}
        self.assertTrue(existing_paths.isdisjoint((method, path) for method, path, _ in proposed))

        mapped = self.doc["x-reused-operations"]
        self.assertEqual(set(mapped), AREAS)
        catalog = {row["command"]: row for row in read(CATALOG)["commands"]}
        for entries in mapped.values():
            for entry in entries:
                source = self.sources[entry["source"]]
                original = source["paths"][entry["path"]][entry["method"].lower()]
                self.assertEqual(original["operationId"], entry["operationId"])
                self.assertIn(entry["schema"], source["components"]["schemas"])
                if "commandId" in entry:
                    self.assertEqual(entry["source"], "parity-api")
                    self.assertEqual(entry["schema"], catalog[entry["commandId"]]["request_schema"])
                else:
                    self.assertIn(
                        f"#/components/schemas/{entry['schema']}",
                        set(refs(original)),
                    )
                source_symbol(entry["handler"])
        history = mapped["history-api"]
        self.assertEqual({item["operationId"] for item in history}, {"getRevisionContent", "saveRevision"})
        self.assertIn("automatic text/audio synchronization", self.doc["x-reuse-note"])

    def test_all_schema_references_are_resolvable(self):
        self.assertEqual(self.doc["openapi"], "3.1.0")
        self.assertIn("Speakr", self.doc["info"]["title"])
        self.assertIn("unresolved", self.doc["info"]["description"])
        self.assertIn("CLINIC_MODE", self.doc["info"]["description"])
        resolved = [resolve(SUPPLEMENT, ref) for ref in refs(self.doc)]
        self.assertGreater(len(resolved), 30)
        for value in resolved:
            self.assertIsInstance(value, dict)

    def test_all_missing_operations_have_real_service_entries(self):
        entries = list(operations(self.doc))
        self.assertEqual({op["tags"][0] for _, _, op in entries}, AREAS)
        for method, path, op in entries:
            self.assertEqual(len(op["tags"]), 1)
            source_symbol(op["x-handler"])
            self.assertIn("401", op["responses"])
            success = {"200", "201", "202", "204"} & set(op["responses"])
            self.assertEqual(len(success), 1, (method, path))
            path_parameters = self.doc["paths"][path].get("parameters", [])
            path_parameters += op.get("parameters", [])
            names = {
                resolve(SUPPLEMENT, param["$ref"])["name"] if "$ref" in param else param["name"]
                for param in path_parameters
            }
            self.assertTrue(set(re.findall(r"\{([^}]+)\}", path)) <= names)

    def test_write_guards_and_retry_failure_contract(self):
        for method, _, op in operations(self.doc):
            if method not in {"PUT", "POST", "PATCH", "DELETE"}:
                continue
            parameter_refs = {p["$ref"].split("/")[-1] for p in op.get("parameters", [])}
            self.assertIn("Csrf", parameter_refs)
            if op["operationId"] == "previewClinicRegexRules":
                self.assertNotIn("Idempotency", parameter_refs)
                continue
            self.assertGreaterEqual(len(parameter_refs), 3)
            self.assertTrue({"Idempotency", "IfMatch"}.issubset(parameter_refs))
            self.assertTrue({"401", "403", "409", "412", "428", "503"}.issubset(op["responses"]))
            self.assertTrue(op["requestBody"]["required"])
        policy = self.doc["x-write-policy"]
        for constraint in ("atomic transaction", "actor+operation+object+key", "identical request hash", "409", "412", "428", "503", "reconciled"):
            self.assertIn(constraint, policy)

    def test_positive_examples_and_response_limits(self):
        schemas = self.doc["components"]["schemas"]
        expected_columns = {"dictation_id", "title", "status", "author_user_id", "priority", "deadline_at", "assignee_user_id", "assigned_at", "notes", "created_at", "duration_seconds", "recipient", "source_kind", "open_mode", "revision"}
        self.assertEqual(set(schemas["WorklistRow"]["required"]), expected_columns)
        self.assertEqual(schemas["WorklistRow"]["properties"]["priority"]["enum"], [0, 1, 128])
        self.assertIn("document", schemas["WorklistRow"]["properties"]["source_kind"]["enum"])
        self.assertEqual(schemas["WorklistPage"]["properties"]["items"]["maxItems"], 100)
        self.assertEqual(schemas["TemplateWrite"]["properties"]["content_base64"]["maxLength"], 2796204)
        self.assertIn("application/vnd.oasis.opendocument.text", schemas["TemplateWrite"]["properties"]["mime_type"]["enum"])
        self.assertEqual(schemas["LayoutWrite"]["properties"]["columns"]["maxItems"], 30)
        self.assertEqual(schemas["LayoutWrite"]["properties"]["group_by"]["maxItems"], 3)
        self.assertEqual(self.doc["components"]["parameters"]["PageSize"]["schema"]["maximum"], 100)
        for name in ("CategoryWrite", "RegexWrite", "TemplateWrite", "LayoutWrite"):
            self.assertFalse(schemas[name]["additionalProperties"])
        history = self.doc["paths"]["/api/clinic/v1/dictations/{dictation_id}/history"]["get"]
        page_schema = history["responses"]["200"]["content"]["application/json"]["schema"]
        self.assertEqual(set(page_schema["required"]), {"items", "total", "page", "page_size"})
        self.assertEqual(page_schema["properties"]["items"]["maxItems"], 100)

    def test_visibility_admin_pool_leads_and_open_flags_are_explicit(self):
        scope = self.doc["x-read-policy"]
        for rule in ("administrators", "access auditing", "typist-pool leads", "any author", "Ordinary typists see only their assignments"):
            self.assertIn(rule, scope)
        self.assertIn("current policy.decide does not grant", scope)
        self.assertIn("workflow.worklist currently includes unassigned", scope)
        workflow = self.doc["paths"]["/api/clinic/v1/dictations/{dictation_id}/workflow"]["post"]
        self.assertIn("review markers remain open", workflow["description"])
        self.assertIn("edit lease", workflow["description"])

    def test_existing_services_positive_unauthorized_stale_and_write_retry(self):
        layout = {"version": 1, "columns": [{"key": "status", "width": 140, "visible": True}], "sort": [], "group_by": []}
        writes = {"count": 0}

        def writer(_user, _version, _text):
            writes["count"] += 1
            if writes["count"] == 1:
                raise OSError("synthetic storage failure")

        store = LayoutStore(writer)
        with self.assertRaisesRegex(OSError, "synthetic storage failure"):
            store.save(11, 11, layout, ["status"], expected_version=0)
        self.assertIsNone(store.snapshot(11, 11))
        saved = store.save(11, 11, layout, ["status"], expected_version=0)
        self.assertEqual(saved.version, 1)
        self.assertEqual(store.load(11, 11, layout, ["status"]).layout, layout)
        with self.assertRaisesRegex(WorklistLayoutError, "unauthorized"):
            store.load(12, 11, layout, ["status"])
        with self.assertRaisesRegex(WorklistLayoutError, "stale_version"):
            store.save(11, 11, layout, ["status"], expected_version=0)
        self.assertEqual(store.snapshot(11, 11)[0], 1)

        rows = [{"id": 1, "owner_id": 11, "title": "Eigen"}, {"id": 2, "owner_id": 12, "title": "Fremd"}]
        visible = query_worklist(rows, Subject(11), page=1, page_size=1)
        self.assertEqual(visible.total, 1)
        self.assertEqual([row["id"] for row in visible.items], [1])

    def test_original_oracles_remain_pending(self):
        cases = {case["id"]: case for case in read(ORACLE)["test_cases"]}
        for test_id, parity in (("BT-WF-001", "PAR-WF-001"), ("BT-CFG-002", "PAR-CFG-002")):
            case = cases[test_id]
            self.assertEqual(case["feature_ids"], [parity])
            self.assertEqual(case["status"], "not_run")
            self.assertGreater(len(case["then"]), 0)


if __name__ == "__main__":
    unittest.main()
