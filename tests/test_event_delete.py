"""Deleting an event is an edit of the recording (#598).

DELETE /api/event/<id> is allowed to the owner of the recording only.

SHARED-DB: the users, recordings and events are removed afterwards.
"""

import os
import secrets
import sys
from datetime import datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.app import app, db
from src.models import Event, Recording, User

from tests.login_client import _client

app.config["WTF_CSRF_ENABLED"] = False


@pytest.fixture
def world():
    with app.app_context():
        s = secrets.token_hex(4)
        owner, outsider = (User(username=f"ed{k}_{s}", email=f"ed{k}_{s}@local.test", password="x")
                           for k in ("o", "x"))
        db.session.add_all([owner, outsider])
        db.session.commit()
        rec = Recording(user_id=owner.id, title="Sync", status="COMPLETED", original_filename="s.wav")
        db.session.add(rec)
        db.session.commit()
        events = [Event(recording_id=rec.id, title=f"Review {i}", start_datetime=datetime(2026, 11, 20, 9 + i))
                  for i in range(3)]
        db.session.add_all(events)
        db.session.commit()
        yield dict(owner=owner, outsider=outsider, rec=rec.id,
                   events=[e.id for e in events])
        db.session.rollback()
        Event.query.filter_by(recording_id=rec.id).delete()
        Recording.query.filter_by(id=rec.id).delete()
        User.query.filter(User.id.in_([owner.id, outsider.id])).delete(synchronize_session=False)
        db.session.commit()


def _exists(event_id):
    db.session.expire_all()
    return db.session.get(Event, event_id) is not None


def test_outsider_delete_is_not_found(world):
    w = world
    event = w["events"][0]
    resp = _client(w["outsider"]).delete(f"/api/event/{event}")
    assert resp.status_code == 404
    assert resp.get_json()["code"] == "event_not_found"
    assert _exists(event)


def test_owner_delete_removes_the_event(world):
    w = world
    event = w["events"][1]
    resp = _client(w["owner"]).delete(f"/api/event/{event}")
    assert resp.status_code == 200
    assert resp.get_json() == {"success": True}
    assert not _exists(event)
    assert _exists(w["events"][0])
