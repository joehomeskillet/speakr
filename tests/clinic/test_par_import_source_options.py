"""Import-source backup name, retention and cleanup. Old rows keep EOL on and cleanup off.

SQLite only. This is service-layer evidence, not a Philips device proof.
"""

import uuid

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from src.app import app, db
from src.clinic import import_source_config as isc
from src.clinic import migration_import_source_options as migration
from src.clinic.agent_channel import AgentChannelError
from src.models import User

DEVICE_ROOT = "/srv/in/usb-1"
BACKUP_ROOT = "/srv/dropfolders/backup"
OLD_DDL = """
CREATE TABLE clinic_import_source_config (
    id VARCHAR(36) PRIMARY KEY,
    source_kind VARCHAR(16) NOT NULL,
    source_ref VARCHAR(128) NOT NULL,
    approved_root VARCHAR(1024) NOT NULL,
    target_author_user_id INTEGER NOT NULL,
    target_pool_id VARCHAR(36),
    autosend_enabled BOOLEAN NOT NULL,
    eol_filter_enabled BOOLEAN,
    file_pattern VARCHAR(255),
    priority INTEGER,
    language_id INTEGER,
    asr_profile_id VARCHAR(36),
    policy_version INTEGER NOT NULL,
    allow_source_delete BOOLEAN NOT NULL,
    created_by_user_id INTEGER NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
)
"""


def _user(prefix="opt"):
    row = User(username=prefix + uuid.uuid4().hex[:8], email=uuid.uuid4().hex[:8] + "@example.invalid",
               password="x", is_admin=True)
    db.session.add(row)
    db.session.commit()
    return row


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("CLINIC_IMPORT_ROOT_ALLOWLIST", "/srv/in;/srv/dropfolders")
    with app.app_context():
        isc.create_tables()
        yield


@pytest.fixture
def admin():
    with app.app_context():
        yield _user()


def _create(actor, **kwargs):
    base = dict(
        actor_user_id=actor.id, source_kind="device", source_ref="usb-" + uuid.uuid4().hex[:8],
        approved_root=DEVICE_ROOT, target_author_user_id=actor.id,
    )
    base.update(kwargs)
    return isc.create_source_config(**base)


def test_new_source_defaults_eol_on_and_cleanup_off(admin):
    cfg = _create(admin)
    opened = isc.get_source_config(actor_user_id=admin.id, config_id=cfg["id"])
    assert opened["eol_filter_enabled"] is True
    assert opened["cleanup_enabled"] is False
    assert opened["backup_name"] == ""
    assert opened["backup_root"] is None
    assert opened["backup_retention_days"] is None
    assert opened["file_pattern"] == "*"
    assert opened["priority"] == 1
    assert opened["language_id"] == 1031


def test_backup_without_approved_root_is_rejected(admin):
    for kwargs in (
        {"backup_name": "%A - %T"},
        {"backup_retention_days": 30},
        {"cleanup_enabled": True, "backup_retention_days": 30},
    ):
        with pytest.raises(AgentChannelError) as err:
            _create(admin, **kwargs)
        assert err.value.code == "invalid"
        assert "approved root" in str(err.value)


def test_backup_root_must_be_approved_and_disjoint(admin):
    with pytest.raises(AgentChannelError) as err:
        _create(admin, backup_root="/etc/backup", backup_name="%A")
    assert err.value.code == "forbidden"
    with pytest.raises(AgentChannelError) as err:
        _create(admin, backup_root="/srv/in/usb-1/copy", backup_name="%A")
    assert err.value.code == "invalid"
    with pytest.raises(AgentChannelError) as err:
        _create(admin, backup_root=BACKUP_ROOT, backup_name="../secret.ds2")
    assert err.value.code == "invalid"
    with pytest.raises(AgentChannelError) as err:
        _create(admin, backup_root=BACKUP_ROOT, cleanup_enabled=True)
    assert err.value.code == "invalid"


