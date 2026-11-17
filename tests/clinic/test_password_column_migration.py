"""user.password grows from VARCHAR(60) to TEXT without rewriting old hashes."""

from sqlalchemy import create_engine, text
from werkzeug.security import generate_password_hash

from src.app import app, bcrypt
from src.init_db import widen_user_password_column


def _legacy_engine(path):
    engine = create_engine("sqlite:///" + path.as_posix())
    with engine.begin() as conn:
        conn.execute(text(
            'CREATE TABLE "user" ('
            "id INTEGER PRIMARY KEY, "
            "username VARCHAR(20) NOT NULL, "
            "email VARCHAR(120) NOT NULL, "
            "password VARCHAR(60), "
            "ui_language VARCHAR(10)"
            ")"
        ))
        conn.execute(text(
            'CREATE UNIQUE INDEX ux_user_email ON "user" (email)'
        ))
    return engine


def _password(engine, user_id):
    with engine.connect() as conn:
        return conn.execute(
            text('SELECT password FROM "user" WHERE id = :id'),
            {"id": user_id},
        ).scalar()


def _column_type(engine):
    with engine.connect() as conn:
        rows = conn.execute(text('PRAGMA table_info("user")')).fetchall()
    return next(row[2] for row in rows if row[1] == "password")


def test_scrypt_hash_round_trips_and_migration_is_idempotent(tmp_path):
    engine = _legacy_engine(tmp_path / "legacy.db")
    with app.app_context():
        bcrypt_hash = bcrypt.generate_password_hash("secret").decode("utf-8")
    assert len(bcrypt_hash) <= 60
    scrypt_hash = generate_password_hash("secret", method="scrypt:131072:8:1")
    assert len(scrypt_hash) > 60

    with engine.begin() as conn:
        conn.execute(
            text(
                'INSERT INTO "user" (id, username, email, password, ui_language) '
                "VALUES (1, 'legacy', 'legacy@example.invalid', :password, 'de')"
            ),
            {"password": bcrypt_hash},
        )

    assert widen_user_password_column(engine) is True
    assert _column_type(engine).upper() == "TEXT"
    assert _password(engine, 1) == bcrypt_hash

    with engine.begin() as conn:
        conn.execute(
            text(
                'INSERT INTO "user" (id, username, email, password, ui_language) '
                "VALUES (2, 'scrypt', 'scrypt@example.invalid', :password, 'de')"
            ),
            {"password": scrypt_hash},
        )
        kept = conn.execute(
            text('SELECT ui_language FROM "user" WHERE id = 1')
        ).scalar()
        index_names = {
            row[0]
            for row in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'user'")
            )
        }
    assert kept == "de"
    assert "ux_user_email" in index_names
    assert _password(engine, 2) == scrypt_hash

    assert widen_user_password_column(engine) is False
    assert _password(engine, 1) == bcrypt_hash
    assert _password(engine, 2) == scrypt_hash
    assert _column_type(engine).upper() == "TEXT"
