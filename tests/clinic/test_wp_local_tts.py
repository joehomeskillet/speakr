"""Bounded local TTS adapter. Synthetic text only. No socket and no database writes."""

import pytest

from src.clinic.local_tts import (
    CAPABILITY_UNAVAILABLE,
    MAX_CHARS,
    PINNED_MODEL_ID,
    BrowserVoice,
    CanonicalText,
    TtsEngineFailed,
    TtsError,
    TtsTransient,
    declare_capability,
    synthesize,
)
from src.clinic.policy import Deny, RecordingFacts, Subject

PARAGRAPHS = "Erster Absatz.\n\nZweiter Absatz.\n\nDritter Absatz."


class FakeEngine:
    def __init__(self, audio=b"RIFF", failures=None):
        self.audio = audio
        self.failures = list(failures or [])
        self.calls = []

    def synthesize(self, text, *, voice_id, model_id, timeout_s):
        self.calls.append(
            {"text": text, "voice_id": voice_id, "model_id": model_id, "timeout_s": timeout_s}
        )
        if self.failures:
            raise self.failures.pop(0)
        return self.audio


def _own():
    return Subject(user_id=7), RecordingFacts(recording_id=3, owner_id=7)


_MISSING = object()


def _read(text=PARAGRAPHS, version=4, engine=_MISSING, **kwargs):
    subject, recording = _own()
    document = CanonicalText(text, version)
    before = (document.text, document.version)
    if engine is _MISSING:
        engine = FakeEngine()
    try:
        return synthesize(
            document,
            current_version=kwargs.pop("current_version", version),
            subject=kwargs.pop("subject", subject),
            recording=kwargs.pop("recording", recording),
            capability=kwargs.pop("capability", declare_capability()),
            engine=engine,
            **kwargs,
        )
    finally:
        assert (document.text, document.version) == before


def _raises(code, **kwargs):
    with pytest.raises(TtsError) as caught:
        _read(**kwargs)
    assert caught.value.code == code
    return caught.value


def test_marked_paragraph_uses_pinned_voice_and_leaves_text():
    start = PARAGRAPHS.index("Zweiter Absatz.")
    end = start + len("Zweiter Absatz.")
    engine = FakeEngine(b"wav-marked")
    browser = BrowserVoice("browser-de", "browser", verified=False)
    result = _read(engine=engine, start=start, end=end, browser_voices=(browser,))
    assert result.spoken_text == "Zweiter Absatz."
    assert result.audio == b"wav-marked"
    assert result.training_proven is False
    assert result.voice_profile_stored is False
    assert engine.calls == [
        {
            "text": "Zweiter Absatz.",
            "voice_id": "de_DE",
            "model_id": PINNED_MODEL_ID,
            "timeout_s": 15.0,
        }
    ]


def test_cursor_reads_only_the_tail():
    cursor = PARAGRAPHS.index("Dritter Absatz.")
    engine = FakeEngine()
    result = _read(engine=engine, cursor=cursor)
    assert result.spoken_text == "Dritter Absatz."
    assert engine.calls[0]["text"] == "Dritter Absatz."


def test_pronunciation_is_read_and_is_not_training():
    document = CanonicalText("Eisberg bleibt.", 2)
    engine = FakeEngine(b"wav-phon")
    subject, recording = _own()
    result = synthesize(
        document,
        current_version=2,
        subject=subject,
        recording=recording,
        capability=declare_capability(),
        engine=engine,
        pronunciation="ˈaɪs",
    )
    assert document.text == "Eisberg bleibt."
    assert document.version == 2
    assert result.spoken_text == "ˈaɪs"
    assert result.training_proven is False
    assert result.voice_profile_stored is False
    assert engine.calls[0]["text"] == "ˈaɪs"


def test_other_user_is_unauthorized_and_engine_stays_idle():
    engine = FakeEngine()
    _raises(
        "unauthorized",
        engine=engine,
        subject=Subject(user_id=8),
        recording=RecordingFacts(recording_id=3, owner_id=7),
        capability=None,
    )
    assert engine.calls == []


def test_deny_exception_blocks_the_owner():
    engine = FakeEngine()
    _raises(
        "unauthorized",
        engine=engine,
        subject=Subject(user_id=7, denies=(Deny(recording_ids=frozenset({3})),)),
    )
    assert engine.calls == []


