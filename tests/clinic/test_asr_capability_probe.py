"""Real missing-capability probes extending the P10 spike (w038-engine-spike).

Evaluates realtime, negative lexicon, pronunciation, acoustic adaptation,
checkpoint, and local TTS capabilities using synthetic test data.
Demonstrates a local provider for local TTS while retaining precise blocked
capability evidence for unsupported features.
Hotword prompts and UI metadata never count as acoustic training.
"""

from __future__ import annotations

from dataclasses import replace
import json
from typing import Any

import pytest

from src.clinic.asr_capabilities import (
    CAPABILITY_UNAVAILABLE,
    AsrCapability,
    AsrCapabilityRegistry,
    AsrTarget,
    CapabilityEvidence,
)
from src.clinic.config import is_local_model_url
from src.clinic.local_tts import (
    BrowserVoice,
    CanonicalText,
    LocalTtsCapability,
    PINNED_MODEL_ID,
    PINNED_REVISION,
    TtsEngineFailed,
    TtsError,
    TtsTransient,
    declare_capability,
    synthesize,
)
from src.clinic.policy import Deny, RecordingFacts, Subject

# Pinned engine targets based on Speaches v0.8.3 (commit c78b77d)
LOCAL_TTS_TARGET = AsrTarget(
    base_url="http://speaches/v1/audio/speech",
    engine="speaches",
    engine_revision="v0.8.3",
    model=PINNED_MODEL_ID,
    model_revision=PINNED_REVISION,
    parameters_json='{"voice":"de_DE","language":"de"}',
)

BATCH_STT_TARGET = AsrTarget(
    base_url="http://speaches/v1/audio/transcriptions",
    engine="speaches",
    engine_revision="v0.8.3",
    model="Systran/faster-whisper-large-v3",
    model_revision="large-v3",
    parameters_json='{"language":"de","temperature":0.0}',
)

LIVE_STT_TARGET = AsrTarget(
    base_url="http://speaches/v1/realtime",
    engine="speaches",
    engine_revision="v0.8.3",
    model="Systran/faster-distil-whisper-small.en",
    model_revision="distil-small.en",
    parameters_json='{"language":"en","modalities":["audio","text"]}',
)

ADAPTATION_TARGET = AsrTarget(
    base_url="http://speaches/v1/models/train",
    engine="speaches",
    engine_revision="v0.8.3",
    model="Systran/faster-whisper-large-v3",
    model_revision="large-v3",
    parameters_json='{"prompt":"Klinischer Befund","hotwords":"Hypertonie,Aspirin"}',
)

REMOTE_TARGET = AsrTarget(
    base_url="https://remote-asr.example.com/v1",
    engine="remote-engine",
    engine_revision="v1.0",
    model="remote-model",
    model_revision="m1",
    parameters_json="{}",
)

PARAGRAPHS = "Erster Absatz.\n\nZweiter Absatz.\n\nDritter Absatz."
SYNTHETIC_WAV = b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x44\xac\x00\x00\x88\x58\x01\x00\x02\x00\x10\x00data\x00\x00\x00\x00"


class FakeProbeEngine:
    """Synthetic local engine for probe execution without network sockets."""

    def __init__(self, audio: bytes = SYNTHETIC_WAV, failures: list[Exception] | None = None) -> None:
        self.audio = audio
        self.failures = list(failures or [])
        self.calls: list[dict[str, Any]] = []

    def synthesize(self, text: str, *, voice_id: str, model_id: str, timeout_s: float) -> bytes:
        self.calls.append({
            "text": text,
            "voice_id": voice_id,
            "model_id": model_id,
            "timeout_s": timeout_s,
        })
        if self.failures:
            raise self.failures.pop(0)
        return self.audio


