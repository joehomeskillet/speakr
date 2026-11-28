"""PAR-WF-013 (Q-06): non-destructive deletion-request disposition.

Synthetic data only. Cancelling writes nothing; confirming records a durable
deletion request and an audit row while preserving clinical bytes, revisions,
archive status, retention state and existing history. Archiving stays separate.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from flask import Flask
from flask_login import LoginManager, UserMixin, login_user
from flask_wtf.csrf import generate_csrf

from src.clinic import archive_policy, lease, policy, release, revisions, routes_history, workflow
from src.clinic.acceptance_policy import SCOPE_DICTATION, AcceptanceLedger
from src.clinic.archive_policy import (
    ARCHIVE,
    DELETE,
    DELETION_REQUESTED,
    ArchiveBook,
    ArchivePolicyError,
    ClinicArchiveEntry,
    ClinicDeletionRequest,
    Links,
)
from src.clinic.models import (
    ClinicArchiveEvent,
    ClinicAudioRevision,
    ClinicContextRevision,
    ClinicDictation,
    ClinicOriginal,
    ClinicRelease,
    ClinicTranscriptRevision,
)
from src.clinic.parity_dispatch import ParityDispatch
from src.clinic.parity_receipts import ClinicParityReceipt, run_once
from src.clinic.routes_workflow import create_blueprint, register_commands
from src.database import db
from src.models import AuditEvent, Recording, User
from src.services import audit, retention

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def world(monkeypatch, tmp_path):
    monkeypatch.setenv("CLINIC_MODE", "1")
    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        SECRET_KEY="synthetic-delete-request",
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path / 'delreq.db'}",
    )
    db.init_app(app)
    from src.services.job_queue import job_queue

    monkeypatch.setattr(job_queue, "_app", app)
    monkeypatch.setattr(job_queue, "_is_owner", False)

    def add(row):
        db.session.add(row)
        db.session.flush()
        return row

    with app.app_context():
        db.create_all()
        audit.reset_memory()
        user = add(User(username="author", email="a@example.invalid", password="synthetic"))
        media = tmp_path / "original.wav"
        media.write_bytes(b"RIFF-synthetic-original-bytes")
        recording = add(Recording(user_id=user.id, title="Synthetic", audio_path=str(media)))
        item = workflow.start(
            db.session(), "create", author_user_id=user.id, pool_id="pool",
            assignee_user_id=user.id, acceptance_required=False,
        )
        dictation = add(ClinicDictation(id=item.id, recording_id=recording.id, author_user_id=user.id))
        for trigger in ("send_offline", "skip_recognition"):
            workflow.transition(db.session(), item.id, trigger)
        original = add(ClinicOriginal(
            dictation_id=item.id,
            content_hash=hashlib.sha256(media.read_bytes()).hexdigest(),
            size_bytes=media.stat().st_size,
            storage_key=str(media),
            receipt="stored",
            origin_ref="test",
        ))
        asr_rev = revisions.add_asr_revision(dictation, original, "Rohtext synthetisch.", "model:synthetic")
        head = revisions.add_human_revision(
            dictation, asr_rev, "Synthetischer Text.", user.id, original_version=original,
        )
        audio = add(ClinicAudioRevision(dictation_id=item.id, content_hash="b" * 64))
        context = add(ClinicContextRevision(
            dictation_id=item.id, source_system="test", tenant_scope="test", case_ref="C-1", patient_ref="P-1",
        ))
        released = release.create_release(
            dictation, audio, head, context, head.normalised_text_hash, user.id,
            {"allowed": True}, NOW - timedelta(days=10), document_type="report", release_type="clinical_final",
        )
        workflow.transition(db.session(), item.id, "complete")
        lease.acquire(item.id, user.id, datetime.now(timezone.utc))
        rule = AcceptanceLedger().publish(
            SCOPE_DICTATION, item.id, required=False, approver_user_ids=[user.id], now=NOW,
        )
        db.session.commit()

        state = {"actions": {DELETE, ARCHIVE}, "denies": set()}
        monkeypatch.setattr(
            policy, "build_subject",
            lambda uid: policy.Subject(
                uid, False,
                (policy.Grant("own", frozenset(state["actions"])),),
                (policy.Deny(recording_ids=frozenset(state["denies"])),) if state["denies"] else (),
            ),
        )
        monkeypatch.setattr(policy, "build_facts", lambda rid, owner: policy.RecordingFacts(rid, owner))

        def authorize(actor, permission, target):
            action = DELETE if permission == "clinic.delete" else ARCHIVE if permission == "clinic.archive" else "view"
            return policy.decide(policy.build_subject(actor), action, policy.build_facts(target.recording_id, target.author_user_id)).allowed

        book = ArchiveBook()
        app.register_blueprint(create_blueprint(
            authorize=authorize, run_once=run_once, allowed_actions=lambda *_: set(),
            record_audit=lambda _rows: None, trusted_context=lambda _c: True,
            open_review_flags=lambda *_: 0, job_type="summarize", book=book,
            acceptance_policy=lambda _d: rule,
        ))
        dispatch = ParityDispatch(run_once)
        register_commands(dispatch, authorize, lambda _d: rule)
        app.register_blueprint(dispatch.blueprint)

        login = LoginManager(app)

        class Actor(UserMixin):
            def __init__(self, uid):
                self.id = int(uid)

        login.user_loader(Actor)

        @app.get("/synthetic-login/<int:uid>")
        def browser(uid):
            login_user(Actor(uid))
            return {"csrf": generate_csrf()}

        with app.test_client() as client:
            token = client.get(f"/synthetic-login/{user.id}").json["csrf"]
            yield dict(
                client=client, token=token, app=app, item=item, dictation=dictation,
                recording=recording, media=media, original=original, asr_rev=asr_rev,
                head=head, audio=audio, context=context, released=released, user=user,
                state=state, book=book,
            )


def _post(w, body, *, version=0, key=None, csrf=True, if_match=True):
    headers = {"Idempotency-Key": key or str(uuid4())}
    if csrf:
        headers["X-CSRFToken"] = w["token"]
    if if_match:
        headers["If-Match"] = f'"{w["item"].id}:{version}"'
    return w["client"].post(f"/api/clinic/v1/dictations/{w['item'].id}/archive", json=body, headers=headers)


def _snapshot(w):
    dictation = db.session.get(ClinicDictation, w["item"].id, populate_existing=True)
    item = db.session.get(workflow.ClinicWorkflowDictation, w["item"].id, populate_existing=True)
    recording = db.session.get(Recording, w["recording"].id, populate_existing=True)
    revs = [
        (r.id, r.kind, r.parent_revision_id, r.body, r.normalised_text_hash)
        for r in revisions.history(dictation)
    ]
    entry = db.session.get(ClinicArchiveEntry, w["item"].id, populate_existing=True)
    entry_snap = None if entry is None else (entry.archived_at, entry.version, entry.legal_hold, entry.release_id)
    return {
        "media_sha256": hashlib.sha256(w["media"].read_bytes()).hexdigest(),
        "workflow_status": item.status,
        "retention_archived_at": recording.retention_archived_at,
        "archive_revision": recording.archive_revision,
        "revisions": revs,
        "releases": [(r.id, r.text_hash) for r in ClinicRelease.query.filter_by(dictation_id=dictation.id).all()],
        "history": routes_history._history(dictation),
        "archive_entry": entry_snap,
        "archive_events": ClinicArchiveEvent.query.filter_by(recording_id=recording.id).count(),
    }


def test_cancel_delete_request_writes_no_receipt_audit_or_state(world):
    w = world
    before = _snapshot(w)
    res = _post(w, {"action": DELETE, "confirm": False}, version=0)
    assert res.status_code == 200
    data = res.json
    assert data["applied"] is False
    assert data["requested"] == DELETE
    assert data["disposition"] is None
    assert data["physically_deleted"] is False
    assert data["retention_changed"] is False
    assert data["dry_run"]["would_delete"] is False
    assert data["status"] == workflow.DONE
    assert data["version"] == 0
    assert data["etag"] == f'"{w["item"].id}:0"'

    assert ClinicParityReceipt.query.count() == 0
    assert ClinicDeletionRequest.query.count() == 0
    assert AuditEvent.query.count() == 0
    assert ClinicArchiveEntry.query.count() == 0
    assert db.session.get(ClinicDictation, w["item"].id, populate_existing=True).version_counter == 0
    assert _snapshot(w) == before


@pytest.mark.parametrize("archived_first", [False, True], ids=["active", "archived"])
def test_confirmed_delete_request_preserves_bytes_revisions_archive_and_history(world, monkeypatch, archived_first):
    w = world

    def forbid_physical_delete(*_args, **_kwargs):
        raise AssertionError("physical deletion must never run")

    monkeypatch.setattr(retention, "remove_recording_audio", forbid_physical_delete)
    monkeypatch.setattr(retention, "process_auto_deletion", forbid_physical_delete)

    version = 0
    if archived_first:
        arch = _post(w, {"action": ARCHIVE, "confirm": True}, version=version)
        assert arch.status_code == 200 and arch.json["status"] == workflow.TO_ARCHIVE
        version = 1

    before = _snapshot(w)
    audit_before = AuditEvent.query.count()
    key = str(uuid4())
    res = _post(w, {"action": DELETE, "confirm": True}, version=version, key=key)
    assert res.status_code == 200, res.json
    data = res.json
    assert data["applied"] is True
    assert data["requested"] == DELETE
    assert data["disposition"] == DELETION_REQUESTED
    assert data["deletion_request_id"]
    assert data["physically_deleted"] is False
    assert data["retention_changed"] is False
    assert data["dry_run"]["would_delete"] is False
    assert data["status"] == before["workflow_status"]
    assert data["version"] == version + 1
    assert data["etag"] == f'"{w["item"].id}:{version + 1}"'

    # Clinical bytes, revisions, archive status and history remain unchanged.
    assert _snapshot(w) == before
    assert ClinicDeletionRequest.query.filter_by(dictation_id=w["item"].id).count() == 1
    req = ClinicDeletionRequest.query.filter_by(request_id=key).one()
    assert req.disposition == DELETION_REQUESTED
    assert req.workflow_status == before["workflow_status"]

    # Append-only guard on ClinicDeletionRequest.
    req.disposition = "mutated"
    with pytest.raises(ArchivePolicyError, match="append_only"):
        db.session.flush()
    db.session.rollback()
    db.session.delete(db.session.get(ClinicDeletionRequest, req.id))
    with pytest.raises(ArchivePolicyError, match="append_only"):
        db.session.flush()
    db.session.rollback()

    # Audit has a deletion-request edit record and zero physical delete/archive records.
    new_audits = AuditEvent.query.order_by(AuditEvent.id).all()[audit_before:]
    assert len(new_audits) == 1
    assert new_audits[0].event == "recording.edited"
    assert new_audits[0].details == {"fields": [DELETION_REQUESTED]}
    assert AuditEvent.query.filter_by(event="recording.deleted").count() == 0
    assert AuditEvent.query.filter_by(event="recording.archived").count() == 0


def test_idempotency_conflict_and_stale_etag(world):
    w = world
    key = str(uuid4())
    # Stale ETag on confirm=False and confirm=True.
    assert _post(w, {"action": DELETE, "confirm": False}, version=9).status_code == 409
    assert _post(w, {"action": DELETE, "confirm": True}, version=9).status_code == 409
    assert ClinicDeletionRequest.query.count() == 0

    first = _post(w, {"action": DELETE, "confirm": True}, version=0, key=key)
    assert first.status_code == 200
    replay = _post(w, {"action": DELETE, "confirm": True}, version=0, key=key)
    assert replay.status_code == 200 and replay.json == first.json
    assert ClinicDeletionRequest.query.count() == 1
    assert AuditEvent.query.count() == 1

    # Reusing key with different action or confirm=False yields 409 IDEMPOTENCY_CONFLICT.
    assert _post(w, {"action": DELETE, "confirm": False}, version=0, key=key).json["code"] == "IDEMPOTENCY_CONFLICT"
    assert _post(w, {"action": ARCHIVE, "confirm": True}, version=0, key=key).json["code"] == "IDEMPOTENCY_CONFLICT"

    # A stale ETag with a fresh key yields 409 STALE_VERSION.
    stale = _post(w, {"action": DELETE, "confirm": True}, version=0)
    assert (stale.status_code, stale.json["code"]) == (409, "STALE_VERSION")
    assert stale.json["state"]["version"] == 1

    # Archiving remains a separate, still-working action at the updated version.
    arch = _post(w, {"action": ARCHIVE, "confirm": True}, version=1)
    assert arch.status_code == 200 and arch.json["status"] == workflow.TO_ARCHIVE


def test_audit_failure_rolls_back_deletion_request_and_receipt(world, monkeypatch):
    w = world
    monkeypatch.setattr(audit, "record_in_tx", lambda *_a, **_kw: None)
    res = _post(w, {"action": DELETE, "confirm": True}, version=0)
    assert res.status_code == 503
    assert ClinicDeletionRequest.query.count() == 0
    assert ClinicParityReceipt.query.count() == 0
    assert db.session.get(ClinicDictation, w["item"].id, populate_existing=True).version_counter == 0


def test_real_app_separate_grant_and_detail_capability(tmp_path):
    """Real app wiring: archive grant != delete grant, and detail exposes request_delete only when granted."""
    script = """