def test_approved_backup_keeps_cleanup_off_until_requested(admin):
    cfg = _create(admin, backup_root=BACKUP_ROOT, backup_name="%A - %T.ds2", backup_retention_days=30)
    assert cfg["backup_root"] == BACKUP_ROOT
    assert cfg["backup_enabled"] is True
    assert cfg["backup_name"] == "%A - %T.ds2"
    assert cfg["backup_retention_days"] == 30
    assert cfg["cleanup_enabled"] is False
    assert cfg["eol_filter_enabled"] is True
    opened = isc.update_source_config(
        actor_user_id=admin.id, config_id=cfg["id"], expected_version=1, cleanup_enabled=True,
    )
    assert opened["cleanup_enabled"] is True
    assert opened["policy_version"] == 2


def _insert(connection, config_id, *, eol, pattern, priority, language):
    connection.execute(text(
        "INSERT INTO clinic_import_source_config ("
        "id, source_kind, source_ref, approved_root, target_author_user_id, autosend_enabled, "
        "eol_filter_enabled, file_pattern, priority, language_id, policy_version, "
        "allow_source_delete, created_by_user_id, created_at, updated_at"
        ") VALUES ("
        ":id, 'device', :ref, :root, 1, 0, :eol, :pattern, :priority, :language, 1, 0, 1, "
        "'2026-01-01 00:00:00', '2026-01-01 00:00:00')"
    ), {"id": config_id, "ref": config_id, "root": DEVICE_ROOT, "eol": eol, "pattern": pattern,
        "priority": priority, "language": language})


def test_upgrade_old_source_then_open_settings(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "old.db"))
    with engine.begin() as connection:
        connection.execute(text(OLD_DDL))
        _insert(connection, "keep-on", eol=1, pattern="*.ds2", priority=128, language=1033)
        _insert(connection, "keep-off", eol=0, pattern="*.wav", priority=0, language=1031)
        _insert(connection, "blank", eol=None, pattern="", priority=None, language=None)
    ran = migration.upgrade(engine)
    assert len(ran) == 5 + 4  # five new columns, then EOL, pattern, priority and language
    assert migration.upgrade(engine) == []
    with Session(engine) as session:
        kept = isc._view(session.get(isc.ClinicImportSourceConfig, "keep-on"))
        explicit_off = isc._view(session.get(isc.ClinicImportSourceConfig, "keep-off"))
        blank = isc._view(session.get(isc.ClinicImportSourceConfig, "blank"))
    assert kept["eol_filter_enabled"] is True
    assert kept["cleanup_enabled"] is False
    assert kept["backup_root"] is None
    assert kept["backup_enabled"] is False
    assert kept["backup_name"] == ""
    assert kept["file_pattern"] == "*.ds2"
    assert kept["priority"] == 128
    assert kept["language_id"] == 1033
    assert explicit_off["eol_filter_enabled"] is False
    assert explicit_off["cleanup_enabled"] is False
    assert blank["eol_filter_enabled"] is True
    assert blank["cleanup_enabled"] is False
    assert blank["file_pattern"] == "*"
    assert blank["priority"] == 1
    assert blank["language_id"] == 1031
    assert {"backup_name", "backup_retention_days", "cleanup_enabled", "backup_root", "backup_enabled"} <= {
        column["name"] for column in inspect(engine).get_columns(migration.TABLE)
    }
    assert len(migration.downgrade(engine)) == 5
    left = {column["name"] for column in inspect(engine).get_columns(migration.TABLE)}
    assert not set(migration.COLUMNS) & left
    assert "eol_filter_enabled" in left and "file_pattern" in left
    with engine.connect() as connection:
        row = connection.execute(text(
            "SELECT eol_filter_enabled, file_pattern, priority FROM clinic_import_source_config WHERE id = 'keep-on'"
        )).one()
    assert (row[0], row[1], row[2]) == (1, "*.ds2", 128)
    assert migration.downgrade(engine) == []
