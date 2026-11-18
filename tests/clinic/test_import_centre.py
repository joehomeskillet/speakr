"""Import centre: user intents, delivery matching, author hand-over, language default.

Real SQLite database and real temporary files; the original store is the real IngestService.
Nothing here is evidence for a device, a real agent or the foot-pedal/KIS side.
"""

import hashlib
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from src.app import app, db
from src.clinic import agent_channel as ch
from src.clinic import agent_channel_ingest as ing
from src.clinic import import_centre as ic
from src.clinic.agent_channel import AgentChannelError, ChannelPolicy
from src.clinic.ingest import IngestService, storage_key_for
from src.clinic.models import ClinicDictation, ClinicOriginal
from src.models import User

NOW = datetime(2026, 11, 18, 8, 0, 0, tzinfo=timezone.utc)
SECRET = b"s" * 32
_counter = iter(range(1, 10_000))


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _policy():
    return ChannelPolicy(master_secret=SECRET, authorise=lambda user, action: True)


@pytest.fixture(autouse=True)
def _env():
    with app.app_context():
        ch.create_tables()
        ic.create_tables()
        yield


def _user(prefix="ic"):
    row = User(username=prefix + uuid.uuid4().hex[:8], email=uuid.uuid4().hex[:8] + "@example.invalid", password="x")
    db.session.add(row)
    db.session.commit()
    return row


@pytest.fixture
def env(tmp_path):
    policy = _policy()
    admin, author, other = _user(), _user("au"), _user("ot")
    reg = ch.register_agent(policy, actor_user_id=admin.id, display_name="Agent",
                            allowed_sources=["usb-1", "usb-2"], credential_fingerprint="aa" * 20)
    principal = ch.authenticate_agent("aa" * 20)
    return {"policy": policy, "admin": admin, "author": author, "other": other, "agent_id": reg["agent_id"],
            "principal": principal, "service": IngestService(tmp_path / "store")}


def _intent(env, **kwargs):
    base = dict(actor_user_id=env["author"].id, source_kind="device", source_ref="usb-1", now=NOW)
    base.update(kwargs)
    return ic.create_import_intent(**base)


def _upload(env, data, *, root="usb-1", name="a/dict1.ds2"):
    """Agent side up to (not including) finalize. Returns (ingest_id, finalize body, idempotency key)."""
    n = next(_counter)
    snapshot, key = "v%d" % n, "idem-key-%04d" % n
    item = {"source_id": "src-%d" % n, "root_id": root, "relative_path": name, "size": len(data),
            "mtime_utc": "2026-11-18T07:00:00+00:00", "sha256": _sha(data), "snapshot_version": snapshot}
    ch.report_inventory(env["principal"], env["policy"], {"items": [item]}, now=NOW)
    ch.create_intent(env["policy"], actor_user_id=env["admin"].id, agent_id=env["agent_id"],
                     source_id=item["source_id"], snapshot_version=snapshot, now=NOW)
    wire = ch.poll_intents(env["principal"], env["policy"], now=NOW)["intents"]
    intent = next(w for w in wire if w["source_id"] == item["source_id"])
    receipt = ing.begin_ingest(env["principal"], env["policy"], intent_id=intent["intent_id"],
                               declared_size=len(data), declared_sha256=_sha(data), idempotency_key=key, now=NOW)
    ing.put_chunk(env["principal"], env["policy"], ingest_id=receipt["ingest_id"], sequence=0, offset=0,
                  data=data, chunk_sha256=_sha(data), now=NOW)
    body = {"chunks": [{"sequence": 0, "bytes": len(data), "sha256": _sha(data)}],
            "total_bytes": len(data), "sha256": _sha(data), "source_finished_reading": True}
    return receipt["ingest_id"], body, key


def _finalize(env, ingest_id, body, now=NOW):
    return ic.finalize_delivery(env["principal"], env["policy"], ingest_id=ingest_id, body=body,
                                ingest_service=env["service"], now=now)


def _data(tag):
    return ("synthetic dictation %s " % tag).encode() * 50


# --- intent lifecycle ---------------------------------------------------------------------

