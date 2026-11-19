"""Guard: no authorisation helper may rely on "the user exists" alone."""

import ast
from pathlib import Path

CLINIC = Path(__file__).resolve().parents[2] / "src" / "clinic"
PREFIXES = ("_may_", "_can_", "_actor", "_authori")
EVIDENCE_CALLS = {"decide"}
EVIDENCE_NAMES = {"is_admin"}


def _has_authorisation_evidence(func):
    for node in ast.walk(func):
        if isinstance(node, ast.Attribute) and node.attr in EVIDENCE_NAMES:
            return True
        if isinstance(node, ast.Call):
            target = node.func
            name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
            if name in EVIDENCE_CALLS or name.startswith("may_"):
                return True
    return False


def _checks_user_existence(func):
    for node in ast.walk(func):
        if isinstance(node, ast.Call):
            target = node.func
            if isinstance(target, ast.Attribute) and target.attr == "get" and node.args:
                first = node.args[0]
                if isinstance(first, ast.Name) and first.id == "User":
                    return True
    return False


def existence_only_helpers(source, label="<src>"):
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(PREFIXES):
            if _checks_user_existence(node) and not _has_authorisation_evidence(node):
                found.append(f"{label}:{node.lineno} {node.name}")
    return found


def test_no_existence_only_authorisation_helpers():
    offenders = []
    for path in sorted(CLINIC.glob("*.py")):
        offenders += existence_only_helpers(path.read_text(encoding="utf-8"), path.name)
    assert not offenders, "existence-only authorisation helpers: " + ", ".join(offenders)


def test_scanner_detects_the_old_pattern():
    old = "def _actor(a):\n    if db.session.get(User, a) is None:\n        raise X\n    return a\n"
    assert existence_only_helpers(old)
    fixed = old + "def _may_x(a):\n    u = db.session.get(User, a)\n    return u.is_admin\n"
    assert len(existence_only_helpers(fixed)) == 1
