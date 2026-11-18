"""Voice enrollment and consent HTTP API (web-only, own-user).

Implements the contract specified in clinic-evidence/contracts/enrollment-web.openapi.json:
- GET  /api/clinic/v1/voice/enrollment        (getOwnVoiceEnrollment)
- POST /api/clinic/v1/voice/enrollment        (enrollOwnVoiceSample)
- POST /api/clinic/v1/voice/consent           (grantOwnVoiceConsent)
- POST /api/clinic/v1/voice/consent/revoke    (revokeOwnVoiceConsent)

All operations are restricted to the authenticated user's own profile.
Embeddings and raw audio are never returned to the browser or logged.
Transient audio files are overwritten and deleted immediately after embedding.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import struct
import tempfile
import threading
import wave
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from flask import Blueprint, abort, current_app, jsonify, request, session
from flask_login import current_user
from flask_wtf.csrf import CSRFError, validate_csrf
from wtforms.validators import ValidationError

from src.clinic import speaker_client, voice_consent
from src.clinic.config import clinic_mode_enabled

logger = logging.getLogger(__name__)

MAX_AUDIO_BYTES = 2_000_000
MAX_GRANT_BYTES = 100_000
MAX_REVOKE_BYTES = 4_096

voice_enrollment_bp = Blueprint("voice_enrollment", __name__, url_prefix="/api/clinic/v1/voice")
blueprint = voice_enrollment_bp


@dataclass(frozen=True)
class IdempotencyRecord:
    digest: str
    status_code: int
    etag: str
    body: dict[str, Any]


_idempotency_store: dict[tuple[int, str, str], IdempotencyRecord] = {}
_in_flight: dict[tuple[int, str, str], threading.Event] = {}
_idempotency_lock = threading.Lock()


def clear_idempotency_store() -> None:
    """Clear idempotency receipts (for test isolation)."""
    with _idempotency_lock:
        _idempotency_store.clear()
        _in_flight.clear()


def _problem(code: str, title: str, status: int = 400):
    response = jsonify({"type": "about:blank", "title": title, "status": status, "code": code})
    response.status_code = status
    response.mimetype = "application/problem+json"
    response.headers["Cache-Control"] = "no-store"
    return response


voice_enrollment_bp.register_error_handler(CSRFError, lambda _e: _problem("FORBIDDEN", "CSRF token invalid", 403))


def _iso_datetime(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc).isoformat() if dt.tzinfo is None else dt.isoformat()


def _build_enrollment_state(user_id: int) -> tuple[dict[str, Any], str]:
    state = voice_consent.consent_state(user_id)
    history = voice_consent.consent_history(user_id)
    latest_id = history[-1]["id"] if history else 0
    return {
        "consent_revision": latest_id,
        "state": state.state,
        "embedding_count": voice_consent.embedding_count(user_id),
        "granted_at": _iso_datetime(state.granted_at),
        "revoked_at": _iso_datetime(state.revoked_at),
    }, f'"voice-consent:{latest_id}"'


def _parse_if_match(header_val: str | None) -> int | None:
    if not header_val:
        return None
    val = header_val.strip().strip('"')
    if val.startswith("voice-consent:"):
        val = val[len("voice-consent:") :].strip()
    try:
        rev = int(val)
        return rev if rev >= 0 else None
    except ValueError:
        return None


def validate_enrollment_audio(data: bytes) -> tuple[bool, str]:
    """Validate audio constraints: WAV, 16 kHz mono PCM 16-bit, 3-30 seconds."""
    if len(data) < 44 or not (data[:4] == b"RIFF" and data[8:12] == b"WAVE"):
        return False, "Data is not a valid RIFF/WAVE file"
    try:
        with wave.open(io.BytesIO(data), "rb") as r:
            channels, width, rate, frames = r.getnchannels(), r.getsampwidth(), r.getframerate(), r.getnframes()
    except (wave.Error, EOFError) as exc:
        return False, f"Malformed WAV audio: {exc}"

    if channels != 1:
        return False, f"Audio must be mono (1 channel), got {channels}"
    if width != 2:
        return False, f"Audio must be 16-bit PCM, got {width * 8}-bit"
    if rate != 16000:
        return False, f"Sample rate must be 16000 Hz, got {rate} Hz"
    duration = frames / float(rate)
    if duration < 3.0:
        return False, f"Audio duration {duration:.2f}s is shorter than minimum 3.0s"
    if duration > 30.0:
        return False, f"Audio duration {duration:.2f}s exceeds maximum 30.0s"
    return True, ""


def _handle_consent_error(exc: voice_consent.VoiceConsentError):
    code, msg = exc.code, str(exc)
    if code in ("consent_required", "not_self"):
        return _problem(code.upper(), msg, 403)
    if code == "stale_consent":
        return _problem("PRECONDITION_FAILED", msg, 412)
    if code in ("already_granted", "no_active_consent"):
        return _problem(code.upper(), msg, 409)
    if code in ("statement_required", "statement_too_long", "invalid_embedding", "invalid_space", "invalid_user_id", "unknown_user"):
        return _problem("INVALID_PAYLOAD", msg, 400)
    return _problem("CONSENT_ERROR", msg, 500)


def _preflight(expected_ct: str | None = None) -> tuple[int | None, Any]:
    if not clinic_mode_enabled():
        abort(404)
    if not (current_user.is_authenticated and current_user.is_active and session.get("_user_id") == current_user.get_id()):
        return None, _problem("UNAUTHORIZED", "Authentication required", 401)
    try:
        actor_id = int(current_user.id)
    except (TypeError, ValueError):
        return None, _problem("UNAUTHORIZED", "Invalid session user id", 401)

    if expected_ct is not None:
        csrf_token = request.headers.get("X-CSRFToken") or request.headers.get("X-CSRF-Token")
        if not csrf_token:
            return None, _problem("FORBIDDEN", "CSRF token missing", 403)
        if current_app.config.get("WTF_CSRF_ENABLED", True):
            try:
                validate_csrf(csrf_token)
            except (ValidationError, CSRFError):
                return None, _problem("FORBIDDEN", "CSRF token invalid", 403)

        ct = (request.content_type or "").split(";")[0].strip().lower()
        if expected_ct == "audio/wav" and ct not in ("audio/wav", "audio/x-wav", "audio/wave"):
            return None, _problem("UNSUPPORTED_MEDIA_TYPE", "Content-Type must be audio/wav", 415)
        elif expected_ct != "audio/wav" and ct != expected_ct:
            return None, _problem("UNSUPPORTED_MEDIA_TYPE", f"Content-Type must be {expected_ct}", 415)

    return actor_id, None


def _idempotent_post(operation: str, actor_id: int, payload_bytes: bytes, executor):
    key = request.headers.get("Idempotency-Key", "").strip()
    if not (8 <= len(key) <= 128):
        return _problem("INVALID_PAYLOAD", "Idempotency-Key header must be 8-128 characters", 400)

    digest = hashlib.sha256(payload_bytes).hexdigest()
    scope = (actor_id, operation, key)

    while True:
        with _idempotency_lock:
            if scope in _idempotency_store:
                rec = _idempotency_store[scope]
                if rec.digest != digest:
                    return _problem("IDEMPOTENCY_CONFLICT", "Idempotency key reused with different payload", 409)
                resp = jsonify(rec.body)
                resp.status_code, resp.headers["ETag"], resp.headers["Cache-Control"] = rec.status_code, rec.etag, "no-store"
                return resp

            if scope in _in_flight:
                evt = _in_flight[scope]
                wait = True
            else:
                evt = _in_flight.setdefault(scope, threading.Event())
                wait = False
                break
        if wait:
            evt.wait(timeout=30)

    if_match = request.headers.get("If-Match")
    if not if_match:
        with _idempotency_lock:
            _in_flight.pop(scope, None)
            evt.set()
        return _problem("PRECONDITION_REQUIRED", "If-Match header is required", 428)

    expected_rev = _parse_if_match(if_match)
    if expected_rev is None:
        with _idempotency_lock:
            _in_flight.pop(scope, None)
            evt.set()
        return _problem("PRECONDITION_FAILED", "Invalid If-Match header format", 412)

    try:
        result = executor(expected_rev)
        if isinstance(result, tuple) and len(result) == 3:
            status_code, body, etag = result
            with _idempotency_lock:
                _idempotency_store[scope] = IdempotencyRecord(digest=digest, status_code=status_code, etag=etag, body=body)
                _in_flight.pop(scope, None)
                evt.set()
            resp = jsonify(body)
            resp.status_code, resp.headers["ETag"], resp.headers["Cache-Control"] = status_code, etag, "no-store"
            return resp
        with _idempotency_lock:
            _in_flight.pop(scope, None)
            evt.set()
        return result
    except Exception:
        with _idempotency_lock:
            _in_flight.pop(scope, None)
            evt.set()
        raise


@voice_enrollment_bp.route("/enrollment", methods=["GET"], endpoint="get_own_voice_enrollment")
def get_own_voice_enrollment():
    actor_id, err = _preflight()
    if err:
        return err
    if request.args.get("user_id") and request.args.get("user_id") != str(actor_id):
        return _problem("FORBIDDEN", "Foreign subject access is denied", 403)
    body, etag = _build_enrollment_state(actor_id)
    resp = jsonify(body)
    resp.status_code, resp.headers["ETag"], resp.headers["Cache-Control"] = 200, etag, "no-store"
    return resp


@voice_enrollment_bp.route("/enrollment", methods=["POST"], endpoint="enroll_own_voice_sample")
def enroll_own_voice_sample():
    actor_id, err = _preflight(expected_ct="audio/wav")
    if err:
        return err

    data = request.get_data()
    if len(data) > MAX_AUDIO_BYTES:
        return _problem("PAYLOAD_TOO_LARGE", f"Audio exceeds limit of {MAX_AUDIO_BYTES} bytes", 413)

    valid_audio, reason = validate_enrollment_audio(data)
    if not valid_audio:
        return _problem("INVALID_AUDIO", reason, 400)

    def _execute(expected_consent_id: int):
        if not voice_consent.has_active_consent(actor_id):
            return _problem("CONSENT_REQUIRED", "Active voice consent is required before enrollment", 403)

        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
                temp_path = tf.name
                tf.write(data)
            try:
                emb_res = speaker_client.embed_with_space(temp_path)
            except speaker_client.SpeakerServiceError as exc:
                logger.warning("Speaker service failure during enrollment: %s", exc)
                return _problem("DEPENDENCY_UNAVAILABLE", "Speaker service unavailable", 503)
        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    with open(temp_path, "wb") as f:
                        f.write(b"\x00" * min(len(data), 4096))
                except OSError:
                    pass
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass

        ref = voice_consent.EmbeddingRef(
            vector=struct.pack(f"<{len(emb_res.vector)}f", *emb_res.vector),
            dimension=len(emb_res.vector),
            space_id=emb_res.space_id,
        )
        try:
            voice_consent.enrol(user_id=actor_id, embedding_ref=ref, actor_user_id=actor_id, expected_consent_id=expected_consent_id)
        except voice_consent.VoiceConsentError as exc:
            return _handle_consent_error(exc)

        body, etag = _build_enrollment_state(actor_id)
        return 201, body, etag

    return _idempotent_post("enrollOwnVoiceSample", actor_id, data, _execute)


@voice_enrollment_bp.route("/consent", methods=["POST"], endpoint="grant_own_voice_consent")
def grant_own_voice_consent():
    actor_id, err = _preflight(expected_ct="application/json")
    if err:
        return err

    raw_data = request.get_data()
    if len(raw_data) > MAX_GRANT_BYTES:
        return _problem("PAYLOAD_TOO_LARGE", f"Payload exceeds {MAX_GRANT_BYTES} bytes limit", 413)

    try:
        payload = json.loads(raw_data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return _problem("INVALID_PAYLOAD", "Malformed JSON body", 400)

    if not isinstance(payload, dict) or set(payload.keys()) != {"statement_text"}:
        return _problem("INVALID_PAYLOAD", "Payload must contain only statement_text", 400)

    text = payload.get("statement_text")
    if not isinstance(text, str) or not text.strip() or len(text) > 20000:
        return _problem("INVALID_PAYLOAD", "statement_text must be non-blank up to 20000 chars", 400)

    def _execute(expected_consent_id: int):
        try:
            voice_consent.grant_consent(
                actor_user_id=actor_id, user_id=actor_id, statement_text=text, expected_consent_id=expected_consent_id
            )
        except voice_consent.VoiceConsentError as exc:
            return _handle_consent_error(exc)
        body, etag = _build_enrollment_state(actor_id)
        return 200, body, etag

    return _idempotent_post("grantOwnVoiceConsent", actor_id, raw_data, _execute)


@voice_enrollment_bp.route("/consent/revoke", methods=["POST"], endpoint="revoke_own_voice_consent")
def revoke_own_voice_consent():
    actor_id, err = _preflight(expected_ct="application/json")
    if err:
        return err

    raw_data = request.get_data()
    if len(raw_data) > MAX_REVOKE_BYTES:
        return _problem("PAYLOAD_TOO_LARGE", f"Payload exceeds {MAX_REVOKE_BYTES} bytes limit", 413)

    try:
        payload = json.loads(raw_data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return _problem("INVALID_PAYLOAD", "Malformed JSON body", 400)

    if not isinstance(payload, dict) or set(payload.keys()) != {"confirm"} or payload.get("confirm") is not True:
        return _problem("INVALID_PAYLOAD", "Payload must contain confirm: true", 400)

    def _execute(expected_consent_id: int):
        try:
            voice_consent.revoke_consent(
                actor_user_id=actor_id, user_id=actor_id, expected_consent_id=expected_consent_id
            )
        except voice_consent.VoiceConsentError as exc:
            return _handle_consent_error(exc)
        body, etag = _build_enrollment_state(actor_id)
        return 200, body, etag

    return _idempotent_post("revokeOwnVoiceConsent", actor_id, raw_data, _execute)
