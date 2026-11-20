"""HTTP routes of the import-agent channel (v8-13).

Real SQLite database, real temporary files, the real IngestService and import centre; the
requests use the JSON shapes of apps/windows-import (HttpImportServer.cs, WireModels.cs).
Synthetic data only. Nothing here is evidence for a real agent, certificate or device.
"""

import hashlib
import io
import json
import logging
import uuid

import pytest
from flask import Flask
from flask_login import LoginManager

from src.app import app, db
from src.clinic import agent_channel as ch
from src.clinic import import_centre as ic
from src.clinic import routes_agent
from src.clinic.agent_channel import ChannelPolicy
from src.clinic.ingest import IngestService
from src.clinic.models import ClinicDictation
from src.models import User

SECRET = b"s" * 32
PROXY = "p" * 40
FINGERPRINT = "aa" * 20
CHUNK = 4096
DATA = b"synthetic dictation bytes " * 400  # 10400 bytes, three chunks
MARKER = b"CONFIDENTIAL-AUDIO-MARKER"


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _headers(fingerprint=FINGERPRINT, proxy=PROXY, **extra):
    base = {}
    if proxy is not None:
        base[routes_agent.PROXY_SECRET_HEADER] = proxy
    if fingerprint is not None:
        base[routes_agent.FINGERPRINT_HEADER] = fingerprint
    return {**base, **extra}


@pytest.fixture(autouse=True)
def _env():
    with app.app_context():
        ch.create_tables()
        ic.create_tables()
        yield


def _user(prefix="ag"):
    row = User(username=prefix + uuid.uuid4().hex[:8], email=uuid.uuid4().hex[:8] + "@example.invalid", password="x")
    db.session.add(row)
    db.session.commit()
    return row


@pytest.fixture
def env(tmp_path):
    policy = ChannelPolicy(master_secret=SECRET, authorise=lambda user, action: True, max_chunk_bytes=CHUNK)
    admin, author = _user(), _user("au")
    reg = ch.register_agent(policy, actor_user_id=admin.id, display_name="Agent",
                            allowed_sources=["usb-1"], credential_fingerprint=FINGERPRINT)
    service = IngestService(tmp_path / "store")
    flask = Flask(__name__)
    flask.config.update(TESTING=True, SECRET_KEY="synthetic-agent-test",
                        SQLALCHEMY_DATABASE_URI=app.config["SQLALCHEMY_DATABASE_URI"],
                        SQLALCHEMY_ENGINE_OPTIONS=app.config.get("SQLALCHEMY_ENGINE_OPTIONS", {}))
    db.init_app(flask)
    LoginManager(flask)
    flask.register_blueprint(routes_agent.create_agent_blueprint(policy, service, PROXY))
    return {"client": flask.test_client(), "policy": policy, "admin": admin, "author": author,
            "agent_id": reg["agent_id"], "service": service}


def _item(data=DATA, snapshot="v1", source="src-1"):
    # Fields of InventoryItemWire, including the two the server does not store.
    return {"source_id": source, "root_id": "usb-1", "relative_path": "Inbox/Dict1.DS2", "size": len(data),
            "mtime_utc": "2026-11-18T07:00:00.1234567+00:00", "sha256": _sha(data),
            "snapshot_version": snapshot, "observed_completion": "unknown", "evidence_level": "unverified"}


def _json(env, method, path, body=None, headers=None, **kwargs):
    kwargs.setdefault("content_type", "application/json")
    if "data" not in kwargs:
        kwargs["data"] = json.dumps(body) if body is not None else None
    return getattr(env["client"], method)(
        routes_agent.PREFIX + path, headers=_headers() if headers is None else headers, **kwargs)


def _prepare(env, data=DATA, snapshot="v1"):
    """Inventory over HTTP, intent through the web service, poll over HTTP. Returns the wire intent."""
    response = _json(env, "put", "/inventory", {"items": [_item(data, snapshot)]})
    assert response.status_code == 200
    ic.create_import_intent(actor_user_id=env["author"].id, source_kind="device", source_ref="usb-1",
                            expected_files=["dict1.ds2"])
    ch.create_intent(env["policy"], actor_user_id=env["admin"].id, agent_id=env["agent_id"],
                     source_id="src-1", snapshot_version=snapshot)
    polled = _json(env, "get", "/intents", headers=_headers())
    assert polled.status_code == 200
    return next(w for w in polled.get_json()["intents"] if w["snapshot_version"] == snapshot)


def _begin(env, intent, data=DATA, key="idem-key-0001"):
    return _json(env, "post", "/ingests", {"intent_id": intent["intent_id"], "declared_size": len(data),
                                           "declared_sha256": _sha(data)},
                 headers=_headers(**{"Idempotency-Key": key}))


