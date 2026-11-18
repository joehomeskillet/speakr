"""
Audit models.

``AuditEvent`` is the access audit log: one row per recorded event (sign-in,
access to a recording, change, administration). It is written only
through ``src/services/audit.py``.
"""

from datetime import datetime

from sqlalchemy import event as sa_event

from src.database import db


class AuditEvent(db.Model):
    """One event of the access audit log.

    No foreign keys and no relationships on purpose: a row has to remain after
    the recording, the account or the group it names is deleted. The ids of
    deleted rows can be given out again on SQLite, so a row is read together
    with the deletion events that follow it (docs/admin-guide/audit-log.md).

    Rows are never updated and are deleted only by the pruning statement in
    ``src/services/audit.py``; the listeners below refuse both through the ORM.
    """

    __tablename__ = 'audit_event'

    # AUTOINCREMENT on SQLite, a sequence on PostgreSQL: an id is never given
    # out again, so removed rows remain visible as gaps.
    id = db.Column(db.BigInteger().with_variant(db.Integer, 'sqlite'), primary_key=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    event = db.Column(db.String(48), nullable=False)
    event_group = db.Column(db.String(12), nullable=False)
    actor_kind = db.Column(db.String(12), nullable=False)
    actor_user_id = db.Column(db.Integer, nullable=True)
    actor_token_id = db.Column(db.Integer, nullable=True)
    ip = db.Column(db.String(45), nullable=True)
    user_agent = db.Column(db.String(160), nullable=True)
    recording_id = db.Column(db.Integer, nullable=True)
    recording_owner_id = db.Column(db.Integer, nullable=True)
    target_user_id = db.Column(db.Integer, nullable=True)
    group_id = db.Column(db.Integer, nullable=True)
    outcome = db.Column(db.String(8), nullable=False, default='success')
    details = db.Column(db.JSON, nullable=True)
    dedup_key = db.Column(db.String(80), nullable=True)

    __table_args__ = (
        db.Index('ix_audit_event_created_at', 'created_at'),
        db.Index('ix_audit_event_actor_created', 'actor_user_id', 'created_at'),
        db.Index('ix_audit_event_recording_created', 'recording_id', 'created_at'),
        db.Index('ix_audit_event_target_created', 'target_user_id', 'created_at'),
        db.Index('ix_audit_event_group_created', 'event_group', 'created_at'),
        db.Index('ux_audit_event_dedup_key', 'dedup_key', unique=True),
        {'sqlite_autoincrement': True},
    )

    def to_dict(self):
        return {
            'id': self.id,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'event': self.event,
            'event_group': self.event_group,
            'actor_kind': self.actor_kind,
            'actor_user_id': self.actor_user_id,
            'actor_token_id': self.actor_token_id,
            'ip': self.ip,
            'user_agent': self.user_agent,
            'recording_id': self.recording_id,
            'recording_owner_id': self.recording_owner_id,
            'target_user_id': self.target_user_id,
            'group_id': self.group_id,
            'outcome': self.outcome,
            'details': self.details,
        }


@sa_event.listens_for(AuditEvent, 'before_update')
def _refuse_audit_update(mapper, connection, target):
    raise RuntimeError('Audit events are append-only and cannot be updated')


@sa_event.listens_for(AuditEvent, 'before_delete')
def _refuse_audit_delete(mapper, connection, target):
    raise RuntimeError('Audit events are append-only and cannot be deleted')
