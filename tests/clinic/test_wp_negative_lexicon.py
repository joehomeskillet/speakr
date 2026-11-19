"""PAR-VOC-003 slice tests. The fake engine is synthetic and proves no real engine effect."""

from __future__ import annotations

from dataclasses import replace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.clinic import negative_lexicon as nl
from src.clinic import vocabulary
from src.clinic.asr_capabilities import (
    AsrCapability,
    AsrCapabilityRegistry,
    AsrTarget,
    CapabilityEvidence,
)
from src.clinic.pools import ClinicAuthorPoolMember

TARGET = AsrTarget("http://127.0.0.1:9000", "test-engine", "e1", "test-model", "m1", "{}")
PROOF = CapabilityEvidence(AsrCapability.NEGATIVE_LEXICON, TARGET, "pair-v1", "fixture:pair-1",
                           "runtime_passed")


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'nl.db'}")
    vocabulary.create_tables(bind=engine)
    ClinicAuthorPoolMember.__table__.create(bind=engine, checkfirst=True)
    sess = sessionmaker(bind=engine)()
    sess.add(ClinicAuthorPoolMember(pool_id="p1", user_id=1, created_by=1))
    sess.commit()
    yield sess
    sess.close()
    engine.dispose()


def _vocab(session, scope, word, status, **ref):
    vocabulary.add_vocabulary_entry(scope, word, status=status, session=session, **ref)
    session.commit()


def _apply(session, engine_apply, registry=None, **kw):
    args = dict(actor_id=1, user_id=1, pool_ids=["p1"], session=session,
                registry=registry or AsrCapabilityRegistry([PROOF]), target=TARGET,
                engine_apply=engine_apply)
    args.update(kw)
    return nl.apply_negative_lexicon(**args)


def test_precedence_and_reactivation_with_membership(session):
    _vocab(session, "pool", "Alpha", "suppressed", pool_id="p1")
    _vocab(session, "pool", "Beta", "suppressed", pool_id="p1")
    _vocab(session, "user", "Beta", "active", user_id=1)  # user reactivation wins
    _vocab(session, "user", "Gamma", "suppressed", user_id=1)
    _vocab(session, "pool", "Foreign", "suppressed", pool_id="p2")
    assert nl.resolve_negative_terms(1, ["p1", "p2"], session) == ("Alpha", "Gamma")


def test_membership_loss_drops_pool_suppression(session):
    _vocab(session, "pool", "Alpha", "suppressed", pool_id="p1")
    session.query(ClinicAuthorPoolMember).delete()
    session.commit()
    assert nl.resolve_negative_terms(1, ["p1"], session) == ()


def test_applied_records_versions(session):
    _vocab(session, "user", "Gamma", "suppressed", user_id=1)
    seen = []
    run = _apply(session, seen.append)
    assert run.status == nl.APPLIED and seen == [("Gamma",)]
    assert run.lexicon_version == nl.lexicon_version(["Gamma"])
    assert (run.engine_revision, run.model_revision, run.evidence_ref) == ("e1", "m1", "fixture:pair-1")
    assert run.parity_item_open is True


def test_unsupported_engine_is_capability_unavailable(session):
    _vocab(session, "user", "Gamma", "suppressed", user_id=1)
    seen = []
    run = _apply(session, seen.append, registry=AsrCapabilityRegistry([]))
    assert run.status == "CAPABILITY_UNAVAILABLE" and seen == [] and run.parity_item_open


def test_stale_engine_revision_has_no_proof(session):
    stale = replace(TARGET, engine_revision="e2")
    assert _apply(session, lambda t: None, target=stale).status == "CAPABILITY_UNAVAILABLE"


def test_unauthorized_actor(session):
    seen = []
    assert _apply(session, seen.append, actor_id=2).status == nl.FORBIDDEN and seen == []


def test_stale_lexicon_version(session):
    _vocab(session, "user", "Gamma", "suppressed", user_id=1)
    old = nl.lexicon_version(["Gamma"])
    _vocab(session, "user", "Delta", "suppressed", user_id=1)
    seen = []
    run = _apply(session, seen.append, expected_version=old)
    assert run.status == nl.STALE_VERSION and seen == []


def test_retry_then_success_and_failure(session):
    _vocab(session, "user", "Gamma", "suppressed", user_id=1)
    calls = []

    def flaky(terms):
        calls.append(terms)
        if len(calls) == 1:
            raise RuntimeError("boom")

    assert _apply(session, flaky).status == nl.APPLIED and len(calls) == 2

    def broken(terms):
        raise RuntimeError("boom")

    run = _apply(session, broken)
    assert run.status == nl.FAILED and run.error == "RuntimeError" and run.evidence_ref == ""


def test_controlled_audio_pair_change():
    def recognize(audio, terms):
        return "Gamma" if audio == b"a" and not terms else "gamma-free"

    assert nl.recognition_changed(recognize, b"a", ["Gamma"]) is True
    assert nl.recognition_changed(recognize, b"b", ["Gamma"]) is False