import json, sys, uuid
from pathlib import Path
from src.app import app, db
from src.clinic import pools, revisions, workflow
from src.clinic.identity import ClinicAccountPolicy
from src.clinic.models import ClinicAudioRevision, ClinicContextRevision, ClinicDictation, ClinicOriginal
from src.init_db import initialize_database
from src.models import AuditEvent, Recording, User

with app.app_context():
    initialize_database(app)
    admin = User(username="adm", email="adm@local.test", password="x", is_admin=True)
    author = User(username="aut", email="aut@local.test", password="x", is_admin=False)
    archiver = User(username="arc", email="arc@local.test", password="x", is_admin=False)
    deleter = User(username="del", email="del@local.test", password="x", is_admin=False)
    db.session.add_all([admin, author, archiver, deleter])
    db.session.flush()
    for u in (admin, author, archiver, deleter):
        db.session.add(ClinicAccountPolicy(user_id=u.id, status="active"))
    rec = Recording(user_id=author.id, title="Synthetic", status="COMPLETED", original_filename="s.wav")
    db.session.add(rec)
    db.session.flush()
    dic = ClinicDictation(recording_id=rec.id, author_user_id=author.id, version_counter=0)
    db.session.add(dic)
    db.session.flush()
    orig = ClinicOriginal(dictation_id=dic.id, content_hash="a" * 64, size_bytes=1, storage_key="k", receipt="r", origin_ref="o")
    db.session.add(orig)
    db.session.flush()
    revisions.add_human_revision(dic, None, "Text", author.id, original_version=orig)
    db.session.add(ClinicAudioRevision(dictation_id=dic.id, content_hash="b" * 64))
    db.session.add(ClinicContextRevision(dictation_id=dic.id, source_system="test", case_ref="F-1"))
    item = workflow.ClinicWorkflowDictation(id=dic.id, author_user_id=author.id, pool_id="p", status=workflow.DONE, audio_seconds=4)
    db.session.add(item)
    db.session.commit()

    pools.allow(archiver.id, "all", actor_id=admin.id, actions=("view", "edit", "archive"))
    pools.allow(deleter.id, "all", actor_id=admin.id, actions=("view", "edit", "delete"))
    did, uids = dic.id, {"author": author.id, "archiver": archiver.id, "deleter": deleter.id}

