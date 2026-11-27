"""Synthetic DB evidence only; browser copying and recipient paste remain unproven."""

from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import select

from src.app import app
from src.clinic.clipboard_ledger import AuditClipboardLedger
from src.clinic.models import (
    ClinicAudioRevision, ClinicContextRevision, ClinicDictation, ClinicOriginal,
    ClinicTranscriptRevision,
)
from src.clinic.output import list_outputs, record_output, CHANNEL_CLIPBOARD, FORMAT_FORMATTED
from src.clinic.output_attempts import FAILED, SUCCEEDED, UNKNOWN, OutputAttemptError, bind_output_source
from src.clinic.parity_receipts import ClinicParityReceipt
from src.clinic.revisions import add_human_revision
from src.clinic.routes_clipboard import prepare_clipboard, report_clipboard
from src.database import db


@pytest.fixture
def case(world):
    with app.app_context():
        actor = world['user_a']
        dictation = ClinicDictation(recording_id=world['rec_a'], author_user_id=actor)
        db.session.add(dictation)
        db.session.flush()
        audio = ClinicAudioRevision(dictation_id=dictation.id, content_hash='a' * 64)
        context = ClinicContextRevision(dictation_id=dictation.id, source_system='synthetic',
                                        tenant_scope='site', case_ref='case', patient_ref='patient')
        original = ClinicOriginal(dictation_id=dictation.id, content_hash='b' * 64, size_bytes=1,
                                  storage_key='originals/synthetic', receipt='stored', origin_ref='test')
        db.session.add_all([audio, context, original])
        db.session.flush()
        revision = add_human_revision(dictation, None, 'Synthetic <text>', actor, original_version=original)
        source = bind_output_source(dictation, audio, revision, context)
        db.session.commit()
        yield dict(dictation_id=dictation.id, revision_id=revision.id, source=source,
                   actor=actor, other=world['user_b'], request_id=str(uuid4()))
        db.session.rollback()


def objects(case):
    return (db.session.get(ClinicDictation, case['dictation_id']),
            db.session.get(ClinicTranscriptRevision, case['revision_id']), case['source'])


def prepare(case, **changes):
    options = dict(actor=case['actor'], request_id=case['request_id'], allowed=True)
    options.update(changes)
    return prepare_clipboard(AuditClipboardLedger(), *objects(case), **options)


def report(case, outcome, **changes):
    options = dict(actor=case['actor'], request_id=case['request_id'], outcome=outcome)
    options.update(changes)
    return report_clipboard(AuditClipboardLedger(), *objects(case), **options)


def test_fresh_worker_reads_claim_and_single_local_outcome(case):
    claim, flavors = prepare(case)
    assert flavors['text/plain'] == 'Synthetic <text>'
    db.session.commit()
    db.session.remove()
    assert AuditClipboardLedger()[claim.idempotency_key] == claim
    assert claim.status == UNKNOWN
    result = report(case, 'copied')
    db.session.commit()
    db.session.remove()
    assert AuditClipboardLedger()[claim.idempotency_key] == result
    assert result.status == SUCCEEDED
    with pytest.raises(OutputAttemptError):
        report(case, 'copied')
    assert len(list_outputs(objects(case)[0])) == 1
    rows = db.session.execute(select(ClinicParityReceipt)).scalars().all()
    assert len(rows) == 2
    assert all(row.result['recipient_status'] == UNKNOWN for row in rows)
    assert all('Synthetic <text>' not in str(row.result) for row in rows)


@pytest.mark.parametrize('outcome,status', [('aborted', UNKNOWN), ('denied', FAILED)])
def test_unfinished_or_denied_attempt_has_no_success_receipt(case, outcome, status):
    claim, _ = prepare(case)
    db.session.commit()
    db.session.remove()
    assert report(case, outcome).status == status
    db.session.commit()
    db.session.remove()
    assert AuditClipboardLedger()[claim.idempotency_key].status == status
    assert list_outputs(objects(case)[0]) == []
    if outcome == 'aborted':
        with pytest.raises(OutputAttemptError):
            prepare(case, request_id=str(uuid4()))
    else:
        retry, _ = prepare(case)
        assert retry.generation == 2


