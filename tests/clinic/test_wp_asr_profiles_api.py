"""ASR profile routes (PAR-ASR-002/003/006/007), HTTP slice.

Positive, unauthorized, stale-version, profile-not-found and default-off cases against
the real ``asr_profiles`` service. Capability evidence rows are synthetic and prove no engine.
"""

import secrets

import pytest
from flask import Flask, g, jsonify
from flask_login import LoginManager, login_user
from flask_wtf.csrf import CSRFProtect, generate_csrf

from src.app import app as real_app
from src.clinic import asr_profiles
from src.clinic.asr_capabilities import AsrCapability, AsrCapabilityRegistry, CapabilityEvidence
from src.clinic.asr_job_profile import asr_capability_target, resolve_asr_job_profile
from src.clinic.routes_asr_profiles import ENDPOINTS, PREFIX, create_asr_profiles_blueprint
from src.database import db
from src.models import User

LOCAL_URL = "http://127.0.0.1:9000"
GPU_URL = "http://127.0.0.1:9100"


def _evidence(capability, base_url):
    import dataclasses
    target = dataclasses.replace(asr_capability_target("de", "large-v3"), base_url=base_url)
    return CapabilityEvidence(AsrCapability(capability), target, "set-1", "fixture:1", "runtime_passed")


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setenv("CLINIC_MODE", "1")
    monkeypatch.setenv("ASR_BASE_URL", LOCAL_URL)
    monkeypatch.delenv("CLINIC_GPU_ASR_BASE_URL", raising=False)
    evidence = []
    flask = Flask(__name__)
    flask.config.update(TESTING=True, SECRET_KEY="synthetic",
                        SQLALCHEMY_DATABASE_URI=real_app.config["SQLALCHEMY_DATABASE_URI"])
    db.init_app(flask)
    login = LoginManager(flask)
    CSRFProtect(flask)
    login.user_loader(lambda uid: db.session.get(User, int(uid)))

    @flask.get("/login/<int:uid>")
    def _login(uid):
        login_user(db.session.get(User, uid))
        g.pop("csrf_token", None)
        return jsonify(csrf=generate_csrf())

    flask.register_blueprint(create_asr_profiles_blueprint(lambda: AsrCapabilityRegistry(evidence)))

    with real_app.app_context():
        db.create_all()
        tag = secrets.token_hex(4)
        users = [User(username=f"asrapi_{i}_{tag}", email=f"asrapi_{i}_{tag}@local.test", password="x")
                 for i in range(2)]
        db.session.add_all(users)
        db.session.commit()
        ids = [u.id for u in users]

        def client(uid):
            c = flask.test_client()
            return c, {"X-CSRF-Token": c.get(f"/login/{uid}").json["csrf"]}

        with flask.app_context():
            yield dict(client=client, a=ids[0], b=ids[1], evidence=evidence, flask=flask)
        db.session.rollback()  # profile versions are append-only; synthetic users are unique per run


def _profile(uid, **options):
    return asr_profiles.set_profile(uid, actor_id=uid, language="de", model="large-v3",
                                    options={"source": "headset", **options})


def test_endpoint_list():
    assert ENDPOINTS == (("clinic_asr_profiles.show", "GET", "/api/clinic/v1/asr-profiles"),
                         ("clinic_asr_profiles.select", "PUT", "/api/clinic/v1/asr-profiles/selection"))


def test_get_without_profile_reports_defaults(world):
    c, _ = world["client"](world["a"])
    d = c.get(PREFIX).json
    assert (d["version"], d["profile"], d["profiles"], d["source"]) == (0, None, [], "headset")
    assert d["sources"] == ["headset", "recorder"]
    assert d["prompts_enabled"] is False
    assert d["engine"] == {"configured": "cpu", "actual": "cpu", "reason": "nicht ermittelt"}
    assert d["capabilities"] == {"configured": [], "proven": []}
    assert d["output_format"] == {"container": "webm", "codec": "opus", "sample_rate": 48000}


