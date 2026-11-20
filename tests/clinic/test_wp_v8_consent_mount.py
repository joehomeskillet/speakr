"""Voice consent and voice enrollment routes mounted in the real app (v8-75).

The real application (src/app.py) is built in a child process with CLINIC_MODE on and off.
With it on, both blueprints (own-user voice consent, own-user voice enrollment) exist, are
classified in the route policy, answer 401 without a login, and work for the own user. Voice
profiles are opt-in: enrollment is refused without consent and again after revocation.
With CLINIC_MODE off none of the routes exist (404). Synthetic throwaway database, synthetic
user and one synthetic embedding; the speaker service is replaced by a fixed vector.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

CONSENT = "/api/clinic/v1/voice-consent"
VOICE = "/api/clinic/v1/voice"

EXPECTED = {
    "clinic_voice_consent.get_consent": ("GET", CONSENT),
    "clinic_voice_consent.grant": ("POST", CONSENT + "/grant"),
    "clinic_voice_consent.revoke": ("POST", CONSENT + "/revoke"),
    "voice_enrollment.get_own_voice_enrollment": ("GET", VOICE + "/enrollment"),
    "voice_enrollment.enroll_own_voice_sample": ("POST", VOICE + "/enrollment"),
    "voice_enrollment.grant_own_voice_consent": ("POST", VOICE + "/consent"),
    "voice_enrollment.revoke_own_voice_consent": ("POST", VOICE + "/consent/revoke"),
}

CHILD = r'''
import io, json, struct, sys, wave
from src.app import app
from src.clinic import speaker_client
from src.clinic.route_policy import ROUTE_POLICY
from src.database import db
from src.init_db import initialize_database
from src.models import User

expected = json.loads(sys.argv[1])
CONSENT, VOICE = sys.argv[2], sys.argv[3]
with app.app_context():
    initialize_database(app)
    user = User(username="v875", email="v875@example.invalid", password="x")
    db.session.add(user)
    db.session.commit()
    uid = str(user.id)

endpoints = {rule.endpoint: sorted(rule.methods - {"HEAD", "OPTIONS"}) for rule in app.url_map.iter_rules()}
out = {
    "blueprints": sorted(app.blueprints),
    "endpoints": {name: endpoints.get(name) for name in expected},
    "classified": {name: ROUTE_POLICY[name].kind if name in ROUTE_POLICY else None for name in expected},
}

# Anonymous: the answer under test is the auth refusal, not the CSRF one.
app.config["WTF_CSRF_ENABLED"] = False
anon = app.test_client()
out["anonymous"] = {name: anon.open(path, method=method).status_code for name, (method, path) in expected.items()}
app.config["WTF_CSRF_ENABLED"] = True

if not out["endpoints"] or not any(out["endpoints"].values()):
    print("RESULT " + json.dumps(out))
    sys.exit(0)

def wav():
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(struct.pack("<h", 1000) * 16000 * 4)
    return buf.getvalue()

speaker_client.embed_with_space = lambda path, timeout=0: speaker_client.EmbeddingResult(None, "synthetic", [0.25, -0.5])

client = app.test_client()
with client.session_transaction() as sess:
    sess["_user_id"] = uid
    sess["_fresh"] = True
token = client.get("/api/csrf-token").get_json()["csrf_token"]
n = [0]
def headers(extra=None):
    n[0] += 1
    h = {"X-CSRF-Token": token, "Idempotency-Key": "v875-key-%04d" % n[0]}
    h.update(extra or {})
    return h
def short(r):
    body = r.get_json(silent=True) or {}
    return [r.status_code, body.get("state"), body.get("embedding_count"), body.get("code")]
def enroll(rev):
    return short(client.post(VOICE + "/enrollment", data=wav(), headers=headers({"Content-Type": "audio/wav", "If-Match": '"voice-consent:%d"' % rev})))

steps = {}
steps["get_consent"] = short(client.get(CONSENT))
steps["get_enrollment"] = short(client.get(VOICE + "/enrollment"))
steps["enroll_without_consent"] = enroll(0)
r = client.post(CONSENT + "/grant", headers=headers(), json={"expected_consent_id": 0, "statement_text": "Ich willige ein."})
steps["grant"] = short(r)
steps["enroll_with_consent"] = enroll(r.get_json()["latest_consent_id"])
r = client.post(CONSENT + "/revoke", headers=headers(), json={"expected_consent_id": r.get_json()["latest_consent_id"]})
steps["revoke"] = short(r)
steps["enroll_after_revoke"] = enroll(r.get_json()["latest_consent_id"])
# Same cycle through the enrollment module's own consent routes.
rev = client.get(VOICE + "/enrollment").get_json()["consent_revision"]
r = client.post(VOICE + "/consent", headers=headers({"Content-Type": "application/json", "If-Match": '"voice-consent:%d"' % rev}),
                data=json.dumps({"statement_text": "Ich willige ein."}))
steps["grant_enrollment_route"] = short(r)
rev = r.get_json()["consent_revision"]
steps["enroll_with_consent_2"] = enroll(rev)
r = client.post(VOICE + "/consent/revoke", headers=headers({"Content-Type": "application/json", "If-Match": '"voice-consent:%d"' % rev}),
                data=json.dumps({"confirm": True}))
steps["revoke_enrollment_route"] = short(r)
steps["enroll_after_revoke_2"] = enroll(r.get_json()["consent_revision"])
out["steps"] = steps
print("RESULT " + json.dumps(out))
'''


def _build(tmp_path: Path, clinic: bool) -> dict:
    env = {**os.environ, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'reg.db'}",
           "UPLOAD_FOLDER": str(tmp_path / "uploads"), "RATELIMIT_ENABLED": "false",
           "DISABLE_VOICE_EMBEDDING_CHECK": "true", "ENABLE_AUTO_PROCESSING": "false"}
    env.pop("CLINIC_MODE", None)
    for name in ("CLINIC_AGENT_PROXY_SECRET", "CLINIC_AGENT_MASTER_SECRET"):
        env.pop(name, None)
    if clinic:
        env["CLINIC_MODE"] = "1"
    done = subprocess.run([sys.executable, "-c", CHILD, json.dumps(EXPECTED), CONSENT, VOICE], cwd=ROOT,
                          env=env, capture_output=True, text=True, timeout=300)
    line = next((x for x in done.stdout.splitlines() if x.startswith("RESULT ")), None)
    assert line, done.stderr[-3000:]
    return json.loads(line[len("RESULT "):])


@pytest.fixture(scope="module")
def clinic_app(tmp_path_factory):
    return _build(tmp_path_factory.mktemp("voice_on"), True)


@pytest.fixture(scope="module")
def plain_app(tmp_path_factory):
    return _build(tmp_path_factory.mktemp("voice_off"), False)


def test_every_endpoint_exists_with_its_method_in_clinic_mode(clinic_app):
    assert {"clinic_voice_consent", "voice_enrollment"} <= set(clinic_app["blueprints"])
    for endpoint, (method, _path) in EXPECTED.items():
        assert clinic_app["endpoints"][endpoint] == [method], endpoint


def test_every_endpoint_is_classified_auth_in_the_route_policy(clinic_app):
    assert clinic_app["classified"] == {name: "auth" for name in EXPECTED}, clinic_app["classified"]


def test_every_endpoint_answers_401_without_login(clinic_app):
    assert clinic_app["anonymous"] == {name: 401 for name in EXPECTED}, clinic_app["anonymous"]


def test_own_user_consent_and_enrollment_cycle(clinic_app):
    steps = clinic_app["steps"]
    assert steps["get_consent"] == [200, "none", 0, None]
    assert steps["get_enrollment"] == [200, "none", 0, None]
    # Opt-in: no consent, no enrollment.
    assert steps["enroll_without_consent"] == [403, None, None, "CONSENT_REQUIRED"]
    assert steps["grant"] == [201, "granted", 0, None]
    assert steps["enroll_with_consent"] == [201, "granted", 1, None]
    # Revocation stops use and removes the vectors.
    assert steps["revoke"] == [200, "revoked", 0, None]
    assert steps["enroll_after_revoke"] == [403, None, None, "CONSENT_REQUIRED"]


def test_enrollment_module_own_consent_routes_work_and_gate_enrollment(clinic_app):
    steps = clinic_app["steps"]
    assert steps["grant_enrollment_route"] == [200, "granted", 0, None]
    assert steps["enroll_with_consent_2"] == [201, "granted", 1, None]
    assert steps["revoke_enrollment_route"] == [200, "revoked", 0, None]
    assert steps["enroll_after_revoke_2"] == [403, None, None, "CONSENT_REQUIRED"]


def test_none_of_them_exists_with_clinic_mode_off(plain_app):
    assert not {"clinic_voice_consent", "voice_enrollment"} & set(plain_app["blueprints"])
    assert plain_app["endpoints"] == {name: None for name in EXPECTED}
    assert plain_app["anonymous"] == {name: 404 for name in EXPECTED}, plain_app["anonymous"]
