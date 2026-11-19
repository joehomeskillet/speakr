"""Production initializer registers import-source and identity-intent tables."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from flask import Flask
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

NEW_TABLES = {"clinic_import_source_config", "clinic_identity_link_request"}


@pytest.mark.parametrize("scenario", ["fresh", "upgrade"])
def test_integration_schema_on_startup(tmp_path: Path, scenario: str) -> None:
    # A fresh process hides registrations that other tests imported earlier.
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
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Migration section 'clinic integrity' failed" not in result.stderr


def _check_startup(scenario: str, directory: Path) -> None:
    from src.init_db import db, initialize_database

    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{directory / 'integration.db'}"
    db.init_app(app)
    with app.app_context():
        identity = None
        if scenario == "upgrade":
            db.metadata.create_all(
                db.engine,
                tables=[t for t in db.metadata.sorted_tables if t.name not in NEW_TABLES],
            )
            original_id, identity = _seed_history()

        before = set(inspect(db.engine).get_table_names())
        assert NEW_TABLES.isdisjoint(before)
        initialize_database(app)
        after = set(inspect(db.engine).get_table_names())
        assert before | NEW_TABLES <= after
        _assert_foreign_keys()
        if identity:
            assert _history(original_id) == identity

        from src.clinic.entra_link_intent import ClinicIdentityLinkRequest
        from src.clinic.import_source_config import ClinicImportSourceConfig

        # Every column of the two new models exists in the created tables.
        for model in (ClinicImportSourceConfig, ClinicIdentityLinkRequest):
            columns = {c["name"] for c in inspect(db.engine).get_columns(model.__tablename__)}
            assert {c.name for c in model.__table__.columns} <= columns

        # Second startup is idempotent and keeps the table set unchanged.
        db.session.remove()
        initialize_database(app)
        assert set(inspect(db.engine).get_table_names()) == after
        if identity:
            assert _history(original_id) == identity
            _assert_failed_transaction_rolls_back(original_id, identity)
            _assert_original_immutable(original_id, identity)


def _assert_foreign_keys() -> None:
    from src.database import db

    insp = inspect(db.engine)
    for table, cols in (
        ("clinic_import_source_config", {"target_author_user_id", "created_by_user_id"}),
        ("clinic_identity_link_request", {"user_id", "requested_by"}),
    ):
        fks = {tuple(fk["constrained_columns"]): fk["referred_table"] for fk in insp.get_foreign_keys(table)}
        for col in cols:
            assert fks.get((col,)) == "user"


def _assert_failed_transaction_rolls_back(original_id: str, identity: tuple) -> None:
    from src.database import db

    def users() -> int:
        with db.engine.connect() as connection:
            return connection.execute(text('SELECT COUNT(*) FROM "user"')).scalar_one()

    count = users()
    with pytest.raises(RuntimeError, match="forced failure"):
        with db.engine.begin() as connection:
            connection.execute(text(
                'INSERT INTO "user" (username, email, password) '
                "VALUES ('rollback-probe', 'probe@example.invalid', 'x')"
            ))
            raise RuntimeError("forced failure")
    assert users() == count
    assert _history(original_id) == identity


def _assert_original_immutable(original_id: str, identity: tuple) -> None:
    from src.clinic.models import ClinicOriginal
    from src.database import db

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
