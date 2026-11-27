"""Exercise PAR schema registration and upgrades through the central registry."""

import pytest
from sqlalchemy import create_engine, inspect, text

from src.clinic.registry import MODEL_MODULES, create_clinic_tables
from src.database import db


@pytest.fixture
def engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'clinic.db'}")
    try:
        db.metadata.create_all(
            engine, tables=[table for table in db.metadata.tables.values()
                            if not table.name.startswith("clinic_")],
        )
        yield engine
    finally:
        engine.dispose()


def assert_all_clinic_tables(engine):
    declared = {name for name in db.metadata.tables if name.startswith("clinic_")}
    present = {name for name in inspect(engine).get_table_names() if name.startswith("clinic_")}
    assert present == declared


def test_registered_par_models_create_tables_on_fresh_database(engine):
    assert len(MODEL_MODULES) == len(set(MODEL_MODULES))
    assert {
        "author_aliases", "correction_recognition_marks", "draft_store", "key_bindings",
        "notification_preferences", "output_preferences", "recipient_assignment",
        "statistics_grants", "workstation_preferences",
    } <= set(MODEL_MODULES)

    create_clinic_tables(engine)
    assert_all_clinic_tables(engine)


def test_registry_upgrades_legacy_database_twice_and_keeps_rows(engine):
    # Keep just the legacy columns needed to exercise each additive upgrade.
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE clinic_workflow_dictation (id TEXT PRIMARY KEY, assignee_user_id INTEGER)"))
        conn.execute(text("INSERT INTO clinic_workflow_dictation VALUES ('old-workflow', 42)"))
        conn.execute(text("CREATE TABLE clinic_agent_inventory (id TEXT PRIMARY KEY, snapshot_version TEXT)"))
        conn.execute(text("INSERT INTO clinic_agent_inventory VALUES ('old-inventory', 'snapshot-1')"))
        conn.execute(text("CREATE TABLE clinic_import_intent (id TEXT PRIMARY KEY, target_author_user_id INTEGER)"))
        conn.execute(text("INSERT INTO clinic_import_intent VALUES ('old-intent', 7)"))
        conn.execute(text("CREATE TABLE clinic_import_source_config ("
                          "id TEXT PRIMARY KEY, eol_filter_enabled BOOLEAN, file_pattern TEXT, "
                          "priority INTEGER, language_id INTEGER)"))
        conn.execute(text("INSERT INTO clinic_import_source_config VALUES ('old-source', 0, '*.wav', 128, 1033)"))

    for _ in range(2):
        create_clinic_tables(engine)
        assert_all_clinic_tables(engine)
        with engine.connect() as conn:
            assert conn.execute(text(
                "SELECT id, assignee_user_id, assigned_at FROM clinic_workflow_dictation"
            )).one() == ("old-workflow", 42, None)
            assert conn.execute(text(
                "SELECT id, snapshot_version, claimed_author_code, completion_source, evidence_level "
                "FROM clinic_agent_inventory"
            )).one() == ("old-inventory", "snapshot-1", None, "unknown", "unverified")
            assert conn.execute(text(
                "SELECT id, target_author_user_id, metadata_version, metadata_json FROM clinic_import_intent"
            )).one() == ("old-intent", 7, None, None)
            assert conn.execute(text(
                "SELECT id, eol_filter_enabled, file_pattern, priority, language_id, "
                "backup_name, backup_retention_days, cleanup_enabled, backup_root FROM clinic_import_source_config"
            )).one() == ("old-source", 0, "*.wav", 128, 1033, "", None, 0, None)
