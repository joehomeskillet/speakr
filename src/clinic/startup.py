"""Clinic work done once at app start, called from ``init_db.initialize_database``."""
from __future__ import annotations

import os

from src.clinic.config import clinic_mode_enabled
from src.clinic.ingest_recovery import sweep_originals


def originals_root(app) -> str:
    """Root of the durable original store: CLINIC_ORIGINALS_ROOT or the upload folder."""
    return os.environ.get('CLINIC_ORIGINALS_ROOT', '').strip() or app.config['UPLOAD_FOLDER']


def run_startup_sweep(app):
    """Sweep the original store in clinic mode and log the counts as one line.

    A failure is logged and never stops the app start. Returns the report, or
    None when clinic mode is off or the sweep failed.
    """
    if not clinic_mode_enabled():
        return None
    try:
        report = sweep_originals(originals_root(app))
    except Exception as exc:  # noqa: BLE001 - a start-up check must not block the start
        app.logger.error('Clinic original sweep failed: %s', exc)
        return None
    counts = report.counts
    app.logger.info(
        'Clinic original sweep: scanned=%d removed_leftovers=%d incomplete=%d awaiting_row=%d errors=%d',
        counts['scanned'], counts['removed_leftovers'], counts['incomplete'],
        counts['awaiting_row'], counts['errors'],
    )
    return report
