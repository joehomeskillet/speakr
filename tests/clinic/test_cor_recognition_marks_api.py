from src.app import app, db
from src.clinic import revisions
from src.clinic.audio_revisions import current_audio_revision
from src.clinic.correction import ingest_asr_result
from src.clinic.correction_recognition_marks import marks_for, store_asr_marks
from src.clinic.models import ClinicDictation
from test_correction import BODY, SEGMENTS
from test_wp_v8_save_contract import _make, get


def _binding(ids):
    dictation = db.session.get(ClinicDictation, ids["dictation"])
    return dictation, revisions.current_revision(dictation), current_audio_revision(dictation)


def test_ingested_asr_and_command_metadata_keep_provenance_in_snapshot(monkeypatch):
    client, _, ids = _make(monkeypatch, True)
    with app.app_context():
        dictation, head, audio = _binding(ids)
        segments = [
            {"start": 0, "end": 1, "text": "Anamnese", "avg_logprob": -1.5},
            {"start": 1, "end": 2, "text": "Neuer Abschnitt", "kind": "command",
             "source": "fixture-asr:1/command/7", "confidence": 0.8},
            {"start_ms": 2000, "end_ms": 3000, "kind": "unrecognized",
             "source": "fixture-asr:1/gap/2"},
            {"start_ms": 3000, "end_ms": 4000, "text": "Befund", "kind": "recognition",
             "source": "fixture-asr:1/segment/3", "confidence": 0},
            {"start_ms": 4000, "end_ms": 5000, "text": ""},
            {"start_ms": 5000, "end_ms": 6000, "text": "Unbelegt", "kind": "command",
             "source": " ", "confidence": 1},
        ]
        ingest_asr_result(
            dictation, BODY, SEGMENTS, "fixture-asr:1", recognition_run_id="run-source",
            recognition_segments=(segment for segment in segments),
        )
        db.session.commit()

        response = get(client, ids)
        assert response.status_code == 200
        spans = response.json["recognition_evidence"]
        assert [(row["kind"], row["start_ms"], row["end_ms"], row["source"], row["confidence"])
                for row in spans] == [
            ("uncertain", 0, 1000, "fixture-asr:1/segment/avg_logprob", None),
            ("command", 1000, 2000, "fixture-asr:1/command/7", 0.8),
            ("unrecognized", 2000, 3000, "fixture-asr:1/gap/2", None),
            ("recognition", 3000, 4000, "fixture-asr:1/segment/3", 0),
        ]
        assert all(row["run_id"] == "run-source" for row in spans)
        assert all(row["text_revision_id"] == head.id for row in spans)
        assert all(row["audio_revision_id"] == audio.id for row in spans)


def test_missing_confidence_stays_unknown_and_empty_text_makes_no_gap(monkeypatch):
    client, _, ids = _make(monkeypatch, True)
    with app.app_context():
        dictation, head, audio = _binding(ids)
        store_asr_marks(
            dictation, audio, head, run_id="with-evidence", model_ref="fixture-asr:1",
            segments=[
                {"start": 0, "end": 1, "text": "Unsicher", "kind": "uncertain",
                 "source": "fixture-asr:1/segment/uncertain"},
                {"start": 1, "end": 2, "text": ""},
            ], duration_ms=10000,
        )
        db.session.commit()
        measured = get(client, ids).json["recognition_evidence"]
        assert len(measured) == 1
        assert measured[0]["kind"] == "uncertain"
        assert "confidence" in measured[0] and measured[0]["confidence"] is None
        assert measured[0]["source"] == "fixture-asr:1/segment/uncertain"

        store_asr_marks(
            dictation, audio, head, run_id="without-metadata", model_ref="fixture-asr:1",
            segments=[{"start": 0, "end": 1, "text": ""},
                      {"start": 1, "end": 2, "text": "Pause"}],
            duration_ms=10000,
        )
        db.session.commit()
        assert get(client, ids).json["recognition_evidence"] == []
        retained = marks_for(dictation.id, audio.id, head.id, run_id="with-evidence")
        assert len(retained) == 1 and retained[0].confidence is None
        assert marks_for(dictation.id, audio.id, head.id, run_id="without-metadata") == []
