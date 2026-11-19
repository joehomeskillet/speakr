"""Durable LayoutStore and WorklistFilterStore (x25b).

Two Flask apps share one SQLite file, standing in for two workers or a restart.
"""

import json

import pytest
from flask import Flask

from src.clinic import registry
from src.clinic.list_layout import LayoutError
from src.clinic.saved_filters import FilterError
from src.clinic.worklist_filters import ClinicWorklistFilter, WorklistFilterStore
from src.clinic.worklist_layout import ClinicWorklistLayout, LayoutStore, WorklistLayoutError
from src.database import db

COLUMNS = ["title", "status"]
DEFAULT = {
    "version": 1,
    "columns": [
        {"key": "title", "width": 200, "visible": True},
        {"key": "status", "width": 100, "visible": True},
    ],
    "sort": [["title", "asc"]],
    "group_by": [],
}
SPEC = {"all": [{"field": "status", "op": "eq", "value": "open"}], "any": []}


def _make_app(uri):
    flask_app = Flask(__name__)
    flask_app.config["SQLALCHEMY_DATABASE_URI"] = uri
    db.init_app(flask_app)
    return flask_app


@pytest.fixture
def apps(tmp_path):
    uri = "sqlite:///" + str(tmp_path / "shared.db")
    first, second = _make_app(uri), _make_app(uri)
    with first.app_context():
        db.metadata.create_all(
            bind=db.engine,
            tables=[ClinicWorklistLayout.__table__, ClinicWorklistFilter.__table__],
        )
    return first, second


def _layout(width):
    layout = json.loads(json.dumps(DEFAULT))
    layout["columns"][0]["width"] = width
    return layout


def test_models_are_registered():
    assert "worklist_layout" in registry.MODEL_MODULES
    assert "worklist_filters" in registry.MODEL_MODULES
    assert ClinicWorklistLayout.__tablename__.startswith("clinic_")
    assert ClinicWorklistFilter.__tablename__.startswith("clinic_")


def test_layout_survives_restart_and_is_shared(apps):
    first, second = apps
    with first.app_context():
        saved = LayoutStore().save(7, 7, _layout(300), COLUMNS, 0)
        assert saved.version == 1
    with second.app_context():
        store = LayoutStore()
        loaded = store.load(7, 7, DEFAULT, COLUMNS)
        assert loaded.version == 1
        assert loaded.layout["columns"][0]["width"] == 300
        assert store.save(7, 7, _layout(250), COLUMNS, 1).version == 2
    with first.app_context():
        assert LayoutStore().load(7, 7, DEFAULT, COLUMNS).layout["columns"][0]["width"] == 250


def test_layout_isolation_and_version_checks(apps):
    first, second = apps
    with first.app_context():
        store = LayoutStore()
        store.save(1, 1, _layout(300), COLUMNS, 0)
        with pytest.raises(WorklistLayoutError) as denied:
            store.load(2, 1, DEFAULT, COLUMNS)
        assert denied.value.code == "unauthorized"
        with pytest.raises(WorklistLayoutError) as denied:
            store.save(2, 1, _layout(100), COLUMNS, 1)
        assert denied.value.code == "unauthorized"
        # user 2 sees the default, not user 1's layout
        assert store.load(2, 2, DEFAULT, COLUMNS).version == 0
        with pytest.raises(WorklistLayoutError) as stale:
            store.save(1, 1, _layout(100), COLUMNS, 0)
        assert stale.value.code == "stale_version"
    with second.app_context():
        # a writer holding an old version loses the compare-and-set
        store = LayoutStore()
        store.save(1, 1, _layout(120), COLUMNS, 1)
        with pytest.raises(WorklistLayoutError) as stale:
            store.save(1, 1, _layout(130), COLUMNS, 1)
        assert stale.value.code == "stale_version"


def test_layout_size_limit_and_bad_stored_json(apps):
    first, _second = apps
    with first.app_context():
        store = LayoutStore()
        big = _layout(300)
        big["columns"] = [{"key": "c" * 300 + str(i), "width": 100, "visible": True} for i in range(30)]
        with pytest.raises(WorklistLayoutError) as large:
            store.save(1, 1, big, COLUMNS + [c["key"] for c in big["columns"]], 0)
        assert large.value.code == "oversized"
        db.session.add(ClinicWorklistLayout(user_id=9, version=1, layout_json="{not json"))
        db.session.add(ClinicWorklistLayout(user_id=10, version=1, layout_json="[1]"))
        db.session.commit()
        for user in (9, 10):
            with pytest.raises(LayoutError):
                store.load(user, user, DEFAULT, COLUMNS)