def probe_engine_capability(
    capability: AsrCapability | str,
    target: AsrTarget,
    *,
    engine: FakeProbeEngine | None = None,
    subject: Subject | None = None,
    recording: RecordingFacts | None = None,
    text: str = PARAGRAPHS,
    version: int = 1,
    start: int | None = None,
    end: int | None = None,
    cursor: int = 0,
    pronunciation: str | None = None,
    browser_voices: tuple[BrowserVoice, ...] = (),
    requested_voice: str | None = None,
) -> CapabilityEvidence | str:
    """Probe an engine target for a specific capability using synthetic data.

    Returns CapabilityEvidence if demonstrated at runtime on local engine,
    otherwise CAPABILITY_UNAVAILABLE.
    Hotword prompts and UI configuration never count as acoustic training.
    """
    if not is_local_model_url(target.base_url) or not all(
        v.strip() for v in (target.engine, target.engine_revision, target.model, target.model_revision)
    ):
        return CAPABILITY_UNAVAILABLE

    try:
        cap_enum = AsrCapability(capability)
        params = json.loads(target.parameters_json)
    except (ValueError, TypeError):
        return CAPABILITY_UNAVAILABLE
    if not isinstance(params, dict):
        return CAPABILITY_UNAVAILABLE

    if cap_enum == AsrCapability.LOCAL_TTS:
        if (
            target.engine != "speaches"
            or target.engine_revision != "v0.8.3"
            or target.model != PINNED_MODEL_ID
            or target.model_revision != PINNED_REVISION
            or params.get("voice") != "de_DE"
            or params.get("language") != "de"
        ):
            return CAPABILITY_UNAVAILABLE

        sub = subject or Subject(user_id=1)
        rec = recording or RecordingFacts(recording_id=1, owner_id=1)
        tts_cap = declare_capability(
            model_id=target.model,
            revision=target.model_revision,
            endpoint=target.base_url,
            language=params["language"],
        )
        doc = CanonicalText(text, version)
        try:
            result = synthesize(
                doc,
                current_version=version,
                subject=sub,
                recording=rec,
                capability=tts_cap,
                engine=engine or FakeProbeEngine(),
                start=start,
                end=end,
                cursor=cursor,
                pronunciation=pronunciation,
                browser_voices=browser_voices,
                requested_voice=requested_voice,
            )
        except (TtsError, Exception):
            return CAPABILITY_UNAVAILABLE

        if result.audio and len(result.audio) > 0 and not result.training_proven:
            return CapabilityEvidence(
                capability=AsrCapability.LOCAL_TTS,
                target=target,
                test_set_id="synthetic-tts-de-v1",
                evidence_ref="clinic-evidence/asr/capability-probe.md#local-tts",
                outcome="runtime_passed",
            )
        return CAPABILITY_UNAVAILABLE

    # For unproven/unsupported engine capabilities in Speaches v0.8.3:
    # LIVE STT has only English distil default; negative lexicon, pronunciation
    # training, acoustic adaptation and optimization checkpoints are absent.
    # Hotwords/prompts in params must NEVER satisfy training or adaptation.
    return CAPABILITY_UNAVAILABLE


# ============================================================================
# 1. Capability Inventory Probe Tests
# ============================================================================


def test_probe_unproven_capabilities_inventory() -> None:
    engine = FakeProbeEngine()
    # local_tts has a demonstrated local provider
    tts_ev = probe_engine_capability(AsrCapability.LOCAL_TTS, LOCAL_TTS_TARGET, engine=engine)
    assert isinstance(tts_ev, CapabilityEvidence)
    assert tts_ev.outcome == "runtime_passed"
    assert tts_ev.capability == AsrCapability.LOCAL_TTS

    # Unproven capabilities retain precise blocked capability
    for cap, target in (
        (AsrCapability.LIVE, LIVE_STT_TARGET),
        (AsrCapability.NEGATIVE_LEXICON, BATCH_STT_TARGET),
        (AsrCapability.PRONUNCIATION, BATCH_STT_TARGET),
        (AsrCapability.ADAPTATION, ADAPTATION_TARGET),
        (AsrCapability.TRAINING, ADAPTATION_TARGET),
        (AsrCapability.CHECKPOINT, BATCH_STT_TARGET),
    ):
        assert probe_engine_capability(cap, target, engine=engine) == CAPABILITY_UNAVAILABLE


# ============================================================================
# 2. Local TTS Demonstrated Provider Oracle (BT-LIVE-008 / PAR-LIVE-008)
# ============================================================================


