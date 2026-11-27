"""Audit the local Speaches TTS engine. No socket and no model download."""

import json

import httpx
import pytest

from src.clinic.local_tts import (
    CAPABILITY_UNAVAILABLE,
    PINNED_MODEL_ID,
    CanonicalText,
    TtsEngineFailed,
    TtsError,
)
from src.clinic.local_tts_engine import (
    APPROVED_VOICE_ID,
    SYNTHETIC_GERMAN,
    InstalledVoice,
    LocalInventory,
    SpeachesLocalTtsEngine,
    approved_local_fixture,
    preview,
    report_availability,
    unavailable_fixture,
)

ENDPOINT = "http://speaches/v1/audio/speech"
MODELS = "/v1/models"
VOICES = "/v1/audio/voices"
SPEECH = "/v1/audio/speech"


class _Recorder:
    def __init__(self, audio=b"RIFF", fail=None):
        self.audio = audio
        self.fail = fail
        self.calls = []

    def synthesize(self, text, *, voice_id, model_id, timeout_s):
        self.calls.append(
            {"text": text, "voice_id": voice_id, "model_id": model_id, "timeout_s": timeout_s}
        )
        if self.fail is not None:
            raise self.fail
        return self.audio


def _document():
    return CanonicalText(SYNTHETIC_GERMAN, 4)


def _installed(**overrides):
    """Local catalog: the approved model, one German voice, and decoys."""
    catalog = {
        "models": [{"id": "other/model"}, {"id": PINNED_MODEL_ID}],
        "voices": [
            {"id": "af_heart", "language": "en"},
            {"id": APPROVED_VOICE_ID, "language": "de"},
        ],
        "speech": b"RIFFwav",
        "speech_status": 200,
        "timeout_on": None,
    }
    catalog.update(overrides)
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.host, request.url.path, request.content))
        if catalog["timeout_on"] == request.url.path:
            raise httpx.ReadTimeout("timed out", request=request)
        if request.url.path == MODELS:
            return httpx.Response(200, json={"data": catalog["models"]})
        if request.url.path == VOICES:
            return httpx.Response(200, json={"data": catalog["voices"]})
        if request.method == "POST" and request.url.path == SPEECH:
            if catalog["speech_status"] != 200:
                return httpx.Response(
                    catalog["speech_status"],
                    headers={"location": "https://evil.example/voice.wav"},
                )
            return httpx.Response(
                200, content=catalog["speech"], headers={"content-type": "audio/wav"}
            )
        raise AssertionError(f"unexpected {request.method} {request.url}")

    client = httpx.Client(
        transport=httpx.MockTransport(handler), trust_env=False, follow_redirects=False
    )
    return SpeachesLocalTtsEngine(ENDPOINT, client), seen


def test_only_the_approved_model_and_german_voice_are_reported():
    fixture = LocalInventory(
        ENDPOINT,
        (PINNED_MODEL_ID, "openai/tts-1"),
        (
            InstalledVoice(APPROVED_VOICE_ID, "de"),
            InstalledVoice("af_heart", "en"),
        ),
    )
    report = report_availability(fixture)
    assert report.available is True
    assert report.model_id == PINNED_MODEL_ID
    assert report.voice_id == APPROVED_VOICE_ID
    assert report.model_id != "openai/tts-1"
    assert report.voice_id != "af_heart"


def test_missing_model_remote_endpoint_and_unlisted_id_are_unavailable():
    missing = LocalInventory(ENDPOINT, ("other/model",), (InstalledVoice(APPROVED_VOICE_ID, "de"),))
    remote = LocalInventory(
        "https://tts.example.com/v1/audio/speech",
        (PINNED_MODEL_ID,),
        (InstalledVoice(APPROVED_VOICE_ID, "de"),),
    )
    not_a_list = LocalInventory(
        ENDPOINT,
        PINNED_MODEL_ID,  # type: ignore[arg-type]
        (InstalledVoice(APPROVED_VOICE_ID, "de"),),
    )
    for fixture in (missing, remote, not_a_list):
        report = report_availability(fixture)
        assert report.available is False
        assert report.model_id is None
        assert report.voice_id is None
        assert report.code == CAPABILITY_UNAVAILABLE
        assert report.message == "Lokale Sprachausgabe ist nicht verfügbar."


def test_preview_speaks_synthetic_german_with_the_approved_voice():
    document = _document()
    engine = _Recorder()
    audio = preview(document, approved_local_fixture(), engine=engine)
    assert audio == b"RIFF"
    assert document.text == SYNTHETIC_GERMAN
    assert document.version == 4
    assert engine.calls == [
        {
            "text": SYNTHETIC_GERMAN,
            "voice_id": APPROVED_VOICE_ID,
            "model_id": PINNED_MODEL_ID,
            "timeout_s": 15.0,
        }
    ]


def test_default_preview_returns_audio_without_changing_text():
    document = _document()
    assert preview(document, approved_local_fixture()) == b"RIFF"
    assert (document.text, document.version) == (SYNTHETIC_GERMAN, 4)


def test_unavailable_fixture_is_visible_and_leaves_the_dictation():
    document = _document()
    engine = _Recorder()
    with pytest.raises(TtsError) as caught:
        preview(document, unavailable_fixture(), engine=engine)
    assert caught.value.code == "voice_missing"
    assert str(caught.value) == "Fehlende Stimme."
    assert engine.calls == []
    assert (document.text, document.version) == (SYNTHETIC_GERMAN, 4)


