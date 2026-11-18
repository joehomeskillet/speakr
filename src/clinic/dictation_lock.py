"""Optional extra protection of one clinic dictation.

A protected dictation needs an unlock secret on top of the access rights,
never instead of them: ``decide_access`` is False whenever the ACL says no.

Scope: scrypt secret hashing (standard library only), constant-time verify,
JSON form of the stored record, an in-memory failed-attempt lockout, a
time-limited unlock grant and the final access decision.

Not in scope: Flask routes, database access, persistence of the record or
the lockout state, own cryptography. Nothing here reads a clock: every call
that needs time takes a timezone-aware ``now``; a naive value raises
``ValueError``. Nothing here deletes data.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta

_VERSION = 1
_N, _R, _P = 2**14, 8, 1
_SALT_BYTES, _DIGEST_BYTES = 16, 32
_MAX_SECRET_CHARS = 1024
# _MAX_N stays close to the written _N: a tampered record with a huge n would
# otherwise make verify() allocate gigabytes (memory-exhaustion DoS). 2**15
# leaves exactly one strengthening step above the current write value.
_MAX_N, _MAX_R, _MAX_P = 2**15, 8, 4
_HEX = frozenset("0123456789abcdef")


class LockError(ValueError):
    """Invalid input or record. ``code`` is a stable string; the message never holds a secret."""

    def __init__(self, code: str, message: str | None = None):
        self.code = code
        super().__init__(message or code)


@dataclass(frozen=True)
class ProtectionRecord:
    """Stored form of a secret: scrypt parameters, hex salt and hex digest. Holds no secret."""

    version: int
    salt: str
    digest: str
    n: int
    r: int
    p: int


@dataclass(frozen=True)
class UnlockGrant:
    user_id: int
    dictation_id: str
    expires_at: datetime


# --- validation helpers -------------------------------------------------


def _check_now(now: object, name: str = "now") -> datetime:
    if not isinstance(now, datetime):
        raise TypeError(f"{name} must be a datetime")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return now


def _check_user(user_id: object) -> int:
    if isinstance(user_id, bool) or not isinstance(user_id, int):
        raise LockError("bad_user_id")
    return user_id


def _check_dictation(dictation_id: object) -> str:
    if not isinstance(dictation_id, str) or not dictation_id.strip():
        raise LockError("bad_dictation_id")
    if any(unicodedata.category(c) == "Cc" for c in dictation_id):
        raise LockError("bad_dictation_id")
    return dictation_id


def _check_positive_int(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise LockError(code)
    return value


def _normalise_secret(secret: object) -> str:
    if not isinstance(secret, str):
        raise LockError("bad_secret")
    text = unicodedata.normalize("NFC", secret)
    if not text.strip():
        raise LockError("empty_secret")
    if len(text) > _MAX_SECRET_CHARS:
        raise LockError("too_long")
    if any(unicodedata.category(c) == "Cc" for c in text):
        raise LockError("bad_secret")
    return text


def _is_hex(value: object, length: int) -> bool:
    return isinstance(value, str) and len(value) == length and _HEX.issuperset(value)


def _check_record(record: object) -> ProtectionRecord:
    bad = LockError("bad_record")
    if not isinstance(record, ProtectionRecord):
        raise bad
    for value in (record.version, record.n, record.r, record.p):
        if isinstance(value, bool) or not isinstance(value, int):
            raise bad
    if record.version != _VERSION:
        raise bad
    if not (2 <= record.n <= _MAX_N and record.n & (record.n - 1) == 0):
        raise bad
    if not (1 <= record.r <= _MAX_R and 1 <= record.p <= _MAX_P):
        raise bad
    if not _is_hex(record.salt, _SALT_BYTES * 2) or not _is_hex(record.digest, _DIGEST_BYTES * 2):
        raise bad
    return record


def _scrypt(secret: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    # maxmem sized to the parameters: the default 32 MiB cap rejects n above 2**14 with r=8.
    return hashlib.scrypt(
        secret.encode("utf-8"), salt=salt, n=n, r=r, p=p,
        maxmem=128 * n * r * p + 128 * n * r + 2**20, dklen=_DIGEST_BYTES,
    )


# --- secret hashing -----------------------------------------------------


def set_protection(secret: str) -> ProtectionRecord:
    """Hash ``secret`` (NFC-normalised) with a fresh random salt."""
    text = _normalise_secret(secret)
    salt = os.urandom(_SALT_BYTES)
    digest = _scrypt(text, salt, _N, _R, _P)
    return ProtectionRecord(_VERSION, salt.hex(), digest.hex(), _N, _R, _P)


def verify(record: ProtectionRecord, secret: str) -> bool:
    """True when ``secret`` matches ``record``. Constant-time comparison."""
    rec = _check_record(record)
    text = _normalise_secret(secret)
    digest = _scrypt(text, bytes.fromhex(rec.salt), rec.n, rec.r, rec.p)
    return hmac.compare_digest(digest, bytes.fromhex(rec.digest))


def record_to_json(record: ProtectionRecord) -> str:
    rec = _check_record(record)
    return json.dumps(
        {"version": rec.version, "salt": rec.salt, "digest": rec.digest,
         "n": rec.n, "r": rec.r, "p": rec.p},
        sort_keys=True,
    )


def record_from_json(text: str) -> ProtectionRecord:
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        raise LockError("bad_record") from None
    fields = ("version", "salt", "digest", "n", "r", "p")
    if not isinstance(data, dict) or set(data) != set(fields):
        raise LockError("bad_record")
    return _check_record(ProtectionRecord(**{k: data[k] for k in fields}))


# --- lockout ------------------------------------------------------------


class LockoutTracker:
    """In-memory failed-attempt counter per (user, dictation).

    After ``max_attempts`` consecutive failures the pair is locked for
    ``lock_seconds``. During the lock every attempt is refused and changes
    nothing. When the lock has passed the counter starts at zero.
    """

    def __init__(self, max_attempts: int = 5, lock_seconds: int = 300):
        self._max = _check_positive_int(max_attempts, "bad_max_attempts")
        self._lock_for = timedelta(seconds=_check_positive_int(lock_seconds, "bad_lock_seconds"))
        self._failures: dict[tuple[int, str], int] = {}
        self._until: dict[tuple[int, str], datetime] = {}
        self._mutex = threading.Lock()

    def _expire(self, key: tuple[int, str], now: datetime) -> None:
        until = self._until.get(key)
        if until is not None and now >= until:
            del self._until[key]
            self._failures.pop(key, None)

    def attempt(self, user_id: int, dictation_id: str, ok: bool, now: datetime) -> bool:
        """Record one unlock attempt; True when it is allowed to unlock."""
        key = (_check_user(user_id), _check_dictation(dictation_id))
        if not isinstance(ok, bool):
            raise LockError("bad_ok")
        _check_now(now)
        with self._mutex:
            self._expire(key, now)
            if key in self._until:
                return False
            if ok:
                self._failures.pop(key, None)
                return True
            count = self._failures.get(key, 0) + 1
            self._failures[key] = count
            if count >= self._max:
                self._until[key] = now + self._lock_for
            return False

    def locked_until(self, user_id: int, dictation_id: str, now: datetime) -> datetime | None:
        key = (_check_user(user_id), _check_dictation(dictation_id))
        _check_now(now)
        with self._mutex:
            self._expire(key, now)
            return self._until.get(key)


# --- grant and decision -------------------------------------------------


def grant(user_id: int, dictation_id: str, now: datetime, ttl_seconds: int = 600) -> UnlockGrant:
    ttl = _check_positive_int(ttl_seconds, "bad_ttl")
    return UnlockGrant(
        _check_user(user_id), _check_dictation(dictation_id),
        _check_now(now) + timedelta(seconds=ttl),
    )


def decide_access(
    acl_allows: bool,
    protected: bool,
    grant: UnlockGrant | None,
    *,
    user_id: int,
    dictation_id: str,
    now: datetime,
) -> bool:
    """ACL first; protection only adds a requirement and never lifts a denial."""
    if not isinstance(acl_allows, bool) or not isinstance(protected, bool):
        raise LockError("bad_flag")
    _check_user(user_id)
    _check_dictation(dictation_id)
    _check_now(now)
    if grant is not None:
        if not isinstance(grant, UnlockGrant):
            raise LockError("bad_grant")
        _check_now(grant.expires_at, "grant.expires_at")
    if not acl_allows:
        return False
    if not protected:
        return True
    return (
        grant is not None
        and grant.user_id == user_id
        and grant.dictation_id == dictation_id
        and grant.expires_at > now
    )
