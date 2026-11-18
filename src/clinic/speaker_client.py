"""Client for the clinic speaker service (diarization and voice embeddings).

The service is a separate local HTTP process. Contract:

    GET  /healthz  -> {"status": "ok", "model": "<model id>"}
    POST /diarize  -> body = WAV bytes (Content-Type audio/wav)
                      {"segments": [{"start": s, "end": s, "speaker": "SPEAKER_00"}, ...]}
    POST /embed    -> body = WAV bytes
                      {"embedding": [float, ...]}

The base URL comes from ``CLINIC_SPEAKER_SERVICE_URL``. Every call has a hard
timeout and is tried exactly once: no retries, so a slow or dead service can
never pile up work. Segments are returned in the shape of the ASR segments
(``speaker``, ``start_time``, ``end_time`` in seconds, label ``SPEAKER_NN``).

Embeddings live in ``VoiceEmbeddingSpace`` rows. Each model reported by
``/healthz`` gets its own space (matched by ``backend_fingerprint``); an
existing space is only touched to refresh ``last_seen_at``. Nothing here
changes the current or legacy space settings.

``diarize_recording`` is a plain function. Nothing in processing calls it yet.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import socket
import urllib.error
import urllib.request
from datetime import datetime
from typing import NamedTuple

logger = logging.getLogger(__name__)

ENV_URL = "CLINIC_SPEAKER_SERVICE_URL"
DEFAULT_TIMEOUT_S = 120.0
HEALTH_TIMEOUT_S = 5.0
FINGERPRINT_PREFIX = "speaker-service:"
_LABEL_RE = re.compile(r"^SPEAKER_\d{2,}$")
_MAX_FINGERPRINT = 64


class SpeakerServiceError(Exception):
    """Base class for every failure of the speaker service client."""


class SpeakerServiceNotConfigured(SpeakerServiceError):
    """CLINIC_SPEAKER_SERVICE_URL is not set."""


class SpeakerServiceUnavailable(SpeakerServiceError):
    """The service cannot be reached (connection refused, DNS, reset)."""


class SpeakerServiceTimeout(SpeakerServiceError):
    """The service did not answer within the timeout."""


class SpeakerServiceHTTPError(SpeakerServiceError):
    """The service answered with a non-2xx status."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


class SpeakerServiceBadResponse(SpeakerServiceError):
    """The service answered 2xx but the body does not match the contract."""


class EmbeddingResult(NamedTuple):
    space_id: int
    model: str
    vector: list


def base_url() -> str:
    url = os.environ.get(ENV_URL, "").strip().rstrip("/")
    if not url:
        raise SpeakerServiceNotConfigured(f"{ENV_URL} is not set")
    return url


