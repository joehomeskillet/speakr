"""Flask app: POST /diarize, POST /embed, GET /healthz.

Audio is only ever held in memory. No temp files, no audio in logs.
"""

import logging
import os
import threading
from typing import Callable

from flask import Flask, jsonify, request

from .audio import AudioError, decode_wav, duration_seconds
from .engine import Engine

log = logging.getLogger("speaker_service")

DEFAULT_MAX_BYTES = 256 * 1024 * 1024
DEFAULT_MIN_EMBED_SECONDS = 3.0
DEFAULT_MIN_DIARIZE_SECONDS = 1.0


def _error(status: int, code: str, detail: str = ""):
    return jsonify({"error": code, "detail": detail}), status


def create_app(
    engine_factory: Callable[[], Engine],
    max_bytes: int | None = None,
    min_embed_seconds: float | None = None,
    min_diarize_seconds: float | None = None,
) -> Flask:
    """`engine_factory` loads the models; tests pass a stub instead."""
    app = Flask(__name__)
    limit = max_bytes or int(os.environ.get("SPEAKER_MAX_BYTES", DEFAULT_MAX_BYTES))
    min_embed = (
        min_embed_seconds
        if min_embed_seconds is not None
        else float(os.environ.get("SPEAKER_MIN_EMBED_SECONDS", DEFAULT_MIN_EMBED_SECONDS))
    )
    min_diarize = (
        min_diarize_seconds
        if min_diarize_seconds is not None
        else float(os.environ.get("SPEAKER_MIN_DIARIZE_SECONDS", DEFAULT_MIN_DIARIZE_SECONDS))
    )
    app.config["MAX_CONTENT_LENGTH"] = limit

    state: dict = {"engine": None, "error": None}
    lock = threading.Lock()
    try:
        state["engine"] = engine_factory()
    except Exception as exc:  # noqa: BLE001 - reported via /healthz, never raised at import
        state["error"] = f"{type(exc).__name__}: {exc}"
        log.error("model load failed: %s", state["error"])

    @app.errorhandler(413)
    def too_large(_exc):
        return _error(413, "payload_too_large", f"limit {limit} bytes")

    def read_samples(minimum: float):
        engine = state["engine"]
        if engine is None:
            return None, _error(503, "models_not_loaded", state["error"] or "")
        data = request.get_data(cache=False)
        if not data:
            return None, _error(400, "empty_body", "send the WAV as raw request body")
        try:
            samples = decode_wav(data)
        except AudioError as exc:
            return None, _error(400, "invalid_wav", str(exc))
        if duration_seconds(samples) < minimum:
            return None, _error(
                422, "audio_too_short", f"need at least {minimum:g} seconds of audio"
            )
        return samples, None

    @app.get("/healthz")
    def healthz():
        engine = state["engine"]
        if engine is None:
            return jsonify({"models_loaded": False, "error": state["error"]}), 503
        return jsonify({"models_loaded": True, **engine.info()})

    @app.post("/embed")
    def embed():
        samples, failure = read_samples(min_embed)
        if failure:
            return failure
        with lock:
            vector = state["engine"].embed(samples)
        return jsonify(
            {
                "model": state["engine"].info()["embedding"]["name"],
                "dim": len(vector),
                "duration_s": round(duration_seconds(samples), 3),
                "embedding": vector,
            }
        )

    @app.post("/diarize")
    def diarize():
        num_speakers = request.args.get("num_speakers", type=int)
        threshold = request.args.get("threshold", type=float)
        for name, parsed in (("num_speakers", num_speakers), ("threshold", threshold)):
            if name in request.args and parsed is None:
                return _error(400, "invalid_parameter", f"{name} is not a number")
        if num_speakers is not None and num_speakers < 1:
            return _error(400, "invalid_parameter", "num_speakers must be >= 1")
        if threshold is not None and not 0.0 < threshold < 1.0:
            return _error(400, "invalid_parameter", "threshold must be in (0, 1)")
        samples, failure = read_samples(min_diarize)
        if failure:
            return failure
        with lock:
            segments = state["engine"].diarize(samples, num_speakers, threshold)
        return jsonify(
            {
                "duration_s": round(duration_seconds(samples), 3),
                "num_speakers": len({seg["speaker"] for seg in segments}),
                "segments": segments,
            }
        )

    return app
