"""Per-account notification choices survive a new database session (PAR-WF-014)."""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

import pytest

from src.app import app
from src.clinic.notification_preferences import (
    CHANNEL_IN_APP,
    EVENTS,
    PreferenceError,
    StaleVersion,
    create_tables,
    read,
    set_event,
)
from src.database import db
from src.models import User

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def prefs():
    with app.app_context():
        create_tables()
        yield
        db.session.rollback()


def _user() -> User:
    tag = secrets.token_hex(4)
    user = User(username=f"np{tag}", email=f"np{tag}@local.test", password="x")
    db.session.add(user)
    db.session.commit()
    return user


def _in_app(user_id: int) -> dict[str, bool]:
    return read(user_id).channels[CHANNEL_IN_APP]


def test_two_accounts_keep_isolated_choices_after_reopen(prefs):
    one, two = _user(), _user()
    one_id, two_id = one.id, two.id
    assert read(one_id).version == 0
    assert _in_app(one_id) == {event: True for event in EVENTS}
    saved = set_event(one_id, "completed", False, 0)
    assert saved.version == 1
    assert saved.channels[CHANNEL_IN_APP]["completed"] is False
    assert saved.channels[CHANNEL_IN_APP]["accepted"] is True
    db.session.remove()
    assert read(one_id).version == 1
    assert _in_app(one_id)["completed"] is False
    assert read(two_id).version == 0
    assert _in_app(two_id) == {event: True for event in EVENTS}


def test_stale_version_writes_nothing(prefs):
    user_id = _user().id
    set_event(user_id, "accepted", False, 0)
    with pytest.raises(StaleVersion) as caught:
        set_event(user_id, "accepted", True, 0)
    assert (caught.value.code, caught.value.version) == ("stale_version", 1)
    db.session.remove()
    assert read(user_id).version == 1
    assert _in_app(user_id)["accepted"] is False


def test_rejected_choice_writes_nothing(prefs):
    user_id = _user().id
    cases = (
        (lambda: set_event(user_id, "nope", False, 0), "unknown_event"),
        (lambda: set_event(user_id, "accepted", "no", 0), "bad_value"),
        (lambda: set_event(user_id, "accepted", False, 0, channel="smtp"), "unknown_channel"),
        (lambda: set_event(user_id, "accepted", False, -1), "bad_version"),
        (lambda: set_event(user_id, "accepted", False, True), "bad_version"),
        (lambda: read(0), "bad_user"),
    )
    for call, code in cases:
        with pytest.raises(PreferenceError) as caught:
            call()
        assert caught.value.code == code
    db.session.remove()
    assert read(user_id).version == 0


def test_two_processes_read_the_same_account_after_the_change(prefs):
    user_id = _user().id
    set_event(user_id, "acceptance_requested", False, 0)
    db.session.remove()
    seen = [_process_read(user_id) for _ in range(2)]
    assert seen[0] == seen[1]
    assert seen[0]["version"] == 1
    assert seen[0]["events"]["acceptance_requested"] is False
    assert seen[0]["events"]["completed"] is True


def _process_read(user_id: int) -> dict:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    env["SPEAKR_SKIP_BACKGROUND_TASKS"] = "true"
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), str(user_id)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr[-2000:]
    # App startup writes status lines to stdout before the choice JSON.
    return json.loads(result.stdout.strip().splitlines()[-1])


if __name__ == "__main__":
    with app.app_context():
        pref = read(int(sys.argv[1]))
        json.dump(
            {"version": pref.version, "events": pref.channels[CHANNEL_IN_APP]},
            sys.stdout,
        )
