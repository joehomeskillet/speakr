"""Server-side media decoder adapter for the clinic fork.

Detects the container by magic bytes, then decodes to 16 kHz mono PCM WAV
inside a caller-supplied directory. The original file is only read, never
modified; its sha256 is reported so callers can prove that.

* WAV (RIFF/WAVE) and MP3 are decoded with ``ffmpeg``.
* DSS and DS2 are decoded with an external ``dss-codec`` binary whose path
  comes from ``CLINIC_DSS_CODEC_BIN`` (pinned upstream commit
  ``c38d3192239005b0570c5987cbde62e69dd7dbbe``). Without it the status is
  ``decoder_missing``; there is no fallback.
* Everything else is ``unsupported``.

Subprocesses use argument lists (no shell), a hard timeout, an output size cap
and no stdin. Network isolation is a deployment property (no egress for the
media worker); neither tool is given a URL or protocol that needs the network:
ffmpeg is restricted to ``-protocol_whitelist file``.

Real DS2 decoding is not proven here: it needs the pinned binary and a real
DS2 fixture. The dss-codec call convention assumed is ``<bin> <input> <output.wav>``.
"""
from __future__ import annotations

import hashlib
import os
import subprocess  # nosec B404
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

DSS_CODEC_BIN_ENV = 'CLINIC_DSS_CODEC_BIN'
DSS_CODEC_PINNED_COMMIT = 'c38d3192239005b0570c5987cbde62e69dd7dbbe'

DEFAULT_TIMEOUT_S = 120
# 16 kHz * 2 bytes * 3600 s = 115 MB per hour of audio; cap at about 4 hours.
DEFAULT_MAX_OUTPUT_BYTES = 480 * 1024 * 1024

OK, UNSUPPORTED, CORRUPT, EMPTY = 'ok', 'unsupported', 'corrupt', 'empty'
DECODER_MISSING, TIMEOUT = 'decoder_missing', 'timeout'


@dataclass(frozen=True)
class DecodeResult:
    status: str
    container: Optional[str] = None      # 'wav' | 'dss' | 'ds2' | 'mp3' | None
    input_sha256: Optional[str] = None   # hash of the untouched original
    output_path: Optional[str] = None
    output_sha256: Optional[str] = None
    duration_s: Optional[float] = None
    detail: str = ''


def detect_container(head: bytes) -> Optional[str]:
    """Return the container name for the first bytes of a file, or None."""
    if head[:4] == b'RIFF' and head[8:12] == b'WAVE':
        return 'wav'
    if head[:4] == b'\x02dss':
        return 'dss'
    if head[:4] == b'\x03ds2':
        return 'ds2'
    if head[:3] == b'ID3' or (len(head) >= 2 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0):
        return 'mp3'
    return None


def sha256_file(path) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _run(cmd, timeout_s):
    """Run cmd; return (returncode, stderr_tail) or None on timeout."""
    try:
        proc = subprocess.run(  # nosec B603
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, timeout=timeout_s, check=False)
    except subprocess.TimeoutExpired:
        return None
    return proc.returncode, proc.stderr.decode('utf-8', 'replace')[-500:]


def _wav_duration(path: Path) -> float:
    with wave.open(str(path), 'rb') as wav:
        return wav.getnframes() / float(wav.getframerate())


def decode_to_wav(src, out_dir, *, timeout_s: int = DEFAULT_TIMEOUT_S,
                  max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
                  ffmpeg_bin: str = 'ffmpeg') -> DecodeResult:
    """Decode ``src`` to ``<out_dir>/<stem>.16k.wav`` (16 kHz mono PCM)."""
    src = Path(src)
    out_dir = Path(out_dir)
    if not src.is_file():
        return DecodeResult(CORRUPT, detail='input is not a regular file')
    in_hash = sha256_file(src)
    with open(src, 'rb') as handle:
        head = handle.read(16)
    if not head:
        return DecodeResult(EMPTY, input_sha256=in_hash, detail='zero-byte input')

    container = detect_container(head)
    if container is None:
        return DecodeResult(UNSUPPORTED, input_sha256=in_hash, detail='unknown magic bytes')

    def result(status, detail='', **extra):
        return DecodeResult(status, container=container, input_sha256=in_hash,
                            detail=detail, **extra)

    out = out_dir / (src.stem + '.16k.wav')
    if container in ('dss', 'ds2'):
        binary = os.environ.get(DSS_CODEC_BIN_ENV, '').strip()
        if not binary:
            return result(DECODER_MISSING, f'{DSS_CODEC_BIN_ENV} not set')
        cmd = [binary, str(src), str(out)]
    else:
        cmd = [ffmpeg_bin, '-nostdin', '-v', 'error', '-protocol_whitelist', 'file',
               '-threads', '1', '-i', str(src), '-vn', '-ac', '1', '-ar', '16000',
               '-c:a', 'pcm_s16le', '-fs', str(max_output_bytes + 1), '-y', str(out)]

    try:
        outcome = _run(cmd, timeout_s)
    except FileNotFoundError:
        return result(DECODER_MISSING, f'executable not found: {cmd[0]}')
    except OSError as exc:
        return result(DECODER_MISSING, f'executable not runnable: {exc}')
    if outcome is None:
        out.unlink(missing_ok=True)
        return result(TIMEOUT, f'decoder exceeded {timeout_s}s')
    returncode, stderr = outcome
    if returncode != 0 or not out.is_file():
        out.unlink(missing_ok=True)
        return result(CORRUPT, stderr.strip() or f'decoder exit {returncode}')
    if out.stat().st_size > max_output_bytes:
        out.unlink(missing_ok=True)
        return result(CORRUPT, 'output exceeds size cap')
    try:
        duration = _wav_duration(out)
    except (wave.Error, EOFError, ZeroDivisionError):
        out.unlink(missing_ok=True)
        return result(CORRUPT, 'decoder output is not a valid WAV')
    if duration <= 0:
        out.unlink(missing_ok=True)
        return result(EMPTY, 'decoded audio has no samples')
    return result(OK, output_path=str(out), output_sha256=sha256_file(out),
                  duration_s=duration)
