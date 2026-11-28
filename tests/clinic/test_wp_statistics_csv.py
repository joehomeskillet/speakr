"""Scoped statistics CSV export and server command. Synthetic rows, real SQLite file."""

import logging
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.app import app
from src.clinic.identity import ClinicAccountPolicy
from src.clinic.models import ClinicDictation
from src.clinic.statistics import StatisticsQuery, summarize
from src.clinic.statistics_access import grant_aggregate, revoke_aggregate
from src.clinic.statistics_export import (
    EXIT_FAILED,
    EXIT_INVALID,
    EXIT_OK,
    EXIT_REFUSED,
    StatisticsExportError,
    csv_cell,
    export_csv,
    main,
    render_csv,
)
from src.database import db
from src.models import Recording, User

WHEN = datetime(2026, 3, 2, 8, tzinfo=timezone.utc)


@pytest.fixture
def stats_db(tmp_path, monkeypatch):
    monkeypatch.setenv("CLINIC_MODE", "1")
    engine = create_engine("sqlite:///" + (tmp_path / "statistics-csv.db").as_posix())
    with app.app_context():
        db.metadata.create_all(bind=engine)
        session = sessionmaker(bind=engine)()
        try:
            yield session
        finally:
            session.close()
            engine.dispose()


def _user(session, name):
    user = User(username=name, email=f"{name}@example.invalid", password="x")
    session.add(user)
    session.flush()
    return user


def _dictation(session, author, seconds):
    recording = Recording(user_id=author.id, title="d", status="PENDING", audio_duration_seconds=seconds)
    session.add(recording)
    session.flush()
    session.add(ClinicDictation(recording_id=recording.id, author_user_id=author.id, created_at=WHEN))
    session.flush()


@pytest.fixture
def world(stats_db):
    stats = _user(stats_db, "stats")
    stats_db.add(ClinicAccountPolicy(user_id=stats.id, status="active"))
    a = _user(stats_db, "authora")
    b = _user(stats_db, "authorb")
    _dictation(stats_db, a, 10)
    _dictation(stats_db, a, 20)
    _dictation(stats_db, b, 7)
    stats_db.commit()
    return stats_db, stats, a, b


def _args(out, stats, *extra):
    return ["--actor-id", str(stats.id), "--role", "statistics", "--output", str(out), *extra]


def test_cell_guards_formula_injection():
    for lead in ("=1+1", "+1", "-1", "@SUM(A1)", "\tx", "\rx", "  =1"):
        assert csv_cell(lead) == "'" + lead
    assert csv_cell("normal") == "normal"
    assert csv_cell(None) == ""
    assert csv_cell(True) == "true"
    assert csv_cell(3) == "3"


def test_csv_matches_scoped_aggregate_and_is_deterministic(world, tmp_path):
    session, stats, a, _b = world
    grant_aggregate(stats.id, "author", author_ids=[a.id], session=session)
    query = StatisticsQuery(group_by=("author",))
    out = tmp_path / "s.csv"
    receipt = export_csv(session, stats.id, query, out, role="statistics")
    data = out.read_bytes()
    assert data == (
        "author_user_id,count,total_seconds,average_seconds,known_duration_count,incomplete\r\n"
        f"{a.id},2,30.0,15.0,2,false\r\n"
    ).encode("utf-8")
    assert receipt.rows == 1 and receipt.size == len(data)
    assert receipt.audit["actor_user_id"] == stats.id and receipt.audit["group_by"] == ["author"]
    again = tmp_path / "t.csv"
    export_csv(session, stats.id, query, again, role="statistics")
    assert again.read_bytes() == data
    assert render_csv(summarize(session, query)) != data  # unscoped summary shows author B too


def test_empty_result_keeps_header(world, tmp_path):
    session, stats, a, _b = world
    grant_aggregate(stats.id, "author", author_ids=[a.id], session=session)
    out = tmp_path / "e.csv"
    query = StatisticsQuery(group_by=("status",), start=datetime(2030, 1, 1, tzinfo=timezone.utc))
    export_csv(session, stats.id, query, out, role="statistics")
    assert out.read_text(encoding="utf-8").splitlines()[0].startswith("status,count,")