def test_get_profile_wait_and_unproven_capabilities(world):
    p = _profile(world["a"], skip_wait_minutes=5)
    c, _ = world["client"](world["a"])
    d = c.get(PREFIX + "?supported=audio/wav").json
    assert d["profile"] == {"id": p.id, "name": "large-v3", "version": 1}
    assert d["profiles"] == [{"id": p.id, "name": "large-v3", "mode": "both"}]
    assert d["live_profile_id"] == d["batch_profile_id"] == p.id
    assert d["output_format"] == {"container": "wav", "codec": None, "sample_rate": 48000}
    assert d["wait"] == {
        "offer_manual_after_s": 300, "hard_timeout_s": 900, "manual_on_timeout": True,
        "skip_wait_minutes": 5,
    }
    # Configured is not proven: no evidence means an empty proven list and no actual-engine claim.
    assert d["capabilities"] == {"configured": ["batch", "live"], "proven": []}
    assert d["engine"]["reason"] == "nicht ermittelt"
    assert c.get(PREFIX + "?supported=video/mp4").status_code == 400


def test_oversized_skip_wait_falls_back_to_default(world):
    _profile(world["a"], skip_wait_minutes=60)
    c, _ = world["client"](world["a"])
    wait = c.get(PREFIX).json["wait"]
    assert wait["offer_manual_after_s"] == 120
    assert wait["skip_wait_minutes"] == 60


def test_put_skip_wait_minutes_stores_two_minutes_as_120_seconds(world):
    other = _profile(world["b"], skip_wait_minutes=9, save_rule="ask")
    before = (other.version, dict(other.options))
    c, h = world["client"](world["a"])
    saved = c.put(PREFIX + "/selection", headers=h, json={
        "source": "headset", "expected_version": 0, "skip_wait_minutes": 2})
    assert saved.status_code == 200
    assert saved.json["wait"]["skip_wait_minutes"] == 2
    assert saved.json["wait"]["offer_manual_after_s"] == 120
    again = c.get(PREFIX).json["wait"]
    assert again["offer_manual_after_s"] == 120
    assert again["skip_wait_minutes"] == 2
    own = asr_profiles.ClinicAsrProfile.query.filter_by(user_id=world["a"]).one()
    assert own.options["skip_wait_minutes"] == 2
    foreign = asr_profiles.ClinicAsrProfile.query.filter_by(user_id=world["b"]).one()
    assert (foreign.version, foreign.options) == before


def test_put_wait_disabled_hides_manual_offer_while_running(world):
    _profile(world["b"], skip_wait_minutes=4)
    c, h = world["client"](world["a"])
    saved = c.put(PREFIX + "/selection", headers=h, json={
        "source": "recorder", "expected_version": 0, "skip_wait_minutes": 2, "wait_disabled": True})
    assert saved.status_code == 200
    wait = saved.json["wait"]
    assert wait["wait_disabled"] is True
    assert wait["offer_manual_after_s"] is None
    assert wait["manual_on_timeout"] is False
    assert wait["during_run"] == {
        "state": "waiting",
        "can_start_manual": False,
        "message": "Spracherkennung läuft …",
    }
    assert c.get(PREFIX).json["wait"]["during_run"]["can_start_manual"] is False
    own = asr_profiles.ClinicAsrProfile.query.filter_by(user_id=world["a"]).one()
    assert own.options["wait_disabled"] is True
    assert own.options["skip_wait_minutes"] == 2
    foreign = asr_profiles.ClinicAsrProfile.query.filter_by(user_id=world["b"]).one()
    assert foreign.options == {"source": "headset", "skip_wait_minutes": 4}


