"""Durable pieces behind the clinic wiring in src/app.py.

- choose_author_pool: resolve the saved author pool against current memberships.
- DurableProtectionStore: protection records in the database (survive a restart).
- make_source_loader: read-only decoded WAV sources of one dictation for the derived rendering.
"""
from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.exc import IntegrityError

from src.clinic import audio_revisions, decoder, dictation_lock
from src.clinic.audio_render import AudioSource
from src.clinic.models import ClinicOriginal
from src.clinic.pools import ClinicAuthorPool, ClinicAuthorPoolMember, is_routable
from src.clinic.presets import ClinicPreset, set_preset
from src.database import db
from src.services.storage.locator import local_path_from_key


_ACTIVE_POOL_KEY = "active_author_pool"


@dataclass(frozen=True)
class ActiveAuthorPool:
    pool_id: str | None
    reselection_required: bool
    pools: tuple[ClinicAuthorPool, ...]


class ActivePoolNotAllowed(ValueError):
    pass


def _routable_member_pools(user_id: int) -> tuple[ClinicAuthorPool, ...]:
    now = datetime.now(timezone.utc)
    members = (db.session.query(ClinicAuthorPool)
               .join(ClinicAuthorPoolMember, ClinicAuthorPoolMember.pool_id == ClinicAuthorPool.id)
               .filter(ClinicAuthorPoolMember.user_id == user_id)
               .order_by(ClinicAuthorPool.name, ClinicAuthorPool.id).all())
    return tuple(pool for pool in members if is_routable(pool, now))


def active_author_pool(user_id: int) -> ActiveAuthorPool:
    """Fall back by name only when the user has never chosen a pool."""
    pools = _routable_member_pools(user_id)
    saved = db.session.query(ClinicPreset.value_json).filter_by(
        scope="user", scope_id=str(user_id), key=_ACTIVE_POOL_KEY,
    ).scalar()
    if saved is None:
        return ActiveAuthorPool(pools[0].id if pools else None, False, pools)
    pool_id = json.loads(saved)
    if any(pool.id == pool_id for pool in pools):
        return ActiveAuthorPool(pool_id, False, pools)
    return ActiveAuthorPool(None, True, pools)


def set_active_author_pool(user_id: int, pool_id: str) -> ActiveAuthorPool:
    pools = _routable_member_pools(user_id)
    if not any(pool.id == pool_id for pool in pools):
        raise ActivePoolNotAllowed(pool_id)
    set_preset(_ACTIVE_POOL_KEY, pool_id, scope="user", scope_id=user_id)
    db.session.commit()
    return ActiveAuthorPool(pool_id, False, pools)


def choose_author_pool(user_id: int) -> str | None:
    return active_author_pool(user_id).pool_id


class ClinicDictationProtection(db.Model):
    """Hashed protection record of one dictation (no secret); version is the optimistic lock."""

    __tablename__ = 'clinic_dictation_protection'

    dictation_id = db.Column(db.String(36), db.ForeignKey('clinic_dictation.id'), primary_key=True)
    record_json = db.Column(db.Text, nullable=True)
    version = db.Column(db.Integer, nullable=False, default=1, server_default='1')


class DurableProtectionStore:
    """ProtectionStore over the database; ACL decisions come from the two injected callables."""

    def __init__(self, acl_allows, can_manage, dictation_exists):
        self._acl, self._manage, self._exists = acl_allows, can_manage, dictation_exists

    def acl_allows(self, user_id, dictation_id):
        return self._acl(user_id, dictation_id)

    def can_manage(self, user_id, dictation_id):
        return self._manage(user_id, dictation_id)

    def get(self, dictation_id):
        if not self._exists(dictation_id):
            return None
        row = db.session.get(ClinicDictationProtection, dictation_id, populate_existing=True)
        if row is None:
            return None, 1
        record = None if row.record_json is None else dictation_lock.record_from_json(row.record_json)
        return record, row.version

    def put(self, dictation_id, record, expected_version):
        text = None if record is None else dictation_lock.record_to_json(record)
        row = db.session.get(ClinicDictationProtection, dictation_id, populate_existing=True)
        if row is None:
            if expected_version != 1:
                return None
            db.session.add(ClinicDictationProtection(dictation_id=dictation_id, record_json=text, version=2))
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                return None
            return 2
        updated = (db.session.query(ClinicDictationProtection)
                   .filter_by(dictation_id=dictation_id, version=expected_version)
                   .update({'record_json': text, 'version': expected_version + 1}))
        db.session.commit()
        return expected_version + 1 if updated else None


def make_source_loader(root):
    """Build load_sources(dictation, edl) reading stored audio under `root`, read-only.

    Only sources named in the edit list are loaded, and each must belong to the dictation.
    All sources are verified against their stored hash; everything is decoded to 16 kHz mono WAV
    in a temporary directory. Any problem raises AudioRevisionError (the route answers 503).
    """
    def load(dictation, edl):
        sources = {}
        for entry in edl.entries:
            key = (entry.source_type, entry.source_id)
            if key in sources:
                continue
            if entry.source_type == audio_revisions.SOURCE_ORIGINAL:
                row = db.session.get(ClinicOriginal, entry.source_id)
            else:
                row = db.session.get(audio_revisions.ClinicAudioSegment, entry.source_id)
            if row is None or row.dictation_id != dictation.id:
                raise audio_revisions.AudioRevisionError('audio source does not belong to this dictation')
            try:
                path = Path(local_path_from_key(str(root), row.storage_key))
            except ValueError as exc:
                raise audio_revisions.AudioRevisionError('invalid audio storage key') from exc
            with tempfile.TemporaryDirectory(prefix='clinic-derive-') as tmp:
                result = decoder.decode_to_wav(path, tmp)
                if result.status != decoder.OK:
                    raise audio_revisions.AudioRevisionError('audio source cannot be decoded: ' + result.status)
                if result.input_sha256 != row.content_hash:
                    raise audio_revisions.AudioRevisionError('stored audio source does not match its hash')
                sources[key] = AudioSource(Path(result.output_path).read_bytes(), result.input_sha256)
        return sources
    return load
