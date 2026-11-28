"""Registration and classification of the resources handlers (w200).

PAR-VOC-001, PAR-ASR-002; R-011, R-042.

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
    "clinic_parity.execute",
    "clinic_vocabulary.search", "clinic_vocabulary.export", "clinic_vocabulary.import_text",
    "clinic_vocabulary.additions", "clinic_vocabulary.edit_entry",
    "clinic_vocabulary.suppress", "clinic_vocabulary.reactivate",
    "clinic_vocabulary_review.list_resource_vocabulary_reviews",
    "clinic_vocabulary_review.undo_resource_vocabulary_review",
    "clinic_commands.list_commands", "clinic_commands.list_pronunciations",
    "clinic_commands.save_pronunciation_hint_endpoint",
    "clinic_commands.get_pronunciation_capabilities", "clinic_commands.execute_command_endpoint",
}

SCRIPT = r'''
import json, sys, uuid
from src.app import app, db
from src.init_db import initialize_database
from src.models import Recording, User
from src.clinic import vocabulary
from src.clinic.route_policy import ROUTE_POLICY

out = {}
endpoints = {r.endpoint for r in app.url_map.iter_rules()}
out["clinic_endpoints"] = sorted(e for e in endpoints if e.startswith("clinic_"))
if sys.argv[1] == "off":
    with app.app_context():
        initialize_database(app)
    out["off_status"] = app.test_client().get("/api/clinic/v1/vocabulary/search?scope=user").status_code
    print("RESULT:" + json.dumps(out)); sys.exit(0)

app.add_url_rule("/zz-unlisted", "zz_unlisted", lambda: "x")  # fail-closed probe
out["unclassified"] = sorted(e for e in app.url_map._rules_by_endpoint if e not in ROUTE_POLICY and e != "zz_unlisted")
out["kinds"] = {e: ROUTE_POLICY[e].kind for e in endpoints if e.startswith("clinic_")}
with app.app_context():
    initialize_database(app)
    ids = {}
    for name in "ab":
        u = User(username=f"w200_{name}", email=f"w200_{name}@local.test", password="x", is_admin=False)
        db.session.add(u); db.session.flush(); ids[name] = u.id
    rec = Recording(user_id=ids["a"], title="A", status="COMPLETED", original_filename="a.wav")
    db.session.add(rec); db.session.commit()
    rec_a = rec.id
    item = vocabulary.submit_review_item(user_id=ids["a"], written_form="Hypertonie")
    db.session.commit(); item_id = item.id

def login(uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(uid); s["_fresh"] = True
    return c, {"X-CSRF-Token": c.get("/api/csrf-token").json["csrf_token"]}

anon = app.test_client()
out["anon_vocab"] = anon.get("/api/clinic/v1/vocabulary/search?scope=user").status_code
out["anon_parity"] = anon.post("/api/clinic/v1/parity/commands", json={}).status_code
out["unlisted"] = login(ids["a"])[0].get("/zz-unlisted").status_code

ca, ha = login(ids["a"]); cb, hb = login(ids["b"])
V = "/api/clinic/v1/vocabulary"
out["add_no_csrf"] = ca.post(V + "/additions", json={"scope": "user", "written": "Alpha"}).status_code
r = ca.post(V + "/additions", headers=ha, json={"scope": "user", "written": "Alpha", "spoken": "alpha"})
out["add"] = r.status_code; entry = r.json["entry"]
out["a_sees"] = len(ca.get(V + "/search?scope=user").json["entries"])
out["b_sees"] = len(cb.get(V + "/search?scope=user").json["entries"])
out["b_toggle_foreign"] = cb.post(f"{V}/entries/{entry['id']}/suppress", headers=hb, json={"expected_version": entry["version"]}).status_code
out["a_toggle_stale"] = ca.post(f"{V}/entries/{entry['id']}/suppress", headers=ha, json={"expected_version": "stale"}).status_code
out["a_toggle_ok"] = ca.post(f"{V}/entries/{entry['id']}/suppress", headers=ha, json={"expected_version": entry["version"]}).status_code

P = "/api/clinic/v1/parity/commands"
def review(c, h, rev, key):
    body = {"command": "review_resource_change", "request_id": str(uuid.uuid4()) if key is None else key[1],
            "expected_revision": rev, "payload": {"proposal_id": item_id, "decision": "user"}}
    return c.post(P, headers={**h, "Idempotency-Key": "w200-" + (key[0] if key else uuid.uuid4().hex)}, json=body)
out["review_foreign"] = review(cb, hb, 1, None).status_code
out["review_no_csrf"] = ca.post(P, headers={"Idempotency-Key": "w200-nocsrf-key"}, json={}).status_code
out["review_stale"] = review(ca, ha, 5, None).status_code
rid = str(uuid.uuid4()); first = review(ca, ha, 1, ("k1" + "0" * 8, rid))
replay = review(ca, ha, 1, ("k1" + "0" * 8, rid))
out["review_ok"] = [first.status_code, first.json.get("revision")]
out["review_replay"] = [replay.status_code, replay.json == first.json]
RV = "/api/clinic/v1/resources/vocabulary/reviews"
out["list_own"] = ca.get(f"{RV}?scope_type=user&scope_id={ids['a']}").status_code
out["list_foreign"] = cb.get(f"{RV}?scope_type=user&scope_id={ids['a']}").status_code

C = "/api/clinic/v1/resources"
out["commands_ok"] = ca.get(C + "/commands").status_code
out["pron_foreign"] = cb.get(f"/pronunciations/users/{ids['a']}/hints").status_code
out["execute_foreign_recording"] = cb.post("/api/clinic/v1/commands/execute", headers=hb,
    json={"phrase": "x", "recording_id": rec_a, "expected_version": "v"}).status_code
out["execute_own_recording"] = ca.post("/api/clinic/v1/commands/execute", headers=ha,
    json={"phrase": "x", "recording_id": rec_a, "expected_version": "v"}).status_code
print("RESULT:" + json.dumps(out))
'''


def _run(mode: str, tmp_path: Path) -> dict:
    env = {**os.environ, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'w200.db'}",
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
    return _run("on", tmp_path_factory.mktemp("w200_on"))


def test_clinic_mode_off_registers_nothing(tmp_path):
    result = _run("off", tmp_path)
    assert result["clinic_endpoints"] == []
    assert result["off_status"] == 404


def test_real_url_map_lists_every_handler_classified_as_auth(on):
    assert EXPECTED <= set(on["clinic_endpoints"])
    assert {on["kinds"][e] for e in EXPECTED} == {"auth"}
    assert on["unclassified"] == []


def test_unknown_route_fails_closed_and_anonymous_is_refused(on):
    assert on["unlisted"] == 403
    assert on["anon_vocab"] in (302, 401)
    assert on["anon_parity"] in (302, 401)


def test_vocabulary_positive_and_cross_user_denied(on):
    assert (on["add"], on["a_sees"], on["b_sees"]) == (201, 1, 0)
    assert on["add_no_csrf"] == 403
    assert on["b_toggle_foreign"] == 404
    assert (on["a_toggle_stale"], on["a_toggle_ok"]) == (409, 200)


def test_parity_review_guards_stale_and_retry(on):
    assert on["review_no_csrf"] == 403
    assert on["review_foreign"] == 403
    assert on["review_stale"] == 409
    assert on["review_ok"] == [200, 2]
    assert on["review_replay"] == [200, True]
    assert (on["list_own"], on["list_foreign"]) == (200, 403)


def test_commands_resources_and_guard_first_on_foreign_recording(on):
    assert on["commands_ok"] == 200
    assert on["pron_foreign"] == 403
    assert on["execute_foreign_recording"] == 404
    assert on["execute_own_recording"] != 404
