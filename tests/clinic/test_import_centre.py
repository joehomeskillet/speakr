"""Import centre: user intents, delivery matching, author hand-over, language default.

Real SQLite database and real temporary files; the original store is the real IngestService.
Nothing here is evidence for a device, a real agent or the foot-pedal/KIS side.
"""

import hashlib
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

import pytest
from sqlalchemy.exc import OperationalError

from src.app import app, db
from src.clinic import agent_channel as ch
from src.clinic import agent_channel_ingest as ing
from src.clinic import import_centre as ic
from src.clinic.agent_channel import AgentChannelError, ChannelPolicy
from src.clinic.ingest import IngestError, IngestService, storage_key_for
from src.clinic.models import ClinicDictation, ClinicOriginal
from src.models import Recording, User

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


# --- permanent originals and expired upload sessions ----------------------------------------

def test_unassigned_original_survives_expiry_and_assignment_without_upload_chunks(env):
    _intent(env, source_ref="usb-2")
    data = _data("permanent")
    ingest_id, body, key = _upload(env, data)
    first = _finalize(env, ingest_id, body)
    delivery_id = first["import"]["delivery_id"]
    original = db.session.get(ic.ClinicImportOriginal, delivery_id)
    storage_key = storage_key_for("agent-ingest:%s:%s" % (env["agent_id"], key))
    assert original.storage_key == storage_key
    assert original.content_hash == _sha(data)
    assert original.size_bytes == len(data)
    assert "expires_at" not in original.__table__.columns
    path = env["service"].file_path(storage_key)
    inode = path.stat().st_ino
    session_row = db.session.get(ch.ClinicAgentIngest, ingest_id)
    expires_at = ch.aware(session_row.expires_at)
    late = expires_at + timedelta(days=30)

    assert ic.expire_intents(now=late) == 1
    # Expired temporary upload chunks can be retired independently of the original.
    ch.ClinicAgentChunk.query.filter_by(ingest_id=ingest_id).delete()
    db.session.commit()
    author_id = env["author"].id
    db.session.remove()
    ic.create_tables()
    env["author"] = db.session.get(User, author_id)
    listed = ic.list_unassigned(now=late)
    assert [delivery["id"] for delivery in listed] == [delivery_id]
    assert listed[0]["original_state"] == "author_required"
    assert listed[0]["storage_key"] == storage_key
    assert listed[0]["sha256"] == _sha(data)
    assert listed[0]["size"] == len(data)
    again = _finalize(env, ingest_id, body, now=late)
    assert again["state"] == "UNKNOWN"
    assert again["import"]["delivery_id"] == delivery_id
    assert again["permitted_post_step"] == "none"
    assert again["deletion_permitted"] is False
    assert ic.ClinicImportOriginal.query.count() == 1
    assert ClinicOriginal.query.count() == 0
    assert path.read_bytes() == data
    assert ch.aware(db.session.get(ch.ClinicAgentIngest, ingest_id).expires_at) == expires_at

    pool = str(uuid.uuid4())
    view = _intent(env, now=late, language_id=1033, target_pool_id=pool)
    ic.assign_unassigned(actor_user_id=env["author"].id, delivery_id=delivery_id,
                         intent_id=view["id"], now=late)
    # The web-side assignment creates the full dictation before another agent call.
    stored = ClinicOriginal.query.one()
    dictation = db.session.get(ClinicDictation, stored.dictation_id)
    assert dictation.author_user_id == env["author"].id
    assert dictation.pool_id == pool
    assert stored.storage_key == storage_key
    assert ic.list_unassigned(now=late) == []
    saved_receipt = stored.receipt
    healed = _finalize(env, ingest_id, body, now=late)
    assert healed["state"] == "STORED"
    assert healed["original_id"] == stored.id
    assert healed["dictation_id"] == dictation.id
    assert healed["import"]["language_id"] == 1033
    assert db.session.get(ic.ClinicImportIntent, view["id"]).status == "FULFILLED"

    much_later = late + timedelta(days=365)
    retry = _finalize(env, ingest_id, body, now=much_later)
    session_row = db.session.get(ch.ClinicAgentIngest, ingest_id)
    begun_again = ing.begin_ingest(
        env["principal"], env["policy"], intent_id=session_row.intent_id,
        declared_size=len(data), declared_sha256=_sha(data), idempotency_key=key, now=much_later,
    )
    for receipt in (retry, begun_again):
        assert receipt["ingest_id"] == ingest_id
        assert receipt["original_id"] == healed["original_id"]
        assert receipt["dictation_id"] == healed["dictation_id"]
    assert ClinicOriginal.query.count() == 1
    assert ClinicDictation.query.count() == 1
    assert Recording.query.filter_by(user_id=env["author"].id).count() == 1
    assert ic.ClinicImportOriginal.query.count() == 1
    assert db.session.get(ClinicOriginal, stored.id).receipt == saved_receipt
    assert path.stat().st_ino == inode
    assert path.read_bytes() == data


