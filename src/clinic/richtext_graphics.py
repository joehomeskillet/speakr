"""Scoped immutable local PNG objects for canonical document references.

The caller supplies an authenticated actor ID and a private, application-owned
storage root. Every access rechecks the recording policy. Metadata is derived
from the persisted PNG header, never from client claims. No routes or document
schema changes are registered by importing this module.
"""

from __future__ import annotations

import hashlib
import os
import re
import struct
import tempfile
import zlib
from dataclasses import dataclass
from pathlib import Path

from src.clinic import policy
from src.clinic.config import clinic_mode_enabled
from src.clinic.models import ClinicDictation
from src.clinic.rich_text_doc import RichTextError
from src.database import db
from src.models import Recording, User
from src.services.storage.locator import local_path_from_key

MAX_BYTES = 2 * 1024 * 1024
MAX_DIMENSION = 4096
MAX_PIXELS = 4_000_000
_SIGNATURE = b"\x89PNG\r\n\x1a\n"


@dataclass(frozen=True)
class GraphicReference:
    """Bounded metadata suitable for a canonical node's object reference."""

    dictation_id: str
    object_id: str
    width: int
    height: int
    size_bytes: int
    content_type: str = "image/png"


def _dimensions(data: bytes) -> tuple[int, int]:
    """Validate a bounded PNG including CRCs, deflate stream and scanline filters."""
    if not isinstance(data, bytes) or not data:
        raise RichTextError("invalid_graphic")
    if len(data) > MAX_BYTES:
        raise RichTextError("graphic_too_large")
    if not data.startswith(_SIGNATURE):
        raise RichTextError("unsupported_graphic")
    # shortcut: only non-interlaced 8-bit grayscale/RGB(+alpha) without ancillary
    # chunks; add a pinned image decoder when palette/interlace/other formats are needed.
    offset = 8
    header = None
    compressed = bytearray()
    ended = False
    while offset + 12 <= len(data):
        size = int.from_bytes(data[offset:offset + 4], "big")
        kind = data[offset + 4:offset + 8]
        end = offset + 12 + size
        if end > len(data):
            raise RichTextError("invalid_graphic")
        chunk = data[offset + 8:end - 4]
        if zlib.crc32(kind + chunk) != int.from_bytes(data[end - 4:end], "big"):
            raise RichTextError("invalid_graphic")
        if kind == b"IHDR" and offset == 8 and size == 13:
            width, height, depth, color, compression, filtering, interlace = struct.unpack(
                ">IIBBBBB", chunk,
            )
            if not (0 < width <= MAX_DIMENSION and 0 < height <= MAX_DIMENSION
                    and width * height <= MAX_PIXELS):
                raise RichTextError("graphic_too_large")
            if depth != 8 or color not in (0, 2, 4, 6) or (compression, filtering, interlace) != (0, 0, 0):
                raise RichTextError("unsupported_graphic")
            header = (width, height, {0: 1, 2: 3, 4: 2, 6: 4}[color])
        elif kind == b"IDAT" and header is not None:
            compressed.extend(chunk)
        elif kind == b"IEND" and size == 0 and compressed and end == len(data):
            ended = True
            break
        else:
            # Reject SVG, animation, embedded text/metadata and unknown chunks.
            raise RichTextError("unsupported_graphic")
        offset = end
    if not ended or header is None:
        raise RichTextError("invalid_graphic")
    width, height, channels = header
    stride = 1 + width * channels
    decoder = zlib.decompressobj()
    try:
        pixels = decoder.decompress(compressed, height * stride + 1)
    except zlib.error as exc:
        raise RichTextError("invalid_graphic") from exc
    if (len(pixels) != height * stride or not decoder.eof or decoder.unused_data
            or decoder.unconsumed_tail or any(pixels[i] > 4 for i in range(0, len(pixels), stride))):
        raise RichTextError("invalid_graphic")
    return width, height


def _authorize(dictation_id: str, actor_user_id: int, action: str) -> None:
    if not clinic_mode_enabled():
        raise RichTextError("clinic_mode_disabled")
    if not isinstance(dictation_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", dictation_id):
        raise RichTextError("forbidden")
    actor = db.session.get(User, actor_user_id) if type(actor_user_id) is int else None
    dictation = db.session.get(ClinicDictation, dictation_id)
    recording = db.session.get(Recording, dictation.recording_id) if dictation else None
    if actor is None or recording is None or not policy.decide(
        policy.build_subject(actor.id, bool(actor.is_admin)), action,
        policy.build_facts(recording.id, recording.user_id),
    ).allowed:
        raise RichTextError("forbidden")


class GraphicStore:
    """Content-addressed objects isolated by dictation; no overwrite/delete API."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()

    def _path(self, dictation_id: str, object_id: str) -> Path:
        if not isinstance(object_id, str) or not re.fullmatch(r"[0-9a-f]{64}", object_id):
            raise RichTextError("invalid_graphic_reference")
        scope = hashlib.sha256(dictation_id.encode()).hexdigest()
        return Path(local_path_from_key(str(self.root), f"clinic/graphics/v1/{scope}/{object_id}"))

    def put(self, dictation_id: str, data: bytes, *, actor_user_id: int) -> GraphicReference:
        """Validate and durably publish bytes; retries return the same reference."""
        _authorize(dictation_id, actor_user_id, "edit")
        width, height = _dimensions(data)
        object_id = hashlib.sha256(data).hexdigest()
        path = self._path(dictation_id, object_id)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".partial-") as stream:
            stream.write(data)
            stream.flush()
            os.fchmod(stream.fileno(), 0o400)
            os.fsync(stream.fileno())
            try:
                os.link(stream.name, path)  # Atomic publication, never overwrite.
            except FileExistsError:
                if self._read(path, object_id) != data:
                    raise RichTextError("graphic_integrity_error")
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        return GraphicReference(dictation_id, object_id, width, height, len(data))

    @staticmethod
    def _read(path: Path, object_id: str) -> bytes:
        try:
            with path.open("rb") as stream:
                data = stream.read(MAX_BYTES + 1)
        except FileNotFoundError as exc:
            raise RichTextError("graphic_not_found") from exc
        if len(data) > MAX_BYTES or hashlib.sha256(data).hexdigest() != object_id:
            raise RichTextError("graphic_integrity_error")
        return data

    def get(self, dictation_id: str, object_id: str, *, actor_user_id: int) -> tuple[GraphicReference, bytes]:
        """Read in the current scope and reconstruct trusted metadata from stored bytes."""
        _authorize(dictation_id, actor_user_id, "view")
        data = self._read(self._path(dictation_id, object_id), object_id)
        width, height = _dimensions(data)
        return GraphicReference(dictation_id, object_id, width, height, len(data)), data
