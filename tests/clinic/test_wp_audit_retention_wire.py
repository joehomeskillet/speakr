"""w119: clinic append-only semantics for ordinary audit pruning.

Synthetic rows only. With CLINIC_MODE on, prune_audit_events removes nothing
and reports the retention boundary, also with a database guard installed.
With it off, upstream pruning is unchanged. The separately privileged retention
process is only planned, never executed.
"""

import logging
import secrets
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import DBAPIError

from src.app import app, db
from src.models import AuditEvent
from src.services import audit

AGE_DAYS = 4000
KEEP_DAYS = 3650
_GUARD = 'w119_audit_event_no_delete'


def _marker():
    return 10 ** 9 + secrets.randbelow(10 ** 8)


def _append(marker, days=AGE_DAYS, event_name='admin.user.unlocked'):
    details = {'rule': 'synthetic'} if event_name == 'recording.archived' else None
    row = audit.record_in_tx(event_name, target_user_id=marker, force=True, actor_kind='system',
                             details=details, created_at=datetime.utcnow() - timedelta(days=days))
    db.session.commit()
    return row.id


def _present(ids):
    db.session.expire_all()
    return [i for i in ids if db.session.get(AuditEvent, i) is not None]


@pytest.fixture
def ctx(monkeypatch):
    monkeypatch.setattr(audit, 'retention_days', lambda: KEEP_DAYS)
    with app.app_context():
        yield
        db.session.rollback()


@pytest.fixture
def delete_guard(ctx):
    """A database-level guard on audit_event. On SQLite a trigger stands in for
    the PostgreSQL application role, which has no delete privilege there."""
    sqlite = db.engine.dialect.name == 'sqlite'
    if sqlite:
        with db.engine.begin() as conn:
            conn.execute(text(f'DROP TRIGGER IF EXISTS {_GUARD}'))
            conn.execute(text(f'CREATE TRIGGER {_GUARD} BEFORE DELETE ON audit_event FOR EACH ROW '
                              "BEGIN SELECT RAISE(ABORT, 'audit_event is append-only'); END"))
    try:
        yield
    finally:
        if sqlite:
            with db.engine.begin() as conn:
                conn.execute(text(f'DROP TRIGGER IF EXISTS {_GUARD}'))


def _policy(**overrides):
    values = dict(policy_id='synthetic-audit-retention', version=2, retention_days=KEEP_DAYS,
                  approved_by='synthetic-dpo', approved_at=datetime.utcnow() - timedelta(days=1))
    values.update(overrides)
    return audit.RetentionPolicy(**values)


def test_clinic_pruning_keeps_aged_rows_and_reports_boundary(clinic_on, ctx, caplog):
    marker = _marker()
    ids = [_append(marker), _append(marker, days=AGE_DAYS + 1), _append(marker, days=1)]
    with caplog.at_level(logging.INFO, logger='speakr.audit'):
        assert audit.prune_audit_events() == 0
    assert _present(ids) == ids
    rows = db.session.execute(audit.filtered_query({'user_id': marker})).all()
    assert sorted(r.id for r in rows) == sorted(ids)
    assert not db.session.execute(audit.filtered_query({'event': 'audit.pruned'}).where(
        AuditEvent.__table__.c.id > ids[0])).first()

    report = audit.retention_boundary()
    assert report['boundary'] == audit.RETENTION_BOUNDARY == 'separate_privileged_retention'
    assert report['deleted'] == 0 and report['retention_days'] == KEEP_DAYS
    assert report['withheld'] >= 2 and report['cutoff']
    assert any('separate_privileged_retention' in r.getMessage() for r in caplog.records)