def test_command_success_and_audit_log(world, tmp_path, caplog):
    session, stats, a, _b = world
    grant_aggregate(stats.id, "author", author_ids=[a.id], session=session)
    out = tmp_path / "c.csv"
    with caplog.at_level(logging.INFO, logger="speakr.clinic.statistics_export"):
        code = main(_args(out, stats, "--group-by", "status", "--start", "2026-01-01", "--end", "2026-12-31"), session=session)
    assert code == EXIT_OK
    assert out.read_text(encoding="utf-8").splitlines()[1].startswith("open,2,")
    assert "actor_user_id" in caplog.text and "2025-12-31T23:00:00+00:00" in caplog.text


@pytest.mark.parametrize(
    "extra",
    [
        ["--start", "2026-13-40"],
        ["--start", "2026-01-01T00:00:00"],
        ["--start", "2026-02-01", "--end", "2026-01-01"],
        ["--group-by", "bogus"],
        ["--time-basis", "nope"],
        ["--group-by", "period"],
    ],
)
def test_invalid_input_is_nonzero_and_writes_nothing(world, tmp_path, extra):
    session, stats, a, _b = world
    grant_aggregate(stats.id, "author", author_ids=[a.id], session=session)
    out = tmp_path / "x.csv"
    assert main(_args(out, stats, *extra), session=session) == EXIT_INVALID
    assert not out.exists()


def test_missing_required_argument_is_invalid(tmp_path):
    assert main(["--role", "statistics"], err=open(tmp_path / "e", "w")) == EXIT_INVALID


def test_unauthorized_scopes_are_refused(world, tmp_path):
    session, stats, a, _b = world
    out = tmp_path / "u.csv"
    assert main(_args(out, stats), session=session) == EXIT_REFUSED  # no grant
    grant_aggregate(stats.id, "author", author_ids=[a.id], session=session)
    bad_role = ["--actor-id", str(stats.id), "--role", "admin", "--output", str(out)]
    assert main(bad_role, session=session) == EXIT_REFUSED
    assert not out.exists()


def test_clinic_mode_off_refused(world, tmp_path, monkeypatch):
    session, stats, a, _b = world
    grant_aggregate(stats.id, "author", author_ids=[a.id], session=session)
    monkeypatch.setenv("CLINIC_MODE", "0")
    out = tmp_path / "o.csv"
    assert main(_args(out, stats), session=session) == EXIT_REFUSED
    assert not out.exists()


def test_stale_version_and_revoked_grant(world, tmp_path):
    session, stats, a, _b = world
    grant = grant_aggregate(stats.id, "author", author_ids=[a.id], session=session)
    out = tmp_path / "v.csv"
    assert main(_args(out, stats, "--expected-version", "0"), session=session) == EXIT_REFUSED
    assert not out.exists()
    assert main(_args(out, stats, "--expected-version", str(grant.version)), session=session) == EXIT_OK
    out.unlink()
    revoke_aggregate(stats.id, grant.id, session=session)
    assert main(_args(out, stats), session=session) == EXIT_REFUSED
    assert not out.exists()


def test_write_failure_is_nonzero_and_leaves_no_partial_file(world, tmp_path, monkeypatch):
    session, stats, a, _b = world
    grant_aggregate(stats.id, "author", author_ids=[a.id], session=session)
    missing = tmp_path / "no-dir" / "w.csv"
    assert main(_args(missing, stats), session=session) == EXIT_FAILED

    out = tmp_path / "keep.csv"
    out.write_bytes(b"old")
    monkeypatch.setattr("src.clinic.statistics_export.os.replace", _boom)
    assert main(_args(out, stats), session=session) == EXIT_FAILED
    assert out.read_bytes() == b"old"
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []


def _boom(*_a, **_k):
    raise OSError("disk full")


def test_write_failure_code(world, tmp_path, monkeypatch):
    session, stats, a, _b = world
    grant_aggregate(stats.id, "author", author_ids=[a.id], session=session)
    monkeypatch.setattr("src.clinic.statistics_export.os.replace", _boom)
    with pytest.raises(StatisticsExportError) as info:
        export_csv(session, stats.id, StatisticsQuery(), tmp_path / "z.csv", role="statistics")
    assert info.value.code == "write_failed"


def test_retry_after_failure_succeeds(world, tmp_path, monkeypatch):
    session, stats, a, _b = world
    grant_aggregate(stats.id, "author", author_ids=[a.id], session=session)
    out = tmp_path / "r.csv"
    with monkeypatch.context() as patch:
        patch.setattr("src.clinic.statistics_export.os.replace", _boom)
        assert main(_args(out, stats), session=session) == EXIT_FAILED
    assert main(_args(out, stats), session=session) == EXIT_OK
    assert out.read_text(encoding="utf-8").splitlines()[1].startswith("2,")
