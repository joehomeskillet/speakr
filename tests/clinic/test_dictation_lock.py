"""Tests for the optional dictation protection (PAR-REC-006). Pure, no database."""

from __future__ import annotations

import inspect
import itertools
import json
from datetime import datetime, timedelta, timezone

import pytest

from src.clinic import dictation_lock as dl
from src.clinic.dictation_lock import (
    LockError,
    LockoutTracker,
    UnlockGrant,
    decide_access,
    grant,
    record_from_json,
    record_to_json,
    set_protection,
    verify,
)

NOW = datetime(2026, 11, 18, 12, 0, 0, tzinfo=timezone.utc)
SECRET = "Zx9-distinct-Geheimnis-Q7"


def _g(user=1, dic="d1", expires=None):
    return UnlockGrant(user, dic, expires or NOW + timedelta(minutes=5))


def _access(acl, protected, g):
    return decide_access(acl, protected, g, user_id=1, dictation_id="d1", now=NOW)


# 1. protection never substitutes for rights
@pytest.mark.parametrize(
    "protected,g",
    list(itertools.product([False, True], [None, _g(), _g(user=2), _g(dic="x"), _g(expires=NOW)])),
)
def test_acl_denial_is_final(protected, g):
    assert _access(False, protected, g) is False


# 2. grant rules
def test_unprotected_with_acl_allows():
    assert _access(True, False, None) is True


def test_protected_needs_matching_live_grant():
    assert _access(True, True, None) is False
    assert _access(True, True, _g()) is True
    assert _access(True, True, _g(user=2)) is False
    assert _access(True, True, _g(dic="other")) is False
    assert _access(True, True, _g(expires=NOW)) is False
    assert _access(True, True, _g(expires=NOW - timedelta(seconds=1))) is False
    assert _access(True, True, _g(expires=NOW + timedelta(microseconds=1))) is True


def test_grant_ttl():
    g = grant(1, "d1", NOW)
    assert g.expires_at == NOW + timedelta(seconds=600)
    assert grant(1, "d1", NOW, ttl_seconds=5).expires_at == NOW + timedelta(seconds=5)
    for bad in (0, -1, True, 1.5):
        with pytest.raises(LockError) as e:
            grant(1, "d1", NOW, ttl_seconds=bad)
        assert e.value.code == "bad_ttl"


# 3. secret normalisation and limits
def test_nfc_normalisation():
    composed, decomposed = "über-Geheim", "über-Geheim"
    assert composed != decomposed
    assert verify(set_protection(composed), decomposed) is True


@pytest.mark.parametrize("secret", ["", "   ", "  "])
def test_empty_secret(secret):
    with pytest.raises(LockError) as e:
        set_protection(secret)
    assert e.value.code == "empty_secret"


def test_secret_length_limit():
    set_protection("a" * 1024)
    with pytest.raises(LockError) as e:
        set_protection("a" * 1025)
    assert e.value.code == "too_long"


@pytest.mark.parametrize("secret", [None, 123, b"x", "a\x00b", "a\nb"])
def test_bad_secret_type_or_control(secret):
    with pytest.raises(LockError) as e:
        set_protection(secret)
    assert e.value.code == "bad_secret"


# 4. hashing
def test_salt_and_digest_differ_and_verify():
    a, b = set_protection(SECRET), set_protection(SECRET)
    assert a.salt != b.salt and a.digest != b.digest
    assert (len(a.salt), len(a.digest)) == (32, 64)
    assert (a.n, a.r, a.p) == (2**14, 8, 1)
    assert verify(a, SECRET) is True
    assert verify(a, SECRET + "x") is False


def test_source_uses_compare_digest_and_no_own_crypto():
    src = inspect.getsource(dl)
    assert "hmac.compare_digest" in src
    assert "hashlib.scrypt" in src
    for forbidden in ("sha256(", "md5(", "xor", "AES", "Fernet"):
        assert forbidden not in src


# 5. no leakage
def test_no_secret_leak():
    rec = set_protection(SECRET)
    for text in (repr(rec), str(rec), record_to_json(rec)):
        assert SECRET not in text
    messages = []
    for call in (
        lambda: set_protection(" "),
        lambda: set_protection(SECRET * 100),
        lambda: set_protection(SECRET + "\x00"),
        lambda: verify(rec, SECRET + "\x00"),
        lambda: record_from_json(SECRET),
    ):
        with pytest.raises(LockError) as e:
            call()
        messages.append(str(e.value))
    assert all(SECRET not in m for m in messages)


# 6. record JSON
def test_json_round_trip_verifies():
    rec = set_protection(SECRET)
    back = record_from_json(record_to_json(rec))
    assert back == rec
    assert verify(back, SECRET) is True


def _mutated(**changes):
    data = json.loads(record_to_json(set_protection(SECRET)))
    data.update(changes)
    return json.dumps(data)


@pytest.mark.parametrize(
    "changes",
    [
        {"version": 2}, {"version": "1"}, {"version": True},
        {"salt": "ab" * 15}, {"salt": "zz" * 16}, {"salt": 5}, {"salt": "AB" * 16},
        {"digest": "ab" * 31}, {"digest": None},
        # n above _MAX_N (2**15) is a memory-exhaustion vector on verify and is
        # refused even though it is a valid scrypt parameter.
        {"n": 2**21}, {"n": 2**16}, {"n": 2**14 + 1}, {"n": 1}, {"n": 0}, {"n": "16384"}, {"n": 16384.0},
        {"r": 0}, {"r": 99}, {"p": 0}, {"p": 99}, {"r": True},
    ],
)
def test_record_from_json_rejects(changes):
    with pytest.raises(LockError) as e:
        record_from_json(_mutated(**changes))
    assert e.value.code == "bad_record"