def test_expired_upload_without_a_durable_original_still_expires(env, monkeypatch):
    ingest_id, body, _ = _upload(env, _data("not-stored"))

    def unavailable(*args, **kwargs):
        raise OSError("synthetic unavailable original store")

    monkeypatch.setattr(env["service"], "store_original", unavailable)
    assert _finalize(env, ingest_id, body)["state"] == "UNKNOWN"
    late = NOW + timedelta(hours=25)
    with pytest.raises(AgentChannelError) as err:
        _finalize(env, ingest_id, body, now=late)
    assert err.value.code == "expired"
    assert ic.ClinicImportOriginal.query.count() == 0
    assert ClinicOriginal.query.count() == 0
    assert list(env["service"].root.rglob("*")) == []


def test_expired_receiving_session_cannot_publish_chunks(env):
    data = _data("expired-upload")
    ingest_id, body, _ = _upload(env, data)
    late = NOW + timedelta(hours=25)
    with pytest.raises(AgentChannelError) as err:
        _finalize(env, ingest_id, body, now=late)
    assert err.value.code == "expired"
    with pytest.raises(AgentChannelError) as err:
        ing.put_chunk(env["principal"], env["policy"], ingest_id=ingest_id,
                      sequence=0, offset=0, data=data, chunk_sha256=_sha(data), now=late)
    assert err.value.code == "expired"
    assert ic.ClinicImportOriginal.query.count() == 0
    assert ClinicOriginal.query.count() == 0
    assert list(env["service"].root.rglob("*")) == []


def test_legacy_unknown_original_is_reconciled_after_expiry(env):
    data = _data("legacy")
    ingest_id, body, _ = _upload(env, data)
    # The pre-fix channel writes the file without an import-original reference.
    first = ing.finalize_ingest(env["principal"], env["policy"], ingest_id=ingest_id,
                                body=body, store_original=env["service"].store_original, now=NOW)
    assert first["state"] == "UNKNOWN"
    assert ic.ClinicImportOriginal.query.count() == 0
    late = NOW + timedelta(days=7)
    recovered = _finalize(env, ingest_id, body, now=late)
    assert recovered["import"]["status"] == "UNASSIGNED"
    delivery_id = recovered["import"]["delivery_id"]
    assert db.session.get(ic.ClinicImportOriginal, delivery_id) is not None
    view = _intent(env, now=late)
    ic.assign_unassigned(actor_user_id=env["author"].id, delivery_id=delivery_id,
                         intent_id=view["id"], now=late)
    healed = _finalize(env, ingest_id, body, now=late)
    assert healed["state"] == "STORED"
    original = db.session.get(ClinicOriginal, healed["original_id"])
    assert env["service"].file_path(original.storage_key).read_bytes() == data


def test_failed_reference_write_is_recovered_after_expiry(env, monkeypatch):
    ingest_id, body, _ = _upload(env, _data("reference-failure"))

    def unavailable(*args, **kwargs):
        raise OperationalError("INSERT", {}, Exception("synthetic reference write failure"))

    with monkeypatch.context() as patch:
        patch.setattr(ic, "_remember_original", unavailable)
        assert _finalize(env, ingest_id, body)["state"] == "UNKNOWN"
    assert ic.ClinicImportOriginal.query.count() == 0
    recovered = _finalize(env, ingest_id, body, now=NOW + timedelta(days=7))
    assert recovered["import"]["status"] == "UNASSIGNED"
    assert ic.ClinicImportOriginal.query.count() == 1
    assert ic.list_unassigned()[0]["original_state"] == "author_required"


