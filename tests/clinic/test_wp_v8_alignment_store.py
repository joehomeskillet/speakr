"""Persistent alignment revisions and their state machine (v8-82)."""

import copy
import json
import uuid

import pytest
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError

from src.app import app, db
from src.clinic import alignment as al
from src.clinic import alignment_tokens as at
from src.clinic.models import ClinicAudioRevision, ClinicDictation, ClinicOriginal
from src.clinic.registry import create_clinic_tables
from src.clinic.revisions import add_asr_revision
from src.models import Recording, User

SCHEMA_PATH = "/root/work/wp-backlog/ui-v8/contracts/editor-alignment.schema.json"
DOC = json.loads(r'''{"document_schema_version":"1.0","nodes":[{"text_node_id":"node-1","kind":"text","block_type":"paragraph","tokens":[{"token_id":"token-1","kind":"word","text":"Die","marks":[]},{"token_id":"token-2","kind":"separator","text":" ","marks":[]},{"token_id":"token-3","kind":"word","text":"Unterlagen","marks":[]},{"token_id":"token-4","kind":"separator","text":" ","marks":[]},{"token_id":"token-5","kind":"word","text":"sind","marks":[]},{"token_id":"token-6","kind":"separator","text":" ","marks":[]},{"token_id":"token-7","kind":"word","text":"bereit","marks":[]},{"token_id":"token-8","kind":"separator","text":".","marks":[]}]}]}''')
EX_SUCCEEDED = json.loads(r'''{"schema_version":"2.0","recording_id":"4524","audio_revision_id":"AR-DEMO-3","transcript_revision_id":"TR-DEMO-5","alignment_revision_id":"ALIGN-DEMO-5","document_hash":"00500e8209beafa824c46e90ecb2e30a1b9c5fdccc85ec73f5ddf146a8f640f5","time_unit":"ms","audio_duration_ms":2500,"state":"succeeded","source":{"kind":"demo","engine":"synthetic-fixture","model_revision":"fixture-1","tokenization_version":"canonical-token-1"},"created_at":"2026-11-19T17:00:01Z","items":[{"word_id":"token-1","text_node_id":"node-1","token_index":0,"displayed_text":"Die","state":"valid","start_ms":200,"end_ms":430,"reason_code":null},{"word_id":"token-3","text_node_id":"node-1","token_index":2,"displayed_text":"Unterlagen","state":"valid","start_ms":480,"end_ms":1150,"reason_code":null},{"word_id":"token-5","text_node_id":"node-1","token_index":4,"displayed_text":"sind","state":"valid","start_ms":1200,"end_ms":1450,"reason_code":null},{"word_id":"token-7","text_node_id":"node-1","token_index":6,"displayed_text":"bereit","state":"valid","start_ms":1500,"end_ms":2010,"reason_code":null}],"quality":{"valid_words":4,"unaligned_words":0,"stale_words":0,"coverage":1,"warnings":[]}}''')
EX_PARTIAL = json.loads(r'''{"schema_version":"2.0","recording_id":"4524","audio_revision_id":"AR-DEMO-3","transcript_revision_id":"TR-DEMO-5","alignment_revision_id":"ALIGN-DEMO-5-PARTIAL","document_hash":"00500e8209beafa824c46e90ecb2e30a1b9c5fdccc85ec73f5ddf146a8f640f5","time_unit":"ms","audio_duration_ms":2500,"state":"partial","source":{"kind":"demo","engine":"synthetic-fixture","model_revision":"fixture-1","tokenization_version":"canonical-token-1"},"created_at":"2026-11-19T17:00:01Z","items":[{"word_id":"token-1","text_node_id":"node-1","token_index":0,"displayed_text":"Die","state":"valid","start_ms":200,"end_ms":430,"reason_code":null},{"word_id":"token-3","text_node_id":"node-1","token_index":2,"displayed_text":"Unterlagen","state":"valid","start_ms":480,"end_ms":1150,"reason_code":null},{"word_id":"token-5","text_node_id":"node-1","token_index":4,"displayed_text":"sind","state":"valid","start_ms":1200,"end_ms":1450,"reason_code":null},{"word_id":"token-7","text_node_id":"node-1","token_index":6,"displayed_text":"bereit","state":"unaligned","start_ms":null,"end_ms":null,"reason_code":"NO_VERIFIED_TIMING"}],"quality":{"valid_words":3,"unaligned_words":1,"stale_words":0,"coverage":0.75,"warnings":["PARTIAL_ALIGNMENT"]}}''')
EX_PENDING = json.loads(r'''{"schema_version":"2.0","recording_id":"4524","audio_revision_id":"AR-DEMO-3","transcript_revision_id":"TR-DEMO-5","alignment_revision_id":"ALIGN-DEMO-5-PENDING","document_hash":"00500e8209beafa824c46e90ecb2e30a1b9c5fdccc85ec73f5ddf146a8f640f5","time_unit":"ms","audio_duration_ms":2500,"state":"pending","source":{"kind":"demo","engine":"synthetic-fixture","model_revision":"fixture-1","tokenization_version":"canonical-token-1"},"created_at":"2026-11-19T17:00:01Z","items":[],"quality":{"valid_words":0,"unaligned_words":0,"stale_words":0,"coverage":0,"warnings":[]}}''')
DURATION = 2500