def test_intent_defaults_and_listing(env):
    view = _intent(env, expected_files=["A/Dict1.DS2", "dict1.ds2", "dict2.ds2"])
    assert view["status"] == "OPEN"
    assert view["language_id"] == 1031
    assert view["target_author_user_id"] == env["author"].id
    assert view["expected_files"] == ["dict1.ds2", "dict2.ds2"]
    assert view["expires_at"] == (NOW + timedelta(hours=24)).isoformat()
    assert [i["id"] for i in ic.list_import_intents(actor_user_id=env["author"].id, now=NOW)] == [view["id"]]
    assert ic.list_import_intents(actor_user_id=env["other"].id, now=NOW) == []


def test_intent_validation_and_foreign_author(env):
    for bad in (dict(source_kind="cloud"), dict(source_ref="a b"), dict(expected_files=[""]),
                dict(language_id=0), dict(language_id=True)):
        with pytest.raises(AgentChannelError) as err:
            _intent(env, **bad)
        assert err.value.code == "invalid"
    with pytest.raises(AgentChannelError) as err:
        _intent(env, target_author_user_id=env["other"].id)
    assert err.value.code == "forbidden"
    allowed = _intent(env, target_author_user_id=env["other"].id, may_assign_author=lambda actor, target: True)
    assert allowed["target_author_user_id"] == env["other"].id


def test_cancel_only_by_owner_and_only_open(env):
    view = _intent(env)
    with pytest.raises(AgentChannelError) as err:
        ic.cancel_import_intent(actor_user_id=env["other"].id, intent_id=view["id"])
    assert err.value.code == "not_found"
    assert ic.cancel_import_intent(actor_user_id=env["author"].id, intent_id=view["id"]) == "CANCELLED"
    assert ic.cancel_import_intent(actor_user_id=env["author"].id, intent_id=view["id"]) == "CANCELLED"


def test_intent_expires_after_24_hours(env):
    view = _intent(env)
    assert ic.expire_intents(now=NOW + timedelta(hours=23, minutes=59)) == 0
    assert ic.list_import_intents(actor_user_id=env["author"].id, now=NOW + timedelta(hours=23, minutes=59))[0][
        "status"] == "OPEN"
    assert ic.expire_intents(now=NOW + timedelta(hours=24)) == 1
    listed = ic.list_import_intents(actor_user_id=env["author"].id, status="EXPIRED", now=NOW + timedelta(hours=25))
    assert [i["id"] for i in listed] == [view["id"]]


# --- matching and author hand-over ----------------------------------------------------------

def test_matched_delivery_is_stored_for_the_intent_author(env):
    view = _intent(env, expected_files=["dict1.ds2"])
    data = _data("one")
    ingest_id, body, key = _upload(env, data, name="Inbox/Dict1.DS2")
    receipt = _finalize(env, ingest_id, body)
    assert receipt["state"] == "STORED"
    assert receipt["import"]["status"] == "MATCHED"
    assert receipt["import"]["intent_id"] == view["id"]
    assert receipt["import"]["language_id"] == 1031
    dictation = db.session.get(ClinicDictation, receipt["dictation_id"])
    assert dictation.author_user_id == env["author"].id
    original = db.session.get(ClinicOriginal, receipt["original_id"])
    assert env["service"].file_path(original.storage_key).read_bytes() == data
    assert ic.list_import_intents(actor_user_id=env["author"].id, now=NOW)[0]["status"] == "FULFILLED"
    assert ic.list_unassigned() == []
    # repeated finalize is idempotent: no second row
    again = _finalize(env, ingest_id, body)
    assert again["dictation_id"] == receipt["dictation_id"]
    assert ClinicOriginal.query.count() == 1


def test_expected_file_list_needs_every_file(env):
    view = _intent(env, expected_files=["dict1.ds2", "dict2.ds2"])
    first = _finalize(env, *_upload(env, _data("a"), name="x/dict1.ds2")[:2])
    assert first["state"] == "STORED"
    assert ic.list_import_intents(actor_user_id=env["author"].id, now=NOW)[0]["status"] == "OPEN"
    stray = _finalize(env, *_upload(env, _data("b"), name="x/other.ds2")[:2])
    assert stray["state"] == "UNKNOWN" and stray["import"]["status"] == "UNASSIGNED"
    second = _finalize(env, *_upload(env, _data("c"), name="x/DICT2.ds2")[:2])
    assert second["state"] == "STORED"
    assert ic.list_import_intents(actor_user_id=env["author"].id, now=NOW)[0]["id"] == view["id"]
    assert ic.list_import_intents(actor_user_id=env["author"].id, status="FULFILLED", now=NOW)[0]["id"] == view["id"]


