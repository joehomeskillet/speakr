"""Notes writes need owner or edit access (security hotfix 2026-11-18).

With sharing removed, the personal-notes branch is gone and a notes write goes
to the recording row itself. A user with mere view access (today none exists
without ownership; the clinic access policy will add pool/group viewers) must
not overwrite the owner's notes. The guard is tested directly by granting view
access without edit access through the access check.
"""
import os
import sys
import uuid
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pytest

from src.app import app, db
from src.models import Recording, User

app.config['WTF_CSRF_ENABLED'] = False

NOTES = 'owner notes, must survive'


@pytest.fixture
def world():
    with app.app_context():
        s = uuid.uuid4().hex[:8]
        owner = User(username=f'nw_o_{s}', email=f'nw_o_{s}@local.test', password='x')
        viewer = User(username=f'nw_v_{s}', email=f'nw_v_{s}@local.test', password='x')
        db.session.add_all([owner, viewer])
        db.session.commit()
        rec = Recording(user_id=owner.id, title='r', audio_path='/tmp/nw.mp3',
                        status='COMPLETED', notes=NOTES)
        db.session.add(rec)
        db.session.commit()
        ids = dict(owner=owner.id, viewer=viewer.id, rec=rec.id)
        try:
            yield ids
        finally:
            db.session.rollback()
            Recording.query.filter_by(id=ids['rec']).delete()
            User.query.filter(User.id.in_([ids['owner'], ids['viewer']])).delete(
                synchronize_session=False)
            db.session.commit()


def _client(user_id):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['_user_id'] = str(user_id)
        sess['_fresh'] = True
    return client


def _view_only_access(recording, user, require_edit=False):
    """Grant everyone view access and nobody edit access."""
    return not require_edit


def _notes(rec_id):
    with app.app_context():
        return db.session.get(Recording, rec_id).notes


def test_owner_writes_notes(world):
    resp = _client(world['owner']).post(
        '/save', json={'id': world['rec'], 'notes': 'owner update'})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    assert _notes(world['rec']) == 'owner update'


def test_view_only_user_cannot_write_notes_via_save_metadata(world):
    with patch('src.api.recordings.has_recording_access', side_effect=_view_only_access):
        resp = _client(world['viewer']).post(
            '/save', json={'id': world['rec'], 'notes': 'overwritten'})
    assert resp.status_code == 403, resp.get_data(as_text=True)
    assert _notes(world['rec']) == NOTES


def test_view_only_user_cannot_write_notes_via_api_v1(world):
    with app.app_context():
        import secrets
        from src.models import APIToken
        from src.utils.token_auth import hash_token
        plain = f'tok-{secrets.token_urlsafe(16)}'
        token = APIToken(user_id=world['viewer'], token_hash=hash_token(plain),
                         name='nw', scopes='read write')
        db.session.add(token)
        db.session.commit()
        token_id = token.id
    try:
        with patch('src.api.api_v1.has_recording_access', side_effect=_view_only_access):
            resp = app.test_client().put(
                f"/api/v1/recordings/{world['rec']}/notes",
                json={'notes': 'overwritten'},
                headers={'Authorization': f'Bearer {plain}'})
        assert resp.status_code == 403, resp.get_data(as_text=True)
        assert _notes(world['rec']) == NOTES
    finally:
        with app.app_context():
            APIToken.query.filter_by(id=token_id).delete()
            db.session.commit()


def test_without_any_access_the_recording_stays_hidden(world):
    resp = _client(world['viewer']).post(
        '/save', json={'id': world['rec'], 'notes': 'x'})
    assert resp.status_code == 404
    assert _notes(world['rec']) == NOTES
