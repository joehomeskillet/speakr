"""Exercise the production initializer without clinic test imports."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from flask import Flask
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError


WORKFLOW_TABLES = {
    "clinic_recipient_default",
    "clinic_live_phrase",
    "clinic_rich_text_snapshot",
}


@pytest.mark.parametrize("scenario", ["fresh", "upgrade"])
def test_workflow_schema_on_startup(tmp_path: Path, scenario: str) -> None:
    # Other clinic tests import model modules at collection time. A new process
    # makes missing production registrations visible regardless of test order.
    script = Path(__file__).resolve()
    root = script.parents[2]
    result = subprocess.run(
        [sys.executable, str(script), scenario, str(tmp_path)],
        cwd=root,
        env={
            **os.environ,
            "PYTHONPATH": str(root),
            "TMPDIR": str(tmp_path),
            "CLINIC_MODE": "1",
            "CLINIC_ORIGINALS_ROOT": str(tmp_path / "originals"),
            "ENABLE_INQUIRE_MODE": "false",
        },
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Migration section 'clinic integrity' failed" not in result.stderr


def _check_startup(scenario: str, directory: Path) -> None:
    from src.init_db import db, initialize_database

    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{directory / 'workflow.db'}"
    db.init_app(app)
    with app.app_context():
        if scenario == "upgrade":
            # Model the schema before these tables were registered, without
            # hand-copying any predecessor DDL or deleting existing tables.
            db.metadata.create_all(
                db.engine,
                tables=[table for table in db.metadata.sorted_tables
                        if table.name not in WORKFLOW_TABLES],
            )
            original_id, identity = _seed_history()

        before = set(inspect(db.engine).get_table_names())
        assert WORKFLOW_TABLES.isdisjoint(before)
        initialize_database(app)
        after = set(inspect(db.engine).get_table_names())
        assert before | WORKFLOW_TABLES <= after
        if scenario == "upgrade":
            assert _history(original_id) == identity

        # Service imports come after the first startup and schema assertion.
        from src.clinic.recipient_defaults import get_default, set_default
        from src.models import User

        if scenario == "upgrade":
            user_id = identity[0]
        else:
            author = User(username="author", email="author@example.invalid", password="synthetic")
            db.session.add(author)
            db.session.commit()
            user_id = author.id
        row = set_default(user_id, "self_correction", actor_id=user_id, expected_version=0)
        default_id = row.id
        db.session.remove()

        initialize_database(app)
        assert set(inspect(db.engine).get_table_names()) == after
        saved = get_default(user_id)
        assert saved is not None
        assert (saved.id, saved.author_user_id, saved.mode, saved.version) == (
            default_id, user_id, "self_correction", 1,
        )
        if scenario == "upgrade":
            from src.clinic.models import ClinicOriginal

            with pytest.raises(IntegrityError, match="clinic original is immutable"):
                with db.engine.begin() as connection:
                    connection.execute(
                        ClinicOriginal.__table__.update().where(ClinicOriginal.id == original_id)
                        .values(content_hash="b" * 64)
                    )
            assert _history(original_id) == identity


def _seed_history() -> tuple[str, tuple]:
    from src.clinic.models import ClinicDictation, ClinicOriginal
    from src.database import db
    from src.models import Recording, User

    author = User(username="author", email="author@example.invalid", password="synthetic")
    db.session.add(author)
    db.session.flush()
    recording = Recording(user_id=author.id, title="Synthetic dictation", status="PENDING")
    db.session.add(recording)
    db.session.flush()
    dictation = ClinicDictation(recording_id=recording.id, author_user_id=author.id)
    db.session.add(dictation)
    db.session.flush()
    original = ClinicOriginal(dictation_id=dictation.id, content_hash="a" * 64, size_bytes=10,
                              storage_key="synthetic/original", receipt="stored", origin_ref="test")
    db.session.add(original)
    db.session.commit()
    return original.id, _history(original.id)


def _history(original_id: str) -> tuple:
    from src.database import db

    with db.engine.connect() as connection:
        return tuple(connection.execute(text("""
            SELECT u.id, u.username, r.id, d.id, o.id, o.content_hash, o.storage_key
            FROM clinic_original_version AS o
            JOIN clinic_dictation AS d ON d.id = o.dictation_id
            JOIN recording AS r ON r.id = d.recording_id
            JOIN "user" AS u ON u.id = r.user_id AND u.id = d.author_user_id
            WHERE o.id = :original_id
        """), {"original_id": original_id}).one())


if __name__ == "__main__":
    _check_startup(sys.argv[1], Path(sys.argv[2]))
