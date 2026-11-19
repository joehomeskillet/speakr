"""Registration and classification of the identity/admin handlers (w196).

PAR-ADM-001, PAR-ADM-010; R-011, R-042.

The real application is started in a child process with its own throwaway
database, once with CLINIC_MODE on and once with it off, so the real URL map is
inspected and requests pass through the real clinic guard. No handler is
registered by the test itself. The in-process app of the suite is never touched.
Synthetic users only; no provider, directory or Entra is contacted, so the
real-provider gaps stay open.
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
    "clinic_identity_providers.discovery", "clinic_identity_providers.entra_login",
    "clinic_identity_providers.entra_callback", "clinic_identity_providers.iwa_login",
    "clinic_identity_providers.entra_link_start", "clinic_identity_providers.link_request",
    "clinic_identity_providers.unlink",
    "clinic_directory.capability", "clinic_directory.preview", "clinic_directory.apply",
    "clinic_pools.list_pools", "clinic_pools.put_pool",
    "clinic_grants.read", "clinic_grants.change",
    "clinic_preferences.recipient_default", "clinic_preferences.recipient_resolve",
    "clinic_preferences.last_selection", "clinic_preferences.workstation_issue",
    "clinic_preferences.workstation",
}
PREFIXES = ("clinic_identity_providers.", "clinic_directory.", "clinic_pools.", "clinic_grants.",
            "clinic_preferences.")

SCRIPT = r'''
import json, sys
from src.app import app, db
from src.init_db import initialize_database
from src.models import User
from src.clinic import identity, pools
from src.clinic.route_policy import ROUTE_POLICY

PREFIXES = json.loads(sys.argv[2])
out = {}
endpoints = {r.endpoint for r in app.url_map.iter_rules()}
out["identity_endpoints"] = sorted(e for e in endpoints if e.startswith(tuple(PREFIXES)))
if sys.argv[1] == "off":
    with app.app_context():
        initialize_database(app)
    out["off_status"] = app.test_client().get("/api/clinic/v1/pools").status_code
    print("RESULT:" + json.dumps(out)); sys.exit(0)

out["unclassified"] = sorted(e for e in app.url_map._rules_by_endpoint if e not in ROUTE_POLICY)
out["kinds"] = {e: ROUTE_POLICY[e].kind for e in endpoints if e.startswith(tuple(PREFIXES))}
with app.app_context():
    initialize_database(app)
    ids = {}
    for name, admin in (("admin", True), ("a", False), ("b", False)):
        u = User(username=f"w196_{name}", email=f"w196_{name}@local.test", password="x", is_admin=admin)
        db.session.add(u); db.session.flush(); ids[name] = u.id
    db.session.add(identity.ClinicAccountPolicy(user_id=ids["admin"], status="active", is_identity_admin=True))
    db.session.commit()
    pool = pools.create_author_pool("W196 Pool", actor_id=ids["admin"])
    pool_id = pool.id

def login(uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(uid); s["_fresh"] = True
        s["clinic_provider"] = "local"; s["clinic_security_version"] = 0
    return c, {"X-CSRF-Token": c.get("/api/csrf-token").json["csrf_token"]}

anon = app.test_client()
P, G, PREF = "/api/clinic/v1/pools", "/api/clinic/v1/identity/users", "/api/clinic/v1/preferences"
IDP, DIR = "/api/clinic/v1/identity/providers", "/api/clinic/v1/directory"
for name, path in (("pools", P), ("grants", f"{G}/{ids['a']}/grants"), ("prefs", PREF + "/recipient-default"),
                   ("directory", DIR + "/capability")):
    out["anon_" + name] = anon.get(path).status_code
out["anon_discovery"] = [anon.get(IDP).status_code, anon.get(IDP).json]
anon_h = {"X-CSRF-Token": anon.get("/api/csrf-token").json["csrf_token"]}
out["anon_entra_login"] = [anon.post(IDP + "/entra/login", headers=anon_h, json={}).status_code,
                           anon.post(IDP + "/entra/login", json={}).status_code]
out["anon_iwa_login"] = anon.post(IDP + "/iwa/login", headers=anon_h, json={}).status_code

ca, ha = login(ids["a"]); cb, hb = login(ids["b"]); cs, hs = login(ids["admin"])
A_GRANTS, B_GRANTS = f"{G}/{ids['a']}/grants", f"{G}/{ids['b']}/grants"
out["grants_read"] = [ca.get(A_GRANTS).status_code, ca.get(B_GRANTS).status_code, cs.get(B_GRANTS).status_code]
change = {"expected_revision": 0, "effect": "allow", "scope": "own", "actions": ["view"]}
out["grants_change_non_admin"] = [ca.post(A_GRANTS, headers=ha, json=change).status_code,
                                  ca.post(B_GRANTS, headers=ha, json=change).status_code]
out["grants_change_no_csrf"] = cs.post(B_GRANTS, json=change).status_code
out["grants_change_admin"] = cs.post(B_GRANTS, headers=hs, json=change).status_code
out["grants_change_stale"] = cs.post(B_GRANTS, headers=hs, json=change).status_code
out["grants_revision_after"] = cs.get(B_GRANTS).json["revision"]

state = cs.get(P).json
out["pools_list_admin"] = [len(state["author_pools"]), state["revision"]]
out["pools_list_plain"] = [ca.get(P).status_code, len(ca.get(P).json["author_pools"])]
body = {"name": "W196 Pool", "member_user_ids": [ids["a"]], "expected_revision": state["revision"]}
PUT = f"{P}/author/{pool_id}"
out["pool_put_non_admin"] = ca.put(PUT, headers=ha, json=body).status_code
out["pool_put_no_csrf"] = cs.put(PUT, json=body).status_code
out["pool_put_admin"] = cs.put(PUT, headers=hs, json=body).status_code
out["pool_put_stale"] = cs.put(PUT, headers=hs, json=body).status_code
out["pool_member_visible"] = [len(ca.get(P).json["author_pools"]), len(cb.get(P).json["author_pools"])]

out["directory_non_admin"] = [ca.get(DIR + "/capability").status_code,
                              ca.post(DIR + "/preview", headers=ha, json={}).status_code]
out["directory_admin_capability"] = cs.get(DIR + "/capability").json["directory_available"]
out["directory_admin_no_csrf"] = cs.post(DIR + "/preview", json={}).status_code
out["directory_admin_preview"] = cs.post(DIR + "/preview", headers=hs, json={}).status_code
out["directory_admin_apply"] = cs.post(DIR + "/apply", headers=hs, json={"version": "v", "hash": "h"}).status_code

out["idp_link_start"] = [ca.post(IDP + "/entra/link/start", headers=ha, json={}).status_code,
                         ca.post(IDP + "/entra/link/start", json={}).status_code]
out["idp_unlink_no_csrf"] = ca.post(f"{G}/{ids['a']}/entra-links/1/unlink", json={"expected_version": 1}).status_code
out["idp_link_request"] = ca.post(f"{G}/{ids['a']}/entra-link-requests", headers=ha, json={}).status_code

R = PREF + "/recipient-default"
out["pref_default"] = [ca.get(R).status_code, ca.get(R).json["version"]]
out["pref_default_no_csrf"] = ca.put(R, json={"mode": "none"}).status_code
issued = ca.post(PREF + "/workstations", headers=ha, json={})
out["pref_workstation_issue"] = issued.status_code
wid = (issued.json or {}).get("workstation_id")
out["pref_workstation_read"] = [ca.get(f"{PREF}/workstations/{wid}").status_code,
                                cb.get(f"{PREF}/workstations/{wid}").status_code]
sel = {"selection": {"role": "author", "author_id": ids["a"], "pool_id": pool_id}}
out["pref_selection"] = [ca.get(PREF + "/last-selection").status_code,
                         ca.put(PREF + "/last-selection", headers=ha, json=sel).status_code,
                         ca.put(PREF + "/last-selection", json=sel).status_code]
print("RESULT:" + json.dumps(out))
'''


def _run(mode: str, tmp_path: Path) -> dict:
    env = {**os.environ, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'w196.db'}",
           "UPLOAD_FOLDER": str(tmp_path / "uploads"), "RATELIMIT_ENABLED": "false",
           "DISABLE_VOICE_EMBEDDING_CHECK": "true", "ENABLE_AUTO_PROCESSING": "false"}
    env.pop("CLINIC_MODE", None)
    if mode == "on":
        env["CLINIC_MODE"] = "1"
    done = subprocess.run([sys.executable, "-c", SCRIPT, mode, json.dumps(PREFIXES)], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=300)
    lines = [line for line in done.stdout.splitlines() if line.startswith("RESULT:")]
    assert lines, done.stderr[-3000:]
    return json.loads(lines[-1][len("RESULT:"):])


@pytest.fixture(scope="module")
def on(tmp_path_factory):
    return _run("on", tmp_path_factory.mktemp("w196_on"))


def test_clinic_mode_off_registers_nothing(tmp_path):
    result = _run("off", tmp_path)
    assert result["identity_endpoints"] == []
    assert result["off_status"] == 404


def test_real_url_map_lists_every_handler_with_its_scope(on):
    assert EXPECTED <= set(on["identity_endpoints"])
    kinds = on["kinds"]
    public = {"clinic_identity_providers." + n for n in ("discovery", "entra_login", "entra_callback", "iwa_login")}
    admin = {"clinic_directory.capability", "clinic_directory.preview", "clinic_directory.apply",
             "clinic_grants.change"}
    assert {e for e in EXPECTED if kinds[e] == "public"} == public
    assert {e for e in EXPECTED if kinds[e] == "admin"} == admin
    assert {kinds[e] for e in EXPECTED - public - admin} == {"auth"}
    assert on["unclassified"] == []


def test_anonymous_is_refused_before_any_handler(on):
    for name in ("pools", "grants", "prefs", "directory"):
        assert on["anon_" + name] in (302, 401), name


def test_disabled_providers_stay_inert_and_csrf_is_kept(on):
    assert on["anon_discovery"][0] == 200
    assert on["anon_discovery"][1]["providers"] == [
        {"provider": "entra", "enabled": False}, {"provider": "iwa", "enabled": False},
        {"provider": "ldap", "enabled": False}]
    assert on["anon_entra_login"][0] == 422  # inert answer, no authorization URL
    assert on["anon_entra_login"][1] == 403  # browser CSRF is kept on the public sign-in POST
    assert on["anon_iwa_login"] == 422
    assert on["idp_link_start"] == [422, 403]  # inert with CSRF; without the token the global CSRF refuses first
    assert on["idp_unlink_no_csrf"] in (400, 403)
    assert on["idp_link_request"] == 422


def test_grants_read_is_own_or_admin_and_change_is_site_admin_only(on):
    assert on["grants_read"] == [200, 403, 200]  # own, foreign denied, admin allowed
    assert on["grants_change_non_admin"] == [403, 403]
    assert on["grants_change_no_csrf"] in (400, 403)
    assert (on["grants_change_admin"], on["grants_change_stale"]) == (200, 409)
    assert on["grants_revision_after"] == 1


def test_pool_writes_need_admin_csrf_and_current_revision(on):
    assert on["pools_list_admin"][0] == 1
    assert on["pools_list_plain"] == [200, 0]  # a pool the user is not in stays hidden
    assert on["pool_put_non_admin"] == 403
    assert on["pool_put_no_csrf"] in (400, 403)
    assert (on["pool_put_admin"], on["pool_put_stale"]) == (200, 409)
    assert on["pool_member_visible"] == [1, 0]


def test_directory_is_admin_only_and_unavailable_without_provider(on):
    assert on["directory_non_admin"] == [403, 403]
    assert on["directory_admin_capability"] is False
    assert on["directory_admin_no_csrf"] == 403
    assert (on["directory_admin_preview"], on["directory_admin_apply"]) == (503, 503)


def test_preferences_use_the_session_actor(on):
    assert on["pref_default"] == [200, 0]
    assert on["pref_default_no_csrf"] == 403
    assert on["pref_workstation_issue"] == 201
    assert on["pref_workstation_read"][0] == 200
    assert on["pref_workstation_read"][1] != 200  # another user cannot read it
    assert on["pref_selection"] == [200, 200, 403]
