"""user.password VARCHAR(60) -> TEXT on PostgreSQL.

Runs only when PYTEST_DATABASE_URI points at PostgreSQL (the test is not
defined otherwise, so the default SQLite run has nothing to skip). It works in
a scratch database next to the test one and drops it afterwards.
"""

import os

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from werkzeug.security import generate_password_hash

from src.app import app, bcrypt
from src.init_db import widen_user_password_column

_URI = os.environ.get("PYTEST_DATABASE_URI", "")
_SCRATCH = "clinic_pw_migration"

if _URI.startswith("postgresql"):

    def test_postgresql_migration_is_idempotent_and_keeps_bcrypt_hash():
        admin = create_engine(_URI, isolation_level="AUTOCOMMIT")
        with admin.connect() as conn:
            conn.execute(text("DROP DATABASE IF EXISTS " + _SCRATCH))
            conn.execute(text("CREATE DATABASE " + _SCRATCH))
        engine = create_engine(make_url(_URI).set(database=_SCRATCH))
        try:
            with engine.begin() as conn:
                conn.execute(text(
                    'CREATE TABLE "user" (id INTEGER PRIMARY KEY, '
                    "username VARCHAR(20) NOT NULL, email VARCHAR(120) NOT NULL, "
                    "password VARCHAR(60))"
                ))
                conn.execute(text(
                    'CREATE UNIQUE INDEX ux_user_email ON "user" (email)'
                ))
            with app.app_context():
                bcrypt_hash = bcrypt.generate_password_hash("secret").decode("utf-8")
            scrypt_hash = generate_password_hash("secret", method="scrypt:131072:8:1")
            with engine.begin() as conn:
                conn.execute(
                    text(
                        'INSERT INTO "user" (id, username, email, password) '
                        "VALUES (1, 'legacy', 'legacy@example.invalid', :p)"
                    ),
                    {"p": bcrypt_hash},
                )

            def declared():
                with engine.connect() as conn:
                    return conn.execute(text(
                        "SELECT data_type FROM information_schema.columns "
                        "WHERE table_name = 'user' AND column_name = 'password'"
                    )).scalar()

            def stored(user_id):
                with engine.connect() as conn:
                    return conn.execute(
                        text('SELECT password FROM "user" WHERE id = :i'),
                        {"i": user_id},
                    ).scalar()

            assert declared() == "character varying"
            assert widen_user_password_column(engine) is True
            assert declared() == "text"
            assert stored(1) == bcrypt_hash
            with engine.begin() as conn:
                conn.execute(
                    text(
                        'INSERT INTO "user" (id, username, email, password) '
                        "VALUES (2, 'scrypt', 'scrypt@example.invalid', :p)"
                    ),
                    {"p": scrypt_hash},
                )
            assert widen_user_password_column(engine) is False
            assert stored(1) == bcrypt_hash
            assert stored(2) == scrypt_hash
        finally:
            engine.dispose()
            with admin.connect() as conn:
                conn.execute(text("DROP DATABASE IF EXISTS " + _SCRATCH))
            admin.dispose()
