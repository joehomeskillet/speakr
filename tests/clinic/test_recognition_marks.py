"""Recognition measurements and their path to the correction snapshot."""

import math

import pytest

from src.app import app, db
from src.clinic import revisions
from src.clinic.audio_revisions import init_audio_revision
from src.clinic.correction_recognition_marks import marks_for
from src.clinic.models import ClinicDictation, ClinicOriginal
from src.services.transcription.connectors.asr_endpoint import ASREndpointConnector
from test_wp_asr_engine_wire import _connector, _job, _run, ctx  # noqa: F401
from test_wp_v8_asr_links_ingest import _dictation
from test_wp_v8_save_contract import _make, get


def test_phrase_at_uses_half_open_ranges_and_earliest_start():
    from src.clinic.recognition_marks import RecognitionMarksError, phrase_at

    short = {"start_ms": 500, "end_ms": 1000, "text": "Short"}
    long = {"start_time": 0, "end_time": 1, "sentence": "Long"}
    following = {"start": 1, "end": 2, "text": "Following"}
    segments = [short, following, {"start": False, "end": 8}, long]
    assert phrase_at(segments, 999) is long
    assert phrase_at(segments, 1000) is following
    assert phrase_at(segments, 2000) is None
    with pytest.raises(RecognitionMarksError, match="invalid_position_ms"):
        phrase_at(segments, True)


def test_only_real_measurements_produce_marks():
    from src.clinic.recognition_marks import Mark, recognition_marks

    report = recognition_marks([
        {"start": 0, "end": 1, "text": "First", "avg_logprob": -1.5,
         "no_speech_prob": 0.8, "words": [
             {"word": "first", "start": 0.2, "end": 0.7, "probability": 0},
             {"word": "missing", "start": 0.7, "end": 1},
         ]},
        {"start_ms": 1000, "end_ms": 2000, "text": "Second", "avg_logprob": math.nan,
         "no_speech_prob": 1.1, "words": [
             {"word": "exactly", "start_ms": 1000, "end_ms": 2000, "probability": 0.5},
         ]},
    ])
    assert report.marks == (
        Mark(0, 1000, "low_confidence", "segment", -1.5),
        Mark(0, 1000, "no_speech", "segment", 0.8),
        Mark(200, 700, "low_confidence", "word", 0),
    )
    assert report.unknown_count == 3
    assert report.confidence_available is True
    missing = recognition_marks([{"start": 0, "end": 1, "text": "No evidence"}])
    assert missing.marks == () and missing.unknown_count == 2
    assert missing.confidence_available is False


def test_local_connector_measurements_reach_revision_bound_store(ctx):
    from src.clinic.correction_recognition_marks import current_marks

    recording, dictation, original = _dictation(ctx)
    audio = init_audio_revision(dictation, original, 3000)
    db.session.commit()
    job = _job(recording, {})
    connector = _connector()
    connector.transcribe.return_value = ASREndpointConnector._parse_response(connector, {
        "text": "Eins Zwei",
        "segments": [
            {"text": "Eins", "start": 0, "end": 1, "avg_logprob": -1.5},
            {"text": "Zwei", "start": 1, "end": 2, "no_speech_prob": 0.2, "words": [
                {"word": "Zwei", "start": 1.3, "end": 1.8, "probability": 0.3},
            ]},
        ],
    })
    _run(recording, connector, job)
    head = revisions.current_revision(dictation)
    assert head.body == "Eins\nZwei"
    rows = current_marks(dictation.id, audio.id, head.id)
    assert [(row.start_ms, row.end_ms, row.kind, row.confidence) for row in rows] == [
        (0, 1000, "uncertain", None),
        (1300, 1800, "uncertain", 0.3),
    ]
    assert rows[0].source.endswith("/segment/avg_logprob")
    assert rows[1].source.endswith("/word/probability")
    assert marks_for(dictation.id, audio.id, head.id, run_id=rows[0].run_id) == rows


def test_correction_snapshot_uses_exact_evidence_and_clears_an_unmeasured_run(monkeypatch):
    from src.clinic.correction_recognition_marks import add_mark, store_asr_marks

    client, _, ids = _make(monkeypatch, True)
    with app.app_context():
        dictation = db.session.get(ClinicDictation, ids["dictation"])
        head = revisions.current_revision(dictation)
        original = db.session.get(ClinicOriginal, head.original_version_id)
        audio = init_audio_revision(dictation, original, 10000)
        for run_id, kind, start in (
            ("older", "uncertain", 0), ("selected", "uncertain", 100), ("selected", "command", 400),
        ):
            add_mark(dictation, audio, head, run_id=run_id, kind=kind,
                     source="fixture-asr", start_ms=start, end_ms=start + 100)
        def select_run(run_id):
            store_asr_marks(dictation, audio, head, run_id=run_id, model_ref="fixture-asr",
                            segments=(), duration_ms=10000)

        select_run("selected")
        db.session.commit()
        result = get(client, ids).json
        assert result["audio_revision_id"] == audio.id and result["text_revision_id"] == head.id
        assert [(mark["kind"], mark["start_ms"], mark["end_ms"])
                for mark in result["recognition_spans"]] == [("uncertain", 100, 200)]

        select_run("unmeasured")
        db.session.commit()
        assert get(client, ids).json["recognition_spans"] == []
        assert len(marks_for(dictation.id, audio.id, head.id, run_id="selected")) == 2
