"""Durable draft ownership, version fencing and non-destructive discard."""

import json
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier

import pytest

from src.app import app, db
from src.clinic.draft_registry import DraftConflict, DraftError
from src.clinic.draft_store import DraftStore, create_tables
from src.clinic.models import ClinicDictation, ClinicOriginal, ClinicTranscriptRevision
from src.clinic.revisions import history
from src.models import Recording, User

NOW = datetime(2026, 11, 27, 10, tzinfo=timezone.utc)


@pytest.fixture
def scene():
    with app.app_context():
        create_tables()
        create_tables()
        suffix = uuid.uuid4().hex[:14]
        owner = User(username="draft-" + suffix, email=suffix + "@example.invalid", password="x")
        other = User(username="other-" + suffix, email="other-" + suffix + "@example.invalid", password="x")
        db.session.add_all([owner, other])
        db.session.flush()
        ids, originals = [], []
        for title in ("First", "Second"):
            recording = Recording(user_id=owner.id, title=title, status="PENDING")
            db.session.add(recording)
            db.session.flush()
            dictation = ClinicDictation(recording_id=recording.id, author_user_id=owner.id)
            db.session.add(dictation)
            db.session.flush()
            original = ClinicOriginal(dictation_id=dictation.id, content_hash="a" * 64,
                                      size_bytes=12, storage_key="original/" + dictation.id,
                                      receipt="stored", origin_ref="synthetic:test")
            db.session.add(original)
            db.session.flush()
            ids.append(dictation.id)
            originals.append(original.id)
        db.session.commit()
        store = DraftStore()
        for key, title in zip(ids, ("First", "Second")):
            store.create(owner.id, key, title, NOW)
        yield store, owner.id, other.id, ids, originals
        db.session.rollback()


def test_two_drafts_survive_new_process_and_keep_authors_separate(scene):
    store, owner, other, ids, _ = scene
    for key, text in zip(ids, ("first saved", "second saved")):
        saved = store.save(owner, key, text, store.get(owner, key).version, NOW)
        assert saved.version == 2
    uri = db.engine.url.render_as_string(hide_password=False)
    # The test database URI goes to the child via stdin, never into CLI logs.
    script = """
import json, sys
from flask import Flask
from src.database import db
from src.models import User, Recording
from src.clinic.draft_store import DraftStore
uri, owner, other = json.load(sys.stdin)
app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = uri
db.init_app(app)
with app.app_context():
    store = DraftStore()
    print(json.dumps([[d.id, d.text, d.version] for d in store.list(owner)]))
    print(json.dumps([d.id for d in store.list(other)]))
"""
    process = subprocess.run([sys.executable, "-c", script],
                             input=json.dumps([uri, owner, other]), text=True,
                             capture_output=True, timeout=30, check=True)
    owned, foreign = [json.loads(line) for line in process.stdout.splitlines()]
    assert {row[0]: row[1:] for row in owned} == {
        ids[0]: ["first saved", 2], ids[1]: ["second saved", 2],
    }
    assert foreign == []


@pytest.mark.parametrize("operation", ["get", "save", "discard", "create"])
def test_foreign_author_cannot_read_or_mutate(scene, operation):
    store, owner, other, ids, _ = scene
    with pytest.raises(DraftError) as error:
        if operation == "get":
            store.get(other, ids[0])
        elif operation == "save":
            store.save(other, ids[0], "intruder", 1, NOW)
        elif operation == "discard":
            store.discard(other, ids[0], 1, confirm=True, now=NOW)
        else:
            store.create(other, ids[0], "intruder", NOW)
    assert error.value.code == "not_found"
    assert store.get(owner, ids[0]).version == 1
    assert store.list(other) == []
    assert store.list(other, discarded=True) == []


def test_simultaneous_saves_have_one_winner_and_visible_conflict(scene):
    store, owner, _, ids, _ = scene
    barrier = Barrier(2)
    db.session.remove()

    def write(text):
        with app.app_context():
            writer = DraftStore()
            base = writer.get(owner, ids[0]).version
            barrier.wait(timeout=10)
            try:
                return writer.save(owner, ids[0], text, base, NOW)
            except DraftConflict as error:
                return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, ("one", "two")))
    winners = [r for r in results if not isinstance(r, DraftConflict)]
    conflicts = [r for r in results if isinstance(r, DraftConflict)]
    assert len(winners) == len(conflicts) == 1
    assert conflicts[0].current_version == winners[0].version == 2
    assert conflicts[0].current_text == winners[0].text
    assert store.get(owner, ids[0]).text == winners[0].text
    assert ClinicTranscriptRevision.query.filter_by(dictation_id=ids[0]).count() == 1


