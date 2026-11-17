"""Fixtures for the clinic tests.

SHARED-DB: the app uses one throwaway SQLite database for the whole session, so
every fixture removes the rows it made.
"""

import secrets
from datetime import datetime

import pytest

from src.app import app, db
from src.clinic import policy
from src.models import (
    APIToken, Event, InternalShare, Recording, Share, SystemSetting, User,
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

    b's recording is also shared internally with a and publicly by link, and has
    an event: every upstream path to it exists, the clinic policy must close them.
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
        event = Event(recording_id=rec_b.id, title='B event', start_datetime=datetime(2030, 1, 1, 9, 0))
        event_a = Event(recording_id=rec_a.id, title='A event', start_datetime=datetime(2030, 1, 2, 9, 0))
        plain = f'tok-{secrets.token_urlsafe(16)}'
        token = APIToken(user_id=users['a'].id, token_hash=hash_token(plain), name='clinic-test')
        db.session.add_all([share, internal, event, event_a, token])
        db.session.commit()
        w.update(rec_a=rec_a.id, rec_b=rec_b.id, public_id=share.public_id, event_a=event_a.id,
                 event_b=event.id, token=plain, token_id=token.id, internal_id=internal.id,
                 share_id=share.id)
    yield w
    with app.app_context():
        db.session.rollback()
        Event.query.filter(Event.recording_id.in_([w['rec_a'], w['rec_b']])).delete(
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

