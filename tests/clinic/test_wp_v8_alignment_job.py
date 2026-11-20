"""The clinic_alignment job: request, dedupe, coalescing and carry-over (v8-84)."""

import uuid

import pytest

from src.app import app, db
from src.clinic import alignment as al
from src.clinic import alignment_job as aj
from src.clinic import alignment_tokens as at
from src.clinic.audio_revisions import apply_cut, current_audio_revision, init_audio_revision
from src.clinic.correction import ClinicTextAudioLink
from src.clinic.models import (
    ClinicAudioRevision, ClinicDictation, ClinicOriginal, ClinicTranscriptRevision,
)
from src.clinic.registry import create_clinic_tables
from src.clinic.revisions import add_asr_revision, add_human_revision, current_revision
from src.models import ProcessingJob, Recording, User
from src.services import job_queue as jq

BODY = "Die Unterlagen sind bereit."
DURATION = 3000


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("CLINIC_MODE", "1")
    monkeypatch.setattr(jq, "_CLINIC_HANDLERS", {})
    with app.app_context():
        create_clinic_tables()
        yield
        db.session.rollback()


def _scene(body=BODY):
    suffix = uuid.uuid4().hex[:10]
    user = User(username="aj" + suffix, email=suffix + "@example.invalid", password="x")
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
    audio = init_audio_revision(dictation, original, DURATION)
    rev1 = add_asr_revision(dictation, original, body, "stub-asr:1")
    return user, dictation, audio, rev1


def _seed(dictation, audio, revision, duration=DURATION):
    """A completed ASR alignment: word n spans n*200 .. n*200+100 ms."""
    row = al.create_pending(dictation, audio, revision, source_kind="asr", engine="stub-engine",
                            model_revision="m1", audio_duration_ms=duration)
    document = at.tokens_for(dictation, revision).document
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
    al.complete(row, items)
    db.session.commit()
    return row


def _request(user, dictation, queue=None, **override):
    head = current_revision(dictation)
    args = dict(
        audio_revision_id=current_audio_revision(dictation).id,
        transcript_revision_id=head.id,
        document_hash=at.tokens_for(dictation, head).document_hash,
        queue=queue or jq.job_queue,
    )
    args.update(override)
    return aj.request_alignment(dictation, user.id, **args)


def _params(row):
    return {"dictation_id": row.dictation_id, "alignment_revision_id": row.id}


def _run(row):
    aj.handle(_params(row), "test")
    db.session.expire_all()
    return db.session.get(al.ClinicAlignmentRevision, row.id)


def _by_text(row):
    return {i["displayed_text"]: i for i in row.items}


def _counts(dictation_id):
    return (
        db.session.query(ClinicTranscriptRevision).filter_by(dictation_id=dictation_id).count(),
        db.session.query(ClinicAudioRevision).filter_by(dictation_id=dictation_id).count(),
        db.session.query(ClinicTextAudioLink).filter_by(dictation_id=dictation_id).count(),
    )


def test_job_type_is_registered(env):
    assert "clinic_alignment" in jq.CLINIC_JOBS
    assert jq.JOB_RUNNERS["clinic_alignment"] == "_run_clinic_job"
    assert jq.queue_name_for("clinic_alignment") == "summary"
    jq.register_clinic_handler("clinic_alignment", aj.handle)
    assert jq._CLINIC_HANDLERS["clinic_alignment"] is aj.handle
    assert aj.ALIGNER_VERSION == "carry-1"


def test_old_basis_raises_conflict_and_creates_nothing(env):
    user, dictation, audio, rev1 = _scene()
    old_hash = at.tokens_for(dictation, rev1).document_hash
    old_audio = audio.id
    add_human_revision(dictation, rev1, "Die Papiere sind bereit.", user.id)
    apply_cut(dictation, 2000, 3000)
    db.session.commit()
    rows_before = db.session.query(al.ClinicAlignmentRevision).count()
    cases = (
        dict(transcript_revision_id=rev1.id),
        dict(audio_revision_id=old_audio),
        dict(document_hash=old_hash),
        dict(document_hash="0" * 64),
    )
    for override in cases:
        with pytest.raises(aj.AlignmentBaseConflict):
            _request(user, dictation, **override)
    assert db.session.query(al.ClinicAlignmentRevision).count() == rows_before
    assert db.session.query(ProcessingJob).filter_by(job_type="clinic_alignment").count() == 0


