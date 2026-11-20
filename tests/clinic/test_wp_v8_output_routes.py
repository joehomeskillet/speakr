"""Output routes (V8 P16 backend reachability): targets, export request, export status.

Synthetic data only. The routes are a thin layer over the export, KIS and release
services; this file proves the binding to the released revision, idempotency,
the policy gate and the unavailable answer of a disabled KIS adapter.
"""

import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from flask import Flask, jsonify
from flask_login import LoginManager, login_user
from flask_wtf.csrf import CSRFProtect, generate_csrf

from src.app import app as real_app
from src.clinic import routes_output as ro
from src.clinic.kis import DisabledAdapter, MockKisAdapter
from src.clinic.models import (
    ClinicAudioRevision, ClinicContextRevision, ClinicDelivery, ClinicDictation, ClinicOriginal,
)
from src.clinic.release import create_release
from src.clinic.revisions import add_human_revision
from src.database import db
from src.models import User

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def case(world, monkeypatch):
    monkeypatch.setenv("CLINIC_MODE", "1")
    with real_app.app_context():
        actor = world["user_a"]
        dictation = ClinicDictation(recording_id=world["rec_a"], author_user_id=actor)
        db.session.add(dictation)
        db.session.flush()
        audio = ClinicAudioRevision(dictation_id=dictation.id, content_hash="a" * 64)
        original = ClinicOriginal(dictation_id=dictation.id, content_hash="b" * 64, size_bytes=1,
                                  storage_key="originals/synthetic", receipt="stored", origin_ref="test")
        context = ClinicContextRevision(dictation_id=dictation.id, source_system="test",
                                        tenant_scope="site", case_ref="case", patient_ref="patient")
        db.session.add_all([audio, original, context])
        db.session.flush()
        text = add_human_revision(dictation, None, "Zeile 1\nZeile 2", actor, original_version=original)
        db.session.commit()
        yield dict(dictation=dictation, audio=audio, context=context, text=text, original=original,
                   actor=actor, other=world["user_b"], adapter=DisabledAdapter(), jobs={})
        db.session.rollback()


def _release(c):
    row = create_release(
        c["dictation"], c["audio"], c["text"], c["context"], c["text"].normalised_text_hash,
        c["actor"], {"allowed": True}, NOW, document_type="report", release_type="clinical")
    db.session.commit()
    return row


@pytest.fixture
def web(case):
    flask = Flask(__name__)
    flask.config.update(TESTING=True, SECRET_KEY="synthetic",
                        SQLALCHEMY_DATABASE_URI=real_app.config["SQLALCHEMY_DATABASE_URI"])
    db.init_app(flask)
    login = LoginManager(flask)
    CSRFProtect(flask)
    login.user_loader(lambda uid: db.session.get(User, int(uid)))

    @flask.get("/login/<int:uid>")
    def _login(uid):
        login_user(db.session.get(User, uid))
        return jsonify(csrf=generate_csrf())

    flask.register_blueprint(ro.create_blueprint(
        authorize=lambda user, action, d: action == "export" and user.id == d.author_user_id,
        adapter_for=lambda: case["adapter"], jobs=case["jobs"]))

    def client(uid):
        c = flask.test_client()
        if uid is None:
            return c, {}
        return c, {"X-CSRF-Token": c.get(f"/login/{uid}").json["csrf"]}

    with flask.app_context():
        yield client, case


def _key():
    return {"Idempotency-Key": str(uuid.uuid4())}


def _base(c):
    return f"/api/clinic/v1/dictations/{c['dictation'].id}"


def _body(c, fmt="txt", revision=None):
    return {"revision_id": (revision or c["text"]).id, "format": fmt}


def test_unreleased_export_is_rejected(web):
    client, c = web
    cl, csrf = client(c["actor"])
    res = cl.post(_base(c) + "/exports", headers=_key() | csrf, json=_body(c))
    assert res.status_code == 409 and res.json["code"] == "NOT_RELEASED"
    assert res.mimetype == "application/problem+json" and c["jobs"] == {}


