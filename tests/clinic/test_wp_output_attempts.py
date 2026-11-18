"""Synthetic service evidence for the independent-target part of BT-EXP-001."""

from datetime import datetime, timezone

import pytest

from src.app import app, db
from src.clinic.kis import LOOKUP_ABSENT, LOOKUP_CONFIRMED, LOOKUP_UNKNOWN
from src.clinic.models import ClinicAudioRevision, ClinicContextRevision, ClinicDictation, ClinicOriginal
from src.clinic.output import CHANNEL_CLIPBOARD, FORMAT_PLAIN, is_current_output, record_output
from src.clinic.output_attempts import (
    FAILED, PENDING, SUCCEEDED, UNKNOWN, OutputAttemptError, begin_output, bind_output_source,
    cancel_output, freeze_output_attempts, record_output_result,
)
from src.clinic.release import create_release, transfer_work_state
from src.clinic.revisions import add_human_revision


@pytest.fixture
def output_case(world):
    with app.app_context():
        actor = world["user_a"]
        dictation = ClinicDictation(recording_id=world["rec_a"], author_user_id=actor)
        db.session.add(dictation)
        db.session.flush()
        audio = ClinicAudioRevision(dictation_id=dictation.id, content_hash="a" * 64)
        original = ClinicOriginal(dictation_id=dictation.id, content_hash="b" * 64, size_bytes=1,
                                  storage_key="originals/synthetic", receipt="stored", origin_ref="test")
        context = ClinicContextRevision(dictation_id=dictation.id, source_system="test",
                                        tenant_scope="site", case_ref="case", patient_ref="patient")
        db.session.add_all([audio, original, context])
        db.session.flush()
        text = add_human_revision(dictation, None, "Synthetischer Text", actor, original_version=original)
        source = bind_output_source(dictation, audio, text, context)
        yield dictation, actor, audio, text, context, source
        db.session.rollback()


def freeze(case, **overrides):
    dictation, actor, _, _, _, source = case
    args = dict(actor_user_id=actor, request_id="test-request", allowed=True, mode="working_copy",
                targets={(CHANNEL_CLIPBOARD, "browser"): {"format": FORMAT_PLAIN},
                         ("RTF", "download"): {"format": "rtf", "renderer": "1"}})
    args.update(overrides)
    current = args.pop("current", source)
    return freeze_output_attempts(dictation, source, current, **args)


def test_frozen_bindings_keys_and_independent_outcomes(output_case):
    dictation, actor, audio, text, _, source = output_case
    clipboard, rtf = freeze(output_case)
    assert clipboard.source == rtf.source == source
    assert (source.audio_hash, source.text_hash) == (audio.content_hash, text.normalised_text_hash)
    assert len(source.context_hash) == 64
    assert clipboard.document_format_hash != rtf.document_format_hash
    assert clipboard.idempotency_key != rtf.idempotency_key
    assert (clipboard, rtf) == freeze(output_case)
    settings = {("RTF", "download"): {"format": "rtf", "renderer": "2"}}
    changed, = freeze(output_case, targets=settings)
    assert changed.idempotency_key == rtf.idempotency_key
    assert changed.payload_hash != rtf.payload_hash
    assert changed.document_format_hash != rtf.document_format_hash
    settings[("RTF", "download")]["renderer"] = "3"
    assert dict(changed.format_settings)["renderer"] == "2"

    clip_key, rtf_key = clipboard.idempotency_key, rtf.idempotency_key
    ledger = {clip_key: clipboard, rtf_key: rtf}
    claim = begin_output(ledger, clip_key, dictation, source, allowed=True)
    receipt = record_output(dictation, text, actor, CHANNEL_CLIPBOARD, "browser")
    ledger[clip_key] = record_output_result(claim, claim, LOOKUP_CONFIRMED)
    assert is_current_output(receipt) and receipt.text_hash == source.text_hash
    assert ledger[rtf_key].status == PENDING
    rtf_claim = begin_output(ledger, rtf_key, dictation, source, allowed=True)
    ledger[rtf_key] = record_output_result(rtf_claim, rtf_claim, LOOKUP_ABSENT)
    assert (ledger[clip_key].status, ledger[rtf_key].status) == (SUCCEEDED, FAILED)
    with pytest.raises(OutputAttemptError, match="cannot be started"):
        begin_output(ledger, clip_key, dictation, source, allowed=True)


