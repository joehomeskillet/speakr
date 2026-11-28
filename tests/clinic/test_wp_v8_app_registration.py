"""Registration of the V8 blueprints in the real app (int-b15).

The real application (src/app.py) is built in a child process with CLINIC_MODE on and off.
With it on, every endpoint of the recordings list (v8-01), the processing overview (v8-11),
the import agent (v8-13), the statistics (v8-30) and the ASR training (v8-31) blueprints must
exist and answer 401, not 404, without a login or credential. With it off none of them exist.
Synthetic throwaway database; nothing is logged in.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

BLUEPRINTS = ("clinic_recordings", "clinic_jobs", "clinic_statistics", "clinic_training", "clinic_agent",
              "clinic_agent_admin")

EXPECTED = {
    "clinic_recordings.list_recordings": ("GET", "/api/clinic/v1/recordings"),
    "clinic_jobs.health": ("GET", "/api/clinic/v1/jobs/health"),
    "clinic_jobs.retry": ("POST", "/api/clinic/v1/jobs/1/retry"),
    "clinic_statistics.aggregates": ("GET", "/api/clinic/v1/statistics/aggregates"),
    "clinic_training.manifest": ("GET", "/api/admin/clinic/training/manifest"),
    "clinic_training.schedule": ("GET", "/api/admin/clinic/training/schedule"),
    "clinic_training.start": ("POST", "/api/admin/clinic/training/runs"),
    "clinic_training.status": ("GET", "/api/admin/clinic/training/runs/1"),
    "clinic_training.cancel": ("POST", "/api/admin/clinic/training/runs/1/cancel"),
    "clinic_agent.inventory": ("PUT", "/api/clinic/v1/agent/inventory"),
    "clinic_agent.intents": ("GET", "/api/clinic/v1/agent/intents"),
    "clinic_agent.begin": ("POST", "/api/clinic/v1/agent/ingests"),
    "clinic_agent.chunk": ("PUT", "/api/clinic/v1/agent/ingests/00000000-0000-0000-0000-000000000000/chunks/0"),
    "clinic_agent.finalize": ("POST", "/api/clinic/v1/agent/ingests/00000000-0000-0000-0000-000000000000/finalize"),
    "clinic_agent.receipt": ("GET", "/api/clinic/v1/agent/ingests/00000000-0000-0000-0000-000000000000"),
    "clinic_agent.abort": ("POST", "/api/clinic/v1/agent/ingests/00000000-0000-0000-0000-000000000000/abort"),
    "clinic_agent.outcomes": ("POST", "/api/clinic/v1/agent/outcomes"),
    "clinic_agent_admin.list_agents": ("GET", "/api/clinic/v1/admin/agents"),
    "clinic_agent_admin.enroll": ("POST", "/api/clinic/v1/admin/agents"),
    "clinic_agent_admin.set_enabled": ("PATCH", "/api/clinic/v1/admin/agents/00000000-0000-0000-0000-000000000000"),
    "clinic_agent_admin.rotate_key": (
        "POST", "/api/clinic/v1/admin/agents/00000000-0000-0000-0000-000000000000/rotate-key"),
}

CHILD = r'''
import json, sys
from src.app import app
from src.clinic.route_policy import ROUTE_POLICY
from src.init_db import initialize_database

expected = json.loads(sys.argv[1])
with app.app_context():
    initialize_database(app)
app.config["WTF_CSRF_ENABLED"] = False  # the answer under test is the auth refusal, not the CSRF one
client = app.test_client()
endpoints = {rule.endpoint: sorted(rule.methods - {"HEAD", "OPTIONS"}) for rule in app.url_map.iter_rules()}
answers = {}
for endpoint, (method, path) in expected.items():
    answers[endpoint] = client.open(path, method=method).status_code
print("RESULT " + json.dumps({
    "blueprints": sorted(app.blueprints),
    "endpoints": {name: endpoints.get(name) for name in expected},
    "classified": {name: ROUTE_POLICY[name].kind if name in ROUTE_POLICY else None for name in expected},
    "answers": answers,
}))
'''


def _build(tmp_path: Path, clinic: bool) -> dict:
    env = {**os.environ, "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'reg.db'}",
           "UPLOAD_FOLDER": str(tmp_path / "uploads"), "RATELIMIT_ENABLED": "false",
           "DISABLE_VOICE_EMBEDDING_CHECK": "true", "ENABLE_AUTO_PROCESSING": "false"}
    env.pop("CLINIC_MODE", None)
    for name in ("CLINIC_AGENT_PROXY_SECRET", "CLINIC_AGENT_MASTER_SECRET"):
        env.pop(name, None)
    if clinic:
        env["CLINIC_MODE"] = "1"
    done = subprocess.run([sys.executable, "-c", CHILD, json.dumps(EXPECTED)], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=300)
    line = next((x for x in done.stdout.splitlines() if x.startswith("RESULT ")), None)
    assert line, done.stderr[-3000:]
    return json.loads(line[len("RESULT "):])


@pytest.fixture(scope="module")
def clinic_app(tmp_path_factory):
    return _build(tmp_path_factory.mktemp("reg_on"), True)


@pytest.fixture(scope="module")
def plain_app(tmp_path_factory):
    return _build(tmp_path_factory.mktemp("reg_off"), False)


def test_every_endpoint_exists_with_its_method_in_clinic_mode(clinic_app):
    assert set(BLUEPRINTS) <= set(clinic_app["blueprints"])
    for endpoint, (method, _path) in EXPECTED.items():
        assert clinic_app["endpoints"][endpoint] == [method], endpoint


def test_every_endpoint_is_classified_in_the_route_policy(clinic_app):
    assert all(kind is not None for kind in clinic_app["classified"].values()), clinic_app["classified"]
    assert {clinic_app["classified"][name] for name in EXPECTED if name.startswith("clinic_training.")} == {"admin"}
    assert {clinic_app["classified"][name] for name in EXPECTED if name.startswith("clinic_agent_admin.")} == {"admin"}
    assert {clinic_app["classified"][name] for name in EXPECTED if name.startswith("clinic_agent.")} == {"public"}
    assert {clinic_app["classified"][name] for name in EXPECTED
            if name.startswith(("clinic_recordings.", "clinic_jobs.", "clinic_statistics."))} == {"auth"}


def test_every_endpoint_answers_401_without_login(clinic_app):
    assert clinic_app["answers"] == {name: 401 for name in EXPECTED}, clinic_app["answers"]


def test_none_of_them_exists_with_clinic_mode_off(plain_app):
    assert not set(BLUEPRINTS) & set(plain_app["blueprints"])
    assert plain_app["endpoints"] == {name: None for name in EXPECTED}
    assert plain_app["answers"] == {name: 404 for name in EXPECTED}, plain_app["answers"]