def test_put_wait_rejects_invalid_values(world):
    c, h = world["client"](world["a"])
    url = PREFIX + "/selection"
    base = {"source": "headset", "expected_version": 0}
    for extra in (
        {"skip_wait_minutes": 0}, {"skip_wait_minutes": True}, {"skip_wait_minutes": -2},
        {"skip_wait_minutes": "2"}, {"wait_disabled": "true"}, {"wait_disabled": 1},
    ):
        assert c.put(url, headers=h, json={**base, **extra}).status_code == 400
    assert asr_profiles.ClinicAsrProfile.query.filter_by(user_id=world["a"]).first() is None


def test_proven_capabilities_and_gpu_fallback_state(world, monkeypatch):
    _profile(world["a"])
    c, _ = world["client"](world["a"])
    world["evidence"].append(_evidence("batch", LOCAL_URL))
    d = c.get(PREFIX).json
    assert d["capabilities"] == {"configured": ["batch", "live"], "proven": ["batch"]}
    assert d["engine"] == {"configured": "cpu", "actual": "cpu", "reason": "nachgewiesen"}
    # GPU configured but only the CPU target is proven: the view shows the fallback.
    monkeypatch.setenv("CLINIC_GPU_ASR_BASE_URL", GPU_URL)
    d = c.get(PREFIX).json
    assert (d["engine"]["configured"], d["engine"]["actual"]) == ("gpu", "cpu")
    world["evidence"].append(_evidence("live", GPU_URL))
    d = c.get(PREFIX).json
    assert (d["engine"]["actual"], d["capabilities"]["proven"]) == ("gpu", ["live"])


def test_put_selection_stores_source_and_bumps_version(world):
    p = _profile(world["a"])
    c, h = world["client"](world["a"])
    r = c.put(PREFIX + "/selection", headers=h, json={
        "source": "recorder", "live_profile_id": p.id, "batch_profile_id": None, "expected_version": 1})
    assert r.status_code == 200 and (r.json["version"], r.json["source"]) == (2, "recorder")
    assert asr_profiles.profile_history(world["a"])[-1].changes["options"][1] == {"source": "recorder"}
    assert c.get(PREFIX).json["source"] == "recorder"


def test_versioned_source_mapping_and_save_rule_reload_and_resolve(world):
    user = world["a"]
    p = _profile(user)
    c, h = world["client"](user)
    second, second_headers = world["client"](user)
    url = PREFIX + "/selection"
    changes = {"source": "recorder", "batch_source": "headset", "profile_mode": "separate",
               "save_rule": "ask", "expected_version": 1}
    assert c.put(url, headers=h, json=changes).status_code == 200
    loaded = second.get(PREFIX).json
    assert (loaded["version"], loaded["source"], loaded["batch_source"], loaded["profile_mode"], loaded["save_rule"]) == (
        2, "recorder", "headset", "separate", "ask")
    assert loaded["live_profile_id"] == loaded["batch_profile_id"] == p.id
    assert asr_profiles.profile_history(user)[-1].snapshot["options"] == {
        "source": "recorder", "batch_source": "headset", "separate_offline_profile": True, "save_rule": "ask"}
    assert second.put(url, headers=second_headers, json=changes).status_code == 409

    registry = AsrCapabilityRegistry([_evidence(mode, LOCAL_URL) for mode in ("live", "batch")])
    def resolve(mode):
        return resolve_asr_job_profile(user, mode, ["audio/wav"], registry)

    for mode, source in (("live", "recorder"), ("batch", "headset")):
        resolved = resolve(mode)
        assert (resolved.shared, resolved.source, resolved.profile_version) == (False, source, 2)
        assert resolved.parameters["source"] == source
        assert resolved.parameters["save_rule"] == "ask"

    shared = {"expected_version": 2, "profile_mode": "shared", "source": "recorder"}
    assert second.put(url, headers=second_headers, json=shared).status_code == 200
    loaded = c.get(PREFIX).json
    assert (loaded["version"], loaded["batch_source"], loaded["save_rule"]) == (3, "recorder", "ask")
    assert all(r.shared and r.source == "recorder" for r in (resolve("live"), resolve("batch")))
    assert "batch_source" not in asr_profiles.profile_history(user)[-1].snapshot["options"]


