"""Durable pieces behind the clinic wiring in src/app.py.

- choose_author_pool: the one-author-pool-per-user rule made deterministic.
- DurableProtectionStore: protection records in the database (survive a restart).
- make_source_loader: read-only decoded WAV sources of one dictation for the derived rendering.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from sqlalchemy.exc import IntegrityError

from src.clinic import audio_revisions, decoder, dictation_lock
from src.clinic.audio_render import AudioSource
from src.clinic.models import ClinicOriginal
from datetime import datetime, timezone

from src.clinic.pools import ClinicAuthorPool, ClinicAuthorPoolMember, is_routable
from src.database import db
from src.services.storage.locator import local_path_from_key


def choose_author_pool(user_id):
    """Author pool id of the user, or None when the user is in none.

    The rule: among the member pools that are routable now (active, inside valid_from /
    valid_to; a null bound is open), the one with the alphabetically first name (pool names
    are unique), pool id as the final tie-break. The same input always gives the same pool,
    independent of row or UUID order.
    """
    now = datetime.now(timezone.utc)
    pools = (db.session.query(ClinicAuthorPool)
             .join(ClinicAuthorPoolMember, ClinicAuthorPoolMember.pool_id == ClinicAuthorPool.id)
             .filter(ClinicAuthorPoolMember.user_id == user_id)
             .order_by(ClinicAuthorPool.name, ClinicAuthorPool.id).all())
    for pool in pools:
        if is_routable(pool, now):
            return pool.id
    return None


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
    Originals are verified against their stored hash; everything is decoded to 16 kHz mono WAV
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
                if entry.source_type == audio_revisions.SOURCE_ORIGINAL and result.input_sha256 != row.content_hash:
                    raise audio_revisions.AudioRevisionError('stored original does not match its hash')
                sources[key] = AudioSource(Path(result.output_path).read_bytes(), result.input_sha256)
        return sources
    return load
