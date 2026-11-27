"""Persist resolved routing targets without assigning a typist or granting rights.

Absence of a row means no explicit target, including for existing dictations.
Callers authorize reads and commit writes with their enclosing workflow transaction.
"""

import json

from sqlalchemy import CheckConstraint

from src.clinic import recipient_defaults as defaults
from src.clinic.models import ClinicDictation
from src.database import db


class ClinicRecipientTarget(db.Model):
    """One resolved snapshot per dictation, separate from workflow assignee state."""

    __tablename__ = "clinic_recipient_target"

    dictation_id = db.Column(
        db.String(36), db.ForeignKey("clinic_dictation.id"), primary_key=True
    )
    author_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    author_pool_id = db.Column(db.String(36), nullable=True)
    mode = db.Column(db.String(24), nullable=False)
    target_ids_json = db.Column(db.Text, nullable=False)
    recipient_user_ids_json = db.Column(db.Text, nullable=False)
    default_version = db.Column(db.Integer, nullable=False)

    __table_args__ = (
        CheckConstraint(
            "mode IN ('all_eligible','person','staff_pool','self_correction')",
            name="ck_clinic_recipient_target_mode",
        ),
        CheckConstraint("default_version >= 0", name="ck_clinic_recipient_target_version"),
    )

    @property
    def assignment(self) -> defaults.Assignment:
        return defaults.Assignment(
            author_user_id=self.author_user_id,
            author_pool_id=self.author_pool_id,
            mode=self.mode,
            target_ids=tuple(json.loads(self.target_ids_json)),
            recipient_user_ids=tuple(json.loads(self.recipient_user_ids_json)),
            default_version=self.default_version,
        )


def create_tables(bind=None) -> None:
    db.metadata.create_all(
        bind=bind or db.engine, tables=[ClinicRecipientTarget.__table__], checkfirst=True
    )


def get_target(dictation_id: str) -> defaults.Assignment | None:
    """Read the stored snapshot; never derive a target from today's defaults."""
    row = db.session.get(ClinicRecipientTarget, dictation_id)
    return None if row is None else row.assignment


def set_target(
    dictation_id: str, *, actor_id: int, override: dict | None = None,
    expected_version: int | None = None,
) -> defaults.Assignment:
    """Resolve and flush a target. The caller owns commit/rollback, never this helper."""
    dictation = db.session.get(
        ClinicDictation, dictation_id, populate_existing=True, with_for_update=True
    )
    if dictation is None:
        raise defaults.RecipientDefaultError("unknown_dictation")
    assignment = defaults.submit(
        dictation.author_user_id, dictation.pool_id, actor_id=actor_id,
        override=override, expected_version=expected_version,
    )
    row = db.session.get(ClinicRecipientTarget, dictation_id, populate_existing=True)
    if row is None:
        row = ClinicRecipientTarget(dictation_id=dictation_id)
        db.session.add(row)
    row.author_user_id = assignment.author_user_id
    row.author_pool_id = assignment.author_pool_id
    row.mode = assignment.mode
    row.target_ids_json = json.dumps(assignment.target_ids)
    row.recipient_user_ids_json = json.dumps(assignment.recipient_user_ids)
    row.default_version = assignment.default_version
    db.session.flush()
    return assignment
