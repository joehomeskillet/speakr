"""P10 ASR profiles, hotword sets and the training-evidence split.

Real SQLite from the shared test app, no DB mocks. The clinic_* tables of
the module are created here because the module is not wired into the app yet.
"""

import secrets
from datetime import datetime, timedelta, timezone

import pytest

from src.app import app, db
from src.clinic.asr_profiles import (
    AsrProfileError,
    ClinicAsrTrainingRun,
    asr_status,
    create_hotword_set,
    effective_hotwords,
    profile_history,
    record_training_run,
    set_profile,
    update_hotword_set,
)
from src.models import User


@pytest.fixture
def ctx():
    with app.app_context():
        db.create_all()
        tag = secrets.token_hex(4)
        users = {}
        for name in ("a", "b", "admin"):
            user = User(username=f"asr_{name}_{tag}", email=f"asr_{name}_{tag}@local.test",
                        password="x", is_admin=name == "admin")
            db.session.add(user)
            users[name] = user
        db.session.commit()
        yield {k: v.id for k, v in users.items()}
        db.session.rollback()


def _code(excinfo):
    return excinfo.value.code


# ---------------------------------------------------------------- versioning

def test_profile_versioning_records_who_when_what(ctx):
    p = set_profile(ctx["a"], actor_id=ctx["admin"], language="de", model="large-v3")
    assert p.version == 1
    p = set_profile(ctx["a"], actor_id=ctx["a"], model="large-v3-turbo",
                    options={"save_rule": "ask", "source": "recorder"})
    assert p.version == 2
    hist = profile_history(ctx["a"])
    assert [h.version for h in hist] == [1, 2]
    assert hist[0].actor_user_id == ctx["admin"]
    assert hist[1].actor_user_id == ctx["a"]
    assert hist[1].changes["model"] == ["large-v3", "large-v3-turbo"]
    assert "language" not in hist[1].changes
    assert hist[1].snapshot["language"] == "de"
    assert hist[1].snapshot["options"] == {"save_rule": "ask", "source": "recorder"}
    assert hist[1].changed_at.tzinfo is not None


def test_unchanged_call_writes_no_version(ctx):
    set_profile(ctx["a"], actor_id=ctx["a"], language="de")
    again = set_profile(ctx["a"], actor_id=ctx["a"], language="de")
    assert again.version == 1
    assert len(profile_history(ctx["a"])) == 1


def test_history_rows_are_append_only(ctx):
    set_profile(ctx["a"], actor_id=ctx["a"], language="de")
    row = profile_history(ctx["a"])[0]
    row.changes_json = "{}"
    with pytest.raises(AsrProfileError) as exc:
        db.session.commit()
    assert _code(exc) == "append_only"
    db.session.rollback()


def test_invalid_options_rejected(ctx):
    for bad in ({"nope": 1}, {"save_rule": "sometimes"}, {"skip_wait_minutes": 0},
                {"separate_offline_profile": "yes"}, {"source": "x"}):
        with pytest.raises(AsrProfileError):
            set_profile(ctx["a"], actor_id=ctx["a"], options=bad)
    assert profile_history(ctx["a"]) == []


def test_no_optimisation_option_exists(ctx):
    with pytest.raises(AsrProfileError) as exc:
        set_profile(ctx["a"], actor_id=ctx["a"], options={"use_for_optimization": True})
    assert _code(exc) == "option_unknown"


# ---------------------------------------------------------------- hotword sets

def test_author_set_beats_pool_set_and_merge_is_documented(ctx):
    pool = "pool-1"
    ps = create_hotword_set("Stationen", ["Pneumonie", "Ileus", "COPD"],
                            actor_id=ctx["admin"], pool_id=pool)
    ps2 = create_hotword_set("Medikamente", ["Metformin"], actor_id=ctx["admin"], pool_id=pool)
    au = create_hotword_set("Eigene", ["ileus", "Apixaban"], actor_id=ctx["a"],
                            owner_user_id=ctx["a"], blocked=["COPD"])
    # author set listed first, pool sets later: scope still decides, not list order
    set_profile(ctx["a"], actor_id=ctx["a"], hotword_set_ids=[au.id, ps.id, ps2.id])
    words = effective_hotwords(ctx["a"], pool_id=pool)
    assert words == ["Pneumonie", "Metformin", "ileus", "Apixaban"]  # winner spelling, COPD blocked
    assert "COPD" not in words


def test_pool_sets_only_apply_for_the_named_pool(ctx):
    ps = create_hotword_set("S", ["Alpha"], actor_id=ctx["admin"], pool_id="p1")
    au = create_hotword_set("E", ["Beta"], actor_id=ctx["a"], owner_user_id=ctx["a"])
    set_profile(ctx["a"], actor_id=ctx["a"], hotword_set_ids=[ps.id, au.id])
    assert effective_hotwords(ctx["a"], pool_id="p1") == ["Alpha", "Beta"]
    assert effective_hotwords(ctx["a"], pool_id="other") == ["Beta"]
    assert effective_hotwords(ctx["a"]) == ["Beta"]
    assert effective_hotwords(ctx["b"], pool_id="p1") == []


