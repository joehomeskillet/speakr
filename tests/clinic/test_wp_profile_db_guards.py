"""Database guards for ASR profile versions and training runs (w060)."""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from src.app import app, db
from src.clinic.append_only_guards import pg_trigger_name, trigger_names
from src.clinic.asr_profiles import (
    AsrProfileError,
    ClinicAsrProfile,
    ClinicAsrProfileVersion,
    ClinicAsrTrainingRun,
    asr_status,
    create_hotword_set,
    effective_hotwords,
    profile_history,
    record_training_run,
    set_profile,
)
from src.clinic.profile_db_guards import (
    PROFILE_HEAD_TABLE,
    PROFILE_HISTORY_TABLES,
    install_profile_db_guards,
)
from src.models import User


def _user(name):
    tag = secrets.token_hex(4)
    user = User(
        username=f"pdbg_{name}_{tag}",
        email=f"pdbg_{name}_{tag}@local.test",
        password="x",
    )
    db.session.add(user)
    db.session.commit()
    return user.id


def _selection(user_id):
    profile = ClinicAsrProfile.query.filter_by(user_id=user_id).one()
    status = asr_status(user_id)
    history = tuple(
        (row.version, row.changes_json, row.snapshot_json) for row in profile_history(user_id)
    )
    return (
        profile.version,
        profile.language,
        profile.model,
        profile.options,
        tuple(effective_hotwords(user_id)),
        history,
        status["training_proven"],
        status["training_runs"],
    )


def _run(user_id, actor_id, ref):
    now = datetime.now(timezone.utc)
    return record_training_run(
        user_id,
        kind="acoustic",
        engine="local-adapt",
        started_at=now - timedelta(minutes=5),
        finished_at=now,
        audio_seconds=30,
        text_chars=120,
        dictation_count=1,
        result_ref=ref,
        actor_id=actor_id,
    )


def _rejected(statement, params):
    with pytest.raises(DBAPIError) as caught:
        db.session.execute(text(statement), params)
    assert "append-only" in str(caught.value)
    db.session.rollback()


def test_install_is_idempotent_and_safe_before_tables_exist():
    engine = create_engine("sqlite://")
    try:
        install_profile_db_guards(engine)
        install_profile_db_guards(engine)
    finally:
        engine.dispose()
    with app.app_context():
        install_profile_db_guards(db.engine)
        install_profile_db_guards(db.engine)
        assert PROFILE_HEAD_TABLE not in PROFILE_HISTORY_TABLES
        # Drop this slice's triggers and install again, so the assertion is not
        # only seeing triggers the session fixture already created.
        if db.engine.dialect.name == "sqlite":
            for table in PROFILE_HISTORY_TABLES:
                for name in trigger_names(table):
                    db.session.execute(text("DROP TRIGGER IF EXISTS " + name))
        else:
            for table in PROFILE_HISTORY_TABLES:
                db.session.execute(
                    text("DROP TRIGGER IF EXISTS " + pg_trigger_name(table) + " ON " + table)
                )
        db.session.commit()
        install_profile_db_guards(db.engine)
        if db.engine.dialect.name == "sqlite":
            names = {
                row[0]
                for row in db.session.execute(
                    text("SELECT name FROM sqlite_master WHERE type = 'trigger'")
                )
            }
            for table in PROFILE_HISTORY_TABLES:
                update_name, delete_name = trigger_names(table)
                assert update_name in names
                assert delete_name in names
        else:
            names = {
                row[0]
                for row in db.session.execute(
                    text("SELECT tgname FROM pg_trigger WHERE NOT tgisinternal")
                )
            }
            for table in PROFILE_HISTORY_TABLES:
                assert pg_trigger_name(table) in names


def test_raw_and_stale_writes_fail_and_versioned_save_keeps_selection():
    with app.app_context():
        install_profile_db_guards(db.engine)
        user_id = _user("a")
        other_id = _user("b")
        own = create_hotword_set("Eigene", ["Ileus"], actor_id=user_id, owner_user_id=user_id)
        foreign = create_hotword_set("Fremd", ["X"], actor_id=other_id, owner_user_id=other_id)
        set_profile(
            user_id,
            actor_id=user_id,
            language="de-CH",
            model="large-v3",
            options={
                "save_rule": "ask",
                "source": "headset",
                "separate_offline_profile": False,
            },
            hotword_set_ids=[own.id],
        )
        run = _run(user_id, user_id, "artifact://run-1")
        chosen = _selection(user_id)
        version = profile_history(user_id)[0]

        with pytest.raises(AsrProfileError) as caught:
            set_profile(user_id, actor_id=user_id, hotword_set_ids=[foreign.id])
        assert caught.value.code == "set_foreign"
        db.session.rollback()

        _rejected(
            "UPDATE clinic_asr_profile_version SET snapshot_json = '{}' WHERE id = :id",
            {"id": version.id},
        )
        _rejected(
            "DELETE FROM clinic_asr_profile_version WHERE id = :id",
            {"id": version.id},
        )
        _rejected(
            "UPDATE clinic_asr_training_run SET result_ref = 'tampered' WHERE id = :id",
            {"id": run.id},
        )
        _rejected("DELETE FROM clinic_asr_training_run WHERE id = :id", {"id": run.id})

        with pytest.raises(DBAPIError) as caught:
            db.session.query(ClinicAsrProfileVersion).filter_by(id=version.id).update(
                {"changes_json": "{}"}, synchronize_session=False
            )
        assert "append-only" in str(caught.value)
        db.session.rollback()
        with pytest.raises(DBAPIError) as caught:
            db.session.query(ClinicAsrTrainingRun).filter_by(id=run.id).delete(
                synchronize_session=False
            )
        assert "append-only" in str(caught.value)
        db.session.rollback()

        db.session.add(
            ClinicAsrProfileVersion(
                profile_id=version.profile_id,
                version=version.version,
                actor_user_id=user_id,
                changes_json="{}",
                snapshot_json="{}",
            )
        )
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()

        db.session.execute(
            text("UPDATE clinic_asr_profile SET language = 'fr' WHERE user_id = :id"),
            {"id": user_id},
        )
        with pytest.raises(DBAPIError):
            db.session.execute(
                text(
                    "UPDATE clinic_asr_profile_version SET snapshot_json = '{}' "
                    "WHERE id = :id"
                ),
                {"id": version.id},
            )
        db.session.rollback()

        install_profile_db_guards(db.engine)
        db.session.expire_all()
        assert _selection(user_id) == chosen

        set_profile(user_id, actor_id=user_id, language="de")
        _run(user_id, user_id, "artifact://run-2")
        after = _selection(user_id)
        assert after[0] == chosen[0] + 1
        assert after[1] == "de"
        assert after[2] == chosen[2]
        assert after[3] == chosen[3]
        assert after[4] == chosen[4]
        assert after[5][: len(chosen[5])] == chosen[5]
        assert after[7] == chosen[7] + 1
        assert asr_status(user_id)["profile_configured"] is True
