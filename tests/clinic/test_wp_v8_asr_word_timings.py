"""ASR word timings (v8-83): opt-in connector granularity and first alignment revision.

Synthetic words and a mocked client only. Proves the mapping and storage wiring,
not word-timing accuracy of any engine.
"""

import io
import json
import logging
import uuid
from unittest.mock import MagicMock

import pytest
from sqlalchemy import event

from src.app import app, db
from src.clinic import alignment as al
from src.clinic import alignment_ingest as ing
from src.clinic.audio_revisions import init_audio_revision
from src.clinic.correction import ClinicTextAudioLink, ingest_asr_result
from src.clinic.models import ClinicDictation, ClinicOriginal
from src.clinic.registry import create_clinic_tables
from src.clinic.revisions import current_revision
from src.models import Recording, User
from src.services.transcription.base import TranscriptionRequest
from src.services.transcription.connectors.openai_whisper import OpenAIWhisperConnector
from src.services.transcription.registry import ConnectorRegistry

MODEL = "stub-asr:1"
DURATION = 5000
LOCAL = "http://127.0.0.1:8000/v1"


@pytest.fixture
def clinic_db():
    with app.app_context():
        hook = None
        if db.engine.dialect.name == "sqlite":
            def hook(dbapi_connection, _record):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()

            event.listen(db.engine, "connect", hook)
            db.session.remove()
            db.engine.dispose()
        db.session.rollback()
        create_clinic_tables()
        yield
        db.session.rollback()
        if hook is not None:
            event.remove(db.engine, "connect", hook)
            db.session.remove()
            db.engine.dispose()


def _dictation(with_audio=True):
    suffix = uuid.uuid4().hex[:10]
    user = User(username="wt" + suffix, email=suffix + "@example.invalid", password="x")
    db.session.add(user)
    db.session.flush()
    recording = Recording(user_id=user.id, title="clinic", status="PENDING")
    db.session.add(recording)
    db.session.flush()
    dictation = ClinicDictation(recording_id=recording.id, author_user_id=user.id)
    db.session.add(dictation)
    db.session.flush()
    original = ClinicOriginal(
        dictation_id=dictation.id, content_hash="o" * 64, size_bytes=1,
        storage_key="originals/" + uuid.uuid4().hex, receipt="stored", origin_ref="device:test",
    )
    db.session.add(original)
    db.session.flush()
    audio = init_audio_revision(dictation, original, DURATION) if with_audio else None
    return dictation, audio


def _stored(text, words, start=0.0, end=4.0):
    return json.dumps([{
        "speaker": "SPEAKER_00", "sentence": text, "start_time": start, "end_time": end,
        "words": [{"word": w, "start": s, "end": e} for w, s, e in words],
    }])


def _rows(dictation):
    return db.session.query(al.ClinicAlignmentRevision).filter_by(dictation_id=dictation.id).all()


def _ingest(dictation, text, words):
    stored = _stored(text, words)
    ingest_asr_result(dictation, text, stored, MODEL)
    return current_revision(dictation), stored


# ----------------------------------------------------------------- connector


def _connector(monkeypatch, clinic, flag, response=None):
    monkeypatch.setenv("CLINIC_MODE", "1" if clinic else "0")
    if flag:
        monkeypatch.setenv("TRANSCRIPTION_WORD_TIMESTAMPS", "yes")
    else:
        monkeypatch.delenv("TRANSCRIPTION_WORD_TIMESTAMPS", raising=False)
    monkeypatch.setenv("TRANSCRIPTION_BASE_URL", LOCAL)
    monkeypatch.setenv("TRANSCRIPTION_API_KEY", "k")
    config = ConnectorRegistry()._build_config_from_env("openai_whisper")
    connector = OpenAIWhisperConnector(config)
    create = MagicMock(return_value=response or {"text": "x", "segments": []})
    connector.client.audio.transcriptions.create = create
    return connector, create


def _request():
    return TranscriptionRequest(audio_file=io.BytesIO(b"a"), filename="a.wav", mime_type="audio/wav")


def test_flag_on_clinic_on_requests_word(monkeypatch):
    connector, create = _connector(monkeypatch, True, True)
    connector.transcribe(_request())
    assert create.call_args.kwargs["timestamp_granularities"] == ["segment", "word"]


