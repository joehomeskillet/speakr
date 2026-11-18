"""Wiring A: model registration, database append-only triggers, start-up sweep."""
from __future__ import annotations

import logging

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError

from src.app import app, db
from src.clinic import registry
from src.clinic.append_only_guards import APPEND_ONLY_TABLES, install_append_only_guards
from src.clinic.startup import run_startup_sweep


def test_every_clinic_model_is_on_the_metadata_and_in_the_database():
    with app.app_context():
        registry.create_clinic_tables()
        present = set(inspect(db.engine).get_table_names())
        declared = {t for t in db.metadata.tables if t.startswith('clinic_')}
        assert declared <= present
        for table in APPEND_ONLY_TABLES:
            assert table in declared
        # every db.Model subclass in src.clinic belongs to a registered module
        for mapper in db.Model.registry.mappers:
            cls = mapper.class_
            if cls.__module__.startswith('src.clinic.'):
                assert cls.__module__.split('.')[-1] in registry.MODEL_MODULES, cls


def test_create_clinic_tables_is_idempotent():
    with app.app_context():
        registry.create_clinic_tables()
        registry.create_clinic_tables()


def test_append_only_trigger_exists_for_each_table():
    with app.app_context():
        install_append_only_guards(db.engine)
        if db.engine.dialect.name == 'sqlite':
            names = {r[0] for r in db.session.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'trigger'"))}
            for table in APPEND_ONLY_TABLES:
                assert table + '_no_update' in names
                assert table + '_no_delete' in names
        else:
            names = {r[0] for r in db.session.execute(text(
                'SELECT tgname FROM pg_trigger WHERE NOT tgisinternal'))}
            for table in APPEND_ONLY_TABLES:
                assert table + '_append_only' in names


def test_raw_sql_cannot_change_an_audit_row(world):
    with app.app_context():
        install_append_only_guards(db.engine)
        db.session.execute(text(
            'INSERT INTO clinic_voice_consent_audit (user_id, action, vector_count, created_at) '
            "VALUES (:u, 'enrolled', 1, CURRENT_TIMESTAMP)"), {'u': world['user_a']})
        db.session.commit()
        with pytest.raises(DBAPIError):
            db.session.execute(text("UPDATE clinic_voice_consent_audit SET vector_count = 2"))
        db.session.rollback()
        with pytest.raises(DBAPIError):
            db.session.execute(text('DELETE FROM clinic_voice_consent_audit'))
        db.session.rollback()
        count = db.session.execute(text('SELECT COUNT(*) FROM clinic_voice_consent_audit')).scalar()
        assert count == 1


def test_install_append_only_guards_is_idempotent():
    with app.app_context():
        install_append_only_guards(db.engine)
        install_append_only_guards(db.engine)


def test_startup_sweep_is_off_without_clinic_mode(clinic_off, tmp_path, monkeypatch):
    monkeypatch.setenv('CLINIC_ORIGINALS_ROOT', str(tmp_path))
    with app.app_context():
        assert run_startup_sweep(app) is None


def test_startup_sweep_logs_one_line_with_counts(clinic_on, tmp_path, monkeypatch, caplog):
    monkeypatch.setenv('CLINIC_ORIGINALS_ROOT', str(tmp_path))
    with app.app_context(), caplog.at_level(logging.INFO, logger=app.logger.name):
        report = run_startup_sweep(app)
    assert report is not None and report.counts['scanned'] == 0
    lines = [r.getMessage() for r in caplog.records if 'Clinic original sweep' in r.getMessage()]
    assert lines == [
        'Clinic original sweep: scanned=0 removed_leftovers=0 incomplete=0 awaiting_row=0 errors=0'
    ]