@pytest.mark.parametrize("updates", [
    {"save_rule": None}, {"save_rule": "sometimes"}, {"profile_mode": "unknown"},
    {"profile_mode": "separate"}, {"profile_mode": "separate", "batch_source": "mic"},
    {"profile_mode": "shared", "batch_source": "headset"}, {"batch_source": "headset"},
])
def test_invalid_mapping_and_save_rule_are_not_persisted(world, updates):
    c, h = world["client"](world["a"])
    result = c.put(PREFIX + "/selection", headers=h, json={
        "source": "recorder", "expected_version": 0, **updates})
    assert (result.status_code, result.json["code"]) == (400, "invalid_payload")
    assert c.get(PREFIX).json["version"] == 0


def test_put_creates_profile_when_none_exists(world):
    c, h = world["client"](world["a"])
    r = c.put(PREFIX + "/selection", headers=h, json={
        "source": "recorder", "live_profile_id": None, "batch_profile_id": None, "expected_version": 0})
    assert r.status_code == 200 and (r.json["version"], r.json["source"]) == (1, "recorder")


def test_stale_version_and_profile_not_found(world):
    p = _profile(world["a"])
    c, h = world["client"](world["a"])
    url = PREFIX + "/selection"
    stale = c.put(url, headers=h, json={"source": "recorder", "expected_version": 7})
    assert (stale.status_code, stale.json["code"], stale.json["current_version"]) == (409, "stale_version", 1)
    assert stale.mimetype == "application/problem+json"
    missing = c.put(url, headers=h, json={"source": "recorder", "live_profile_id": "nope", "expected_version": 1})
    assert (missing.status_code, missing.json["code"]) == (404, "profile_not_found")
    # A profile id of another user is equally unknown.
    other = _profile(world["b"])
    foreign = c.put(url, headers=h, json={"source": "recorder", "batch_profile_id": other.id, "expected_version": 1})
    assert (foreign.status_code, foreign.json["code"]) == (404, "profile_not_found")
    assert c.get(PREFIX).json["version"] == p.version == 1  # nothing changed


def test_users_only_see_and_change_their_own_profile(world):
    _profile(world["a"], save_rule="always")
    cb, hb = world["client"](world["b"])
    assert cb.get(PREFIX).json["profile"] is None
    cb.put(PREFIX + "/selection", headers=hb, json={"source": "recorder", "expected_version": 0})
    assert asr_profiles.ClinicAsrProfile.query.filter_by(user_id=world["a"]).one().options == {
        "source": "headset", "save_rule": "always"}


def test_unauthorized_csrf_clinic_mode_and_untrusted_payloads(world, monkeypatch):
    c, h = world["client"](world["a"])
    anon = world["flask"].test_client()
    assert anon.get(PREFIX).status_code == 401
    url = PREFIX + "/selection"
    ok = {"source": "recorder", "expected_version": 0}
    no_csrf = c.put(url, json=ok)
    assert (no_csrf.status_code, no_csrf.mimetype) == (403, "application/problem+json")
    for key in ("actor_id", "user_id", "role", "is_admin"):
        assert c.put(url, headers=h, json={**ok, key: world["b"]}).status_code == 400
    for bad in ({"source": "mic", "expected_version": 0}, {"source": "recorder", "expected_version": "0"},
                {"source": "recorder", "expected_version": True}, {"source": "recorder"},
                {"source": "recorder", "expected_version": 0, "prompts_enabled": True}):
        assert c.put(url, headers=h, json=bad).status_code == 400
    assert c.put(url, headers=h, data="x").status_code == 400
    monkeypatch.delenv("CLINIC_MODE")
    assert c.get(PREFIX).status_code == 404
