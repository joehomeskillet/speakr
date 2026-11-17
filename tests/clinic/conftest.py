"""Shared isolation for the clinic tests.

The app uses one database for the whole pytest session and the clinic services
commit, so a per-test rollback is not enough. ``clinic_isolation`` runs around
every test: it removes all clinic_* rows and the users and recordings the test
created. The immutability triggers on clinic_original_version are switched off
only for this clean-up and put back right after.
"""

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.sql.ddl import sort_tables

from src.app import app, db
from src.clinic.models import install_clinic_guards
from src.models import Recording, User

_ORIGINAL_TABLE = "clinic_original_version"
_ORIGINAL_TRIGGERS = ("clinic_original_immutable", "clinic_original_no_delete")


def _clinic_tables_children_first():
    present = set(inspect(db.engine).get_table_names())
    own = [
        table
        for table in db.metadata.tables.values()
        if table.name.startswith("clinic_") and table.name in present
    ]
    return list(reversed(sort_tables(own)))


def _purge_clinic_rows():
    db.session.rollback()
    tables = _clinic_tables_children_first()
    dialect = db.engine.dialect.name
    with db.engine.begin() as conn:
        if dialect == "sqlite":
            for trigger in (*_ORIGINAL_TRIGGERS, "clinic_lease_generation_monotone"):
                conn.execute(text("DROP TRIGGER IF EXISTS " + trigger))
        elif dialect == "postgresql":
            conn.execute(text("ALTER TABLE " + _ORIGINAL_TABLE + " DISABLE TRIGGER USER"))
        for table in tables:
            conn.execute(table.delete())
        if dialect == "postgresql":
            conn.execute(text("ALTER TABLE " + _ORIGINAL_TABLE + " ENABLE TRIGGER USER"))
    if dialect == "sqlite":
        install_clinic_guards(db.engine)


def _purge_new_accounts(user_ids, recording_ids):
    db.session.rollback()
    Recording.query.filter(~Recording.id.in_(recording_ids or [0])).delete(
        synchronize_session=False
    )
    User.query.filter(~User.id.in_(user_ids or [0])).delete(synchronize_session=False)
    db.session.commit()


@pytest.fixture(autouse=True)
def clinic_isolation():
    with app.app_context():
        _purge_clinic_rows()
        users = [row[0] for row in db.session.query(User.id).all()]
        recordings = [row[0] for row in db.session.query(Recording.id).all()]
    yield
    with app.app_context():
        _purge_clinic_rows()
        _purge_new_accounts(users, recordings)
