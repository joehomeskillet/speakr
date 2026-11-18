"""WAV decoding for the speaker service. Memory only, nothing touches disk."""

import io
import wave

import numpy as np

TARGET_RATE = 16000


class AudioError(ValueError):
    """The request body is not usable WAV audio."""


def _is_wav(data: bytes) -> bool:
    return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE"


def _to_float(frames: bytes, width: int) -> np.ndarray:
    if width == 1:
        return (np.frombuffer(frames, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    if width == 2:
        return np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    if width == 3:
        raw = np.frombuffer(frames, dtype=np.uint8).reshape(-1, 3)
        ints = (
            raw[:, 0].astype(np.int32)
            | (raw[:, 1].astype(np.int32) << 8)
            | (raw[:, 2].astype(np.int32) << 16)
        )
        ints = np.where(ints & 0x800000, ints - 0x1000000, ints)
        return ints.astype(np.float32) / 8388608.0
    if width == 4:
        return np.frombuffer(frames, dtype="<i4").astype(np.float32) / 2147483648.0
    raise AudioError(f"unsupported sample width {width}")


def _resample(samples: np.ndarray, rate: int) -> np.ndarray:
    if rate == TARGET_RATE or samples.size == 0:
        return samples
    out_len = int(round(samples.size * TARGET_RATE / rate))
    positions = np.linspace(0, samples.size - 1, num=out_len)
    return np.interp(positions, np.arange(samples.size), samples).astype(np.float32)


def decode_wav(data: bytes) -> np.ndarray:
    """Return mono float32 samples at 16 kHz from PCM WAV bytes."""
    if not _is_wav(data):
        raise AudioError("body is not a WAV file")
    try:
        with wave.open(io.BytesIO(data), "rb") as reader:
            channels = reader.getnchannels()
            width = reader.getsampwidth()
            rate = reader.getframerate()
            frames = reader.readframes(reader.getnframes())
    except (wave.Error, EOFError) as exc:
        raise AudioError(f"unreadable WAV: {exc}") from exc
    if rate <= 0 or channels <= 0:
        raise AudioError("invalid WAV header")
    samples = _to_float(frames, width)
    usable = samples.size - samples.size % channels
    samples = samples[:usable].reshape(-1, channels).mean(axis=1).astype(np.float32)
    return _resample(samples, rate)


def duration_seconds(samples: np.ndarray) -> float:
    return float(samples.size) / TARGET_RATE
