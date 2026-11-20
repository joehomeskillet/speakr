"""Canonical token documents for clinic transcript revisions (v8-81)."""

import json
import re
import uuid

import pytest
from sqlalchemy import event

from src.app import app, db
from src.clinic import alignment_tokens as at
from src.clinic.models import ClinicDictation, ClinicOriginal
from src.clinic.revisions import add_asr_revision, add_human_revision, normalise_text
from src.clinic.unicode_anchor_map import to_utf16_offset
from src.models import Recording, User

EXAMPLE = json.loads(r'''{"document_schema_version":"1.0","nodes":[{"text_node_id":"node-1","kind":"text","block_type":"paragraph","tokens":[{"token_id":"token-1","kind":"word","text":"Die","marks":[]},{"token_id":"token-2","kind":"separator","text":" ","marks":[]},{"token_id":"token-3","kind":"word","text":"Unterlagen","marks":[]},{"token_id":"token-4","kind":"separator","text":" ","marks":[]},{"token_id":"token-5","kind":"word","text":"sind","marks":[]},{"token_id":"token-6","kind":"separator","text":" ","marks":[]},{"token_id":"token-7","kind":"word","text":"bereit","marks":[]},{"token_id":"token-8","kind":"separator","text":".","marks":[]}]}]}''')
EXAMPLE_HASH = "00500e8209beafa824c46e90ecb2e30a1b9c5fdccc85ec73f5ddf146a8f640f5"


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
        at.create_tables()
        yield
        db.session.rollback()
        if hook is not None:
            event.remove(db.engine, "connect", hook)
            db.session.remove()
            db.engine.dispose()


def _scene():
    suffix = uuid.uuid4().hex[:10]
    user = User(username="tk" + suffix, email=suffix + "@example.invalid", password="x")
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
    return user, dictation, original


def _ids(document):
    return [t["token_id"] for n in document["nodes"] for t in n["tokens"]]


def _by_text(document):
    """text -> list of token ids, in document order (words only)."""
    out = {}
    for n in document["nodes"]:
        for t in n["tokens"]:
            if t["kind"] == "word":
                out.setdefault(t["text"], []).append(t["token_id"])
    return out


def _doc(body, rid="rev-a"):
    return at.tokenize(body, rid)


ROUND_TRIP = [
    "Der Patient ist stabil.",
    "a  b   c",
    "  lead und trail  ",
    "eins\n\n\nzwei",
    "zeile\n",
    "a\tb\t\tc",
    "\U0001F600 x \U0001F468\u200d\U0001F469\u200d\U0001F467 y",
    "Cafe\u0301",
    "",
    "\n",
]


@pytest.mark.parametrize("body", ROUND_TRIP)
def test_projection_round_trip_and_contract_shape(body):
    body = normalise_text(body)
    document = _doc(body)
    assert at.project(document) == body
    assert set(document) == {"document_schema_version", "nodes"}
    assert document["document_schema_version"] == "1.0"
    assert len(document["nodes"]) == body.count("\n") + 1
    ids = _ids(document) + [n["text_node_id"] for n in document["nodes"]]
    assert len(ids) == len(set(ids))
    for node in document["nodes"]:
        assert set(node) == {"text_node_id", "kind", "block_type", "tokens"}
        assert node["kind"] == "text" and node["block_type"] == "paragraph"
        assert re.fullmatch(r"\S+", node["text_node_id"])
        for token in node["tokens"]:
            assert set(token) == {"token_id", "kind", "text", "marks"}
            assert token["marks"] == []
            assert re.fullmatch(r"\S+", token["token_id"]) and len(token["token_id"]) <= 160
            if token["kind"] == "word":
                assert re.fullmatch(r"\S+", token["text"])
            else:
                assert token["kind"] == "separator" and token["text"].strip() == ""


def test_empty_line_is_a_node_without_tokens():
    document = _doc("a\n\nb")
    assert [len(n["tokens"]) for n in document["nodes"]] == [1, 0, 1]


def test_tokenize_rejects_unnormalised_body():
    with pytest.raises(at.AlignmentTokensError):
        _doc("a\r\nb")


def test_document_hash_matches_contract_reference():
    assert at.document_hash(EXAMPLE) == EXAMPLE_HASH
    assert at.project(EXAMPLE) == "Die Unterlagen sind bereit."


def _carried(old_body, new_body):
    parent = _doc(old_body, "rev-a")
    return parent, at.carry(parent, new_body, "rev-b")


def test_insert_keeps_every_other_id():
    parent, child = _carried("Die Unterlagen sind bereit.", "Die neuen Unterlagen sind bereit.")
    old, new = set(_ids(parent)), _ids(child)
    assert old <= set(new)
    assert len(set(new) - old) == 2  # new word and its separator


