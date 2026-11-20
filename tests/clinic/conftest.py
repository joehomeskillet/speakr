"""Fixtures for the clinic tests.

SHARED-DB: the app uses one throwaway database for the whole pytest session and
the clinic services commit, so a per-test rollback is not enough. Every fixture
removes the rows it made, and ``clinic_isolation`` runs around every test: it
removes all clinic_* rows and the users and recordings the test created. The
immutability triggers on clinic_original_version are switched off only for this
clean-up and put back right after.
"""

import secrets

import pytest
from sqlalchemy import inspect, select, text

from sqlalchemy.sql.ddl import sort_tables

from src.app import app, db
from src.clinic import policy
from src.clinic.models import install_clinic_guards
from src.services import audit
from src.clinic import registry as _clinic_registry  # noqa: F401  (all clinic tables on the metadata)
from src.clinic.append_only_guards import (
    APPEND_ONLY_TABLES,
    install_append_only_guards,
    trigger_names,
)
from src.models import (
    APIToken, AuditEvent, ProcessingJob, Recording, RecordingTag, SystemSetting, Tag, User,
)
from src.utils.token_auth import hash_token

from clinic_support import SECRET_MARKER

# Claim takes a queued row and the orphan sweep requeues a processing row.
# Each pytest process has its own database (root conftest), so a file lock
# cannot hide rows an earlier module in this process left behind. Park those
# rows before the test creates its own, and leave them parked.
_QUEUE_DB_MODULES = (
    'test_job_lease', 'test_job_queue_owner', 'test_llm_decoupling',
    'test_job_failed_on_recording_failed',
)


def _retire_foreign_jobs():
    with app.app_context():
        ProcessingJob.query.filter(
            ProcessingJob.status.in_(('queued', 'processing'))
        ).update({'status': 'failed'}, synchronize_session=False)
        db.session.commit()


@pytest.fixture(autouse=True)
def _queue_table_quiet(request):
    """No claimable or sweepable ProcessingJob rows from an earlier module."""
    name = request.module.__name__.rsplit('.', 1)[-1]
    if name in _QUEUE_DB_MODULES:
        _retire_foreign_jobs()
    yield


@pytest.fixture
def clinic_on(monkeypatch):
    """Clinic mode on, CSRF off (the tests post JSON without a token)."""
    monkeypatch.setenv('CLINIC_MODE', '1')
    monkeypatch.setitem(app.config, 'WTF_CSRF_ENABLED', False)


@pytest.fixture
def clinic_off(monkeypatch):
    monkeypatch.delenv('CLINIC_MODE', raising=False)
    monkeypatch.setitem(app.config, 'WTF_CSRF_ENABLED', False)


@pytest.fixture(autouse=True)
def _no_policy_providers():
    policy.clear_providers()
    yield
    policy.clear_providers()


@pytest.fixture
def world(_queue_table_quiet):
    """Authors a and b (same routing pool in a later work package), one admin.

    Each author owns one recording. The clinic policy must close every remaining
    upstream path to the other author's recording.
    """
    w = {}
    with app.app_context():
        tag = secrets.token_hex(4)
        users = {}
        for name, admin in (('a', False), ('b', False), ('admin', True)):
            user = User(username=f'cl_{name}_{tag}', email=f'cl_{name}_{tag}@local.test',
                        password='x', is_admin=admin)
            db.session.add(user)
            users[name] = user
        db.session.commit()
        w.update({f'user_{k}': v.id for k, v in users.items()})
        rec_a = Recording(user_id=users['a'].id, title='A own dictation', status='COMPLETED',
                          original_filename='a.wav', transcription=f'{SECRET_MARKER}A')
        rec_b = Recording(user_id=users['b'].id, title='B dictation', status='COMPLETED',
                          original_filename='b.wav', transcription=f'{SECRET_MARKER}B',
                          summary=f'{SECRET_MARKER}B-summary')
        db.session.add_all([rec_a, rec_b])
        db.session.commit()
        plain = f'tok-{secrets.token_urlsafe(16)}'
        token = APIToken(user_id=users['a'].id, token_hash=hash_token(plain), name='clinic-test')
        job_a = ProcessingJob(user_id=users['a'].id, recording_id=rec_a.id, job_type='transcribe', status='queued')
        job_b = ProcessingJob(user_id=users['b'].id, recording_id=rec_b.id, job_type='transcribe', status='queued')
        # a failed new upload: DELETE on it removes its recording as well
        job_a_new = ProcessingJob(user_id=users['a'].id, recording_id=rec_a.id, job_type='summarize',
                                  status='failed', is_new_upload=True)
        db.session.add_all([token, job_a, job_b, job_a_new])
        db.session.commit()
        w.update(job_a=job_a.id, job_b=job_b.id, job_a_new=job_a_new.id)
        w.update(rec_a=rec_a.id, rec_b=rec_b.id, token=plain, token_id=token.id)
    yield w
    with app.app_context():
        db.session.rollback()
        if db.engine.dialect.name == "postgresql":
            _purge_clinic_rows()  # foreign keys are enforced: clinic rows first
        ProcessingJob.query.filter(ProcessingJob.recording_id.in_([w['rec_a'], w['rec_b']])).delete(
            synchronize_session=False)
        APIToken.query.filter_by(user_id=w['user_a']).delete()
        for key in ('rec_a', 'rec_b'):
            obj = db.session.get(Recording, w[key])
            if obj is not None:
                db.session.delete(obj)
        SystemSetting.query.filter(SystemSetting.key.in_(
            ['enable_internal_sharing'])).delete(synchronize_session=False)
        User.query.filter(User.id.in_([w['user_a'], w['user_b'], w['user_admin']])).delete(
            synchronize_session=False)
        db.session.commit()


