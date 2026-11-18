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
from sqlalchemy import inspect, text
from sqlalchemy.sql.ddl import sort_tables

from src.app import app, db
from src.clinic import policy
from src.clinic.models import install_clinic_guards
from src.models import (
    APIToken, InternalShare, ProcessingJob, Recording, Share, SystemSetting, User,
)
from src.utils.token_auth import hash_token

from clinic_support import SECRET_MARKER


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
def world():
    """Authors a and b (same routing pool in a later work package), one admin.

    b's recording is also shared internally with a and publicly by link: every upstream path to it exists, the clinic policy must close them.
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
        share = Share(recording_id=rec_b.id, user_id=users['b'].id)
        internal = InternalShare(recording_id=rec_b.id, owner_id=users['b'].id,
                                 shared_with_user_id=users['a'].id, can_edit=True, can_reshare=True)
        plain = f'tok-{secrets.token_urlsafe(16)}'
        token = APIToken(user_id=users['a'].id, token_hash=hash_token(plain), name='clinic-test')
        job_a = ProcessingJob(user_id=users['a'].id, recording_id=rec_a.id, job_type='transcribe', status='queued')
        job_b = ProcessingJob(user_id=users['b'].id, recording_id=rec_b.id, job_type='transcribe', status='queued')
        # a failed new upload: DELETE on it removes its recording as well
        job_a_new = ProcessingJob(user_id=users['a'].id, recording_id=rec_a.id, job_type='summarize',
                                  status='failed', is_new_upload=True)
        db.session.add_all([share, internal, token, job_a, job_b, job_a_new])
        db.session.commit()
        w.update(job_a=job_a.id, job_b=job_b.id, job_a_new=job_a_new.id)
        w.update(rec_a=rec_a.id, rec_b=rec_b.id, public_id=share.public_id,
                 token=plain, token_id=token.id, internal_id=internal.id,
                 share_id=share.id)
    yield w
    with app.app_context():
        db.session.rollback()
        ProcessingJob.query.filter(ProcessingJob.recording_id.in_([w['rec_a'], w['rec_b']])).delete(
            synchronize_session=False)
        InternalShare.query.filter_by(recording_id=w['rec_b']).delete()
        Share.query.filter_by(recording_id=w['rec_b']).delete()
        APIToken.query.filter_by(user_id=w['user_a']).delete()
        for key in ('rec_a', 'rec_b'):
            obj = db.session.get(Recording, w[key])
            if obj is not None:
                db.session.delete(obj)
        SystemSetting.query.filter(SystemSetting.key.in_(
            ['enable_public_sharing', 'enable_internal_sharing'])).delete(synchronize_session=False)
        User.query.filter(User.id.in_([w['user_a'], w['user_b'], w['user_admin']])).delete(
            synchronize_session=False)
        db.session.commit()


_ORIGINAL_TABLE = "clinic_original_version"
_ORIGINAL_TRIGGERS = ("clinic_original_immutable", "clinic_original_no_delete")
_TRANSCRIPT_TABLE = "clinic_transcript_revision"
_TRANSCRIPT_TRIGGERS = ("clinic_transcript_immutable", "clinic_transcript_no_delete")


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
            ):
                conn.execute(text("DROP TRIGGER IF EXISTS " + trigger))
        elif dialect == "postgresql":
            for table_name in (_ORIGINAL_TABLE, _TRANSCRIPT_TABLE, "clinic_archive_event"):
                conn.execute(text("ALTER TABLE " + table_name + " DISABLE TRIGGER USER"))
        for table in tables:
            conn.execute(table.delete())
        if dialect == "postgresql":
            for table_name in (_ORIGINAL_TABLE, _TRANSCRIPT_TABLE, "clinic_archive_event"):
                conn.execute(text("ALTER TABLE " + table_name + " ENABLE TRIGGER USER"))
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
