"""Upstream UI lockdown (v8-06): no entry whose backing route the route policy disables.

In CLINIC_MODE the account and admin pages render no tab, link or panel for a feature that
src/clinic/route_policy.py lists under DISABLED. The hidden entries are declared here as
(page, feature, markers); the test derives the policy side from ROUTE_POLICY, so an entry
cannot name a feature the policy no longer disables. With CLINIC_MODE off the upstream
markup is unchanged. The inline clinic script moved into static/js/clinic/admin-clinic-tab.js;
both pages only import and call it.

The real application renders the real templates in a child process (throwaway SQLite
database, synthetic users, no patient data); no browser is needed.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from src.clinic import route_policy

ROOT = Path(__file__).resolve().parents[2]

# page -> hidden entry -> (route_policy feature, markers that must be absent in clinic mode)
HIDDEN = {
    "/account": {
        "Prompts tab": ("generative_summary", ['id="tab-prompts"', 'id="content-prompts"']),
        "Speakers tab": ("speaker_identification", ['id="tab-speakers"', 'id="content-speakers"']),
        "API tokens tab": ("personal_api_tokens", ['id="tab-tokens"', 'id="content-tokens"']),
        "Webhooks tab": ("user_webhooks", ['id="tab-webhooks"', 'id="content-webhooks"', "account-webhooks.js"]),
    },
    "/admin": {
        "Default prompts tab": ("generative_summary", ["activeTab = 'prompts'", "activeTab !== 'prompts'"]),
        "Groups tab": ("internal_sharing", ["activeTab = 'groups'", "activeTab !== 'groups'"]),
        "Vector store tab": ("inquire", ["activeTab = 'vectorstore'", "activeTab !== 'vectorstore'"]),
    },
}

HARNESS = r'''
# The account view does not pass clinic_mode to its template yet (src/api/auth.py is not owned by this package):
# this context processor stands in for that one-line wiring so the template is rendered as it will be.
from src.clinic.config import clinic_mode_enabled
app.context_processor(lambda: {"clinic_mode": clinic_mode_enabled()})
'''

RENDER = r'''
import json, sys
from src.app import app, db
from src.init_db import initialize_database
from src.models import User

#HARNESS#
with app.app_context():
    initialize_database(app)
    admin = User(username="v806a", email="v806a@local.test", password="x", is_admin=True)
    db.session.add(admin); db.session.commit()
    uid = admin.id
out = {}
for path in ("/account", "/admin"):
    client = app.test_client()
    with client.session_transaction() as s:
        s["_user_id"] = str(uid); s["_fresh"] = True
    resp = client.get(path)
    out[path] = [resp.status_code, resp.get_data(as_text=True)]
sys.stdout.write("BODY:" + json.dumps(out))
'''.replace("#HARNESS#", HARNESS)


def _render(tmp_path: Path, clinic: bool) -> dict:
    env = {**os.environ, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'v806.db'}",
           "UPLOAD_FOLDER": str(tmp_path / "uploads"), "RATELIMIT_ENABLED": "false",
           "DISABLE_VOICE_EMBEDDING_CHECK": "true", "ENABLE_AUTO_PROCESSING": "false"}
    env.pop("CLINIC_MODE", None)
    if clinic:
        env["CLINIC_MODE"] = "1"
    done = subprocess.run([sys.executable, "-c", RENDER], cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
    assert "BODY:" in done.stdout, done.stderr[-3000:]
    pages = json.loads(done.stdout.split("BODY:", 1)[1])
    for path, (status, _) in pages.items():
        assert status == 200, (path, status)
    return {path: body for path, (_, body) in pages.items()}


@pytest.fixture(scope="module")
def clinic_pages(tmp_path_factory):
    return _render(tmp_path_factory.mktemp("v806on"), True)


@pytest.fixture(scope="module")
def upstream_pages(tmp_path_factory):
    return _render(tmp_path_factory.mktemp("v806off"), False)


def _entries():
    return [(page, entry, feature, markers) for page, items in HIDDEN.items()
            for entry, (feature, markers) in items.items()]


@pytest.mark.parametrize("page,entry,feature,markers", _entries(), ids=lambda v: v if isinstance(v, str) else None)
def test_hidden_entry_is_backed_by_a_disabled_route(page, entry, feature, markers):
    backing = [name for name, rule in route_policy.ROUTE_POLICY.items()
               if rule.kind == route_policy.DISABLED and rule.feature == feature]
    assert backing, f"{entry}: route_policy disables no route for feature {feature}"


@pytest.mark.parametrize("page,entry,feature,markers", _entries(), ids=lambda v: v if isinstance(v, str) else None)
def test_clinic_mode_renders_no_disabled_entry(clinic_pages, page, entry, feature, markers):
    for marker in markers:
        assert marker not in clinic_pages[page], f"{page}: {entry} still rendered ({marker})"


@pytest.mark.parametrize("page,entry,feature,markers", _entries(), ids=lambda v: v if isinstance(v, str) else None)
def test_clinic_mode_off_renders_every_entry_as_before(upstream_pages, page, entry, feature, markers):
    for marker in markers:
        if marker.startswith("activeTab") and "vectorstore" in marker:
            continue  # the stock vector store tab shows only with inquire mode on
        assert marker in upstream_pages[page], f"{page}: {entry} missing without clinic mode ({marker})"


def test_clinic_mode_keeps_the_functional_entries(clinic_pages, upstream_pages):
    for marker in ('id="tab-account"', 'id="tab-preferences"', 'id="tab-organize"', 'id="tab-templates"', 'id="tab-about"'):
        assert marker in clinic_pages["/account"], marker
    assert 'data-testid="admin-tab-clinic"' in clinic_pages["/admin"]
    assert "admin-tab-clinic" not in upstream_pages["/admin"]


def test_clinic_pages_import_the_module_once(clinic_pages, upstream_pages):
    assert clinic_pages["/admin"].count("/static/js/clinic/admin-clinic-tab.js") == 1
    assert "/static/js/clinic/" not in upstream_pages["/admin"] and "/static/js/clinic/" not in upstream_pages["/account"]


def test_no_inline_clinic_script_is_left_in_the_pages():
    for name in ("admin.html", "group-admin.html"):
        src = (ROOT / "templates" / name).read_text(encoding="utf-8")
        assert "createApp(Root)" not in src and "createClinicApiClient" not in src, name
        assert "ClinicGrantsHost" not in src and "ClinicRegexHost" not in src, name
        assert len(re.findall(r"import \{ mountClinicAdmin \} from", src)) == 1, name
        assert src.count("mountClinicAdmin();") == 1, name
    # one module, with the behaviour the pages used to carry inline
    module = (ROOT / "static/js/clinic/admin-clinic-tab.js").read_text(encoding="utf-8")
    assert module.count("createClinicApiClient()") == 1 and module.count("Vue.createApp(Root)") == 1