def test_voice_in_another_language_is_visible_and_leaves_the_dictation():
    document = _document()
    fixture = LocalInventory(
        ENDPOINT,
        (PINNED_MODEL_ID,),
        (InstalledVoice(APPROVED_VOICE_ID, "en"), InstalledVoice("af_heart", "de")),
    )
    with pytest.raises(TtsError) as caught:
        preview(document, fixture)
    assert caught.value.code == "voice_missing"
    assert str(caught.value) == "Fehlende deutsche Stimme."
    assert (document.text, document.version) == (SYNTHETIC_GERMAN, 4)


def test_timeout_is_visible_and_is_not_retried():
    document = _document()
    engine = _Recorder(fail=TimeoutError("slow"))
    with pytest.raises(TtsError) as caught:
        preview(document, approved_local_fixture(), engine=engine)
    assert caught.value.code == "timeout"
    assert "Zeitlimit" in str(caught.value)
    assert len(engine.calls) == 1
    assert (document.text, document.version) == (SYNTHETIC_GERMAN, 4)

    timed = LocalInventory(
        ENDPOINT,
        (PINNED_MODEL_ID,),
        (InstalledVoice(APPROVED_VOICE_ID, "de"),),
        timeout=True,
    )
    again = _document()
    with pytest.raises(TtsError) as caught:
        preview(again, timed)
    assert caught.value.code == "timeout"
    assert (again.text, again.version) == (SYNTHETIC_GERMAN, 4)


def test_http_reports_only_the_installed_approved_pair():
    engine, seen = _installed()
    report = engine.availability()
    assert report.available is True
    assert (report.model_id, report.voice_id) == (PINNED_MODEL_ID, APPROVED_VOICE_ID)
    assert [path for _, _, path, _ in seen] == [MODELS, VOICES]
    assert {host for _, host, _, _ in seen} == {"speaches"}


def test_http_does_not_download_or_speak_when_the_model_is_absent():
    engine, seen = _installed(models=[{"id": "other/model"}])
    with pytest.raises(TtsError) as caught:
        engine.synthesize("Hallo", voice_id=APPROVED_VOICE_ID, model_id=PINNED_MODEL_ID, timeout_s=5)
    assert caught.value.code == CAPABILITY_UNAVAILABLE
    assert engine.calls == [("GET", MODELS)]
    assert [item[0] for item in seen] == ["GET"]
    assert SPEECH not in {path for _, _, path, _ in seen}


def test_http_missing_german_voice_does_not_post_or_fall_back():
    engine, seen = _installed(voices=[{"id": "af_heart", "language": "en"}])
    with pytest.raises(TtsError) as caught:
        engine.synthesize(
            SYNTHETIC_GERMAN, voice_id=APPROVED_VOICE_ID, model_id=PINNED_MODEL_ID, timeout_s=5
        )
    assert caught.value.code == "voice_missing"
    assert str(caught.value) == "Fehlende Stimme."
    assert engine.calls == [("GET", MODELS), ("GET", VOICES)]
    assert "POST" not in {method for method, _, _, _ in seen}
    assert {host for _, host, _, _ in seen} == {"speaches"}


def test_http_preview_uses_the_approved_voice_and_leaves_the_text():
    engine, seen = _installed()
    document = _document()
    audio = preview(document, approved_local_fixture(), engine=engine)
    assert audio == b"RIFFwav"
    assert (document.text, document.version) == (SYNTHETIC_GERMAN, 4)
    body = json.loads(seen[-1][3])
    assert body == {
        "model": PINNED_MODEL_ID,
        "input": SYNTHETIC_GERMAN,
        "voice": APPROVED_VOICE_ID,
        "response_format": "wav",
    }
    assert "download" not in body
    assert [path for _, _, path, _ in seen] == [MODELS, VOICES, SPEECH]
    assert {host for _, host, _, _ in seen} == {"speaches"}


def test_http_timeout_and_remote_failure_stay_on_one_host():
    document = _document()
    engine, seen = _installed(timeout_on=SPEECH)
    with pytest.raises(TtsError) as caught:
        preview(document, approved_local_fixture(), engine=engine)
    assert caught.value.code == "timeout"
    assert "Zeitlimit" in str(caught.value)
    assert (document.text, document.version) == (SYNTHETIC_GERMAN, 4)
    assert [path for _, _, path, _ in seen] == [MODELS, VOICES, SPEECH]

    failed, failed_seen = _installed(speech_status=302)
    with pytest.raises(TtsEngineFailed):
        failed.synthesize("Hallo", voice_id=APPROVED_VOICE_ID, model_id=PINNED_MODEL_ID, timeout_s=5)
    assert [path for _, _, path, _ in failed_seen] == [MODELS, VOICES, SPEECH]
    assert {host for _, host, _, _ in failed_seen} == {"speaches"}


def test_remote_endpoint_makes_no_request():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        raise AssertionError("remote request")

    client = httpx.Client(transport=httpx.MockTransport(handler), trust_env=False, follow_redirects=False)
    engine = SpeachesLocalTtsEngine("https://tts.example.com/v1/audio/speech", client)
    report = engine.availability()
    assert report.available is False
    assert report.code == CAPABILITY_UNAVAILABLE
    with pytest.raises(TtsError) as caught:
        engine.synthesize("Hallo", voice_id=APPROVED_VOICE_ID, model_id=PINNED_MODEL_ID, timeout_s=5)
    assert caught.value.code == CAPABILITY_UNAVAILABLE
    assert seen == []
    assert engine.calls == []