def test_release_export_binds_revision_and_replays_once(web):
    client, c = web
    release = _release(c)
    cl, csrf = client(c["actor"])
    headers = _key() | csrf
    first = cl.post(_base(c) + "/exports", headers=headers, json=_body(c, "docx"))
    assert first.status_code == 201 and first.headers["Cache-Control"] == "no-store"
    job = first.json
    assert job["revision_id"] == c["text"].id and job["release_id"] == release.id
    assert job["status"] == "ready" and job["format"] == "docx" and len(job["sha256"]) == 64
    again = cl.post(_base(c) + "/exports", headers=headers, json=_body(c, "docx"))
    assert again.status_code == 200 and again.json["id"] == job["id"] and again.json["replayed"] is True
    assert len([k for k in c["jobs"] if k[0] == "id"]) == 1
    clash = cl.post(_base(c) + "/exports", headers=headers, json=_body(c, "txt"))
    assert clash.status_code == 409 and clash.json["code"] == "IDEMPOTENCY_CONFLICT"
    status = cl.get(_base(c) + f"/exports/{job['id']}")
    assert status.status_code == 200 and status.json["revision_id"] == c["text"].id
    assert "actor_id" not in status.json


def test_status_hidden_from_other_actor_and_unknown_job(web):
    client, c = web
    _release(c)
    cl, csrf = client(c["actor"])
    job = cl.post(_base(c) + "/exports", headers=_key() | csrf, json=_body(c)).json
    assert cl.get(_base(c) + f"/exports/{uuid.uuid4()}").status_code == 404
    other, _ = client(c["other"])
    assert other.get(_base(c) + f"/exports/{job['id']}").status_code == 404


def test_superseded_revision_is_not_exportable(web):
    client, c = web
    _release(c)
    add_human_revision(c["dictation"], c["text"], "Neu", c["actor"], original_version=c["original"])
    db.session.commit()
    cl, csrf = client(c["actor"])
    res = cl.post(_base(c) + "/exports", headers=_key() | csrf, json=_body(c))
    assert res.status_code == 409 and res.json["code"] == "NOT_RELEASED"


def test_targets_follow_release_and_adapter(web):
    client, c = web
    cl, _ = client(c["actor"])
    before = cl.get(_base(c) + "/output-targets").json
    assert before["released_revision_id"] is None
    by_id = {t["id"]: t for t in before["targets"]}
    assert by_id["txt"]["reason"] == "not_released" and by_id["clipboard"]["available"] is True
    _release(c)
    after = cl.get(_base(c) + "/output-targets").json
    by_id = {t["id"]: t for t in after["targets"]}
    assert after["released_revision_id"] == c["text"].id
    assert by_id["txt"]["available"] and by_id["docx"]["available"]
    assert by_id["kis"] == {"id": "kis", "channel": "kis", "available": False, "reason": "kis_unavailable"}
    c["adapter"] = MockKisAdapter()
    assert {t["id"]: t for t in cl.get(_base(c) + "/output-targets").json["targets"]}["kis"]["available"]


def test_disabled_kis_reports_unavailable_never_success(web):
    client, c = web
    _release(c)
    cl, csrf = client(c["actor"])
    res = cl.post(_base(c) + "/exports", headers=_key() | csrf, json=_body(c, "kis"))
    assert res.status_code == 503 and res.json["code"] == "KIS_UNAVAILABLE"
    assert res.json["export_status"] == "unavailable"
    assert db.session.query(ClinicDelivery).count() == 0


def test_mock_kis_delivers_once_per_key_and_status_reads_the_ledger(web):
    client, c = web
    release = _release(c)
    c["adapter"] = adapter = MockKisAdapter()
    cl, csrf = client(c["actor"])
    headers = _key() | csrf
    first = cl.post(_base(c) + "/exports", headers=headers, json=_body(c, "kis"))
    assert first.status_code == 201 and first.json["status"] == "delivered"
    assert first.json["revision_id"] == c["text"].id and first.json["release_id"] == release.id
    again = cl.post(_base(c) + "/exports", headers=headers, json=_body(c, "kis"))
    assert again.status_code == 200 and again.json["id"] == first.json["id"] and again.json["replayed"] is True
    assert adapter.deliver_calls == 1
    status = cl.get(_base(c) + f"/exports/{first.json['id']}")
    assert status.json["status"] == "delivered" and status.json["revision_id"] == c["text"].id
    assert client(c["other"])[0].get(_base(c) + f"/exports/{first.json['id']}").status_code == 404


