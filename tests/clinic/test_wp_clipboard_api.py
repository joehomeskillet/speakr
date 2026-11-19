"""Browser clipboard output (PAR-EXP-002/007/008), service and HTTP slice.

Synthetic data only. BT-EXP-002/007/008 stay unexecuted: this file contributes
the clipboard-to-receipt part and does not claim the originals.
"""

import uuid

import pytest
from flask import Flask, jsonify
from flask_login import LoginManager, login_user
from flask_wtf.csrf import CSRFProtect, generate_csrf

from src.app import app as real_app
from src.clinic.models import ClinicAudioRevision, ClinicContextRevision, ClinicDictation, ClinicOriginal
from src.clinic.output import list_outputs
from src.clinic.output_attempts import (
    FAILED, SUCCEEDED, UNKNOWN, OutputAttemptError, bind_output_source,
)
from src.clinic.revisions import add_human_revision
from src.clinic import routes_clipboard as rc
from src.database import db
from src.models import User


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
        text = add_human_revision(dictation, None, "Zeile <1>\nZeile 2", actor, original_version=original)
        db.session.commit()
        state = dict(dictation=dictation, audio=audio, context=context, text=text, original=original,
                     actor=actor, other=world["user_b"], ledger={})
        state["source"] = lambda d: bind_output_source(d, audio, state["text"], context)
        yield state
        db.session.rollback()


def _req():
    return str(uuid.uuid4())


def _prepare(c, req=None, **kw):
    d = c["dictation"]
    args = dict(actor=c["actor"], request_id=req or _req(), allowed=True)
    args.update(kw)
    return rc.prepare_clipboard(c["ledger"], d, c["text"], c["source"](d), **args)


def _report(c, req, outcome, **kw):
    d = c["dictation"]
    return rc.report_clipboard(c["ledger"], d, c["text"], c["source"](d),
                               **dict(actor=c["actor"], request_id=req, outcome=outcome) | kw)


def test_prepare_returns_escaped_flavours_and_unknown_claim(case):
    claim, flavors = _prepare(case)
    assert flavors["text/plain"] == "Zeile <1>\nZeile 2"
    assert "&lt;1&gt;" in flavors["text/html"] and "<script" not in flavors["text/html"]
    assert claim.status == UNKNOWN and claim.generation == 1
    assert case["ledger"][claim.idempotency_key] == claim
    assert list_outputs(case["dictation"]) == []


def test_only_browser_success_completes_and_appends_receipt(case):
    req = _req()
    _prepare(case, req)
    done = _report(case, req, "copied")
    assert done.status == SUCCEEDED
    receipts = list_outputs(case["dictation"])
    assert [(r.channel, r.text_format, r.actor_user_id) for r in receipts] == [("CLIPBOARD", "FORMATTED", case["actor"])]
    assert receipts[0].transcript_revision_id == case["text"].id
    with pytest.raises(OutputAttemptError):
        _report(case, req, "copied")  # replay of a finished attempt


def test_denial_fails_and_allows_retry_abort_stays_unknown(case):
    req = _req()
    _prepare(case, req)
    assert _report(case, req, "denied").status == FAILED
    assert list_outputs(case["dictation"]) == []
    retry, _ = _prepare(case, req)
    assert retry.status == UNKNOWN and retry.generation == 2
    assert _report(case, req, "aborted").status == UNKNOWN
    assert list_outputs(case["dictation"]) == []
    with pytest.raises(OutputAttemptError):
        _prepare(case, req)  # unresolved attempt blocks a blind resend
    with pytest.raises(OutputAttemptError):
        _prepare(case, _req())


def test_unauthorized_and_stale_refused_without_flavours(case):
    with pytest.raises(OutputAttemptError, match="not authorised"):
        _prepare(case, allowed=False)
    newer = add_human_revision(case["dictation"], case["text"], "Neu", case["actor"],
                               original_version=case["original"])
    with pytest.raises(OutputAttemptError, match="stale"):
        _prepare(case)  # case["text"] is no longer the head
    assert newer.id != case["text"].id and case["ledger"] == {}


def test_report_without_claim_and_bad_outcome(case):
    with pytest.raises(OutputAttemptError):
        _report(case, _req(), "copied")
    req = _req()
    _prepare(case, req)
    with pytest.raises(OutputAttemptError):
        _report(case, req, "pasted")
    with pytest.raises(OutputAttemptError):  # another actor cannot complete this claim
        _report(case, req, "copied", actor=case["other"])


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

    flask.register_blueprint(rc.create_blueprint(
        authorize=lambda user, action, d: action == "clipboard" and user.id == d.author_user_id,
        source_for=case["source"], ledger=case["ledger"]))

    def client(uid):
        c = flask.test_client()
        if uid is None:
            return c, {}
        return c, {"X-CSRF-Token": c.get(f"/login/{uid}").json["csrf"]}

    with flask.app_context():
        yield client, case


def _url(c, suffix=""):
    return f"/api/clinic/v1/dictations/{c['dictation'].id}/clipboard/{c['text'].id}{suffix}"


def test_http_flow_with_csrf_and_trusted_actor(web):
    client, c = web
    h = {"Idempotency-Key": _req()}
    cl, csrf = client(c["actor"])
    ok = cl.get(_url(c), headers=h)
    assert ok.status_code == 200 and ok.headers["Cache-Control"] == "no-store"
    assert set(ok.json) == {"revision_id", "text/plain", "text/html", "status", "generation"}
    assert ok.json["status"] == UNKNOWN
    body = {"outcome": "copied", "actor_user_id": c["other"]}
    assert cl.post(_url(c, "/outcome"), headers=h | csrf, json=body).status_code == 400
    assert cl.post(_url(c, "/outcome"), headers=h, json={"outcome": "copied"}).status_code == 403  # no CSRF
    done = cl.post(_url(c, "/outcome"), headers=h | csrf, json={"outcome": "copied"})
    assert done.status_code == 200 and done.json["status"] == SUCCEEDED
    assert len(list_outputs(c["dictation"])) == 1


def test_http_refusals(web):
    client, c = web
    h = {"Idempotency-Key": _req()}
    assert client(None)[0].get(_url(c), headers=h).status_code == 401
    other, _ = client(c["other"])
    assert other.get(_url(c), headers=h).status_code == 404  # no object policy
    own, _ = client(c["actor"])
    assert own.get(_url(c)).status_code == 400  # missing request ID
    add_human_revision(c["dictation"], c["text"], "Neu", c["actor"], original_version=c["original"])
    stale = own.get(_url(c), headers=h)
    assert stale.status_code == 409 and stale.mimetype == "application/problem+json"
