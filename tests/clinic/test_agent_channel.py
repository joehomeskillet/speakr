"""Import-agent channel: identity, inventory, signed intents, resumable ingest, outcome audit.

The original store is a fake callable; wiring to the real ingest module is not
exercised here. Nothing in this file is evidence for a real agent or device.
"""

import hashlib
import inspect
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from src.app import app, db
from src.clinic import agent_channel as ch
from src.clinic import agent_channel_ingest as ing
from src.clinic.agent_channel import AgentChannelError, ChannelPolicy
from src.models import User

NOW = datetime(2026, 11, 17, 8, 0, 0, tzinfo=timezone.utc)
SECRET = b"s" * 32
DATA = b"synthetic dictation bytes " * 400  # 10400 bytes, three chunks of 4096


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _policy(**kwargs):
    return ChannelPolicy(master_secret=SECRET, authorise=lambda user, action: True, **kwargs)


class FakeStore:
    def __init__(self, fail=False, dictation_id="d-1"):
        self.calls, self.fail, self.dictation_id = [], fail, dictation_id

    def __call__(self, stream, **kwargs):
        if self.fail:
            raise RuntimeError("store down")
        self.calls.append({**kwargs, "bytes": stream.read()})
        return {"original_id": "orig-1", "dictation_id": self.dictation_id}


@pytest.fixture(autouse=True)
def _env():
    with app.app_context():
        ch.create_tables()
        yield


@pytest.fixture
def user():
    row = User(username="ac" + uuid.uuid4().hex[:8], email=uuid.uuid4().hex[:8] + "@example.invalid", password="x")
    db.session.add(row)
    db.session.commit()
    return row


def _register(policy, user, fingerprint="aa" * 20, roots=("usb-1",)):
    reg = ch.register_agent(policy, actor_user_id=user.id, display_name="Agent", allowed_sources=list(roots),
                            credential_fingerprint=fingerprint)
    return reg, ch.authenticate_agent(fingerprint)


def _item(data=DATA, snapshot="v1", root="usb-1", source="src-1"):
    return {"source_id": source, "root_id": root, "relative_path": "a/b.dss", "size": len(data),
            "mtime_utc": "2026-11-17T07:00:00.1234567+00:00", "sha256": _sha(data), "snapshot_version": snapshot}


@pytest.fixture
def setup(user):
    policy = _policy()
    reg, principal = _register(policy, user)
    ch.report_inventory(principal, policy, {"items": [_item()]}, now=NOW)
    return policy, reg, principal, user


def _intent(setup, post_step="none", policy=None):
    base, reg, principal, user = setup
    return ch.create_intent(policy or base, actor_user_id=user.id, agent_id=reg["agent_id"], source_id="src-1",
                            snapshot_version="v1", post_step=post_step, now=NOW)


def _begin(principal, policy, intent, key="idem-key-0001", data=DATA):
    return ing.begin_ingest(principal, policy, intent_id=intent["intent_id"], declared_size=len(data),
                            declared_sha256=_sha(data), idempotency_key=key, now=NOW)