def _put_chunks(env, ingest_id, data=DATA):
    digests = []
    for sequence, start in enumerate(range(0, len(data), CHUNK)):
        part = data[start:start + CHUNK]
        response = env["client"].put(
            "%s/ingests/%s/chunks/%d" % (routes_agent.PREFIX, ingest_id, sequence), data=part,
            content_type="application/octet-stream",
            headers=_headers(**{"X-Chunk-SHA256": _sha(part), "X-Chunk-Offset": str(start)}))
        assert response.status_code == 200, response.get_data(as_text=True)
        digests.append({"sequence": sequence, "bytes": len(part), "sha256": _sha(part)})
    return digests


def _finalize(env, ingest_id, digests, data=DATA, key="idem-key-0002"):
    return _json(env, "post", "/ingests/%s/finalize" % ingest_id,
                 {"chunks": digests, "total_bytes": len(data), "sha256": _sha(data),
                  "source_finished_reading": True},
                 headers=_headers(**{"Idempotency-Key": key}))


def _dictations():
    db.session.rollback()
    return ClinicDictation.query.count()


# --- every path of HttpImportServer.cs requires the agent credential --------------------------

_GUID = "11111111-2222-3333-4444-555555555555"
_ROUTES = (
    ("put", "/inventory"), ("get", "/intents"), ("post", "/ingests"),
    ("put", "/ingests/%s/chunks/0" % _GUID), ("post", "/ingests/%s/finalize" % _GUID),
    ("get", "/ingests/%s" % _GUID), ("post", "/ingests/%s/abort" % _GUID), ("post", "/outcomes"),
)


@pytest.mark.parametrize("method,path", _ROUTES)
@pytest.mark.parametrize("headers", (
    {},
    {routes_agent.FINGERPRINT_HEADER: FINGERPRINT},
    {routes_agent.PROXY_SECRET_HEADER: PROXY},
    {routes_agent.PROXY_SECRET_HEADER: "x" * 40, routes_agent.FINGERPRINT_HEADER: FINGERPRINT},
    {routes_agent.PROXY_SECRET_HEADER: PROXY, routes_agent.FINGERPRINT_HEADER: "bb" * 20},
    {routes_agent.PROXY_SECRET_HEADER: PROXY, routes_agent.FINGERPRINT_HEADER: "not a fingerprint"},
), ids=("none", "no-proxy-secret", "no-fingerprint", "wrong-proxy-secret", "unknown-agent", "malformed"))
def test_missing_or_invalid_credential_is_401(env, method, path, headers):
    response = getattr(env["client"], method)(routes_agent.PREFIX + path, headers=headers)
    assert response.status_code == 401
    assert response.get_json()["code"] == "unauthorized"


def test_disabled_agent_is_401(env):
    ch.set_agent_enabled(env["policy"], actor_user_id=env["admin"].id, agent_id=env["agent_id"], enabled=False)
    assert _json(env, "get", "/intents").status_code == 401


def test_browser_session_cookie_is_not_a_credential(env):
    with env["client"].session_transaction() as sess:
        sess["_user_id"] = str(env["admin"].id)
    response = env["client"].get(routes_agent.PREFIX + "/intents")
    assert response.status_code == 401


def test_weak_proxy_secret_is_refused_at_construction(env):
    with pytest.raises(ValueError):
        routes_agent.create_agent_blueprint(env["policy"], env["service"], "short")


# --- inventory and intents --------------------------------------------------------------------

def test_inventory_and_intents_use_the_wire_shapes(env):
    body = {"agent_id": "someone-else", "items": [_item()]}
    response = _json(env, "put", "/inventory", body)
    assert response.status_code == 200
    assert response.get_json() == {"accepted": 1}
    # the body's agent_id is ignored: the inventory belongs to the authenticated agent
    rows = ch.list_inventory(env["policy"], actor_user_id=env["admin"].id, agent_id=env["agent_id"])
    assert [r["snapshot_version"] for r in rows] == ["v1"]
    ch.create_intent(env["policy"], actor_user_id=env["admin"].id, agent_id=env["agent_id"],
                     source_id="src-1", snapshot_version="v1")
    wire = _json(env, "get", "/intents?agent_id=someone-else").get_json()
    assert set(wire) == {"intents"}
    intent = wire["intents"][0]
    assert set(intent) == {"intent_id", "kind", "source_id", "snapshot_version", "post_step", "expires_at",
                           "agent_id", "signature"}
    assert intent["agent_id"] == env["agent_id"]


def test_inventory_outside_the_registered_root_is_refused(env):
    item = {**_item(), "root_id": "other-root"}
    response = _json(env, "put", "/inventory", {"items": [item]})
    assert response.status_code == 403
    assert response.get_json()["code"] == "forbidden"


