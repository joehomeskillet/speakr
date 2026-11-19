"""Durable AcceptanceLedger and ArchiveBook. Synthetic data only."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from flask import Flask
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError

from src.clinic import acceptance_policy as ap
from src.clinic import archive_policy as arp
from src.clinic import policy
from src.clinic import registry
from src.clinic import workflow as wf
from src.clinic.acceptance_policy import SCOPE_DICTATION, AcceptanceLedger, PolicyError
from src.clinic.archive_policy import ARCHIVE, ArchiveBook, ArchivePolicyError, Links
from src.database import db

NOW = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)
LINKS = Links("orig-1", "audio-1", "text-1", "ctx-1", "rel-1")


def _app(uri: str) -> Flask:
    app = Flask(__name__ + uri[-12:])
    app.config.update(TESTING=True, SECRET_KEY="synthetic", SQLALCHEMY_DATABASE_URI=uri)
    db.init_app(app)
    return app


@pytest.fixture
def two_apps(tmp_path):
    uri = "sqlite:///" + (tmp_path / "persist.sqlite").as_posix()
    first, second = _app(uri), _app(uri)
    for app in (first, second):
        with app.app_context():
            # Documented pysqlite recipe so SAVEPOINT joins a real transaction.
            event.listen(db.engine, "connect", lambda conn, _r: setattr(conn, "isolation_level", None))
            event.listen(db.engine, "begin", lambda conn: conn.exec_driver_sql("BEGIN"))
    with first.app_context():
        registry.create_clinic_tables()
    yield first, second
    for app in (first, second):
        with app.app_context():
            db.session.remove()
            db.engine.dispose()


def _publish(ledger, subject="case-1", **extra):
    fields = dict(required=True, approver_user_ids=[2], approver_pool_ids=["pool-a"],
                  deny_user_ids=[3], now=NOW)
    fields.update(extra)
    return ledger.publish(SCOPE_DICTATION, subject, **fields)


def _decide(ledger, **extra):
    fields = dict(kind="accept", actor_id=2, actor_pool_ids=[], revision_id="rev-1", reason=None,
                  previous_assignee_id=None, now=NOW)
    fields.update(extra)
    return ledger.decide_acceptance(SCOPE_DICTATION, "case-1", **fields)


def test_models_are_registered():
    assert "acceptance_policy" in registry.MODEL_MODULES
    assert "archive_policy" in registry.MODEL_MODULES
    names = {t for t in db.metadata.tables}
    assert {"clinic_acceptance_policy_version", "clinic_acceptance_decision",
            "clinic_acceptance_seal", "clinic_archive_entry", "clinic_archive_result"} <= names


def test_ledger_survives_a_second_app_instance(two_apps):
    first, second = two_apps
    with first.app_context():
        created = _publish(AcceptanceLedger())
        _decide(AcceptanceLedger())
        AcceptanceLedger().mark_release_invalid(SCOPE_DICTATION, "case-1", "rev-9")
        db.session.commit()
    with second.app_context():
        ledger = AcceptanceLedger()
        current = ledger.current(SCOPE_DICTATION, "case-1")
        assert current == created
        assert current.approver_user_ids == frozenset([2])
        assert current.approver_pool_ids == frozenset(["pool-a"])
        assert current.deny_user_ids == frozenset([3])
        stored = ledger.decisions(SCOPE_DICTATION, "case-1")
        assert len(stored) == 1 and stored[0].decision.actor_id == 2 and stored[0].policy_version == 1
        assert ledger.may_finish(SCOPE_DICTATION, "case-1", head_is_human_reviewed=True,
                                 head_revision_id="rev-1")
        assert not ledger.may_finish(SCOPE_DICTATION, "case-1", head_is_human_reviewed=True,
                                     head_revision_id="rev-9")
        # Publish a participant change from the second instance; the retry rule still holds.
        nxt = _publish(ledger, approver_user_ids=[2, 4], expected_version=1)
        assert nxt.version == 2
        db.session.commit()
    with first.app_context():
        assert AcceptanceLedger().current(SCOPE_DICTATION, "case-1").version == 2
        assert len(AcceptanceLedger().versions(SCOPE_DICTATION, "case-1")) == 2


def test_ledger_writes_join_the_callers_transaction(two_apps):
    first, second = two_apps
    with first.app_context():
        _publish(AcceptanceLedger())
        db.session.rollback()
    with second.app_context():
        with pytest.raises(PolicyError) as caught:
            AcceptanceLedger().current(SCOPE_DICTATION, "case-1")
        assert caught.value.code == "missing_policy"


def test_ledger_duplicates_are_resolved_by_unique_key(two_apps):
    first, _ = two_apps
    with first.app_context():
        _publish(AcceptanceLedger())
        db.session.flush()
        clone = ap.ClinicAcceptancePolicyVersion(
            scope=SCOPE_DICTATION, subject_id="case-1", version=1, required=True,
            approver_user_ids="[]", approver_pool_ids="[]", deny_user_ids="[]", recorded_at=NOW)
        with pytest.raises(IntegrityError):
            with db.session.begin_nested():
                db.session.add(clone)
        # A stale writer loses without a second row.
        with pytest.raises(PolicyError) as caught:
            _publish(AcceptanceLedger(), approver_user_ids=[5], expected_version=0)
        assert caught.value.code == "stale_version"
        assert len(AcceptanceLedger().versions(SCOPE_DICTATION, "case-1")) == 1
        # Same decision twice is one row.
        _decide(AcceptanceLedger())
        _decide(AcceptanceLedger())
        assert len(AcceptanceLedger().decisions(SCOPE_DICTATION, "case-1")) == 1
        db.session.rollback()


def test_ledger_rows_are_append_only(two_apps):
    first, _ = two_apps
    with first.app_context():
        _publish(AcceptanceLedger())
        _decide(AcceptanceLedger())
        db.session.commit()
        row = db.session.query(ap.ClinicAcceptancePolicyVersion).one()
        row.required = False
        with pytest.raises(PolicyError):
            db.session.flush()
        db.session.rollback()
        row = db.session.query(ap.ClinicAcceptanceDecision).one()
        db.session.delete(row)
        with pytest.raises(PolicyError):
            db.session.flush()
        db.session.rollback()


def test_ledger_without_app_context_keeps_memory_behaviour():
    ledger = AcceptanceLedger()
    first = _publish(ledger)
    assert ledger.versions(SCOPE_DICTATION, "case-1")[0] is first
    assert AcceptanceLedger().versions(SCOPE_DICTATION, "case-1") == ()


def _register(book, ident="case-1", **extra):
    fields = dict(user_id=1, links=LINKS, data_class="audio",
                  finished_at=NOW - timedelta(days=40), retention_days=30, legal_hold=False)
    fields.update(extra)
    return book.register(ident, **fields)


def _finished():
    row = wf.start(db.session, "create", author_user_id=1, pool_id="pool-a")
    wf.transition(db.session, row.id, "send_offline")
    wf.transition(db.session, row.id, "skip_recognition")
    return wf.transition(db.session, row.id, "complete")


def _apply(book, ident, **extra):
    fields = dict(action=ARCHIVE, subject=policy.Subject(1, False, (policy.Grant("own", frozenset({ARCHIVE})),), ()),
                  facts=policy.RecordingFacts(9, 1), expected_version=1, request_id="req-1",
                  now=NOW, confirm=True)
    fields.update(extra)
    return book.apply(db.session, ident, **fields)


def test_archive_book_survives_a_second_app_instance(two_apps):
    first, second = two_apps
    with first.app_context():
        wf.create_tables(bind=db.engine)
        row = _finished()
        assert _register(ArchiveBook(), row.id) == 1
        result = _apply(ArchiveBook(), row.id)
        db.session.commit()
        ident = row.id
    with second.app_context():
        book = ArchiveBook()
        again = _apply(book, ident)
        assert again == result
        assert again.version == 2 and again.links == LINKS and again.applied is True
        assert book.archive_index() == {arp.archive_slot(1, NOW): (ident,)}
        assert book.dry_run(ident, NOW).eligible is True
        with pytest.raises(ArchivePolicyError) as dup:
            _register(book, ident)
        assert dup.value.code == "duplicate"
        with pytest.raises(arp.StaleVersion):
            _apply(book, ident, request_id="req-2", expected_version=1)
        db.session.rollback()


def test_archive_book_has_no_delete_path(two_apps):
    first, _ = two_apps
    with first.app_context():
        _register(ArchiveBook(), "case-1")
        db.session.commit()
        db.session.delete(db.session.get(arp.ClinicArchiveEntry, "case-1"))
        with pytest.raises(ArchivePolicyError):
            db.session.flush()
        db.session.rollback()
        assert db.session.get(arp.ClinicArchiveEntry, "case-1") is not None


def test_archive_register_joins_the_callers_transaction(two_apps):
    first, second = two_apps
    with first.app_context():
        _register(ArchiveBook(), "case-1")
        db.session.rollback()
    with second.app_context():
        assert ArchiveBook().archive_index() == {}
        _register(ArchiveBook(), "case-1")
        db.session.rollback()


def test_archive_book_without_app_context_keeps_memory_behaviour():
    book = ArchiveBook()
    _register(book, "mem")
    assert book.dry_run("mem", NOW).eligible is True
    assert ArchiveBook().archive_index() == {}
    with pytest.raises(ArchivePolicyError):
        _register(book, "mem")
