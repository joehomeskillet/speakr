"""OpenAI-kompatibler ASR-Dienst (POST /v1/audio/transcriptions) auf OpenVINO-GPU.

Ein Modell, einmal geladen; Requests laufen ueber einen Ein-Thread-Executor strikt seriell.
Engine und Decoder sind injizierbar (Contract-Tests ohne GPU). Audio wird nicht persistiert.
"""
from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

from flask import Flask, jsonify, request

import asr_engine as engine_mod

SAMPLE_RATE = engine_mod.SAMPLE_RATE
LANG_RE = re.compile(r"^[a-z]{2,3}$")
FORMATS = ("json", "verbose_json")
PROMPT_WORDS_CEILING = 55  # stabil gemessen; konfigurierbar nur nach unten


def _env_config() -> dict:
    return {
        "MODEL_DIR": os.environ.get("ASR_MODEL_DIR", "/models/whisper-large-v3-turbo-int8-ov"),
        "MODEL_NAME": os.environ.get("ASR_MODEL_NAME", "whisper-large-v3-turbo-int8-ov"),
        "DEVICE": os.environ.get("ASR_DEVICE", "GPU.0"),
        "TIMEOUT_S": float(os.environ.get("ASR_TIMEOUT_S", "300")),
        "PROMPTS_ENABLED": os.environ.get("ASR_PROMPTS_ENABLED", "0").lower() in ("1", "true", "yes"),
        "PROMPT_MAX_WORDS": int(os.environ.get("ASR_PROMPT_MAX_WORDS", str(PROMPT_WORDS_CEILING))),
        "MAX_CONTENT_LENGTH": int(os.environ.get("ASR_MAX_UPLOAD_BYTES", str(512 * 1024 * 1024))),
    }


def _limit_prompts(prompt, hotwords, enabled, max_words, log):
    """Prompt/Hotwords sind standardmaessig aus; sonst gemeinsames Budget von max_words Woertern.

    Gemessen: oberhalb ~88 Woertern wirft die Pipeline `max_length > prompt_length`, lange
    Prompts erzeugen Auslassungen. Die Laenge darf nie zu einem Request-Fehler fuehren.
    """
    prompt, hotwords = (prompt or "").split(), (hotwords or "").split()
    if not (prompt or hotwords):
        return None, None
    if not enabled:
        log.warning("prompt/hotwords ignored: ASR_PROMPTS_ENABLED is off (%d words)", len(prompt) + len(hotwords))
        return None, None
    total = len(prompt) + len(hotwords)
    if total > max_words:
        log.warning("prompt/hotwords truncated from %d to %d words", total, max_words)
        prompt = prompt[:max_words]
        hotwords = hotwords[: max_words - len(prompt)]
    return " ".join(prompt) or None, " ".join(hotwords) or None


def _error(status: int, code: str, message: str):
    resp = jsonify({"error": {"message": message, "type": code, "code": code}})
    resp.status_code = status
    return resp


def create_app(engine_loader=None, decoder=None, config: dict | None = None) -> Flask:
    cfg = {**_env_config(), **(config or {})}
    cfg["PROMPT_MAX_WORDS"] = max(0, min(cfg["PROMPT_MAX_WORDS"], PROMPT_WORDS_CEILING))
    loader = engine_loader or engine_mod.load_openvino_engine
    decode = decoder or engine_mod.decode_audio

    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = cfg["MAX_CONTENT_LENGTH"]
    # Genau ein Worker: serialisiert alle Requests auf der einen GPU-Pipeline.
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="asr-engine")
    state = {"engine": None, "load_error": None}
    app.extensions["gpu_asr"] = state
    try:
        state["engine"] = loader(cfg["MODEL_DIR"], cfg["DEVICE"], cfg["MODEL_NAME"])
    except Exception as exc:  # healthz meldet 503; der Prozess bleibt fuer den Fallback erreichbar
        state["load_error"] = f"{type(exc).__name__}: {str(exc)[:300]}"

    accepted_models = {cfg["MODEL_NAME"], "whisper-1"}

    @app.errorhandler(413)
    def _too_large(_exc):
        return _error(413, "payload_too_large", "upload exceeds ASR_MAX_UPLOAD_BYTES")

    @app.get("/healthz")
    def healthz():
        eng = state["engine"]
        body = {
            "status": "ok" if eng else "unavailable",
            "model_loaded": eng is not None,
            "model": cfg["MODEL_NAME"],
            "device": getattr(eng, "device", cfg["DEVICE"]),
            "versions": getattr(eng, "versions", {}),
        }
        if state["load_error"]:
            body["error"] = state["load_error"]
        resp = jsonify(body)
        resp.status_code = 200 if eng else 503
        return resp

    @app.post("/v1/audio/transcriptions")
    def transcriptions():
        eng = state["engine"]
        if eng is None:
            return _error(503, "engine_unavailable", state["load_error"] or "model not loaded")
        upload = request.files.get("file")
        if upload is None:
            return _error(400, "invalid_request", "multipart field 'file' is required")
        model = request.form.get("model")
        if model and model not in accepted_models:
            return _error(404, "model_not_found", f"only '{cfg['MODEL_NAME']}' is served")
        fmt = request.form.get("response_format", "json")
        if fmt not in FORMATS:
            return _error(400, "invalid_request", f"response_format must be one of {', '.join(FORMATS)}")
        language = (request.form.get("language") or "").strip().lower() or None
        if language and not LANG_RE.match(language):
            return _error(400, "invalid_request", "language must be an ISO 639 code such as 'de'")
        try:
            timeout = min(float(request.form.get("timeout", cfg["TIMEOUT_S"])), cfg["TIMEOUT_S"])
        except ValueError:
            return _error(400, "invalid_request", "timeout must be a number of seconds")
        if timeout <= 0:
            return _error(400, "invalid_request", "timeout must be positive")

        try:
            samples = decode(upload.read())
        except ValueError as exc:
            return _error(400, "invalid_audio", str(exc))

        prompt, hotwords = _limit_prompts(
            request.form.get("prompt"), request.form.get("hotwords"),
            cfg["PROMPTS_ENABLED"], cfg["PROMPT_MAX_WORDS"], app.logger,
        )
        verbose = fmt == "verbose_json"
        future = executor.submit(
            eng.transcribe, samples,
            language=language,
            prompt=prompt,
            hotwords=hotwords,
            timestamps=verbose,
        )
        try:
            result = future.result(timeout=timeout)
        except FutureTimeout:
            future.cancel()
            return _error(504, "engine_timeout", f"transcription exceeded {timeout:g} s")
        except Exception as exc:
            return _error(500, "engine_error", f"{type(exc).__name__}: {str(exc)[:300]}")

        if not verbose:
            return jsonify({"text": result["text"]})
        return jsonify({
            "task": "transcribe",
            "language": language,
            "duration": round(len(samples) / SAMPLE_RATE, 3),
            "text": result["text"],
            "segments": [{"id": i, **seg} for i, seg in enumerate(result["segments"])],
            "words": result["words"],
        })

    return app


def create_wsgi_app() -> Flask:
    return create_app()