def _request(method: str, path: str, *, body: bytes | None, timeout: float) -> dict:
    req = urllib.request.Request(base_url() + path, data=body, method=method)
    if body is not None:
        req.add_header("Content-Type", "audio/wav")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raise SpeakerServiceHTTPError(exc.code, f"{method} {path} -> HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (socket.timeout, TimeoutError)):
            raise SpeakerServiceTimeout(f"{method} {path} timed out after {timeout}s") from exc
        raise SpeakerServiceUnavailable(f"{method} {path} failed: {exc.reason}") from exc
    except (socket.timeout, TimeoutError) as exc:
        raise SpeakerServiceTimeout(f"{method} {path} timed out after {timeout}s") from exc
    except OSError as exc:
        raise SpeakerServiceUnavailable(f"{method} {path} failed: {exc}") from exc
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise SpeakerServiceBadResponse(f"{method} {path}: body is not JSON") from exc
    if not isinstance(data, dict):
        raise SpeakerServiceBadResponse(f"{method} {path}: body is not a JSON object")
    return data


def _num(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise SpeakerServiceBadResponse("segment time is not a finite number")
    return float(value)


def health(timeout: float = HEALTH_TIMEOUT_S) -> str:
    """Return the model id the service reports. Raises if it is not healthy."""
    data = _request("GET", "/healthz", body=None, timeout=timeout)
    if data.get("status") != "ok":
        raise SpeakerServiceBadResponse("healthz: status is not ok")
    model = data.get("model")
    if not isinstance(model, str) or not model.strip():
        raise SpeakerServiceBadResponse("healthz: model id missing")
    return model.strip()


def _read_wav(wav_path) -> bytes:
    with open(wav_path, "rb") as fh:
        return fh.read()


def diarize(wav_path, timeout: float = DEFAULT_TIMEOUT_S) -> list[dict]:
    """POST the WAV to /diarize and return validated ASR-shaped segments."""
    data = _request("POST", "/diarize", body=_read_wav(wav_path), timeout=timeout)
    raw = data.get("segments")
    if not isinstance(raw, list):
        raise SpeakerServiceBadResponse("diarize: 'segments' is not a list")
    out = []
    for seg in raw:
        if not isinstance(seg, dict):
            raise SpeakerServiceBadResponse("diarize: segment is not an object")
        start, end = _num(seg.get("start")), _num(seg.get("end"))
        speaker = seg.get("speaker")
        if end < start or start < 0:
            raise SpeakerServiceBadResponse("diarize: segment times out of order")
        if not isinstance(speaker, str) or not _LABEL_RE.match(speaker):
            raise SpeakerServiceBadResponse("diarize: speaker label is not SPEAKER_NN")
        out.append({"speaker": speaker, "start_time": start, "end_time": end})
    out.sort(key=lambda s: (s["start_time"], s["end_time"]))
    return out


def embed(wav_path, timeout: float = DEFAULT_TIMEOUT_S) -> list[float]:
    """POST the WAV to /embed and return the vector as a list of floats."""
    data = _request("POST", "/embed", body=_read_wav(wav_path), timeout=timeout)
    vec = data.get("embedding")
    if not isinstance(vec, list) or not vec:
        raise SpeakerServiceBadResponse("embed: 'embedding' is not a non-empty list")
    return [_num(x) for x in vec]


def model_fingerprint(model: str) -> str:
    """Stable fingerprint for a model id, within the 64 character column."""
    fingerprint = FINGERPRINT_PREFIX + model
    if len(fingerprint) <= _MAX_FINGERPRINT:
        return fingerprint
    digest = hashlib.sha256(model.encode("utf-8")).hexdigest()[:32]
    return FINGERPRINT_PREFIX + digest


def space_for_model(model: str, dimension: int) -> int:
    """Return the id of the embedding space of this model, creating it once.

    An existing space is never rewritten; only ``last_seen_at`` is refreshed.
    A model that suddenly returns another dimension is an error, not a new space.
    """
    from src.database import db
    from src.models import VoiceEmbeddingSpace

    fingerprint = model_fingerprint(model)
    space = (VoiceEmbeddingSpace.query.filter_by(backend_fingerprint=fingerprint)
             .order_by(VoiceEmbeddingSpace.id).first())
    if space is not None:
        if space.dimension is not None and space.dimension != dimension:
            raise SpeakerServiceBadResponse(
                f"model {model!r} returned {dimension} dimensions, space {space.id} has {space.dimension}")
        space.last_seen_at = datetime.utcnow()
        db.session.commit()
        return space.id
    space = VoiceEmbeddingSpace(dimension=dimension, backend_fingerprint=fingerprint)
    db.session.add(space)
    db.session.commit()
    logger.info("Registered speaker service embedding space %s (%s, %s dimensions)",
                space.id, model, dimension)
    return space.id


def embed_with_space(wav_path, timeout: float = DEFAULT_TIMEOUT_S) -> EmbeddingResult:
    """Embed a WAV and register the space of the reporting model."""
    model = health()
    vector = embed(wav_path, timeout=timeout)
    return EmbeddingResult(space_for_model(model, len(vector)), model, vector)


def diarize_recording(recording, wav_path, timeout: float = DEFAULT_TIMEOUT_S) -> list[dict]:
    """Diarize the WAV of a recording and return ASR-shaped segments.

    Does not write anything and is not called from processing yet.
    """
    health()
    segments = diarize(wav_path, timeout=timeout)
    logger.info("Diarized recording %s: %d segments, %d speakers",
                getattr(recording, "id", None), len(segments), len({s["speaker"] for s in segments}))
    return segments
