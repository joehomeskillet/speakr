"""w056: consented speaker embeddings -> author suggestions. Synthetic users and vectors only."""

import struct

import pytest
from flask import Flask

from src.clinic import speaker_client, speaker_pipeline as sp, voice_consent as vc
from src.database import db
from src.models import User, VoiceEmbeddingSpace

DIM = 4
STATEMENT = "Synthetische Einwilligung"
A = [1.0, 0.0, 0.0, 0.0]
B = [0.0, 1.0, 0.0, 0.0]


@pytest.fixture
def env():
    app = Flask(__name__)
    app.config.update(SQLALCHEMY_DATABASE_URI="sqlite://", TESTING=True)
    db.init_app(app)
    with app.app_context():
        db.create_all()
        vc.create_tables()
        users = [User(username=f"u{i}", email=f"u{i}@example.invalid") for i in range(3)]
        space = VoiceEmbeddingSpace(dimension=DIM, backend_fingerprint="speaker-service:test")
        other_space = VoiceEmbeddingSpace(dimension=DIM, backend_fingerprint="speaker-service:other")
        db.session.add_all(users + [space, other_space])
        db.session.commit()
        yield users, space, other_space
        db.session.rollback()


def _ref(vec, space_id):
    return vc.EmbeddingRef(struct.pack(f"<{len(vec)}f", *vec), len(vec), space_id)


def _consent(user):
    vc.grant_consent(actor_user_id=user.id, user_id=user.id, statement_text=STATEMENT)


def _enrol(user, vec, space):
    _consent(user) if not vc.has_active_consent(user.id) else None
    vc.enrol(user.id, _ref(vec, space.id), actor_user_id=user.id)


def _result(vec, space):
    return speaker_client.EmbeddingResult(space.id, "test", list(vec))


def _fake_embed(monkeypatch, vec, space, before=None):
    def fake(_path, timeout=0):
        if before:
            before()
        return _result(vec, space)
    monkeypatch.setattr(speaker_client, "embed_with_space", fake)


def test_single_suggestion_for_consented_user_needs_confirmation(env):
    (u0, u1, _), space, _ = env
    _enrol(u0, A, space)
    _enrol(u1, B, space)
    out = sp.suggest_for_embedding(_result(A, space), 10.0).to_dict()
    assert out["band"] == "single"
    assert [s["user_id"] for s in out["suggestions"]] == [u0.id]
    assert out["requires_confirmation"] is True


def test_unconsented_and_revoked_users_are_not_candidates(env):
    (u0, u1, u2), space, _ = env
    _enrol(u0, A, space)
    _enrol(u1, A, space)
    vc.revoke_consent(actor_user_id=u1.id, user_id=u1.id)
    # u2 never consented: nothing to enrol, nothing stored
    with pytest.raises(vc.VoiceConsentError) as exc:
        vc.enrol(u2.id, _ref(A, space.id), actor_user_id=u2.id)
    assert exc.value.code == "consent_required"
    out = sp.suggest_for_embedding(_result(A, space), 10.0)
    assert [s.user_id for s in out.suggestions] == [u0.id]


def test_other_embedding_space_is_ignored(env):
    (u0, _, _), space, other = env
    _enrol(u0, A, other)
    out = sp.suggest_for_embedding(_result(A, space), 10.0)
    assert out.band == "none" and out.reason == "no_candidates"


def test_short_audio_rejected_without_calling_service(env, monkeypatch):
    (u0, _, _), space, _ = env
    _enrol(u0, A, space)

    def boom(*_a, **_k):
        raise AssertionError("service must not be called")
    monkeypatch.setattr(speaker_client, "embed_with_space", boom)
    out = sp.suggest_author_for_wav("x.wav", 1.0)
    assert out["band"] == "none" and out["reason"] == "speech_too_short"
    assert out["suggestions"] == []


def test_consent_withdrawn_before_publish_drops_suggestion(env, monkeypatch):
    (u0, _, _), space, _ = env
    _enrol(u0, A, space)
    real = sp._load_candidates

    def load_then_revoke(*a, **k):
        out = real(*a, **k)
        if vc.has_active_consent(u0.id):
            vc.revoke_consent(actor_user_id=u0.id, user_id=u0.id)
        return out
    monkeypatch.setattr(sp, "_load_candidates", load_then_revoke)
    out = sp.suggest_for_embedding(_result(A, space), 10.0)
    assert out.suggestions == () and out.band == "none"


def test_enrol_own_voice_stores_embedding_in_model_space(env, monkeypatch):
    (u0, _, _), space, _ = env
    _consent(u0)
    _fake_embed(monkeypatch, A, space)
    sp.enrol_own_voice(u0.id, "x.wav")
    row = vc.ClinicVoiceEmbedding.query.filter_by(user_id=u0.id).one()
    assert row.space_id == space.id and row.dimension == DIM


def test_enrol_refused_without_consent(env, monkeypatch):
    (u0, _, _), space, _ = env
    _fake_embed(monkeypatch, A, space)
    with pytest.raises(vc.VoiceConsentError) as exc:
        sp.enrol_own_voice(u0.id, "x.wav")
    assert exc.value.code == "consent_required"
    assert vc.ClinicVoiceEmbedding.query.count() == 0


def test_withdrawal_during_processing_prevents_late_enrolment(env, monkeypatch):
    (u0, _, _), space, _ = env
    _consent(u0)
    _fake_embed(monkeypatch, A, space,
                before=lambda: vc.revoke_consent(actor_user_id=u0.id, user_id=u0.id))
    with pytest.raises(vc.VoiceConsentError) as exc:
        sp.enrol_own_voice(u0.id, "x.wav")
    assert exc.value.code == "stale_consent"
    assert vc.ClinicVoiceEmbedding.query.count() == 0


def test_stale_expected_consent_version_rejected(env, monkeypatch):
    (u0, _, _), space, _ = env
    _consent(u0)
    _fake_embed(monkeypatch, A, space)
    with pytest.raises(vc.VoiceConsentError) as exc:
        sp.enrol_own_voice(u0.id, "x.wav", expected_consent_id=0)
    assert exc.value.code == "stale_consent"
    assert vc.ClinicVoiceEmbedding.query.count() == 0


def test_service_failure_is_reported_and_stores_nothing(env, monkeypatch):
    (u0, _, _), space, _ = env
    _consent(u0)

    def down(*_a, **_k):
        raise speaker_client.SpeakerServiceUnavailable("down")
    monkeypatch.setattr(speaker_client, "embed_with_space", down)
    with pytest.raises(sp.SpeakerPipelineError) as exc:
        sp.enrol_own_voice(u0.id, "x.wav")
    assert exc.value.code == "service_unavailable"
    with pytest.raises(sp.SpeakerPipelineError):
        sp.suggest_author_for_wav("x.wav", 10.0)
    assert vc.ClinicVoiceEmbedding.query.count() == 0
    # retry after recovery succeeds
    _fake_embed(monkeypatch, A, space)
    sp.enrol_own_voice(u0.id, "x.wav")
    assert vc.ClinicVoiceEmbedding.query.count() == 1


def test_suggest_author_for_wav_end_to_end_creates_no_profile(env, monkeypatch):
    from src.models import Speaker
    (u0, _, _), space, _ = env
    _enrol(u0, A, space)
    _fake_embed(monkeypatch, A, space)
    out = sp.suggest_author_for_wav("x.wav", 12.0)
    assert out["band"] == "single" and out["requires_confirmation"] is True
    assert Speaker.query.count() == 0