@pytest.fixture
def clinic_db(monkeypatch):
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

        def no_commit(*_a, **_k):
            raise AssertionError("alignment helpers must not commit")

        monkeypatch.setattr(db.session, "commit", no_commit)
        yield
        db.session.rollback()
        if hook is not None:
            event.remove(db.engine, "connect", hook)
            db.session.remove()
            db.engine.dispose()


def _scene(body="Die Unterlagen sind bereit."):
    suffix = uuid.uuid4().hex[:10]
    user = User(username="al" + suffix, email=suffix + "@example.invalid", password="x")
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
    audio = ClinicAudioRevision(dictation_id=dictation.id, content_hash="a" * 64)
    db.session.add(audio)
    db.session.flush()
    transcript = add_asr_revision(dictation, original, body, "stub-asr:1")
    return dictation, audio, transcript


def _pending(dictation, audio, transcript, **kw):
    args = dict(source_kind="asr", engine="stub-engine", model_revision="m1", audio_duration_ms=DURATION)
    args.update(kw)
    return al.create_pending(dictation, audio, transcript, **args)


def _word_items(row):
    """Valid items for every word of the row's document, 100 ms each."""
    document = at.tokens_for(db.session.get(ClinicDictation, row.dictation_id),
                             db.session.get(al.ClinicTranscriptRevision, row.transcript_revision_id)).document
    items = []
    for node in document["nodes"]:
        for index, token in enumerate(node["tokens"]):
            if token["kind"] == "word":
                n = len(items)
                items.append({
                    "word_id": token["token_id"], "text_node_id": node["text_node_id"],
                    "token_index": index, "displayed_text": token["text"], "state": "valid",
                    "start_ms": n * 200, "end_ms": n * 200 + 100, "reason_code": None,
                })
    return items


def _seeded_row():
    """Row whose token document is the contract example document."""
    dictation, audio, transcript = _scene("Die Unterlagen sind bereit.")
    # The deterministic tokenizer yields different ids, so store the example document instead.
    db.session.add(at.ClinicTranscriptTokens(
        transcript_revision_id=transcript.id, dictation_id=dictation.id,
        tokenization_version=at.TOKENIZATION_VERSION, document_hash=at.document_hash(DOC),
        document=copy.deepcopy(DOC),
    ))
    db.session.flush()
    return dictation, audio, transcript, _pending(dictation, audio, transcript)


def _fresh_items():
    return copy.deepcopy(EX_SUCCEEDED["items"])


def test_create_clinic_tables_creates_both_tables(clinic_db):
    names = set(db.inspect(db.engine).get_table_names())
    assert {"clinic_alignment_revision", "clinic_transcript_tokens"} <= names
    assert "alignment" in __import__("src.clinic.registry", fromlist=["x"]).MODEL_MODULES


def test_contract_examples_validate_and_classify(clinic_db):
    for example, state in ((EX_SUCCEEDED, "succeeded"), (EX_PARTIAL, "partial")):
        _d, _a, _t, row = _seeded_row()
        al.complete(row, copy.deepcopy(example["items"]))
        assert row.state == state
        assert row.operation_state == "succeeded"
        assert row.quality == example["quality"]
        contract = al.to_contract(row)
        assert set(contract) == set(example)
        assert contract["items"] == example["items"]
        assert contract["quality"] == example["quality"]
        assert contract["schema_version"] == "2.0" and contract["time_unit"] == "ms"
        assert contract["recording_id"] == str(db.session.get(ClinicDictation, row.dictation_id).recording_id)
        assert contract["alignment_revision_id"] == row.id
        assert contract["created_at"].endswith("Z")
        assert contract["source"] == {
            "kind": "asr", "engine": "stub-engine", "model_revision": "m1",
            "tokenization_version": at.TOKENIZATION_VERSION,
        }


