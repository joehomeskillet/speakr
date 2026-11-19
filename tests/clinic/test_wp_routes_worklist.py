"""Registration and classification of the worklist handlers (w197).

PAR-WF-001, PAR-CFG-002; R-011, R-042.

The real application is started in a child process with its own throwaway
database, once with CLINIC_MODE on and once with it off, so the real URL map is
inspected and requests pass through the real clinic guard. No handler is
registered by the test itself. The in-process app of the suite is never touched.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

EXPECTED = {
    "clinic_worklist.rows", "clinic_worklist.own_filters", "clinic_worklist.own_filter",
    "clinic_worklist.create_filter", "clinic_worklist.update_filter", "clinic_worklist.delete_filter",
    "clinic_worklist.own_layout", "clinic_columns.own_layout",
    "clinic_workflow.metadata", "clinic_workflow.release", "clinic_workflow.archive",
    "clinic_history.get_history", "clinic_history.review_lease",
    "clinic_templates.list_templates", "clinic_templates.get_template",
    "clinic_templates.get_template_content", "clinic_templates.save_template",
    "clinic_templates.assign_template_route", "clinic_templates.delete_template",
    "clinic_categories.list_categories", "clinic_categories.check_categories",
    "clinic_regex.preview", "clinic_regex.apply",
}

SCRIPT = r'''
import json, sys, uuid
from src.app import app, db
from src.init_db import initialize_database
from src.models import Recording, User
from src.clinic import policy, workflow
from src.clinic.models import (ClinicAudioRevision, ClinicContextRevision, ClinicDictation, ClinicOriginal,
                               ClinicTranscriptRevision)
from src.clinic.route_policy import ROUTE_POLICY

out = {}
endpoints = {r.endpoint for r in app.url_map.iter_rules()}
out["clinic_endpoints"] = sorted(e for e in endpoints if e.startswith("clinic_"))
if sys.argv[1] == "off":
    with app.app_context():
        initialize_database(app)
    out["off_status"] = app.test_client().get("/api/clinic/v1/worklist/rows").status_code
    print("RESULT:" + json.dumps(out)); sys.exit(0)

out["unclassified"] = sorted(e for e in app.url_map._rules_by_endpoint if e not in ROUTE_POLICY)
out["kinds"] = {e: ROUTE_POLICY[e].kind for e in endpoints if e.startswith("clinic_")}
with app.app_context():
    initialize_database(app)
    ids = {}
    for name in "ab":
        u = User(username=f"w197_{name}", email=f"w197_{name}@local.test", password="x", is_admin=False)
        db.session.add(u); db.session.flush(); ids[name] = u.id
    rec = Recording(user_id=ids["a"], title="Alpha", status="COMPLETED", original_filename="a.wav")
    db.session.add(rec); db.session.flush()
    dic = ClinicDictation(recording_id=rec.id, author_user_id=ids["a"], version_counter=0)
    db.session.add(dic); db.session.flush()
    db.session.add(workflow.ClinicWorkflowDictation(
        id=dic.id, author_user_id=ids["a"], pool_id=str(uuid.uuid4()), status=workflow.TO_WRITE, audio_seconds=30))
    orig = ClinicOriginal(dictation_id=dic.id, content_hash="h", size_bytes=1, storage_key="k", receipt="r", origin_ref="o")
    db.session.add(orig); db.session.flush()
    audio = ClinicAudioRevision(dictation_id=dic.id, content_hash="h")
    text = ClinicTranscriptRevision(dictation_id=dic.id, kind="HUMAN", body="Text", normalised_text_hash="t" * 64,
                                    original_version_id=orig.id)
    context = ClinicContextRevision(dictation_id=dic.id)
    db.session.add_all([audio, text, context]); db.session.commit()
    did, revs = dic.id, (audio.id, text.id, context.id)

def login(uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(uid); s["_fresh"] = True
    return c, {"X-CSRF-Token": c.get("/api/csrf-token").json["csrf_token"]}

# Explicit release authority for author a only (own default never includes release).
policy.register_grant_provider(lambda uid: [policy.Grant("own", frozenset({"release"}))] if uid == ids["a"] else [])
W = "/api/clinic/v1/worklist"
D = f"/api/clinic/v1/dictations/{did}"
anon = app.test_client()
for name, path in (("rows", W + "/rows"), ("templates", "/api/clinic/v1/templates"),
                   ("history", D + "/history"), ("categories", "/api/clinic/v1/categories"),
                   ("layout", W + "/layout")):
    out["anon_" + name] = anon.get(path).status_code

ca, ha = login(ids["a"]); cb, hb = login(ids["b"])
ra = ca.get(W + "/rows"); rb = cb.get(W + "/rows")
out["rows"] = [ra.status_code, ra.json["total"], rb.status_code, rb.json["total"]]
out["history"] = [ca.get(D + "/history").status_code, cb.get(D + "/history").status_code]

spec = {"all": [{"field": "status", "op": "in", "value": ["zu_schreiben"]}]}
out["filter_no_csrf"] = ca.post(W + "/filters", json={"name": "F", "spec": spec}).status_code
made = ca.post(W + "/filters", headers=ha, json={"name": "F", "spec": spec})
out["filter_create"] = made.status_code
fid = made.json["id"]
out["filter_foreign"] = cb.get(f"{W}/filters/{fid}").status_code
out["filter_stale"] = ca.put(f"{W}/filters/{fid}", headers={**ha, "If-Match": '"9"'}, json={"name": "G"}).status_code
out["filter_rename"] = ca.put(f"{W}/filters/{fid}", headers={**ha, "If-Match": '"1"'}, json={"name": "G"}).status_code
out["rows_filtered"] = ca.get(f"{W}/rows?filter_id={fid}").json["total"]

lay = ca.get(W + "/layout").json["layout"]
put = lambda c, h, tag, key: c.put(W + "/layout", headers={**h, "If-Match": tag, "Idempotency-Key": key}, json=lay)
out["layout_no_csrf"] = ca.put(W + "/layout", json=lay).status_code
out["layout_put"] = put(ca, ha, '"0"', str(uuid.uuid4())).status_code
out["layout_stale"] = put(ca, ha, '"0"', str(uuid.uuid4())).status_code

meta = lambda c, h, tag, extra={}: c.post(D + "/metadata", headers={**h, "If-Match": tag, "Idempotency-Key": str(uuid.uuid4()), **extra},
                                          json={"fields": {"title": "Neu"}})
out["meta_no_csrf"] = ca.post(D + "/metadata", json={"fields": {"title": "x"}}).status_code
out["meta_foreign"] = meta(cb, hb, f'"{did}:0"').status_code
out["meta_no_lease"] = meta(ca, ha, f'"{did}:0"').status_code
out["meta_no_match"] = ca.post(D + "/metadata", headers={**ha, "Idempotency-Key": str(uuid.uuid4())},
                               json={"fields": {"title": "x"}}).status_code
lease = ca.post(D + "/leases", headers={**ha, "Idempotency-Key": str(uuid.uuid4())})
out["lease"] = lease.status_code
out["lease_foreign"] = cb.post(D + "/leases", headers={**hb, "Idempotency-Key": str(uuid.uuid4())}).status_code
out["lease_body"] = lease.get_data(as_text=True)[:300]
gen = (lease.json or {}).get("generation")
out["meta_ok"] = meta(ca, ha, f'"{did}:0"').status_code
out["meta_stale"] = meta(ca, ha, f'"{did}:0"').status_code
release = ca.post(D + "/releases", headers={**ha, "If-Match": f'"{did}:1"', "Idempotency-Key": str(uuid.uuid4())}, json={
    "lease_id": (lease.json or {}).get("lease_id"), "lease_generation": gen, "original_version_id": revs[0],
    "transcript_revision_id": revs[1], "context_revision_id": revs[2],
    "document_type": "report", "mode": "PLATFORM_REVIEWED_KIS_DRAFT", "text_sha256": "t" * 64,
    "confirmed_content": True, "confirmed_context": True})
out["release"] = release.status_code
out["release_body"] = release.get_data(as_text=True)[:300]

T = "/api/clinic/v1/templates"
out["templates"] = [ca.get(T).status_code, ca.get(T).json["items"]]
out["templates_no_csrf"] = ca.post(T, json={}).status_code
out["categories"] = ca.get("/api/clinic/v1/categories").status_code
out["categories_check"] = ca.post("/api/clinic/v1/categories/check", headers=ha, json={"selections": []}).status_code

R = "/api/clinic/v1/regex/preview"
body = {"dictation_id": did, "text": "RR 120", "rules": [{"pattern": "RR", "target_field": "title", "replacement": "Blutdruck"}]}
out["regex_own"] = ca.post(R, headers=ha, json=body).status_code
out["regex_foreign"] = cb.post(R, headers=hb, json=body).status_code
out["regex_no_csrf"] = ca.post(R, json=body).status_code
print("RESULT:" + json.dumps(out))
'''


def _run(mode: str, tmp_path: Path) -> dict:
    env = {**os.environ, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'w197.db'}",
           "UPLOAD_FOLDER": str(tmp_path / "uploads"), "RATELIMIT_ENABLED": "false",
           "DISABLE_VOICE_EMBEDDING_CHECK": "true", "ENABLE_AUTO_PROCESSING": "false"}
    env.pop("CLINIC_MODE", None)
    if mode == "on":
        env["CLINIC_MODE"] = "1"
    done = subprocess.run([sys.executable, "-c", SCRIPT, mode], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=300)
    lines = [line for line in done.stdout.splitlines() if line.startswith("RESULT:")]
    assert lines, done.stderr[-3000:]
    return json.loads(lines[-1][len("RESULT:"):])


@pytest.fixture(scope="module")
def on(tmp_path_factory):
    return _run("on", tmp_path_factory.mktemp("w197_on"))


def test_clinic_mode_off_registers_nothing(tmp_path):
    result = _run("off", tmp_path)
    assert result["clinic_endpoints"] == []
    assert result["off_status"] == 404


def test_real_url_map_lists_every_handler_classified_as_auth(on):
    assert EXPECTED <= set(on["clinic_endpoints"])
    assert {on["kinds"][e] for e in EXPECTED} == {"auth"}
    assert on["unclassified"] == []


def test_anonymous_is_refused_before_any_handler(on):
    for name in ("rows", "templates", "history", "categories", "layout"):
        assert on["anon_" + name] in (302, 401), name


def test_worklist_and_history_are_scoped_to_the_actor(on):
    assert on["rows"] == [200, 1, 200, 0]
    assert on["history"] == [200, 404]


def test_saved_filters_csrf_stale_version_and_cross_user(on):
    assert on["filter_no_csrf"] in (400, 403)
    assert on["filter_create"] == 201
    assert on["filter_foreign"] == 404
    assert (on["filter_stale"], on["filter_rename"]) == (409, 200)
    assert on["rows_filtered"] == 1


def test_layout_write_needs_csrf_and_current_version(on):
    assert on["layout_no_csrf"] == 403
    assert (on["layout_put"], on["layout_stale"]) == (200, 412)


def test_workflow_metadata_guards_and_stale_retry(on):
    assert on["meta_no_csrf"] in (400, 403)
    assert on["meta_foreign"] == 404
    assert on["meta_no_match"] == 428
    assert on["meta_no_lease"] == 409
    assert (on["lease"], on["lease_foreign"]) == (200, 404), on["lease_body"]
    assert (on["meta_ok"], on["meta_stale"]) == (200, 409)


def test_release_stays_fail_closed_without_trusted_context(on):
    assert on["release"] == 422
    assert "UNTRUSTED_CONTEXT" in on["release_body"]


def test_templates_categories_and_regex_use_the_session_actor(on):
    assert on["templates"] == [200, []]
    assert on["templates_no_csrf"] == 403
    assert (on["categories"], on["categories_check"]) == (200, 200)
    assert (on["regex_own"], on["regex_foreign"], on["regex_no_csrf"]) == (200, 403, 403)