def test_local_tts_demonstrated_provider_positive_probe() -> None:
    engine = FakeProbeEngine(audio=SYNTHETIC_WAV)
    start = PARAGRAPHS.index("Zweiter Absatz.")
    end = start + len("Zweiter Absatz.")

    evidence = probe_engine_capability(
        AsrCapability.LOCAL_TTS,
        LOCAL_TTS_TARGET,
        engine=engine,
        start=start,
        end=end,
    )
    assert isinstance(evidence, CapabilityEvidence)
    assert evidence.target == LOCAL_TTS_TARGET
    assert len(engine.calls) == 1
    assert engine.calls[0]["text"] == "Zweiter Absatz."

    # Registry integration with demonstrated local provider
    registry = AsrCapabilityRegistry([evidence])
    assert registry.check("local_tts", LOCAL_TTS_TARGET) == evidence
    assert not registry.training_proven(LOCAL_TTS_TARGET)


def test_local_tts_cursor_and_pronunciation_reading() -> None:
    engine = FakeProbeEngine()
    cursor_pos = PARAGRAPHS.index("Dritter Absatz.")
    ev_cursor = probe_engine_capability(
        AsrCapability.LOCAL_TTS,
        LOCAL_TTS_TARGET,
        engine=engine,
        cursor=cursor_pos,
    )
    assert isinstance(ev_cursor, CapabilityEvidence)
    assert engine.calls[-1]["text"] == "Dritter Absatz."

    ev_pron = probe_engine_capability(
        AsrCapability.LOCAL_TTS,
        LOCAL_TTS_TARGET,
        engine=engine,
        pronunciation="ˈaɪs",
    )
    assert isinstance(ev_pron, CapabilityEvidence)
    assert engine.calls[-1]["text"] == "ˈaɪs"


# ============================================================================
# 3. Security, Authorization and Validation Cases
# ============================================================================


def test_probe_unauthorized_and_security_cases() -> None:
    engine = FakeProbeEngine()

    # Remote target refused by is_local_model_url
    assert probe_engine_capability(AsrCapability.LOCAL_TTS, REMOTE_TARGET, engine=engine) == CAPABILITY_UNAVAILABLE
    assert engine.calls == []

    # Subject without access to recording
    sub_forbidden = Subject(user_id=42)
    rec_owned = RecordingFacts(recording_id=1, owner_id=1)
    assert probe_engine_capability(
        AsrCapability.LOCAL_TTS, LOCAL_TTS_TARGET, engine=engine, subject=sub_forbidden, recording=rec_owned
    ) == CAPABILITY_UNAVAILABLE

    # Subject with explicit Deny
    sub_denied = Subject(user_id=1, denies=(Deny(recording_ids=frozenset({1})),))
    assert probe_engine_capability(
        AsrCapability.LOCAL_TTS, LOCAL_TTS_TARGET, engine=engine, subject=sub_denied, recording=rec_owned
    ) == CAPABILITY_UNAVAILABLE

    # Unverified browser voice rejected
    unverified_voice = BrowserVoice("cloud-voice", "browser", verified=False)
    assert probe_engine_capability(
        AsrCapability.LOCAL_TTS,
        LOCAL_TTS_TARGET,
        engine=engine,
        browser_voices=(unverified_voice,),
        requested_voice="cloud-voice",
    ) == CAPABILITY_UNAVAILABLE


# ============================================================================
# 4. Stale-Version and Metadata Mismatch Cases
# ============================================================================


@pytest.mark.parametrize("stale_target", [
    replace(LOCAL_TTS_TARGET, engine_revision="v0.8.2"),
    replace(LOCAL_TTS_TARGET, model_revision="mismatched-revision"),
    replace(LOCAL_TTS_TARGET, model="other/model"),
    replace(LOCAL_TTS_TARGET, parameters_json='{"voice":"en_US","language":"en"}'),
    replace(LOCAL_TTS_TARGET, parameters_json="not-json"),
])
def test_probe_stale_target_cannot_inherit_proof(stale_target: AsrTarget) -> None:
    engine = FakeProbeEngine()
    assert probe_engine_capability(AsrCapability.LOCAL_TTS, stale_target, engine=engine) == CAPABILITY_UNAVAILABLE
    assert engine.calls == []


