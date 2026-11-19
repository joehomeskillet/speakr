"""Training schedule on the existing job queue (PAR-ASR-005).

BT-ASR-005 stays the unchanged oracle and is not marked passed. These checks
cover windows, data thresholds, one intent per manifest/profile version,
unauthorised and stale callers, and separate cancelled/failed reporting.
"""

import threading
from datetime import datetime, timezone

import pytest

from src.app import app, db
from src.clinic.asr_profiles import set_profile
from src.clinic.training_manifest import build_manifest
from src.clinic.training_schedule import (
    ALREADY_SCHEDULED,
    DEFERRED,
    FAILED,
    INSUFFICIENT_DATA,
    JOB_TYPE,
    SCHEDULED,
    ScheduleConfig,
    TrainingScheduleError,
    parse_window,
    schedule_training,
    training_state,
)
from src.clinic.voice_consent import grant_consent, revoke_consent
from src.models import ProcessingJob, Recording
from src.services.job_queue import job_queue
from test_wp_training_manifest import _approved, _candidate, _user, clinic_db  # noqa: F401

NOON_UTC = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)  # 13:00 in Europe/Zurich
NIGHT = parse_window("22:00", "06:00")


@pytest.fixture(autouse=True)
def _no_workers(monkeypatch):
    monkeypatch.setattr(job_queue, "start", lambda: None)


@pytest.fixture(autouse=True)
def _clean_jobs(clinic_db):
    """Scenes are committed (see ``_scene``); drop the queued test jobs they leave behind."""
    yield
    db.session.rollback()
    ProcessingJob.query.filter_by(job_type=JOB_TYPE).delete()
    db.session.commit()


def _scene(kind="language_model", count=1):
    author = _user()
    set_profile(author.id, actor_id=author.id, language="de", model="stub")
    scenes = [_approved(author, "Alpha Beta", None) for _ in range(count)]
    manifest = build_manifest(
        author.id, scope="author", subject_user_id=author.id, kind=kind,
        candidates=[_candidate(scene) for scene in scenes],
    )
    db.session.commit()  # the queue writes on its own connection; SQLite would stay locked
    return author, scenes, manifest


def _call(author, manifest, **kw):
    kw.setdefault("config", ScheduleConfig())
    kw.setdefault("now", NOON_UTC)
    kw.setdefault("expected_profile_version", 1)
    return schedule_training(author.id, manifest, **kw)


def _jobs(author):
    return ProcessingJob.query.filter_by(user_id=author.id, job_type=JOB_TYPE).all()


def test_schedules_once_per_manifest_and_profile_version(clinic_db):
    author, scenes, manifest = _scene()
    recording = db.session.get(Recording, scenes[0]["dictation"].recording_id)
    status = recording.status

    first = _call(author, manifest)
    again = _call(author, manifest)

    assert first["state"] == SCHEDULED
    assert again["state"] == ALREADY_SCHEDULED
    assert again["job_id"] == first["job_id"]
    assert len(_jobs(author)) == 1
    assert db.session.get(Recording, recording.id).status == status
    assert training_state(manifest["manifest_hash"], 1, author.id) == "queued"


def test_concurrent_retries_queue_one_job(clinic_db):
    author, _scenes, manifest = _scene()
    results = []

    def worker():
        with app.app_context():
            results.append(_call(author, manifest))

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(r["state"] for r in results).count(SCHEDULED) == 1
    assert len({r["job_id"] for r in results}) == 1
    assert len(_jobs(author)) == 1


def test_window_uses_configured_timezone(clinic_db):
    author, _scenes, manifest = _scene()
    config = ScheduleConfig(windows=(NIGHT,))

    outside = _call(author, manifest, config=config)  # 13:00 local
    inside = _call(
        author, manifest, config=config,
        now=datetime(2026, 11, 18, 21, 30, tzinfo=timezone.utc),  # 22:30 local
    )

    assert outside["state"] == DEFERRED
    assert outside["reason"] == "outside_window"
    assert outside["next_window_start"] == "2026-11-18T22:00:00+01:00"
    assert inside["state"] == SCHEDULED
    assert len(_jobs(author)) == 1


def test_wrapped_window_and_weekday_filter(clinic_db):
    author, _scenes, manifest = _scene()
    early = datetime(2026, 11, 18, 4, 0, tzinfo=timezone.utc)  # 05:00 local, Wednesday
    wednesday_start = parse_window("22:00", "06:00", [2])  # window opened Tuesday: not allowed
    tuesday_start = parse_window("22:00", "06:00", [1])

    assert _call(author, manifest, now=early, config=ScheduleConfig(windows=(wednesday_start,)))[
        "state"
    ] == DEFERRED
    assert _call(author, manifest, now=early, config=ScheduleConfig(windows=(tuesday_start,)))[
        "state"
    ] == SCHEDULED


