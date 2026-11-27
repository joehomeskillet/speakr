"""Mounted-app TTS with a throwaway database and a synthetic Speaches transport."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LOCAL_ENDPOINT = "http://speaches:8000/v1/audio/speech"

CHILD = r'''
import io
import json
import os
import wave
from contextlib import nullcontext
from unittest.mock import patch

import httpx

from src.clinic.local_tts_engine import SpeachesLocalTtsEngine

MODEL = "speaches-ai/Kokoro-82M-v1.0-ONNX"
VOICES = [{"id": "af_heart", "language": "en"}, {"id": "de_DE", "language": "de"}]
STATUS = "/api/clinic/v1/tts/capability"
SPEAK = "/api/clinic/v1/tts/speak"
buffer = io.BytesIO()
with wave.open(buffer, "wb") as writer:
    writer.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
    writer.writeframes(b"\x01\x00" * 16)
wav = buffer.getvalue()
requests = []
speech = []

def speaches(request):
    requests.append((request.method, request.url.path))
    assert request.url.host == "speaches" and request.url.port == 8000
    if requests[-1] == ("GET", "/v1/models"):
        return httpx.Response(200, json={"data": [{"id": MODEL}]})
    if requests[-1] == ("GET", "/v1/audio/voices"):
        return httpx.Response(200, json={"data": VOICES})
    assert requests[-1] == ("POST", "/v1/audio/speech")
    speech.append(json.loads(request.content))
    return httpx.Response(200, content=wav, headers={"Content-Type": "audio/wav"})

with httpx.Client(transport=httpx.MockTransport(speaches)) as upstream:
    with patch.object(SpeachesLocalTtsEngine, "_session", side_effect=lambda _: nullcontext(upstream)):
        from src.app import app, db
        from src.clinic.models import ClinicDictation, ClinicOriginal
        from src.clinic.revisions import add_asr_revision, add_human_revision, current_revision
        from src.init_db import initialize_database
        from src.models import Recording, User

        with app.app_context():
            initialize_database(app)
            owner = User(username="tts_owner", email="tts_owner@local.test", password="synthetic")
            other = User(username="tts_other", email="tts_other@local.test", password="synthetic")
            db.session.add_all([owner, other])
            db.session.flush()
            recording = Recording(user_id=owner.id, title="Synthetisch", status="COMPLETED",
                                  original_filename="s.wav")
            db.session.add(recording)
            db.session.flush()
            dictation = ClinicDictation(recording_id=recording.id, author_user_id=owner.id)
            db.session.add(dictation)
            db.session.flush()
            original = ClinicOriginal(dictation_id=dictation.id, content_hash="a" * 64, size_bytes=1,
                                      storage_key="synthetic", receipt="stored", origin_ref="synthetic")
            db.session.add(original)
            db.session.flush()
            asr = add_asr_revision(dictation, original, "Vorher", "model:synthetic")
            head = add_human_revision(dictation, asr, "Erst Testwort dann Ende.", owner.id)
            db.session.commit()
            dictation_id, head_id, owner_id, other_id = dictation.id, head.id, owner.id, other.id

        def login(user_id):
            client = app.test_client()
            with client.session_transaction() as session:
                session["_user_id"] = str(user_id)
                session["_fresh"] = True
            token = client.get("/api/csrf-token").json["csrf_token"]
            return client, {"X-CSRF-Token": token}

        client, headers = login(owner_id)
        body = {"dictation_id": dictation_id, "version": 2, "start": 5, "end": 13}
        assert requests == []  # Registration does not contact the engine.
        status = client.get(STATUS)
        assert status.status_code == 200, status.json
        local = os.environ["CLINIC_TTS_ENDPOINT"].startswith("http://speaches:")
        if local:
            assert status.json == {"available": True}, status.json
            denied_client, denied_headers = login(other_id)
            denied = denied_client.post(SPEAK, headers=denied_headers, json=body)
            assert denied.status_code == 403 and denied.json["code"] == "POLICY_DENIED"
            assert speech == []
            response = client.post(SPEAK, headers=headers, json=body)
            assert response.status_code == 200, response.json
            assert response.mimetype == "audio/wav"
            assert response.data == wav
            assert response.headers["Cache-Control"] == "no-store"
            assert response.headers["X-TTS-Voice"] == "de_DE"
            unapproved = client.post(SPEAK, headers=headers, json={**body, "voice": "af_heart"})
            assert (unapproved.status_code, unapproved.json["code"]) == (422, "CAPABILITY_UNAVAILABLE")
            assert speech == [{"model": MODEL, "input": "Testwort", "voice": "de_DE",
                               "response_format": "wav"}]
            with app.app_context():
                stored = current_revision(db.session.get(ClinicDictation, dictation_id))
                assert (stored.id, stored.body) == (head_id, "Erst Testwort dann Ende.")

            VOICES.pop()  # Leave a non-German voice installed.
            status = client.get(STATUS)
            speech.clear()

        assert status.json == {"available": False}, status.json
        unavailable = client.post(SPEAK, headers=headers, json=body)
        assert unavailable.status_code == 422 and unavailable.json["code"] == "CAPABILITY_UNAVAILABLE"
        assert speech == []
        if not local:
            assert requests == []
'''


@pytest.mark.parametrize(
    "endpoint", [LOCAL_ENDPOINT, "", "https://tts.example.com/v1/audio/speech"],
    ids=["verified-then-missing", "disabled", "remote"],
)
def test_real_app_tts_wiring(tmp_path, endpoint):
    env = {
        **os.environ,
        "CLINIC_MODE": "1",
        "CLINIC_TTS_ENDPOINT": endpoint,
        "CLINIC_LOCAL_MODEL_HOSTS": "speaches",
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{tmp_path / 'tts.db'}",
        "UPLOAD_FOLDER": str(tmp_path / "uploads"),
        "RATELIMIT_ENABLED": "false",
        "DISABLE_VOICE_EMBEDDING_CHECK": "true",
        "ENABLE_AUTO_PROCESSING": "false",
    }
    done = subprocess.run([sys.executable, "-c", CHILD], cwd=ROOT, env=env,
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-4000:]
