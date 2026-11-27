"""P-016: durable per-user bindings, conflicts, resets and concurrent writers."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from flask import Flask
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError

from src.clinic import registry
from src.clinic.key_bindings import (
    DEFAULTS, BindingConflict, BindingError, ClinicKeyBindings, KeyBindingStore,
    StaleVersion, create_tables,
)
from src.database import db


@pytest.fixture
def apps(tmp_path):
    apps = [Flask(__name__), Flask(__name__)]
    for app in apps:
        app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{tmp_path / 'bindings.db'}"
        db.init_app(app)
        with app.app_context():
            @event.listens_for(db.engine, "connect")
            def enforce_foreign_keys(connection, _record):
                connection.execute("PRAGMA foreign_keys=ON")

    with apps[0].app_context():
        # Only the parent key is needed to exercise real FK enforcement here.
        with db.engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE user (id INTEGER PRIMARY KEY)")
            connection.exec_driver_sql("INSERT INTO user (id) VALUES (1), (2)")
        create_tables()
        create_tables()  # Existing installations can run startup again.
    yield apps
    for app in apps:
        with app.app_context():
            db.session.remove()
            db.engine.dispose()


def test_registered_and_durable_across_apps(apps):
    assert "key_bindings" in registry.MODEL_MODULES
    with apps[0].app_context():
        store = KeyBindingStore()
        initial = store.load(1)
        assert (initial.user_id, initial.version, initial.bindings) == (1, 0, DEFAULTS)
        assert db.session.query(ClinicKeyBindings).count() == 0
        saved = store.save(1, {"stop": " ctrl + alt + s "}, 0)
        assert saved.version == 1
        assert saved.bindings == {**DEFAULTS, "stop": "Ctrl+Alt+S"}
        saved.bindings["stop"] = "F2"  # Returned mappings cannot mutate storage.
        db.session.remove()
        db.engine.dispose()
    with apps[1].app_context():
        loaded = KeyBindingStore().load(1)
        assert loaded.version == 1
        assert loaded.bindings == {**DEFAULTS, "stop": "Ctrl+Alt+S"}


def test_reset_one_and_all_preserve_other_account(apps):
    with apps[0].app_context():
        store = KeyBindingStore()
        custom = {**DEFAULTS, "stop": "F1", "rewind": "F2"}
        store.save(1, custom, 0)
        other = store.set_binding(2, "stop", "F3", 0)
        restored = store.reset(1, "stop", expected_version=1)
        assert restored.version == 2
        assert restored.bindings == {**custom, "stop": DEFAULTS["stop"]}
    with apps[1].app_context():
        store = KeyBindingStore()
        assert store.load(1) == restored
        assert store.load(2) == other
        assert store.reset(1, expected_version=2).bindings == DEFAULTS
        assert store.load(1).version == 3
        assert store.load(2) == other


def test_conflicts_leave_entire_row_unchanged(apps):
    with apps[0].app_context():
        store = KeyBindingStore()
        store.set_binding(1, "stop", "F1", 0)
        saved = store.set_binding(1, "rewind", DEFAULTS["stop"], 1)
        with pytest.raises(BindingConflict) as reset_conflict:
            store.reset(1, "stop", expected_version=2)
        assert reset_conflict.value.conflicts == ((DEFAULTS["stop"], "rewind"),)
        with pytest.raises(BindingConflict):
            store.set_binding(1, "forward", "f1", 2)
        with pytest.raises(BindingConflict):
            store.save(1, {"stop": "F2", "rewind": "f2"}, 2)
        assert store.load(1) == saved


@pytest.mark.parametrize("operation", ["save", "set_binding", "reset"])
def test_stale_version_rejected_for_every_write(apps, operation):
    with apps[0].app_context():
        saved = KeyBindingStore().set_binding(1, "stop", "F1", 0)
    with apps[1].app_context():
        store = KeyBindingStore()
        args = {"save": (DEFAULTS,), "set_binding": ("stop", "F2"), "reset": ("stop",)}
        with pytest.raises(StaleVersion) as stale:
            getattr(store, operation)(1, *args[operation], expected_version=0)
        assert stale.value.current_version == 1
        assert store.load(1) == saved


@pytest.mark.parametrize("existing", [False, True])
def test_simultaneous_writers_have_exactly_one_winner(apps, monkeypatch, existing):
    if existing:
        with apps[0].app_context():
            KeyBindingStore().save(1, DEFAULTS, 0)
    barrier = Barrier(2, timeout=10)
    read = KeyBindingStore._get

    def write(app, chord):
        with app.app_context():
            store = KeyBindingStore()
            first_read = True

            def synchronized_read(user_id):
                nonlocal first_read
                row = read(store, user_id)
                if first_read:
                    first_read = False
                    barrier.wait()  # Both writers have read the same version.
                return row

            monkeypatch.setattr(store, "_get", synchronized_read)
            try:
                return store.set_binding(1, "stop", chord, int(existing))
            except StaleVersion as error:
                return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(write, app, chord) for app, chord in zip(apps, ("F1", "F2"))]
        results = [future.result(timeout=20) for future in futures]
    losers = [result for result in results if isinstance(result, StaleVersion)]
    winners = [result for result in results if not isinstance(result, StaleVersion)]
    assert len(losers) == len(winners) == 1
    assert losers[0].current_version == winners[0].version == int(existing) + 1
    with apps[0].app_context():
        assert KeyBindingStore().load(1) == winners[0]


@pytest.mark.parametrize("version", [None, True, -1, "0"])
def test_invalid_version_never_writes(apps, version):
    with apps[0].app_context():
        with pytest.raises(BindingError, match="^bad_version$"):
            KeyBindingStore().reset(1, expected_version=version)
        assert KeyBindingStore().load(1).version == 0


def test_missing_user_is_not_reported_as_concurrent_update(apps):
    with apps[0].app_context():
        with pytest.raises(IntegrityError):
            KeyBindingStore().save(999, DEFAULTS, 0)
        assert KeyBindingStore().load(1).version == 0


@pytest.mark.parametrize("user_id", [True, 0, -1, "1", None])
def test_invalid_user_rejected(user_id):
    store = KeyBindingStore()
    with pytest.raises(BindingError, match="^bad_user$"):
        store.load(user_id)
    with pytest.raises(BindingError, match="^bad_user$"):
        store.save(user_id, DEFAULTS, 0)


@pytest.mark.parametrize(("bindings", "code"), [
    ({"unknown": "F1"}, "unknown_action"),
    ({"stop": "Ctrl+W"}, "reserved_chord"),
    ({"stop": None}, "bad_chord"),
    ([], "bad_bindings"),
])
def test_invalid_bindings_leave_storage_unchanged(apps, bindings, code):
    with apps[0].app_context():
        store = KeyBindingStore()
        saved = store.save(1, DEFAULTS, 0)
        with pytest.raises(BindingError, match=f"^{code}$"):
            store.save(1, bindings, 1)
        assert store.load(1) == saved


def test_memory_mode_keeps_copies_and_version_checks():
    store = KeyBindingStore()
    saved = store.set_binding(1, "stop", "F1", 0)
    saved.bindings["stop"] = "F2"
    assert store.load(1).bindings["stop"] == "F1"
    with pytest.raises(StaleVersion):
        store.reset(1, expected_version=0)
    assert store.reset(1, "stop", expected_version=1).bindings == DEFAULTS
    assert store.load(2).version == 0