def test_insufficient_data_is_not_deferred_and_queues_nothing(clinic_db):
    author, scenes, manifest = _scene(count=2)
    stats = {s["revision"].id: {"audio_seconds": 10, "utterances": 2} for s in scenes}

    few = _call(author, manifest, config=ScheduleConfig(min_utterances=5), stats=stats)
    enough = _call(author, manifest, config=ScheduleConfig(min_utterances=4), stats=stats)
    assert few["state"] == INSUFFICIENT_DATA
    assert few["utterances"] == 4
    assert enough["state"] == SCHEDULED

    grant_consent(
        actor_user_id=author.id, user_id=author.id,
        statement_text="Ich erlaube die lokale Anpassung aus meinen Korrekturen.",
    )
    acoustic = build_manifest(
        author.id, scope="author", subject_user_id=author.id, kind="acoustic",
        candidates=[_candidate(s) for s in scenes],
    )
    short = _call(
        author, acoustic, stats=stats,
        config=ScheduleConfig(min_audio_seconds=30.0, windows=(NIGHT,)),
    )
    assert short["state"] == INSUFFICIENT_DATA
    assert short["audio_seconds"] == 20.0
    assert len(_jobs(author)) == 1
    revoke_consent(actor_user_id=author.id, user_id=author.id)


def test_unauthorized_stale_and_tampered_are_refused(clinic_db):
    author, _scenes, manifest = _scene()
    other = _user()
    forged = dict(manifest, scope="pool")

    for actor in (other.id, True, None):
        with pytest.raises(TrainingScheduleError) as denied:
            schedule_training(
                actor, manifest, config=ScheduleConfig(), now=NOON_UTC, expected_profile_version=1
            )
        assert denied.value.code == "unauthorized"
    with pytest.raises(TrainingScheduleError) as tampered:
        _call(author, forged)
    with pytest.raises(TrainingScheduleError) as stale:
        _call(author, manifest, expected_profile_version=2)
    with pytest.raises(TrainingScheduleError) as naive:
        _call(author, manifest, now=datetime(2026, 11, 18, 12, 0))

    assert tampered.value.code == "manifest_invalid"
    assert stale.value.code == "stale_version"
    assert naive.value.code == "now_naive"
    assert _jobs(author) == []

    set_profile(author.id, actor_id=author.id, model="stub-2")
    with pytest.raises(TrainingScheduleError) as moved:
        _call(author, manifest)
    assert moved.value.code == "stale_version"


def test_clinic_mode_off_refuses(clinic_db, monkeypatch):
    author, _scenes, manifest = _scene()
    monkeypatch.delenv("CLINIC_MODE")
    with pytest.raises(TrainingScheduleError) as off:
        _call(author, manifest)
    assert off.value.code == "clinic_mode_required"


def test_queue_failure_and_retry_are_reported_separately(clinic_db):
    author, _scenes, manifest = _scene()

    class Broken:
        def enqueue(self, *args, **kwargs):
            raise RuntimeError("queue down")

    failed = _call(author, manifest, queue=Broken())
    assert failed["state"] == FAILED
    assert failed["reason"] == "RuntimeError"
    assert training_state(manifest["manifest_hash"], 1, author.id) == "none"

    first = _call(author, manifest)
    job = db.session.get(ProcessingJob, first["job_id"])
    job.status = "failed"
    db.session.commit()
    assert training_state(manifest["manifest_hash"], 1, author.id) == "failed"

    retry = _call(author, manifest)  # a failed job is not active: the retry queues anew
    assert retry["state"] == SCHEDULED
    assert retry["job_id"] != first["job_id"]


def test_cancel_requested_is_reported_as_cancelled(clinic_db):
    author, _scenes, manifest = _scene()
    job = db.session.get(ProcessingJob, _call(author, manifest)["job_id"])
    job.status = "processing"
    job.cancel_requested_at = datetime.utcnow()
    db.session.commit()

    assert training_state(manifest["manifest_hash"], 1, author.id) == "cancelled"
    job.status = "completed"
    job.cancel_requested_at = None
    db.session.commit()
    assert training_state(manifest["manifest_hash"], 1, author.id) == "completed"


def test_other_intent_on_the_same_slot_is_deferred_not_merged(clinic_db):
    author, scenes, text = _scene()
    grant_consent(
        actor_user_id=author.id, user_id=author.id,
        statement_text="Ich erlaube die lokale Anpassung aus meinen Korrekturen.",
    )
    acoustic = build_manifest(
        author.id, scope="author", subject_user_id=author.id, kind="acoustic",
        candidates=[_candidate(scenes[0])],
    )
    first = _call(author, text)
    second = _call(author, acoustic)

    assert second["state"] == DEFERRED
    assert second["reason"] == "other_training_active"
    assert second["job_id"] == first["job_id"]
    assert len(_jobs(author)) == 1
    revoke_consent(actor_user_id=author.id, user_id=author.id)


def test_bad_window_is_refused():
    for bad in (("22:00", "22:00"), ("x", "06:00"), ("22:00", "06:00", [7]), ("22:00", "06:00", [])):
        with pytest.raises(TrainingScheduleError):
            parse_window(*bad)