def test_clinic_pruning_issues_no_delete_with_guard_installed(clinic_on, delete_guard):
    marker = _marker()
    ids = [_append(marker), _append(marker, event_name='recording.archived')]
    statements = []

    def capture(conn, cursor, statement, *args):
        statements.append(statement)

    event.listen(db.engine, 'before_cursor_execute', capture)
    try:
        # Retry: a second run behaves exactly like the first.
        assert audit.prune_audit_events() == 0
        assert audit.prune_audit_events(now=datetime.utcnow() + timedelta(days=365)) == 0
    finally:
        event.remove(db.engine, 'before_cursor_execute', capture)
    assert statements and not [s for s in statements if s.lstrip().upper().startswith('DELETE')]
    assert _present(ids) == ids

    # The guard is real: a raw mutation is refused and the row stays.
    with pytest.raises(DBAPIError, match='append-only|permission denied'):
        db.session.execute(text('DELETE FROM audit_event WHERE id = :id'), {'id': ids[0]})
    db.session.rollback()
    assert _present(ids) == ids


def test_upstream_pruning_unchanged_when_clinic_off(clinic_off, ctx):
    marker = _marker()
    aged, archived, recent = (_append(marker), _append(marker, event_name='recording.archived'),
                              _append(marker, days=1))
    removed = audit.prune_audit_events()
    assert removed >= 1
    assert _present([aged, archived, recent]) == [archived, recent]
    pruned = db.session.execute(audit.filtered_query({'event': 'audit.pruned'}).where(
        AuditEvent.__table__.c.id > recent)).all()
    assert len(pruned) == 1 and pruned[0].details['count'] == removed


@pytest.mark.parametrize('policy', [
    pytest.param(None, id='no-policy'),
    pytest.param({'policy_id': 'x', 'version': 2}, id='not-a-policy'),
    pytest.param(_policy(approved_by=''), id='no-approver'),
    pytest.param(_policy(policy_id=''), id='no-id'),
    pytest.param(_policy(approved_at=None), id='no-approval-date'),
    pytest.param(_policy(approved_at=datetime.utcnow() + timedelta(days=30)), id='future-approval'),
])
def test_privileged_plan_refuses_unapproved_policy(ctx, policy):
    with pytest.raises(audit.RetentionPolicyRejected) as info:
        audit.plan_privileged_retention(policy, reviewed_version=2)
    assert info.value.code == 'policy_not_approved'


def test_privileged_plan_refuses_stale_version(ctx):
    with pytest.raises(audit.RetentionPolicyRejected) as info:
        audit.plan_privileged_retention(_policy(version=3), reviewed_version=2)
    assert info.value.code == 'stale_version'


@pytest.mark.parametrize('overrides,configured,code', [
    pytest.param({'retention_days': KEEP_DAYS - 1}, KEEP_DAYS, 'shorter_than_configured', id='shorter'),
    pytest.param({}, 0, 'shorter_than_configured', id='configured-keep-all'),
    pytest.param({'retention_days': 0}, KEEP_DAYS, 'bad_retention', id='zero-days'),
    pytest.param({'retention_days': True}, KEEP_DAYS, 'bad_retention', id='bool-days'),
    pytest.param({'version': 0}, KEEP_DAYS, 'bad_version', id='bad-version'),
])
def test_privileged_plan_never_releases_earlier_than_configured(ctx, monkeypatch, overrides,
                                                                 configured, code):
    monkeypatch.setattr(audit, 'retention_days', lambda: configured)
    version = overrides.get('version', 2)
    with pytest.raises(audit.RetentionPolicyRejected) as info:
        audit.plan_privileged_retention(_policy(**overrides), reviewed_version=version)
    assert info.value.code == code


def test_privileged_plan_counts_without_deleting(clinic_on, delete_guard):
    marker = _marker()
    ids = [_append(marker), _append(marker), _append(marker, event_name='recording.archived'),
           _append(marker, days=1)]
    aware = _policy(approved_at=datetime.now(timezone.utc) - timedelta(hours=1))
    now = datetime.utcnow()
    first = audit.plan_privileged_retention(aware, reviewed_version=2, now=now)
    again = audit.plan_privileged_retention(aware, reviewed_version=2, now=now)
    assert first == again
    assert first['performed_deletion'] is False
    assert first['policy_id'] == 'synthetic-audit-retention' and first['version'] == 2
    assert first['retention_days'] == KEEP_DAYS and first['eligible'] >= 2
    assert _present(ids) == ids
