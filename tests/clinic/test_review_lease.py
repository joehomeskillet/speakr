"""Service rules for the clinic review lease.

Acceptance catalog entries T-017 and T-062 stay not_run. These tests cover
the exclusive hold, the heartbeat, expiry, takeover, a rejected stale
generation and a rejected foreign holder. They do not cover the editor,
an HTTP 409 response, an ETag or an unchanged released text version.
"""

import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event

from src.app import app, db
from src.clinic.lease import (
    EXPIRED,
    FOREIGN_HOLDER,
    LeaseConflict,
    STALE_GENERATION,
    _insert,
    acquire,
    assert_current,
    heartbeat,
    release,
    review_intervals,
)
from src.clinic.models import ClinicDictation, ClinicReviewLease
from src.models import Recording, User

T0 = datetime(2026, 11, 17, 8, 0, tzinfo=timezone.utc)


@pytest.fixture
def clinic_db():
    """App context with SQLite foreign keys on for this test only."""
    with app.app_context():
        hook = None
        if db.engine.dialect.name == "sqlite":
            def hook(dbapi_connection, _record):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()

            event.listen(db.engine, "connect", hook)
            db.session.remove()
            db.engine.dispose()
        db.session.rollback()
        yield
        db.session.rollback()
        if hook is not None:
            event.remove(db.engine, "connect", hook)
            db.session.remove()
            db.engine.dispose()


def _user():
    suffix = uuid.uuid4().hex[:10]
    user = User(
        username="cl" + suffix,
        email=suffix + "@example.invalid",
        password="x",
    )
    db.session.add(user)
    db.session.flush()
    return user


def _account():
    user = _user()
    recording = Recording(user_id=user.id, title="clinic", status="PENDING")
    db.session.add(recording)
    db.session.flush()
    return user, recording


def _dictation(user, recording):
    row = ClinicDictation(recording_id=recording.id, author_user_id=user.id)
    db.session.add(row)
    db.session.flush()
    return row


def _aware(value):
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _stored(dictation_id):
    db.session.expire_all()
    row = db.session.query(ClinicReviewLease).filter_by(dictation_id=dictation_id).one()
    return row.holder_user_id, row.generation, _aware(row.expires_at)


def test_acquire_heartbeat_expiry_and_takeover(clinic_db):
    owner, recording = _account()
    other = _user()
    dictation = _dictation(owner, recording)
    lease_seconds, _heartbeat_seconds = review_intervals()

    lease = acquire(dictation.id, owner.id, T0)
    assert lease.holder_user_id == owner.id
    assert lease.generation == 1
    assert lease.expires_at.tzinfo is not None
    assert lease.expires_at == T0 + timedelta(seconds=lease_seconds)

    renewed = heartbeat(lease, lease.generation, T0 + timedelta(seconds=30))
    assert renewed.holder_user_id == owner.id
    assert renewed.generation == 1
    assert renewed.expires_at == T0 + timedelta(seconds=30 + lease_seconds)

    expiry = renewed.expires_at
    with pytest.raises(LeaseConflict) as missed:
        heartbeat(renewed, renewed.generation, expiry)
    assert missed.value.reason == EXPIRED
    with pytest.raises(LeaseConflict) as late:
        assert_current(dictation.id, owner.id, 1, expiry)
    assert late.value.reason == EXPIRED
    assert _stored(dictation.id) == (owner.id, 1, expiry)

    taken = acquire(dictation.id, other.id, expiry)
    assert taken.holder_user_id == other.id
    assert taken.generation == 2
    assert taken.expires_at == expiry + timedelta(seconds=lease_seconds)
    assert assert_current(dictation.id, other.id, 2, expiry).generation == 2
    with pytest.raises(LeaseConflict) as stale:
        assert_current(dictation.id, owner.id, 1, expiry)
    assert stale.value.reason == STALE_GENERATION
    assert _stored(dictation.id)[0:2] == (other.id, 2)