def test_oldest_matching_intent_wins(env):
    older = _intent(env, now=NOW)
    _intent(env, actor_user_id=env["other"].id, now=NOW + timedelta(minutes=5))
    receipt = _finalize(env, *_upload(env, _data("old"))[:2], now=NOW + timedelta(minutes=10))
    assert receipt["import"]["intent_id"] == older["id"]
    assert db.session.get(ClinicDictation, receipt["dictation_id"]).author_user_id == env["author"].id


def test_language_and_pool_follow_the_intent(env):
    pool = str(uuid.uuid4())
    view = _intent(env, language_id=1033, target_pool_id=pool)
    receipt = _finalize(env, *_upload(env, _data("lang"))[:2])
    assert receipt["import"]["intent_id"] == view["id"]
    assert receipt["import"]["language_id"] == 1033
    assert db.session.get(ClinicDictation, receipt["dictation_id"]).pool_id == pool


def test_language_default_is_german():
    assert ic.DEFAULT_LANG_ID == 1031
    assert ic.resolve_language(None) == 1031
    assert ic.resolve_language(1033) == 1033


# --- no match: clarification list ------------------------------------------------------------

def test_unmatched_delivery_keeps_bytes_and_lands_on_clarification_list(env):
    _intent(env, source_ref="usb-2")  # other source
    data = _data("lost")
    ingest_id, body, key = _upload(env, data, root="usb-1")
    receipt = _finalize(env, ingest_id, body)
    assert receipt["state"] == "UNKNOWN"
    assert receipt["import"]["status"] == "UNASSIGNED" and receipt["import"]["intent_id"] is None
    assert receipt["import"]["language_id"] == 1031
    assert ClinicOriginal.query.count() == 0
    stored = env["service"].file_path(storage_key_for("agent-ingest:%s:%s" % (env["agent_id"], key)))
    assert stored.read_bytes() == data
    listed = ic.list_unassigned()
    assert [d["ingest_id"] for d in listed] == [ingest_id]
    assert listed[0]["relative_path"] == "a/dict1.ds2" and listed[0]["root_id"] == "usb-1"


def test_expired_or_cancelled_intent_does_not_match(env):
    stale = _intent(env, now=NOW - timedelta(hours=25))
    gone = _intent(env)
    ic.cancel_import_intent(actor_user_id=env["author"].id, intent_id=gone["id"])
    receipt = _finalize(env, *_upload(env, _data("late"))[:2])
    assert receipt["import"]["status"] == "UNASSIGNED"
    assert stale["id"] != gone["id"]


def test_unassigned_delivery_can_be_claimed_and_is_healed_by_the_retry(env):
    ingest_id, body, key = _upload(env, _data("claim"))
    first = _finalize(env, ingest_id, body)
    assert first["state"] == "UNKNOWN"
    delivery_id = ic.list_unassigned()[0]["id"]
    view = _intent(env)
    with pytest.raises(AgentChannelError) as err:
        ic.assign_unassigned(actor_user_id=env["other"].id, delivery_id=delivery_id, intent_id=view["id"], now=NOW)
    assert err.value.code == "not_found"
    ic.assign_unassigned(actor_user_id=env["author"].id, delivery_id=delivery_id, intent_id=view["id"], now=NOW)
    assert ic.list_unassigned() == []
    healed = _finalize(env, ingest_id, body)
    assert healed["state"] == "STORED"
    assert healed["import"]["intent_id"] == view["id"]
    assert db.session.get(ClinicDictation, healed["dictation_id"]).author_user_id == env["author"].id
    assert ClinicOriginal.query.count() == 1
    with pytest.raises(AgentChannelError) as err:
        ic.assign_unassigned(actor_user_id=env["author"].id, delivery_id=delivery_id, intent_id=view["id"], now=NOW)
    assert err.value.code == "conflict"


def test_foreign_agent_cannot_finalize_the_session(env):
    ingest_id, body, _ = _upload(env, _data("own"))
    ch.register_agent(env["policy"], actor_user_id=env["admin"].id, display_name="Other",
                      allowed_sources=["usb-9"], credential_fingerprint="bb" * 20)
    stranger = ch.authenticate_agent("bb" * 20)
    with pytest.raises(AgentChannelError) as err:
        ic.finalize_delivery(stranger, env["policy"], ingest_id=ingest_id, body=body,
                             ingest_service=env["service"], now=NOW)
    assert err.value.code == "not_found"
