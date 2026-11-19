"""Role source for the clinic frontend (x33).

The index page renders ``data-clinic-role`` on #app in clinic mode: ``admin`` for an
active site admin, ``typist`` for a staff-pool member, otherwise ``author``. The
value is a UI hint only; this file also checks that no server-side decision reads it.
Real Flask app in a child process, throwaway SQLite database, synthetic users only.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

RENDER = r'''
import json, sys
from src.app import app, db
from src.clinic import identity, pools
from src.init_db import initialize_database
from src.models import GroupMembership, User

def mk(name, admin=False, status="active"):
    u = User(username=name, email=name + "@local.test", password="x", is_admin=admin)
    db.session.add(u); db.session.flush()
    if status:
        db.session.add(identity.ClinicAccountPolicy(user_id=u.id, status=status, login_policy="local_only"))
    return u

with app.app_context():
    initialize_database(app)
    users = {
        "author": mk("author"),
        "admin": mk("admin", admin=True),
        "revoked": mk("revoked", admin=True, status="disabled"),
        "typist": mk("typist"),
        "both": mk("both"),
    }
    db.session.commit()
    staff = pools.create_staff_pool("Schreibdienst", actor_id=users["admin"].id)
    for key in ("typist", "both"):
        db.session.add(GroupMembership(group_id=staff.group_id, user_id=users[key].id, role="member"))
    pools.create_author_pool("Aerzte", actor_id=users["admin"].id)
    db.session.commit()
    ids = {k: u.id for k, u in users.items()}

out = {}
for key, uid in ids.items():
    c = app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(uid); s["_fresh"] = True
    out[key] = c.get("/").get_data(as_text=True)
with app.app_context():
    out["ui_role"] = {k: pools.ui_role(v) for k, v in ids.items()}
    out["ui_role"]["unknown"] = pools.ui_role(999999)
sys.stdout.write("BODY:" + json.dumps(out))
'''


def _render(tmp_path: Path, clinic: bool) -> dict:
    env = {**os.environ, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'x33.db'}",
           "UPLOAD_FOLDER": str(tmp_path / "uploads"), "RATELIMIT_ENABLED": "false",
           "DISABLE_VOICE_EMBEDDING_CHECK": "true", "ENABLE_AUTO_PROCESSING": "false"}
    env.pop("CLINIC_MODE", None)
    if clinic:
        env["CLINIC_MODE"] = "1"
    done = subprocess.run([sys.executable, "-c", RENDER], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=300)
    assert "BODY:" in done.stdout, done.stderr[-3000:]
    return json.loads(done.stdout.split("BODY:", 1)[1])


def _role(html: str):
    found = re.search(r'data-clinic-role="([^"]*)"', html)
    return found.group(1) if found else None


def test_clinic_mode_renders_role_per_user(tmp_path):
    out = _render(tmp_path, clinic=True)
    assert _role(out["author"]) == "author"
    assert _role(out["typist"]) == "typist"
    assert _role(out["admin"]) == "admin"
    assert _role(out["revoked"]) == "author"
    assert _role(out["both"]) == "typist"  # documented limitation: author and typist yields typist
    assert out["ui_role"]["unknown"] == "author"
    assert out["ui_role"]["admin"] == "admin"


def test_clinic_mode_off_has_no_role_attribute(tmp_path):
    out = _render(tmp_path, clinic=False)
    for key in ("author", "admin", "typist"):
        assert "data-clinic-role" not in out[key]


def test_role_is_never_a_server_side_input():
    # The hint is only passed to the template; no clinic module or route reads it back.
    src = ROOT / "src"
    hits = []
    for path in list(src.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if "ui_role" in text:
            hits.append(path.relative_to(ROOT).as_posix())
    assert sorted(hits) == ["src/api/recordings.py", "src/clinic/pools.py"]
    pools_text = (src / "clinic" / "pools.py").read_text(encoding="utf-8")
    assert pools_text.count("ui_role(") == 1  # defined, never called inside the policy code