def test_same_basis_twice_gives_one_row_and_one_job(env):
    user, dictation, audio, rev1 = _scene()
    first = _request(user, dictation)
    second = _request(user, dictation)
    assert first.id == second.id
    assert first.operation_key == aj._operation_key(
        dictation.recording_id, audio.id, rev1.id, first.document_hash, at.TOKENIZATION_VERSION)
    jobs = db.session.query(ProcessingJob).filter_by(
        recording_id=dictation.recording_id, job_type="clinic_alignment").all()
    assert len(jobs) == 1
    assert first.job_id == str(jobs[0].id)
    assert db.session.query(al.ClinicAlignmentRevision).filter_by(dictation_id=dictation.id).count() == 1


def test_burst_computes_newest_and_supersedes_older(env):
    user, dictation, audio, rev1 = _scene()
    _seed(dictation, audio, rev1)
    rev2 = add_human_revision(dictation, rev1, "Die Papiere sind bereit.", user.id)
    a = _request(user, dictation)
    rev3 = add_human_revision(dictation, rev2, "Die Papiere sind fertig.", user.id)
    b = _request(user, dictation)  # job of A is active: B waits without error
    assert b.id != a.id and b.state == "pending" and b.job_id is None
    assert db.session.query(ProcessingJob).filter_by(job_type="clinic_alignment").count() == 1
    _run(a)
    a = db.session.get(al.ClinicAlignmentRevision, a.id)
    b = db.session.get(al.ClinicAlignmentRevision, b.id)
    assert (a.state, a.operation_state) == ("stale", "superseded")
    assert b.transcript_revision_id == rev3.id
    assert b.state == "partial" and b.operation_state == "succeeded"
    words = _by_text(b)
    assert words["fertig."]["state"] == "unaligned" and words["Papiere"]["state"] == "unaligned"
    assert words["Die"]["state"] == "valid"


def test_edited_word_is_unaligned_and_others_keep_times(env):
    user, dictation, audio, rev1 = _scene()
    seed = _seed(dictation, audio, rev1)
    add_human_revision(dictation, rev1, "Die Papiere sind bereit.", user.id)
    row = _run(_request(user, dictation))
    assert row.state == "partial" and row.operation_state == "succeeded"
    words = _by_text(row)
    assert words["Papiere"]["state"] == "unaligned"
    assert words["Papiere"]["reason_code"] == "NO_VERIFIED_TIMING"
    old = _by_text(seed)
    for text in ("Die", "sind", "bereit."):
        assert words[text]["state"] == "valid"
        assert (words[text]["start_ms"], words[text]["end_ms"]) == (old[text]["start_ms"], old[text]["end_ms"])
    assert row.quality["warnings"] == ["CARRY_OVER", "PARTIAL_ALIGNMENT"]
    assert (row.source_kind, row.engine, row.model_revision) == ("asr", "stub-engine", "m1")


def test_typed_insertion_leaves_only_inserted_words_unaligned(env):
    user, dictation, audio, rev1 = _scene()
    _seed(dictation, audio, rev1)
    add_human_revision(dictation, rev1, "Die neuen Unterlagen sind bereit.", user.id)
    row = _run(_request(user, dictation))
    states = {i["displayed_text"]: i["state"] for i in row.items}
    assert states == {"Die": "valid", "neuen": "unaligned", "Unterlagen": "valid",
                      "sind": "valid", "bereit.": "valid"}


def test_audio_cut_marks_later_words_stale(env):
    user, dictation, audio, rev1 = _scene()
    _seed(dictation, audio, rev1)
    apply_cut(dictation, 550, 3000)  # words 0..2 end by 500 ms, word 3 starts at 600 ms
    db.session.commit()
    row = _run(_request(user, dictation))
    assert row.audio_revision_id != audio.id and row.audio_duration_ms == 550
    states = [(i["state"], i["reason_code"]) for i in row.items]
    assert states == [("valid", None)] * 3 + [("stale", "AUDIO_CHANGED")]
    assert row.state == "partial"


