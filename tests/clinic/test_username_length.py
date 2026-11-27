"""Clinic username boundaries and the PostgreSQL startup upgrade."""

import os
from uuid import uuid4

import pytest
from flask import Flask
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import make_url

from src.app import app, db
from src.clinic import identity
from src.init_db import initialize_database
from src.models import User
from test_wp_identity_admin_api import admin_client, command  # noqa: F401


def _username(length):
    domain = '@example.invalid'
    return 'u' * (length - len(domain)) + domain if length > len(domain) else 'u' * length


@pytest.mark.parametrize('length', [2, 21, 120, 121])
def test_clinic_admin_username_length(admin_client, length):
    client, _ = admin_client
    path = '/admin/users'
    username = _username(length)
    response = command(client, 'POST', path, username=username, email='length@example.invalid')
    if length == 121:
        assert response.status_code == 400 and response.json['code'] == 'invalid_username'
        response = command(client, 'POST', path, username='valid', email='length@example.invalid')
    assert response.status_code == 201, response.json
    if length < 121:
        assert response.json['username'] == username
    account = response.json
    updated = command(client, 'PUT', f'{path}/{account["id"]}',
                      username='v' + username[1:], expected_version=account['version'])
    if length == 121:
        assert updated.status_code == 400 and updated.json['code'] == 'invalid_username'
    else:
        assert updated.status_code == 200, updated.json
        assert updated.json['username'] == 'v' + username[1:]


@pytest.mark.parametrize('length', [2, 21, 120, 121])
def test_clinic_identity_username_length(length):
    username = _username(length)
    with app.app_context():
        if length == 121:
            with pytest.raises(identity.IdentityConflict, match='^invalid_username$'):
                identity.create_local_user(username, 'length@example.invalid')
        else:
            user = identity.create_local_user(username, 'length@example.invalid')
            assert identity.find_login_user(username).id == user.id
            assert db.session.get(User, user.id).username == username


@pytest.mark.parametrize('length', [2, 120, 121])
def test_docker_admin_username_length(monkeypatch, length):
    from scripts.docker_create_admin import create_admin_user_from_env

    username = _username(length)
    monkeypatch.setenv('ADMIN_USERNAME', username)
    monkeypatch.setenv('ADMIN_EMAIL', 'docker-admin@example.com')
    monkeypatch.setenv('ADMIN_PASSWORD', 'synthetic administration phrase 37')
    if length == 121:
        with pytest.raises(SystemExit) as exc:
            create_admin_user_from_env()
        assert exc.value.code == 1
    else:
        create_admin_user_from_env()
        with app.app_context():
            assert User.query.filter_by(username=username, is_admin=True).one().email == 'docker-admin@example.com'


# Match test_postgres_migration.py: collect only when CI selects PostgreSQL.
_URI = os.environ.get('PYTEST_DATABASE_URI', '')
if _URI.startswith('postgresql'):

    def test_postgresql_startup_widens_username_once(tmp_path):
        admin = create_engine(_URI, isolation_level='AUTOCOMMIT')
        scratch = f'clinic_username_migration_{uuid4().hex}'
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE {scratch}'))
        scratch_app = Flask(__name__)
        scratch_app.config.update(SQLALCHEMY_DATABASE_URI=make_url(_URI).set(database=scratch),
                                  UPLOAD_FOLDER=str(tmp_path))
        db.init_app(scratch_app)
        with scratch_app.app_context():
            engine = db.engine
            try:
                db.create_all()
                with engine.begin() as conn:
                    conn.execute(text('ALTER TABLE "user" ALTER COLUMN username TYPE VARCHAR(20)'))
                    conn.execute(text(
                        'INSERT INTO "user" (username, email) VALUES (\'legacy\', \'legacy@example.invalid\')'
                    ))

                def declared_length():
                    columns = inspect(engine).get_columns('user')
                    return next(column['type'].length for column in columns if column['name'] == 'username')

                alters = []

                @event.listens_for(engine, 'before_cursor_execute')
                def record_username_alter(conn, cursor, statement, parameters, context, executemany):
                    if statement.startswith('ALTER TABLE "user" ALTER COLUMN "username"'):
                        alters.append(statement)

                assert declared_length() == 20
                initialize_database(scratch_app)
                assert declared_length() == 120
                initialize_database(scratch_app)
                assert declared_length() == 120
                assert len(alters) == 1
                with engine.connect() as conn:
                    assert conn.scalar(text('SELECT username FROM "user"')) == 'legacy'
                assert any(item['column_names'] == ['username']
                           for item in inspect(engine).get_unique_constraints('user'))
            finally:
                db.session.remove()
                engine.dispose()
                with admin.connect() as conn:
                    conn.execute(text(f'DROP DATABASE {scratch}'))
                admin.dispose()