def test_assignment_commit_failure_keeps_original_unassigned_and_retryable(env, monkeypatch):
    ingest_id, body, _ = _upload(env, _data("atomic-claim"))
    delivery_id = _finalize(env, ingest_id, body)["import"]["delivery_id"]
    view = _intent(env)
    actor_id = env["author"].id

    def unavailable():
        raise OperationalError("COMMIT", {}, Exception("synthetic assignment commit failure"))

    with monkeypatch.context() as patch:
        patch.setattr(db.session, "commit", unavailable)
        with pytest.raises(OperationalError):
            ic.assign_unassigned(actor_user_id=actor_id, delivery_id=delivery_id,
                                 intent_id=view["id"], now=NOW)
    assert [delivery["id"] for delivery in ic.list_unassigned()] == [delivery_id]
    assert ic.ClinicImportOriginal.query.count() == 1
    assert ClinicOriginal.query.count() == 0
    assert ClinicDictation.query.count() == 0
    assert Recording.query.filter_by(user_id=actor_id).count() == 0
    ic.assign_unassigned(actor_user_id=actor_id, delivery_id=delivery_id, intent_id=view["id"], now=NOW)
    assert ClinicOriginal.query.count() == 1
    assert _finalize(env, ingest_id, body)["state"] == "STORED"


def test_assignment_refuses_changed_stored_bytes_and_keeps_clarification_entry(env):
    data = _data("changed-original")
    ingest_id, body, _ = _upload(env, data)
    delivery_id = _finalize(env, ingest_id, body)["import"]["delivery_id"]
    original = db.session.get(ic.ClinicImportOriginal, delivery_id)
    path = env["service"].file_path(original.storage_key)
    path.write_bytes(b"x" * len(data))
    view = _intent(env)
    with pytest.raises(IngestError) as err:
        ic.assign_unassigned(actor_user_id=env["author"].id, delivery_id=delivery_id,
                             intent_id=view["id"], now=NOW)
    assert err.value.code == "conflict"
    assert [delivery["id"] for delivery in ic.list_unassigned()] == [delivery_id]
    assert ClinicOriginal.query.count() == 0
    assert db.session.get(ch.ClinicAgentIngest, ingest_id).state == "UNKNOWN"


def test_concurrent_assignments_create_one_dictation_for_the_winning_author(env):
    ingest_id, body, _ = _upload(env, _data("concurrent-claim"))
    delivery_id = _finalize(env, ingest_id, body)["import"]["delivery_id"]
    first = _intent(env)
    second = _intent(env, actor_user_id=env["other"].id)
    claims = [(env["author"].id, first["id"]), (env["other"].id, second["id"])]
    ready = Barrier(2)
    db.session.remove()

    def claim(actor_id, intent_id):
        with app.app_context():
            ready.wait(timeout=10)
            try:
                ic.assign_unassigned(actor_user_id=actor_id, delivery_id=delivery_id,
                                     intent_id=intent_id, now=NOW)
                return "assigned", actor_id, intent_id
            except AgentChannelError as exc:
                return exc.code, actor_id, intent_id

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(claim, *args) for args in claims]
        results = [future.result(timeout=30) for future in futures]
    assert sorted(result[0] for result in results) == ["assigned", "conflict"]
    winner = next(result for result in results if result[0] == "assigned")
    assert ClinicOriginal.query.count() == 1
    assert ClinicDictation.query.one().author_user_id == winner[1]
    assert db.session.get(ic.ClinicImportDelivery, delivery_id).intent_id == winner[2]


@pytest.mark.parametrize("legacy", [False, True])
def test_concurrent_finalize_retries_after_expiry_keep_identical_ids(env, monkeypatch, legacy):
    ingest_id, body, _ = _upload(env, _data("concurrent-finalize"))

    def unavailable(*args, **kwargs):
        raise OperationalError("INSERT", {}, Exception("synthetic legacy reference gap"))

    with monkeypatch.context() as patch:
        if legacy:
            patch.setattr(ic, "_remember_original", unavailable)
        first = _finalize(env, ingest_id, body)
    view = _intent(env)
    ic.assign_unassigned(actor_user_id=env["author"].id, delivery_id=first["import"]["delivery_id"],
                         intent_id=view["id"], now=NOW)
    ready = Barrier(2)
    db.session.remove()

    def finalize():
        with app.app_context():
            ready.wait(timeout=10)
            return _finalize(env, ingest_id, body, now=NOW + timedelta(days=7))

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(finalize) for _ in range(2)]
        receipts = [future.result(timeout=30) for future in futures]
    assert all(receipt["state"] == "STORED" for receipt in receipts)
    assert receipts[0]["original_id"] == receipts[1]["original_id"]
    assert receipts[0]["dictation_id"] == receipts[1]["dictation_id"]
    assert ClinicOriginal.query.count() == 1
    assert ClinicDictation.query.count() == 1
    assert ic.ClinicImportOriginal.query.count() == 1
