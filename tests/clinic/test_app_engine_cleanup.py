"""Per-test Flask apps must release their pools before the next test starts."""

import pytest
from flask import Flask
from sqlalchemy import event, text

from src.app import app as main_app, db


@pytest.fixture(scope="module")
def retained_apps():
    # Keep apps alive deliberately: pool cleanup must not depend on cyclic GC.
    apps, closed = [], []
    yield apps, closed
    assert closed == [True] * 4


@pytest.mark.parametrize("iteration", range(2))
def test_previous_test_apps_close_all_bound_connections(retained_apps, iteration, monkeypatch):
    apps, closed = retained_apps
    assert closed == [True] * (iteration * 2)
    # init_app registers bind metadata globally; keep the extra test bind local.
    monkeypatch.setattr(db, "metadatas", db.metadatas.copy())
    app = Flask(__name__)
    app.config.update(
        SQLALCHEMY_DATABASE_URI=main_app.config["SQLALCHEMY_DATABASE_URI"],
        SQLALCHEMY_BINDS={"extra": main_app.config["SQLALCHEMY_DATABASE_URI"]},
    )
    db.init_app(app)
    apps.append(app)
    with app.app_context():
        for engine in db.engines.values():
            index = len(closed)
            closed.append(False)

            def on_close(_connection, _record, index=index):
                closed[index] = True

            event.listen(engine, "close", on_close)
            with engine.connect() as connection:
                assert connection.scalar(text("SELECT 1")) == 1
    with main_app.app_context():
        assert db.session.scalar(text("SELECT 1")) == 1