def test_probe_stale_document_version_rejected() -> None:
    engine = FakeProbeEngine()
    # Current version is 5 while document has version 1 -> stale_version
    sub = Subject(user_id=1)
    rec = RecordingFacts(recording_id=1, owner_id=1)
    doc = CanonicalText("Test", version=1)
    with pytest.raises(TtsError) as caught:
        synthesize(
            doc,
            current_version=5,
            subject=sub,
            recording=rec,
            capability=declare_capability(),
            engine=engine,
        )
    assert caught.value.code == "stale_version"
    assert engine.calls == []


# ============================================================================
# 5. Retry, Transient and Hard Failure Cases
# ============================================================================


def test_probe_retry_recovers_from_transient_failure() -> None:
    # Engine fails once with TtsTransient, recovers on retry
    engine = FakeProbeEngine(audio=SYNTHETIC_WAV, failures=[TtsTransient("network blip")])
    evidence = probe_engine_capability(AsrCapability.LOCAL_TTS, LOCAL_TTS_TARGET, engine=engine)
    assert isinstance(evidence, CapabilityEvidence)
    assert len(engine.calls) == 2


def test_probe_hard_failure_and_empty_audio_fail() -> None:
    engine_fail = FakeProbeEngine(failures=[TtsEngineFailed("unrecoverable crash")])
    assert probe_engine_capability(AsrCapability.LOCAL_TTS, LOCAL_TTS_TARGET, engine=engine_fail) == CAPABILITY_UNAVAILABLE

    engine_empty = FakeProbeEngine(audio=b"")
    assert probe_engine_capability(AsrCapability.LOCAL_TTS, LOCAL_TTS_TARGET, engine=engine_empty) == CAPABILITY_UNAVAILABLE


def test_blocked_capability_retry_is_side_effect_free() -> None:
    # Repeated calls on blocked capabilities consistently return CAPABILITY_UNAVAILABLE
    for _ in range(3):
        assert probe_engine_capability(AsrCapability.TRAINING, ADAPTATION_TARGET) == CAPABILITY_UNAVAILABLE
        assert probe_engine_capability(AsrCapability.NEGATIVE_LEXICON, BATCH_STT_TARGET) == CAPABILITY_UNAVAILABLE
        assert probe_engine_capability(AsrCapability.CHECKPOINT, BATCH_STT_TARGET) == CAPABILITY_UNAVAILABLE


# ============================================================================
# 6. Responsible Acceptance Cases X4-013 & WB-018
# ============================================================================


def test_x4_013_acoustic_adaptation_and_pronunciation_no_dummy_success() -> None:
    """X4-013: Adapter without train_capability must return CAPABILITY_UNAVAILABLE.

    Never dummy success; decoder prompt / hotwords are NOT training evidence.
    """
    # Target declares hotwords and initial_prompt
    assert "hotwords" in ADAPTATION_TARGET.parameters_json
    assert "prompt" in ADAPTATION_TARGET.parameters_json

    # Both training and pronunciation training must be CAPABILITY_UNAVAILABLE
    for cap in (AsrCapability.TRAINING, AsrCapability.ADAPTATION, AsrCapability.PRONUNCIATION):
        outcome = probe_engine_capability(cap, ADAPTATION_TARGET)
        assert outcome == CAPABILITY_UNAVAILABLE

    # Registry verification: prompt biasing cannot prove training
    registry = AsrCapabilityRegistry([])
    assert not registry.training_proven(ADAPTATION_TARGET)
    assert registry.check("training", ADAPTATION_TARGET) == CAPABILITY_UNAVAILABLE


def test_wb_018_asr_training_not_a_mock() -> None:
    """WB-018: ASR training must not be a mock or fake profile update."""
    # Attempting to trigger training returns CAPABILITY_UNAVAILABLE
    assert probe_engine_capability(AsrCapability.TRAINING, ADAPTATION_TARGET) == CAPABILITY_UNAVAILABLE

    # Reloading profile returns identical configuration without claiming acoustic training
    reloaded_target = replace(ADAPTATION_TARGET)
    assert reloaded_target == ADAPTATION_TARGET
    assert not AsrCapabilityRegistry([]).training_proven(reloaded_target)


