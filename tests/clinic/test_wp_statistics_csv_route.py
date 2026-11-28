"""CSV parity between the Flask download and a headless process with persisted grants."""

import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from flask import Flask
from flask_login import LoginManager

from src.clinic import guard, route_policy, routes_statistics, statistics_export
from src.clinic.identity import ClinicAccountPolicy
from src.clinic.statistics_access import grant_aggregate, revoke_aggregate
from src.database import db
from src.models import User

from test_wp_statistics_csv import stats_db, world  # noqa: F401  (shared fixtures)


PATH = "/api/clinic/v1/statistics/export.csv"
SCRIPT = Path(__file__).resolve().parents[2] / "scripts/clinic_statistics_export.py"


@pytest.fixture
def client(world, monkeypatch):
    session, _, _, _ = world
    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="synthetic-session-key",
                      SQLALCHEMY_DATABASE_URI=session.bind.url)
    db.init_app(app)
    LoginManager(app).user_loader(lambda uid: db.session.get(User, int(uid)))
    app.register_blueprint(routes_statistics.blueprint)
    monkeypatch.setattr(guard, "ROUTE_POLICY", route_policy._build())
    app.before_request(guard.clinic_guard)
    return app.test_client()


def _login(client, user):
    with client.session_transaction() as browser:
        browser["_user_id"] = str(user.id)


def _cli(session, actor, destination, *extra, **env):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--actor-id", str(actor.id), "--role", "statistics",
         "--output", str(destination), *extra],
        cwd=Path(session.bind.url.database).parent,
        env={"CLINIC_MODE": "true", "SPEAKR_SKIP_BACKGROUND_TASKS": "true",
             "SQLALCHEMY_DATABASE_URI": str(session.bind.url), **env},
        stdin=subprocess.DEVNULL, capture_output=True, timeout=30,
    )


def test_granted_download_matches_headless_cli_byte_for_byte(world, client, tmp_path):
    session, actor, author, _ = world
    grant = grant_aggregate(actor.id, "author", author_ids=[author.id], session=session)
    _login(client, actor)
    result = client.get(PATH, query_string={"from": "2026-03-01", "to": "2026-04-01",
                                           "group_by": "author", "expected_version": grant.version})
    assert result.status_code == 200
    assert result.content_type == "text/csv; charset=utf-8"
    assert result.headers["Content-Disposition"] == 'attachment; filename="statistics.csv"'
    assert result.headers["Cache-Control"] == "no-store"
    assert result.headers["X-Grant-Version"] == str(grant.version)

    out = tmp_path / "cli.csv"
    run = _cli(session, actor, out, "--start", "2026-03-01", "--end", "2026-04-01",
               "--group-by", "author", "--expected-version", str(grant.version))
    assert run.returncode == 0, run.stderr
    assert run.stdout == b""
    assert out.read_bytes() == result.data == (
        "author_user_id,count,total_seconds,average_seconds,known_duration_count,incomplete\r\n"
        f"{author.id},2,30.0,15.0,2,false\r\n"
    ).encode()


def test_download_requires_active_session_and_current_grant(world, client, tmp_path, monkeypatch):
    session, actor, author, _ = world
    out = tmp_path / "refused.csv"
    assert client.get(PATH).status_code == 401
    _login(client, actor)
    assert client.get(PATH).status_code == 403
    assert _cli(session, actor, out).returncode == 3

    grant = grant_aggregate(actor.id, "author", author_ids=[author.id], session=session)
    assert client.get(PATH + "?expected_version=0").status_code == 409
    assert _cli(session, actor, out, "--expected-version", "0").returncode == 3
    account = session.get(ClinicAccountPolicy, actor.id)
    account.status = "disabled"
    session.commit()
    assert client.get(PATH).status_code == 403
    assert _cli(session, actor, out).returncode == 3
    account.status = "active"
    session.commit()
    assert revoke_aggregate(actor.id, grant.id, session=session) == grant.version + 1
    assert client.get(PATH).status_code == 403

    monkeypatch.setenv("CLINIC_MODE", "false")
    assert client.get(PATH).status_code == 404
    assert _cli(session, actor, out, CLINIC_MODE="false").returncode == 3
    assert not out.exists()


def test_download_and_cli_reject_bad_queries_and_write_failures(world, client, tmp_path):
    session, actor, author, _ = world
    grant_aggregate(actor.id, "author", author_ids=[author.id], session=session)
    _login(client, actor)
    for query in ("from=2026-02-30", "from=0001-01-01", "group_by=bogus",
                  "expected_version=-1", "actor_id=3", "role=admin"):
        response = client.get(f"{PATH}?{query}")
        assert response.status_code == 400, query
        assert response.mimetype == "application/problem+json"

    out = tmp_path / "cli.csv"
    for extra in (("--start", "2026-02-30"), ("--start", "0001-01-01"),
                  ("--group-by", "bogus"), ("--expected-version", "-1")):
        assert _cli(session, actor, out, *extra).returncode == 2
    assert _cli(session, actor, tmp_path / "missing" / "cli.csv").returncode == 4
    assert _cli(session, actor, out, SQLALCHEMY_DATABASE_URI="invalid").returncode == 4
    empty_db = tmp_path / "unprovisioned.db"
    empty_db.touch()
    assert _cli(session, actor, out, SQLALCHEMY_DATABASE_URI=f"sqlite:///{empty_db}").returncode == 4
    assert empty_db.stat().st_size == 0
    assert not out.exists()


def test_both_exports_escape_formula_cells(world, client, tmp_path, monkeypatch):
    session, actor, author, _ = world
    grant_aggregate(actor.id, "author", author_ids=[author.id], session=session)
    _login(client, actor)
    original_read = routes_statistics.read_aggregates

    def read_with_formula(*args, **kwargs):
        result = original_read(*args, **kwargs)
        return replace(result, buckets=(replace(result.buckets[0], status="  =1+1"),))

    monkeypatch.setattr(routes_statistics, "read_aggregates", read_with_formula)
    monkeypatch.setattr(statistics_export, "read_aggregates", read_with_formula)
    out = tmp_path / "formula.csv"
    assert statistics_export.main(["--actor-id", str(actor.id), "--role", "statistics",
                                   "--output", str(out), "--group-by", "status"], session=session) == 0
    response = client.get(PATH + "?group_by=status")
    assert response.status_code == 200
    assert response.data == out.read_bytes()
    assert response.data.splitlines()[1].startswith(b"'  =1+1,")


def test_cli_does_not_write_if_grant_changes_during_export(world, tmp_path, monkeypatch):
    session, actor, author, _ = world
    grant = grant_aggregate(actor.id, "author", author_ids=[author.id], session=session)
    original_render = statistics_export.render_csv

    def render_and_revoke(result):
        revoke_aggregate(actor.id, grant.id, session=session)
        return original_render(result)

    monkeypatch.setattr(statistics_export, "render_csv", render_and_revoke)
    out = tmp_path / "revoked.csv"
    assert statistics_export.main(["--actor-id", str(actor.id), "--role", "statistics",
                                   "--output", str(out)], session=session) == 3
    assert not out.exists()
