"""Trusted-context provider and metadata-field routes wired into the real app (x29).

The real application runs in a child process with a throwaway database, so requests pass the
real clinic guard and the real release route. Data is synthetic. Patient values are opaque
placeholders; trust binds the dictation and the case reference only.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

SCRIPT = r'''
import json, sys, uuid
from datetime import datetime, timedelta, timezone
from src.app import app, db
from src.init_db import initialize_database
from src.models import Recording, User
from src.clinic import context_grants, policy, workflow
from src.clinic.acceptance_policy import AcceptanceLedger
from src.clinic.context_grants import ClinicContextGrantUse, ContextGrantError, VerifiedContextGrant
from src.clinic.kis import KisAdapter
from src.clinic.models import (ClinicAudioRevision, ClinicContextRevision, ClinicDictation, ClinicOriginal,
                               ClinicTranscriptRevision)
from src.clinic.route_policy import ROUTE_POLICY

mode = sys.argv[1]
out = {}
FIELDS = "/api/clinic/v1/metadata-fields"
rules = {r.rule for r in app.url_map.iter_rules()}
out["fields_rule_mounted"] = FIELDS in rules
if mode == "off":
    out["clinic_endpoints"] = sorted(r.endpoint for r in app.url_map.iter_rules() if r.endpoint.startswith("clinic_"))
    print("RESULT:" + json.dumps(out)); sys.exit(0)

out["field_kinds"] = {e: ROUTE_POLICY[e].kind for e in ("clinic_metadata_fields.list_fields", "clinic_metadata_fields.save_field")}
out["unclassified"] = sorted(e for e in app.url_map._rules_by_endpoint if e not in ROUTE_POLICY)

NOW = datetime.now(timezone.utc)
with app.app_context():
    initialize_database(app)
    ids = {}
    for name in "ab":
        u = User(username=f"x29_{name}", email=f"x29_{name}@local.test", password="x", is_admin=False)
        db.session.add(u); db.session.flush(); ids[name] = u.id
    db.session.commit()

    def make(author, bound_nonce=None, grant_user=None, expires=None):
        rec = Recording(user_id=author, title="Alpha", status="COMPLETED", original_filename="a.wav")
        db.session.add(rec); db.session.flush()
        dic = ClinicDictation(recording_id=rec.id, author_user_id=author, version_counter=0)
        db.session.add(dic); db.session.flush()
        db.session.add(workflow.ClinicWorkflowDictation(
            id=dic.id, author_user_id=author, pool_id=str(uuid.uuid4()), status=workflow.TO_WRITE, audio_seconds=30))
        orig = ClinicOriginal(dictation_id=dic.id, content_hash="h", size_bytes=1, storage_key="k", receipt="r", origin_ref="o")
        db.session.add(orig); db.session.flush()
        audio = ClinicAudioRevision(dictation_id=dic.id, content_hash="h")
        text = ClinicTranscriptRevision(dictation_id=dic.id, kind="HUMAN", body="Text", normalised_text_hash="t" * 64,
                                        original_version_id=orig.id)
        db.session.add_all([audio, text]); db.session.flush()
        if bound_nonce is None:
            context = ClinicContextRevision(dictation_id=dic.id)
            db.session.add(context); db.session.commit()
            return dic.id, (audio.id, text.id, context.id)
        db.session.commit()
        return dic.id, (audio.id, text.id, None)

    def bind(did, actor, nonce, user=None, ttl=30):
        evidence = VerifiedContextGrant(
            issuer="kis", audience="speakr", user_id=actor if user is None else user, dictation_id=did,
            source_system="mock", subject_reference="subject", tenant_reference="tenant",
            case_reference="CASE-0001", patient_reference="opaque-ref", purpose="bind_existing",
            nonce=nonce, expires_at=NOW + timedelta(seconds=ttl))
        try:
            row = context_grants.bind_context_grant(
                "grant", dictation_id=did, actor_user_id=actor, tenant="tenant", expected_version=0,
                issuer="kis", audience="speakr", now=NOW, resolve=lambda _g, _u: evidence,
                adapter=app.extensions["clinic_kis_adapter"], authorize_binding=lambda *_: True)
            db.session.commit()
            return row.id, None
        except ContextGrantError as error:
            db.session.rollback()
            return None, str(error)

    # Profile-dependent adapter: staging answers with the synthetic mock, production never trusts.
    from src.clinic.kis import DisabledAdapter, MockKisAdapter
    class FakeMock(KisAdapter):
        is_mock = True
    if mode == "staging":
        out["default_adapter_before_use"] = "clinic_kis_adapter" in app.extensions
        app.extensions["clinic_kis_adapter"] = MockKisAdapter()
    elif mode == "production":
        app.extensions["clinic_kis_adapter"] = FakeMock()

policy.register_grant_provider(lambda uid: [policy.Grant("own", frozenset({"release"}))] if uid in ids.values() else [])


def login(uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(uid); s["_fresh"] = True
    return c, {"X-CSRF-Token": c.get("/api/csrf-token").json["csrf_token"]}

ca, ha = login(ids["a"]); cb, hb = login(ids["b"])


def release(client, headers, did, revs, context_id):
    D = f"/api/clinic/v1/dictations/{did}"
    lease = client.post(D + "/leases", headers={**headers, "Idempotency-Key": str(uuid.uuid4())})
    with app.app_context():
        version = db.session.get(ClinicDictation, did, populate_existing=True).version_counter
    held = lease.json if lease.status_code == 200 else {"lease_id": "none", "generation": 1}
    body = {"lease_id": held.get("lease_id"), "lease_generation": held.get("generation"),
            "original_version_id": revs[0], "transcript_revision_id": revs[1], "context_revision_id": context_id,
            "document_type": "report", "mode": "PLATFORM_REVIEWED_KIS_DRAFT", "text_sha256": "t" * 64,
            "confirmed_content": True, "confirmed_context": True}
    r = client.post(D + "/releases", headers={**headers, "If-Match": f'"{did}:{version}"',
                                              "Idempotency-Key": str(uuid.uuid4())}, json=body)
    return r.status_code, r.get_data(as_text=True)[:400]


def bound_dictation(nonce):
    with app.app_context():
        did, revs = make(ids["a"], bound_nonce=nonce)
        revision, error = bind(did, ids["a"], nonce)
        if mode == "production":
            # Durable provenance exists, so only the profile can be the reason for the refusal.
            revision = db.session.scalar(db.select(ClinicContextRevision.id).filter_by(dictation_id=did)) if revision else None
            if revision is None:
                ctx = ClinicContextRevision(dictation_id=did)
                db.session.add(ctx); db.session.flush()
                db.session.add(ClinicContextGrantUse(
                    revision_id=ctx.id, nonce_digest=nonce.ljust(64, "0"), issuer="kis", audience="speakr",
                    actor_user_id=ids["a"], subject_reference="subject", purpose="bind_existing", verified_at=NOW))
                db.session.commit(); revision = ctx.id
        return did, revs, revision, error

if mode == "production":
    did, revs, revision, error = bound_dictation("prod-nonce")
    out["prod_with_provenance"] = release(ca, ha, did, revs, revision)
    did, revs = None, None
    with app.app_context():
        did, revs = make(ids["a"])
        ctx = revs[2]
    out["prod_unbound"] = release(ca, ha, did, revs, ctx)
    print("RESULT:" + json.dumps(out)); sys.exit(0)

# staging: first the acceptance ledger is left as wired (required, no approver).
did, revs, revision, error = bound_dictation("n-accept")
out["bind_error"] = error
out["bound_acceptance_gate"] = release(ca, ha, did, revs, revision)

# Then relax only the acceptance ledger default, which another package wires, to reach finalize.
original_publish = AcceptanceLedger.publish
def relaxed(self, scope, subject_id, *, required, **kw):
    return original_publish(self, scope, subject_id, required=False, **kw)
AcceptanceLedger.publish = relaxed

did, revs, revision, error = bound_dictation("n-final")
out["bound_final"] = release(ca, ha, did, revs, revision)
out["foreign_user"] = release(cb, hb, did, revs, revision)[0]

with app.app_context():
    plain_id, plain_revs = make(ids["a"])
out["unbound"] = release(ca, ha, plain_id, plain_revs, plain_revs[2])

with app.app_context():
    # Failed binds leave the plain context untrusted: expired, replayed, foreign-user grant.
    out["bind_expired"] = bind(plain_id, ids["a"], "n-exp", ttl=-5)[1]
    out["bind_replayed"] = bind(plain_id, ids["a"], "n-final")[1]
    out["bind_foreign"] = bind(plain_id, ids["a"], "n-foreign", user=ids["b"])[1]
out["after_failed_binds"] = release(ca, ha, plain_id, plain_revs, plain_revs[2])

# Metadata fields: signed-in read, anonymous refused, non-admin write refused.
out["fields_anon"] = app.test_client().get(FIELDS).status_code
out["fields_get"] = ca.get(FIELDS).status_code
out["fields_put_non_admin"] = ca.put(FIELDS + "/synthetic", headers=ha, json={}).status_code
print("RESULT:" + json.dumps(out))
'''


def _run(mode: str, tmp_path: Path) -> dict:
    env = {**os.environ, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'x29.db'}",
           "UPLOAD_FOLDER": str(tmp_path / "uploads"), "RATELIMIT_ENABLED": "false",
           "DISABLE_VOICE_EMBEDDING_CHECK": "true", "ENABLE_AUTO_PROCESSING": "false"}
    env.pop("CLINIC_MODE", None)
    env.pop("CLINIC_PROFILE", None)
    if mode != "off":
        env["CLINIC_MODE"] = "1"
        env["CLINIC_PROFILE"] = mode
    done = subprocess.run([sys.executable, "-c", SCRIPT, mode], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=300)
    lines = [line for line in done.stdout.splitlines() if line.startswith("RESULT:")]
    assert lines, done.stderr[-3000:]
    return json.loads(lines[-1][len("RESULT:"):])


@pytest.fixture(scope="module")
def staging(tmp_path_factory):
    return _run("staging", tmp_path_factory.mktemp("x29_staging"))


@pytest.fixture(scope="module")
def production(tmp_path_factory):
    return _run("production", tmp_path_factory.mktemp("x29_production"))


def test_clinic_mode_off_leaves_the_url_map_unchanged(tmp_path):
    result = _run("off", tmp_path)
    assert result["clinic_endpoints"] == []
    assert result["fields_rule_mounted"] is False


def test_metadata_field_routes_are_classified_auth_and_admin(staging):
    assert staging["field_kinds"] == {"clinic_metadata_fields.list_fields": "auth",
                                      "clinic_metadata_fields.save_field": "admin"}
    assert staging["fields_rule_mounted"] is True
    assert staging["unclassified"] == []
    assert staging["fields_anon"] in (302, 401)
    assert staging["fields_get"] == 200
    assert staging["fields_put_non_admin"] in (401, 403)


def test_bound_grant_passes_the_trust_gate_and_finalizes_in_staging(staging):
    assert staging["bind_error"] is None
    # Trust is satisfied: the next gate (acceptance, wired elsewhere) is the one that answers.
    status, body = staging["bound_acceptance_gate"]
    assert status == 422 and "ACCEPTANCE_REQUIRED" in body and "UNTRUSTED_CONTEXT" not in body
    status, body = staging["bound_final"]
    assert status == 201, body


def test_unbound_expired_replayed_and_foreign_contexts_are_refused(staging):
    assert staging["foreign_user"] == 404
    for key in ("unbound", "after_failed_binds"):
        status, body = staging[key]
        assert status == 422 and "UNTRUSTED_CONTEXT" in body, key
    assert staging["bind_expired"] == "invalid_grant"
    assert staging["bind_replayed"] == "grant_replayed"
    assert staging["bind_foreign"] == "invalid_grant"


def test_production_profile_refuses_every_context(production):
    for key in ("prod_with_provenance", "prod_unbound"):
        status, body = production[key]
        assert status == 422 and "UNTRUSTED_CONTEXT" in body, key
