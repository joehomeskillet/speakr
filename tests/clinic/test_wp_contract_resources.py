"""Synthetic contract checks for additive clinic resource reads (WP w275)."""

import ast
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SUPPLEMENT = ROOT / "clinic-evidence/contracts/resources-web.openapi.json"
PARITY = ROOT / "clinic-spec/contracts/parity-api.openapi.json"
CLINIC = ROOT / "clinic-spec/contracts/clinic-api.openapi.json"
AGENT = ROOT / "clinic-evidence/contracts/agent-channel.openapi.json"
CATALOG = ROOT / "clinic-spec/contracts/parity-command-catalog.json"


def load_contract(path):
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise AssertionError(f"Duplicate JSON key {key} in {path}")
            result[key] = value
        return result

    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_keys)


def operations(contract):
    return [value for methods in contract["paths"].values()
            for method, value in methods.items() if method.lower() in {"get", "post", "put", "patch", "delete"}]


class ResourceWebContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = load_contract(SUPPLEMENT)
        cls.parity = load_contract(PARITY)
        cls.catalog = {item["command"]: item for item in load_contract(CATALOG)["commands"]}

    def test_references_resolve_and_ids_are_globally_unique(self):
        sources = [self.doc, self.parity, load_contract(CLINIC), load_contract(AGENT)]
        ids = [item["operationId"] for source in sources for item in operations(source)]
        self.assertEqual(len(ids), len(set(ids)))

        def walk(value, source):
            if isinstance(value, list):
                for item in value:
                    walk(item, source)
            elif isinstance(value, dict):
                if "$ref" in value:
                    reference = value["$ref"]
                    self.assertTrue(reference.startswith("#/"), reference)
                    target = source
                    for part in reference[2:].split("/"):
                        target = target[part.replace("~1", "/").replace("~0", "~")]
                    self.assertIsNotNone(target)
                for nested in value.values():
                    walk(nested, source)

        for source in sources:
            walk(source, source)

    def test_existing_writes_are_reused_with_exact_mapping(self):
        expected = {
            "vocabulary-api": ("propose_vocabulary_change", "ProposeVocabularyChange"),
            "vocabulary-review-api": ("review_resource_change", "ReviewResourceChange"),
            "commands-api": ("save_command", "SaveCommand"),
            "asr-profiles-api": ("save_profile", "SaveProfile"),
        }
        reused = self.doc["x-reused-operations"]
        self.assertEqual(set(expected), {item["resource"] for item in reused})
        for entry in reused:
            command, schema = expected[entry["resource"]]
            self.assertEqual((entry["method"], entry["path"], entry["operationId"]),
                             ("POST", "/parity/commands", "executeParityCommand"))
            self.assertEqual((entry["command"], entry["schema"]), (command, schema))
            self.assertEqual(entry["permission"], self.catalog[command]["permission"])
            self.assertEqual(schema, self.catalog[command]["request_schema"])
            self.assertIn(schema, self.parity["components"]["schemas"])
            self.assertIn("/parity/commands", self.parity["paths"])
        self.assertEqual(self.parity["paths"]["/parity/commands"]["post"]["operationId"],
                         "executeParityCommand")
        self.assertFalse(any("post" in path for path in self.doc["paths"].values()))

    def test_positive_reads_are_typed_and_bounded(self):
        expected = {
            "/resources/vocabulary": ("VocabularyPage", "src.clinic.vocabulary.search_vocabulary"),
            "/resources/vocabulary/reviews": ("ReviewPage", "src.clinic.vocabulary.list_review_items"),
            "/resources/commands": ("CommandPage", "src.clinic.vocabulary.get_effective_voice_commands"),
            "/resources/asr-profiles/me": ("AsrProfileView", "src.clinic.asr_profiles.asr_status"),
        }
        self.assertEqual(set(expected), set(self.doc["paths"]))
        for path, (schema, handler) in expected.items():
            read = self.doc["paths"][path]["get"]
            self.assertEqual(read["x-handler"], handler)
            self.assertEqual(read["responses"]["200"]["content"]["application/json"]["schema"]["$ref"],
                             "#/components/schemas/" + schema)
            self.assertTrue(read["x-object-permission"])
        components = self.doc["components"]
        for schema in ("VocabularyPage", "ReviewPage", "CommandPage"):
            self.assertEqual(components["schemas"][schema]["allOf"][1]["properties"]["items"]["maxItems"], 100)
        self.assertEqual(components["parameters"]["Limit"]["schema"]["maximum"], 100)
        self.assertEqual(components["parameters"]["Cursor"]["schema"]["maxLength"], 512)
        self.assertEqual(components["parameters"]["ScopeId"]["schema"]["maxLength"], 256)
        asr = components["schemas"]["AsrProfileView"]["properties"]
        self.assertEqual(asr["training_proven"]["type"], "boolean")
        self.assertEqual(asr["options"]["properties"]["source"]["enum"], ["headset", "recorder"])
        self.assertEqual(asr["options"]["properties"]["separate_offline_profile"]["type"], "boolean")

    def test_public_service_handlers_exist_without_import_side_effects(self):
        for value in self.doc["x-reused-operations"]:
            for handler in value["service_handlers"]:
                module, function = handler.rsplit(".", 1)
                path = ROOT / (module.replace(".", "/") + ".py")
                names = {node.name for node in ast.parse(path.read_text(encoding="utf-8")).body
                         if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
                self.assertIn(function, names)
        for operation in operations(self.doc):
            module, name = operation["x-handler"].rsplit(".", 1)
            path = ROOT / (module.replace(".", "/") + ".py")
            names = {node.name for node in ast.parse(path.read_text(encoding="utf-8")).body
                     if isinstance(node, ast.FunctionDef)}
            self.assertIn(name, names)

    def test_unauthorized_and_cross_scope_reads_are_denied(self):
        self.assertEqual(self.doc["security"], [{"ClinicSession": []}])
        self.assertEqual(self.doc["components"]["securitySchemes"]["ClinicSession"]["in"], "cookie")
        for path in self.doc["paths"].values():
            operation = path["get"]
            self.assertIs(operation["x-csrf-required"], False)
            self.assertIn("401", operation["responses"])
            self.assertIn("403", operation["responses"])
            self.assertNotIn("requestBody", operation)
        self.assertIn("current actor", self.doc["paths"]["/resources/vocabulary"]["get"]["x-object-permission"])
        self.assertIn("current ClinicSession", self.doc["paths"]["/resources/asr-profiles/me"]["get"]["x-object-permission"])

    def test_stale_version_and_replay_requirements(self):
        for path in self.doc["paths"].values():
            operation = path["get"]
            self.assertIn("409", operation["responses"])
            self.assertIn("304", operation["responses"])
            self.assertIn("ETag", operation["responses"]["200"]["headers"])
            self.assertIn({"$ref": "#/components/parameters/IfNoneMatch"}, operation["parameters"])
        write = self.doc["x-write-semantics"]
        self.assertIn("expected_revision", write["revision"])
        self.assertIn("409", write["revision"])
        self.assertIn("Idempotency-Key", write["idempotency"])
        params = self.parity["paths"]["/parity/commands"]["post"]["parameters"]
        self.assertEqual({p["name"] for p in params}, {"X-CSRF-Token", "Idempotency-Key"})
        self.assertEqual(next(p for p in params if p["name"] == "Idempotency-Key")["schema"]["minLength"], 8)
        self.assertEqual(self.doc["components"]["schemas"]["PageState"]["properties"]["revision"]["minimum"], 0)

    def test_source_configuration_command_covers_only_missing_delta(self):
        patch = self.doc["paths"]["/resources/asr-profiles/me"]["patch"]
        self.assertEqual(patch["operationId"], "configureOwnAsrSource")
        self.assertEqual(patch["x-handler"], "src.clinic.asr_profiles.set_profile")
        self.assertIs(patch["x-csrf-required"], True)
        self.assertIn("current ClinicSession", patch["x-object-permission"])
        self.assertIn("expected_revision", patch["x-revision"])
        self.assertIn("409", patch["x-idempotency"])
        names = [self.doc["components"]["parameters"][p["$ref"].split("/")[-1]]["name"]
                 for p in patch["parameters"]]
        self.assertEqual(names, ["X-CSRF-Token", "Idempotency-Key"])
        change = self.doc["components"]["schemas"]["SourceConfigurationChange"]
        self.assertEqual(change["required"], ["request_id", "expected_revision"])
        self.assertEqual(set(change["properties"]),
                         {"request_id", "expected_revision", "source", "separate_offline_profile"})
        self.assertEqual(change["properties"]["source"]["enum"], ["headset", "recorder"])
        self.assertEqual(change["properties"]["expected_revision"]["minimum"], 0)
        self.assertTrue({"200", "401", "403", "409", "422", "429", "503"} <= set(patch["responses"]))

    def test_retry_failures_are_explicit(self):
        for operation in operations(self.doc):
            self.assertTrue({"429", "503"}.issubset(operation["responses"]))
        for name in ("RateLimited", "Unavailable"):
            response = self.doc["components"]["responses"][name]
            self.assertIn("Retry-After", response["headers"])
            self.assertIn("application/problem+json", response["content"])
        self.assertIn("backoff", self.doc["x-read-semantics"]["retry"])
        self.assertIn("unresolved", self.doc["x-manufacturer-interfaces"])
        self.assertEqual(self.doc["x-original-behavior-tests"], ["BT-VOC-001", "BT-ASR-002"])


if __name__ == "__main__":
    unittest.main()
