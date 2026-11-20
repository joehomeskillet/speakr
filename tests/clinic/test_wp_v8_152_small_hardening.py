"""Synthetic checks against the production clinic route registrations and shell."""

import hashlib
import os
import subprocess
import sys
import wave
from datetime import datetime
from pathlib import Path

import pytest

from test_wp_v8_shell_nav import _launch, _login, live_server  # noqa: F401 (fixture)


ROOT = Path(__file__).resolve().parents[2]
PREFIX = "/api/clinic/v1"
CAPABILITY = PREFIX + "/offline-web/capability"
TEST_SCRIPT = (
    'import runpy, sys; runpy.run_path(sys.argv[1])[sys.argv[2]](sys.argv[3])'
)


def _run(tmp_path, check, *, clinic=True):
    env = {
        **os.environ,
        "CLINIC_MODE": "1" if clinic else "0",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'app.db'}",
        "UPLOAD_FOLDER": str(tmp_path / "uploads"),
        "CLINIC_ORIGINALS_ROOT": str(tmp_path / "originals"),
        "DISABLE_VOICE_EMBEDDING_CHECK": "true",
        "ENABLE_AUTO_PROCESSING": "false",
        "RATELIMIT_ENABLED": "false",
        "SPEAKR_SKIP_BACKGROUND_TASKS": "true",
        "PYTHONPATH": str(ROOT / "tests/clinic"),
    }
    result = subprocess.run(
        [sys.executable, "-c", TEST_SCRIPT, __file__, check, str(tmp_path)],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr[-6000:]


def _scene(tmp):
    from src.app import app, db
    from src.clinic import audio_revisions, workflow
    from src.clinic.models import ClinicDictation, ClinicOriginal
    from src.clinic.pools import ClinicAuthorPool
    from src.init_db import initialize_database
    from src.models import Recording, User

    storage_key = "clinic/originals/v1/synthetic"
    wav = Path(tmp, "originals", storage_key)
    wav.parent.mkdir(parents=True)
    with wave.open(str(wav), "wb") as writer:
        writer.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        writer.writeframes(b"\x01\x00\x02\x00" * 8000)
    audio = wav.read_bytes()

    with app.app_context():
        initialize_database(app)
        owner = User(username="v8152_owner", email="v8152_owner@example.invalid", password="x")
        stranger = User(username="v8152_other", email="v8152_other@example.invalid", password="x")
        db.session.add_all([owner, stranger])
        db.session.flush()
        pool = ClinicAuthorPool(name="Synthetic pool", created_by=owner.id)
        db.session.add(pool)
        db.session.flush()
        ids = []
        for day, status in enumerate((workflow.DRAFT, workflow.TO_ARCHIVE), start=1):
            rec = Recording(user_id=owner.id, title="Synthetic", status="COMPLETED",
                            original_filename="synthetic.wav", meeting_date=datetime(2026, 11, day),
                            is_archived=True)
            db.session.add(rec)
            db.session.flush()
            item = ClinicDictation(recording_id=rec.id, author_user_id=owner.id, pool_id=pool.id)
            db.session.add(item)
            db.session.flush()
            db.session.add(workflow.ClinicWorkflowDictation(
                id=item.id, author_user_id=owner.id, pool_id=pool.id, status=status))
            ids.append(item.id)
        original = ClinicOriginal(dictation_id=ids[0], content_hash=hashlib.sha256(audio).hexdigest(),
                                  size_bytes=len(audio), storage_key=storage_key, receipt="{}",
                                  origin_ref="synthetic")
        db.session.add(original)
        db.session.flush()
        audio_revisions.init_audio_revision(db.session.get(ClinicDictation, ids[0]), original, 1000)
        db.session.commit()
        clients = []
        for user in (owner, stranger):
            client = app.test_client()
            with client.session_transaction() as session:
                session.update(_user_id=user.get_id(), _fresh=True)
            clients.append(client)
    return (*clients, *ids)


def _check_audio(tmp):
    owner, stranger, active, _archived = _scene(tmp)
    url = f"{PREFIX}/dictations/{active}/audio/derived"
    full = owner.get(url)
    assert full.status_code == 200
    assert full.mimetype == "audio/wav"
    assert full.headers.get("Accept-Ranges") == "bytes"
    length = len(full.data)
    for spec, start, end in (("bytes=2-7", 2, 7), ("bytes=44-", 44, length - 1),
                             ("bytes=-8", length - 8, length - 1)):
        partial = owner.get(url, headers={"Range": spec, "If-Range": full.headers["ETag"]})
        assert partial.status_code == 206, partial.status_code
        assert partial.data == full.data[start:end + 1]
        assert partial.content_length == end - start + 1
        assert partial.headers["Content-Range"] == f"bytes {start}-{end}/{length}"
        for header in ("ETag", "X-Audio-Revision", "Content-Type", "Cache-Control"):
            assert partial.headers[header] == full.headers[header]
    assert "no-store" in full.headers["Cache-Control"]
    fallback = owner.get(url, headers={"Range": "bytes=2-7", "If-Range": '"old"'})
    assert (fallback.status_code, fallback.data) == (200, full.data)
    invalid = owner.get(url, headers={"Range": f"bytes={length}-"})
    assert invalid.status_code == 416
    assert invalid.headers["Content-Range"] == f"bytes */{length}"
    denied = stranger.get(url, headers={"Range": "bytes=2-7"})
    assert denied.status_code == 404
    assert "Content-Range" not in denied.headers


def _check_archive(tmp):
    owner, stranger, active, archived = _scene(tmp)
    recordings = PREFIX + "/recordings"
    archive = recordings + "?status=zu_archivieren"
    worklist = PREFIX + "/worklist/rows?page_size=1"

    def read(client, url):
        response = client.get(url)
        assert response.status_code == 200
        return response.json

    listing = read(owner, recordings + "?limit=1")
    assert [item["dictation_id"] for item in listing["items"]] == [active], listing
    assert listing["next_cursor"] is None
    work = read(owner, worklist)
    assert work["total"] == 1
    assert [item["dictation_id"] for item in work["items"]] == [active]
    assert [item["dictation_id"] for item in read(owner, archive)["items"]] == [archived]
    assert read(stranger, archive)["items"] == []


def _check_offline(tmp):
    from src.app import app
    from src.clinic.config import dev_defaults

    owner, *_ = _scene(tmp)
    assert app.test_client().get(CAPABILITY).status_code == 401
    response = owner.get(CAPABILITY)
    assert response.status_code == 200
    assert response.json["available"] is False
    assert "nicht freigegeben" in response.json["message"]
    assert set(response.json) == {"available", "message"}
    assert response.headers["Cache-Control"] == "no-store"
    assert owner.get(CAPABILITY, headers={"X-Offline-Previous-Version": "1"}).status_code == 400
    dev_defaults()["web_client"]["managed_offline_enabled"] = True
    hinted = owner.get(CAPABILITY + "?profile=managed_web_offline", headers={
        "X-Clinic-Channel": "managed_web_offline",
        "X-Offline-Previous-Version": "1",
        "X-Offline-Previous-Browser-Id": "synthetic-browser",
        "X-Offline-Previous-Account-Version": "synthetic-account-version",
    })
    assert hinted.status_code == 200 and hinted.json == response.json


def _check_upstream(_tmp):
    from src.app import app

    assert not any(rule.rule == CAPABILITY for rule in app.url_map.iter_rules())
    assert app.test_client().get(CAPABILITY).status_code == 404


def test_derived_audio_byte_ranges(tmp_path):
    _run(tmp_path, "_check_audio")


def test_archive_is_excluded_before_active_list_pagination(tmp_path):
    _run(tmp_path, "_check_archive")


def test_offline_capability_is_session_bound_and_denied(tmp_path):
    _run(tmp_path, "_check_offline")


def test_offline_capability_is_not_registered_upstream(tmp_path):
    _run(tmp_path, "_check_upstream", clinic=False)


@pytest.mark.parametrize("width,height", [(1280, 720), (390, 844)])
def test_shell_limits_aufnahmen_and_explains_offline_denial(live_server, width, height):
    from playwright.sync_api import expect, sync_playwright

    base, _ = live_server
    with sync_playwright() as playwright:
        browser = _launch(playwright)
        try:
            page = _login(browser, base, width, height)
            page.goto(base + "/#/aufnahmen")
            page.locator(".app-loading-overlay").first.wait_for(state="detached", timeout=30000)
            status = page.get_by_test_id("filter-status")
            expect(status.locator('option[value="neu_angelegt"]')).to_have_count(1)
            expect(status.locator('option[value="zu_archivieren"]')).to_have_count(0)
            page.get_by_test_id("nav-clinic-offline").click()
            offline = page.locator(".clinic-offline")
            expect(offline.get_by_test_id("notice")).to_contain_text("nicht freigegeben")
            expect(offline.get_by_role("heading", name="Lokale Entwürfe")).to_have_count(0)
            assert not page.js_errors, page.js_errors
        finally:
            browser.close()