def test_w10_link_changes_nothing(env):
    user, dictation, audio, rev1 = _scene()
    _seed(dictation, audio, rev1)
    rev2 = add_human_revision(dictation, rev1, "Die Papiere sind bereit.", user.id)
    baseline = _run(_request(user, dictation)).items
    other_user, other, other_audio, other_rev1 = _scene()
    _seed(other, other_audio, other_rev1)
    other_rev2 = add_human_revision(other, other_rev1, "Die Papiere sind bereit.", other_user.id)
    db.session.add(ClinicTextAudioLink(
        dictation_id=other.id, transcript_revision_id=other_rev2.id, seq=1, origin="AUTO",
        text_start=4, text_end=11, audio_start_ms=100, audio_end_ms=1900))
    db.session.commit()
    with_link = _run(_request(other_user, other)).items
    strip = lambda items: [(i["displayed_text"], i["state"], i["start_ms"], i["end_ms"]) for i in items]  # noqa: E731
    assert strip(with_link) == strip(baseline)
    assert rev2.id != other_rev2.id


def test_heads_moving_during_compute_store_a_superseded_result(env, monkeypatch):
    user, dictation, audio, rev1 = _scene()
    _seed(dictation, audio, rev1)
    rev2 = add_human_revision(dictation, rev1, "Die Papiere sind bereit.", user.id)
    row = _request(user, dictation)
    original = aj._carry_items

    def moving(*args):
        items = original(*args)
        add_human_revision(dictation, rev2, "Die Papiere sind fertig.", user.id)
        return items

    monkeypatch.setattr(aj, "_carry_items", moving)
    done = _run(row)
    assert done.state == "partial" and done.operation_state == "superseded"
    exact = al.get_exact(dictation.id, audio.id, rev2.id)
    assert exact.id == row.id and exact.operation_state == "superseded"
    head = current_revision(dictation)
    assert head.id != rev2.id
    assert al.get_exact(dictation.id, audio.id, head.id) is None


def test_job_leaves_transcripts_audio_and_links_unchanged(env):
    user, dictation, audio, rev1 = _scene()
    _seed(dictation, audio, rev1)
    rev2 = add_human_revision(dictation, rev1, "Die Papiere sind bereit.", user.id)
    db.session.add(ClinicTextAudioLink(
        dictation_id=dictation.id, transcript_revision_id=rev2.id, seq=1, origin="AUTO",
        text_start=0, text_end=3, audio_start_ms=0, audio_end_ms=100))
    db.session.commit()
    before = _counts(dictation.id)
    bodies = sorted(r.body for r in db.session.query(ClinicTranscriptRevision).filter_by(dictation_id=dictation.id))
    link = db.session.query(ClinicTextAudioLink).filter_by(dictation_id=dictation.id).one()
    link_state = (link.id, link.text_start, link.text_end, link.audio_start_ms, link.audio_end_ms)
    _run(_request(user, dictation))
    assert _counts(dictation.id) == before
    assert sorted(r.body for r in db.session.query(ClinicTranscriptRevision).filter_by(dictation_id=dictation.id)) == bodies
    link = db.session.query(ClinicTextAudioLink).filter_by(dictation_id=dictation.id).one()
    assert (link.id, link.text_start, link.text_end, link.audio_start_ms, link.audio_end_ms) == link_state


def test_no_source_gives_a_failed_result(env):
    user, dictation, audio, rev1 = _scene()
    row = _run(_request(user, dictation))
    assert row.state == "failed" and row.operation_state == "failed"
    assert "NO_VALID_WORDS" in row.quality["warnings"]


def test_enqueue_failure_keeps_row_pending_and_retry_enqueues(env):
    user, dictation, audio, rev1 = _scene()

    class Broken:
        def enqueue(self, *args, **kwargs):
            raise RuntimeError("queue down")

    with pytest.raises(aj.AlignmentUnavailable):
        _request(user, dictation, queue=Broken())
    row = db.session.query(al.ClinicAlignmentRevision).filter_by(dictation_id=dictation.id).one()
    assert row.state == "pending" and row.job_id is None
    again = _request(user, dictation)
    assert again.id == row.id and again.job_id is not None
    assert db.session.query(ProcessingJob).filter_by(job_type="clinic_alignment").count() == 1


def test_handle_refuses_missing_dictation_or_row(env):
    user, dictation, audio, rev1 = _scene()
    with pytest.raises(jq.ClinicJobRefused):
        aj.handle({"dictation_id": "missing", "alignment_revision_id": "x"}, "c")
    with pytest.raises(jq.ClinicJobRefused):
        aj.handle({"dictation_id": dictation.id, "alignment_revision_id": "missing"}, "c")
