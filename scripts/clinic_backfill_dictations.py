#!/usr/bin/env python3
"""Operator console: take over legacy recordings into clinic dictations, once.

Runs inside the app container. Default is a dry run that lists one line per candidate
(recording id, owner id, action, reason); --apply creates the dictations and prints their
ids. A second --apply creates nothing. Source recordings and files are never changed.
Output carries ids, counts and reasons only.

Exit codes: 0 done, 2 clinic mode is off, 1 other error.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="list candidates only (default)")
    mode.add_argument("--apply", action="store_true", help="create the dictations")
    args = parser.parse_args(argv)

    from src.app import app
    from src.clinic import legacy_backfill
    from src.clinic.config import clinic_mode_enabled

    with app.app_context():
        if not clinic_mode_enabled():
            print("Error: clinic mode is off (CLINIC_MODE).", file=sys.stderr)
            return 2
        rows = legacy_backfill.apply() if args.apply else legacy_backfill.plan()
        for row in rows:
            print(legacy_backfill.format_row(row))
        created = [row.dictation_id for row in rows if row.dictation_id]
        if args.apply:
            print("created dictations: " + (", ".join(created) if created else "none"))
        else:
            planned = sum(1 for row in rows if row.action == legacy_backfill.CREATE)
            print(f"dry run: {planned} to create, {len(rows) - planned} skipped; nothing changed")
    return 0


if __name__ == "__main__":
    sys.exit(run())
