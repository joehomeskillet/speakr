"""HTTP evidence for the alignment routes (v8-85)."""

import logging
import uuid

import pytest
from flask import Flask, jsonify
from flask_login import LoginManager, login_user
from flask_wtf.csrf import CSRFProtect, generate_csrf

from src.app import app as main_app, db
from src.clinic import alignment as al
from src.clinic import alignment_tokens as at
from src.clinic import routes_alignment
from src.clinic.audio_revisions import current_audio_revision, init_audio_revision
from src.clinic.models import ClinicDictation, ClinicOriginal, ClinicTranscriptRevision
from src.clinic.parity_receipts import run_once
from src.clinic.registry import create_clinic_tables
from src.clinic.revisions import add_asr_revision, add_human_revision, current_revision
from src.models import ProcessingJob, Recording, User
from src.services import job_queue as jq

BODY = "Die Unterlagen sind bereit."
SECRET = "Geheimwort"
DURATION = 3000
BASE = "/api/clinic/v1/dictations/{}"
REQUEST_KEYS = {"schema_version", "audio_revision_id", "transcript_revision_id", "document_hash"}


def _user():
    suffix = uuid.uuid4().hex[:10]
    user = User(username="ra" + suffix, email=suffix + "@example.invalid", password="x")
    db.session.add(user)
    db.session.flush()
    return user


def _scene(body=BODY):
    author, other = _user(), _user()
    recording = Recording(user_id=author.id, title="clinic", status="PENDING")
    db.session.add(recording)
    db.session.flush()
    dictation = ClinicDictation(recording_id=recording.id, author_user_id=author.id)
    db.session.add(dictation)
    db.session.flush()
    original = ClinicOriginal(
        dictation_id=dictation.id, content_hash="o" * 64, size_bytes=1,
        storage_key="originals/" + uuid.uuid4().hex, receipt="stored", origin_ref="device:test",
    )
    db.session.add(original)
    db.session.flush()
    audio = init_audio_revision(dictation, original, DURATION)
    revision = add_asr_revision(dictation, original, body, "stub-asr:1")
    return author, other, dictation, audio, revision


def _seed(dictation, audio, revision, partial=False):
    row = al.create_pending(dictation, audio, revision, source_kind="asr", engine="stub-engine",
                            model_revision="m1", audio_duration_ms=DURATION)
    document = at.tokens_for(dictation, revision).document
    items = []
    for node in document["nodes"]:
        for index, token in enumerate(node["tokens"]):
            if token["kind"] == "word":
                n = len(items)
                gap = partial and n == 0
                items.append({
                    "word_id": token["token_id"], "text_node_id": node["text_node_id"],
                    "token_index": index, "displayed_text": token["text"],
                    "state": "unaligned" if gap else "valid",
                    "start_ms": None if gap else n * 200, "end_ms": None if gap else n * 200 + 100,
                    "reason_code": "NO_VERIFIED_TIMING" if gap else None,
                })
    al.complete(row, items)
    db.session.commit()
    return row


class FailingQueue:
    def enqueue(self, *args, **kwargs):
        raise RuntimeError("queue down")


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv("CLINIC_MODE", "1")
    monkeypatch.setattr(jq, "_CLINIC_HANDLERS", {})
    with main_app.app_context():
        create_clinic_tables()
        author, other, dictation, audio, revision = _scene()
        db.session.commit()
        ids = dict(dictation=dictation.id, author=author.id, other=other.id,
                   audio=audio.id, revision=revision.id)

        flask = Flask(__name__)
        flask.config.update(TESTING=True, SECRET_KEY="synthetic-test-key",
                            SQLALCHEMY_DATABASE_URI=main_app.config["SQLALCHEMY_DATABASE_URI"])
        db.init_app(flask)
        login = LoginManager(flask)
        CSRFProtect(flask)
        state = {"queue": None}

        class QueueProxy:
            def enqueue(self, *args, **kwargs):
                return (state["queue"] or jq.job_queue).enqueue(*args, **kwargs)

        @login.user_loader
        def load(uid):
            return db.session.get(User, int(uid))

        @flask.get("/csrf")
        def anonymous_csrf():
            return jsonify(csrf=generate_csrf())

        @flask.get("/as/<int:uid>")
        def as_user(uid):
            login_user(db.session.get(User, uid))
            return jsonify(csrf=generate_csrf())

        flask.register_blueprint(routes_alignment.create_blueprint(
            authorize=lambda user, action, d: user.id == d.author_user_id,
            run_once=run_once, queue=QueueProxy()))

        def login_as(uid):
            client = flask.test_client()
            return client, {"X-CSRF-Token": client.get(f"/as/{uid}").json["csrf"]}

        client, headers = login_as(ids["author"])
        yield client, headers, ids, state, login_as, flask