_ORIGINAL_TABLE = "clinic_original_version"
_ORIGINAL_TRIGGERS = ("clinic_original_immutable", "clinic_original_no_delete")
_TRANSCRIPT_TABLE = "clinic_transcript_revision"
_TRANSCRIPT_TRIGGERS = ("clinic_transcript_immutable", "clinic_transcript_no_delete")


def _guarded_tables():
    present = set(inspect(db.engine).get_table_names())
    return [
        name
        for name in (_ORIGINAL_TABLE, _TRANSCRIPT_TABLE, "clinic_archive_event", *APPEND_ONLY_TABLES)
        if name in present
    ]


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
            for trigger in (
                *_ORIGINAL_TRIGGERS,
                *_TRANSCRIPT_TRIGGERS,
                "clinic_archive_no_update",
                "clinic_archive_no_delete",
                "clinic_lease_generation_monotone",
                *(name for table in APPEND_ONLY_TABLES for name in trigger_names(table)),
            ):
                conn.execute(text("DROP TRIGGER IF EXISTS " + trigger))
        elif dialect == "postgresql":
            for table_name in _guarded_tables():
                conn.execute(text("ALTER TABLE " + table_name + " DISABLE TRIGGER USER"))
        for table in tables:
            conn.execute(table.delete())
        if dialect == "postgresql":
            for table_name in _guarded_tables():
                conn.execute(text("ALTER TABLE " + table_name + " ENABLE TRIGGER USER"))
    if dialect == "sqlite":
        install_clinic_guards(db.engine)
        install_append_only_guards(db.engine)


def _purge_upstream_dependents(user_ids, recording_ids):
    """PostgreSQL enforces foreign keys that SQLite ignores: remove the upstream
    rows (usage, shares, tags and so on) that point at accounts or recordings
    this test created, children first, before those parents are deleted."""
    own = {"user": user_ids or [0], "recording": recording_ids or [0]}
    skip = {"user", "recording"}
    tables = [t for t in db.metadata.tables.values()
              if not t.name.startswith("clinic_") and t.name not in skip]
    with db.engine.begin() as conn:
        for table in reversed(sort_tables(tables)):
            for fk in table.foreign_keys:
                parent = fk.column.table.name
                if parent in own and fk.column.name == "id":
                    conn.execute(table.delete().where(fk.parent.notin_(own[parent])))


def _purge_audit_rows():
    """Audit rows outlive their test while SQLite reuses the ids of deleted users and
    recordings, so a later test that filters by actor or recording id sees the rows (and
    dedup keys) of an earlier one. Drain the writer queue first so that no row of this
    process arrives after the purge; reset the in-memory dedup map as well."""
    audit.flush()
    db.session.rollback()
    AuditEvent.query.delete(synchronize_session=False)
    db.session.commit()
    audit.reset_memory()


def _purge_orphan_jobs():
    """SQLite does not enforce foreign keys: a job left behind for a deleted recording
    would collide on (recording_id, job_type) when a later test reuses that recording id.
    Tags of deleted accounts and their recording links leak into later tests the same way."""
    db.session.rollback()
    ProcessingJob.query.filter(ProcessingJob.recording_id.notin_(select(Recording.id))).delete(
        synchronize_session=False
    )
    RecordingTag.query.filter(RecordingTag.recording_id.notin_(select(Recording.id))).delete(
        synchronize_session=False
    )
    RecordingTag.query.filter(
        RecordingTag.tag_id.in_(select(Tag.id).where(Tag.user_id.notin_(select(User.id))))
    ).delete(synchronize_session=False)
    Tag.query.filter(Tag.user_id.notin_(select(User.id))).delete(synchronize_session=False)
    db.session.commit()


def _purge_new_accounts(user_ids, recording_ids):
    db.session.rollback()
    if db.engine.dialect.name == "postgresql":
        _purge_upstream_dependents(user_ids, recording_ids)
    Recording.query.filter(~Recording.id.in_(recording_ids or [0])).delete(
        synchronize_session=False
    )
    db.session.commit()
    User.query.filter(~User.id.in_(user_ids or [0])).delete(synchronize_session=False)
    db.session.commit()
    _purge_orphan_jobs()


def _ensure_issuer_user():
    """PostgreSQL enforces foreign keys: tests name user id 1 as the issuer of
    recovery tokens. Keep one such account for the session (SQLite does not check)."""
    if db.engine.dialect.name != "postgresql" or db.session.get(User, 1) is not None:
        return
    db.session.add(User(id=1, username="clinic_issuer", email="clinic_issuer@local.test",
                        password="x", is_admin=True))
    db.session.commit()
    db.session.execute(text(
        "SELECT setval(pg_get_serial_sequence('\"user\"', 'id'), (SELECT MAX(id) FROM \"user\"))"))
    db.session.commit()


@pytest.fixture(scope="session", autouse=True)
def _clinic_tables_exist():
    """Every clinic table exists before the first test of the process. Several test
    modules create their tables lazily (db.create_all in their own fixture), so a module
    that needs one it does not create passed or failed with the order xdist chose."""
    with app.app_context():
        db.create_all()
    yield


@pytest.fixture(autouse=True)
def clinic_isolation():
    with app.app_context():
        _purge_clinic_rows()
        _purge_audit_rows()
        _purge_orphan_jobs()
        _ensure_issuer_user()
        users = [row[0] for row in db.session.query(User.id).all()]
        recordings = [row[0] for row in db.session.query(Recording.id).all()]
    yield
    with app.app_context():
        _purge_clinic_rows()
        _purge_audit_rows()
        _purge_new_accounts(users, recordings)
