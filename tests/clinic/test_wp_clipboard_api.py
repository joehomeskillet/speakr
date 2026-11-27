"""Browser clipboard output (PAR-EXP-002/007/008), service and HTTP slice.

Synthetic data only. BT-EXP-002/007/008 stay unexecuted: this file contributes
the clipboard-to-receipt part and does not claim the originals.
"""

import uuid
from datetime import datetime, timezone

import pytest
from flask import Flask, jsonify
from flask_login import LoginManager, login_user
from flask_wtf.csrf import CSRFProtect, generate_csrf

from src.app import app as real_app
from src.clinic import config, release, richtext, routes_output
from src.clinic.clipboard_ledger import AuditClipboardLedger
from src.clinic.kis import DisabledAdapter
from src.clinic.models import (
    ClinicAudioRevision, ClinicContextRevision, ClinicDictation, ClinicOriginal, ClinicRelease,
)
from src.clinic.output import list_outputs
from src.clinic.output_attempts import (
    FAILED, SUCCEEDED, UNKNOWN, OutputAttemptError, bind_output_source,
)
from src.clinic.revisions import add_asr_revision, add_human_revision
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
    return rc.report_clipboard(c["ledger"], d, c["text"],
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
def web(case, world, monkeypatch):
    def allowed(user, action, dictation):
        return action == "correction_read" or (
            action in {"clipboard", "export"} and case.get("can_copy", True)
            and user.id in {dictation.author_user_id, world["user_admin"]}
        )

    monkeypatch.setattr(config, "client_profile", lambda: case.get("profile", "managed_web_online"))
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
        authorize=allowed, source_for=routes_output.output_source, ledger=AuditClipboardLedger()))
    flask.register_blueprint(routes_output.create_blueprint(authorize=allowed, adapter_for=DisabledAdapter))

    def client(uid):
        c = flask.test_client()
        if uid is None:
            return c, {}
        return c, {"X-CSRF-Token": c.get(f"/login/{uid}").json["csrf"]}

    return client, case


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
    db.session.commit()
    stale = own.get(_url(c), headers=h)
    assert stale.status_code == 409 and stale.mimetype == "application/problem+json"


def _target(browser, dictation):
    targets = browser.get(f"/api/clinic/v1/dictations/{dictation.id}/output-targets?file_formats=all").json
    return next(target for target in targets["targets"] if target["id"] == "clipboard")


def _snapshot(revision, version, *, bold):
    run = richtext.Segment(id="s1", text=revision.body, bold=bold)
    document = richtext.CanonicalDocument((richtext.Node("n1", "paragraph", (run,)),))
    db.session.add(richtext.ClinicRichTextSnapshot(revision_id=revision.id, version=version,
                                                  payload=document.to_payload()))


@pytest.mark.parametrize("legacy", [False, True])
def test_p16_released_clipboard_uses_pinned_document(web, legacy):
    client, c = web
    if legacy:
        active = ClinicRelease(
            dictation_id=c["dictation"].id, audio_revision_id=c["audio"].id,
            transcript_revision_id=c["text"].id, context_revision_id=c["context"].id,
            text_hash=c["text"].normalised_text_hash, actor_user_id=c["actor"],
            authorization_decision='{"allowed": true}', document_type="report", release_type="clinical")
        db.session.add(active)
    else:
        _snapshot(c["text"], 1, bold=True)
        active = release.create_release(
            c["dictation"], c["audio"], c["text"], c["context"], c["text"].normalised_text_hash,
            c["actor"], {"allowed": True}, datetime.now(timezone.utc),
            document_type="report", release_type="clinical")
    _snapshot(c["text"], 2, bold=legacy)
    db.session.commit()

    browser, csrf = client(c["actor"])
    assert _target(browser, c["dictation"])["binding"] == active.id
    stale_draft = browser.get(_url(c), query_string={"binding": "working_copy"},
                              headers={"Idempotency-Key": _req()})
    assert stale_draft.status_code == 409 and "text/plain" not in stale_draft.json
    for fmt in ("plain", "formatted"):
        key = {"Idempotency-Key": _req()}
        result = browser.get(_url(c), query_string={"format": fmt, "binding": active.id}, headers=key)
        assert result.status_code == 200
        assert result.json["text/plain"] == c["text"].body
        if fmt == "formatted":
            assert ("<strong>" in result.json["text/html"]) is not legacy
        else:
            assert "text/html" not in result.json
        claim, = [row for row in AuditClipboardLedger().values() if row.status == UNKNOWN]
        assert (claim.release_id, claim.source.audio_revision_id, claim.source.context_revision_id) == (
            active.id, active.audio_revision_id, active.context_revision_id)
        assert dict(claim.format_settings)["document_version"] == ("0" if legacy else "1")
        assert browser.post(_url(c, "/outcome"), headers=key | csrf, json={"outcome": "copied"}).status_code == 200