def _basis(ids):
    return {
        "schema_version": "2.0", "audio_revision_id": ids["audio"],
        "transcript_revision_id": ids["revision"],
        "document_hash": db.session.get(at.ClinicTranscriptTokens, ids["revision"]).document_hash
        if db.session.get(at.ClinicTranscriptTokens, ids["revision"]) else _hash(ids),
    }


def _hash(ids):
    dictation = db.session.get(ClinicDictation, ids["dictation"])
    revision = db.session.get(ClinicTranscriptRevision, ids["revision"])
    value = at.tokens_for(dictation, revision).document_hash
    db.session.commit()
    return value


def _post(client, headers, ids, payload, key=None, drop=()):
    h = {k: v for k, v in headers.items() if k not in drop}
    h["Idempotency-Key"] = key or str(uuid.uuid4())
    return client.post(BASE.format(ids["dictation"]) + "/alignment", headers=h, json=payload)


def _jobs():
    return db.session.query(ProcessingJob).filter_by(job_type="clinic_alignment").count()


def _all_no_store(*responses):
    for response in responses:
        assert response.headers["Cache-Control"] == "no-store"


# ------------------------------------------------------------ document

def test_document_route_matches_revision_and_hash(api):
    client, _, ids, *_ = api
    resp = client.get(BASE.format(ids["dictation"]) + f"/transcript-revisions/{ids['revision']}/document")
    assert resp.status_code == 200, resp.json
    body = resp.json
    assert set(body) == {"schema_version", "transcript_revision_id", "tokenization_version",
                         "document_hash", "canonical_document"}
    assert body["schema_version"] == "2.0" and body["transcript_revision_id"] == ids["revision"]
    assert at.project(body["canonical_document"]) == BODY
    assert at.document_hash(body["canonical_document"]) == body["document_hash"]
    _all_no_store(resp)


def test_document_row_for_legacy_revision_is_created_and_committed(api):
    client, _, ids, *_ = api
    assert db.session.get(at.ClinicTranscriptTokens, ids["revision"]) is None
    assert client.get(BASE.format(ids["dictation"]) + f"/transcript-revisions/{ids['revision']}/document").status_code == 200
    db.session.rollback()
    assert db.session.get(at.ClinicTranscriptTokens, ids["revision"]) is not None


def test_document_of_a_foreign_revision_is_404(api):
    client, _, ids, *_ = api
    _, _, foreign, _, other_rev = _scene("Anderer Text.")
    db.session.commit()
    resp = client.get(BASE.format(ids["dictation"]) + f"/transcript-revisions/{other_rev.id}/document")
    assert resp.status_code == 404 and resp.mimetype == "application/problem+json"
    unknown = client.get(BASE.format(ids["dictation"]) + f"/transcript-revisions/{uuid.uuid4()}/document")
    assert unknown.status_code == 404
    _all_no_store(resp, unknown)


# ------------------------------------------------------------ alignment read

def _get(client, ids, audio=None, transcript=None):
    params = {}
    if audio is not None:
        params["audio_revision_id"] = audio
    if transcript is not None:
        params["transcript_revision_id"] = transcript
    return client.get(BASE.format(ids["dictation"]) + "/alignment", query_string=params)


def test_alignment_exact_row_with_contract_keys(api):
    client, _, ids, *_ = api
    dictation = db.session.get(ClinicDictation, ids["dictation"])
    row = _seed(dictation, current_audio_revision(dictation), current_revision(dictation))
    resp = _get(client, ids, ids["audio"], ids["revision"])
    assert resp.status_code == 200, resp.json
    assert set(resp.json) == al.CONTRACT_KEYS
    assert resp.json["state"] == "succeeded" and resp.json["alignment_revision_id"] == row.id
    assert len(resp.json["items"]) == 4
    _all_no_store(resp)


def test_alignment_partial_row_passes(api):
    client, _, ids, *_ = api
    dictation = db.session.get(ClinicDictation, ids["dictation"])
    _seed(dictation, current_audio_revision(dictation), current_revision(dictation), partial=True)
    resp = _get(client, ids, ids["audio"], ids["revision"])
    assert resp.status_code == 200 and resp.json["state"] == "partial"
    assert resp.json["items"][0]["state"] == "unaligned"


