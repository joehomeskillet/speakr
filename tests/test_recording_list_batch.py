"""GET /api/recordings: SQL cost per page (D#483).

The paginated list used to run several queries per row while serializing
it. The statement tests count every SQL statement of one request and require
the same count for 25 and for 100 rows, under STATEMENT_BOUND; the v1 list,
GET /recordings (the full to_dict of every owned recording), the tag list and
the folder list are held to the same rule.

SHARED-DB: everything is created under fresh users and removed afterwards;
assertions are scoped to this module's viewers.
"""

import os
import sys
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import event

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.app import app, db
from src.models import (
    APIToken, Event, ExportTemplate, Folder, Group, GroupMembership, NamingTemplate, Recording,
    RecordingTag, SystemSetting, Tag, User,
)
from src.utils.token_auth import hash_token

app.config["WTF_CSRF_ENABLED"] = False

_PREFIX = "lstb"

# Statements for one page, whatever its size: the login user, the count, the
# page, tags and folders (one each), duplicates, group memberships, tag counts,
# folder counts and the group-admin check. 20 leaves room for a lookup or two,
# while one per-row query would add at least 25.
STATEMENT_BOUND = 20


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

@contextmanager
def count_statements():
    """Collect every SQL statement this thread sends to the database inside the block.

    The test client serves the request in the calling thread. Statements of
    background threads (the webhook dispatcher polls the same engine) are
    left out, so a poll during the request does not change the count.
    """
    statements = []
    thread = threading.get_ident()

    def _before(conn, cursor, statement, parameters, context, executemany):
        if threading.get_ident() == thread:
            statements.append(statement)

    with app.app_context():
        engine = db.engine
    event.listen(engine, "before_cursor_execute", _before)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", _before)


@contextmanager
def folders_on():
    real = SystemSetting.get_setting

    def get_setting(key, default_value=None):
        return True if key == 'enable_folders' else real(key, default_value)

    with patch.object(SystemSetting, "get_setting", staticmethod(get_setting)):
        yield


def _user(tag):
    s = uuid.uuid4().hex[:8]
    u = User(username=f"{_PREFIX}{tag}_{s}"[:20], email=f"{_PREFIX}{tag}_{s}@local.test", password="x")
    db.session.add(u)
    db.session.flush()
    return u


def _rec(user, title, marker, **kw):
    kw.setdefault("status", "COMPLETED")
    kw.setdefault("audio_path", "local://lstb/a.mp3")
    rec = Recording(user_id=user.id, title=title, participants=marker,
                    transcription="hello", summary="sum", original_filename="a.mp3", **kw)
    db.session.add(rec)
    db.session.flush()
    return rec


def _tag(rec, tag, order):
    db.session.add(RecordingTag(recording_id=rec.id, tag_id=tag.id, order=order))


def _cleanup(user_ids, group_ids):
    with app.app_context():
        db.session.rollback()
        rec_ids = [r for (r,) in db.session.query(Recording.id).filter(Recording.user_id.in_(user_ids))]
        if rec_ids:
            for model in (RecordingTag, Event):
                model.query.filter(model.recording_id.in_(rec_ids)).delete(synchronize_session=False)
            Recording.query.filter(Recording.id.in_(rec_ids)).delete(synchronize_session=False)
        Tag.query.filter(Tag.user_id.in_(user_ids)).delete(synchronize_session=False)
        Folder.query.filter(Folder.user_id.in_(user_ids)).delete(synchronize_session=False)
        GroupMembership.query.filter(GroupMembership.user_id.in_(user_ids)).delete(synchronize_session=False)
        if group_ids:
            Group.query.filter(Group.id.in_(group_ids)).delete(synchronize_session=False)
        APIToken.query.filter(APIToken.user_id.in_(user_ids)).delete(synchronize_session=False)
        NamingTemplate.query.filter(NamingTemplate.user_id.in_(user_ids)).delete(synchronize_session=False)
        ExportTemplate.query.filter(ExportTemplate.user_id.in_(user_ids)).delete(synchronize_session=False)
        User.query.filter(User.id.in_(user_ids)).delete(synchronize_session=False)
        db.session.commit()


def _client(user_id):
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True
    return c


def _page(user_id, **params):
    params.setdefault("per_page", 100)
    return _get(user_id, "/api/recordings", **params)


def _get(user_id, path, **params):
    resp = _client(user_id).get(path, query_string=params)
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def _token(user_id):
    plain = f"tok-{uuid.uuid4().hex}"
    with app.app_context():
        db.session.add(APIToken(user_id=user_id, token_hash=hash_token(plain), name="lstb"))
        db.session.commit()
    return plain


