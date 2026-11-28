"""Synthetic P-003 contract evidence; rich HTTP support remains an integration gate.

Use the host's existing jsonschema CLI interpreter because the shared app venv
does not contain jsonschema. No packages are installed or environments changed.
"""

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from src.clinic.richtext import CanonicalDocument


ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "clinic-spec/contracts/parity-api.openapi.json"
REVISION = "00000000-0000-4000-8000-000000000001"
DOCUMENT = {"schema_version": 1, "nodes": [
    {"id": "p1", "kind": "paragraph", "runs": [
        {"id": "s1", "text": "Ä🙂", "bold": True, "audio_anchor_id": None},
    ]},
]}
PLAIN = {"text": "Ä🙂", "audio_position_ms": 0, "lease_generation": 1,
         "expected_text_revision_id": REVISION}
RICH = {**PLAIN, "document": DOCUMENT, "expected_document_version": 0, "move_map": [
    {"segment_id": "s1", "old_start": 3, "old_end": 5, "new_start": 0, "new_end": 2},
]}
MARK = {"type": "low_confidence", "text_revision_id": REVISION,
        "audio_revision_id": REVISION, "text_start": 0, "text_end": 2, "confidence": None}


def test_plain_and_rich_payloads_against_openapi_schema():
    cases = [("CorrectionSave", PLAIN, True), ("CorrectionSave", RICH, True),
             ("CorrectionSave", {**PLAIN, "expected_text_revision_id": None,
                                 "expected_state_version": None}, True)]
    snapshot = {"dictation_id": REVISION, "text_revision_id": REVISION,
                "text": "Ä🙂", "state": None, "links": [], "markers": []}
    cases += [("CorrectionSnapshot", snapshot, True),
              ("CorrectionSnapshot", {**snapshot, "document": DOCUMENT, "document_version": 1,
                                      "recognition_marks": [MARK]}, True),
              ("CorrectionSnapshot", {**snapshot, "document": DOCUMENT}, False)]
    for field, value in [("expected_document_version", -1), ("expected_document_version", True),
                         ("document", None), ("document", {}), ("move_map", {}),
                         ("recognition_marks", [MARK]), ("lease_generation", 0)]:
        cases.append(("CorrectionSave", {**RICH, field: value}, False))
    for field in ("document", "expected_document_version"):
        cases.append(("CorrectionSave", {k: v for k, v in RICH.items() if k != field}, False))
    for field, value in [("bold", 1), ("id", "bad id"), ("href", "javascript:alert(1)")]:
        document = copy.deepcopy(DOCUMENT)
        document["nodes"][0]["runs"][0][field] = value
        cases.append(("CorrectionSave", {**RICH, "document": document}, False))
    for kind in ("low_confidence", "unrecognized"):
        for confidence in (None, 0, 0.5, 1):
            cases.append(("CorrectionRecognitionMark", {**MARK, "type": kind, "confidence": confidence}, True))
    for field, value in [("type", "section"), ("confidence", -0.1), ("confidence", 1.1),
                         ("confidence", True), ("text_start", -1), ("text_end", 0),
                         ("text_revision_id", "stale"), ("audio_revision_id", None)]:
        cases.append(("CorrectionRecognitionMark", {**MARK, field: value}, False))
    cases.append(("CorrectionMove", {**RICH["move_map"][0], "old_start": True}, False))
    # Real Draft 2020-12 validation, including references, format and dependentRequired.
    python = sys.executable if importlib.util.find_spec("jsonschema") else shutil.which("python3", path=os.defpath)
    assert python, "A Python interpreter with jsonschema is required"
    result = subprocess.run([python, "-c", """
import json, sys
from jsonschema import Draft202012Validator, FormatChecker
document, cases = json.load(sys.stdin)
for schema in document['components']['schemas'].values():
    Draft202012Validator.check_schema(schema)
for name, payload, expected in cases:
    schema = {'$ref': '#/components/schemas/' + name, 'components': document['components']}
    errors = list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(payload))
    assert (not errors) == expected, (name, payload, [error.message for error in errors])
print(f'{len(cases)} synthetic payloads validated with Draft 2020-12')
"""], input=json.dumps([json.loads(CONTRACT.read_text()), cases]), text=True,
        capture_output=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    print(result.stdout.strip())


def test_document_matches_existing_canonical_model():
    document = CanonicalDocument.from_payload(DOCUMENT)
    assert document.project() == ("Ä🙂", {"s1": (0, 2)})
    assert document.to_payload()["nodes"][0]["runs"][0]["audio_anchor_id"] is None


def test_existing_resource_wiring_and_stale_semantics():
    contract = json.loads(CONTRACT.read_text())
    path = "/dictations/{dictation_id}/correction"
    get = contract["paths"][path]["get"]
    save = contract["paths"][path + "/save"]["post"]
    assert get["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/CorrectionSnapshot"}
    assert save["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/CorrectionSave"}
    for guard in ("If-Match", "expected_text_revision_id", "expected_state_version",
                  "expected_document_version", "lease_generation", "No writes"):
        assert guard in save["responses"]["409"]["description"]
    assert {p["name"] for p in save["parameters"] if p["required"]} == {
        "If-Match", "X-CSRF-Token", "Idempotency-Key"}
    assert "428" in save["responses"]


def test_contract_manifest_entry_matches_bytes():
    manifest = json.loads((ROOT / "clinic-spec/evidence/build-manifest.json").read_text())
    entry, = [row for row in manifest["files"] if row["path"] == "contracts/parity-api.openapi.json"]
    content = CONTRACT.read_bytes()
    assert entry["bytes"] == len(content)
    assert entry["sha256"] == hashlib.sha256(content).hexdigest()
