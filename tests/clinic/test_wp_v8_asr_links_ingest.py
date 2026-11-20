"""ASR segment links at ingest (v8-41).

Synthetic segments and a mocked connector only. Proves the server wiring of
the first ASR revision and its millisecond links, not recognition quality,
audio alignment accuracy or any device.
"""

from __future__ import annotations

import json
import uuid

from src.app import db
from src.clinic import revisions
from src.clinic.correction import ClinicTextAudioLink, ingest_asr_result
from src.clinic.models import ClinicDictation, ClinicOriginal
from src.models import Recording
from test_wp_asr_engine_wire import _connector, _recording, _run, ctx  # noqa: F401 - ctx is a fixture

MODEL = "stub-asr:1"


def _stored(*segments) -> str:
    return json.dumps([
        {"speaker": "SPEAKER_00", "sentence": text, "start_time": start, "end_time": end}
        for text, start, end in segments
    ])


def _plain(stored: str) -> str:
    return "\n".join(seg["sentence"] for seg in json.loads(stored))


def _dictation(user, transcription=None):
    rec = _recording(user, transcription=transcription)
    dictation = ClinicDictation(recording_id=rec.id, author_user_id=user.id)
    db.session.add(dictation)
    db.session.flush()
    original = ClinicOriginal(dictation_id=dictation.id, content_hash="o" * 64, size_bytes=1,
                              storage_key="originals/" + uuid.uuid4().hex, receipt="stored",
                              origin_ref="device:test")
    db.session.add(original)
    db.session.flush()
    db.session.commit()
    return rec, dictation, original


def _links(dictation):
    return db.session.query(ClinicTextAudioLink).filter_by(dictation_id=dictation.id).order_by(
        ClinicTextAudioLink.seq).all()


def test_first_asr_revision_gets_ms_links(ctx):
    _rec, dictation, original = _dictation(ctx)
    stored = _stored(("Erster Satz.", 0.0, 1.25), ("Zweiter Satz.", 1.25, 2.5))
    links = ingest_asr_result(dictation, _plain(stored), stored, MODEL)
    head = revisions.current_revision(dictation)
    assert head.kind == "ASR" and head.body == "Erster Satz.\nZweiter Satz."
    assert [(link.audio_start_ms, link.audio_end_ms) for link in links] == [(0, 1250), (1250, 2500)]
    assert [head.body[link.text_start:link.text_end] for link in links] == ["Erster Satz.", "Zweiter Satz."]
    assert {link.transcript_revision_id for link in links} == {head.id}
    # The audio side of the binding runs through the ASR revision's original.
    assert head.original_version_id == original.id
    assert {link.origin for link in links} == {"AUTO"}


def test_repeat_creates_no_duplicates(ctx):
    _rec, dictation, _original = _dictation(ctx)
    stored = _stored(("Erster Satz.", 0.0, 1.25))
    ingest_asr_result(dictation, _plain(stored), stored, MODEL)
    ingest_asr_result(dictation, _plain(stored), stored, MODEL)
    assert len(_links(dictation)) == 1
    assert len(revisions.history(dictation)) == 1


def test_text_without_times_creates_no_links(ctx):
    _rec, dictation, _original = _dictation(ctx)
    assert ingest_asr_result(dictation, "Nur Text ohne Zeiten.", "Nur Text ohne Zeiten.", MODEL) == []
    untimed = json.dumps([{"speaker": "S", "sentence": "Ohne Zeit.", "start_time": None, "end_time": None}])
    other_rec, other, _o = _dictation(ctx)
    assert ingest_asr_result(other, "Ohne Zeit.", untimed, MODEL) == []
    assert _links(dictation) == [] and _links(other) == []
    assert len(revisions.history(dictation)) == 1


def test_asr_head_with_other_text_is_left_alone(ctx):
    _rec, dictation, original = _dictation(ctx)
    revisions.add_asr_revision(dictation, original, "Erster Entwurf", MODEL)
    stored = _stored(("Neuer Entwurf.", 0.0, 1.0))
    assert ingest_asr_result(dictation, _plain(stored), stored, MODEL) == []
    assert [row.body for row in revisions.history(dictation)] == ["Erster Entwurf"]


def test_processing_hook_links_the_first_asr_revision(ctx):
    rec, dictation, original = _dictation(ctx)
    connector = _connector(text="Erster Satz. Zweiter Satz.")
    from src.services.transcription.base import TranscriptionSegment
    connector.transcribe.return_value.segments = [
        TranscriptionSegment(text="Erster Satz.", speaker="SPEAKER_00", start_time=0.5, end_time=1.5),
        TranscriptionSegment(text="Zweiter Satz.", speaker="SPEAKER_00", start_time=1.5, end_time=3.0),
    ]
    connector.transcribe.return_value.to_storage_format.return_value = _stored(
        ("Erster Satz.", 0.5, 1.5), ("Zweiter Satz.", 1.5, 3.0))
    connector.supports_diarization = True
    connector.default_diarize = True
    _run(rec, connector)
    db.session.expire_all()
    head = revisions.current_revision(dictation)
    assert head is not None and head.kind == "ASR"
    assert head.original_version_id == original.id
    assert [(link.audio_start_ms, link.audio_end_ms) for link in _links(dictation)] == [(500, 1500), (1500, 3000)]
    assert db.session.get(Recording, rec.id).transcription is not None


def test_processing_hook_without_times_adds_no_links(ctx):
    rec, dictation, _original = _dictation(ctx)
    _run(rec, _connector(text="Nur Text."))
    db.session.expire_all()
    assert revisions.current_revision(dictation).body == "Nur Text."
    assert _links(dictation) == []


def test_clinic_mode_off_leaves_processing_untouched(ctx, monkeypatch):
    monkeypatch.delenv("CLINIC_MODE", raising=False)
    rec, dictation, _original = _dictation(ctx)
    _run(rec, _connector(text="Nur Text."))
    db.session.expire_all()
    assert revisions.history(dictation) == []
    assert _links(dictation) == []
    assert db.session.get(Recording, rec.id).transcription == "Nur Text."


def test_failed_link_step_logs_no_transcript_text(ctx, monkeypatch, caplog):
    import logging

    from src.tasks.processing import _link_asr_segments_at_ingest

    _rec, dictation, _original = _dictation(ctx)
    secret = "Vertraulicher Befund Eins"

    def _boom(*_args, **_kwargs):
        raise RuntimeError(f"INSERT failed [parameters: ('{secret}',)]")

    monkeypatch.setattr("src.clinic.correction.ingest_asr_result", _boom)
    with caplog.at_level(logging.INFO):
        assert _link_asr_segments_at_ingest(dictation, secret, secret, MODEL) is False
    assert "not stored" in caplog.text
    assert secret not in caplog.text