def _v1(token, **params):
    resp = app.test_client().get("/api/v1/recordings", query_string=params,
                                 headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def _client(user_id):
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True
    return c


def _page(user_id, **params):
    params.setdefault("per_page", 100)
    return _get(user_id, "/api/recordings", **params)


def _get(user_id, path, **params):
    resp = _client(user_id).get(path, query_string=params)
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def _token(user_id):
    plain = f"tok-{uuid.uuid4().hex}"
    with app.app_context():
        db.session.add(APIToken(user_id=user_id, token_hash=hash_token(plain), name="lstb"))
        db.session.commit()
    return plain


def _v1(token, **params):
    resp = app.test_client().get("/api/v1/recordings", query_string=params,
                                 headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


# --------------------------------------------------------------------------- #
# Statements per page
# --------------------------------------------------------------------------- #

def _build_scenario(kind, n):
    """A fresh viewer whose list holds n rows of one kind; returns the ids to clean up."""
    with app.app_context():
        viewer = _user("sv")
        base = datetime(2026, 4, 1, 9, 0, 0)
        for i in range(n):
            rec = _rec(viewer, f"own {i}", "m", created_at=base + timedelta(seconds=i))
            if kind == "owned_rich":
                folder = Folder(name=f"f{i}", user_id=viewer.id)
                tag = Tag(name=f"t{i}", user_id=viewer.id)
                db.session.add_all([folder, tag])
                db.session.flush()
                rec.folder_id = folder.id
                rec.file_hash = f"hash{i // 2}-{viewer.id}"    # pairs of duplicates
                rec.notes = f"notes {i}"
                _tag(rec, tag, 0)
                db.session.add(Event(recording_id=rec.id, title=f"e{i}", start_datetime=base))
        db.session.commit()
        return viewer.id, [viewer.id], []


def _statements(fetch, rows):
    fetch()                                                 # warm-up
    with count_statements() as statements:
        body = fetch()
    assert len(body["recordings"] if isinstance(body, dict) else body) == rows
    return len(statements)


def _statements_for_page(viewer_id, per_page):
    return _statements(lambda: _page(viewer_id, per_page=per_page), per_page)


@pytest.mark.parametrize("kind", ["owned_plain", "owned_rich"])
def test_statements_per_page_do_not_grow_with_rows(kind):
    small_viewer, small_users, small_groups = _build_scenario(kind, 25)
    large_viewer, large_users, large_groups = _build_scenario(kind, 100)
    try:
        small = _statements_for_page(small_viewer, 25)
        large = _statements_for_page(large_viewer, 100)
        print(f"{kind}: 25 rows -> {small} statements, 100 rows -> {large} statements")
        assert large == small
        assert small <= STATEMENT_BOUND
    finally:
        _cleanup(small_users + large_users, small_groups + large_groups)


def test_v1_statements_do_not_grow_with_rows():
    small_viewer, small_users, small_groups = _build_scenario("owned_rich", 25)
    large_viewer, large_users, large_groups = _build_scenario("owned_rich", 100)
    try:
        counts = {}
        for viewer, n in ((small_viewer, 25), (large_viewer, 100)):
            token = _token(viewer)
            counts["v1", n] = _statements(lambda: _v1(token, scope="all", per_page=n), n)
        print(counts)
        for (name, n), count in counts.items():
            assert count == counts[name, 25], (name, counts)
            assert count <= STATEMENT_BOUND
    finally:
        _cleanup(small_users + large_users, small_groups + large_groups)


def test_full_list_tag_and_folder_statements_do_not_grow_with_rows():
    small_viewer, small_users, small_groups = _build_scenario("owned_rich", 25)
    large_viewer, large_users, large_groups = _build_scenario("owned_rich", 100)
    paths = ["/api/tags", "/api/folders", "/recordings"]
    try:
        counts = {}
        with folders_on():
            for viewer, n in ((small_viewer, 25), (large_viewer, 100)):
                for path in paths:
                    resp = lambda: _get(viewer, path)
                    resp()
                    with count_statements() as statements:
                        body = resp()
                    assert body, path
                    counts[path, n] = len(statements)
        print(counts)
        for (path, n), count in counts.items():
            assert count == counts[path, 25], (path, counts)
            assert count <= STATEMENT_BOUND
    finally:
        _cleanup(small_users + large_users, small_groups + large_groups)
