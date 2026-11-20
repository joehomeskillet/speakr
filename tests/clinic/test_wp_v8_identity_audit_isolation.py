"""Exercise the audit-off test and its clinic successor in the same pytest worker."""

import os
from pathlib import Path
import subprocess
import sys


def test_identity_audit_after_upstream_audit_off():
    result = subprocess.run(
        [
            sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--tb=line",
            "tests/test_audit_log_auth.py::test_nothing_is_recorded_while_the_log_is_off",
            "tests/clinic/test_wp_identity_review_fixes.py::test_change_password_counts_audits_and_respects_lock",
        ],
        cwd=Path(__file__).resolve().parents[2],
        # Root conftest creates a fresh SQLite file. Never reuse the parent worker's DB.
        env={**os.environ, "PYTEST_DATABASE_URI": "", "PYTEST_ADDOPTS": "",
             "CLINIC_MODE": "0", "AUDIT_LOG_ENABLED": "true"},
        capture_output=True, text=True, timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed" in result.stdout