def test_pending_matches_example_shape(clinic_db):
    _d, _a, _t, row = _seeded_row()
    contract = al.to_contract(row)
    assert set(contract) == set(EX_PENDING)
    assert contract["items"] == [] and contract["state"] == "pending"
    assert contract["quality"] == EX_PENDING["quality"]
    assert contract["document_hash"] == EX_PENDING["document_hash"]


def _mut(fn):
    items = _fresh_items()
    fn(items)
    return items


INVALID = {
    "duplicate word_id": lambda i: i[1].update(word_id=i[0]["word_id"]),
    "unknown word_id": lambda i: i[1].update(word_id="token-99"),
    "separator referenced": lambda i: i[1].update(word_id="token-2"),
    "wrong token_index": lambda i: i[1].update(token_index=3),
    "wrong displayed_text": lambda i: i[1].update(displayed_text="Unterlage"),
    "wrong text_node_id": lambda i: i[1].update(text_node_id="node-2"),
    "missing word": lambda i: i.pop(),
    "out of order": lambda i: i.insert(0, i.pop(1)),
    "overlapping times": lambda i: i[1].update(start_ms=400),
    "end beyond duration": lambda i: i[3].update(end_ms=DURATION + 1),
    "start equals end": lambda i: i[0].update(end_ms=200),
    "float ms": lambda i: i[0].update(start_ms=200.0),
    "bool ms": lambda i: i[0].update(start_ms=True, end_ms=430),
    "valid with null time": lambda i: i[0].update(start_ms=None),
    "unaligned with times": lambda i: i[3].update(state="unaligned", reason_code="X"),
    "bad reason code": lambda i: i[3].update(state="unaligned", start_ms=None, end_ms=None, reason_code="bad code"),
    "valid with reason": lambda i: i[0].update(reason_code="OK"),
    "extra key": lambda i: i[0].update(extra=1),
    "negative start": lambda i: i[0].update(start_ms=-1),
}


@pytest.mark.parametrize("name", sorted(INVALID))
def test_invalid_payload_makes_whole_payload_failed(clinic_db, name):
    _d, _a, _t, row = _seeded_row()
    items = _mut(INVALID[name])
    with pytest.raises(al.AlignmentInvalid):
        al.validate_items(DOC, items, DURATION)
    al.complete(row, items)
    assert row.state == "failed" and row.items == [] and row.operation_state == "failed"
    assert "INVALID_PAYLOAD" in row.quality["warnings"]
    assert row.quality["valid_words"] == 0 and row.quality["coverage"] == 0


def test_no_valid_word_fails_with_warning(clinic_db):
    _d, _a, _t, row = _seeded_row()
    items = _fresh_items()
    for item in items:
        item.update(state="unaligned", start_ms=None, end_ms=None, reason_code="NO_VERIFIED_TIMING")
    al.complete(row, items, warnings=["Z_ENGINE", "Z_ENGINE"])
    assert row.state == "failed" and row.items == []
    assert row.quality["warnings"] == ["NO_VALID_WORDS", "Z_ENGINE"]


def test_non_list_payload_is_invalid(clinic_db):
    _d, _a, _t, row = _seeded_row()
    al.complete(row, None)
    assert row.state == "failed" and "INVALID_PAYLOAD" in row.quality["warnings"]


def test_complete_twice_raises(clinic_db):
    _d, _a, _t, row = _seeded_row()
    al.complete(row, _fresh_items())
    with pytest.raises(al.AlignmentStateError):
        al.complete(row, _fresh_items())


def test_terminal_row_rejects_other_updates(clinic_db):
    _d, _a, _t, row = _seeded_row()
    al.complete(row, _fresh_items())
    row.items = []
    with pytest.raises(al.AlignmentImmutable):
        db.session.flush()
    db.session.rollback()


def test_terminal_row_binding_is_write_once(clinic_db):
    _d, _a, _t, row = _seeded_row()
    row.document_hash = "f" * 64
    with pytest.raises(al.AlignmentImmutable):
        db.session.flush()
    db.session.rollback()


def test_terminal_row_cannot_return_to_pending(clinic_db):
    _d, _a, _t, row = _seeded_row()
    al.complete(row, _fresh_items())
    row.state = "pending"
    with pytest.raises(al.AlignmentImmutable):
        db.session.flush()
    db.session.rollback()