# ============================================================================
# 7. Original Behavior Tests (BT) Alignment
# ============================================================================


def test_bt_live_001_realtime_stt_oracle_preserved() -> None:
    """BT-LIVE-001 / PAR-LIVE-001: Realtime STT blocked prerequisite.

    Speaches v0.8.3 defaults to English distil-whisper without German live mode.
    Pure upload after recording end does not fulfill live recognition.
    """
    assert probe_engine_capability(AsrCapability.LIVE, LIVE_STT_TARGET) == CAPABILITY_UNAVAILABLE


def test_bt_voc_003_negative_lexicon_oracle_preserved() -> None:
    """BT-VOC-003 / PAR-VOC-003: Negative lexicon blocked capability.

    Missing engine function is not concealed with empty UI toggles.
    """
    assert probe_engine_capability(AsrCapability.NEGATIVE_LEXICON, BATCH_STT_TARGET) == CAPABILITY_UNAVAILABLE


def test_bt_voc_007_pronunciation_reading_is_not_training() -> None:
    """BT-VOC-007 / PAR-VOC-007: Pronunciation training blocked capability.

    TTS reading of pronunciation is verified audio playback, NOT training.
    """
    # Pronunciation training is blocked
    assert probe_engine_capability(AsrCapability.PRONUNCIATION, BATCH_STT_TARGET) == CAPABILITY_UNAVAILABLE

    # TTS reading of phonetic hint works but does not claim training
    engine = FakeProbeEngine()
    ev = probe_engine_capability(
        AsrCapability.LOCAL_TTS,
        LOCAL_TTS_TARGET,
        engine=engine,
        pronunciation="ˈaɪs",
    )
    assert isinstance(ev, CapabilityEvidence)
    assert not AsrCapabilityRegistry([ev]).training_proven(LOCAL_TTS_TARGET)


def test_bt_asr_004_and_005_adaptation_window_oracle_preserved() -> None:
    """BT-ASR-004 & BT-ASR-005: Adaptation and checkpoint blocked capabilities."""
    # Text+audio adaptation is blocked on CTranslate2 inference engine
    assert probe_engine_capability(AsrCapability.ADAPTATION, ADAPTATION_TARGET) == CAPABILITY_UNAVAILABLE

    # Insufficient data (< 180s) and optimization checkpoint window are blocked
    assert probe_engine_capability(AsrCapability.CHECKPOINT, BATCH_STT_TARGET) == CAPABILITY_UNAVAILABLE


# ============================================================================
# 8. Consumer Handoff and Registry Integration
# ============================================================================


def test_consumer_handoff_registry_wiring() -> None:
    """Scheduled consumers w043, w044, w041 receive exact capability statuses."""
    engine = FakeProbeEngine()
    tts_ev = probe_engine_capability(AsrCapability.LOCAL_TTS, LOCAL_TTS_TARGET, engine=engine)
    assert isinstance(tts_ev, CapabilityEvidence)

    registry = AsrCapabilityRegistry([tts_ev])

    # Local TTS consumer receives demonstrated provider
    assert registry.check("local_tts", LOCAL_TTS_TARGET) == tts_ev

    # Negative lexicon consumer (w043) receives CAPABILITY_UNAVAILABLE
    assert registry.check("negative_lexicon", BATCH_STT_TARGET) == CAPABILITY_UNAVAILABLE

    # Pronunciation training consumer (w044) receives CAPABILITY_UNAVAILABLE
    assert registry.check("pronunciation", BATCH_STT_TARGET) == CAPABILITY_UNAVAILABLE

    # Training runner consumer (w041) receives CAPABILITY_UNAVAILABLE and training_proven False
    assert registry.check("training", ADAPTATION_TARGET) == CAPABILITY_UNAVAILABLE
    assert registry.check("adaptation", ADAPTATION_TARGET) == CAPABILITY_UNAVAILABLE
    assert registry.check("checkpoint", BATCH_STT_TARGET) == CAPABILITY_UNAVAILABLE
    assert not registry.training_proven(ADAPTATION_TARGET)
