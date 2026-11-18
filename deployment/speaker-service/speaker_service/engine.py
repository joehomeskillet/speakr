"""Model engine for the speaker service (sherpa-onnx, CPU only).

The app depends on the small `Engine` protocol, so tests inject a stub and
never load model files.
"""

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

EMBEDDING_MODEL_NAME = "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k"
SEGMENTATION_MODEL_NAME = "sherpa-onnx-pyannote-segmentation-3-0"


class Engine(Protocol):
    def info(self) -> dict: ...

    def embed(self, samples: np.ndarray) -> list[float]: ...

    def diarize(
        self, samples: np.ndarray, num_speakers: int | None, threshold: float | None
    ) -> list[dict]: ...


@dataclass(frozen=True)
class EngineConfig:
    segmentation_path: Path
    embedding_path: Path
    segmentation_sha256: str
    embedding_sha256: str
    num_threads: int = 4
    cluster_threshold: float = 0.6

    @classmethod
    def from_env(cls) -> "EngineConfig":
        model_dir = Path(os.environ.get("SPEAKER_MODEL_DIR", "/models"))
        return cls(
            segmentation_path=model_dir / SEGMENTATION_MODEL_NAME / "model.onnx",
            embedding_path=model_dir / f"{EMBEDDING_MODEL_NAME}.onnx",
            segmentation_sha256=os.environ.get("SPEAKER_SEG_MODEL_SHA256", ""),
            embedding_sha256=os.environ.get("SPEAKER_EMB_MODEL_SHA256", ""),
            num_threads=int(os.environ.get("SPEAKER_NUM_THREADS", "4")),
            cluster_threshold=float(os.environ.get("SPEAKER_CLUSTER_THRESHOLD", "0.6")),
        )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _verified_hash(path: Path, expected: str) -> str:
    actual = _sha256(path)
    if expected and actual != expected:
        raise RuntimeError(f"hash mismatch for {path.name}: expected {expected}, got {actual}")
    return actual


class SherpaEngine:
    """Real engine. Loaded once; calls are serialised by the app."""

    def __init__(self, config: EngineConfig):
        import sherpa_onnx

        self._sherpa = sherpa_onnx
        self._config = config
        self._hashes = {
            "segmentation": _verified_hash(config.segmentation_path, config.segmentation_sha256),
            "embedding": _verified_hash(config.embedding_path, config.embedding_sha256),
        }
        extractor_config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(config.embedding_path), num_threads=config.num_threads
        )
        if not extractor_config.validate():
            raise RuntimeError("invalid speaker embedding config")
        self._extractor = sherpa_onnx.SpeakerEmbeddingExtractor(extractor_config)
        self._diarizer = self._build_diarizer(num_clusters=-1, threshold=config.cluster_threshold)
        self._diarizer_key = (-1, config.cluster_threshold)

    def _build_diarizer(self, num_clusters: int, threshold: float):
        s = self._sherpa
        config = s.OfflineSpeakerDiarizationConfig(
            segmentation=s.OfflineSpeakerSegmentationModelConfig(
                pyannote=s.OfflineSpeakerSegmentationPyannoteModelConfig(
                    model=str(self._config.segmentation_path)
                ),
                num_threads=self._config.num_threads,
            ),
            embedding=s.SpeakerEmbeddingExtractorConfig(
                model=str(self._config.embedding_path), num_threads=self._config.num_threads
            ),
            clustering=s.FastClusteringConfig(num_clusters=num_clusters, threshold=threshold),
            min_duration_on=0.3,
            min_duration_off=0.5,
        )
        if not config.validate():
            raise RuntimeError("invalid diarization config")
        return s.OfflineSpeakerDiarization(config)

    def info(self) -> dict:
        return {
            "sherpa_onnx": self._sherpa.__version__,
            "segmentation": {
                "name": SEGMENTATION_MODEL_NAME,
                "sha256": self._hashes["segmentation"],
            },
            "embedding": {
                "name": EMBEDDING_MODEL_NAME,
                "sha256": self._hashes["embedding"],
                "dim": int(self._extractor.dim),
            },
        }

    def embed(self, samples: np.ndarray) -> list[float]:
        stream = self._extractor.create_stream()
        stream.accept_waveform(sample_rate=16000, waveform=samples)
        stream.input_finished()
        if not self._extractor.is_ready(stream):
            raise RuntimeError("not enough audio for an embedding")
        return [float(v) for v in self._extractor.compute(stream)]

    def diarize(
        self, samples: np.ndarray, num_speakers: int | None, threshold: float | None
    ) -> list[dict]:
        key = (
            num_speakers if num_speakers else -1,
            threshold if threshold is not None else self._config.cluster_threshold,
        )
        if key != self._diarizer_key:
            self._diarizer = self._build_diarizer(*key)
            self._diarizer_key = key
        result = self._diarizer.process(samples).sort_by_start_time()
        return [
            {"start": round(seg.start, 3), "end": round(seg.end, 3), "speaker": f"SPEAKER_{seg.speaker:02d}"}
            for seg in result
        ]
