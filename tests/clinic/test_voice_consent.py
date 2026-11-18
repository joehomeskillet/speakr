"""Voice profile consent: append-only history, revocation deletes embeddings, enrolment guard."""

import struct

import pytest
from sqlalchemy import text

from src.app import app, db
from src.clinic import voice_consent as vc
from src.clinic.voice_consent import (
    ClinicVoiceConsent, ClinicVoiceConsentAudit, ClinicVoiceEmbedding, EmbeddingRef, VoiceConsentError,
)
from src.models.voice import VoiceEmbeddingSpace

TEXT = "Ich willige in die Erstellung eines Stimmprofils zur Autorenerkennung ein."


def _vec(dim=4, base=0.5):
    return struct.pack("<%df" % dim, *([base] * dim))


@pytest.fixture
def spaces():
    created = []
    with app.app_context():
        vc.create_tables()
        for dim in (4, 8):
            space = VoiceEmbeddingSpace(dimension=dim)
            db.session.add(space)
            db.session.commit()
            created.append((space.id, dim))
    yield created
    with app.app_context():
        db.session.rollback()
        VoiceEmbeddingSpace.query.filter(VoiceEmbeddingSpace.id.in_([s for s, _ in created])).delete(
            synchronize_session=False)
        db.session.commit()


def _ref(space, base=0.5):
    space_id, dim = space
    return EmbeddingRef(vector=_vec(dim, base), dimension=dim, space_id=space_id)


def test_state_none_without_history(world, spaces):
    with app.app_context():
        state = vc.consent_state(world["user_a"])
        assert (state.state, state.granted_at, state.revoked_at) == ("none", None, None)


def test_grant_records_who_when_purpose_and_hash(world, spaces):
    with app.app_context():
        vc.grant_consent(actor_user_id=world["user_a"], user_id=world["user_a"], statement_text=TEXT)
        (entry,) = vc.consent_history(world["user_a"])
        assert entry["actor_user_id"] == world["user_a"]
        assert entry["purpose"] == "author_recognition"
        assert entry["status"] == "granted"
        assert len(entry["statement_hash"]) == 64
        assert TEXT not in str(entry)
        assert entry["created_at"] is not None
        state = vc.consent_state(world["user_a"])
        assert state.state == "granted" and state.granted_at is not None and state.revoked_at is None


def test_only_the_user_can_give_or_revoke_consent(world, spaces):
    with app.app_context():
        with pytest.raises(VoiceConsentError) as err:
            vc.grant_consent(actor_user_id=world["user_admin"], user_id=world["user_a"], statement_text=TEXT)
        assert err.value.code == "not_self"
        vc.grant_consent(actor_user_id=world["user_a"], user_id=world["user_a"], statement_text=TEXT)
        with pytest.raises(VoiceConsentError) as err:
            vc.revoke_consent(actor_user_id=world["user_admin"], user_id=world["user_a"])
        assert err.value.code == "not_self"
        assert vc.has_active_consent(world["user_a"])


def test_grant_needs_statement_and_is_not_repeated(world, spaces):
    with app.app_context():
        uid = world["user_a"]
        for bad in ("", "   ", None):
            with pytest.raises(VoiceConsentError) as err:
                vc.grant_consent(actor_user_id=uid, user_id=uid, statement_text=bad)
            assert err.value.code == "statement_required"
        vc.grant_consent(actor_user_id=uid, user_id=uid, statement_text=TEXT)
        with pytest.raises(VoiceConsentError) as err:
            vc.grant_consent(actor_user_id=uid, user_id=uid, statement_text=TEXT)
        assert err.value.code == "already_granted"
        assert len(vc.consent_history(uid)) == 1


def test_revoke_without_consent_refused(world, spaces):
    with app.app_context():
        with pytest.raises(VoiceConsentError) as err:
            vc.revoke_consent(actor_user_id=world["user_a"], user_id=world["user_a"])
        assert err.value.code == "no_active_consent"


def test_consent_rows_are_append_only(world, spaces):
    with app.app_context():
        uid = world["user_a"]
        vc.grant_consent(actor_user_id=uid, user_id=uid, statement_text=TEXT)
        row = ClinicVoiceConsent.query.filter_by(user_id=uid).one()
        row.status = "revoked"
        with pytest.raises(VoiceConsentError) as err:
            db.session.commit()
        assert err.value.code == "append_only"
        db.session.rollback()
        row = ClinicVoiceConsent.query.filter_by(user_id=uid).one()
        db.session.delete(row)
        with pytest.raises(VoiceConsentError):
            db.session.commit()
        db.session.rollback()
        assert ClinicVoiceConsent.query.filter_by(user_id=uid).one().status == "granted"


def test_enrol_refused_without_consent(world, spaces):
    with app.app_context():
        for _ in range(2):  # never consented, then revoked
            with pytest.raises(VoiceConsentError) as err:
                vc.enrol(world["user_a"], _ref(spaces[0]))
            assert err.value.code == "consent_required"
            assert ClinicVoiceEmbedding.query.count() == 0
            uid = world["user_a"]
            vc.grant_consent(actor_user_id=uid, user_id=uid, statement_text=TEXT)
            vc.revoke_consent(actor_user_id=uid, user_id=uid)