def test_later_reference_wins_within_same_scope(ctx):
    s1 = create_hotword_set("S1", ["Foo"], actor_id=ctx["a"], owner_user_id=ctx["a"])
    s2 = create_hotword_set("S2", ["FOO", "Bar"], actor_id=ctx["a"], owner_user_id=ctx["a"])
    set_profile(ctx["a"], actor_id=ctx["a"], hotword_set_ids=[s1.id, s2.id])
    assert effective_hotwords(ctx["a"]) == ["FOO", "Bar"]


def test_foreign_author_set_cannot_be_referenced(ctx):
    other = create_hotword_set("Fremd", ["X"], actor_id=ctx["b"], owner_user_id=ctx["b"])
    with pytest.raises(AsrProfileError) as exc:
        set_profile(ctx["a"], actor_id=ctx["a"], hotword_set_ids=[other.id])
    assert _code(exc) == "set_foreign"


def test_set_name_unique_per_owner_and_update_bumps_revision(ctx):
    s = create_hotword_set("Eigene", ["A"], actor_id=ctx["a"], owner_user_id=ctx["a"])
    with pytest.raises(AsrProfileError) as exc:
        create_hotword_set("Eigene", ["B"], actor_id=ctx["a"], owner_user_id=ctx["a"])
    assert _code(exc) == "name_taken"
    s = update_hotword_set(s.id, words=["A", "B"])
    assert s.revision == 2
    assert s.words == ["A", "B"]


def test_set_scope_must_be_exactly_one_owner(ctx):
    with pytest.raises(AsrProfileError):
        create_hotword_set("X", ["a"], actor_id=ctx["a"])
    with pytest.raises(AsrProfileError):
        create_hotword_set("X", ["a"], actor_id=ctx["a"], owner_user_id=ctx["a"], pool_id="p")


# ---------------------------------------------------------------- config vs evidence

def _run(user, actor, **kw):
    now = datetime.now(timezone.utc)
    args = dict(kind="acoustic", engine="ct2-adapt-x", started_at=now - timedelta(hours=1),
                finished_at=now, audio_seconds=600, text_chars=5000, dictation_count=12,
                result_ref="artifact://run-1", actor_id=actor)
    args.update(kw)
    return record_training_run(user, **args)


def test_configuration_never_creates_training_evidence(ctx):
    assert asr_status(ctx["a"]) == {**asr_status(ctx["a"]), "profile_configured": False,
                                    "training_proven": False}
    s = create_hotword_set("E", ["A"], actor_id=ctx["a"], owner_user_id=ctx["a"])
    set_profile(ctx["a"], actor_id=ctx["a"], language="de", model="large-v3",
                options={"save_rule": "always"}, hotword_set_ids=[s.id])
    update_hotword_set(s.id, words=["A", "B"])
    status = asr_status(ctx["a"])
    assert status["profile_configured"] is True
    assert status["training_proven"] is False
    assert status["training_runs"] == 0
    assert status["last_training_at"] is None
    assert ClinicAsrTrainingRun.query.count() == 0


def test_training_proven_only_by_recorded_run(ctx):
    set_profile(ctx["a"], actor_id=ctx["a"], language="de")
    run = _run(ctx["a"], ctx["admin"])
    status = asr_status(ctx["a"])
    assert status["profile_configured"] is True
    assert status["training_proven"] is True
    assert status["training_runs"] == 1
    assert status["last_training_at"] == run.finished_at
    assert run.result_ref == "artifact://run-1"
    assert run.audio_seconds == 600 and run.dictation_count == 12


def test_run_without_profile_is_proven_but_not_configured(ctx):
    _run(ctx["b"], ctx["admin"])
    status = asr_status(ctx["b"])
    assert status["profile_configured"] is False
    assert status["training_proven"] is True
    assert asr_status(ctx["a"])["training_proven"] is False  # per author


def test_training_run_needs_real_evidence_fields(ctx):
    for kw in ({"result_ref": ""}, {"result_ref": "  "}, {"kind": "magic"},
               {"audio_seconds": -1}, {"engine": ""}):
        with pytest.raises(AsrProfileError):
            _run(ctx["a"], ctx["admin"], **kw)
    now = datetime.now(timezone.utc)
    with pytest.raises(AsrProfileError) as exc:
        _run(ctx["a"], ctx["admin"], started_at=now, finished_at=now - timedelta(seconds=1))
    assert _code(exc) == "time_order"
    with pytest.raises(AsrProfileError) as exc:
        _run(ctx["a"], ctx["admin"], started_at=datetime.now())
    assert _code(exc) == "naive_datetime"
    assert asr_status(ctx["a"])["training_proven"] is False


def test_training_runs_are_append_only(ctx):
    run = _run(ctx["a"], ctx["admin"])
    run.result_ref = "changed"
    with pytest.raises(AsrProfileError) as exc:
        db.session.commit()
    assert _code(exc) == "append_only"
    db.session.rollback()
