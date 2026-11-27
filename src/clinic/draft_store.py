"""Durable draft metadata over existing dictations and transcript revisions.

Draft IDs are dictation IDs; ownership and versions come from ClinicDictation.
Mutations commit their transaction and roll it back on failure. Call them with
no unrelated pending writes. Integrators must call create_tables at startup,
authenticate owner_id at the HTTP boundary, and map DraftConflict to HTTP 409.
API/UI wiring and browser-tab selection belong to later work packages.
"""

from datetime import timezone

from sqlalchemy import update

from src.clinic import revisions
from src.clinic.draft_registry import (
    Draft, DraftConflict, DraftError, _require_draft_id, _require_now,
    _require_owner, _require_text, _require_title, _require_version,
)
from src.clinic.models import ClinicDictation, ClinicOriginal
from src.database import db


class ClinicDraft(db.Model):
    """One persistent draft marker, including its non-destructive tombstone."""

    __tablename__ = "clinic_draft"
    dictation_id = db.Column(
        db.String(36), db.ForeignKey("clinic_dictation.id"), primary_key=True,
    )
    title = db.Column(db.String(200), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False)
    discarded_at = db.Column(db.DateTime(timezone=True), nullable=True)


def create_tables(bind=None):
    """Idempotent additive schema entry point for startup integration."""
    ClinicDraft.__table__.create(bind=bind or db.engine, checkfirst=True)


class DraftStore:
    """Stateless database-backed draft operations, always scoped to one author."""

    def create(self, owner_id, dictation_id, title, now):
        """Register an existing dictation, retaining its current text and originals."""
        title, now = _require_title(title), _require_now(now)
        try:
            dictation = self._dictation(owner_id, dictation_id)
            if db.session.get(ClinicDraft, dictation_id) is not None:
                raise DraftError("duplicate_id")
            self._claim(dictation, dictation.version_counter)
            row = ClinicDraft(dictation_id=dictation_id, title=title,
                              created_at=now, updated_at=now)
            db.session.add(row)
            db.session.flush()
            result = self.get(owner_id, dictation_id)
            db.session.commit()
            return result
        except Exception:
            db.session.rollback()
            raise

    def get(self, owner_id, draft_id):
        """Read one owned draft, including archived text, without leaking other owners."""
        dictation = self._dictation(owner_id, draft_id)
        row = db.session.get(ClinicDraft, draft_id, populate_existing=True)
        if row is None:
            raise DraftError("not_found")
        head = revisions.current_revision(dictation)
        return Draft(row.dictation_id, dictation.author_user_id, row.title,
                     head.body if head else "", dictation.version_counter,
                     _utc(row.created_at), _utc(row.updated_at), _utc(row.discarded_at))

    def list(self, owner_id, *, discarded=False):
        """List only this author's open drafts, or tombstones when requested."""
        _require_owner(owner_id)
        rows = (db.session.query(ClinicDraft).join(ClinicDictation)
                .filter(ClinicDictation.author_user_id == owner_id))
        rows = rows.filter(ClinicDraft.discarded_at.is_not(None) if discarded
                           else ClinicDraft.discarded_at.is_(None))
        return [self.get(owner_id, row.dictation_id) for row in
                rows.order_by(ClinicDraft.updated_at.desc(), ClinicDraft.dictation_id).all()]

    def save(self, owner_id, draft_id, text, base_version, now):
        """Append a human revision. A first text save requires an existing original."""
        return self._write(owner_id, draft_id, base_version, now, text=_require_text(text))

    def discard(self, owner_id, draft_id, base_version, *, confirm, now):
        """Archive exactly the confirmed draft/version; never delete text or audio."""
        if type(confirm) is not bool:
            raise DraftError("invalid_confirm")
        if not confirm:
            raise DraftError("confirmation_required")
        return self._write(owner_id, draft_id, base_version, now, discard=True)

    def _write(self, owner_id, draft_id, base_version, now, *, text=None, discard=False):
        _require_version(base_version)
        now = _require_now(now)
        try:
            current = self.get(owner_id, draft_id)
            if current.discarded_at is not None:
                raise DraftError("discarded")
            dictation = self._dictation(owner_id, draft_id)
            self._claim(dictation, base_version)
            row = db.session.get(ClinicDraft, draft_id, populate_existing=True)
            if discard:
                row.discarded_at = now
            else:
                head = revisions.current_revision(dictation)
                original = None
                if head is None:
                    original = (ClinicOriginal.query.filter_by(dictation_id=draft_id)
                                .order_by(ClinicOriginal.created_at, ClinicOriginal.id).first())
                    if original is None:
                        raise DraftError("original_required")
                revisions.add_human_revision(dictation, head, text, owner_id,
                                             original_version=original)
            row.updated_at = now
            db.session.flush()
            result = self.get(owner_id, draft_id)
            db.session.commit()
            return result
        except Exception:
            db.session.rollback()
            raise

    def _dictation(self, owner_id, draft_id):
        _require_owner(owner_id)
        _require_draft_id(draft_id)
        row = (ClinicDictation.query.filter_by(id=draft_id, author_user_id=owner_id)
               .populate_existing().one_or_none())
        if row is None:
            raise DraftError("not_found")
        return row

    def _claim(self, dictation, version):
        """Compare-and-swap serializes saves and discards across processes."""
        result = db.session.execute(
            update(ClinicDictation).where(
                ClinicDictation.id == dictation.id,
                ClinicDictation.author_user_id == dictation.author_user_id,
                ClinicDictation.version_counter == version,
            ).values(version_counter=version + 1),
            execution_options={"synchronize_session": False},
        )
        if result.rowcount != 1:
            current = self.get(dictation.author_user_id, dictation.id)
            raise DraftConflict(current.version, current.text)
        db.session.expire(dictation, ["version_counter"])


def _utc(value):
    # SQLite returns naive values for DateTime(timezone=True).
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value
