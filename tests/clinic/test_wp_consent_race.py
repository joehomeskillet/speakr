"""Concurrent writes through the clinic consent service, using separate sessions."""

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Event

import pytest
from sqlalchemy import event

from src.app import app, db
from src.clinic import voice_consent as vc

REF = vc.EmbeddingRef(vector=b"\x00\x00\x00\x3f", dimension=1)


def _grant(uid, **kwargs):
    return vc.grant_consent(actor_user_id=uid, user_id=uid, statement_text="Stimmprofil", **kwargs).id


def _revoke(uid, **kwargs):
    return vc.revoke_consent(actor_user_id=uid, user_id=uid, **kwargs)


def _enrol(uid):
    return vc.enrol(uid, REF).id


@pytest.mark.parametrize(
    ("first", "second", "second_error", "statuses", "deleted"),
    [
        (_grant, _grant, "already_granted", ["granted"], None),
        (_revoke, _enrol, "consent_required", ["granted", "revoked"], 1),
        (_enrol, _revoke, None, ["granted", "revoked"], 2),
    ],
    ids=["duplicate-grant", "revoke-wins", "enrol-wins"],
)
def test_concurrent_consent_writes(world, first, second, second_error, statuses, deleted):
    uid = world["user_a"]
    if deleted is not None:
        with app.app_context():
            _grant(uid)
            _enrol(uid)

    flushed, started, release = Event(), Event(), Event()

    def run_first():
        def pause_after_flush(*_):
            flushed.set()
            assert release.wait(10), "test did not release the first transaction"

        with app.app_context():
            event.listen(db.session(), "after_flush", pause_after_flush, once=True)
            return first(uid)

    def run_second():
        with app.app_context():
            conn = db.session.connection()
            event.listen(conn, "before_cursor_execute", lambda *_: started.set(), once=True)
            try:
                return second(uid)
            except vc.VoiceConsentError as exc:
                return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(run_first)
        try:
            assert flushed.wait(10), "first operation did not reach flush"
            waiter = pool.submit(run_second)
            assert started.wait(10), "second operation did not reach the database"
            with pytest.raises(TimeoutError):
                waiter.result(timeout=0.25)
        finally:
            release.set()
        first_result, second_result = writer.result(timeout=10), waiter.result(timeout=10)

    assert second_result == (second_error if second_error else deleted)
    with app.app_context():
        history = vc.consent_history(uid)
        assert [row["status"] for row in history] == statuses
        if first is _revoke:
            assert first_result == deleted
        assert vc.embedding_count(uid) == 0
        audit = vc.audit_entries(uid)
        assert [row["action"] for row in audit] == (
            ["enrolled"] * deleted + ["embeddings_deleted"] if deleted is not None else []
        )
        if deleted is not None:
            assert (audit[-1]["vector_count"], audit[-1]["consent_id"]) == (deleted, history[-1]["id"])


def test_stale_retries_and_foreign_identities(world):
    uid = world["user_a"]
    with app.app_context():
        with pytest.raises(vc.VoiceConsentError, match="positive int"):
            vc.enrol("patient:123", REF)
        original = _grant(uid, expected_consent_id=0)
        for actor in (world["user_admin"], float(uid), None):
            with pytest.raises(vc.VoiceConsentError) as error:
                vc.enrol(uid, REF, actor_user_id=actor)
            assert error.value.code == "not_self"
        assert _revoke(uid, expected_consent_id=original) == 0
        with pytest.raises(vc.VoiceConsentError, match="stale_consent"):
            _grant(uid, expected_consent_id=0)
        current = _grant(uid, expected_consent_id=vc.consent_history(uid)[-1]["id"])
        with pytest.raises(vc.VoiceConsentError, match="stale_consent"):
            vc.enrol(uid, REF, actor_user_id=uid, expected_consent_id=original)
        with pytest.raises(vc.VoiceConsentError, match="stale_consent"):
            _revoke(uid, expected_consent_id=original)
        assert vc.embedding_count(uid) == 0
        vc.enrol(uid, REF, actor_user_id=uid, expected_consent_id=current)
        assert vc.embedding_count(uid) == 1


def test_revocation_rolls_back_when_audit_fails(world):
    uid = world["user_a"]
    with app.app_context():
        version = _grant(uid)
        _enrol(uid)

    def fail_audit(*_):
        raise RuntimeError("synthetic audit failure")

    with app.app_context():
        event.listen(vc.ClinicVoiceConsentAudit, "before_insert", fail_audit)
        try:
            with pytest.raises(RuntimeError, match="synthetic audit failure"):
                _revoke(uid, expected_consent_id=version)
        finally:
            event.remove(vc.ClinicVoiceConsentAudit, "before_insert", fail_audit)

        assert [row["id"] for row in vc.consent_history(uid)] == [version]
        assert [row["action"] for row in vc.audit_entries(uid)] == ["enrolled"]
        assert vc.embedding_count(uid) == 1
        assert _revoke(uid, expected_consent_id=version) == 1
        assert vc.embedding_count(uid) == 0