def test_discard_requires_confirmation_and_current_version_preserves_originals(scene):
    store, owner, _, ids, originals = scene
    first = store.save(owner, ids[0], "old text", 1, NOW)
    latest = store.save(owner, ids[0], "current text", first.version, NOW)
    second = store.save(owner, ids[1], "other draft", 1, NOW)
    original_rows = [(r.id, r.content_hash, r.storage_key, r.receipt)
                     for r in ClinicOriginal.query.order_by(ClinicOriginal.id).all()]
    for confirm, code in ((False, "confirmation_required"), (1, "invalid_confirm")):
        with pytest.raises(DraftError) as error:
            store.discard(owner, ids[0], latest.version, confirm=confirm, now=NOW)
        assert error.value.code == code
    with pytest.raises(DraftConflict) as stale:
        store.discard(owner, ids[0], first.version, confirm=True, now=NOW)
    assert stale.value.current_text == latest.text
    discarded = store.discard(owner, ids[0], latest.version, confirm=True, now=NOW)
    db.session.remove()
    restored = DraftStore()
    assert restored.get(owner, ids[0]) == discarded
    assert discarded.discarded_at == NOW
    assert discarded.version == latest.version + 1
    assert restored.list(owner) == [second]
    assert restored.list(owner, discarded=True) == [discarded]
    assert [r.body for r in history(db.session.get(ClinicDictation, ids[0]))] == [
        "old text", "current text",
    ]
    assert [(r.id, r.content_hash, r.storage_key, r.receipt)
            for r in ClinicOriginal.query.order_by(ClinicOriginal.id).all()] == original_rows
    assert all(db.session.get(ClinicOriginal, key) for key in originals)
    with pytest.raises(DraftError, match="discarded"):
        restored.save(owner, ids[0], "resurrect", discarded.version, NOW)


def test_failed_revision_write_rolls_back_claim_and_text(scene, monkeypatch):
    store, owner, _, ids, _ = scene
    from src.clinic import revisions

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic storage failure")

    monkeypatch.setattr(revisions, "add_human_revision", fail)
    with pytest.raises(RuntimeError, match="synthetic storage failure"):
        store.save(owner, ids[0], "must not persist", 1, NOW)
    assert store.get(owner, ids[0]).version == 1
    assert store.get(owner, ids[0]).text == ""
    assert ClinicTranscriptRevision.query.filter_by(dictation_id=ids[0]).count() == 0


def test_duplicate_registration_and_invalid_inputs_leave_draft_unchanged(scene):
    store, owner, _, ids, _ = scene
    with pytest.raises(DraftError, match="duplicate_id"):
        store.create(owner, ids[0], "replacement", NOW)
    with pytest.raises(DraftError) as version_error:
        store.save(owner, ids[0], "text", True, NOW)
    assert version_error.value.code == "invalid_version"
    assert str(version_error.value) == "base_version must be an int"
    with pytest.raises(DraftError) as text_error:
        store.save(owner, ids[0], "bad\x00text", 1, NOW)
    assert text_error.value.code == "invalid_text"
    assert str(text_error.value) == "text must not contain control characters other than tab and newline"
    assert store.get(owner, ids[0]).title == "First"
    assert store.get(owner, ids[0]).version == 1


def test_no_original_rejects_text_without_leaving_a_claim(scene):
    store, owner, _, _, _ = scene
    recording = Recording(user_id=owner, title="No audio yet", status="PENDING")
    db.session.add(recording)
    db.session.flush()
    dictation = ClinicDictation(recording_id=recording.id, author_user_id=owner)
    db.session.add(dictation)
    db.session.commit()
    draft = store.create(owner, dictation.id, "No audio yet", NOW)
    with pytest.raises(DraftError, match="original_required"):
        store.save(owner, draft.id, "unsaved", draft.version, NOW)
    assert store.get(owner, draft.id) == draft


def test_save_racing_discard_never_resurrects_or_discards_newer_text(scene):
    store, owner, _, ids, _ = scene
    barrier = Barrier(2)
    db.session.remove()

    def write(discard):
        with app.app_context():
            writer = DraftStore()
            base = writer.get(owner, ids[0]).version
            barrier.wait(timeout=10)
            try:
                if discard:
                    return writer.discard(owner, ids[0], base, confirm=True, now=NOW)
                return writer.save(owner, ids[0], "new text", base, NOW)
            except DraftError as error:
                return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, (False, True)))
    winners = [r for r in results if not isinstance(r, DraftError)]
    errors = [r for r in results if isinstance(r, DraftError)]
    assert len(winners) == len(errors) == 1
    assert errors[0].code in ("version_conflict", "discarded")
    current = store.get(owner, ids[0])
    assert current == winners[0]
    assert current.version == 2
    assert (current.text, current.discarded_at) in (("new text", None), ("", NOW))