def test_alignment_older_row_is_reported_stale(api):
    client, _, ids, *_ = api
    dictation = db.session.get(ClinicDictation, ids["dictation"])
    first = current_revision(dictation)
    _seed(dictation, current_audio_revision(dictation), first)
    newer = add_human_revision(dictation, first, BODY + " Neu.", ids["author"])
    db.session.commit()
    resp = _get(client, ids, ids["audio"], newer.id)
    assert resp.status_code == 200
    assert resp.json["state"] == "stale" and resp.json["transcript_revision_id"] == first.id


def test_alignment_none_is_404_not_found_code(api):
    client, _, ids, *_ = api
    resp = _get(client, ids, ids["audio"], ids["revision"])
    assert resp.status_code == 404 and resp.json["code"] == "ALIGNMENT_NOT_FOUND"
    assert resp.mimetype == "application/problem+json"
    assert set(resp.json) == {"type", "title", "status", "code", "correlation_id"}
    _all_no_store(resp)


def test_alignment_missing_or_malformed_parameters_are_400(api):
    client, _, ids, *_ = api
    cases = [_get(client, ids), _get(client, ids, ids["audio"]), _get(client, ids, None, ids["revision"]),
             _get(client, ids, "bad id", ids["revision"]), _get(client, ids, ids["audio"], "x" * 161)]
    assert [r.status_code for r in cases] == [400] * 5
    _all_no_store(*cases)


# ------------------------------------------------------------ alignment request

def test_post_accepts_then_replays_with_one_job(api):
    client, headers, ids, *_ = api
    key = str(uuid.uuid4())
    payload = _basis(ids)
    first = _post(client, headers, ids, payload, key)
    assert first.status_code == 202, first.json
    assert first.json["operation_type"] == "alignment" and first.json["state"] == "pending"
    again = _post(client, headers, ids, payload, key)
    assert again.status_code == 202 and again.json == first.json
    assert _jobs() == 1
    _all_no_store(first, again)


def test_post_other_key_same_basis_reuses_row_and_job(api):
    client, headers, ids, *_ = api
    first = _post(client, headers, ids, _basis(ids))
    second = _post(client, headers, ids, _basis(ids))
    assert first.json["operation_id"] == second.json["operation_id"]
    assert _jobs() == 1


def test_post_terminal_operation_answers_200(api):
    client, headers, ids, *_ = api
    payload = _basis(ids)
    first = _post(client, headers, ids, payload)
    assert first.status_code == 202
    from src.clinic import alignment_job as aj
    aj.handle({"dictation_id": ids["dictation"], "alignment_revision_id": first.json["operation_id"]}, "t")
    db.session.expire_all()
    status = client.get(BASE.format(ids["dictation"]) + f"/operations/{first.json['operation_id']}")
    assert status.status_code == 200 and status.json["state"] == "failed"  # no aligned ancestor: terminal
    repeat = _post(client, headers, ids, payload)
    assert repeat.status_code == 200 and repeat.json["state"] == "failed"


def test_post_same_key_other_body_is_idempotency_conflict(api):
    client, headers, ids, *_ = api
    key = str(uuid.uuid4())
    assert _post(client, headers, ids, _basis(ids), key).status_code == 202
    other = dict(_basis(ids), document_hash="0" * 64)
    resp = _post(client, headers, ids, other, key)
    assert resp.status_code == 409 and resp.json["code"] == "IDEMPOTENCY_CONFLICT"
    _all_no_store(resp)


def test_post_stale_basis_is_base_conflict(api):
    client, headers, ids, *_ = api
    payload = _basis(ids)
    dictation = db.session.get(ClinicDictation, ids["dictation"])
    add_human_revision(dictation, current_revision(dictation), BODY + " Neu.", ids["author"])
    db.session.commit()
    resp = _post(client, headers, ids, payload)
    assert resp.status_code == 409 and resp.json["code"] == "BASE_CONFLICT"
    assert resp.mimetype == "application/problem+json"
    assert _jobs() == 0
    _all_no_store(resp)


def test_post_queue_failure_is_503(api):
    client, headers, ids, state, *_ = api
    state["queue"] = FailingQueue()
    resp = _post(client, headers, ids, _basis(ids))
    assert resp.status_code == 503 and resp.json["code"] == "DEPENDENCY_UNAVAILABLE"
    _all_no_store(resp)