def test_stale_generation_is_rejected(clinic_db):
    owner, recording = _account()
    dictation = _dictation(owner, recording)
    lease = acquire(dictation.id, owner.id, T0)
    before = _stored(dictation.id)
    later = T0 + timedelta(seconds=5)

    for generation in (0, lease.generation + 5):
        with pytest.raises(LeaseConflict) as heartbeat_conflict:
            heartbeat(lease, generation, later)
        assert heartbeat_conflict.value.reason == STALE_GENERATION
        with pytest.raises(LeaseConflict) as release_conflict:
            release(lease, generation, later)
        assert release_conflict.value.reason == STALE_GENERATION
        with pytest.raises(LeaseConflict) as write_conflict:
            assert_current(dictation.id, owner.id, generation, later)
        assert write_conflict.value.reason == STALE_GENERATION

    assert _stored(dictation.id) == before


def test_foreign_holder_is_rejected(clinic_db):
    owner, recording = _account()
    other = _user()
    dictation = _dictation(owner, recording)
    lease = acquire(dictation.id, owner.id, T0)
    before = _stored(dictation.id)
    later = T0 + timedelta(seconds=5)

    with pytest.raises(LeaseConflict) as second:
        acquire(dictation.id, other.id, later)
    assert second.value.reason == FOREIGN_HOLDER
    with pytest.raises(LeaseConflict) as beat:
        heartbeat(lease, lease.generation, later, user_id=other.id)
    assert beat.value.reason == FOREIGN_HOLDER
    with pytest.raises(LeaseConflict) as dropped:
        release(lease, lease.generation, later, user_id=other.id)
    assert dropped.value.reason == FOREIGN_HOLDER
    with pytest.raises(LeaseConflict) as write_conflict:
        assert_current(dictation.id, other.id, lease.generation, later)
    assert write_conflict.value.reason == FOREIGN_HOLDER

    assert _stored(dictation.id) == before
    assert assert_current(dictation.id, owner.id, 1, later).holder_user_id == owner.id


