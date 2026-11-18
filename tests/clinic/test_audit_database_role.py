"""Raw audit mutations through the configured app login on disposable PostgreSQL."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from src.app import app, db
from src.services import audit

pytestmark = pytest.mark.skipif(
    os.environ.get('CLINIC_AUDIT_DB_PROOF') != '1',
    reason='requires CLINIC_AUDIT_DB_PROOF=1 and disposable PostgreSQL in PYTEST_DATABASE_URI',
)

_READ = text('SELECT event, event_group, outcome FROM audit_event WHERE id = :id')
_EVENT = ('recording.downloaded', 'access', 'success')


@pytest.fixture(scope='module', autouse=True)
def app_role() -> None:
    with app.app_context(), db.engine.connect() as conn:
        assert conn.dialect.name == 'postgresql', 'SQLite cannot prove PostgreSQL role enforcement'
        role = conn.execute(text('''
            SELECT current_setting('server_version') AS version, current_database() AS database,
                   session_user AS login, current_user AS effective_role,
                   rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls
            FROM pg_roles WHERE rolname = current_user
        ''')).mappings().one()
        assert role.login == role.effective_role == db.engine.url.username
        assert role.database == db.engine.url.database
        assert not role.rolsuper, 'a superuser session cannot prove the application-role boundary'
        grants = conn.execute(text('''
            SELECT pg_get_userbyid(c.relowner) AS owner,
                   c.relacl::text AS acl, c.relrowsecurity AS rls,
                   ARRAY(SELECT p FROM unnest(ARRAY['SELECT', 'INSERT', 'UPDATE', 'DELETE',
                         'TRUNCATE', 'REFERENCES', 'TRIGGER']) AS p
                         WHERE has_table_privilege(c.oid, p)) AS table_privileges,
                   ARRAY(SELECT p FROM unnest(ARRAY['USAGE', 'SELECT', 'UPDATE']) AS p
                         WHERE has_sequence_privilege(pg_get_serial_sequence('audit_event', 'id'), p))
                         AS id_sequence_privileges,
                   ARRAY(SELECT tgname FROM pg_trigger WHERE tgrelid = c.oid AND NOT tgisinternal)
                         AS user_triggers
            FROM pg_class AS c WHERE c.oid = 'audit_event'::regclass
        ''')).mappings().one()
        print(f'PostgreSQL application connection: {dict(role)}; audit_event: {dict(grants)}')


def _append_event() -> int:
    row = audit.record_in_tx(_EVENT[0], force=True, actor_kind='system', recording_id=641)
    assert row is not None
    db.session.flush()
    return row.id


def test_app_role_appends_and_reads_after_rollback() -> None:
    with app.app_context():
        rolled_back_id = _append_event()
        db.session.rollback()
        assert db.session.execute(_READ, {'id': rolled_back_id}).one_or_none() is None

        retry_id = _append_event()
        db.session.commit()
        assert db.session.execute(_READ, {'id': retry_id}).one() == _EVENT


@pytest.mark.parametrize('statement', [
    pytest.param("UPDATE audit_event SET outcome = 'failed' WHERE id = :id", id='update'),
    pytest.param('DELETE FROM audit_event WHERE id = :id', id='delete'),
])
def test_app_role_cannot_mutate_committed_audit_event(statement: str) -> None:
    with app.app_context():
        params = {'id': _append_event()}
        db.session.commit()
        with db.engine.connect() as conn:
            assert conn.execute(_READ, params).one() == _EVENT
            # The target exists. Always roll back the probe, including a successful mutation.
            probe = conn.begin_nested()
            try:
                result = conn.execute(text(statement), params)
                observed = f'accepted; rowcount={result.rowcount}'
                sqlstate = None
            except DBAPIError as exc:
                sqlstate = getattr(exc.orig, 'sqlstate', None) or getattr(exc.orig, 'pgcode', None)
                observed = f'rejected; SQLSTATE={sqlstate}'
            finally:
                probe.rollback()
            assert conn.execute(_READ, params).one() == _EVENT

        operation = statement.split()[0]
        print(f'{operation}: {observed}')
        assert sqlstate in {'42501', '23514'}, f'Application role {operation}: {observed}'
