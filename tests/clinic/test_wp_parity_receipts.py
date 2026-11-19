"""Durable run_once receipts for the parity dispatcher (x22).

PAR-API-001. In-process cases use the shared test database; the multi-instance
cases start the real application in child processes against one database file.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.app import app, db
from src.clinic import registry
from src.clinic.parity_dispatch import CommandProblem
from src.clinic.parity_receipts import ClinicParityReceipt, run_once, scope_key

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def receipts():
    with app.app_context():
        db.session.query(ClinicParityReceipt).delete()
        db.session.commit()
        yield
        db.session.rollback()
        db.session.query(ClinicParityReceipt).delete()
        db.session.commit()


def test_model_is_registered_and_append_only(receipts):
    assert "parity_receipts" in registry.MODEL_MODULES
    run_once((1, "cmd", "t", "k"), "d1", lambda: {"ok": True})
    row = db.session.get(ClinicParityReceipt, scope_key((1, "cmd", "t", "k")))
    row.digest = "changed"
    with pytest.raises(RuntimeError, match="append-only"):
        db.session.flush()
    db.session.rollback()
    row = db.session.get(ClinicParityReceipt, scope_key((1, "cmd", "t", "k")))
    db.session.delete(row)
    with pytest.raises(RuntimeError, match="append-only"):
        db.session.flush()
    db.session.rollback()


def test_replay_returns_stored_result_without_running_again(receipts):
    calls = []

    def operation():
        calls.append(1)
        return {"n": len(calls)}

    scope = (7, "cmd", "target", "key-1")
    assert run_once(scope, "d", operation) == {"n": 1}
    assert run_once(scope, "d", operation) == {"n": 1}
    assert calls == [1]
    assert db.session.query(ClinicParityReceipt).count() == 1


def test_digest_mismatch_is_idempotency_conflict(receipts):
    scope = (7, "cmd", "target", "key-2")
    run_once(scope, "d1", lambda: {"v": 1})
    with pytest.raises(CommandProblem, match="IDEMPOTENCY_CONFLICT"):
        run_once(scope, "d2", lambda: {"v": 2})
    assert db.session.get(ClinicParityReceipt, scope_key(scope)).result == {"v": 1}


def test_scopes_are_independent(receipts):
    assert run_once((1, "c", "t", "k"), "d", lambda: {"a": 1}) == {"a": 1}
    assert run_once((2, "c", "t", "k"), "d", lambda: {"a": 2}) == {"a": 2}
    assert db.session.query(ClinicParityReceipt).count() == 2


def test_failing_operation_stores_nothing_and_rolls_back(receipts):
    scope = (9, "cmd", "target", "key-3")

    def operation():
        db.session.add(ClinicParityReceipt(scope_key="x" * 64, digest="side", result={}))
        raise ValueError("boom")

    with pytest.raises(ValueError):
        run_once(scope, "d", operation)
    assert db.session.query(ClinicParityReceipt).count() == 0
    # the same scope can run again after a failure
    assert run_once(scope, "d", lambda: {"ok": 1}) == {"ok": 1}


def test_concurrent_duplicate_loses_and_returns_winner(receipts):
    scope = (5, "cmd", "target", "key-4")

    def racing_operation():
        # Another worker commits the receipt while this one is still executing.
        with Session(db.engine) as other:
            other.add(ClinicParityReceipt(scope_key=scope_key(scope), digest="d", result={"winner": True}))
            other.commit()
        return {"winner": False}

    assert run_once(scope, "d", racing_operation) == {"winner": True}
    assert db.session.query(ClinicParityReceipt).count() == 1


def test_concurrent_duplicate_with_other_digest_conflicts(receipts):
    scope = (5, "cmd", "target", "key-5")

    def racing_operation():
        with Session(db.engine) as other:
            other.add(ClinicParityReceipt(scope_key=scope_key(scope), digest="other", result={}))
            other.commit()
        return {}

    with pytest.raises(CommandProblem, match="IDEMPOTENCY_CONFLICT"):
        run_once(scope, "d", racing_operation)


def test_unrelated_integrity_error_is_not_swallowed(receipts):
    from sqlalchemy.exc import IntegrityError

    def operation():
        raise IntegrityError("stmt", {}, Exception("fk"))

    with pytest.raises(IntegrityError):
        run_once((3, "cmd", "t", "k"), "d", operation)


SCRIPT = r'''
import json, sys
from src.app import app, db
from src.init_db import initialize_database
from src.clinic.parity_receipts import run_once
from src.clinic.parity_dispatch import CommandProblem

out = {"has_run_once": "clinic_run_once" in app.extensions}
if sys.argv[1] == "off":
    with app.app_context():
        initialize_database(app)
        tables = db.inspect(db.engine).get_table_names()
        out["receipt_rows"] = (db.session.execute(db.text("SELECT COUNT(*) FROM clinic_parity_receipt")).scalar()
                               if "clinic_parity_receipt" in tables else 0)
    print("RESULT:" + json.dumps(out)); sys.exit(0)

with app.app_context():
    initialize_database(app)
    ran = []
    scope = (1, "cmd", "target", "shared-key")
    out["result"] = run_once(scope, "d", lambda: ran.append(1) or {"by": sys.argv[2]})
    out["ran_here"] = bool(ran)
    try:
        run_once(scope, "other-digest", lambda: {})
        out["conflict"] = False
    except CommandProblem as error:
        out["conflict"] = str(error)
print("RESULT:" + json.dumps(out))
'''


def _run(mode: str, tmp_path: Path, tag: str = "") -> dict:
    env = {**os.environ, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'x22.db'}",
           "UPLOAD_FOLDER": str(tmp_path / "uploads"), "RATELIMIT_ENABLED": "false",
           "DISABLE_VOICE_EMBEDDING_CHECK": "true", "ENABLE_AUTO_PROCESSING": "false"}
    env.pop("CLINIC_MODE", None)
    if mode == "on":
        env["CLINIC_MODE"] = "1"
    done = subprocess.run([sys.executable, "-c", SCRIPT, mode, tag], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=300)
    lines = [line for line in done.stdout.splitlines() if line.startswith("RESULT:")]
    assert lines, done.stderr[-3000:]
    return json.loads(lines[-1][len("RESULT:"):])


def test_two_app_instances_share_one_receipt(tmp_path):
    first = _run("on", tmp_path, "first")
    second = _run("on", tmp_path, "second")
    assert first["has_run_once"] and second["has_run_once"]
    assert (first["ran_here"], first["result"]) == (True, {"by": "first"})
    assert (second["ran_here"], second["result"]) == (False, {"by": "first"})
    assert first["conflict"] == second["conflict"] == "IDEMPOTENCY_CONFLICT"


def test_clinic_mode_off_does_not_use_receipts(tmp_path):
    result = _run("off", tmp_path)
    assert result["has_run_once"] is False
    assert result["receipt_rows"] == 0


def test_table_exists_in_shared_database():
    with app.app_context():
        assert db.session.execute(text("SELECT COUNT(*) FROM clinic_parity_receipt")).scalar() >= 0
