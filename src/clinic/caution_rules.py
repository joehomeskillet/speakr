"""Authoritative caution-word lists (PAR-VOC-004, owner decision Q-19).

One append-only version log per scope: the site-wide ``global`` baseline (site admin) and
one list per author pool (separately authorized). The effective rule set of a pool is the
union of the current global and current pool list. ``effective_rule_set`` hands it to the
W11 matcher in ``caution_words`` that the release gate already uses; matching and per-hit
review are not re-implemented here.

Every version row keeps who published it and when, so labels such as ``g2.p1`` in existing
markers stay attributable. A write names the version it read; a stale write changes nothing.
"""

from __future__ import annotations

import json
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import UniqueConstraint
from sqlalchemy.exc import IntegrityError

from src.clinic.caution_words import CautionRuleSet
from src.clinic.vocabulary import AwareDateTime
from src.database import db

GLOBAL_KEY = "global"
MAX_WORDS = 200
MAX_WORD_LENGTH = 64


class CautionRuleError(ValueError):
    """The list was rejected before it was stored."""


class StaleVersion(CautionRuleError):
    """``expected_version`` is not the stored version. Nothing was written."""

    def __init__(self, version: int):
        self.version = version
        super().__init__("stale_version")


class ClinicCautionRuleVersion(db.Model):
    __tablename__ = "clinic_caution_rule_version"
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    scope_key = db.Column(db.String(64), nullable=False, index=True)
    version = db.Column(db.Integer, nullable=False)
    words_json = db.Column(db.Text, nullable=False)
    created_by = db.Column(db.Integer, nullable=False)
    created_at = db.Column(AwareDateTime(), nullable=False, default=lambda: datetime.now(timezone.utc))
    __table_args__ = (UniqueConstraint("scope_key", "version", name="uq_caution_rule_scope_version"),)

    @property
    def words(self) -> tuple[str, ...]:
        return tuple(json.loads(self.words_json))


@dataclass(frozen=True)
class RuleList:
    scope_key: str
    version: int
    words: tuple[str, ...]
    published_by: int | None
    published_at: datetime | None


def create_tables(bind=None) -> None:
    db.metadata.create_all(bind=bind or db.engine, tables=[ClinicCautionRuleVersion.__table__], checkfirst=True)


def pool_key(pool_id: str) -> str:
    return "pool:" + pool_id


def clean_words(words: Any) -> list[str]:
    """Canonical, de-duplicated word list; the matcher cannot use a word containing ``|``."""
    if not isinstance(words, list) or len(words) > MAX_WORDS:
        raise CautionRuleError("invalid_words")
    seen: dict[str, str] = {}
    for raw in words:
        if not isinstance(raw, str):
            raise CautionRuleError("invalid_words")
        word = " ".join(unicodedata.normalize("NFC", raw).split())
        if not word or len(word) > MAX_WORD_LENGTH or "|" in word or any(ord(c) < 32 or ord(c) == 127 for c in word):
            raise CautionRuleError("invalid_words")
        seen.setdefault(word.casefold(), word)
    return sorted(seen.values(), key=str.casefold)


def current(scope_key: str) -> RuleList:
    """Latest list of a scope; version 0 and no words before the first publication."""
    row = (db.session.query(ClinicCautionRuleVersion).filter_by(scope_key=scope_key)
           .order_by(ClinicCautionRuleVersion.version.desc()).first())
    if row is None:
        return RuleList(scope_key, 0, (), None, None)
    return RuleList(scope_key, row.version, row.words, row.created_by, row.created_at)


def history(scope_key: str) -> list[RuleList]:
    rows = (db.session.query(ClinicCautionRuleVersion).filter_by(scope_key=scope_key)
            .order_by(ClinicCautionRuleVersion.version.desc()).all())
    return [RuleList(scope_key, r.version, r.words, r.created_by, r.created_at) for r in rows]


def publish(scope_key: str, words: Any, actor_id: int, expected_version: int) -> RuleList:
    """Append the next version. The caller has already authorized the scope for ``actor_id``."""
    clean = clean_words(words)
    if type(expected_version) is not int or expected_version < 0:
        raise CautionRuleError("bad_version")
    stored = current(scope_key).version
    if expected_version != stored:
        raise StaleVersion(stored)
    db.session.add(ClinicCautionRuleVersion(
        scope_key=scope_key, version=stored + 1, words_json=json.dumps(clean), created_by=actor_id,
    ))
    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        raise StaleVersion(current(scope_key).version) from exc
    return current(scope_key)


def effective_rule_set(pool_id: str | None) -> CautionRuleSet:
    """Global baseline plus any additions from the dictation's pool."""
    baseline = current(GLOBAL_KEY)
    additions = current(pool_key(pool_id)) if pool_id else None
    words = {w.casefold(): w for w in (*baseline.words, *(additions.words if additions else ()))}
    return CautionRuleSet(
        pool_id=pool_id, version=f"g{baseline.version}.p{additions.version if additions else 0}",
        words=tuple(sorted(words.values(), key=str.casefold)), authorized=True,
    )