def test_post_validation_errors(api):
    client, headers, ids, *_ = api
    payload = _basis(ids)
    assert _post(client, headers, ids, payload, drop=("X-CSRF-Token",)).status_code == 403
    for key in ("", "not-a-uuid", str(uuid.uuid4()).upper()):
        h = dict(headers, **{"Idempotency-Key": key})
        assert client.post(BASE.format(ids["dictation"]) + "/alignment", headers=h, json=payload).status_code == 400
    no_key = client.post(BASE.format(ids["dictation"]) + "/alignment", headers=headers, json=payload)
    assert no_key.status_code == 400
    assert _post(client, headers, ids, dict(payload, extra=1)).status_code == 400
    missing = {k: v for k, v in payload.items() if k != "document_hash"}
    assert _post(client, headers, ids, missing).status_code == 400
    assert _post(client, headers, ids, dict(payload, schema_version="1.0")).status_code == 400
    assert _post(client, headers, ids, dict(payload, document_hash="ABC")).status_code == 400
    assert _post(client, headers, ids, dict(payload, audio_revision_id=5)).status_code == 400
    assert _post(client, headers, ids, [payload]).status_code == 400
    h = dict(headers, **{"Idempotency-Key": str(uuid.uuid4())})
    assert client.post(BASE.format(ids["dictation"]) + "/alignment", headers=h, data="x").status_code == 400
    assert _jobs() == 0


# ------------------------------------------------------------ operation

def test_operation_status_and_foreign_operation(api):
    client, headers, ids, *_ = api
    op = _post(client, headers, ids, _basis(ids)).json
    resp = client.get(BASE.format(ids["dictation"]) + f"/operations/{op['operation_id']}")
    assert resp.status_code == 200 and resp.json == op
    assert set(resp.json) == al.OPERATION_KEYS
    _, _, other_dictation, other_audio, other_rev = _scene("Zweites Diktat.")
    foreign = _seed(other_dictation, other_audio, other_rev)
    miss = client.get(BASE.format(ids["dictation"]) + f"/operations/{foreign.id}")
    unknown = client.get(BASE.format(ids["dictation"]) + f"/operations/{uuid.uuid4()}")
    assert miss.status_code == 404 and unknown.status_code == 404
    _all_no_store(resp, miss, unknown)


# ------------------------------------------------------------ access

def _urls(ids):
    d = BASE.format(ids["dictation"])
    return [
        ("get", d + f"/transcript-revisions/{ids['revision']}/document", None),
        ("get", d + f"/alignment?audio_revision_id={ids['audio']}&transcript_revision_id={ids['revision']}", None),
        ("post", d + "/alignment", None),
        ("get", d + f"/operations/{uuid.uuid4()}", None),
    ]


def test_every_route_401_without_session(api):
    *_, flask = api
    ids = api[2]
    anon = flask.test_client()
    token = anon.get("/csrf").json["csrf"]
    for method, url, _ in _urls(ids):
        kwargs = {"headers": {"X-CSRF-Token": token, "Idempotency-Key": str(uuid.uuid4())}, "json": {}} if method == "post" else {}
        resp = getattr(anon, method)(url, **kwargs)
        assert resp.status_code == 401, url
        assert resp.mimetype == "application/problem+json"
        _all_no_store(resp)


def test_every_route_404_when_policy_denies(api):
    _, _, ids, _, login_as, _ = api
    client, headers = login_as(ids["other"])
    payload = _basis(ids)
    for method, url, _ in _urls(ids):
        kwargs = {"headers": dict(headers, **{"Idempotency-Key": str(uuid.uuid4())}), "json": payload} if method == "post" else {}
        resp = getattr(client, method)(url, **kwargs)
        assert resp.status_code == 404, url
        _all_no_store(resp)
    assert _jobs() == 0


def test_every_route_404_with_clinic_mode_off(api, monkeypatch):
    client, headers, ids, *_ = api
    monkeypatch.setenv("CLINIC_MODE", "0")
    payload = _basis(ids)
    for method, url, _ in _urls(ids):
        kwargs = {"headers": dict(headers, **{"Idempotency-Key": str(uuid.uuid4())}), "json": payload} if method == "post" else {}
        resp = getattr(client, method)(url, **kwargs)
        assert resp.status_code == 404, url
        _all_no_store(resp)


def test_no_transcript_text_in_logs(api, caplog):
    client, headers, ids, *_ = api
    dictation = db.session.get(ClinicDictation, ids["dictation"])
    add_human_revision(dictation, current_revision(dictation), BODY + " " + SECRET, ids["author"])
    db.session.commit()
    ids = dict(ids, revision=current_revision(dictation).id)
    with caplog.at_level(logging.DEBUG):
        _post(client, headers, ids, _basis(ids))
        _post(client, headers, ids, dict(_basis(ids), document_hash="0" * 64))
        _get(client, ids, ids["audio"], ids["revision"])
    assert SECRET not in caplog.text and "Unterlagen" not in caplog.text
