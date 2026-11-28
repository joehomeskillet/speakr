"""Portable dictation bundle (``.sdict``): a ZIP with ``manifest.json``.

Scope: build a deterministic bundle from a manifest and a file mapping, and
read one back without trusting the archive. Reading checks member names,
declared sizes (before any decompression), duplicate members, the manifest,
secrets, checksums, audio signatures and active content.

Non-scope: no Flask, no route, no database access, no clock, no deletion.
Persistence and wiring are a later package. Import never carries rights:
top-level rights keys of the manifest are dropped and reported.
"""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
import zlib
from collections import Counter
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any, Mapping

FORMAT_VERSION = "1"
MANIFEST_NAME = "manifest.json"

_FORBIDDEN_KEYS = frozenset(
    {"token", "password", "secret", "api_key", "cookie", "authorization"}
)
_RIGHTS_KEYS = frozenset(
    {"acl", "shares", "owner", "owner_id", "user_id", "permissions"}
)
_ACTIVE_EXTENSIONS = frozenset(
    {".exe", ".dll", ".js", ".html", ".htm", ".sh", ".bat", ".cmd", ".ps1", ".vbs"}
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_FIXED_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_ALLOWED_COMPRESSION = (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)


class BundleError(ValueError):
    """The bundle was rejected. ``code`` is a stable machine-readable string."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code


@dataclass(frozen=True)
class BundleLimits:
    max_entries: int = 200
    max_total_bytes: int = 512 * 1024 * 1024
    max_entry_bytes: int = 256 * 1024 * 1024
    max_ratio: int = 100

    def __post_init__(self) -> None:
        for name in ("max_entries", "max_total_bytes", "max_entry_bytes", "max_ratio"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive int")


@dataclass(frozen=True)
class Bundle:
    manifest: dict
    files: dict[str, bytes]
    dropped_keys: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------- validation


def _check_name(name: object) -> str:
    if not isinstance(name, str) or not name:
        raise BundleError("unsafe_path", "member name must be a non-empty str")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in name):
        raise BundleError("unsafe_path", f"control character in {name!r}")
    if "\\" in name or ":" in name or name.startswith("/"):
        raise BundleError("unsafe_path", f"unsafe member name {name!r}")
    for segment in name.split("/"):
        if segment in ("", ".", ".."):
            raise BundleError("unsafe_path", f"bad path segment in {name!r}")
    return name


def _extension(name: str) -> str:
    base = name.rsplit("/", 1)[-1]
    dot = base.rfind(".")
    return base[dot:].lower() if dot > 0 else ""


def _check_active(name: str) -> None:
    if _extension(name) in _ACTIVE_EXTENSIONS:
        raise BundleError("active_content", f"active content refused: {name!r}")


def _scan_keys(node: Any) -> None:
    """Refuse secret keys anywhere (nested, case-insensitive)."""
    if isinstance(node, dict):
        for key, value in node.items():
            if not isinstance(key, str):
                raise BundleError("bad_manifest", "manifest keys must be strings")
            if key.lower() in _FORBIDDEN_KEYS:
                raise BundleError("forbidden_key", f"forbidden key {key!r}")
            _scan_keys(value)
    elif isinstance(node, list):
        for item in node:
            _scan_keys(item)


def _is_audio(data: bytes) -> bool:
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return True
    if data[:4] in (b"OggS", b"fLaC", b"\x1a\x45\xdf\xa3") or data[:3] == b"ID3":
        return True
    if data[4:8] == b"ftyp":
        return True
    return len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0


def _validate_manifest(manifest: object) -> dict:
    if not isinstance(manifest, dict):
        raise BundleError("bad_manifest", "manifest must be a JSON object")
    _scan_keys(manifest)
    if manifest.get("format_version") != FORMAT_VERSION:
        raise BundleError(
            "unsupported_version", f"format_version {manifest.get('format_version')!r}"
        )
    sources = manifest.get("audio_sources")
    if not isinstance(sources, list):
        raise BundleError("bad_manifest", "audio_sources must be a list")
    checksums = manifest.get("checksums")
    if not isinstance(checksums, dict):
        raise BundleError("bad_manifest", "checksums must be an object")
    for path, digest in checksums.items():
        if not isinstance(digest, str) or not _SHA256_RE.match(digest):
            raise BundleError("bad_manifest", f"bad checksum for {path!r}")
    for entry in sources:
        if not isinstance(entry, dict):
            raise BundleError("bad_manifest", "audio source must be an object")
        for key in ("id", "path", "sha256"):
            if not isinstance(entry.get(key), str) or not entry[key]:
                raise BundleError("bad_manifest", f"audio source needs {key}")
        size = entry.get("bytes")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise BundleError("bad_manifest", "audio source bytes must be an int")
        if not _SHA256_RE.match(entry["sha256"]):
            raise BundleError("bad_manifest", "audio source sha256 malformed")
        _check_name(entry["path"])
    return manifest


def _listed_paths(manifest: dict) -> set[str]:
    listed = {_check_name(path) for path in manifest["checksums"]}
    listed.update(entry["path"] for entry in manifest["audio_sources"])
    return listed


def _check_contents(manifest: dict, files: Mapping[str, bytes]) -> None:
    """Shared by build and read: listing, checksums, audio, active content."""
    for name in files:
        _check_name(name)
        _check_active(name)
    listed = _listed_paths(manifest)
    for path in sorted(listed):
        _check_active(path)
    for path in sorted(listed):
        if path not in files:
            raise BundleError("missing_file", f"listed file missing: {path!r}")
    for path in sorted(files):
        if path not in listed:
            raise BundleError("unlisted_file", f"file not in manifest: {path!r}")
    for path, digest in manifest["checksums"].items():
        if hashlib.sha256(files[path]).hexdigest() != digest:
            raise BundleError("checksum_mismatch", f"checksum differs: {path!r}")
    for entry in manifest["audio_sources"]:
        data = files[entry["path"]]
        if (
            hashlib.sha256(data).hexdigest() != entry["sha256"]
            or len(data) != entry["bytes"]
        ):
            raise BundleError("checksum_mismatch", f"audio differs: {entry['path']!r}")
        if not _is_audio(data):
            raise BundleError("bad_audio", f"not a known audio container: {entry['path']!r}")


# --------------------------------------------------------------------- build


def build_bundle(manifest: dict, files: Mapping[str, bytes]) -> bytes:
    """Return deterministic ZIP bytes. The manifest is validated, not changed."""
    manifest = _validate_manifest(manifest)
    if not isinstance(files, Mapping):
        raise BundleError("bad_files", "files must be a mapping")
    for name, data in files.items():
        if name == MANIFEST_NAME:
            raise BundleError("unsafe_path", "manifest.json is reserved")
        if not isinstance(data, bytes):
            raise BundleError("bad_files", f"content of {name!r} must be bytes")
    _check_contents(manifest, files)
    try:
        manifest_bytes = json.dumps(
            manifest, sort_keys=True, ensure_ascii=False, allow_nan=False, indent=2
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise BundleError("bad_manifest", str(exc)) from exc

    members = dict(files)
    members[MANIFEST_NAME] = manifest_bytes
    ratio = BundleLimits().max_ratio

    def pack(stored):
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name in sorted(members):
                info = zipfile.ZipInfo(name, date_time=_FIXED_TIMESTAMP)
                info.compress_type = zipfile.ZIP_STORED if name in stored else zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = 0o600 << 16
                archive.writestr(info, members[name])
            oversized = {info.filename for info in archive.infolist()
                         if info.file_size > ratio * info.compress_size}
        return buffer, oversized

    buffer, stored = pack(set())
    if stored:
        # The reader applies its ratio limit even to legitimate silent audio.
        buffer, _ = pack(stored)
    return buffer.getvalue()


# ---------------------------------------------------------------------- read


def _check_archive(infos: list[zipfile.ZipInfo], limits: BundleLimits) -> None:
    """Everything decidable from the central directory, before any read."""
    if len(infos) > limits.max_entries:
        raise BundleError("too_many_entries", f"{len(infos)} entries")
    counts = Counter(info.orig_filename for info in infos)
    for info in infos:
        _check_name(info.orig_filename)
        if info.filename != info.orig_filename:
            raise BundleError("unsafe_path", f"name altered: {info.orig_filename!r}")
    for name, count in counts.items():
        if count > 1:
            raise BundleError("duplicate_member", f"duplicate member {name!r}")
    for info in infos:
        _check_active(info.orig_filename)
        if info.flag_bits & 0x1 or info.compress_type not in _ALLOWED_COMPRESSION:
            raise BundleError("bad_archive", f"unsupported entry {info.filename!r}")
    total = 0
    for info in infos:
        if info.file_size > limits.max_entry_bytes:
            raise BundleError("too_large", f"entry too large: {info.filename!r}")
        total += info.file_size
        if total > limits.max_total_bytes:
            raise BundleError("too_large", "total uncompressed size too large")
    for info in infos:
        if info.file_size and (
            info.compress_size == 0 or info.file_size / info.compress_size > limits.max_ratio
        ):
            raise BundleError("ratio", f"compression ratio too high: {info.filename!r}")


def _read_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    try:
        with archive.open(info) as handle:
            data = handle.read(info.file_size + 1)
    except (zipfile.BadZipFile, zlib.error, NotImplementedError, RuntimeError, EOFError) as exc:
        raise BundleError("bad_archive", f"cannot read {info.filename!r}") from exc
    if len(data) != info.file_size:
        raise BundleError("too_large", f"declared size wrong: {info.filename!r}")
    return data


def read_bundle(data: bytes, *, limits: BundleLimits = BundleLimits()) -> Bundle:
    """Read a bundle. The archive is never trusted; see module docstring."""
    if not isinstance(data, (bytes, bytearray)):
        raise BundleError("bad_archive", "data must be bytes")
    try:
        archive = zipfile.ZipFile(BytesIO(bytes(data)))
    except (zipfile.BadZipFile, EOFError) as exc:
        raise BundleError("bad_archive", "not a ZIP archive") from exc
    with archive:
        infos = archive.infolist()
        _check_archive(infos, limits)
        by_name = {info.filename: info for info in infos}
        if MANIFEST_NAME not in by_name:
            raise BundleError("bad_manifest", "manifest.json missing")
        raw = _read_member(archive, by_name[MANIFEST_NAME])

        def _reject_constant(value: str) -> None:
            raise ValueError(f"non-finite number {value}")

        try:
            parsed = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
        except (UnicodeDecodeError, ValueError, RecursionError) as exc:
            raise BundleError("bad_manifest", "manifest is not valid JSON") from exc
        manifest = _validate_manifest(parsed)
        files = {
            name: _read_member(archive, info)
            for name, info in by_name.items()
            if name != MANIFEST_NAME
        }
    _check_contents(manifest, files)
    dropped = tuple(sorted(key for key in manifest if key.lower() in _RIGHTS_KEYS))
    clean = {key: value for key, value in manifest.items() if key not in dropped}
    return Bundle(manifest=clean, files=files, dropped_keys=dropped)
