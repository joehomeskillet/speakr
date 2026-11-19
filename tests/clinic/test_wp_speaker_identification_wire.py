"""w121: clinic wiring of speaker identification to speaker_pipeline. Synthetic data only."""

import struct

import pytest
from flask import Flask

from src.clinic import speaker_client, voice_consent as vc
from src.database import db
from src.models import Speaker, User, VoiceEmbeddingSpace
from src.services import speaker_identification as si

DIM = 4
A = [1.0, 0.0, 0.0, 0.0]
B = [0.0, 1.0, 0.0, 0.0]


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("CLINIC_MODE", "1")
    app = Flask(__name__)
    app.config.update(SQLALCHEMY_DATABASE_URI="sqlite://", TESTING=True)
    db.init_app(app)
    with app.app_context():
        db.create_all()
        vc.create_tables()
        users = [User(username=f"u{i}", email=f"u{i}@example.invalid") for i in range(3)]
        space = VoiceEmbeddingSpace(dimension=DIM, backend_fingerprint="speaker-service:test")
        db.session.add_all(users + [space])
        db.session.commit()
        yield users, space
        db.session.rollback()


def _enrol(user, vec, space):
    if not vc.has_active_consent(user.id):
        vc.grant_consent(actor_user_id=user.id, user_id=user.id, statement_text="Synthetische Einwilligung")
    ref = vc.EmbeddingRef(struct.pack(f"<{len(vec)}f", *vec), len(vec), space.id)
    vc.enrol(user.id, ref, actor_user_id=user.id)


def _fake_embed(monkeypatch, vec, space):
    monkeypatch.setattr(
        speaker_client, "embed_with_space",
        lambda _p, timeout=0: speaker_client.EmbeddingResult(space.id, "test", list(vec)),
    )


def test_consented_user_is_suggested_with_score_and_needs_confirmation(env, monkeypatch):
    (u0, u1, _), space = env
    _enrol(u0, A, space)
    _enrol(u1, B, space)
    _fake_embed(monkeypatch, A, space)
    out = si.identify_author_suggestions("x.wav", 12.0, actor_user_id=u2_id(env))
    assert out["requires_confirmation"] is True
    assert [s["user_id"] for s in out["suggestions"]] == [u0.id]
    assert isinstance(out["suggestions"][0]["score"], float)
    assert Speaker.query.count() == 0


def u2_id(env):
    return env[0][2].id


def test_unconsented_and_revoked_users_never_suggested(env, monkeypatch):
    (u0, u1, _), space = env
    _enrol(u0, A, space)
    _enrol(u1, A, space)
    vc.revoke_consent(actor_user_id=u1.id, user_id=u1.id)
    _fake_embed(monkeypatch, A, space)
    out = si.identify_author_suggestions("x.wav", 12.0, actor_user_id=u2_id(env))
    assert [s["user_id"] for s in out["suggestions"]] == [u0.id]


def test_output_exposes_no_embedding_data(env, monkeypatch):
    (u0, _, _), space = env
    _enrol(u0, A, space)
    _fake_embed(monkeypatch, A, space)
    out = si.identify_author_suggestions("x.wav", 12.0, actor_user_id=u2_id(env))
    assert set(out) == {"band", "reason", "suggestions", "top_score", "gap", "speech_seconds", "requires_confirmation"}
    assert all(set(s) == {"user_id", "score"} for s in out["suggestions"])


def test_unauthorized_actor_gets_nothing_and_service_not_called(env, monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("service must not be called")
    monkeypatch.setattr(speaker_client, "embed_with_space", boom)
    for actor in (None, 0, -1, "1", True):
        out = si.identify_author_suggestions("x.wav", 12.0, actor_user_id=actor)
        assert out["reason"] == "unauthorized" and out["suggestions"] == []


def test_stale_consent_during_processing_drops_suggestion(env, monkeypatch):
    (u0, _, _), space = env
    _enrol(u0, A, space)

    def embed_then_revoke(_p, timeout=0):
        vc.revoke_consent(actor_user_id=u0.id, user_id=u0.id)
        return speaker_client.EmbeddingResult(space.id, "test", list(A))
    monkeypatch.setattr(speaker_client, "embed_with_space", embed_then_revoke)
    out = si.identify_author_suggestions("x.wav", 12.0, actor_user_id=u2_id(env))
    assert out["suggestions"] == [] and out["band"] == "none"


def test_service_failure_does_not_raise_and_retry_succeeds(env, monkeypatch):
    (u0, _, _), space = env
    _enrol(u0, A, space)

    def down(*_a, **_k):
        raise speaker_client.SpeakerServiceUnavailable("down /secret/path")
    monkeypatch.setattr(speaker_client, "embed_with_space", down)
    out = si.identify_author_suggestions("x.wav", 12.0, actor_user_id=u2_id(env))
    assert out["reason"] == "service_unavailable" and out["suggestions"] == []
    assert "secret" not in repr(out)
    _fake_embed(monkeypatch, A, space)
    out = si.identify_author_suggestions("x.wav", 12.0, actor_user_id=u2_id(env))
    assert [s["user_id"] for s in out["suggestions"]] == [u0.id]


def test_unexpected_error_is_swallowed(env, monkeypatch):
    def broken(*_a, **_k):
        raise RuntimeError("boom")
    monkeypatch.setattr(speaker_client, "embed_with_space", broken)
    out = si.identify_author_suggestions("x.wav", 12.0, actor_user_id=u2_id(env))
    assert out["reason"] == "suggestion_failed" and out["suggestions"] == []


def test_short_speech_rejected_before_service(env, monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("service must not be called")
    monkeypatch.setattr(speaker_client, "embed_with_space", boom)
    out = si.identify_author_suggestions("x.wav", 0.5, actor_user_id=u2_id(env))
    assert out["reason"] == "speech_too_short" and out["suggestions"] == []


def test_outside_clinic_mode_upstream_unchanged(env, monkeypatch):
    monkeypatch.delenv("CLINIC_MODE", raising=False)
    assert si.identify_author_suggestions("x.wav", 12.0, actor_user_id=1) is None
