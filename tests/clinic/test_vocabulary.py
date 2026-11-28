"""Tests for clinic vocabulary, voice commands, pronunciation hints and review workflow.

Covers PAR-VOC-001 through PAR-VOC-008.
Uses a real SQLite database in tmp_path (no database mocks).
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.clinic import vocabulary


@pytest.fixture
def session(tmp_path):
    """Real SQLite database in a temporary directory per test."""
    db_file = tmp_path / "vocab_test.db"
    engine = create_engine(f"sqlite:///{db_file}")
    vocabulary.create_tables(bind=engine)
    SessionLocal = sessionmaker(bind=engine)
    sess = SessionLocal()
    try:
        yield sess
    finally:
        sess.close()
        engine.dispose()


# ============================================================================
# 1. CRUD Tests for User and Pool Vocabulary
# ============================================================================


def test_user_vocabulary_crud(session):
    # Create entry
    entry = vocabulary.add_vocabulary_entry(
        scope="user",
        written_form="Hypertonie",
        spoken_form="Bluthochdruck",
        user_id=101,
        origin=vocabulary.ORIGIN_MANUAL,
        notes="Häufige Diagnose",
        session=session,
    )
    session.commit()
    assert entry.id is not None
    assert entry.scope == "user"
    assert entry.user_id == 101
    assert entry.pool_id is None
    assert entry.written_form == "Hypertonie"
    assert entry.status == vocabulary.STATUS_ACTIVE

    # Update via add_vocabulary_entry (idempotent / upsert)
    updated = vocabulary.add_vocabulary_entry(
        scope="user",
        written_form="Hypertonie",
        spoken_form="hoher Blutdruck",
        user_id=101,
        session=session,
    )
    session.commit()
    assert updated.id == entry.id
    assert updated.spoken_form == "hoher Blutdruck"

    # Search
    results = vocabulary.search_vocabulary(
        query="Hyper", mode="prefix", scope="user", user_id=101, session=session
    )
    assert len(results) == 1
    assert results[0].written_form == "Hypertonie"

    # Suppress (negative word list)
    suppressed = vocabulary.suppress_vocabulary_entry(entry.id, session=session)
    session.commit()
    assert suppressed.status == vocabulary.STATUS_SUPPRESSED

    # Active search should not find it
    active_res = vocabulary.search_vocabulary(
        query="Hyper", scope="user", user_id=101, status=vocabulary.STATUS_ACTIVE, session=session
    )
    assert len(active_res) == 0

    # Reactivate
    reactivated = vocabulary.reactivate_vocabulary_entry(entry.id, session=session)
    session.commit()
    assert reactivated.status == vocabulary.STATUS_ACTIVE

    # Soft delete
    vocabulary.delete_vocabulary_entry(entry.id, hard=False, session=session)
    session.commit()
    assert entry.status == vocabulary.STATUS_SUPPRESSED

    # Hard delete
    vocabulary.delete_vocabulary_entry(entry.id, hard=True, session=session)
    session.commit()
    assert session.get(vocabulary.ClinicVocabularyEntry, entry.id) is None


def test_pool_vocabulary_crud(session):
    pool_id = "pool-cardiology-01"
    entry = vocabulary.add_vocabulary_entry(
        scope="pool",
        written_form="Echokardiographie",
        spoken_form="Echo",
        pool_id=pool_id,
        origin=vocabulary.ORIGIN_PROFILE,
        session=session,
    )
    session.commit()
    assert entry.scope == "pool"
    assert entry.pool_id == pool_id
    assert entry.user_id is None

    # Search suffix
    res = vocabulary.search_vocabulary(
        query="graphie", mode="suffix", scope="pool", pool_id=pool_id, session=session
    )
    assert len(res) == 1
    assert res[0].written_form == "Echokardiographie"


def test_scope_validation_errors(session):
    # User scope without user_id
    with pytest.raises(vocabulary.VocabularyError) as exc_user:
        vocabulary.add_vocabulary_entry(scope="user", written_form="Test", session=session)
    assert exc_user.value.code == "missing_user_id"

    # Pool scope without pool_id
    with pytest.raises(vocabulary.VocabularyError) as exc_pool:
        vocabulary.add_vocabulary_entry(scope="pool", written_form="Test", session=session)
    assert exc_pool.value.code == "missing_pool_id"

    # Invalid scope
    with pytest.raises(vocabulary.VocabularyError) as exc_scope:
        vocabulary.add_vocabulary_entry(scope="global", written_form="Test", session=session)
    assert exc_scope.value.code == "invalid_scope"

    # Empty written form
    with pytest.raises(vocabulary.VocabularyError) as exc_empty:
        vocabulary.add_vocabulary_entry(scope="user", written_form="   ", user_id=1, session=session)
    assert exc_empty.value.code == "empty_written_form"


def test_voice_commands_crud(session):
    cmd = vocabulary.add_voice_command(
        scope="user",
        trigger_phrase="Befund Vorlage einfügen",
        description="Fügt Standardbefund ein",
        action_type="template",
        action_payload="[Anamnese]\n[Befund]\n[Beurteilung]",
        user_id=102,
        before_hook="focus_editor",
        session=session,
    )
    session.commit()
    assert cmd.id is not None
    assert cmd.trigger_phrase == "Befund Vorlage einfügen"
    assert cmd.action_type == "template"

    # Upsert voice command
    updated_cmd = vocabulary.add_voice_command(
        scope="user",
        trigger_phrase="Befund Vorlage einfügen",
        action_type="template",
        action_payload="[Neu Vorlage]",
        user_id=102,
        session=session,
    )
    session.commit()
    assert updated_cmd.id == cmd.id
    assert updated_cmd.action_payload == "[Neu Vorlage]"


def test_pronunciation_hints_crud(session):
    hint = vocabulary.add_pronunciation_hint(
        scope="pool",
        written_form="Xylometazolin",
        phonetic_hint="ksy-lo-me-ta-tso-lin",
        sounds_like="Ksylo",
        pool_id="pool-ent-01",
        notes="Pharmazeutischer Wirkstoff",
        session=session,
    )
    session.commit()
    assert hint.id is not None
    assert hint.phonetic_hint == "ksy-lo-me-ta-tso-lin"
    assert hint.sounds_like == "Ksylo"


# ============================================================================
# 2. Review-Flow «Vokabularänderungen prüfen»
# ============================================================================


def test_review_submit_and_list(session):
    item1 = vocabulary.submit_review_item(
        user_id=201,
        written_form="Vorhofflimmern",
        spoken_form="VHF",
        operation=vocabulary.REVIEW_OP_ADD,
        origin=vocabulary.ORIGIN_CORRECTION,
        information="Aus Diktat 42 gelernt",
        session=session,
    )
    item2 = vocabulary.submit_review_item(
        user_id=201,
        written_form="Veraltetes Wort",
        operation=vocabulary.REVIEW_OP_DELETE,
        origin=vocabulary.ORIGIN_SESSION,
        session=session,
    )
    session.commit()
    assert item1.id is not None
    assert item2.id is not None

    pending = vocabulary.list_review_items(user_id=201, status=vocabulary.REVIEW_STATUS_PENDING, session=session)
    assert len(pending) == 2
    assert pending[0].written_form in ("Vorhofflimmern", "Veraltetes Wort")


def test_review_accept_to_user(session):
    item = vocabulary.submit_review_item(
        user_id=202,
        written_form="Appendektomie",
        spoken_form="Blinddarm OP",
        operation=vocabulary.REVIEW_OP_ADD,
        origin=vocabulary.ORIGIN_CORRECTION,
        session=session,
    )
    session.commit()

    accepted = vocabulary.accept_to_user(item.id, actor_id=999, session=session)
    session.commit()

    assert accepted.status == vocabulary.REVIEW_STATUS_ACCEPTED_USER
    assert accepted.target_entry_id is not None

    # Check that vocabulary entry exists for user
    entry = session.get(vocabulary.ClinicVocabularyEntry, accepted.target_entry_id)
    assert entry is not None
    assert entry.written_form == "Appendektomie"
    assert entry.scope == "user"
    assert entry.user_id == 202
    assert entry.status == vocabulary.STATUS_ACTIVE

    # Check decision record (traceability)
    history = vocabulary.get_decision_history(item.id, session=session)
    assert len(history) == 1
    assert history[0].actor_id == 999
    assert history[0].action == vocabulary.ACTION_ACCEPT_USER
    assert history[0].previous_status == vocabulary.REVIEW_STATUS_PENDING
    assert history[0].new_status == vocabulary.REVIEW_STATUS_ACCEPTED_USER


def test_review_accept_to_pool(session):
    item = vocabulary.submit_review_item(
        user_id=203,
        written_form="Sinustachykardie",
        spoken_form="schneller Puls",
        pool_id="pool-cardio",
        operation=vocabulary.REVIEW_OP_ADD,
        session=session,
    )
    session.commit()

    accepted = vocabulary.accept_to_pool(item.id, actor_id=888, session=session)
    session.commit()

    assert accepted.status == vocabulary.REVIEW_STATUS_ACCEPTED_POOL
    assert accepted.target_pool_id == "pool-cardio"

    entry = session.get(vocabulary.ClinicVocabularyEntry, accepted.target_entry_id)
    assert entry is not None
    assert entry.scope == "pool"
    assert entry.pool_id == "pool-cardio"
    assert entry.written_form == "Sinustachykardie"


def test_review_accept_delete_operation(session):
    item = vocabulary.submit_review_item(
        user_id=204,
        written_form="UnerwünschtePhrase",
        operation=vocabulary.REVIEW_OP_DELETE,
        origin=vocabulary.ORIGIN_SESSION,
        session=session,
    )
    session.commit()

    accepted = vocabulary.accept_to_user(item.id, actor_id=999, session=session)
    session.commit()

    # The entry should be created as suppressed (negative word list)
    entry = session.get(vocabulary.ClinicVocabularyEntry, accepted.target_entry_id)
    assert entry is not None
    assert entry.status == vocabulary.STATUS_SUPPRESSED


def test_review_reject(session):
    item = vocabulary.submit_review_item(
        user_id=205,
        written_form="Falschgeschrieben",
        session=session,
    )
    session.commit()

    rejected = vocabulary.reject_item(item.id, actor_id=999, reason="Tippfehler im Diktat", session=session)
    session.commit()

    assert rejected.status == vocabulary.REVIEW_STATUS_REJECTED
    history = vocabulary.get_decision_history(item.id, session=session)
    assert len(history) == 1
    assert history[0].action == vocabulary.ACTION_REJECT
    assert "Tippfehler" in history[0].details


def test_review_defer_later(session):
    item = vocabulary.submit_review_item(
        user_id=206,
        written_form="Unklares Wort",
        session=session,
    )
    session.commit()

    deferred = vocabulary.defer_item(item.id, actor_id=999, notes="Mit Chefarzt klären", session=session)
    session.commit()

    assert deferred.status == vocabulary.REVIEW_STATUS_DEFERRED

    # Deferred item can be accepted later
    accepted = vocabulary.accept_to_user(item.id, actor_id=999, session=session)
    session.commit()
    assert accepted.status == vocabulary.REVIEW_STATUS_ACCEPTED_USER

    history = vocabulary.get_decision_history(item.id, session=session)
    assert len(history) == 2
    assert history[0].action == vocabulary.ACTION_DEFER
    assert history[1].action == vocabulary.ACTION_ACCEPT_USER


def test_review_undo_decision(session):
    item = vocabulary.submit_review_item(
        user_id=207,
        written_form="IrrtümlichAngenommen",
        spoken_form="Irrtum",
        session=session,
    )
    session.commit()

    accepted = vocabulary.accept_to_user(item.id, actor_id=999, session=session)
    session.commit()
    entry_id = accepted.target_entry_id
    assert session.get(vocabulary.ClinicVocabularyEntry, entry_id) is not None

    # Undo
    undone = vocabulary.undo_decision(item.id, actor_id=999, session=session)
    session.commit()

    assert undone.status == vocabulary.REVIEW_STATUS_PENDING
    assert undone.target_entry_id is None
    # Entry in vocabulary should be deleted
    assert session.get(vocabulary.ClinicVocabularyEntry, entry_id) is None

    # Check history contains both decisions
    history = vocabulary.get_decision_history(item.id, session=session)
    assert len(history) == 2
    assert history[0].action == vocabulary.ACTION_ACCEPT_USER
    assert history[1].action == vocabulary.ACTION_UNDO


# ============================================================================
# 3. Precedence Rules & Effective Vocabulary (Zusammenführung)
# ============================================================================


def test_effective_vocabulary_user_overrides_pool(session):
    user_id = 301
    pool_id = "pool-cardio"

    # Pool has word with general spoken form
    vocabulary.add_vocabulary_entry(
        scope="pool",
        written_form="Hypertonie",
        spoken_form="Bluthochdruck",
        pool_id=pool_id,
        notes="Pool Definition",
        session=session,
    )
    # User has personal override with preferred spoken form
    vocabulary.add_vocabulary_entry(
        scope="user",
        written_form="Hypertonie",
        spoken_form="hoher Blutdruck",
        user_id=user_id,
        notes="Persönliche Präferenz",
        session=session,
    )
    session.commit()

    effective = vocabulary.get_effective_vocabulary(user_id=user_id, pool_ids=[pool_id], session=session)
    assert "Hypertonie" in effective
    assert effective["Hypertonie"]["spoken_form"] == "hoher Blutdruck"
    assert effective["Hypertonie"]["source"] == "user"
    assert effective["Hypertonie"]["notes"] == "Persönliche Präferenz"


def test_effective_vocabulary_user_suppression_overrides_pool_active(session):
    user_id = 302
    pool_id = "pool-cardio"

    # Active word in pool
    vocabulary.add_vocabulary_entry(
        scope="pool", written_form="Aspirin", pool_id=pool_id, status=vocabulary.STATUS_ACTIVE, session=session
    )
    # Suppressed in user (negative list)
    vocabulary.add_vocabulary_entry(
        scope="user", written_form="Aspirin", user_id=user_id, status=vocabulary.STATUS_SUPPRESSED, session=session
    )
    session.commit()

    # Active effective vocabulary should NOT contain Aspirin
    effective = vocabulary.get_effective_vocabulary(user_id=user_id, pool_ids=[pool_id], include_suppressed=False, session=session)
    assert "Aspirin" not in effective

    # When including suppressed, user's suppressed status is visible
    all_vocab = vocabulary.get_effective_vocabulary(user_id=user_id, pool_ids=[pool_id], include_suppressed=True, session=session)
    assert "Aspirin" in all_vocab
    assert all_vocab["Aspirin"]["status"] == vocabulary.STATUS_SUPPRESSED
    assert all_vocab["Aspirin"]["source"] == "user"


def test_effective_vocabulary_pool_suppressed_overridden_by_user_active(session):
    user_id = 303
    pool_id = "pool-cardio"

    # Suppressed in pool
    vocabulary.add_vocabulary_entry(
        scope="pool", written_form="Marcumar", pool_id=pool_id, status=vocabulary.STATUS_SUPPRESSED, session=session
    )
    # Explicitly added/active by user
    vocabulary.add_vocabulary_entry(
        scope="user", written_form="Marcumar", user_id=user_id, status=vocabulary.STATUS_ACTIVE, session=session
    )
    session.commit()

    effective = vocabulary.get_effective_vocabulary(user_id=user_id, pool_ids=[pool_id], session=session)
    assert "Marcumar" in effective
    assert effective["Marcumar"]["status"] == vocabulary.STATUS_ACTIVE
    assert effective["Marcumar"]["source"] == "user"


def test_effective_vocabulary_multi_pool_order(session):
    user_id = 304
    pool_a = "pool-a-priority"
    pool_b = "pool-b-fallback"

    vocabulary.add_vocabulary_entry(
        scope="pool", written_form="Beta-Blocker", spoken_form="Betablocker Eins", pool_id=pool_a, session=session
    )
    vocabulary.add_vocabulary_entry(
        scope="pool", written_form="Beta-Blocker", spoken_form="Betablocker Zwei", pool_id=pool_b, session=session
    )
    session.commit()

    # Pool A is listed first, so Pool A's definition wins
    effective = vocabulary.get_effective_vocabulary(user_id=user_id, pool_ids=[pool_a, pool_b], session=session)
    assert effective["Beta-Blocker"]["spoken_form"] == "Betablocker Eins"

    # Pool B listed first
    effective_b = vocabulary.get_effective_vocabulary(user_id=user_id, pool_ids=[pool_b, pool_a], session=session)
    assert effective_b["Beta-Blocker"]["spoken_form"] == "Betablocker Zwei"


def test_effective_vocabulary_case_insensitivity(session):
    user_id = 305
    pool_id = "pool-general"

    vocabulary.add_vocabulary_entry(
        scope="pool", written_form="ekg", spoken_form="E K G", pool_id=pool_id, session=session
    )
    vocabulary.add_vocabulary_entry(
        scope="user", written_form="EKG", spoken_form="Elektrokardiogramm", user_id=user_id, session=session
    )
    session.commit()

    effective = vocabulary.get_effective_vocabulary(user_id=user_id, pool_ids=[pool_id], session=session)
    # User's casing EKG should be preserved as canonical
    assert "EKG" in effective
    assert effective["EKG"]["spoken_form"] == "Elektrokardiogramm"
    assert effective["EKG"]["source"] == "user"


def test_effective_voice_commands_and_hints_precedence(session):
    user_id = 306
    pool_id = "pool-cardio"

    # Voice command: user overrides pool
    vocabulary.add_voice_command(
        scope="pool", trigger_phrase="neue Zeile", action_payload="pool_newline", pool_id=pool_id, session=session
    )
    vocabulary.add_voice_command(
        scope="user", trigger_phrase="Neue Zeile", action_payload="user_newline", user_id=user_id, session=session
    )

    # Pronunciation hint: user overrides pool
    vocabulary.add_pronunciation_hint(
        scope="pool", written_form="Stent", phonetic_hint="st-ent", pool_id=pool_id, session=session
    )
    vocabulary.add_pronunciation_hint(
        scope="user", written_form="Stent", phonetic_hint="scht-ent", user_id=user_id, session=session
    )
    session.commit()

    cmds = vocabulary.get_effective_voice_commands(user_id=user_id, pool_ids=[pool_id], session=session)
    assert cmds["Neue Zeile"]["action_payload"] == "user_newline"
    assert cmds["Neue Zeile"]["source"] == "user"

    hints = vocabulary.get_effective_pronunciation_hints(user_id=user_id, pool_ids=[pool_id], session=session)
    assert hints["Stent"]["phonetic_hint"] == "scht-ent"
    assert hints["Stent"]["source"] == "user"


# ============================================================================
# 4. Import & Export Tests (PAR-VOC-002)
# ============================================================================


def test_export_vocabulary_text(session):
    vocabulary.add_vocabulary_entry(
        scope="user", written_form="Beta-Blocker", spoken_form="Betablocker", user_id=401, session=session
    )
    vocabulary.add_vocabulary_entry(
        scope="user", written_form="Anamnese", user_id=401, session=session
    )
    session.commit()

    exported = vocabulary.export_vocabulary_text(scope="user", user_id=401, session=session)
    lines = exported.splitlines()
    assert lines[0] == "Anamnese"
    assert lines[1] == "Beta-Blocker\tBetablocker"


def test_import_vocabulary_text_rules(session):
    content = """
    # Kommentarzeile
    Anamnese
    Appendizitis\tBlinddarmentzündung
    
    Arhythmie\tHerzrhythmusstörung
    """
    res = vocabulary.import_vocabulary_text(
        content=content, scope="pool", pool_id="pool-test", duplicate_rule="replace", session=session
    )
    session.commit()
    assert res["added"] == 3

    # Duplicate skip
    skip_content = "Anamnese\tVorgeschichte\nNeuesWort"
    res_skip = vocabulary.import_vocabulary_text(
        content=skip_content, scope="pool", pool_id="pool-test", duplicate_rule="skip", session=session
    )
    session.commit()
    assert res_skip["added"] == 1
    assert res_skip["skipped"] == 1

    # Check Anamnese was NOT changed under skip
    entry = session.query(vocabulary.ClinicVocabularyEntry).filter_by(pool_id="pool-test", written_form="Anamnese").first()
    assert entry.spoken_form is None

    # Duplicate error
    with pytest.raises(vocabulary.VocabularyError) as exc:
        vocabulary.import_vocabulary_text(
            content="Anamnese", scope="pool", pool_id="pool-test", duplicate_rule="error", session=session
        )
    assert exc.value.code == "duplicate_entry"


# ============================================================================
# 5. Spoken search and confirmed bulk suppress/delete (PAR-VOC-006)
# ============================================================================


def _entry(session, written, spoken):
    entry = vocabulary.add_vocabulary_entry(
        scope="user", written_form=written, spoken_form=spoken, user_id=501, session=session
    )
    session.commit()
    return entry


def _find(session, query, mode, field):
    return [
        row.written_form for row in vocabulary.search_vocabulary(
            query=query, mode=mode, field=field, scope="user", user_id=501, session=session
        )
    ]


def test_search_written_spoken_and_both_in_all_four_modes(session):
    _entry(session, "Hypertonie", "Bluthochdruck")
    _entry(session, "Blutbild", "Labor")
    _entry(session, "Anamnese", None)

    # written: only the written form, so the spoken word "Bluthochdruck" misses.
    assert _find(session, "Hyper", "prefix", "written") == ["Hypertonie"]
    assert _find(session, "hochdruck", "suffix", "written") == []
    assert _find(session, "tonie", "contains", "written") == ["Hypertonie"]
    assert _find(session, "Anamnese", "exact", "written") == ["Anamnese"]

    # spoken: only the spoken form. An entry without one never matches.
    assert _find(session, "blut", "prefix", "spoken") == ["Hypertonie"]
    assert _find(session, "druck", "suffix", "spoken") == ["Hypertonie"]
    assert _find(session, "hoch", "contains", "spoken") == ["Hypertonie"]
    assert _find(session, "Labor", "exact", "spoken") == ["Blutbild"]
    assert _find(session, "Anamnese", "exact", "spoken") == []

    # all: either form. "Blut" starts the written form of one and the spoken form of the other.
    assert _find(session, "Blut", "prefix", "all") == ["Blutbild", "Hypertonie"]
    assert _find(session, "druck", "suffix", "all") == ["Hypertonie"]
    assert _find(session, "bor", "contains", "all") == ["Blutbild"]
    assert _find(session, "Anamnese", "exact", "all") == ["Anamnese"]

    # A query of "%" or "_" stays literal, it is not a wildcard.
    _entry(session, "100%", "Prozent")
    assert _find(session, "%", "contains", "written") == ["100%"]
    assert _find(session, "_", "contains", "written") == []


def test_search_rejects_an_unknown_field(session):
    with pytest.raises(vocabulary.VocabularyError) as exc:
        vocabulary.search_vocabulary(query="a", field="regex", scope="user", user_id=501, session=session)
    assert exc.value.code == "invalid_field"


def test_bulk_checks_every_version_then_suppresses_or_deletes_all(session):
    first = _entry(session, "Alpha", "a")
    second = _entry(session, "Beta", "b")
    items = [
        {"id": first.id, "expected_version": first.updated_at.isoformat()},
        {"id": second.id, "expected_version": second.updated_at.isoformat()},
    ]

    # One stale version rejects the whole list: nothing is suppressed.
    stale = [dict(items[0], expected_version="1999-01-01T00:00:00+00:00"), items[1]]
    with pytest.raises(vocabulary.VocabularyError) as exc:
        vocabulary.apply_vocabulary_bulk(stale, "suppress", session=session)
    assert exc.value.code == "stale_version"
    session.rollback()
    assert session.get(vocabulary.ClinicVocabularyEntry, first.id).status == vocabulary.STATUS_ACTIVE
    assert session.get(vocabulary.ClinicVocabularyEntry, second.id).status == vocabulary.STATUS_ACTIVE

    suppressed = vocabulary.apply_vocabulary_bulk(items, "suppress", session=session)
    session.commit()
    assert [row.status for row in suppressed] == [vocabulary.STATUS_SUPPRESSED, vocabulary.STATUS_SUPPRESSED]

    # A hard delete of the same versions now fails, because suppression moved them.
    with pytest.raises(vocabulary.VocabularyError):
        vocabulary.apply_vocabulary_bulk(items, "delete", session=session)
    session.rollback()
    assert session.get(vocabulary.ClinicVocabularyEntry, first.id) is not None

    fresh = [
        {"id": first.id, "expected_version": first.updated_at.isoformat()},
        {"id": second.id, "expected_version": second.updated_at.isoformat()},
    ]
    assert vocabulary.apply_vocabulary_bulk(fresh, "delete", session=session) == []
    session.commit()
    assert session.get(vocabulary.ClinicVocabularyEntry, first.id) is None
    assert session.get(vocabulary.ClinicVocabularyEntry, second.id) is None


def test_bulk_rejects_a_missing_or_repeated_entry_without_touching_the_rest(session):
    kept = _entry(session, "Gamma", "g")
    item = {"id": kept.id, "expected_version": kept.updated_at.isoformat()}
    for bad in (
        [item, {"id": "missing", "expected_version": item["expected_version"]}],
        [item, item],
        [],
    ):
        with pytest.raises(vocabulary.VocabularyError):
            vocabulary.apply_vocabulary_bulk(bad, "suppress", session=session)
        session.rollback()
    assert session.get(vocabulary.ClinicVocabularyEntry, kept.id).status == vocabulary.STATUS_ACTIVE
