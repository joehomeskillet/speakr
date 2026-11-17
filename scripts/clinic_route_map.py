#!/usr/bin/env python3
"""Build the Flask app as conftest.py does, walk url_map, and export route-map.json.

Output format: sorted list of {rule, methods, endpoint, blueprint}.
Runs offline with a temporary SQLite database.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path


def main() -> int:
    temp_dir = tempfile.mkdtemp(prefix="speakr_route_map_")
    try:
        temp_db_path = os.path.join(temp_dir, "test.db")
        os.environ["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{temp_db_path}"
        os.environ["UPLOAD_FOLDER"] = os.path.join(temp_dir, "uploads")

        # Disable external services identically to conftest.py
        for var in (
            "TEXT_MODEL_BASE_URL", "TEXT_MODEL_NAME",
            "EMBEDDING_BASE_URL", "EMBEDDING_API_KEY", "EMBEDDING_MODEL", "EMBEDDING_DIMENSIONS",
            "ASR_BASE_URL", "TRANSCRIPTION_MODEL", "TRANSCRIPTION_CONNECTOR",
            "SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD",
            "SSO_CLIENT_SECRET", "SSO_DISCOVERY_URL",
        ):
            os.environ[var] = ""

        os.environ["USE_ASR_ENDPOINT"] = "false"
        os.environ["TEXT_MODEL_API_KEY"] = "test-key"
        os.environ["TRANSCRIPTION_API_KEY"] = "test-key"
        os.environ["TRANSCRIPTION_BASE_URL"] = "https://api.openai.com/v1"
        os.environ.setdefault("SECRET_KEY", "pytest-secret-key")
        os.environ.setdefault("ENABLE_AUTO_PROCESSING", "false")
        os.environ["JOB_QUEUE_WORKERS"] = "0"
        os.environ["SUMMARY_QUEUE_WORKERS"] = "0"
        os.environ["INDEX_QUEUE_WORKERS"] = "0"
        os.environ["CONVERT_QUEUE_WORKERS"] = "0"
        os.environ["ENABLE_INQUIRE_AGENT"] = "false"
        os.environ["DISABLE_VOICE_EMBEDDING_CHECK"] = "true"
        os.environ.setdefault("RATELIMIT_ENABLED", "false")

        # Now safe to import app
        from src.app import app

        routes = []
        for rule in app.url_map.iter_rules():
            bp = rule.endpoint.split(".", 1)[0] if "." in rule.endpoint else None
            routes.append({
                "rule": rule.rule,
                "methods": sorted(rule.methods),
                "endpoint": rule.endpoint,
                "blueprint": bp,
            })

        # Sort routes by rule, endpoint, methods
        routes.sort(key=lambda r: (r["rule"], r["endpoint"], r["methods"]))

        output_path = Path(__file__).resolve().parent.parent / "clinic-spec" / "evidence" / "route-map.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(routes, f, indent=2, ensure_ascii=False)
            f.write("\n")

        print(f"Exported {len(routes)} routes to {output_path}")
        return 0
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
