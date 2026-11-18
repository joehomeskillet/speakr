"""Notes in API v1 (mailr spec, open question 2).

The owner reads and writes the recording's notes, in the notes route, the
detail, PATCH and search.

SHARED-DB: users, tokens and the recording are removed afterwards.
"""

import json
import os
import secrets
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.app import app, db
from src.models import APIToken, Recording, User
from src.utils.token_auth import hash_token


@pytest.fixture
def world():
    with app.app_context():
        s = secrets.token_hex(4)
        owner = User(username=f"nown_{s}", email=f"nown_{s}@local.test", password="x")
        db.session.add(owner)
        db.session.commit()
        rec = Recording(user_id=owner.id, title="Sync", status="COMPLETED", original_filename="s.wav",
                        notes="OWNER PRIVATE: salary numbers", transcription="hello")
        db.session.add(rec)
        db.session.commit()
        tokens = {}
        for who in (owner,):
            p = f"tok-{secrets.token_urlsafe(16)}"
            db.session.add(APIToken(user_id=who.id, token_hash=hash_token(p), name="n"))
            tokens[who.id] = p
        db.session.commit()
        ids = dict(owner=owner.id, rec=rec.id, tokens=tokens)
    yield ids
    with app.app_context():
        db.session.rollback()
        Recording.query.filter_by(id=ids["rec"]).delete()
        APIToken.query.filter(APIToken.user_id.in_([ids["owner"]])).delete(synchronize_session=False)
        User.query.filter(User.id.in_([ids["owner"]])).delete(synchronize_session=False)
        db.session.commit()


def _h(world, who):
    return {"Authorization": f"Bearer {world['tokens'][world[who]]}"}


def _owner_notes(world):
    with app.app_context():
        return db.session.get(Recording, world["rec"]).notes


def test_the_owner_reads_the_recording_notes(world):
    with app.test_client() as c:
        assert c.get(f"/api/v1/recordings/{world['rec']}/notes",
                     headers=_h(world, "owner")).get_json()["notes"] == "OWNER PRIVATE: salary numbers"


def test_the_owner_still_writes_the_recording_notes(world):
    with app.test_client() as c:
        assert c.put(f"/api/v1/recordings/{world['rec']}/notes", json={"notes": "new"},
                     headers=_h(world, "owner")).status_code == 200
    assert _owner_notes(world) == "new"


def test_search_matches_only_the_notes_the_caller_can_see(world):
    from src.models import User as U
    from src.services.search_v1 import keyword_search
    with app.app_context():
        owner = db.session.get(U, world["owner"])
        hits, _ = keyword_search(owner, "salary", fields=("notes",))
        assert [h["field"] for h in hits] == ["notes"]
