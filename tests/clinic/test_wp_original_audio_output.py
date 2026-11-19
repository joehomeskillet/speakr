"""Synthetic evidence for PAR-EXP-009 (BT-EXP-009 slice). WG-EXP-009 stays open."""

import email
import hashlib
from email import policy

import pytest

from src.app import app, db
from src.clinic.models import ClinicAudioRevision, ClinicContextRevision, ClinicDictation, ClinicOriginal
from src.clinic.original_audio_output import (
    CHANNEL_AUDIO, FORMAT_EML, FORMAT_ORIGINAL, MAX_EML_AUDIO_BYTES, AudioOutputRefused,
    build_eml_artifact, export_allowed, export_original_audio,
)
from src.clinic.output_attempts import (
    OutputAttemptError, begin_output, bind_output_source, freeze_output_attempts, record_output_result,
)
from src.clinic.kis import LOOKUP_ABSENT
from src.clinic.revisions import add_human_revision

AUDIO = b"RIFF synthetic audio bytes"


@pytest.fixture
def case(world):
    with app.app_context():
        actor = world["user_a"]
        dictation = ClinicDictation(recording_id=world["rec_a"], author_user_id=actor)
        db.session.add(dictation)
        db.session.flush()
        audio = ClinicAudioRevision(dictation_id=dictation.id, content_hash="a" * 64)
        original = ClinicOriginal(dictation_id=dictation.id, content_hash=hashlib.sha256(AUDIO).hexdigest(),
                                  size_bytes=len(AUDIO), storage_key="originals/synthetic",
                                  receipt="stored", origin_ref="test")
        context = ClinicContextRevision(dictation_id=dictation.id, source_system="test",
                                        tenant_scope="site", case_ref="case", patient_ref="patient")
        db.session.add_all([audio, original, context])
        db.session.flush()
        text = add_human_revision(dictation, None, "Text", actor, original_version=original)
        source = bind_output_source(dictation, audio, text, context)
        yield dictation, actor, audio, text, context, original, source
        db.session.rollback()


def claim(case, fmt=FORMAT_ORIGINAL):
    dictation, actor, _, _, _, _, source = case
    attempt, = freeze_output_attempts(
        dictation, source, source, actor_user_id=actor, request_id=f"r-{fmt}", allowed=True,
        mode="working_copy", targets={(CHANNEL_AUDIO, "download"): {"format": fmt}})
    return begin_output({attempt.idempotency_key: attempt}, attempt.idempotency_key, dictation, source, allowed=True)


def kw(**o):
    base = dict(allowed=True, profile="managed_web_online", extension="wav")
    base.update(o)
    return base


def test_profile_capability():
    assert export_allowed("managed_web_online") and export_allowed("managed_web_offline")
    for profile in ("byod_internal", "import_agent", "kis_webview", "unknown"):
        assert not export_allowed(profile)


def test_exact_original_bytes_and_hash(case):
    original, source = case[5], case[6]
    claimed = claim(case)
    art = export_original_audio(claimed, source, original, lambda key: AUDIO, **kw())
    assert (art.data, art.sha256, art.size) == (AUDIO, original.content_hash, len(AUDIO))
    assert art.idempotency_key == claimed.idempotency_key and art.filename.endswith(".wav")
    assert record_output_result(claimed, claimed, LOOKUP_ABSENT).status == "failed"


def test_refusals(case):
    original, source = case[5], case[6]
    claimed = claim(case)
    read = lambda key: AUDIO  # noqa: E731
    with pytest.raises(AudioOutputRefused, match="not authorised"):
        export_original_audio(claimed, source, original, read, **kw(allowed=False))
    with pytest.raises(AudioOutputRefused, match="may not download"):
        export_original_audio(claimed, source, original, read, **kw(profile="byod_internal"))
    with pytest.raises(AudioOutputRefused, match="not download|may not"):
        export_original_audio(claimed, source, original, read, **kw(profile="kis_webview"))
    with pytest.raises(AudioOutputRefused, match="does not match"):
        export_original_audio(claimed, source, original, lambda key: AUDIO + b"x", **kw())
    with pytest.raises(AudioOutputRefused, match="invalid audio extension"):
        export_original_audio(claimed, source, original, read, **kw(extension="../x"))
    with pytest.raises(AudioOutputRefused, match="frozen original-audio"):
        export_original_audio(claim(case, FORMAT_EML), source, original, read, **kw())
    pending, = freeze_output_attempts(case[0], source, source, actor_user_id=case[1], request_id="p",
                                      allowed=True, mode="working_copy",
                                      targets={(CHANNEL_AUDIO, "download"): {"format": FORMAT_ORIGINAL}})
    with pytest.raises(AudioOutputRefused, match="claim the attempt"):
        export_original_audio(pending, source, original, read, **kw())


def test_stale_version_refused(case):
    dictation, actor, audio, text, context, original, source = case
    claimed = claim(case)
    newer = add_human_revision(dictation, text, "Neu", actor)
    current = bind_output_source(dictation, audio, newer, context)
    with pytest.raises(AudioOutputRefused, match="stale"):
        export_original_audio(claimed, current, original, lambda key: AUDIO, **kw())
    other = ClinicOriginal(id="f" * 36, dictation_id="foreign", content_hash=original.content_hash,
                           size_bytes=original.size_bytes, storage_key="x", receipt="r", origin_ref="t")
    with pytest.raises(AudioOutputRefused, match="another dictation"):
        export_original_audio(claimed, source, other, lambda key: AUDIO, **kw())


def test_eml_bounded_without_recipients(case):
    original, source = case[5], case[6]
    claimed = claim(case, FORMAT_EML)
    read = lambda key: AUDIO  # noqa: E731
    with pytest.raises(AudioOutputRefused, match="not allowed by policy"):
        build_eml_artifact(claimed, source, original, read, eml_policy_allows=False, **kw())
    with pytest.raises(AudioOutputRefused, match="may not download"):
        build_eml_artifact(claimed, source, original, read, eml_policy_allows=True, **kw(profile="byod_internal"))
    art = build_eml_artifact(claimed, source, original, read, eml_policy_allows=True, **kw())
    msg = email.message_from_bytes(art.data, policy=policy.SMTP)
    assert art.mimetype == "message/rfc822" and art.filename.endswith(".eml")
    assert all(msg[h] is None for h in ("To", "Cc", "Bcc", "From"))
    assert msg["X-Audio-SHA256"].strip() == original.content_hash
    attachment, = list(msg.iter_attachments())
    assert attachment.get_content() == AUDIO
    original.size_bytes = MAX_EML_AUDIO_BYTES + 1
    with pytest.raises(AudioOutputRefused, match="size bound"):
        build_eml_artifact(claimed, source, original, read, eml_policy_allows=True, **kw())
    assert issubclass(AudioOutputRefused, OutputAttemptError)
