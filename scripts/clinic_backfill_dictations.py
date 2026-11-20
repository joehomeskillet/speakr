#!/usr/bin/env python3
"""Operator console: take over legacy recordings into clinic dictations, once.

Runs inside the app container. Default is a dry run that lists one line per candidate
(recording id, owner id, action, reason); --apply creates the dictations and prints their
ids. A second --apply creates nothing. Source recordings and files are never changed.
Use --init-audio to repair missing audio revisions for existing dictations.
Output carries ids, counts and reasons only.

Exit codes: 0 done, 2 clinic mode is off, 1 other error.
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _run_audio_repair(apply):
    from dotenv import load_dotenv
    from flask import Flask
    from sqlalchemy.engine import make_url
    from src.clinic.config import clinic_mode_enabled

    load_dotenv()
    if not clinic_mode_enabled():
        print("Error: clinic mode is off (CLINIC_MODE).", file=sys.stderr)
        return 2

    from src.clinic import legacy_backfill
    from src.database import db

    # The full app runs migrations and the original-store sweep on import.
    app = Flask("src.app")
    url = make_url(os.environ.get("SQLALCHEMY_DATABASE_URI", "sqlite:////data/instance/transcriptions.db"))
    if url.get_backend_name() == "sqlite":
        if not url.database or url.database == ":memory:":
            print("Error: audio repair requires an existing database file.", file=sys.stderr)
            return 1
        if not url.database.startswith("file:"):
            path = Path(app.instance_path, url.database).resolve()
            url = url.set(database=path.as_uri())
        url = url.update_query_dict({"uri": "true", "mode": "rw" if apply else "ro"})
    app.config.update(SQLALCHEMY_DATABASE_URI=url, UPLOAD_FOLDER=os.environ.get("UPLOAD_FOLDER", "/data/uploads"))
    db.init_app(app)
    with app.app_context():
        failed = False
        for dictation_id, action, reason in legacy_backfill.init_missing_audio(apply=apply):
            print(f"dictation={dictation_id} action={action} reason={reason}")
            failed |= action == "error"
    return int(failed)


def run(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--init-audio", action="store_true", help="repair missing initial audio revisions")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="list candidates only (default)")
    mode.add_argument("--apply", action="store_true", help="write the planned changes")
    args = parser.parse_args(argv)
    if args.init_audio:
        return _run_audio_repair(args.apply)

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
