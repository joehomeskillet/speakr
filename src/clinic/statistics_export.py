"""Scoped CSV export of the statistics aggregate and its server command.

The numbers come from ``statistics_access.read_aggregates``, the same scoped
query the statistics UI uses. This module only renders the result and writes
it. Cells carry ids, counts and seconds; no transcript or audio content.

Rendering is deterministic UTF-8 (no BOM, CRLF row ends, bucket order as the
service returns it). A text cell that a spreadsheet could read as a formula
(leading ``=``, ``+``, ``-``, ``@``, tab, carriage return or newline, also
after spaces) gets a leading apostrophe.

The file is written to a temporary name beside the target and moved into
place, so a failed write leaves neither a partial file nor a changed target.

Command exit codes: 0 done, 2 invalid arguments, 3 refused (role, scope,
stale version, clinic mode off), 4 query or write failed.
"""

from __future__ import annotations

import argparse
import csv
import io
import logging
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.clinic.config import clinic_mode_enabled
from src.clinic.identity import ClinicAccountPolicy
from src.clinic.statistics import ZONE, StatisticsError, StatisticsQuery, StatisticsResult
from src.clinic.statistics_access import StatisticsAccessError, read_aggregates, version_of

logger = logging.getLogger("speakr.clinic.statistics_export")

EXIT_OK = 0
EXIT_INVALID = 2
EXIT_REFUSED = 3
EXIT_FAILED = 4

_FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r", "\n")
_METRICS = ("count", "total_seconds", "average_seconds", "known_duration_count", "incomplete")


class StatisticsExportError(Exception):
    """The export cannot complete. ``code`` is a stable token."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ExportReceipt:
    """What was written. ``audit`` holds the parameters worth logging."""

    path: Path
    rows: int
    size: int
    audit: dict


def csv_cell(value) -> str:
    """One cell as text, safe against spreadsheet formula injection."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    text = str(value)
    if text.lstrip(" ").startswith(_FORMULA_LEAD):
        return "'" + text
    return text


def render_csv(result: StatisticsResult) -> bytes:
    """Deterministic UTF-8 CSV for one aggregate result."""
    first = result.buckets[0].as_dict(result.group_by) if result.buckets else None
    if first is not None:
        header = list(first)
    else:
        header = [_COLUMN[name] for name in result.group_by] + list(_METRICS)
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow([csv_cell(name) for name in header])
    for bucket in result.buckets:
        body = bucket.as_dict(result.group_by)
        writer.writerow([csv_cell(body[name]) for name in header])
    return out.getvalue().encode("utf-8")


_COLUMN = {
    "status": "status",
    "author": "author_user_id",
    "typist": "typist_user_id",
    "period": "period_start",
}


def export_csv(
    session,
    actor_id: int,
    query: StatisticsQuery,
    destination,
    *,
    role: str,
    expected_version: int | None = None,
) -> ExportReceipt:
    """Run the scoped query and write the CSV. Raises before anything is written."""
    if not clinic_mode_enabled():
        raise StatisticsAccessError("clinic_mode_off")
    account = session.get(ClinicAccountPolicy, actor_id)
    if account is None or account.status != "active":
        raise StatisticsAccessError("inactive_account")
    if expected_version is None:
        expected_version = version_of(actor_id, session=session)
    result = read_aggregates(session, actor_id, query, role=role, expected_version=expected_version)
    data = render_csv(result)
    if version_of(actor_id, session=session) != expected_version:
        raise StatisticsAccessError("stale_version")
    path = Path(destination)
    _write_atomic(path, data)
    audit = {
        "actor_user_id": actor_id,
        "role": role,
        "time_basis": result.time_basis,
        "start": result.start.isoformat() if result.start else None,
        "end": result.end.isoformat() if result.end else None,
        "grain": result.grain,
        "group_by": list(result.group_by),
        "rows": len(result.buckets),
        "destination": path.name,
    }
    return ExportReceipt(path=path, rows=len(result.buckets), size=len(data), audit=audit)