def test_enrol_takes_only_existing_users(world, spaces):
    with app.app_context():
        for bad in (0, -1, True, "7", None, 99999999):
            with pytest.raises(VoiceConsentError):
                vc.enrol(bad, _ref(spaces[0]))
        assert ClinicVoiceEmbedding.query.count() == 0


def test_enrol_validates_embedding(world, spaces):
    with app.app_context():
        uid = world["user_a"]
        vc.grant_consent(actor_user_id=uid, user_id=uid, statement_text=TEXT)
        space_id, dim = spaces[0]
        for bad in (
            "raw", None,
            EmbeddingRef(vector=_vec(dim), dimension=dim + 1, space_id=space_id),
            EmbeddingRef(vector=b"", dimension=0, space_id=space_id),
            EmbeddingRef(vector=_vec(dim), dimension=dim, space_id=99999999),
        ):
            with pytest.raises(VoiceConsentError):
                vc.enrol(uid, bad)
        assert vc.embedding_count(uid) == 0


def test_revoke_deletes_embeddings_in_all_spaces(world, spaces):
    with app.app_context():
        a, b = world["user_a"], world["user_b"]
        for uid in (a, b):
            vc.grant_consent(actor_user_id=uid, user_id=uid, statement_text=TEXT)
        for space in spaces:
            vc.enrol(a, _ref(space))
        vc.enrol(a, EmbeddingRef(vector=_vec(4), dimension=4))  # legacy space
        vc.enrol(b, _ref(spaces[0], 0.25))
        assert vc.embedding_count(a) == 3

        deleted = vc.revoke_consent(actor_user_id=a, user_id=a)

        assert deleted == 3
        assert vc.embedding_count(a) == 0
        assert ClinicVoiceEmbedding.query.filter_by(user_id=a).count() == 0
        assert db.session.execute(
            text("SELECT COUNT(*) FROM clinic_voice_embedding WHERE user_id = :u"), {"u": a}
        ).scalar() == 0
        # other user untouched, spaces untouched
        assert vc.embedding_count(b) == 1
        assert VoiceEmbeddingSpace.query.filter(VoiceEmbeddingSpace.id.in_([s for s, _ in spaces])).count() == 2
        assert vc.consent_state(a).state == "revoked"


def test_revoke_audit_entry_has_count_and_links_revocation(world, spaces):
    with app.app_context():
        uid = world["user_a"]
        vc.grant_consent(actor_user_id=uid, user_id=uid, statement_text=TEXT)
        vc.enrol(uid, _ref(spaces[0]))
        vc.enrol(uid, _ref(spaces[1]))
        vc.revoke_consent(actor_user_id=uid, user_id=uid)
        entries = vc.audit_entries(uid)
        assert [e["action"] for e in entries] == ["enrolled", "enrolled", "embeddings_deleted"]
        deletion = entries[-1]
        assert deletion["vector_count"] == 2
        revoke_row = ClinicVoiceConsent.query.filter_by(user_id=uid, status="revoked").one()
        assert deletion["consent_id"] == revoke_row.id


def test_audit_rows_are_append_only(world, spaces):
    with app.app_context():
        uid = world["user_a"]
        vc.grant_consent(actor_user_id=uid, user_id=uid, statement_text=TEXT)
        vc.enrol(uid, _ref(spaces[0]))
        row = ClinicVoiceConsentAudit.query.filter_by(user_id=uid).first()
        row.vector_count = 0
        with pytest.raises(VoiceConsentError):
            db.session.commit()
        db.session.rollback()


def test_history_complete_and_survives_revocation(world, spaces):
    with app.app_context():
        uid = world["user_a"]
        statements = ["v1 " + TEXT, "v2 " + TEXT]
        for statement in statements:
            vc.grant_consent(actor_user_id=uid, user_id=uid, statement_text=statement)
            vc.enrol(uid, _ref(spaces[0]))
            vc.revoke_consent(actor_user_id=uid, user_id=uid)
        history = vc.consent_history(uid)
        assert [h["status"] for h in history] == ["granted", "revoked", "granted", "revoked"]
        assert [h["id"] for h in history] == sorted(h["id"] for h in history)
        assert history[0]["statement_hash"] != history[2]["statement_hash"]
        assert history[1]["statement_hash"] is None
        state = vc.consent_state(uid)
        assert state.state == "revoked"
        assert state.granted_at is not None and state.revoked_at is not None
        # after re-grant, enrolment works again and state is granted
        vc.grant_consent(actor_user_id=uid, user_id=uid, statement_text=TEXT)
        vc.enrol(uid, _ref(spaces[1]))
        assert vc.consent_state(uid).state == "granted"
        assert len(vc.consent_history(uid)) == 5


def test_consent_is_per_user(world, spaces):
    with app.app_context():
        a, b = world["user_a"], world["user_b"]
        vc.grant_consent(actor_user_id=a, user_id=a, statement_text=TEXT)
        assert vc.consent_state(b).state == "none"
        with pytest.raises(VoiceConsentError):
            vc.enrol(b, _ref(spaces[0]))
