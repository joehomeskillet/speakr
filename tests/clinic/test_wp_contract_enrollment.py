"""Contract checks for browser-only, own-user voice enrollment extensions."""

import ast
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SUPPLEMENT = ROOT / "clinic-evidence/contracts/enrollment-web.openapi.json"
IMPORT_API = SUPPLEMENT.with_name("import-web.openapi.json")
CLINIC_API = ROOT / "clinic-spec/contracts/clinic-api.openapi.json"
PARITY_API = ROOT / "clinic-spec/contracts/parity-api.openapi.json"
CATALOG = ROOT / "clinic-spec/contracts/parity-command-catalog.json"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_ref(document_path, ref):
    path, pointer = ref.split("#", 1)
    target_path = (document_path.parent / path).resolve() if path else document_path
    target = read_json(target_path)
    if pointer:
        assert pointer.startswith("/"), ref
        for part in pointer[1:].split("/"):
            target = target[part.replace("~1", "/").replace("~0", "~")]
    return target_path, target


class EnrollmentContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = read_json(SUPPLEMENT)
        cls.parity = read_json(PARITY_API)
        cls.catalog = read_json(CATALOG)
        cls.operations = {
            operation["operationId"]: operation
            for path in cls.contract["paths"].values()
            for method, operation in path.items()
            if method in {"get", "post", "put", "patch", "delete"}
        }

    def test_referenced_schemas_resolve_and_operation_ids_are_unique(self):
        visited = set()

        def walk(node, source):
            if isinstance(node, list):
                for child in node:
                    walk(child, source)
            elif isinstance(node, dict):
                if "$ref" in node:
                    identity = (source, node["$ref"])
                    if identity not in visited:
                        visited.add(identity)
                        path, target = resolve_ref(source, node["$ref"])
                        self.assertIsInstance(target, dict)
                        walk(target, path)
                for key, child in node.items():
                    if key != "$ref":
                        walk(child, source)

        walk(self.contract, SUPPLEMENT)
        self.assertGreaterEqual(len(visited), 10)

        ids = []
        documents = sorted((ROOT / "clinic-spec/contracts").glob("*.openapi.json"))
        documents += sorted((ROOT / "clinic-evidence/contracts").glob("*.openapi.json"))
        for path in documents:
            for methods in read_json(path).get("paths", {}).values():
                for method, operation in methods.items():
                    if method in {"get", "post", "put", "patch", "delete"}:
                        ids.append(operation["operationId"])
        self.assertEqual(len(ids), len(set(ids)))

    def test_reuses_existing_parity_operation_and_schema(self):
        (mapping,) = self.contract["x-existing-contract-mapping"]
        self.assertEqual((mapping["method"], mapping["path"], mapping["operationId"]),
                         ("POST", "/api/clinic/v1/parity/commands", "executeParityCommand"))
        operation = self.parity["paths"]["/parity/commands"]["post"]
        self.assertEqual(operation["operationId"], mapping["operationId"])
        self.assertEqual(mapping["parityCommandId"], "save_profile")
        command = next(item for item in self.catalog["commands"]
                       if item["command"] == mapping["parityCommandId"])
        self.assertEqual(command["request_schema"], "SaveProfile")
        path, schema = resolve_ref(SUPPLEMENT, mapping["requestSchema"])
        self.assertEqual(path, PARITY_API)
        self.assertEqual(schema["properties"]["command"]["const"], "save_profile")
        self.assertIn("ASR profile", mapping["scope"])

    def test_new_method_path_handlers_map_to_existing_public_functions(self):
        expected = {
            ("GET", "/api/clinic/v1/voice/enrollment"): ("consent_state", "consent_history", "embedding_count"),
            ("POST", "/api/clinic/v1/voice/enrollment"): ("embed_with_space", "enrol"),
        }
        actual = {}
        for path, methods in self.contract["paths"].items():
            for method, operation in methods.items():
                actual[(method.upper(), path)] = tuple(name.rsplit(".", 1)[-1]
                                                       for name in operation["x-handler"])
                for name in operation["x-handler"]:
                    module, func = name.rsplit(".", 1)
                    module_path = ROOT / (module.replace(".", "/") + ".py")
                    tree = ast.parse(module_path.read_text(encoding="utf-8"))
                    public_functions = {item.name for item in tree.body
                                        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))}
                    self.assertIn(func, public_functions)
        self.assertEqual(actual, expected)

    def test_positive_contract_has_bounded_typed_requests_and_responses(self):
        self.assertTrue(self.contract["x-new-speakr-extension-contract"])
        self.assertEqual(self.contract["x-manufacturer-interfaces"], "unresolved")
        self.assertEqual(set(self.operations), {
            "getOwnVoiceEnrollment", "enrollOwnVoiceSample",
        })
        import_paths = read_json(IMPORT_API)["paths"]
        for action, operation_id in (
            ("grant", "grantOwnVoiceConsent"),
            ("revoke", "revokeOwnVoiceConsent"),
        ):
            self.assertEqual(
                import_paths[f"/api/clinic/v1/voice-consent/{action}"]["post"]["operationId"],
                operation_id,
            )
        schema = self.contract["components"]["schemas"]["EnrollmentState"]
        self.assertEqual(set(schema["required"]), {"consent_revision", "state", "embedding_count"})
        self.assertTrue(schema["additionalProperties"] is False)
        self.assertNotIn("embedding", schema["properties"])
        for date_field in ("granted_at", "revoked_at"):
            self.assertEqual(schema["properties"][date_field]["type"], "string")
            self.assertTrue(schema["properties"][date_field]["nullable"])
        for operation in self.operations.values():
            self.assertEqual(operation["x-max-response-bytes"], 4096)
            success = "201" if operation["operationId"] == "enrollOwnVoiceSample" else "200"
            self.assertIn(success, operation["responses"])
            self.assertIn("ETag", operation["responses"][success]["headers"])
        enroll = self.operations["enrollOwnVoiceSample"]
        audio = enroll["requestBody"]["content"]["audio/wav"]["schema"]
        self.assertEqual(audio["maxLength"], enroll["x-max-body-bytes"])
        self.assertEqual(enroll["x-audio-constraints"], {
            "sample_rate_hz": 16000, "channels": 1, "sample_width_bits": 16,
            "min_duration_seconds": 3, "max_duration_seconds": 30,
        })
        self.assertEqual(self.contract["components"]["schemas"]["GrantConsent"]
                         ["properties"]["statement_text"]["maxLength"], 20000)
        self.assertEqual(self.contract["components"]["schemas"]["RevokeConsent"]
                         ["properties"]["confirm"]["enum"], [True])

    def test_unauthorized_and_cross_user_cases_are_rejected(self):
        self.assertEqual(self.contract["security"], [{"browserSession": []}])
        self.assertIn("self-only", self.contract["x-contract-rules"]["authorization"])
        for operation in self.operations.values():
            self.assertIn("401", operation["responses"])
            self.assertIn("403", operation["responses"])
            self.assertIn("own", operation["x-object-permission"])
            body = operation.get("requestBody", {}).get("content", {}).get("application/json", {})
            if body:
                _, schema = resolve_ref(SUPPLEMENT, body["schema"]["$ref"])
                self.assertNotIn("user_id", schema["properties"])
            if operation["operationId"] != "getOwnVoiceEnrollment":
                self.assertTrue(resolve_ref(SUPPLEMENT, operation["parameters"][0]["$ref"])[1]["required"])
                self.assertEqual(resolve_ref(SUPPLEMENT, operation["parameters"][0]["$ref"])[1]["name"],
                                 "X-CSRFToken")

    def test_stale_revision_and_retry_failure_cases_are_explicit(self):
        rules = self.contract["x-contract-rules"]
        self.assertIn("expected_consent_id", rules["revision"])
        self.assertIn("inside the consent write transaction", rules["revision"])
        self.assertIn("before checking If-Match", rules["idempotency"])
        self.assertIn("reconcile", rules["failure"])
        for operation in self.operations.values():
            if operation["operationId"] == "getOwnVoiceEnrollment":
                continue
            for status in ("401", "403", "409", "412", "428", "503"):
                self.assertIn(status, operation["responses"])
            header_refs = [resolve_ref(SUPPLEMENT, item["$ref"])[1]["name"]
                           for item in operation["parameters"]]
            self.assertEqual(header_refs, ["X-CSRFToken", "If-Match", "Idempotency-Key"])
            self.assertTrue(operation["requestBody"]["required"])
        self.assertIn("Cache-Control: no-store", rules["failure"])


if __name__ == "__main__":
    unittest.main()
