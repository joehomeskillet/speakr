"""Guarded admin metadata access and direct content opening (w306).

Tests run against the shared clinic test database with clinic mode on.
Synthetic rows only; no real recordings, patient data or secrets.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import pytest
from sqlalchemy import func, select

from src.app import app, db
from src.clinic.admin_dictation_access import (
    AdminDictationAccessError,
    can_open_content,
    list_dictations,
    open_content,
)
from src.clinic.admin_dictations import AdminDictationsQuery
from src.clinic.identity import ClinicAccountPolicy, create_tables as _create_identity_tables
from src.models import AuditEvent, Recording, User
from src.services import audit


@pytest.fixture(autouse=True)
def _clinic_mode_and_testing(monkeypatch):
    """Clinic mode on, strict audit details checking, CSRF off."""
    monkeypatch.setenv("CLINIC_MODE", "1")
    monkeypatch.setitem(app.config, "TESTING", True)
    monkeypatch.setitem(app.config, "WTF_CSRF_ENABLED", False)
    with app.app_context():
        _create_identity_tables()
    yield


@pytest.fixture(autouse=True)
def _audit_state():
    """Empty audit queue and de-duplication map around every test."""
    audit.flush()
    audit.reset_memory()
    yield
    audit.flush()
    audit.reset_memory()


@pytest.fixture
def audit_baseline(_audit_state) -> int:
    """Exclude earlier audit rows that may refer to reused IDs."""
    with app.app_context():
        return db.session.execute(select(func.max(AuditEvent.id))).scalar_one() or 0


def _suffix() -> str:
    return uuid.uuid4().hex[:8]


def _user(name: str, *, is_admin: bool = False) -> User:
    tag = _suffix()
    user = User(
        username=f"adacc_{name}_{tag}",
        email=f"{name}_{tag}@example.invalid",
        password="x",
        is_admin=is_admin,
    )
    db.session.add(user)
    db.session.commit()
    return user


def _policy(user: User, *, status: str = "active", is_identity_admin: bool = False) -> ClinicAccountPolicy:
    policy_row = ClinicAccountPolicy(
        user_id=user.id,
        status=status,
        is_identity_admin=is_identity_admin,
    )
    db.session.add(policy_row)
    db.session.commit()
    return policy_row


def _recording(owner: User, title: str = "Dictation") -> Recording:
    rec = Recording(
        user_id=owner.id,
        title=title,
        status="COMPLETED",
        original_filename="secret-filename.wav",
        transcription="SECRET-TRANSCRIPT-TEXT",
    )
    db.session.add(rec)
    db.session.commit()
    return rec


@dataclass(frozen=True)
class _Caller:
    """Anonymous-like user for the metadata-list guard."""

    is_authenticated: bool = False
    is_admin: bool = False
    id: int | None = None


def test_admin_metadata_list_includes_foreign_recording():
    with app.app_context():
        admin = _user("admin", is_admin=True)
        _policy(admin)
        author = _user("author")
        _policy(author)
        foreign = _recording(author, "Foreign dictation")

        page = list_dictations(db.session, admin, AdminDictationsQuery(page_size=50))
        recording_ids = {row.recording_id for row in page.items}
        assert foreign.id in recording_ids
        # The metadata allowlist must not carry clinical text or filenames.
        payload = page.as_dict()
        assert "SECRET-TRANSCRIPT-TEXT" not in str(payload)
        assert "secret-filename.wav" not in str(payload)


@pytest.mark.parametrize(
    "role",
    [
        "author",
        "typist",
        "statistics_only",
        "team_admin_only",
        "revoked_admin",
    ],
)
def test_metadata_list_rejects_non_admin_callers(role: str):
    with app.app_context():
        is_admin = role == "revoked_admin"
        is_identity_admin = role == "team_admin_only"
        caller = _user(role, is_admin=is_admin)
        if role == "revoked_admin":
            _policy(caller, status="disabled")
        else:
            _policy(caller, status="active", is_identity_admin=is_identity_admin)
        author = _user("author_target")
        _policy(author)
        _recording(author)

        with pytest.raises(AdminDictationAccessError) as caught:
            list_dictations(db.session, caller, AdminDictationsQuery())
        assert caught.value.code == "admin_only"


def test_metadata_list_rejects_anonymous_caller():
    with app.app_context():
        author = _user("author")
        _policy(author)
        _recording(author)
        anonymous = _Caller()

        with pytest.raises(AdminDictationAccessError) as caught:
            list_dictations(db.session, anonymous, AdminDictationsQuery())  # type: ignore[arg-type]
        assert caught.value.code == "admin_only"


def test_admin_direct_audio_access_on_foreign_recording(audit_baseline: int):
    with app.app_context():
        admin = _user("admin", is_admin=True)
        _policy(admin)
        author = _user("author")
        _policy(author)
        foreign = _recording(author, "Foreign audio")

        decision, recording = open_content(db.session, admin, foreign.id, "audio")
        assert decision.allowed is True
        assert decision.reason == "admin_full"
        assert recording is foreign

        audit.flush()
        rows = (
            AuditEvent.query.filter_by(
                event="recording.media.played",
                actor_user_id=admin.id,
                recording_id=foreign.id,
            )
            .filter(AuditEvent.id > audit_baseline)
            .order_by(AuditEvent.id)
            .all()
        )
        assert len(rows) == 1
        assert rows[0].recording_owner_id == author.id
        assert rows[0].outcome == "success"
        assert rows[0].dedup_key is None
        assert rows[0].details is None


def test_admin_direct_transcript_access_on_foreign_recording(audit_baseline: int):
    with app.app_context():
        admin = _user("admin", is_admin=True)
        _policy(admin)
        author = _user("author")
        _policy(author)
        foreign = _recording(author, "Foreign transcript")

        decision, recording = open_content(db.session, admin, foreign.id, "transcript")
        assert decision.allowed is True
        assert decision.reason == "admin_full"
        assert recording is foreign

        audit.flush()
        row = AuditEvent.query.filter_by(
            event="recording.content.read",
            actor_user_id=admin.id,
            recording_id=foreign.id,
        ).filter(AuditEvent.id > audit_baseline).one()
        assert row.recording_owner_id == author.id
        assert row.outcome == "success"
        assert row.dedup_key is None
        assert row.details == {"part": "transcript"}


def test_admin_openings_are_not_deduplicated(audit_baseline: int):
    with app.app_context():
        admin = _user("admin", is_admin=True)
        _policy(admin)
        author = _user("author")
        _policy(author)
        foreign = _recording(author)

        open_content(db.session, admin, foreign.id, "audio")
        open_content(db.session, admin, foreign.id, "audio")
        audit.flush()
        rows = AuditEvent.query.filter_by(
            event="recording.media.played",
            actor_user_id=admin.id,
            recording_id=foreign.id,
        ).filter(AuditEvent.id > audit_baseline).all()
        assert len(rows) == 2


def test_audit_row_for_admin_opening_excludes_clinical_text_and_filenames(audit_baseline: int):
    with app.app_context():
        admin = _user("admin", is_admin=True)
        _policy(admin)
        author = _user("author")
        _policy(author)
        foreign = _recording(author, "Clinical title")

        open_content(db.session, admin, foreign.id, "transcript")
        audit.flush()
        row = AuditEvent.query.filter_by(
            event="recording.content.read",
            actor_user_id=admin.id,
            recording_id=foreign.id,
        ).filter(AuditEvent.id > audit_baseline).one()
        payload = row.to_dict()
        assert "Clinical title" not in str(payload)
        assert "secret-filename.wav" not in str(payload)
        assert "SECRET-TRANSCRIPT-TEXT" not in str(payload)


def test_non_admin_audio_keeps_existing_policy(audit_baseline: int):
    with app.app_context():
        author = _user("author")
        _policy(author)
        own = _recording(author, "Own dictation")
        other = _user("other")
        _policy(other)
        foreign = _recording(other, "Foreign dictation")

        own_decision, _ = open_content(db.session, author, own.id, "audio")
        assert own_decision.allowed is True
        assert own_decision.reason == "own_default"

        foreign_decision, _ = open_content(db.session, author, foreign.id, "audio")
        assert foreign_decision.allowed is False
        assert foreign_decision.reason == "no_grant"

        # Non-admin openings are not audit-logged by this module.
        audit.flush()
        assert (
            AuditEvent.query.filter(
                AuditEvent.id > audit_baseline,
                AuditEvent.actor_user_id == author.id,
                AuditEvent.recording_id.in_([own.id, foreign.id]),
            ).count()
            == 0
        )


def test_revoked_admin_content_opening_uses_existing_policy(audit_baseline: int):
    with app.app_context():
        revoked = _user("revoked", is_admin=True)
        _policy(revoked, status="disabled")
        author = _user("author")
        _policy(author)
        foreign = _recording(author)

        decision, _ = open_content(db.session, revoked, foreign.id, "audio")
        assert decision.allowed is False

        audit.flush()
        assert (
            AuditEvent.query.filter_by(actor_user_id=revoked.id)
            .filter(AuditEvent.id > audit_baseline)
            .count()
            == 0
        )


def test_current_role_recheck_after_revocation():
    with app.app_context():
        admin = _user("admin", is_admin=True)
        active_policy = _policy(admin, status="active")
        author = _user("author")
        _policy(author)
        foreign = _recording(author)

        assert open_content(db.session, admin, foreign.id, "audio")[0].allowed is True

        active_policy.status = "disabled"
        db.session.commit()

        decision, _ = open_content(db.session, admin, foreign.id, "audio")
        assert decision.allowed is False


def test_open_content_unknown_part_rejects_before_any_lookup():
    with app.app_context():
        admin = _user("admin", is_admin=True)
        _policy(admin)

        with pytest.raises(AdminDictationAccessError) as caught:
            open_content(db.session, admin, 1, "notes")
        assert caught.value.code == "unknown_part"


def test_open_content_missing_recording_returns_not_found():
    with app.app_context():
        admin = _user("admin", is_admin=True)
        _policy(admin)

        decision, recording = open_content(db.session, admin, 999_999, "audio")
        assert decision.allowed is False
        assert decision.reason == "not_found"
        assert recording is None


def test_can_open_content_does_not_write_audit_row(audit_baseline: int):
    with app.app_context():
        admin = _user("admin", is_admin=True)
        _policy(admin)
        author = _user("author")
        _policy(author)
        foreign = _recording(author)

        decision = can_open_content(db.session, admin, foreign.id, "transcript")
        assert decision.allowed is True
        audit.flush()
        assert (
            AuditEvent.query.filter_by(
                actor_user_id=admin.id, recording_id=foreign.id
            )
            .filter(AuditEvent.id > audit_baseline)
            .count()
            == 0
        )


def test_access_is_inert_outside_clinic_mode(monkeypatch):
    with app.app_context():
        admin = _user("admin", is_admin=True)
        _policy(admin)
        monkeypatch.delenv("CLINIC_MODE", raising=False)

        with pytest.raises(AdminDictationAccessError) as caught:
            list_dictations(db.session, admin, AdminDictationsQuery())
        assert caught.value.code == "clinic_mode_off"

        with pytest.raises(AdminDictationAccessError) as caught:
            open_content(db.session, admin, 1, "audio")
        assert caught.value.code == "clinic_mode_off"