def _chunks(data, size=4096):
    return [(i // size, i, data[i:i + size]) for i in range(0, len(data), size)]


def _put(principal, policy, ingest_id, seq, off, chunk, **kw):
    return ing.put_chunk(principal, policy, ingest_id=ingest_id, sequence=seq, offset=off, data=chunk,
                         chunk_sha256=_sha(chunk), now=NOW, **kw)


def _body(data=DATA):
    return {"chunks": [{"sequence": s, "bytes": len(c), "sha256": _sha(c)} for s, _, c in _chunks(data)],
            "total_bytes": len(data), "sha256": _sha(data), "source_finished_reading": True}


def _upload(principal, policy, intent, store, data=DATA):
    ingest_id = _begin(principal, policy, intent, data=data)["ingest_id"]
    for seq, off, chunk in _chunks(data):
        _put(principal, policy, ingest_id, seq, off, chunk)
    return ingest_id, ing.finalize_ingest(principal, policy, ingest_id=ingest_id, body=_body(data),
                                          store_original=store, now=NOW)


def _code(excinfo):
    return excinfo.value.code


# --- identity ------------------------------------------------------------------

def test_identity_only_from_transport_credential(setup, user):
    policy, reg, principal, _ = setup
    other_reg, other = _register(policy, user, fingerprint="bb" * 20, roots=("usb-2",))
    forged = {"agent_id": other_reg["agent_id"], "items": [_item(snapshot="v9")]}
    ch.report_inventory(principal, policy, forged, now=NOW)
    mine = ch.list_inventory(policy, actor_user_id=user.id, agent_id=reg["agent_id"])
    theirs = ch.list_inventory(policy, actor_user_id=user.id, agent_id=other_reg["agent_id"])
    assert {r["snapshot_version"] for r in mine} == {"v1", "v9"}
    assert theirs == []


def test_unknown_and_disabled_credentials_are_refused(setup, user):
    policy, reg, principal, _ = setup
    with pytest.raises(AgentChannelError) as unknown:
        ch.authenticate_agent("cc" * 20)
    assert _code(unknown) == "forbidden"
    ch.set_agent_enabled(policy, actor_user_id=user.id, agent_id=reg["agent_id"], enabled=False)
    with pytest.raises(AgentChannelError):
        ch.authenticate_agent("aa" * 20)
    with pytest.raises(AgentChannelError):  # an existing principal loses access at once
        ch.poll_intents(principal, policy, now=NOW)


def test_forged_principal_is_refused(setup):
    policy, reg, _, _ = setup
    fake = ch.AgentPrincipal(reg["agent_id"], "x")
    with pytest.raises(AgentChannelError):
        ch.poll_intents(fake, policy, now=NOW)
    with pytest.raises(AgentChannelError):
        ch.poll_intents("agent-id-string", policy, now=NOW)


def test_web_functions_deny_without_authorisation(user):
    policy = ChannelPolicy(master_secret=SECRET)
    with pytest.raises(AgentChannelError) as err:
        ch.register_agent(policy, actor_user_id=user.id, display_name="A", allowed_sources=["r"],
                          credential_fingerprint="dd" * 20)
    assert _code(err) == "forbidden"


# --- inventory ---------------------------------------------------------------------

def test_inventory_outside_registered_sources_refused_atomically(setup, user):
    policy, reg, principal, _ = setup
    body = {"items": [_item(snapshot="v2"), _item(snapshot="v3", root="other-root", source="src-2")]}
    with pytest.raises(AgentChannelError) as err:
        ch.report_inventory(principal, policy, body, now=NOW)
    assert _code(err) == "forbidden"
    versions = {r["snapshot_version"] for r in ch.list_inventory(policy, actor_user_id=user.id, agent_id=reg["agent_id"])}
    assert versions == {"v1"}


@pytest.mark.parametrize("path", ["../x.dss", "/abs/x.dss", "C:\\x.dss", "a/../../x"])
def test_inventory_path_escape_refused(setup, path):
    policy, _, principal, _ = setup
    item = {**_item(snapshot="v5"), "relative_path": path}
    with pytest.raises(AgentChannelError):
        ch.report_inventory(principal, policy, {"items": [item]}, now=NOW)


def test_inventory_grants_no_rights_and_creates_no_intent(setup):
    policy, _, principal, _ = setup
    assert ch.poll_intents(principal, policy, now=NOW) == {"intents": []}


# --- intents -----------------------------------------------------------------------------

def test_intent_signature_verifies_with_documented_canonical_form(setup):
    policy, reg, principal, _ = setup
    wire = _intent(setup)
    key = ch.derive_agent_key(policy, reg["agent_id"], 1)
    assert bytes.fromhex(reg["intent_signing_key_hex"]) == key
    text = "\n".join(["speakr-import-intent-v2", wire["intent_id"], "import_snapshot", reg["agent_id"], "src-1",
                      "v1", "none", wire["expires_at"]])
    import hmac as _hmac
    assert wire["signature"] == _hmac.new(key, text.encode(), hashlib.sha256).hexdigest()
    assert ch.verify_intent_wire(key, wire, expected_agent_id=reg["agent_id"], now=NOW)
    assert ch.poll_intents(principal, policy, now=NOW)["intents"] == [wire]


@pytest.mark.parametrize("field,value", [
    ("intent_id", "other"), ("kind", "x"), ("agent_id", "someone"), ("source_id", "src-9"),
    ("snapshot_version", "v2"), ("post_step", "delete"), ("expires_at", "2030-01-01T00:00:00Z"),
])
def test_signature_fails_after_any_field_change(setup, field, value):
    policy, reg, _, _ = setup
    wire = _intent(setup)
    key = ch.derive_agent_key(policy, reg["agent_id"], 1)
    assert not ch.verify_intent_wire(key, {**wire, field: value}, expected_agent_id=wire["agent_id"], now=NOW)


def test_expired_intent(setup):
    policy, reg, principal, _ = setup
    wire = _intent(setup)
    later = NOW + policy.intent_ttl + timedelta(seconds=1)
    key = ch.derive_agent_key(policy, reg["agent_id"], 1)
    assert not ch.verify_intent_wire(key, wire, expected_agent_id=reg["agent_id"], now=later)
    assert ch.poll_intents(principal, policy, now=later) == {"intents": []}
    with pytest.raises(AgentChannelError) as err:
        ing.begin_ingest(principal, policy, intent_id=wire["intent_id"], declared_size=len(DATA),
                         declared_sha256=_sha(DATA), idempotency_key="idem-key-0001", now=later)
    assert _code(err) == "expired"


def test_intent_for_another_agent_refused(setup, user):
    policy, reg, principal, _ = setup
    wire = _intent(setup)
    _, other = _register(policy, user, fingerprint="bb" * 20, roots=("usb-1",))
    assert ch.poll_intents(other, policy, now=NOW) == {"intents": []}
    with pytest.raises(AgentChannelError) as err:
        _begin(other, policy, wire)
    assert _code(err) == "forbidden"
    with pytest.raises(AgentChannelError) as err:
        ing.record_outcome(other, intent_id=wire["intent_id"], outcome="refused", now=NOW)
    assert _code(err) == "forbidden"
    other_key = ch.derive_agent_key(policy, "someone-else", 1)
    assert not ch.verify_intent_wire(other_key, wire, expected_agent_id="someone-else", now=NOW)


def test_intent_needs_reported_snapshot_and_policy_for_post_step(setup, user):
    policy, reg, _, _ = setup
    with pytest.raises(AgentChannelError) as err:
        ch.create_intent(policy, actor_user_id=user.id, agent_id=reg["agent_id"], source_id="src-1",
                         snapshot_version="nope", now=NOW)
    assert _code(err) == "not_found"
    with pytest.raises(AgentChannelError) as err:
        _intent(setup, post_step="delete")  # default policy: post-import step disabled
    assert _code(err) == "forbidden"
    assert _intent(setup, post_step="delete", policy=_policy(allow_post_import=True))["post_step"] == "delete"


def test_signing_key_rotation_cancels_open_intents(setup, user):
    policy, reg, principal, _ = setup
    _intent(setup)
    new = ch.rotate_signing_key(policy, actor_user_id=user.id, agent_id=reg["agent_id"])
    assert new["intent_signing_key_hex"] != reg["intent_signing_key_hex"]
    assert ch.poll_intents(principal, policy, now=NOW) == {"intents": []}


# --- resumable ingest ----------------------------------------------------------------------

def test_upload_with_interruption_and_resume(setup):
    policy, _, principal, _ = setup
    intent = _intent(setup)
    store = FakeStore()
    first = _begin(principal, policy, intent)
    parts = _chunks(DATA)
    _put(principal, policy, first["ingest_id"], *parts[0])
    # connection lost; the agent repeats begin with the same key and gets the same session and state
    resumed = _begin(principal, policy, intent)
    assert resumed["ingest_id"] == first["ingest_id"] and resumed["received_chunks"] == [0]
    for part in parts[1:]:
        _put(principal, policy, first["ingest_id"], *part)
    receipt = ing.finalize_ingest(principal, policy, ingest_id=first["ingest_id"], body=_body(), store_original=store, now=NOW)
    assert receipt["state"] == "STORED" and receipt["durability"] == "STORED"
    assert len(store.calls) == 1 and store.calls[0]["bytes"] == DATA
    assert store.calls[0]["author_user_id"] is None and store.calls[0]["origin"] == "agent"
    for name, expected in (("source_id", "src-1"), ("snapshot_version", "v1"), ("sha256", _sha(DATA)),
                           ("size", len(DATA)), ("original_id", "orig-1"), ("ingest_id", first["ingest_id"])):
        assert receipt[name] == expected


def test_wrong_chunk_offset_refused(setup):
    policy, _, principal, _ = setup
    ingest_id = _begin(principal, policy, _intent(setup))["ingest_id"]
    parts = _chunks(DATA)
    with pytest.raises(AgentChannelError) as err:  # offset too large
        _put(principal, policy, ingest_id, 0, 10, parts[0][2])
    assert _code(err) == "conflict"
    with pytest.raises(AgentChannelError):  # gap: chunk 1 before chunk 0
        _put(principal, policy, ingest_id, 1, 4096, parts[1][2])
    _put(principal, policy, ingest_id, *parts[0])
    with pytest.raises(AgentChannelError):  # same sequence, other content
        _put(principal, policy, ingest_id, 0, 0, b"x" * 4096)
    with pytest.raises(AgentChannelError):  # chunk hash does not match the bytes
        ing.put_chunk(principal, policy, ingest_id=ingest_id, sequence=1, offset=4096, data=parts[1][2],
                      chunk_sha256="0" * 64, now=NOW)


def test_chunk_limits(setup):
    policy, _, principal, _ = setup
    ingest_id = _begin(principal, policy, _intent(setup))["ingest_id"]
    small = _policy(max_chunk_bytes=100)
    with pytest.raises(AgentChannelError):
        _put(principal, small, ingest_id, 0, 0, DATA[:101])
    with pytest.raises(AgentChannelError):
        _put(principal, _policy(max_chunks=1), ingest_id, 1, 0, DATA[:10])


def test_wrong_final_hash_refused(setup):
    policy, _, principal, _ = setup
    ingest_id = _begin(principal, policy, _intent(setup))["ingest_id"]
    for part in _chunks(DATA):
        _put(principal, policy, ingest_id, *part)
    store = FakeStore()
    with pytest.raises(AgentChannelError) as err:
        ing.finalize_ingest(principal, policy, ingest_id=ingest_id, body={**_body(), "sha256": _sha(b"other")},
                            store_original=store, now=NOW)
    assert _code(err) == "invalid" and store.calls == []
    assert ing.get_receipt(principal, ingest_id=ingest_id)["state"] == "RECEIVING"


def test_corrupt_assembled_bytes_become_invalid_not_stored(setup):
    policy, _, principal, _ = setup
    bad = b"y" * len(DATA)
    intent = _intent(setup)
    ingest_id = _begin(principal, policy, intent)["ingest_id"]
    for seq, off, chunk in _chunks(bad):  # bytes differ from the declared hash, chunk hashes are self-consistent
        _put(principal, policy, ingest_id, seq, off, chunk)
    body = {**_body(bad), "sha256": _sha(DATA)}
    store = FakeStore()
    receipt = ing.finalize_ingest(principal, policy, ingest_id=ingest_id, body=body, store_original=store, now=NOW)
    assert receipt["state"] == "INVALID" and receipt["sha256"] is None and store.calls == []


def test_replay_returns_same_receipt_and_stores_once(setup):
    policy, _, principal, _ = setup
    intent = _intent(setup)
    store = FakeStore()
    ingest_id, receipt = _upload(principal, policy, intent, store)
    again = ing.finalize_ingest(principal, policy, ingest_id=ingest_id, body=_body(), store_original=store, now=NOW)
    assert again == receipt and len(store.calls) == 1
    assert _begin(principal, policy, intent) == receipt
    assert ing.get_receipt(principal, ingest_id=ingest_id) == receipt
    with pytest.raises(AgentChannelError):  # same key, different content
        _begin(principal, policy, intent, data=b"z" * 10)


def test_unknown_is_never_stored_and_can_be_resolved_by_retry(setup):
    policy, _, principal, _ = setup
    intent = _intent(setup)
    ingest_id = _begin(principal, policy, intent)["ingest_id"]
    for part in _chunks(DATA):
        _put(principal, policy, ingest_id, *part)
    receipt = ing.finalize_ingest(principal, policy, ingest_id=ingest_id, body=_body(),
                                  store_original=FakeStore(fail=True), now=NOW)
    assert receipt["state"] == "UNKNOWN" and receipt["durability"] == "RECEIVED"
    assert receipt["original_id"] is None and receipt["sha256"] is None and not receipt["deletion_permitted"]
    with pytest.raises(AgentChannelError):  # UNKNOWN cannot be aborted: the bytes may be durable
        ing.abort_ingest(principal, ingest_id=ingest_id)
    with pytest.raises(AgentChannelError):  # and takes no more chunks
        _put(principal, policy, ingest_id, 0, 0, DATA[:4096])
    empty = ing.finalize_ingest(principal, policy, ingest_id=ingest_id, body=_body(),
                                store_original=lambda stream, **kw: {}, now=NOW)
    assert empty["state"] == "UNKNOWN"
    assert ing.finalize_ingest(principal, policy, ingest_id=ingest_id, body=_body(),
                               store_original=FakeStore(), now=NOW)["state"] == "STORED"


def test_receipt_does_not_permit_removal_by_default(setup):
    policy, _, principal, _ = setup
    _, receipt = _upload(principal, policy, _intent(setup), FakeStore())
    assert receipt["state"] == "STORED"
    assert receipt["deletion_permitted"] is False and receipt["permitted_post_step"] == "none"


def test_removal_needs_policy_intent_and_dictation_id(setup):
    base, reg, principal, user = setup
    on = _policy(allow_post_import=True)
    _, receipt = _upload(principal, on, _intent(setup, "delete", on), FakeStore())
    assert receipt["deletion_permitted"] is True
    # STORED without a dictation id: the source stays (safe default)
    ch.report_inventory(principal, on, {"items": [_item(snapshot="v2")]}, now=NOW)
    wire = ch.create_intent(on, actor_user_id=user.id, agent_id=reg["agent_id"], source_id="src-1",
                            snapshot_version="v2", post_step="delete", now=NOW)
    ingest_id = ing.begin_ingest(principal, on, intent_id=wire["intent_id"], declared_size=len(DATA),
                                 declared_sha256=_sha(DATA), idempotency_key="idem-key-0099", now=NOW)["ingest_id"]
    for part in _chunks(DATA):
        _put(principal, on, ingest_id, *part)
    no_dictation = ing.finalize_ingest(principal, on, ingest_id=ingest_id, body=_body(),
                                       store_original=FakeStore(dictation_id=None), now=NOW)
    assert no_dictation["state"] == "STORED" and no_dictation["deletion_permitted"] is False


def test_abort_returns_intent_to_open(setup):
    policy, _, principal, _ = setup
    intent = _intent(setup)
    ingest_id = _begin(principal, policy, intent)["ingest_id"]
    _put(principal, policy, ingest_id, *_chunks(DATA)[0])
    assert ing.abort_ingest(principal, ingest_id=ingest_id)["state"] == "ABORTED"
    assert ing.abort_ingest(principal, ingest_id=ingest_id)["state"] == "ABORTED"
    with pytest.raises(AgentChannelError):
        _put(principal, policy, ingest_id, *_chunks(DATA)[1])
    assert ch.poll_intents(principal, policy, now=NOW)["intents"][0]["intent_id"] == intent["intent_id"]
    assert _begin(principal, policy, intent, key="idem-key-0002")["state"] == "RECEIVING"


def test_session_expiry(setup):
    policy, _, principal, _ = setup
    ingest_id = _begin(principal, policy, _intent(setup))["ingest_id"]
    with pytest.raises(AgentChannelError) as err:
        ing.put_chunk(principal, policy, ingest_id=ingest_id, sequence=0, offset=0, data=DATA[:10],
                      chunk_sha256=_sha(DATA[:10]), now=NOW + policy.session_ttl + timedelta(seconds=1))
    assert _code(err) == "expired"


def test_begin_must_match_reported_snapshot(setup):
    policy, _, principal, _ = setup
    intent = _intent(setup)
    with pytest.raises(AgentChannelError) as err:
        ing.begin_ingest(principal, policy, intent_id=intent["intent_id"], declared_size=len(DATA) + 1,
                         declared_sha256=_sha(DATA), idempotency_key="idem-key-0001", now=NOW)
    assert _code(err) == "conflict"


def test_other_agent_cannot_touch_session(setup, user):
    policy, _, principal, _ = setup
    ingest_id = _begin(principal, policy, _intent(setup))["ingest_id"]
    _, other = _register(policy, user, fingerprint="bb" * 20)
    for call in (lambda: ing.get_receipt(other, ingest_id=ingest_id),
                 lambda: ing.abort_ingest(other, ingest_id=ingest_id),
                 lambda: _put(other, policy, ingest_id, *_chunks(DATA)[0])):
        with pytest.raises(AgentChannelError) as err:
            call()
        assert _code(err) == "not_found"


# --- outcome audit -----------------------------------------------------------------------------

def test_deleted_without_permission_is_flagged(setup, user):
    policy, _, principal, _ = setup
    intent = _intent(setup)
    ingest_id, receipt = _upload(principal, policy, intent, FakeStore())
    assert receipt["deletion_permitted"] is False
    result = ing.record_outcome(principal, intent_id=intent["intent_id"], outcome="deleted",
                                ingest_id=ingest_id, detail="gone\nforged line", now=NOW)
    assert result == {"recorded": True, "violation": True}
    rows = ing.list_outcomes(policy, actor_user_id=user.id, violations_only=True)
    assert len(rows) == 1 and rows[0]["violation_reason"] == "deletion not permitted by the receipt"


def test_deleted_without_any_receipt_is_flagged_and_permitted_delete_is_not(setup, user):
    base, _, principal, _ = setup
    on = _policy(allow_post_import=True)
    blind = _intent(setup, "delete", on)
    assert ing.record_outcome(principal, intent_id=blind["intent_id"], outcome="deleted", now=NOW)["violation"]
    ch.ClinicAgentIntent.query.filter_by(id=blind["intent_id"]).update({"status": "CANCELLED"})
    db.session.commit()
    wire = _intent(setup, "delete", on)
    ingest_id, _ = _upload(principal, on, wire, FakeStore())
    clean = ing.record_outcome(principal, intent_id=wire["intent_id"], outcome="deleted", ingest_id=ingest_id, now=NOW)
    assert clean["violation"] is False
    assert len(ing.list_outcomes(on, actor_user_id=user.id)) == 2


def test_stored_outcome_without_receipt_and_move_without_permission(setup):
    policy, _, principal, _ = setup
    intent = _intent(setup)
    ingest_id, _ = _upload(principal, policy, intent, FakeStore())
    assert ing.record_outcome(principal, intent_id=intent["intent_id"], outcome="stored", now=NOW)["violation"]
    assert ing.record_outcome(principal, intent_id=intent["intent_id"], outcome="moved",
                              ingest_id=ingest_id, now=NOW)["violation"]
    assert not ing.record_outcome(principal, intent_id=intent["intent_id"], outcome="conflict", now=NOW)["violation"]
    with pytest.raises(AgentChannelError):
        ing.record_outcome(principal, intent_id=intent["intent_id"], outcome="vanished", now=NOW)


def test_outcome_log_is_append_only_by_api():
    names = {n for n, f in inspect.getmembers(ing, inspect.isfunction) if f.__module__ == ing.__name__}
    assert not [n for n in names if n.startswith(("update_outcome", "delete_outcome", "edit_outcome"))]


# --- agent has no dictation read ---------------------------------------------------------------

def test_agent_principal_has_no_dictation_read_function(setup):
    policy, _, principal, _ = setup
    public = [(m.__name__, n, f) for m in (ch, ing) for n, f in inspect.getmembers(m, inspect.isfunction)
              if f.__module__ == m.__name__ and not n.startswith("_")]
    forbidden = ("dictation", "audio", "transcript", "text", "revision", "download", "media")
    assert not [n for _, n, _ in public if any(word in n for word in forbidden)]
    # every function an agent can call returns only ids, hashes and states; none yields stored bytes or text
    _, receipt = _upload(principal, policy, _intent(setup), FakeStore())
    assert not any(isinstance(v, (bytes, bytearray)) for v in receipt.values())
    # an agent principal is not accepted where a user is expected
    with pytest.raises(AgentChannelError):
        ch.register_agent(ChannelPolicy(master_secret=SECRET, authorise=lambda u, a: u == 1), actor_user_id=principal,
                          display_name="x", allowed_sources=["r"], credential_fingerprint="ff" * 20)