def test_layout_restore_snapshot_is_durable(apps):
    first, second = apps
    with first.app_context():
        store = LayoutStore()
        store.save(3, 3, _layout(300), COLUMNS, 0)
        snap = store.snapshot(3, 3)
    with second.app_context():
        assert LayoutStore().snapshot(3, 3) == snap
        LayoutStore().restore_snapshot(3, 3, *snap)  # same blob again: no-op


def test_filters_survive_restart_and_are_shared(apps):
    first, second = apps
    with first.app_context():
        created = WorklistFilterStore().create(5, "Offen", SPEC)
        assert (created.id, created.version) == ("f1", 1)
    with second.app_context():
        store = WorklistFilterStore()
        assert [(f.id, f.name, f.version) for f in store.list(5)] == [("f1", "Offen", 1)]
        renamed = store.rename(5, "f1", "Neu", expected_version=1)
        assert renamed.version == 2
        replaced = store.replace(5, "f1", {"all": [], "any": []}, expected_version=2)
        assert replaced.version == 3
        assert store.create(5, "Zweit", SPEC).id == "f2"
    with first.app_context():
        store = WorklistFilterStore()
        got = store.get(5, "f1")
        assert (got.name, got.version, got.spec) == ("Neu", 3, {"all": [], "any": []})
        store.delete(5, "f1", expected_version=3)
    with second.app_context():
        store = WorklistFilterStore()
        assert [f.id for f in store.list(5)] == ["f2"]
        assert store.create(5, "Wieder", SPEC).id == "f1"  # gap is reused as before


def test_filters_isolation_cap_and_stale_versions(apps):
    first, second = apps
    with first.app_context():
        store = WorklistFilterStore()
        store.create(1, "Privat", SPEC)
        assert store.list(2) == []
        with pytest.raises(FilterError) as hidden:
            store.get(2, "f1")
        assert hidden.value.code == "not_found"
        with pytest.raises(FilterError) as hidden:
            store.rename(2, "f1", "x", expected_version=1)
        assert hidden.value.code == "not_found"
        with pytest.raises(FilterError) as dup:
            store.create(1, "privat", SPEC)
        assert dup.value.code == "duplicate_name"
        for index in range(19):
            store.create(1, f"F{index}", SPEC)
        with pytest.raises(FilterError) as cap:
            store.create(1, "zu viel", SPEC)
        assert cap.value.code == "limit_reached"
        assert len(store.list(1)) == 20
    with second.app_context():
        store = WorklistFilterStore()
        with pytest.raises(FilterError) as cap:
            store.create(1, "auch zu viel", SPEC)
        assert cap.value.code == "limit_reached"
        store.rename(1, "f1", "Anders", expected_version=1)
    with first.app_context():
        with pytest.raises(FilterError) as stale:
            WorklistFilterStore().rename(1, "f1", "Spaet", expected_version=1)
        assert stale.value.code == "stale_version"
        assert WorklistFilterStore().get(1, "f1").name == "Anders"


def test_concurrent_create_loses_on_unique_id(apps):
    first, _second = apps
    with first.app_context():
        store = WorklistFilterStore()
        store.create(4, "Eins", SPEC)
        store._open(4)
        # another worker takes f2 between this worker's read and write
        db.session.add(ClinicWorklistFilter(user_id=4, filter_id="f2", name="Fremd",
                                            spec_json=json.dumps(SPEC), version=1))
        db.session.commit()
        with pytest.raises(FilterError) as lost:
            store._store.save(4, "Meins", SPEC)  # prime memory as create() would
            store._versions[(4, "f2")] = 1
            store._publish(store._dump())
        assert lost.value.code == "stale_version"
        assert [f.name for f in WorklistFilterStore().list(4)] == ["Eins", "Fremd"]


def test_invalid_stored_filter_is_rejected(apps):
    first, _second = apps
    with first.app_context():
        db.session.add(ClinicWorklistFilter(user_id=8, filter_id="f1", name="Kaputt",
                                            spec_json=json.dumps({"all": [{"op": "bogus"}]}), version=1))
        db.session.add(ClinicWorklistFilter(user_id=11, filter_id="f1", name="Kein Json",
                                            spec_json="{nope", version=1))
        db.session.commit()
        store = WorklistFilterStore()
        for user in (8, 11):
            with pytest.raises(FilterError):
                store.list(user)


def test_without_database_stores_stay_in_memory():
    # no app context
    layouts, filters = LayoutStore(), WorklistFilterStore()
    assert layouts.save(1, 1, _layout(300), COLUMNS, 0).version == 1
    assert filters.create(1, "Offen", SPEC).id == "f1"
    # app context without the database extension (the CLINIC_MODE-off shape)
    with Flask(__name__).app_context():
        assert layouts.load(1, 1, DEFAULT, COLUMNS).version == 1
        assert filters.list(1)[0].name == "Offen"
        assert LayoutStore().load(1, 1, DEFAULT, COLUMNS).version == 0
        assert WorklistFilterStore().list(1) == []