def _write_atomic(path: Path, data: bytes) -> None:
    tmp_name = None
    try:
        fd, tmp_name = tempfile.mkstemp(prefix=".stats-", suffix=".tmp", dir=path.parent)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
        tmp_name = None
    except OSError:
        raise StatisticsExportError("write_failed") from None
    finally:
        if tmp_name is not None:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass


def _parse_bound(text: str | None, name: str) -> datetime | None:
    """``YYYY-MM-DD`` is local midnight in Europe/Zurich; a full ISO value needs an offset."""
    if text is None:
        return None
    try:
        if len(text) == 10:
            day = date.fromisoformat(text)
            value = datetime(day.year, day.month, day.day, tzinfo=ZONE)
        else:
            value = datetime.fromisoformat(text)
        if value.utcoffset() is None:
            raise ValueError
        return value.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise StatisticsExportError(f"invalid_{name}") from None


def parse_query(
    *, time_basis: str, start: str | None, end: str | None, grain: str | None, group_by: str,
) -> StatisticsQuery:
    """Normalize HTTP and CLI report parameters before the scoped query."""
    return StatisticsQuery(
        time_basis=time_basis,
        start=_parse_bound(start, "start"),
        end=_parse_bound(end, "end"),
        grain=grain or None,
        group_by=tuple(name for name in group_by.split(",") if name),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="clinic-statistics-export",
        description="Statistik als CSV ausgeben (Serverkonsole).",
        epilog="Beispiel: --actor-id 7 --role statistics --start 2026-01-01 "
        "--end 2026-02-01 --group-by status --output /var/tmp/stat.csv",
    )
    parser.add_argument("--actor-id", type=int, required=True)
    parser.add_argument("--role", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--time-basis", default="created")
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--grain")
    parser.add_argument("--group-by", default="")
    parser.add_argument("--expected-version", type=int)
    return parser


def main(argv=None, *, session=None, err=None) -> int:
    """Server entry point. Returns the exit status; never prints data rows."""
    err = err if err is not None else sys.stderr
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exc:
        return exc.code
    try:
        if not 0 < args.actor_id <= 2**31 - 1:
            raise StatisticsExportError("user_id")
        if args.expected_version is not None and args.expected_version < 0:
            raise StatisticsExportError("version")
        query = parse_query(
            time_basis=args.time_basis,
            start=args.start,
            end=args.end,
            grain=args.grain,
            group_by=args.group_by,
        )
    except StatisticsExportError as exc:
        print(exc.code, file=err)
        return EXIT_INVALID
    if session is not None:
        return _run(session, args, query, err)
    if not clinic_mode_enabled():
        print("clinic_mode_off", file=err)
        return EXIT_REFUSED
    try:
        url = make_url(os.environ.get("SQLALCHEMY_DATABASE_URI", ""))
        if (url.get_backend_name() == "sqlite" and not url.query.get("uri")
                and not Path(url.database or "").is_file()):
            raise StatisticsExportError("query_failed")
        engine = create_engine(url)
        try:
            with Session(engine) as database_session:
                return _run(database_session, args, query, err)
        finally:
            engine.dispose()
    except (SQLAlchemyError, StatisticsExportError, ImportError, OSError, ValueError):
        print("query_failed", file=err)
        return EXIT_FAILED


def _run(session, args, query, err) -> int:
    params = {"actor_user_id": args.actor_id, "role": args.role, "output": Path(args.output).name}
    try:
        receipt = export_csv(
            session,
            args.actor_id,
            query,
            args.output,
            role=args.role,
            expected_version=args.expected_version,
        )
    except StatisticsError as exc:
        return _fail(err, params, exc.code, EXIT_INVALID)
    except StatisticsAccessError as exc:
        return _fail(err, params, exc.code, EXIT_REFUSED)
    except StatisticsExportError as exc:
        return _fail(err, params, exc.code, EXIT_FAILED)
    except (SQLAlchemyError, OverflowError, ValueError):
        return _fail(err, params, "query_failed", EXIT_FAILED)
    logger.info("statistics export done %s", receipt.audit)
    return EXIT_OK


def _fail(err, params: dict, code: str, status: int) -> int:
    logger.warning("statistics export refused %s code=%s", params, code)
    print(code, file=err)
    return status