def login(uid):
    c = app.test_client()
    with c.session_transaction() as s:
        s["_user_id"] = str(uid)
        s["_fresh"] = True
    tok = c.get("/api/csrf-token").json["csrf_token"]
    return c, {"X-CSRFToken": tok}

out = {}
for name, uid in uids.items():
    c, h = login(uid)
    det = c.get(f"/api/clinic/v1/dictations/{did}/detail")
    out[name + "_actions"] = det.json["allowed_actions"]
    out[name + "_transitions"] = det.json["transitions"]
    res = c.post(
        f"/api/clinic/v1/dictations/{did}/archive",
        json={"action": "delete", "confirm": True},
        headers={**h, "Idempotency-Key": str(uuid.uuid4()), "If-Match": det.json["etag"]},
    )
    out[name + "_delete_status"] = res.status_code
    out[name + "_delete_body"] = res.get_json()

# Deleter cannot archive without archive grant, and detail reflects deletion_requested.
dc, dh = login(uids["deleter"])
det2 = dc.get(f"/api/clinic/v1/dictations/{did}/detail").json
out["deleter_after_deletion_requested"] = det2["deletion_requested"]
out["deleter_after_deletion_requested_at"] = det2["deletion_requested_at"]
out["deleter_archive_status"] = dc.post(
    f"/api/clinic/v1/dictations/{did}/archive",
    json={"action": "archive", "confirm": True},
    headers={**dh, "Idempotency-Key": str(uuid.uuid4()), "If-Match": det2["etag"]},
).status_code
with app.app_context():
    out["workflow_status"] = db.session.get(workflow.ClinicWorkflowDictation, did).status
    out["audit_events"] = [(e.event, e.details) for e in AuditEvent.query.filter_by(event="recording.edited").all()]