@pytest.mark.parametrize("text", ["", "not json", "[]", "null", "{}", None, 5])
def test_record_from_json_rejects_garbage(text):
    with pytest.raises(LockError) as e:
        record_from_json(text)
    assert e.value.code == "bad_record"


def test_record_from_json_rejects_extra_field():
    with pytest.raises(LockError) as e:
        record_from_json(_mutated(extra=1))
    assert e.value.code == "bad_record"


def test_higher_n_within_bound_verifies():
    rec = set_protection(SECRET)
    text = _mutated(n=2**15, digest="00" * 32)
    assert verify(record_from_json(text), SECRET) is False
    assert verify(rec, SECRET) is True


# 7. lockout
def _fail(t, n, user=1, dic="d1", start=NOW):
    return [t.attempt(user, dic, False, start + timedelta(seconds=i)) for i in range(n)]


def test_lock_after_max_failures():
    t = LockoutTracker(max_attempts=3, lock_seconds=60)
    assert _fail(t, 3) == [False, False, False]
    assert t.locked_until(1, "d1", NOW + timedelta(seconds=2)) == NOW + timedelta(seconds=62)


def test_correct_attempt_during_lock_refused_and_keeps_lock():
    t = LockoutTracker(max_attempts=3, lock_seconds=60)
    _fail(t, 3)
    during = NOW + timedelta(seconds=30)
    assert t.attempt(1, "d1", True, during) is False
    assert t.locked_until(1, "d1", during) == NOW + timedelta(seconds=62)


def test_counter_restarts_after_lock():
    t = LockoutTracker(max_attempts=3, lock_seconds=60)
    _fail(t, 3)
    after = NOW + timedelta(seconds=62)
    assert t.locked_until(1, "d1", after) is None
    assert t.attempt(1, "d1", False, after) is False
    assert t.attempt(1, "d1", False, after) is False
    assert t.locked_until(1, "d1", after) is None  # only 2 failures so far
    assert t.attempt(1, "d1", False, after) is False
    assert t.locked_until(1, "d1", after) == after + timedelta(seconds=60)


def test_correct_attempt_after_lock_unlocks():
    t = LockoutTracker(max_attempts=2, lock_seconds=10)
    _fail(t, 2)
    assert t.attempt(1, "d1", True, NOW + timedelta(seconds=11)) is True


def test_success_resets_failure_count():
    t = LockoutTracker(max_attempts=3, lock_seconds=60)
    _fail(t, 2)
    assert t.attempt(1, "d1", True, NOW) is True
    _fail(t, 2)
    assert t.locked_until(1, "d1", NOW) is None
    t.attempt(1, "d1", False, NOW)
    assert t.locked_until(1, "d1", NOW) is not None


def test_other_pairs_unaffected():
    t = LockoutTracker(max_attempts=2, lock_seconds=60)
    _fail(t, 2)
    assert t.locked_until(2, "d1", NOW) is None
    assert t.locked_until(1, "d2", NOW) is None
    assert t.attempt(2, "d1", True, NOW) is True
    assert t.attempt(1, "d2", True, NOW) is True


def test_defaults_and_bad_config():
    t = LockoutTracker()
    _fail(t, 4)
    assert t.locked_until(1, "d1", NOW) is None
    t.attempt(1, "d1", False, NOW)
    assert t.locked_until(1, "d1", NOW) == NOW + timedelta(seconds=300)
    for kw in ({"max_attempts": 0}, {"max_attempts": True}, {"lock_seconds": -1}, {"lock_seconds": 1.5}):
        with pytest.raises(LockError):
            LockoutTracker(**kw)


def test_lockout_input_validation():
    t = LockoutTracker()
    with pytest.raises(LockError):
        t.attempt(True, "d1", False, NOW)
    with pytest.raises(LockError):
        t.attempt(1, "", False, NOW)
    with pytest.raises(LockError):
        t.attempt(1, "a\x00", False, NOW)
    with pytest.raises(LockError):
        t.attempt(1, "d1", 1, NOW)


# 8. no clock, naive datetimes rejected
def test_naive_datetime_raises():
    naive = datetime(2026, 11, 18, 12, 0, 0)
    t = LockoutTracker()
    with pytest.raises(ValueError):
        t.attempt(1, "d1", False, naive)
    with pytest.raises(ValueError):
        t.locked_until(1, "d1", naive)
    with pytest.raises(ValueError):
        grant(1, "d1", naive)
    with pytest.raises(ValueError):
        decide_access(True, False, None, user_id=1, dictation_id="d1", now=naive)
    with pytest.raises(ValueError):
        decide_access(True, True, _g(expires=naive), user_id=1, dictation_id="d1", now=NOW)


def test_module_has_no_clock():
    src = inspect.getsource(dl)
    assert "now()" not in src and "utcnow" not in src and "time.time" not in src


def test_decide_access_strict_flags():
    with pytest.raises(LockError):
        decide_access(1, False, None, user_id=1, dictation_id="d1", now=NOW)
    with pytest.raises(LockError):
        decide_access(True, "yes", None, user_id=1, dictation_id="d1", now=NOW)
    with pytest.raises(LockError):
        decide_access(True, True, "grant", user_id=1, dictation_id="d1", now=NOW)