def test_terminal_to_stale_and_pending_to_stale(clinic_db):
    _d, _a, _t, done = _seeded_row()
    al.complete(done, _fresh_items())
    items = list(done.items)
    al.mark_stale(done)
    assert done.state == "stale" and done.operation_state == "superseded" and done.items == items
    assert al.to_operation(done)["result_alignment_revision_id"] == done.id
    with pytest.raises(al.AlignmentStateError):
        al.mark_stale(done)
    _d2, _a2, _t2, waiting = _seeded_row()
    al.mark_stale(waiting)
    assert waiting.state == "stale"
    assert al.to_operation(waiting)["result_alignment_revision_id"] == waiting.id
    with pytest.raises(al.AlignmentStateError):
        al.complete(waiting, _fresh_items())
    waiting.items = _fresh_items()
    with pytest.raises(al.AlignmentImmutable):
        db.session.flush()
    db.session.rollback()


def test_same_operation_key_returns_same_row(clinic_db):
    dictation, audio, transcript = _scene()
    first = _pending(dictation, audio, transcript)
    assert _pending(dictation, audio, transcript) is first
    assert _pending(dictation, audio, transcript, engine="other-engine") is not first
    assert first.items == [] and first.state == "pending"
    assert first.quality == {"valid_words": 0, "unaligned_words": 0, "stale_words": 0, "coverage": 0, "warnings": []}


def test_audio_revision_of_another_dictation_is_rejected_by_fk(clinic_db):
    dictation, audio, transcript = _scene()
    _other, foreign_audio, _t = _scene()
    row = _pending(dictation, audio, transcript)
    with pytest.raises(al.AlignmentError):
        _pending(dictation, foreign_audio, transcript)
    db.session.add(al.ClinicAlignmentRevision(
        dictation_id=dictation.id, audio_revision_id=foreign_audio.id,
        transcript_revision_id=transcript.id, document_hash=row.document_hash,
        tokenization_version=row.tokenization_version, state="pending", source_kind="asr",
        engine="e", model_revision="m", audio_duration_ms=1, items=[], quality={},
        operation_key="k" * 64, operation_state="pending",
    ))
    with pytest.raises(IntegrityError):
        db.session.flush()
    db.session.rollback()


def test_demo_source_is_rejected(clinic_db):
    dictation, audio, transcript = _scene()
    with pytest.raises(al.AlignmentError):
        _pending(dictation, audio, transcript, source_kind="demo")


def test_enums_and_keys_come_from_the_schema(clinic_db):
    schema = json.load(open(SCHEMA_PATH))["$defs"]
    assert set(al.STATES) == set(schema["AlignmentRevision"]["properties"]["state"]["enum"])
    assert set(al.SOURCE_KINDS) == set(schema["AlignmentSource"]["properties"]["kind"]["enum"]) - {"demo"}
    assert set(al.OPERATION_STATES) == set(schema["JobOperation"]["properties"]["state"]["enum"])
    assert set(al.ITEM_STATES) == set(schema["AlignmentItem"]["properties"]["state"]["enum"])
    assert al.CONTRACT_KEYS == set(schema["AlignmentRevision"]["required"])
    assert al.OPERATION_KEYS == set(schema["JobOperation"]["required"])
    assert al.ITEM_KEYS == set(schema["AlignmentItem"]["required"])
    _d, _a, _t, row = _seeded_row()
    assert set(al.to_contract(row)) == set(schema["AlignmentRevision"]["required"])
    operation = al.to_operation(row)
    assert set(operation) == set(schema["JobOperation"]["required"])
    assert operation["operation_type"] == "alignment" and operation["state"] == "pending"
    assert operation["operation_id"] == row.id and operation["request_fingerprint"] == row.operation_key
    assert operation["result_alignment_revision_id"] is None and operation["result_transcript_revision_id"] is None
    assert operation["job_id"] is None
    row.job_id = "job-1"
    db.session.flush()
    assert al.to_operation(row)["job_id"] == "job-1"


def test_read_for_exact_stale_and_missing(clinic_db):
    dictation, audio, transcript = _scene()
    assert al.read_for(dictation.id, audio.id, transcript.id) is None
    older = _pending(dictation, audio, transcript)
    al.complete(older, _word_items(older))
    assert al.get_exact(dictation.id, audio.id, transcript.id) is older
    assert al.read_for(dictation.id, audio.id, transcript.id) == (older, "succeeded")
    audio2 = ClinicAudioRevision(dictation_id=dictation.id, content_hash="b" * 64, parent_revision_id=audio.id)
    db.session.add(audio2)
    db.session.flush()
    assert al.get_exact(dictation.id, audio2.id, transcript.id) is None
    row, state = al.read_for(dictation.id, audio2.id, transcript.id)
    assert row is older and state == "stale"
    assert al.to_contract(row, state)["state"] == "stale" and older.state == "succeeded"
    pending = _pending(dictation, audio2, transcript)
    assert al.read_for(dictation.id, audio2.id, transcript.id) == (pending, "pending")