def test_flag_on_clinic_off_requests_segment_only(monkeypatch):
    connector, create = _connector(monkeypatch, False, True)
    connector.transcribe(_request())
    assert create.call_args.kwargs["timestamp_granularities"] == ["segment"]


def test_flag_off_keeps_request(monkeypatch):
    connector, create = _connector(monkeypatch, True, False)
    connector.transcribe(_request())
    assert create.call_args.kwargs["timestamp_granularities"] == ["segment"]


def test_public_url_refused_with_flag_on(monkeypatch):
    monkeypatch.setenv("CLINIC_MODE", "1")
    with pytest.raises(Exception, match="local or private"):
        OpenAIWhisperConnector({"api_key": "k", "base_url": "https://api.openai.com/v1", "word_timestamps": True})


def test_per_segment_and_top_level_words_are_stored(monkeypatch):
    response = {
        "text": "Eins zwei drei",
        "segments": [
            {"text": "Eins zwei", "start": 0.0, "end": 1.0},
            {"text": "drei", "start": 1.0, "end": 2.0,
             "words": [{"word": "drei", "start": 1.1, "end": 1.9}]},
        ],
        "words": [
            {"word": "Eins", "start": 0.0, "end": 0.4},
            {"word": "zwei", "start": 0.5, "end": 0.9},
            {"word": "drei", "start": 1.2, "end": 1.8},  # segment 2 has own words: not added again
            {"word": "draussen", "start": 2.5, "end": 3.0},  # outside every segment
            {"word": "Grenze", "start": 1.0, "end": 1.5},  # half-open: belongs to segment 2 only
        ],
    }
    connector, _ = _connector(monkeypatch, True, True, response)
    result = connector.transcribe(_request())
    first, second = result.segments
    assert [w["word"] for w in first.words] == ["Eins", "zwei"]
    assert [w["word"] for w in second.words] == ["drei"]
    stored = json.loads(result.to_storage_format())
    assert [w["word"] for w in stored[0]["words"]] == ["Eins", "zwei"]
    assert all(w["word"] != "draussen" for seg in stored for w in seg["words"])


def test_top_level_words_ignored_with_flag_off(monkeypatch):
    response = {"text": "a", "segments": [{"text": "a", "start": 0.0, "end": 1.0}],
                "words": [{"word": "a", "start": 0.1, "end": 0.2}]}
    connector, _ = _connector(monkeypatch, True, False, response)
    assert connector.transcribe(_request()).segments[0].words is None


# -------------------------------------------------------------------- ingest


def test_all_words_match_gives_succeeded_row(clinic_db):
    dictation, audio = _dictation()
    revision, _ = _ingest(dictation, "Die Unterlagen sind bereit.", [
        ("Die", 0.2, 0.43), ("Unterlagen", 0.48, 1.15), ("sind", 1.2, 1.45), ("bereit.", 1.5, 2.01)])
    (row,) = _rows(dictation)
    assert (row.state, row.source_kind) == ("succeeded", "asr")
    assert (row.audio_revision_id, row.transcript_revision_id) == (audio.id, revision.id)
    assert row.document_hash and row.audio_duration_ms == DURATION
    assert (row.engine, row.model_revision) == ("stub-asr", "1")
    times = [(i["start_ms"], i["end_ms"]) for i in row.items]
    assert times == [(200, 430), (480, 1150), (1200, 1450), (1500, 2010)]
    assert all(type(t) is int for pair in times for t in pair)