def test_http_refusals(web):
    client, c = web
    _release(c)
    url = _base(c) + "/exports"
    cl, csrf = client(c["actor"])
    assert cl.post(url, headers=csrf, json=_body(c)).status_code == 400  # no idempotency key
    assert cl.post(url, headers=_key() | csrf, json={**_body(c), "actor_user_id": 1}).status_code == 400
    assert cl.post(url, headers=_key() | csrf, json=_body(c, "pdf")).status_code == 400
    foreign = {"revision_id": str(uuid.uuid4()), "format": "txt"}
    assert cl.post(url, headers=_key() | csrf, json=foreign).status_code == 404
    assert cl.post(url, headers=_key(), json=_body(c)).status_code == 403  # no CSRF token


def test_anonymous_and_unauthorised_actor_are_refused(web):
    client, c = web
    _release(c)
    assert client(None)[0].get(_base(c) + "/output-targets").status_code == 401


def test_actor_without_export_right_sees_nothing(web):
    client, c = web
    _release(c)
    other, other_csrf = client(c["other"])
    assert other.post(_base(c) + "/exports", headers=_key() | other_csrf, json=_body(c)).status_code == 404
    assert other.get(_base(c) + "/output-targets").status_code == 404


def test_routes_are_registered_in_clinic_mode(tmp_path):
    script = (
        "from src.app import app\n"
        "print('RULES', sorted(r.rule.split('/dictations/<uuid:dictation_id>')[1] for r in app.url_map.iter_rules()"
        " if 'clipboard' in r.rule or 'exports' in r.rule or 'output-targets' in r.rule))\n")
    env = {**os.environ, "CLINIC_MODE": "1", "TRANSCRIPTION_BASE_URL": "http://127.0.0.1:9/v1",
           "TRANSCRIPTION_API_KEY": "synthetic", "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path}/routes.db"}
    done = subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=env, capture_output=True, text=True)
    line = next(row for row in done.stdout.splitlines() if row.startswith("RULES"))
    for suffix in ("/clipboard/<uuid:revision_id>", "/exports", "/exports/<uuid:job_id>", "/output-targets"):
        assert suffix in line


def test_new_key_after_unknown_does_not_send_again(web):
    client, c = web
    _release(c)
    c["adapter"] = adapter = MockKisAdapter(script=["timeout"])
    cl, csrf = client(c["actor"])
    first = cl.post(_base(c) + "/exports", headers=_key() | csrf, json=_body(c, "kis"))
    assert first.json["status"] == "unknown"
    second = cl.post(_base(c) + "/exports", headers=_key() | csrf, json=_body(c, "kis"))
    assert second.json["status"] == "unknown" and second.json["replayed"] is True
    assert second.json["id"] == first.json["id"]
    assert adapter.deliver_calls == 1
    assert db.session.query(ClinicDelivery).count() == 1


def test_new_key_after_delivered_does_not_send_again(web):
    client, c = web
    _release(c)
    c["adapter"] = adapter = MockKisAdapter()
    cl, csrf = client(c["actor"])
    first = cl.post(_base(c) + "/exports", headers=_key() | csrf, json=_body(c, "kis"))
    assert first.json["status"] == "delivered"
    second = cl.post(_base(c) + "/exports", headers=_key() | csrf, json=_body(c, "kis"))
    assert second.json["status"] == "delivered" and second.json["replayed"] is True
    assert adapter.deliver_calls == 1


def test_real_app_wiring_in_clinic_mode(tmp_path):
    script = (
        "from src.app import app\n"
        "from src.clinic import routes_output as ro\n"
        "from src.clinic.kis import DisabledAdapter\n"
        "res = app.test_client().get('/api/clinic/v1/dictations/00000000-0000-4000-8000-000000000000/output-targets')\n"
        "print('STATUS', res.status_code, 'clinic_unclassified_route' in res.get_data(as_text=True))\n"
        "print('ADAPTER', isinstance(ro.configured_adapter(app), DisabledAdapter))\n")
    env = {k: v for k, v in os.environ.items() if k != "CLINIC_PROFILE"}
    env.update(CLINIC_MODE="1", TRANSCRIPTION_BASE_URL="http://127.0.0.1:9/v1",
               TRANSCRIPTION_API_KEY="synthetic", SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path}/wiring.db")
    done = subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=env, capture_output=True, text=True)
    assert "STATUS 401 False" in done.stdout, done.stdout + done.stderr
    assert "ADAPTER True" in done.stdout