def test_unknown_blocks_retry_and_new_channel_until_reconciled(output_case):
    dictation, _, _, _, _, source = output_case
    clipboard, rtf = freeze(output_case)
    fallback, = freeze(output_case, request_id="another-request", targets={("TEXT", "download"): {"format": "txt"}})
    key = rtf.idempotency_key
    ledger = {row.idempotency_key: row for row in (clipboard, rtf, fallback)}
    claim = ledger[key] = begin_output(ledger, key, dictation, source, allowed=True)
    assert claim.status == UNKNOWN
    assert record_output_result(claim, claim, LOOKUP_UNKNOWN) == claim
    for other_key in ledger:
        with pytest.raises(OutputAttemptError, match="reconcile"):
            begin_output(ledger, other_key, dictation, source, allowed=True)
    with pytest.raises(OutputAttemptError, match="unresolved claim"):
        record_output_result(clipboard, claim, LOOKUP_CONFIRMED)
    ledger[key] = record_output_result(claim, claim, LOOKUP_ABSENT)
    retry = begin_output(ledger, key, dictation, source, allowed=True)
    assert retry.idempotency_key == key
    with pytest.raises(OutputAttemptError, match="unresolved claim"):
        record_output_result(retry, claim, LOOKUP_CONFIRMED)
    assert record_output_result(retry, retry, LOOKUP_CONFIRMED).status == SUCCEEDED


def test_cancelled_target_cannot_be_sent_and_does_not_confirm_another(output_case):
    dictation, _, _, _, _, source = output_case
    clipboard, rtf = freeze(output_case)
    cancelled = cancel_output(rtf)
    ledger = {row.idempotency_key: row for row in (clipboard, cancelled)}
    assert cancelled.status == FAILED and clipboard.status == PENDING
    with pytest.raises(OutputAttemptError, match="cannot be started"):
        begin_output(ledger, cancelled.idempotency_key, dictation, source, allowed=True)
    assert begin_output(ledger, clipboard.idempotency_key, dictation, source, allowed=True).status == UNKNOWN


def test_denied_stale_and_work_transfer_do_not_authorise_final_output(output_case, world):
    dictation, actor, audio, text, context, source = output_case
    now = datetime(2026, 11, 18, tzinfo=timezone.utc)
    with pytest.raises(OutputAttemptError, match="not authorised"):
        freeze(output_case, allowed=False)
    transfer = transfer_work_state(dictation, actor, world["user_b"], now)
    assert freeze(output_case)[0].release_id is None
    with pytest.raises(OutputAttemptError, match="matching release"):
        freeze(output_case, mode="accepted", release_id=transfer.id)
    with pytest.raises(OutputAttemptError, match="bound KIS draft"):
        freeze(output_case, targets={("KIS", "test"): {"format": "document"}})
    release = create_release(dictation, audio, text, context, source.text_hash, actor,
                             {"allowed": True}, now, document_type="report", release_type="clinical")
    accepted, _ = freeze(output_case, mode="accepted", release_id=release.id)
    assert accepted.release_id == release.id
    ledger = {accepted.idempotency_key: accepted}
    assert begin_output(ledger, accepted.idempotency_key, dictation, source, allowed=True).release_id == release.id
    with pytest.raises(OutputAttemptError, match="not authorised"):
        begin_output(ledger, accepted.idempotency_key, dictation, source, allowed=False)
    updated = add_human_revision(dictation, text, "Neue synthetische Version", actor)
    current = bind_output_source(dictation, audio, updated, context)
    with pytest.raises(OutputAttemptError, match="stale"):
        freeze(output_case, current=current)
    with pytest.raises(OutputAttemptError, match="stale"):
        begin_output(ledger, accepted.idempotency_key, dictation, current, allowed=True)
    with pytest.raises(OutputAttemptError, match="matching release"):
        begin_output(ledger, accepted.idempotency_key, dictation, source, allowed=True)
    with pytest.raises(OutputAttemptError, match="same dictation"):
        bind_output_source(dictation, audio, text, ClinicContextRevision(id=context.id, dictation_id="foreign"))
