"""OpenVINO-GenAI-Engine und Audio-Dekodierung des GPU-ASR-Dienstes.

Der Dienst haelt genau eine WhisperPipeline auf einem festen Device. Es gibt kein
dynamisches Nachladen anderer Modelle: GPU.0 wird mit anderen Diensten geteilt.
Audio wird nur im Speicher verarbeitet (ffmpeg liest von stdin, schreibt nach stdout).
"""
from __future__ import annotations

import subprocess
from array import array

SAMPLE_RATE = 16000


class AudioDecodeError(ValueError):
    """Die Eingabe ist kein dekodierbares Audio."""


class EngineError(RuntimeError):
    """Die Engine (Device/Pipeline) konnte die Anfrage nicht verarbeiten."""


def decode_audio(data: bytes) -> list[float]:
    """Beliebiges Audioformat -> mono float32 @ 16 kHz, nur im Speicher."""
    if not data:
        raise AudioDecodeError("empty upload")
    try:
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", "pipe:0", "-f", "f32le", "-ac", "1", "-ar", str(SAMPLE_RATE), "pipe:1"],
            input=data, capture_output=True, check=False, timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AudioDecodeError(f"ffmpeg unavailable or timed out: {exc}") from exc
    if proc.returncode != 0 or not proc.stdout:
        raise AudioDecodeError("audio could not be decoded")
    samples = array("f")
    samples.frombytes(proc.stdout[: len(proc.stdout) - len(proc.stdout) % 4])
    return samples.tolist()


class OpenVinoWhisperEngine:
    """Duenner Wrapper um openvino_genai.WhisperPipeline (word_timestamps im Konstruktor)."""

    def __init__(self, model_dir: str, device: str, model_name: str):
        import openvino as ov
        import openvino_genai as genai

        self.model_name = model_name
        self.device = device
        self.versions = {"openvino": ov.get_version(), "openvino_genai": getattr(genai, "__version__", "unknown")}
        self._pipe = genai.WhisperPipeline(model_dir, device, word_timestamps=True)

    def transcribe(self, samples, *, language=None, prompt=None, hotwords=None,
                   timestamps=False) -> dict:
        cfg = self._pipe.get_generation_config()
        cfg.task = "transcribe"
        if language:
            cfg.language = f"<|{language}|>"
        if prompt:
            cfg.initial_prompt = prompt
        if hotwords:
            cfg.hotwords = hotwords
        if timestamps:
            cfg.return_timestamps = True
            cfg.word_timestamps = True
        try:
            result = self._pipe.generate(samples, cfg)
        except Exception as exc:  # OpenCL/Device-Fehler tauchen als RuntimeError auf
            raise EngineError(str(exc)[:500]) from exc
        out = {"text": result.texts[0].strip(), "segments": [], "words": []}
        if timestamps:
            out["segments"] = [{"start": c.start_ts, "end": c.end_ts, "text": c.text} for c in (result.chunks or [])]
            out["words"] = [{"word": w.word, "start": w.start_ts, "end": w.end_ts} for w in (result.words or [])]
        return out


def load_openvino_engine(model_dir: str, device: str, model_name: str) -> OpenVinoWhisperEngine:
    return OpenVinoWhisperEngine(model_dir, device, model_name)