def test_malformed_json_bodies_are_400(env):
    assert _json(env, "put", "/inventory", data="{").status_code == 400
    assert _json(env, "put", "/inventory", {"items": ["x"]}).status_code == 400
    assert _json(env, "post", "/ingests", {"intent_id": "i", "declared_size": "7", "declared_sha256": "a"}).status_code == 400
    assert _json(env, "post", "/outcomes", {"outcome": "stored"}).status_code == 400


# --- ingest flow and idempotency -------------------------------------------------------------

def test_full_ingest_flow_stores_one_dictation_and_replays_are_idempotent(env):
    intent = _prepare(env)
    first = _begin(env, intent)
    assert first.status_code == 201
    receipt = first.get_json()
    assert receipt["state"] == "RECEIVING"
    assert receipt["agent_id"] == env["agent_id"] and receipt["intent_id"] == intent["intent_id"]
    ingest_id = receipt["ingest_id"]
    # replay of the same Idempotency-Key returns the same session
    replay = _begin(env, intent)
    assert replay.get_json()["ingest_id"] == ingest_id
    digests = _put_chunks(env, ingest_id)
    # replayed chunk is a no-op
    again = env["client"].put(
        "%s/ingests/%s/chunks/0" % (routes_agent.PREFIX, ingest_id), data=DATA[:CHUNK],
        content_type="application/octet-stream",
        headers=_headers(**{"X-Chunk-SHA256": _sha(DATA[:CHUNK]), "X-Chunk-Offset": "0"}))
    assert again.status_code == 200 and again.get_json()["received_chunks"] == [0, 1, 2]
    before = _dictations()
    final = _finalize(env, ingest_id, digests)
    assert final.status_code == 200, final.get_data(as_text=True)
    done = final.get_json()
    assert done["state"] == "STORED"
    assert done["sha256"] == _sha(DATA) and done["received_bytes"] == len(DATA)
    assert done["dictation_id"] and done["original_id"]
    assert done["import"]["status"] == "MATCHED"
    assert _dictations() == before + 1
    # replay of finalize and of begin: same dictation, no second one
    final_again = _finalize(env, ingest_id, digests).get_json()
    assert final_again["dictation_id"] == done["dictation_id"]
    assert _begin(env, intent).get_json()["dictation_id"] == done["dictation_id"]
    assert _dictations() == before + 1
    got = _json(env, "get", "/ingests/" + ingest_id)
    assert got.status_code == 200 and got.get_json()["dictation_id"] == done["dictation_id"]
    # outcome of the agent
    outcome = _json(env, "post", "/outcomes", {"intent_id": intent["intent_id"], "ingest_id": ingest_id,
                                               "outcome": "stored", "detail": "ok"})
    assert outcome.status_code == 200 and outcome.get_json() == {"recorded": True, "violation": False}
    # receipt carries every field HttpImportServer.ReadReceipt reads
    assert {"ingest_id", "state", "received_chunks", "received_bytes", "sha256", "dictation_id", "durability",
            "decode_state", "completion", "author_state", "correlation_id", "agent_id", "intent_id",
            "source_id", "snapshot_version", "size", "original_id", "permitted_post_step",
            "deletion_permitted"} <= set(done)


def test_reused_idempotency_key_with_other_content_is_409(env):
    intent = _prepare(env)
    assert _begin(env, intent).status_code == 201
    other = _json(env, "post", "/ingests", {"intent_id": intent["intent_id"], "declared_size": len(DATA) + 1,
                                            "declared_sha256": _sha(DATA)},
                  headers=_headers(**{"Idempotency-Key": "idem-key-0001"}))
    assert other.status_code == 409 and other.get_json()["code"] == "conflict"


def test_begin_needs_an_idempotency_key(env):
    intent = _prepare(env)
    response = _json(env, "post", "/ingests", {"intent_id": intent["intent_id"], "declared_size": len(DATA),
                                               "declared_sha256": _sha(DATA)})
    assert response.status_code == 400


def test_abort_returns_the_aborted_receipt(env):
    intent = _prepare(env)
    ingest_id = _begin(env, intent).get_json()["ingest_id"]
    response = env["client"].post("%s/ingests/%s/abort" % (routes_agent.PREFIX, ingest_id), headers=_headers())
    assert response.status_code == 200 and response.get_json()["state"] == "ABORTED"


def test_ingest_id_must_be_a_canonical_guid_and_belong_to_the_agent(env):
    assert env["client"].get(routes_agent.PREFIX + "/ingests/not-a-guid", headers=_headers()).status_code == 404
    assert env["client"].get(routes_agent.PREFIX + "/ingests/" + _GUID, headers=_headers()).status_code == 404
    upper = _GUID.upper()
    assert env["client"].get(routes_agent.PREFIX + "/ingests/" + upper, headers=_headers()).status_code == 404