def test_double_acquire_by_two_users_yields_one_holder(clinic_db):
    owner, recording = _account()
    other = _user()
    dictation = _dictation(owner, recording)
    dictation_id = dictation.id
    owner_id = owner.id
    other_id = other.id
    db.session.commit()
    db.session.remove()

    barrier = threading.Barrier(2)
    found = []
    lock = threading.Lock()

    def grab(user_id):
        with app.app_context():
            try:
                barrier.wait(timeout=5)
                try:
                    lease = acquire(dictation_id, user_id, T0)
                    db.session.commit()
                    outcome = ("held", lease.holder_user_id, lease.generation)
                except LeaseConflict as exc:
                    db.session.rollback()
                    outcome = ("conflict", exc.reason, None)
                except Exception as exc:
                    db.session.rollback()
                    outcome = ("error", type(exc).__name__, str(exc))
                with lock:
                    found.append(outcome)
            finally:
                db.session.remove()

    threads = [
        threading.Thread(target=grab, args=(owner_id,)),
        threading.Thread(target=grab, args=(other_id,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=40)

    assert all(not thread.is_alive() for thread in threads), found
    assert len(found) == 2, found
    held = [item for item in found if item[0] == "held"]
    conflicts = [item for item in found if item[0] == "conflict"]
    assert len(held) == 1, found
    assert held[0][2] == 1, found
    assert len(conflicts) == 1, found
    assert conflicts[0][1] == FOREIGN_HOLDER, found

    rows = db.session.query(ClinicReviewLease).filter_by(dictation_id=dictation_id).all()
    assert len(rows) == 1
    assert rows[0].holder_user_id == held[0][1]
    assert rows[0].generation == 1


def test_losing_insert_keeps_the_session_and_one_holder(clinic_db):
    owner, recording = _account()
    other = _user()
    dictation = _dictation(owner, recording)
    expires = T0 + timedelta(seconds=30)

    first = _insert(dictation.id, owner.id, expires)
    assert first is not None
    assert first.generation == 1
    assert _insert(dictation.id, other.id, expires) is None

    again = acquire(dictation.id, owner.id, T0)
    assert again.id == first.id
    assert again.generation == 1
    assert again.holder_user_id == owner.id
    with pytest.raises(LeaseConflict) as conflict:
        acquire(dictation.id, other.id, T0)
    assert conflict.value.reason == FOREIGN_HOLDER
    assert _stored(dictation.id)[0:2] == (owner.id, 1)


def test_generation_never_decreases(clinic_db):
    owner, recording = _account()
    other = _user()
    dictation = _dictation(owner, recording)
    seen = []

    lease = acquire(dictation.id, owner.id, T0)
    seen.append(lease.generation)
    lease = heartbeat(lease, lease.generation, T0 + timedelta(seconds=1))
    seen.append(lease.generation)
    lease = release(lease, lease.generation, T0 + timedelta(seconds=2))
    seen.append(lease.generation)
    assert lease.generation == 1

    lease = acquire(dictation.id, other.id, T0 + timedelta(seconds=2))
    seen.append(lease.generation)
    expired = lease.expires_at
    lease = acquire(dictation.id, owner.id, expired)
    seen.append(lease.generation)

    with pytest.raises(LeaseConflict) as stale:
        assert_current(dictation.id, other.id, 1, expired)
    assert stale.value.reason == STALE_GENERATION
    _holder, generation, _expires = _stored(dictation.id)
    seen.append(generation)

    assert seen == [1, 1, 1, 2, 3, 3]
    assert all(later >= earlier for earlier, later in zip(seen, seen[1:]))


def test_release_then_same_holder_keeps_generation_until_the_lease_ends(clinic_db):
    owner, recording = _account()
    dictation = _dictation(owner, recording)
    lease = acquire(dictation.id, owner.id, T0, duration=50)
    again = acquire(dictation.id, owner.id, T0 + timedelta(seconds=10), duration=50)
    assert again.id == lease.id
    assert again.generation == 1
    assert again.expires_at == T0 + timedelta(seconds=60)

    released = release(again, 1, T0 + timedelta(seconds=20))
    assert released.generation == 1
    assert released.holder_user_id == owner.id
    assert released.expires_at == T0 + timedelta(seconds=20)
    with pytest.raises(LeaseConflict) as expired:
        assert_current(dictation.id, owner.id, 1, T0 + timedelta(seconds=20))
    assert expired.value.reason == EXPIRED


def test_intervals_follow_dev_defaults_and_arguments(clinic_db):
    owner, recording = _account()
    dictation = _dictation(owner, recording)
    assert review_intervals() == (300, 30)

    zurich = datetime(2026, 11, 17, 9, 0, tzinfo=timezone(timedelta(hours=1)))
    lease = acquire(dictation.id, owner.id, zurich, duration=12, heartbeat_interval=4)
    assert lease.expires_at == T0 + timedelta(seconds=12)
    renewed = heartbeat(lease, 1, T0, duration=7, heartbeat_interval=4)
    assert renewed.expires_at == T0 + timedelta(seconds=7)
    assert review_intervals(duration=12, heartbeat_interval=4) == (12, 4)
    with pytest.raises(ValueError):
        acquire(dictation.id, owner.id, T0, heartbeat_interval=0)
    assert _stored(dictation.id)[2] == T0 + timedelta(seconds=7)


def test_now_must_be_timezone_aware(clinic_db):
    owner, recording = _account()
    dictation = _dictation(owner, recording)
    lease = acquire(dictation.id, owner.id, T0)
    naive = datetime(2026, 11, 17, 8, 0)

    with pytest.raises(ValueError):
        acquire(dictation.id, owner.id, naive)
    with pytest.raises(ValueError):
        heartbeat(lease, 1, naive)
    with pytest.raises(ValueError):
        release(lease, 1, naive)
    with pytest.raises(ValueError):
        assert_current(dictation.id, owner.id, 1, naive)
    assert _stored(dictation.id)[0:2] == (owner.id, 1)