print("RESULT:" + json.dumps(out))
"""
    env = {
        **os.environ,
        "CLINIC_MODE": "1",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'app.db'}",
        "UPLOAD_FOLDER": str(tmp_path / "uploads"),
        "RATELIMIT_ENABLED": "false",
        "SPEAKR_SKIP_BACKGROUND_TASKS": "true",
    }
    done = subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stdout + done.stderr
    line = [l for l in done.stdout.splitlines() if l.startswith("RESULT:")][-1]
    res = __import__("json").loads(line[len("RESULT:"):])
    assert "request_delete" not in res["author_actions"]
    assert res["author_delete_status"] == 404
    assert "request_delete" not in res["archiver_actions"]
    assert "archive" in res["archiver_actions"]
    assert res["archiver_delete_status"] == 404
    assert "request_delete" in res["deleter_actions"]
    assert "request_delete" not in res["deleter_transitions"]
    assert "archive" not in res["deleter_actions"]
    assert res["deleter_delete_status"] == 200
    assert res["deleter_delete_body"]["disposition"] == DELETION_REQUESTED
    assert res["deleter_delete_body"]["status"] == workflow.DONE
    assert res["deleter_archive_status"] == 404
    assert res["workflow_status"] == workflow.DONE
    assert (["recording.edited", {"fields": [DELETION_REQUESTED]}] in res["audit_events"])
    assert res["deleter_after_deletion_requested"] is True
    assert res["deleter_after_deletion_requested_at"] is not None
