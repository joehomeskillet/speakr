"""Startup sweep over the immutable original store.

Three cases are told apart:

* ``published_leftover``: a ``.partial-`` name that is a hard link of a final
  file (a crash between ``os.link`` and the spool cleanup in ``ingest.py``).
  The bytes stay reachable through the final name, so the extra name is
  removed. This is the only mutation the sweep makes.
* ``incomplete``: a ``.partial-`` file with no final file of the same inode
  and older than ``stale_after``. It is only reported, never deleted: it may
  hold the only copy of a dictation.
* ``awaiting_row``: a final file with no ``ClinicOriginal`` row. This is the
  legitimate healing case of ``IngestService.store_original`` (a database
  failure or a missing author). It is only reported.

The sweep is idempotent. A second run finds no ``published_leftover``. No
route and no app-start call is registered here.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from src.clinic.models import ClinicOriginal

logger = logging.getLogger(__name__)

PARTIAL_PREFIX = '.partial-'
ORIGINALS_DIR = 'clinic/originals/v1'
DEFAULT_STALE_AFTER = timedelta(hours=1)


@dataclass(frozen=True)
class SweepEntry:
    """One file seen by the sweep. ``path`` is relative to the store root."""

    path: str
    size: int
    age_seconds: float


@dataclass
class SweepReport:
    """Counts and entries per category, for a log line or an admin view."""

    removed_leftovers: list[SweepEntry] = field(default_factory=list)
    incomplete: list[SweepEntry] = field(default_factory=list)
    awaiting_row: list[SweepEntry] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    scanned: int = 0

    @property
    def counts(self) -> dict[str, int]:
        return {
            'scanned': self.scanned,
            'removed_leftovers': len(self.removed_leftovers),
            'incomplete': len(self.incomplete),
            'awaiting_row': len(self.awaiting_row),
            'errors': len(self.errors),
        }


def sweep_originals(
    root: str | Path,
    *,
    stale_after: timedelta = DEFAULT_STALE_AFTER,
    now: float | None = None,
) -> SweepReport:
    """Scan ``root`` and return the report. Needs an app context for the row lookup."""
    root_path = Path(root).resolve()
    report = SweepReport()
    directory = root_path / ORIGINALS_DIR
    if not directory.is_dir():
        return report
    moment = time.time() if now is None else now

    finals: dict[tuple[int, int], Path] = {}
    partials: list[tuple[Path, os.stat_result]] = []
    for entry in sorted(directory.iterdir(), key=lambda p: p.name):
        try:
            info = entry.lstat()
        except OSError as exc:
            report.errors.append(f'{entry.name}: {exc}')
            continue
        if entry.is_symlink() or not entry.is_file():
            continue
        report.scanned += 1
        if entry.name.startswith(PARTIAL_PREFIX):
            partials.append((entry, info))
        else:
            finals[(info.st_dev, info.st_ino)] = entry

    for path, info in partials:
        if (info.st_dev, info.st_ino) in finals:
            _remove_leftover(path, info, root_path, moment, report)
        elif moment - info.st_mtime >= stale_after.total_seconds():
            report.incomplete.append(_entry(path, info, root_path, moment))

    known = {
        key for (key,) in ClinicOriginal.query.with_entities(ClinicOriginal.storage_key).all()
    }
    for path in finals.values():
        key = path.relative_to(root_path).as_posix()
        if key not in known:
            report.awaiting_row.append(_entry(path, path.lstat(), root_path, moment))

    report.awaiting_row.sort(key=lambda e: e.path)
    _log(report)
    return report


def _remove_leftover(path, info, root, moment, report) -> None:
    entry = _entry(path, info, root, moment)
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError as exc:
        report.errors.append(f'{path.name}: {exc}')
        return
    report.removed_leftovers.append(entry)


def _entry(path: Path, info: os.stat_result, root: Path, moment: float) -> SweepEntry:
    return SweepEntry(
        path=path.relative_to(root).as_posix(),
        size=info.st_size,
        age_seconds=max(0.0, moment - info.st_mtime),
    )


def _log(report: SweepReport) -> None:
    counts = report.counts
    level = logging.WARNING if counts['incomplete'] or counts['errors'] else logging.INFO
    logger.log(level, 'clinic ingest sweep: %s', counts)
