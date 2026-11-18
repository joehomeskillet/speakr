"""A transcription that ends FAILED without raising ends its job failed (clinic mode only).

Empty ASR text and a used-up budget mark the recording FAILED and return
normally. In clinic mode the ProcessingJob then ends 'failed' with the
recording's error message, so the queue does not show "done". With clinic mode
off the upstream outcome ('completed') is unchanged.
"""
import secrets
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from src.app import app
from src.database import db
from src.models import ProcessingJob, Recording, User
from src.services.job_queue import job_queue
from src.tasks.processing import EMPTY_TRANSCRIPT_MESSAGE

OVER_BUDGET = "Monthly transcription budget exceeded (120 of 100 minutes used)."


@pytest.fixture
def queue():
    # A worker thread leaked by an earlier test polls the shared test DB and
    # can claim this test's job: make sure nothing is running, before and after.
    job_queue.stop()
    job_queue._is_owner = False
    job_queue.init_app(app)
    with app.app_context():
        yield job_queue
        job_queue.stop()
        job_queue._is_owner = False
        db.session.rollback()
        ProcessingJob.query.delete()
        db.session.commit()


def _storage():
    storage = MagicMock()
    mat = MagicMock()
    mat.local_path = "/tmp/jf_test.mp3"

    @contextmanager
    def _materialize(audio_path):
        yield mat

    storage.materialize.side_effect = _materialize
    return storage


def _run_failing_transcription(queue, message, returns_message):
    tag = secrets.token_hex(4)
    user = User(username=f"jf_{tag}", email=f"jf_{tag}@local.test", password="x")
    db.session.add(user)
    db.session.commit()
    rec = Recording(user_id=user.id, title="JF", audio_path="/tmp/jf_test.mp3",
                    original_filename="jf.mp3", status="QUEUED")
    db.session.add(rec)
    db.session.commit()
    rid = rec.id
    jid = queue.enqueue(user.id, rid, "transcribe")
    job = queue._claim_next_job(["transcribe"], "transcription")

    def runner(*args, **kwargs):
        row = db.session.get(Recording, rid)
        row.transcription = None
        row.status = "FAILED"
        row.error_message = message
        db.session.commit()
        return message if returns_message else None

    with patch("src.tasks.processing.transcribe_audio_task", side_effect=runner), \
         patch("src.services.storage.get_storage_service", return_value=_storage()), \
         patch("os.path.exists", return_value=True), \
         patch.object(queue, "_emit_started_webhook"), \
         patch("src.services.webhook_dispatch.emit_webhook_event"):
        queue._process_job(job)
    db.session.expire_all()
    return db.session.get(Recording, rid), db.session.get(ProcessingJob, jid)


CASES = [
    pytest.param(EMPTY_TRANSCRIPT_MESSAGE, False, id="empty-asr-text"),
    pytest.param(OVER_BUDGET, True, id="exhausted-budget"),
]


@pytest.mark.parametrize("message,returns_message", CASES)
def test_clinic_mode_on_ends_the_job_failed_with_the_recording_error(
        clinic_on, queue, message, returns_message):
    rec, job = _run_failing_transcription(queue, message, returns_message)
    assert (rec.status, rec.error_message) == ("FAILED", message)
    assert job.status == "failed"
    assert job.error_message == message
    assert job.completed_at is not None


@pytest.mark.parametrize("message,returns_message", CASES)
def test_clinic_mode_off_keeps_the_upstream_outcome(clinic_off, queue, message, returns_message):
    rec, job = _run_failing_transcription(queue, message, returns_message)
    assert (rec.status, rec.error_message) == ("FAILED", message)
    assert job.status == "completed"
    assert job.error_message is None