def test_chunk_validation(env):
    intent = _prepare(env)
    ingest_id = _begin(env, intent).get_json()["ingest_id"]
    path = "%s/ingests/%s/chunks/0" % (routes_agent.PREFIX, ingest_id)
    part = DATA[:CHUNK]
    bad_offset = env["client"].put(path, data=part, content_type="application/octet-stream",
                                   headers=_headers(**{"X-Chunk-SHA256": _sha(part), "X-Chunk-Offset": "-1"}))
    assert bad_offset.status_code == 400
    bad_hash = env["client"].put(path, data=part, content_type="application/octet-stream",
                                 headers=_headers(**{"X-Chunk-SHA256": "0" * 64, "X-Chunk-Offset": "0"}))
    assert bad_hash.status_code == 400 and bad_hash.get_json()["code"] == "invalid"
    no_hash = env["client"].put(path, data=part, content_type="application/octet-stream",
                                headers=_headers(**{"X-Chunk-Offset": "0"}))
    assert no_hash.status_code == 400


# --- edge limits -------------------------------------------------------------------------------

def test_oversized_chunk_is_413_and_wrong_chunk_content_type_is_415(env):
    intent = _prepare(env)
    ingest_id = _begin(env, intent).get_json()["ingest_id"]
    path = "%s/ingests/%s/chunks/0" % (routes_agent.PREFIX, ingest_id)
    big = b"x" * (CHUNK + 1)
    over = env["client"].put(path, data=big, content_type="application/octet-stream",
                             headers=_headers(**{"X-Chunk-SHA256": _sha(big), "X-Chunk-Offset": "0"}))
    assert over.status_code == 413
    assert over.get_json()["code"] == "payload_too_large"
    # without a declared length the body is still bounded while reading
    streamed = env["client"].put(path, input_stream=io.BytesIO(big),
                                 content_type="application/octet-stream",
                                 headers=_headers(**{"X-Chunk-SHA256": _sha(big), "X-Chunk-Offset": "0"}))
    assert streamed.status_code == 413
    part = DATA[:CHUNK]
    wrong = env["client"].put(path, data=part, content_type="application/json",
                              headers=_headers(**{"X-Chunk-SHA256": _sha(part), "X-Chunk-Offset": "0"}))
    assert wrong.status_code == 415
    assert wrong.get_json()["code"] == "unsupported_media_type"
    none = env["client"].put(path, data=part, headers=_headers(**{"X-Chunk-SHA256": _sha(part), "X-Chunk-Offset": "0"}))
    assert none.status_code == 415


def test_json_routes_reject_a_wrong_content_type_and_an_oversized_body(env, monkeypatch):
    for method, path in (("put", "/inventory"), ("post", "/ingests"), ("post", "/outcomes"),
                         ("post", "/ingests/%s/finalize" % _GUID)):
        response = _json(env, method, path, {"items": []}, content_type="text/plain")
        assert response.status_code == 415, path
        assert response.get_json()["code"] == "unsupported_media_type"
    monkeypatch.setattr(routes_agent, "MAX_JSON_BYTES", 200)
    response = _json(env, "put", "/inventory", {"items": [_item() for _ in range(10)]})
    assert response.status_code == 413


def test_declared_size_above_the_policy_is_refused_by_the_service(env):
    intent = _prepare(env)
    response = _json(env, "post", "/ingests", {"intent_id": intent["intent_id"], "declared_size": 10 ** 12,
                                               "declared_sha256": _sha(DATA)},
                     headers=_headers(**{"Idempotency-Key": "idem-key-0009"}))
    assert response.status_code == 409  # differs from the reported snapshot size


# --- logging -----------------------------------------------------------------------------------

def test_no_audio_bytes_or_bodies_in_the_log(env, caplog):
    data = MARKER * 50
    intent = _prepare(env, data=data, snapshot="v7")
    ingest_id = _begin(env, intent, data=data, key="idem-key-0007").get_json()["ingest_id"]
    path = "%s/ingests/%s/chunks/0" % (routes_agent.PREFIX, ingest_id)
    with caplog.at_level(logging.DEBUG):
        bad = env["client"].put(path, data=data, content_type="application/octet-stream",
                                headers=_headers(**{"X-Chunk-SHA256": "0" * 64, "X-Chunk-Offset": "0"}))
        assert bad.status_code == 400
        assert _json(env, "post", "/outcomes", {"intent_id": "x"}).status_code == 400
    assert MARKER.decode() not in caplog.text
    assert MARKER.decode() not in bad.get_data(as_text=True)