def test_scope_payload_and_deletion_are_enforced(case):
    claim, _ = prepare(case)
    db.session.commit()
    for changes in ({'actor': case['other']}, {'request_id': str(uuid4())}):
        with pytest.raises(OutputAttemptError):
            report(case, 'copied', **changes)
    ledger = AuditClipboardLedger()
    with pytest.raises(OutputAttemptError):
        ledger[claim.idempotency_key] = replace(claim, payload_hash='c' * 64, status=SUCCEEDED)
    with pytest.raises(OutputAttemptError):
        del ledger[claim.idempotency_key]
    assert ledger[claim.idempotency_key] == claim
    assert list(ledger) == [claim.idempotency_key] and len(ledger) == 1


def test_stale_outcome_rolls_back_duplicate_local_receipt(case):
    claim, _ = prepare(case)
    db.session.commit()
    report(case, 'copied')
    db.session.commit()
    db.session.remove()
    # A second worker may already have read UNKNOWN before the first one committed.
    dictation, revision, _ = objects(case)
    record_output(dictation, revision, case['actor'], CHANNEL_CLIPBOARD, 'browser',
                  text_format=FORMAT_FORMATTED)
    with pytest.raises(OutputAttemptError):
        AuditClipboardLedger()[claim.idempotency_key] = replace(claim, status=SUCCEEDED)
    assert len(list_outputs(objects(case)[0])) == 1


def test_claim_and_outcome_follow_caller_transaction(case):
    claim, _ = prepare(case)
    db.session.rollback()
    assert len(AuditClipboardLedger()) == 0
    claim, _ = prepare(case)
    db.session.commit()
    report(case, 'copied')
    db.session.rollback()
    db.session.remove()
    assert AuditClipboardLedger()[claim.idempotency_key] == claim
    assert list_outputs(objects(case)[0]) == []


@pytest.mark.parametrize('operation', ['prepare', 'copied'])
def test_competing_worker_sessions_commit_only_one_transition(case, operation):
    claim, _ = prepare(case)
    if operation == 'copied':
        db.session.commit()
    else:
        db.session.rollback()
    db.session.remove()
    barrier = Barrier(2)

    def worker():
        with app.app_context():
            ledger = AuditClipboardLedger()
            observed = ledger.get(claim.idempotency_key)
            barrier.wait(timeout=10)
            try:
                if operation == 'copied':
                    assert observed == claim
                    dictation, revision, _ = objects(case)
                    record_output(dictation, revision, case['actor'], CHANNEL_CLIPBOARD,
                                  'browser', text_format=FORMAT_FORMATTED)
                    ledger[claim.idempotency_key] = replace(claim, status=SUCCEEDED)
                else:
                    assert observed is None
                    ledger[claim.idempotency_key] = claim
                db.session.commit()
                return 'committed'
            except OutputAttemptError:
                return 'conflict'

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: worker(), range(2)))
    assert sorted(results) == ['committed', 'conflict']
    assert len(list_outputs(objects(case)[0])) == (1 if operation == 'copied' else 0)
    assert len(AuditClipboardLedger()) == 1


def test_other_dictation_and_unrelated_receipts_are_isolated(case, world):
    first, _ = prepare(case)
    db.session.commit()
    other = ClinicDictation(recording_id=world['rec_b'], author_user_id=case['other'])
    db.session.add(other)
    db.session.commit()
    second = replace(first, source=replace(first.source, dictation_id=other.id),
                     idempotency_key='f' * 64)
    ledger = AuditClipboardLedger()
    assert ledger.get(second.idempotency_key) is None
    ledger[second.idempotency_key] = second
    db.session.add(ClinicParityReceipt(scope_key='a' * 64, digest='unrelated', result={'ok': True}))
    db.session.commit()
    assert set(ledger) == {first.idempotency_key, second.idempotency_key}
    assert ledger[first.idempotency_key] == first
    assert ledger[second.idempotency_key] == second
    with pytest.raises(OutputAttemptError):
        ledger[first.idempotency_key] = replace(second, idempotency_key=first.idempotency_key)


def test_mapping_expansion_cannot_replace_first_read_token(case):
    claim, _ = prepare(case)
    db.session.rollback()
    ledger = AuditClipboardLedger()
    assert ledger.get(claim.idempotency_key) is None
    with app.app_context():
        other = AuditClipboardLedger()
        assert other.get(claim.idempotency_key) is None
        other[claim.idempotency_key] = claim
        db.session.commit()
    # prepare_clipboard expands the mapping after its first .get(). A worker
    # committing between those reads must not turn this new claim into an abort.
    assert {**ledger}[claim.idempotency_key] == claim
    with pytest.raises(OutputAttemptError):
        ledger[claim.idempotency_key] = claim
    assert ledger[claim.idempotency_key] == claim