def test_delete_keeps_the_others():
    parent, child = _carried("Die neuen Unterlagen sind bereit.", "Die Unterlagen sind bereit.")
    assert set(_ids(child)) <= set(_ids(parent))
    assert at.project(child) == "Die Unterlagen sind bereit."
    assert _by_text(child)["sind"] == _by_text(parent)["sind"]


def test_replace_changes_only_the_replaced_token():
    parent, child = _carried("Die Unterlagen sind bereit.", "Die Unterlagen waren bereit.")
    old, new = _ids(parent), _ids(child)
    assert [a == b for a, b in zip(old, new)] == [True, True, True, True, False, True, True]
    assert len(set(new)) == len(new)


def test_identical_words_keep_their_own_ids():
    parent, child = _carried("ja nein ja", "zuerst ja nein ja")
    ja_old, ja_new = _by_text(parent)["ja"], _by_text(child)["ja"]
    assert ja_old[0] != ja_old[1]
    assert ja_new == ja_old


def test_moved_word_gets_a_new_id():
    parent, child = _carried("alpha beta gamma", "beta gamma alpha")
    assert _by_text(child)["beta"] == _by_text(parent)["beta"]
    assert _by_text(child)["alpha"] != _by_text(parent)["alpha"]


def test_split_line_keeps_ids_on_both_sides():
    parent, child = _carried("eins zwei drei vier", "eins zwei\ndrei vier")
    old, new = _by_text(parent), _by_text(child)
    for word in ("eins", "zwei", "drei", "vier"):
        assert new[word] == old[word]
    assert child["nodes"][0]["text_node_id"] == parent["nodes"][0]["text_node_id"]
    assert child["nodes"][1]["text_node_id"] != child["nodes"][0]["text_node_id"]
    ids = _ids(child) + [n["text_node_id"] for n in child["nodes"]]
    assert len(ids) == len(set(ids))


def test_carry_is_deterministic():
    parent = _doc("a b c")
    assert at.carry(parent, "a x c d", "rev-b") == at.carry(parent, "a x c d", "rev-b")
    assert at.tokenize("a b", "rev-z") == at.tokenize("a b", "rev-z")


def test_node_keeps_parent_id_across_edit():
    parent = _doc("eins\nzwei")
    child = at.carry(parent, "neu eins\nzwei", "rev-b")
    assert [n["text_node_id"] for n in child["nodes"]] == [n["text_node_id"] for n in parent["nodes"]]


def test_utf16_spans_agree_with_anchor_map():
    body = normalise_text("\U0001F600 ab Cafe\u0301 \U0001F468\u200d\U0001F469 z\nzwei")
    document = _doc(body)
    spans = at.utf16_spans(document)
    cursor = 0
    for node_no, node in enumerate(document["nodes"]):
        if node_no:
            cursor += 1
        for token in node["tokens"]:
            start = to_utf16_offset(body, cursor)
            cursor += len(token["text"])
            assert spans[token["token_id"]] == (start, to_utf16_offset(body, cursor))


def test_tokens_for_is_idempotent_and_carries(clinic_db, monkeypatch):
    def no_commit(*_a, **_k):
        raise AssertionError("tokens_for must not commit")

    monkeypatch.setattr(db.session, "commit", no_commit)
    user, dictation, original = _scene()
    asr = add_asr_revision(dictation, original, "Die Unterlagen sind bereit.", "stub-asr:1")
    first = at.tokens_for(dictation, asr)
    assert at.tokens_for(dictation, asr) is first
    assert first.tokenization_version == at.TOKENIZATION_VERSION
    assert first.document_hash == at.document_hash(first.document)
    assert first.dictation_id == dictation.id
    human = add_human_revision(dictation, asr, "Die neuen Unterlagen sind bereit.", user.id)
    second = at.tokens_for(dictation, human)
    assert set(_ids(first.document)) <= set(_ids(second.document))
    assert at.project(second.document) == human.body


def test_row_is_write_once(clinic_db):
    user, dictation, original = _scene()
    asr = add_asr_revision(dictation, original, "alpha", "stub-asr:1")
    row = at.tokens_for(dictation, asr)
    row.document_hash = "x" * 64
    with pytest.raises(at.AlignmentTokensImmutable):
        db.session.flush()
    db.session.rollback()


def test_row_cannot_be_deleted(clinic_db):
    user, dictation, original = _scene()
    asr = add_asr_revision(dictation, original, "alpha", "stub-asr:1")
    db.session.delete(at.tokens_for(dictation, asr))
    with pytest.raises(at.AlignmentTokensImmutable):
        db.session.flush()
    db.session.rollback()


def test_revision_of_another_dictation_raises(clinic_db):
    _user, dictation, original = _scene()
    _other_user, other, other_original = _scene()
    foreign = add_asr_revision(other, other_original, "fremd", "stub-asr:1")
    with pytest.raises(at.AlignmentTokensError):
        at.tokens_for(dictation, foreign)
    assert original is not None
