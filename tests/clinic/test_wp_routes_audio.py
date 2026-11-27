"""Registration and classification of the audio, property, bundle, live, correction and TTS handlers (w198).

PAR-REC-001, PAR-COR-002, PAR-LIVE-001; R-011, R-042.

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
    "clinic_audio.get_current", "clinic_audio.edit", "clinic_audio.derived",
    "clinic_dictation_properties.validate", "clinic_dictation_properties.protection_state",
    "clinic_dictation_properties.set_protection", "clinic_dictation_properties.unlock",
    "clinic_bundle.export", "clinic_bundle.import_session",
    "clinic_live.pause", "clinic_live.resume_recording", "clinic_live.append_phrase",
    "clinic_live.resume", "clinic_live.switch_draft",
    "clinic_correction.get_correction", "clinic_correction.save", "clinic_correction.add_marker",
    "clinic_correction.change_marker",
    "clinic_tts.capability", "clinic_tts.speak",
}

SCRIPT = r'''
import hashlib, io, json, os, sys, uuid, wave, zipfile
from pathlib import Path
from datetime import datetime, timezone
from src.app import app, db
from src.init_db import initialize_database
from src.models import AuditEvent, Recording, User
from src.clinic import policy
from src.services import audit
from src.clinic.identity import ClinicAccountPolicy
from src.clinic.models import ClinicDictation, ClinicOriginal
from src.clinic.route_policy import ROUTE_POLICY

out = {}
endpoints = {r.endpoint for r in app.url_map.iter_rules()}
out["clinic_endpoints"] = sorted(e for e in endpoints if e.startswith("clinic_"))
if sys.argv[1] == "off":
    with app.app_context():
        initialize_database(app)
    c = app.test_client()
    out["off_status"] = [c.get("/api/clinic/v1/tts/capability").status_code,
                         c.get("/api/clinic/v1/live/dictations/x/resume").status_code]
    print("RESULT:" + json.dumps(out)); sys.exit(0)

out["unclassified"] = sorted(e for e in app.url_map._rules_by_endpoint if e not in ROUTE_POLICY)
out["kinds"] = {e: ROUTE_POLICY[e].kind for e in endpoints if e.startswith("clinic_")}
out["parity_rules"] = sum(1 for r in app.url_map.iter_rules() if r.endpoint == "clinic_parity.execute")

from src.clinic.audio_revisions import init_audio_revision
from src.clinic.lease import acquire
from src.clinic.revisions import add_asr_revision

BODY = "Anamnese Husten seit drei Tagen."
with app.app_context():
    initialize_database(app)
    ids = {}
    for name, admin in (("a", False), ("b", False), ("admin", True), ("gone", True)):
        u = User(username=f"w198_{name}", email=f"w198_{name}@local.test", password="x", is_admin=admin)
        db.session.add(u); db.session.flush(); ids[name] = u.id
    for name in ("admin", "gone"):
        db.session.add(ClinicAccountPolicy(user_id=ids[name], status="active" if name == "admin" else "disabled"))
    rec = Recording(user_id=ids["a"], title="Alpha", status="COMPLETED", original_filename="a.wav")
    db.session.add(rec); db.session.flush()
    dic = ClinicDictation(recording_id=rec.id, author_user_id=ids["a"], version_counter=0)
    db.session.add(dic); db.session.flush()
    orig = ClinicOriginal(dictation_id=dic.id, content_hash="h" * 64, size_bytes=1, storage_key="k",
                          receipt="r", origin_ref="o")
    db.session.add(orig); db.session.flush()
    asr = add_asr_revision(dic, orig, BODY, "model:test")
    head = init_audio_revision(dic, orig, 1000)
    lease = acquire(dic.id, ids["a"], datetime.now(timezone.utc), duration=3600)
    # A second dictation whose original is a real file. The first one stays without
    # bytes so derived playback still answers unavailable.
    ms, rate = 1000, 16000
    buf = io.BytesIO()
    with wave.open(buf, "wb") as writer:
        writer.setparams((1, 2, rate, rate * ms // 1000, "NONE", "not compressed"))
        writer.writeframes(b"\x01\x00" * (rate * ms // 1000))
    pcm = buf.getvalue()
    storage_key = "clinic/originals/v1/p067.wav"
    dest = Path(os.environ["UPLOAD_FOLDER"]) / storage_key
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(pcm)
    rec2 = Recording(user_id=ids["a"], title="Beta", status="COMPLETED", original_filename="b.wav")
    db.session.add(rec2); db.session.flush()
    dic2 = ClinicDictation(recording_id=rec2.id, author_user_id=ids["a"], version_counter=0)
    db.session.add(dic2); db.session.flush()
    orig2 = ClinicOriginal(dictation_id=dic2.id, content_hash=hashlib.sha256(pcm).hexdigest(),
                           size_bytes=len(pcm), storage_key=storage_key, receipt="r2", origin_ref="o2")
    db.session.add(orig2); db.session.flush()
    add_asr_revision(dic2, orig2, BODY, "model:test")
    init_audio_revision(dic2, orig2, ms)
    db.session.commit()
    rec_id, did, did2, head_id, asr_id, gen = rec.id, dic.id, dic2.id, head.id, asr.id, lease.generation

def login(uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(uid); s["_fresh"] = True
    return c, {"X-CSRF-Token": c.get("/api/csrf-token").json["csrf_token"]}

# Explicit export authority for author a only (own default never includes export).
policy.register_grant_provider(lambda uid: [policy.Grant("own", frozenset({"export"}))] if uid == ids["a"] else [])
D = f"/api/clinic/v1/dictations/{did}"
anon = app.test_client()
for name, path in (("audio", D + "/audio-revisions/current"), ("tts", "/api/clinic/v1/tts/capability"),
                   ("bundle", f"/api/clinic/v1/bundles/{did}/export"), ("correction", D + "/correction"),
                   ("protection", D + "/protection")):
    out["anon_" + name] = anon.get(path).status_code

ca, ha = login(ids["a"]); cb, hb = login(ids["b"]); cadm, hadm = login(ids["admin"]); cgone, hgone = login(ids["gone"])
key = lambda: {"Idempotency-Key": str(uuid.uuid4())}

# audio
out["audio_get"] = [ca.get(D + "/audio-revisions/current").status_code, cb.get(D + "/audio-revisions/current").status_code]
out["audio_admin"] = [cadm.get(D + "/audio-revisions/current").status_code, cgone.get(D + "/audio-revisions/current").status_code]
with app.app_context():
    audit.flush()
    out["admin_audit"] = db.session.query(AuditEvent).filter_by(
        actor_user_id=ids["admin"], recording_id=rec_id, event="recording.content.read").count()
    out["gone_audit"] = db.session.query(AuditEvent).filter_by(actor_user_id=ids["gone"]).count()
cut = {"operation": "cut", "audio_revision_id": head_id, "lease_generation": gen, "start_ms": 100, "end_ms": 200}
out["edit_no_csrf"] = ca.post(D + "/audio-edits", headers=key(), json=cut).status_code
out["edit_foreign"] = cb.post(D + "/audio-edits", headers={**hb, **key()}, json=cut).status_code
out["edit_admin"] = cadm.post(D + "/audio-edits", headers={**hadm, **key()}, json=cut).status_code
k = key()
first = ca.post(D + "/audio-edits", headers={**ha, **k}, json=cut)
out["edit_ok"] = first.status_code
out["edit_body"] = first.get_data(as_text=True)[:300]
out["edit_replay"] = ca.post(D + "/audio-edits", headers={**ha, **k}, json=cut).status_code
out["edit_stale"] = ca.post(D + "/audio-edits", headers={**ha, **key()}, json=cut).status_code
out["derived"] = [ca.get(D + "/audio/derived").status_code, cb.get(D + "/audio/derived").status_code]

# correction
correction = ca.get(D + "/correction")
out["cor_get"] = [correction.status_code, cb.get(D + "/correction").status_code]
match = {"If-Match": correction.headers["ETag"]}
save = {"text": BODY + " Neu.", "audio_position_ms": 0, "lease_generation": gen, "expected_text_revision_id": asr_id}
out["cor_no_csrf"] = ca.post(D + "/correction/save", headers=key(), json=save).status_code
foreign = cb.post(D + "/correction/save", headers={**hb, **key()}, json=save)
unknown = cb.post(f"/api/clinic/v1/dictations/{uuid.uuid4()}/correction/save", headers={**hb, **key()}, json=save)
out["cor_foreign"] = foreign.status_code
out["cor_concealed"] = (
    foreign.json.keys() == unknown.json.keys() == {"type", "title", "status", "code", "correlation_id"}
    and all(foreign.json[k] == unknown.json[k] for k in ("type", "title", "status", "code"))
    and unknown.status_code == 404 and not foreign.headers.get("ETag"))
missing = ca.post(D + "/correction/save", headers={**ha, **key()}, json=save)
out["cor_missing"] = [missing.status_code, missing.json["code"]]
malformed = ca.post(D + "/correction/save", headers={**ha, **key(), "If-Match": "*"}, json=save)
out["cor_malformed"] = malformed.status_code
r = ca.post(D + "/correction/save", headers={**ha, **key(), **match}, json=save)
out["cor_save"] = r.status_code; out["cor_body"] = r.get_data(as_text=True)[:300]
out["cor_stale"] = ca.post(D + "/correction/save", headers={**ha, **key(), "If-Match": r.json["etag"]}, json=save).status_code
out["cor_stale_header"] = ca.post(D + "/correction/save", headers={**ha, **key(), **match},
                                 json={**save, "expected_text_revision_id": r.json["text_revision_id"]}).status_code

# dictation properties
P = D + "/protection"
out["prot_get"] = [ca.get(P).status_code, cb.get(P).status_code]
out["prot_no_csrf"] = ca.put(P, json={"version": 1, "secret": "geheim-12345"}).status_code
out["prot_foreign"] = cb.put(P, headers=hb, json={"version": 1, "secret": "geheim-12345"}).status_code
out["prot_set"] = ca.put(P, headers=ha, json={"version": 1, "secret": "geheim-12345"}).status_code
out["prot_stale"] = ca.put(P, headers=ha, json={"version": 1, "secret": "geheim-12345"}).status_code
out["validate_no_csrf"] = ca.post(D.rsplit("/", 1)[0] + "/properties/validate", json={}).status_code
out["validate_empty"] = ca.post(D.rsplit("/", 1)[0] + "/properties/validate", headers=ha, json={}).status_code

# bundle
B = f"/api/clinic/v1/bundles/{did}"
out["bundle_export"] = [ca.get(B + "/export?format=txt").status_code, cb.get(B + "/export?format=txt").status_code,
                        cadm.get(B + "/export?format=txt").status_code]
imp = {"If-Match": '"2"', "Idempotency-Key": str(uuid.uuid4())}
out["import_no_csrf"] = ca.post(B + "/import?format=txt", headers=imp, data=b"Text").status_code
out["import_foreign"] = cb.post(B + "/import?format=txt", headers={**hb, **imp}, data=b"Text").status_code
out["import_no_match"] = ca.post(B + "/import?format=txt", headers={**ha, "Idempotency-Key": imp["Idempotency-Key"]}, data=b"Text").status_code
out["import_stale"] = ca.post(B + "/import?format=txt", headers={**ha, **imp, "If-Match": '"1"'}, data=b"Text").status_code
r = ca.post(B + "/import?format=txt", headers={**ha, **imp}, data=b"Text")
out["import_a"] = [r.status_code, r.get_json()["code"]]

# mounted export of the dictation that has real audio
from src.clinic.app_wiring import make_source_loader
from src.clinic.audio_render import render_current_revision
from src.clinic.audio_revisions import current_audio_revision, get_revision_edl
from src.clinic.startup import originals_root
with app.app_context():
    row = db.session.get(ClinicDictation, did2)
    rev = current_audio_revision(row)
    edl = get_revision_edl(row, rev)
    expected_wav = render_current_revision(row, rev.id, make_source_loader(originals_root(app))(row, edl)).wav
B2 = f"/api/clinic/v1/bundles/{did2}"
sdict = ca.get(B2 + "/export?format=sdict")
txt = ca.get(B2 + "/export?format=txt")
rtf = ca.get(B2 + "/export?format=rtf")
archive = zipfile.ZipFile(io.BytesIO(sdict.data))
out["sdict"] = [sdict.status_code, sdict.mimetype, archive.read("text/body.txt").decode("utf-8"),
                archive.read("audio/a.wav") == expected_wav]
out["txt"] = [txt.status_code, txt.mimetype, txt.get_data(as_text=True)]
out["rtf"] = [rtf.status_code, rtf.mimetype, rtf.data.startswith(b"{\\rtf1"), BODY.encode("ascii") in rtf.data]
out["sdict_foreign"] = cb.get(B2 + "/export?format=sdict").status_code

# live (+ parity commands on the one dispatcher)
L = "/api/clinic/v1/live"
cmd = lambda: {"command": "start_live_session", "request_id": str(uuid.uuid4()), "expected_revision": 0,
               "payload": {"dictation_id": did, "profile_revision_id": str(uuid.uuid4()), "capture_mime": "audio/webm"}}
C = "/api/clinic/v1/parity/commands"
out["live_cmd"] = [ca.post(C, headers={**ha, **key()}, json=cmd()).status_code,
                   cb.post(C, headers={**hb, **key()}, json=cmd()).status_code]
out["live_no_csrf"] = ca.post(f"{L}/dictations/{did}/resume", json={}).status_code
out["live_resume"] = [ca.post(f"{L}/dictations/{did}/resume", headers=ha, json={}).status_code,
                      cb.post(f"{L}/dictations/{did}/resume", headers=hb, json={}).status_code]
out["live_draft"] = ca.post(f"{L}/drafts/{did}/switch", headers=ha, json={"tab_id": "t"}).get_json()["code"]

# tts: no declared capability and no engine, the policy and the engine are never reached
out["tts_cap"] = ca.get("/api/clinic/v1/tts/capability").json
out["tts_speak"] = [ca.post("/api/clinic/v1/tts/speak", headers=ha, json={"dictation_id": did, "version": 1}).get_json()["code"],
                    ca.post("/api/clinic/v1/tts/speak", json={"dictation_id": did, "version": 1}).status_code]
print("RESULT:" + json.dumps(out))
'''


def _run(mode: str, tmp_path: Path) -> dict:
    env = {**os.environ, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'w198.db'}",
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
    return _run("on", tmp_path_factory.mktemp("w198_on"))


def test_clinic_mode_off_registers_nothing(tmp_path):
    result = _run("off", tmp_path)
    assert result["clinic_endpoints"] == []
    assert result["off_status"] == [404, 404]


def test_real_url_map_lists_every_handler_classified_as_auth(on):
    assert EXPECTED <= set(on["clinic_endpoints"])
    assert {on["kinds"][e] for e in EXPECTED} == {"auth"}
    assert on["unclassified"] == []
    assert on["parity_rules"] == 1


def test_anonymous_is_refused_before_any_handler(on):
    for name in ("audio", "tts", "bundle", "correction", "protection"):
        assert on["anon_" + name] in (302, 401), name


def test_audio_is_scoped_to_the_actor_and_an_active_admin_may_open_it(on):
    assert on["audio_get"] == [200, 404]
    assert on["audio_admin"] == [200, 404]  # W12: active admin yes, disabled admin no
    assert on["admin_audit"] == 1 and on["gone_audit"] == 0
    assert on["derived"] == [503, 404]  # no durable audio source is wired: unavailable, not faked


def test_audio_edit_csrf_cross_user_stale_and_replay(on):
    assert on["edit_no_csrf"] in (400, 403)
    assert (on["edit_foreign"], on["edit_admin"]) == (404, 404)  # an admin opens audio, never edits it
    assert (on["edit_ok"], on["edit_replay"], on["edit_stale"]) == (200, 200, 409)


def test_correction_save_guards_and_stale_version(on):
    assert on["cor_get"] == [200, 404]
    assert on["cor_no_csrf"] in (400, 403)
    assert on["cor_foreign"] == 404
    assert on["cor_concealed"]
    assert on["cor_missing"] == [428, "PRECONDITION_REQUIRED"]
    assert on["cor_malformed"] == 400
    assert on["cor_stale_header"] == 409
    assert (on["cor_save"], on["cor_stale"]) == (200, 409)


def test_dictation_protection_guards_and_stale_version(on):
    assert on["prot_get"] == [200, 404]
    assert on["prot_no_csrf"] == 403
    assert on["prot_foreign"] == 404
    assert (on["prot_set"], on["prot_stale"]) == (200, 409)
    assert (on["validate_no_csrf"], on["validate_empty"]) == (403, 422)


def test_bundle_needs_the_export_grant_and_import_stays_unavailable(on):
    assert on["bundle_export"] == [200, 404, 404]
    assert on["import_no_csrf"] in (400, 403)
    assert on["import_foreign"] == 404
    assert on["import_no_match"] == 428
    assert on["import_stale"] == 409
    assert on["import_a"] == [503, "DEPENDENCY_UNAVAILABLE"]


def test_mounted_sdict_contains_current_audio_and_text(on):
    shown = "Anamnese Husten seit drei Tagen."
    assert on["sdict"] == [200, "application/zip", shown, True]
    assert on["txt"] == [200, "text/plain", shown]
    assert on["rtf"] == [200, "application/rtf", True, True]
    assert on["sdict_foreign"] == 404


def test_live_commands_use_the_shared_dispatcher_and_session_actor(on):
    assert on["live_cmd"] == [200, 403]
    assert on["live_no_csrf"] in (400, 403)
    assert on["live_resume"] == [200, 403]
    assert on["live_draft"] == "CAPABILITY_UNAVAILABLE"


def test_tts_answers_unavailable_without_engine_and_checks_csrf(on):
    assert on["tts_cap"] == {"available": False}
    assert on["tts_speak"][0] == "CAPABILITY_UNAVAILABLE"
    assert on["tts_speak"][1] in (400, 403)