def test_stale_version_does_not_speak():
    engine = FakeEngine()
    error = _raises("stale_version", engine=engine, current_version=5)
    assert engine.calls == []
    assert "veraltet" in str(error)


def test_missing_engine_is_capability_unavailable():
    _raises(CAPABILITY_UNAVAILABLE, engine=None)


def test_unpinned_or_remote_capability_is_unavailable():
    _raises(CAPABILITY_UNAVAILABLE, capability=None)
    _raises(CAPABILITY_UNAVAILABLE, capability=declare_capability(verified=False))
    _raises(CAPABILITY_UNAVAILABLE, capability=declare_capability(model_id="other-model"))
    _raises(CAPABILITY_UNAVAILABLE, capability=declare_capability(revision="other"))
    _raises(
        CAPABILITY_UNAVAILABLE,
        capability=declare_capability(endpoint="https://tts.example.com/v1/audio/speech"),
    )
    _raises(CAPABILITY_UNAVAILABLE, capability=declare_capability(backend="browser"))
    _raises(CAPABILITY_UNAVAILABLE, capability=declare_capability(timeout_s=90))


def test_browser_voice_is_not_selected_automatically():
    engine = FakeEngine()
    voices = (BrowserVoice("browser-de", "local", verified=True),)
    result = _read(engine=engine, browser_voices=voices)
    assert result.voice_id == "de_DE"
    assert engine.calls[0]["voice_id"] == "de_DE"


def test_unverified_browser_voice_is_rejected():
    engine = FakeEngine()
    _raises(
        "voice_unverified",
        engine=engine,
        requested_voice="browser-de",
        browser_voices=(BrowserVoice("browser-de", "browser", verified=True),),
    )
    assert engine.calls == []
    engine = FakeEngine()
    _raises(
        "voice_unverified",
        engine=engine,
        requested_voice="browser-de",
        browser_voices=(BrowserVoice("browser-de", "local", verified=False),),
    )
    assert engine.calls == []


def test_named_verified_local_browser_voice_still_uses_local_engine():
    engine = FakeEngine()
    result = _read(
        engine=engine,
        requested_voice="browser-de",
        browser_voices=(BrowserVoice("browser-de", "local", verified=True),),
    )
    assert result.voice_id == "browser-de"
    assert engine.calls[0]["model_id"] == PINNED_MODEL_ID


def test_disabled_or_unknown_voice_is_visible():
    engine = FakeEngine()
    _raises("voice_missing", engine=engine, capability=declare_capability(enabled=False))
    assert engine.calls == []
    with pytest.raises(TtsError) as caught:
        _read(requested_voice="gibt-es-nicht")
    assert caught.value.code == "voice_missing"
    assert str(caught.value) == "Fehlende Stimme."
    with pytest.raises(TtsError) as caught:
        _read(capability=declare_capability(language="en"))
    assert str(caught.value) == "Fehlende deutsche Stimme."


def test_text_over_the_bound_is_not_sent():
    engine = FakeEngine()
    _raises("text_too_long", engine=engine, text="a" * (MAX_CHARS + 1), version=1)
    assert engine.calls == []


def test_transient_failure_retries_once():
    engine = FakeEngine(b"wav-ok", failures=[TtsTransient("busy")])
    result = _read(engine=engine)
    assert result.audio == b"wav-ok"
    assert result.attempts == 2
    assert len(engine.calls) == 2


def test_hard_failure_and_empty_audio_do_not_succeed():
    engine = FakeEngine(failures=[TtsEngineFailed("down")])
    _raises("synthesis_failed", engine=engine)
    assert len(engine.calls) == 1
    engine = FakeEngine(audio=b"")
    _raises("synthesis_failed", engine=engine)
    assert len(engine.calls) == 1


def test_timeout_is_not_retried():
    engine = FakeEngine(failures=[TimeoutError()])
    error = _raises("timeout", engine=engine)
    assert len(engine.calls) == 1
    assert "Zeitlimit" in str(error)


def test_two_transient_failures_stop():
    engine = FakeEngine(failures=[TtsTransient("a"), TtsTransient("b")])
    _raises("synthesis_failed", engine=engine)
    assert len(engine.calls) == 2