def test_p16_working_copy_receipt_requires_authorized_claim(web, world):
    client, c = web
    browser, csrf = client(c["actor"])
    outside, outside_csrf = client(c["other"])
    peer, peer_csrf = client(world["user_admin"])
    key = {"Idempotency-Key": _req()}
    assert _target(browser, c["dictation"]) == {
        "id": "clipboard", "channel": "clipboard", "available": True, "reason": None,
        "revision_id": c["text"].id, "binding": "working_copy", "label": "Arbeitskopie",
    }
    assert "revision_id" not in _target(outside, c["dictation"])
    assert outside.get(_url(c), headers=key).status_code == 404
    prepare = browser.get(_url(c), query_string={"binding": "working_copy"}, headers=key)
    assert prepare.status_code == 200 and prepare.headers["X-Clipboard-Label"] == "Arbeitskopie"
    body = {"outcome": "copied"}
    assert outside.post(_url(c, "/outcome"), headers=key | outside_csrf, json=body).status_code == 404
    assert peer.post(_url(c, "/outcome"), headers=key | peer_csrf, json=body).status_code == 409
    c["can_copy"] = False
    assert browser.post(_url(c, "/outcome"), headers=key | csrf, json=body).status_code == 404
    c["can_copy"] = True
    # A later ambiguous context must not replace the context frozen at prepare.
    db.session.add(ClinicContextRevision(dictation_id=c["dictation"].id, source_system="other"))
    db.session.commit()
    assert _target(browser, c["dictation"])["available"] is False
    assert browser.get(_url(c), headers={"Idempotency-Key": _req()}).status_code == 409
    done = browser.post(_url(c, "/outcome"), headers=key | csrf, json=body)
    assert done.status_code == 200 and done.json["status"] == SUCCEEDED
    stored, = AuditClipboardLedger().values()
    assert stored.mode == "working_copy" and stored.release_id is None
    assert stored.source.context_revision_id == c["context"].id
    receipt, = list_outputs(c["dictation"])
    assert (receipt.actor_user_id, receipt.transcript_revision_id) == (c["actor"], c["text"].id)
    assert db.session.query(ClinicRelease).count() == 0


def test_p16_refuses_uncorrected_and_stale_clipboard_sources(web, world):
    client, c = web
    browser, _ = client(c["other"])
    dictation = ClinicDictation(recording_id=world["rec_b"], author_user_id=world["user_b"])
    db.session.add(dictation)
    db.session.flush()
    original = ClinicOriginal(dictation_id=dictation.id, content_hash="c" * 64, size_bytes=1,
                              storage_key="originals/synthetic-b", receipt="stored", origin_ref="test")
    db.session.add_all([original, ClinicAudioRevision(dictation_id=dictation.id, content_hash="d" * 64),
                        ClinicContextRevision(dictation_id=dictation.id)])
    db.session.flush()
    asr = add_asr_revision(dictation, original, "ASR", "synthetic")
    db.session.commit()
    assert _target(browser, dictation)["available"] is False
    key = {"Idempotency-Key": _req()}
    assert browser.get(_url({"dictation": dictation, "text": asr}), headers=key).status_code == 409

    browser, _ = client(c["actor"])
    assert browser.get(_url(c), query_string={"binding": _req()}, headers=key).status_code == 409
    c["profile"] = "byod_internal"
    assert _target(browser, c["dictation"])["available"] is False
    assert browser.get(_url(c), headers=key).status_code == 403
    assert len(AuditClipboardLedger()) == 0
