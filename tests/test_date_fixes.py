"""Date handling fixes from the #412 audit (search, Inquire, edits, watch folder, API v1).

Dates are stored as naive UTC. These tests pin how calendar-day questions are
answered in the viewer's timezone and that no end day is dropped.

SHARED-DB: users, recordings and tokens are removed afterwards.
"""

import os
import re
import secrets
import sys
import tempfile
from datetime import date, datetime, timedelta
from unittest.mock import patch

import pytest
from flask import g, has_app_context
from flask.testing import FlaskClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.app import app, db
from src.models import APIToken, Recording, User
from src.utils.token_auth import hash_token

app.config["WTF_CSRF_ENABLED"] = False
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _Client(FlaskClient):
    def open(self, *args, **kwargs):
        if has_app_context():
            g.pop('_login_user', None)
        return super().open(*args, **kwargs)


@pytest.fixture
def world():
    """A user with two recordings either side of a Tokyo midnight."""
    with app.app_context():
        n = f"dates_{secrets.token_hex(4)}"
        u = User(username=n, email=f"{n}@local.test", password="x")
        db.session.add(u)
        db.session.commit()
        # 2026-03-04 23:30 UTC = 2026-03-05 08:30 in Tokyo
        a = Recording(user_id=u.id, title="Morning Tokyo", status="COMPLETED", original_filename="a.wav",
                      meeting_date=datetime(2026, 3, 4, 23, 30))
        # 2026-03-04 10:00 UTC = 2026-03-04 19:00 in Tokyo
        b = Recording(user_id=u.id, title="Evening Tokyo", status="COMPLETED", original_filename="b.wav",
                      meeting_date=datetime(2026, 3, 4, 10, 0))
        plain = f"test-token-{secrets.token_urlsafe(16)}"
        t = APIToken(user_id=u.id, token_hash=hash_token(plain), name="dates")
        db.session.add_all([a, b, t])
        db.session.commit()
        ids = dict(user=u.id, a=a.id, b=b.id, token=t.id)
    yield ids, plain
    with app.app_context():
        for model, key in ((APIToken, "token"), (Recording, "a"), (Recording, "b"), (User, "user")):
            obj = db.session.get(model, ids[key])
            if obj is not None:
                db.session.delete(obj)
        db.session.commit()


def _search(ids, q, tz="Asia/Tokyo"):
    c = _Client(app, app.response_class, use_cookies=True)
    with c.session_transaction() as sess:
        sess["_user_id"] = str(ids["user"])
    with patch("src.utils.timezones.now_local", return_value=datetime(2026, 3, 5, 12, 0)):
        resp = c.get("/api/recordings", query_string={"q": q, "tz": tz, "per_page": 100})
    assert resp.status_code == 200, resp.get_data(as_text=True)[:300]
    return {r["title"] for r in resp.get_json()["recordings"]}


# ---------------------------------------------------- search (T9, B4)

def test_today_and_yesterday_are_the_viewers_calendar_days(world):
    ids, _ = world
    assert _search(ids, "date:today") == {"Morning Tokyo"}
    assert _search(ids, "date:yesterday") == {"Evening Tokyo"}


def test_a_specific_day_is_the_viewers_calendar_day(world):
    ids, _ = world
    assert _search(ids, "date:2026-03-05") == {"Morning Tokyo"}
    assert _search(ids, "date:2026-03-04", tz="UTC") == {"Morning Tokyo", "Evening Tokyo"}


def test_date_to_includes_the_whole_last_day(world):
    ids, _ = world
    # Before: meeting_date <= 2026-03-05 (midnight) dropped the 08:30 recording.
    assert _search(ids, "date_from:2026-03-05 date_to:2026-03-05") == {"Morning Tokyo"}


def test_last_month_includes_its_last_day(world):
    ids, _ = world
    with app.app_context():
        r = db.session.get(Recording, ids["a"])
        r.meeting_date = datetime(2026, 2, 28, 20, 0)     # 28 Feb 20:00 UTC, last day of Feb in UTC
        db.session.commit()
    with patch("src.utils.timezones.now_local", return_value=datetime(2026, 3, 10, 12, 0)):
        c = _Client(app, app.response_class, use_cookies=True)
        with c.session_transaction() as sess:
            sess["_user_id"] = str(ids["user"])
        resp = c.get("/api/recordings", query_string={"q": "date:lastmonth", "tz": "UTC"})
    assert "Morning Tokyo" in {r["title"] for r in resp.get_json()["recordings"]}


# ------------------------------------------------------- B1: Inquire filters

def test_inquire_filter_dates_are_plain_iso(world):
    ids, _ = world
    c = _Client(app, app.response_class, use_cookies=True)
    with c.session_transaction() as sess:
        sess["_user_id"] = str(ids["user"])
    resp = c.get("/api/inquire/available_filters")
    body = resp.get_json(silent=True) or {}
    if resp.status_code != 200 or "recordings" not in body:
        pytest.skip("Inquire mode disabled in this environment")
    dates = [r["meeting_date"] for r in body["recordings"] if r["id"] in (ids["a"], ids["b"])]
    assert dates and all(re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$", d) for d in dates)


# --------------------------------------------- B5: date-only meeting_date edit

def test_a_date_only_edit_keeps_the_time_of_day(world):
    ids, _ = world
    c = _Client(app, app.response_class, use_cookies=True)
    with c.session_transaction() as sess:
        sess["_user_id"] = str(ids["user"])
    with patch("src.api.recordings.export_recording"):
        resp = c.post("/save", json={"id": ids["b"], "meeting_date": "2026-03-10"})
    assert resp.status_code == 200, resp.get_data(as_text=True)[:300]
    with app.app_context():
        assert db.session.get(Recording, ids["b"]).meeting_date == datetime(2026, 3, 10, 10, 0)


# ----------------------------------------------------- B6: file times in UTC

def test_file_mtime_is_naive_utc_whatever_the_server_zone():
    from src.utils import ffprobe
    with tempfile.NamedTemporaryFile() as f:
        os.utime(f.name, (1772668800, 1772668800))      # 2026-03-05 00:00:00 UTC
        old = os.environ.get("TZ")
        os.environ["TZ"] = "Asia/Tokyo"
        import time
        time.tzset()
        try:
            fn = getattr(ffprobe, "_get_file_mtime", None) or getattr(ffprobe, "get_file_mtime")
            assert fn(f.name) == datetime(2026, 3, 5, 0, 0)
        finally:
            if old is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old
            time.tzset()


# ------------------------------------------------- B8: API v1 date filters

def test_v1_date_to_includes_the_whole_day(world):
    ids, plain = world
    with app.app_context():
        for key, created in (("a", datetime(2026, 3, 5, 18, 0)), ("b", datetime(2026, 3, 6, 1, 0))):
            db.session.get(Recording, ids[key]).created_at = created
        db.session.commit()
    with app.test_client() as c:
        resp = c.get("/api/v1/recordings", query_string={"date_from": "2026-03-05", "date_to": "2026-03-05", "per_page": 100},
                     headers={"Authorization": f"Bearer {plain}"})
    assert resp.status_code == 200, resp.get_data(as_text=True)[:300]
    titles = {r["title"] for r in resp.get_json()["recordings"]}
    assert "Morning Tokyo" in titles and "Evening Tokyo" not in titles


# ----------------------------------------------- B3: token expiry display

def test_token_expiry_is_read_as_utc():
    html = open(os.path.join(ROOT, "templates", "account.html"), encoding="utf-8").read()
    body = html[html.index("function formatTokenDate"):][:600]
    assert "+ 'Z'" in body
