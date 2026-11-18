"""Archive requests, retention dry-run and clinic delete redirection.

The workflow database is a SQLite file under tmp_path. The retention case
uses the app database and synthetic rows only. Nothing is deleted.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.app import app, db
from src.clinic import archive, policy, workflow as wf
from src.clinic.archive_policy import (
    ARCHIVE,
    DELETE,
    ArchiveBook,
    ArchivePolicyError,
    Links,
    StaleVersion,
    archive_slot,
    authorize,
    trigger_for,
)
from src.clinic.models import (
    ClinicAudioRevision,
    ClinicContextRevision,
    ClinicDictation,
    ClinicOriginal,
    ClinicRelease,
    ClinicTranscriptRevision,
)
from src.models import Recording
from src.services import retention

NOW = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)
LINKS = Links("orig-1", "audio-1", "text-1", "ctx-1", "rel-1")


class _Tag:
    def __init__(self, days):
        self.retention_days = days
        self.protect_from_deletion = False
        self.group_id = 1
        self.user_id = 1
        self.id = 3


class _Assoc:
    def __init__(self, tag):
        self.tag = tag


class _Recording:
    def __init__(self, days=30, exempt=False, revision=0):
        self.deletion_exempt = exempt
        self.user_id = 1
        self.archive_revision = revision
        self.tag_associations = [] if days is None else [_Assoc(_Tag(days))]


@pytest.fixture
def db_session(tmp_path):
    engine = create_engine("sqlite:///" + (tmp_path / "archive-policy.sqlite").as_posix())
    wf.create_tables(bind=engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _subject(user_id, actions, *, denies=()):
    grants = ()
    if actions:
        grants = (policy.Grant("own", frozenset(actions)),)
    return policy.Subject(user_id, False, grants, tuple(denies))


def _facts(owner_id, recording_id=9):
    return policy.RecordingFacts(recording_id, owner_id)


def _finished(session, acceptance=False):
    row = wf.start(session, "create", author_user_id=1, pool_id="pool-a", acceptance_required=acceptance)
    wf.transition(session, row.id, "send_offline")
    wf.transition(session, row.id, "skip_recognition")
    return wf.transition(session, row.id, "complete")


def _book_for(row, **extra):
    book = ArchiveBook()
    fields = dict(
        user_id=1,
        links=LINKS,
        data_class="audio",
        finished_at=NOW - timedelta(days=40),
        retention_days=30,
        legal_hold=False,
    )
    fields.update(extra)
    book.register(row.id, **fields)
    return book


def _apply(book, session, row, **extra):
    fields = dict(
        action=ARCHIVE,
        subject=_subject(1, {ARCHIVE}),
        facts=_facts(1),
        expected_version=1,
        request_id="req-1",
        now=NOW,
        confirm=True,
    )
    fields.update(extra)
    return book.apply(session, row.id, **fields)


def test_archive_keeps_links_and_leaves_the_open_case(db_session):
    done = _finished(db_session)
    other = wf.start(db_session, "create", author_user_id=1, pool_id="pool-a")
    book = _book_for(done)
    before = book.dry_run(done.id, NOW)
    assert before.eligible is True and before.would_delete is False
    result = _apply(book, db_session, done)
    db_session.commit()
    assert result.applied is True
    assert result.trigger == "report_archive"
    assert result.status == wf.TO_ARCHIVE
    assert result.links == LINKS
    assert result.version == 2
    assert other.status == wf.DRAFT
    full = wf.worklist(db_session, role=wf.ROLE_AUTHOR, user_id=1, pool_id="pool-a")
    assert any(done.id in group.dictation_ids for group in full if group.status == wf.TO_ARCHIVE)
    active_ids = {ident for group in book.active_groups(db_session, role=wf.ROLE_AUTHOR, user_id=1, pool_id="pool-a") for ident in group.dictation_ids}
    assert done.id not in active_ids
    assert other.id in active_ids
    assert book.archive_index() == {archive_slot(1, NOW): (done.id,)}


def test_dry_run_uses_data_class_legal_hold_and_recording_rules():
    book = ArchiveBook()
    book.register("case", user_id=4, links=LINKS, data_class="transcript", finished_at=None, retention_days=None)
    plain = book.dry_run("case", NOW)
    assert plain.eligible is False and plain.reason == "not_configured" and plain.would_delete is False
    held = ArchiveBook()
    held.register(
        "held", user_id=4, links=LINKS, data_class="release",
        finished_at=NOW - timedelta(days=400), retention_days=30, legal_hold=True,
    )
    blocked = held.dry_run("held", NOW)
    assert blocked.eligible is False and blocked.reason == "legal_hold" and blocked.would_delete is False
    cutoff = NOW - timedelta(days=30)
    young = ArchiveBook()
    young.register("young", user_id=4, links=LINKS, data_class="audio", finished_at=cutoff, retention_days=30)
    assert young.dry_run("young", NOW).reason == "not_elapsed"
    # Configured class days win over a shorter recording tag.
    configured = ArchiveBook()
    configured.register(
        "cfg", user_id=4, links=LINKS, data_class="audio",
        finished_at=NOW - timedelta(days=7), retention_days=10,
    )
    assert configured.dry_run("cfg", NOW, recording=_Recording(days=5)).reason == "not_elapsed"
    # Unset class days come from the recording retention service.
    from_service = ArchiveBook()
    from_service.register(
        "svc", user_id=4, links=LINKS, data_class="audio",
        finished_at=NOW - timedelta(days=11), retention_days=None,
    )
    served = from_service.dry_run("svc", NOW, recording=_Recording(days=10))
    assert served.eligible is True and served.retention_days == 10 and served.reason == "eligible"
    exempt = from_service.dry_run("svc", NOW, recording=_Recording(days=10, exempt=True))
    assert exempt.eligible is False and exempt.reason == "legal_hold" and exempt.would_delete is False


def test_delete_under_clinic_mode_archives_without_deletion(db_session, monkeypatch):
    monkeypatch.setenv("CLINIC_MODE", "1")

    def boom(*_args, **_kwargs):
        raise AssertionError("production deletion")

    monkeypatch.setattr(retention, "remove_recording_audio", boom)
    monkeypatch.setattr(retention, "process_auto_deletion", boom)
    row = _finished(db_session)
    book = _book_for(row, finished_at=NOW, retention_days=None)
    result = _apply(
        book, db_session, row, action=DELETE, subject=_subject(1, {DELETE}), request_id="del-1",
    )
    assert trigger_for(DELETE) == "report_archive"
    assert result.status == wf.TO_ARCHIVE
    assert result.requested == DELETE
    assert result.trigger == "report_archive"
    assert result.links == LINKS
    assert result.dry_run.would_delete is False
    assert result.retention_changed is False
    monkeypatch.delenv("CLINIC_MODE", raising=False)
    assert trigger_for(DELETE) == "report_delete"


def test_delete_without_clinic_mode_stays_a_different_state(db_session, monkeypatch):
    monkeypatch.delenv("CLINIC_MODE", raising=False)
    row = _finished(db_session)
    book = _book_for(row)
    result = _apply(book, db_session, row, action=DELETE, subject=_subject(1, {DELETE}), request_id="del-off")
    assert result.status == wf.TO_DELETE
    assert result.trigger == "report_delete"
    assert result.links == LINKS
    assert result.dry_run.would_delete is False


def test_unauthorized_requests_do_not_move(db_session):
    owner = _facts(1)
    assert authorize(_subject(1, set()), owner, DELETE).allowed is False
    assert authorize(_subject(1, {DELETE}), owner, ARCHIVE).reason == "action_not_granted"
    assert authorize(policy.Subject(1, True), _facts(2), ARCHIVE).reason == "no_grant"
    denied = policy.Subject(
        1, False,
        (policy.Grant("own", frozenset({ARCHIVE})),),
        (policy.Deny(recording_ids=frozenset({9})),),
    )
    assert authorize(denied, owner, ARCHIVE).reason == "deny_exception"
    row = _finished(db_session)
    book = _book_for(row)
    with pytest.raises(ArchivePolicyError) as raised:
        _apply(book, db_session, row, subject=_subject(1, set()), request_id="nope")
    assert raised.value.code == "unauthorized"
    assert row.status == wf.DONE
    assert book.dry_run(row.id, NOW).links == LINKS


def test_stale_version_retries_with_the_current_one(db_session):
    row = _finished(db_session)
    book = _book_for(row)
    with pytest.raises(StaleVersion) as raised:
        _apply(book, db_session, row, expected_version=0, request_id="stale")
    assert raised.value.current_version == 1
    assert row.status == wf.DONE
    result = _apply(book, db_session, row, expected_version=1, request_id="retry")
    assert result.applied is True and result.version == 2 and result.links == LINKS


def test_failed_transition_and_retention_error_keep_the_case(db_session, monkeypatch):
    row = _finished(db_session)
    wf.transition(db_session, row.id, "report_archive")
    book = _book_for(row)
    with pytest.raises(ArchivePolicyError) as raised:
        _apply(book, db_session, row, request_id="again")
    assert raised.value.code == "transition_failed"
    assert row.status == wf.TO_ARCHIVE
    assert book.archive_index() == {}

    fresh = _finished(db_session)
    other = _book_for(fresh, retention_days=30, finished_at=NOW - timedelta(days=40))

    def boom(*_args, **_kwargs):
        raise RuntimeError("archive failed")

    monkeypatch.setattr(archive, "archive_for_retention", boom)
    recording = _Recording(days=30, revision=0)
    with pytest.raises(RuntimeError, match="archive failed"):
        _apply(
            other, db_session, fresh, request_id="boom", recording=recording, expected_archive_revision=0,
        )
    assert fresh.status == wf.DONE
    assert other.dry_run(fresh.id, NOW).links == LINKS
    recovered = _apply(other, db_session, fresh, request_id="recovered", recording=None)
    assert recovered.applied is True and recovered.status == wf.TO_ARCHIVE and recovered.links == LINKS


def test_abort_then_confirm_and_retry_is_idempotent(db_session):
    row = _finished(db_session)
    book = _book_for(row)
    aborted = _apply(book, db_session, row, confirm=False, request_id="once")
    assert aborted.applied is False and aborted.trigger is None and row.status == wf.DONE
    first = _apply(book, db_session, row, confirm=True, request_id="once")
    second = _apply(book, db_session, row, confirm=True, request_id="once", expected_version=1)
    assert first == second
    assert first.version == 2 and row.status == wf.TO_ARCHIVE


def test_stale_archive_revision_does_not_archive(db_session, monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("archive_for_retention")

    monkeypatch.setattr(archive, "archive_for_retention", boom)
    row = _finished(db_session)
    book = _book_for(row)
    with pytest.raises(StaleVersion) as raised:
        _apply(
            book, db_session, row, recording=_Recording(revision=4),
            expected_archive_revision=0, request_id="rev",
        )
    assert raised.value.current_version == 4
    assert row.status == wf.DONE


def test_archive_slot_uses_zurich_month():
    # 31 Mar 2026 22:30 UTC is 1 Apr 2026 00:30 in Europe/Zurich (CEST).
    when = datetime(2026, 3, 31, 22, 30, tzinfo=timezone.utc)
    assert archive_slot(7, when) == "7/2026-04"
    with pytest.raises(ArchivePolicyError) as raised:
        archive_slot(7, datetime(2026, 3, 31, 22, 30))
    assert raised.value.code == "naive_datetime"


def test_eligible_recording_keeps_original_revision_and_release(world):
    with app.app_context():
        recording = db.session.get(Recording, world["rec_b"])
        dictation = ClinicDictation(recording_id=recording.id, author_user_id=recording.user_id)
        db.session.add(dictation)
        db.session.flush()
        original = ClinicOriginal(
            dictation_id=dictation.id, content_hash="a" * 64, size_bytes=8,
            storage_key="synthetic/original", receipt="synthetic", origin_ref="fixture",
        )
        audio = ClinicAudioRevision(dictation_id=dictation.id, content_hash="b" * 64)
        context = ClinicContextRevision(dictation_id=dictation.id, source_system="fixture")
        db.session.add_all([original, audio, context])
        db.session.flush()
        body = "Synthetic correction"
        transcript = ClinicTranscriptRevision(
            dictation_id=dictation.id, kind="HUMAN", body=body,
            normalised_text_hash=hashlib.sha256(body.encode()).hexdigest(),
            original_version_id=original.id,
        )
        db.session.add(transcript)
        db.session.flush()
        release = ClinicRelease(
            dictation_id=dictation.id, audio_revision_id=audio.id,
            transcript_revision_id=transcript.id, context_revision_id=context.id,
            text_hash=transcript.normalised_text_hash, actor_user_id=recording.user_id,
            document_type="fixture", release_type="fixture", authorization_decision="{}",
        )
        db.session.add(release)
        db.session.flush()
        links = Links(original.id, audio.id, transcript.id, context.id, release.id)
        child_ids = (original.id, audio.id, transcript.id, context.id, release.id)
        wf.create_tables(bind=db.engine)
        workflow_row = wf.start(
            db.session, "create", author_user_id=recording.user_id, pool_id="pool-a",
        )
        wf.transition(db.session, workflow_row.id, "send_offline")
        wf.transition(db.session, workflow_row.id, "skip_recognition")
        wf.transition(db.session, workflow_row.id, "complete")
        book = ArchiveBook()
        book.register(
            workflow_row.id, user_id=recording.user_id, links=links, data_class="audio",
            finished_at=NOW - timedelta(days=40), retention_days=30,
        )
        result = book.apply(
            db.session, workflow_row.id,
            action=ARCHIVE,
            subject=_subject(recording.user_id, {ARCHIVE}),
            facts=policy.RecordingFacts(recording.id, recording.user_id),
            expected_version=1, request_id="retain-1", now=NOW, confirm=True,
            recording=recording, expected_archive_revision=0,
        )
        db.session.flush()
        assert result.retention_changed is True
        assert result.links == links
        assert result.status == wf.TO_ARCHIVE
        assert result.dry_run.would_delete is False
        assert recording.retention_archived_at is not None
        assert recording.archive_revision == 1
        assert db.session.get(Recording, recording.id) is not None
        again = (
            db.session.get(ClinicOriginal, original.id).id,
            db.session.get(ClinicAudioRevision, audio.id).id,
            db.session.get(ClinicTranscriptRevision, transcript.id).id,
            db.session.get(ClinicContextRevision, context.id).id,
            db.session.get(ClinicRelease, release.id).id,
        )
        assert again == child_ids
        db.session.rollback()