def test_split_word_gives_partial_with_unaligned_token(clinic_db):
    dictation, _ = _dictation()
    _ingest(dictation, "Gabe 12 mg bei Bedarf", [
        ("Gabe", 0.1, 0.4), ("12", 0.5, 0.7), ("mg", 0.7, 0.9), ("bei", 1.0, 1.2), ("Bedarf", 1.3, 1.6)])
    (row,) = _rows(dictation)
    assert row.state == "succeeded"  # "12" and "mg" are separate body tokens here
    dictation2, _ = _dictation()
    _ingest(dictation2, "Gabe 12mg bei Bedarf", [
        ("Gabe", 0.1, 0.4), ("12", 0.5, 0.7), ("mg", 0.7, 0.9), ("bei", 1.0, 1.2), ("Bedarf", 1.3, 1.6)])
    (row,) = _rows(dictation2)
    assert row.state == "partial"
    by_text = {i["displayed_text"]: i for i in row.items}
    assert by_text["12mg"]["state"] == "unaligned" and by_text["12mg"]["reason_code"] == "NO_VERIFIED_TIMING"
    assert by_text["12mg"]["start_ms"] is None
    assert (by_text["bei"]["start_ms"], by_text["Bedarf"]["end_ms"]) == (1000, 1600)
    assert "PARTIAL_ALIGNMENT" in row.quality["warnings"]


def test_non_matching_word_never_gives_a_time(clinic_db):
    dictation, _ = _dictation()
    _ingest(dictation, "Alpha Beta Gamma", [("Alpha", 0.1, 0.2), ("Xxx", 0.3, 0.4), ("Gamma", 0.5, 0.6)])
    (row,) = _rows(dictation)
    states = {i["displayed_text"]: i["state"] for i in row.items}
    assert states == {"Alpha": "valid", "Beta": "unaligned", "Gamma": "valid"}


def test_overlapping_or_invalid_interval_is_unaligned(clinic_db):
    dictation, _ = _dictation()
    _ingest(dictation, "Alpha Beta Gamma", [("Alpha", 0.1, 0.5), ("Beta", 0.3, 0.6), ("Gamma", 0.7, 0.7)])
    (row,) = _rows(dictation)
    assert [i["state"] for i in row.items] == ["valid", "unaligned", "unaligned"]


def test_no_words_gives_failed_row(clinic_db):
    dictation, _ = _dictation()
    text = "Nur Segmente"
    stored = json.dumps([{"speaker": "S", "sentence": text, "start_time": 0.0, "end_time": 2.0}])
    ingest_asr_result(dictation, text, stored, MODEL)
    (row,) = _rows(dictation)
    assert row.state == "failed" and row.items == []
    assert "NO_WORD_TIMINGS" in row.quality["warnings"]
    # segment times never became word times
    links = db.session.query(ClinicTextAudioLink).filter_by(dictation_id=dictation.id).all()
    assert [(link.audio_start_ms, link.audio_end_ms) for link in links] == [(0, 2000)]


def test_no_audio_revision_gives_no_row(clinic_db):
    dictation, _ = _dictation(with_audio=False)
    revision, stored = _ingest(dictation, "Ein Wort", [("Ein", 0.1, 0.2), ("Wort", 0.3, 0.4)])
    assert _rows(dictation) == []
    result = ing.store_asr_alignment(dictation, revision, stored, MODEL)
    assert (result.row, result.reason) == (None, "NO_AUDIO_REVISION")


def test_repeat_ingest_adds_no_row(clinic_db):
    dictation, _ = _dictation()
    words = [("Ein", 0.1, 0.2), ("Wort", 0.3, 0.4)]
    revision, stored = _ingest(dictation, "Ein Wort", words)
    first = _rows(dictation)
    ingest_asr_result(dictation, "Ein Wort", stored, MODEL)
    again = ing.store_asr_alignment(dictation, revision, stored, MODEL)
    assert [r.id for r in _rows(dictation)] == [r.id for r in first] and again.row.id == first[0].id


def test_alignment_failure_keeps_revision_and_links_without_text_in_log(clinic_db, monkeypatch, caplog):
    dictation, _ = _dictation()
    secret = "Geheimer Befund"

    def boom(*_a, **_k):
        raise RuntimeError("leak: " + secret)

    monkeypatch.setattr(ing, "store_asr_alignment", boom)
    stored = _stored(secret, [("Geheimer", 0.1, 0.2), ("Befund", 0.3, 0.4)])
    with caplog.at_level(logging.INFO):
        links = ingest_asr_result(dictation, secret, stored, MODEL)
    assert current_revision(dictation).body == secret
    assert len(links) == 1 and _rows(dictation) == []
    assert "not stored" in caplog.text
    assert secret not in caplog.text
