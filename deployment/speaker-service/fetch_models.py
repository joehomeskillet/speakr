"""Image-build step: download the two models and verify their SHA-256.

Usage: fetch_models.py <target-dir>
Expected hashes come from the Dockerfile ARGs via the environment. A hash
mismatch aborts the build. Nothing is written to the git repository.
"""

import hashlib
import os
import sys
import tarfile
import urllib.request
from pathlib import Path

SEG_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    "speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
)
EMB_NAME = "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"
EMB_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    f"speaker-recongition-models/{EMB_NAME}"
)


def download(url: str, expected: str, dest: Path) -> None:
    digest = hashlib.sha256()
    with urllib.request.urlopen(url, timeout=300) as response, dest.open("wb") as out:
        for block in iter(lambda: response.read(1 << 20), b""):
            digest.update(block)
            out.write(block)
    if digest.hexdigest() != expected:
        dest.unlink()
        sys.exit(f"SHA-256 mismatch for {url}: expected {expected}, got {digest.hexdigest()}")


def main(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    seg_archive = target / "seg.tar.bz2"
    download(SEG_URL, os.environ["SPEAKER_SEG_ARCHIVE_SHA256"], seg_archive)
    with tarfile.open(seg_archive, "r:bz2") as archive:
        archive.extractall(target, filter="data")
    seg_archive.unlink()
    download(EMB_URL, os.environ["SPEAKER_EMB_MODEL_SHA256"], target / EMB_NAME)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
