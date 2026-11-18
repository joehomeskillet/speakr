"""Portable .sdict bundle: build and safe read (PAR-REC-007, PAR-REC-008)."""

from __future__ import annotations

import hashlib
import io
import json
import warnings
import zipfile

import pytest

from src.clinic.sdict_bundle import (
    FORMAT_VERSION,
    Bundle,
    BundleError,
    BundleLimits,
    build_bundle,
    read_bundle,
)

WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 24


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_manifest(files: dict[str, bytes] | None = None, **extra) -> dict:
    files = {"audio/a.wav": WAV, "text/rev1.txt": "Grüße, Äpfel Öl".encode()} if files is None else files
    audio = files.get("audio/a.wav")
    manifest = {
        "format_version": FORMAT_VERSION,
        "audio_sources": (
            [{"id": "a1", "path": "audio/a.wav", "sha256": sha(audio), "bytes": len(audio)}]
            if audio is not None
            else []
        ),
        "edit_list": [],
        "text_revisions": [{"id": "r1", "path": "text/rev1.txt", "text": "Grüße, Äpfel Öl"}],
        "anchors": [],
        "checksums": {path: sha(data) for path, data in files.items()},
    }
    manifest.update(extra)
    return manifest


def good_files() -> dict[str, bytes]:
    return {"audio/a.wav": WAV, "text/rev1.txt": "Grüße, Äpfel Öl".encode()}


def raw_zip(members: list[tuple[str, bytes]], compression=zipfile.ZIP_DEFLATED) -> bytes:
    buffer = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with zipfile.ZipFile(buffer, "w", compression) as archive:
            for name, data in members:
                archive.writestr(name, data)
    return buffer.getvalue()


def hostile(manifest: dict, files: dict[str, bytes], extra=()) -> bytes:
    members = [("manifest.json", json.dumps(manifest).encode())]
    members += list(files.items()) + list(extra)
    return raw_zip(members)


def code_of(callable_, *args, **kwargs) -> str:
    with pytest.raises(BundleError) as info:
        callable_(*args, **kwargs)
    return info.value.code


# 1 round trip
def test_round_trip_is_byte_exact():
    files = good_files()
    manifest = make_manifest(files)
    result = read_bundle(build_bundle(manifest, files))
    assert isinstance(result, Bundle)
    assert result.files == files
    assert result.manifest == manifest
    assert result.dropped_keys == ()


# 2 checksums and listing
def test_checksum_mismatch():
    files = good_files()
    manifest = make_manifest(files)
    manifest["checksums"]["text/rev1.txt"] = sha(b"other")
    assert code_of(read_bundle, hostile(manifest, files)) == "checksum_mismatch"


def test_audio_hash_mismatch():
    files = good_files()
    manifest = make_manifest(files)
    manifest["audio_sources"][0]["sha256"] = sha(b"other")
    assert code_of(read_bundle, hostile(manifest, files)) == "checksum_mismatch"


def test_missing_file():
    files = good_files()
    manifest = make_manifest(files)
    del files["text/rev1.txt"]
    assert code_of(read_bundle, hostile(manifest, files)) == "missing_file"


def test_unlisted_file():
    files = good_files()
    manifest = make_manifest(files)
    data = hostile(manifest, files, extra=[("notes/x.txt", b"hi")])
    assert code_of(read_bundle, data) == "unlisted_file"


# 3 unsafe paths
@pytest.mark.parametrize(
    "name",
    ["../x", "/abs", "a/../../b", "a\\b", "C:x", "a//b", "a/", "./a"],
)
def test_unsafe_member_names(name):
    files = good_files()
    manifest = make_manifest(files)
    data = hostile(manifest, files, extra=[(name, b"x")])
    assert code_of(read_bundle, data) == "unsafe_path"


def test_nul_in_member_name():
    files = good_files()
    manifest = make_manifest(files)
    data = hostile(manifest, files, extra=[("zzXzz", b"x")]).replace(b"zzXzz", b"zz\x00zz")
    assert code_of(read_bundle, data) == "unsafe_path"


def test_build_rejects_unsafe_name():
    files = {"../x": b"x"}
    manifest = make_manifest(files)
    manifest["audio_sources"] = []
    assert code_of(build_bundle, manifest, files) == "unsafe_path"


# 4 zip bomb guards (declared sizes, before any read)
BOMB = b"\x00" * 200_000


def bomb_zip(count: int = 1) -> bytes:
    manifest = {"format_version": "1", "audio_sources": [], "checksums": {}}
    members = [("manifest.json", json.dumps(manifest).encode())]
    members += [(f"b{i}.bin", BOMB) for i in range(count)]
    return raw_zip(members)


def test_entry_too_large():
    limits = BundleLimits(max_entry_bytes=1000, max_ratio=10**9)
    assert code_of(read_bundle, bomb_zip(), limits=limits) == "too_large"


def test_total_too_large():
    limits = BundleLimits(max_total_bytes=300_000, max_ratio=10**9)
    assert code_of(read_bundle, bomb_zip(3), limits=limits) == "too_large"


def test_too_many_entries():
    limits = BundleLimits(max_entries=2, max_ratio=10**9)
    assert code_of(read_bundle, bomb_zip(3), limits=limits) == "too_many_entries"


def test_ratio():
    assert code_of(read_bundle, bomb_zip(), limits=BundleLimits(max_ratio=10)) == "ratio"


def test_guards_run_before_decompression(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("archive was opened for reading")

    data = bomb_zip()
    monkeypatch.setattr(zipfile.ZipFile, "open", forbidden)
    limits = BundleLimits(max_entry_bytes=1000)
    assert code_of(read_bundle, data, limits=limits) == "too_large"


# 5 manifest and version
def test_unsupported_version():
    files = good_files()
    manifest = make_manifest(files, format_version="2")
    assert code_of(read_bundle, hostile(manifest, files)) == "unsupported_version"
    assert code_of(build_bundle, manifest, files) == "unsupported_version"


def test_manifest_missing():
    assert code_of(read_bundle, raw_zip([("a.txt", b"x")])) == "bad_manifest"


def test_manifest_not_an_object():
    assert code_of(read_bundle, raw_zip([("manifest.json", b"[1, 2]")])) == "bad_manifest"


@pytest.mark.parametrize("raw", [b"{not json", b"\xff\xfe", b'{"a": NaN}'])
def test_manifest_invalid_json(raw):
    assert code_of(read_bundle, raw_zip([("manifest.json", raw)])) == "bad_manifest"


def test_duplicate_member():
    files = good_files()
    manifest = make_manifest(files)
    data = hostile(manifest, files, extra=[("text/rev1.txt", b"again")])
    assert code_of(read_bundle, data) == "duplicate_member"


def test_not_a_zip():
    assert code_of(read_bundle, b"definitely not a zip") == "bad_archive"


# 6 secrets
@pytest.mark.parametrize(
    "key", ["token", "Password", "SECRET", "api_key", "cookie", "Authorization"]
)
@pytest.mark.parametrize("nested", [False, True])
def test_forbidden_keys(key, nested):
    files = good_files()
    extra = {"meta": {"deep": [{key: "x"}]}} if nested else {key: "x"}
    manifest = make_manifest(files, **extra)
    assert code_of(build_bundle, manifest, files) == "forbidden_key"
    assert code_of(read_bundle, hostile(manifest, files)) == "forbidden_key"


# 7 audio signatures and active content
@pytest.mark.parametrize(
    "data",
    [
        WAV,
        b"OggS" + b"\x00" * 20,
        b"fLaC" + b"\x00" * 20,
        b"ID3\x04" + b"\x00" * 20,
        b"\xff\xfb\x90\x00" + b"\x00" * 20,
        b"\x1a\x45\xdf\xa3" + b"\x00" * 20,
        b"\x00\x00\x00\x18ftypM4A " + b"\x00" * 20,
    ],
)
def test_known_audio_signatures_pass(data):
    files = {"audio/a.wav": data}
    manifest = make_manifest(files)
    assert read_bundle(build_bundle(manifest, files)).files == files


@pytest.mark.parametrize(
    "data", [b"MZ\x90\x00" + b"\x00" * 20, b"RIFF\x00\x00\x00\x00AVI " + b"\x00" * 8, b""]
)
def test_unknown_audio_is_refused(data):
    files = {"audio/a.wav": data}
    manifest = make_manifest(files)
    assert code_of(build_bundle, manifest, files) == "bad_audio"
    assert code_of(read_bundle, hostile(manifest, files)) == "bad_audio"


@pytest.mark.parametrize(
    "name",
    ["a.exe", "a.dll", "a.js", "a.html", "a.htm", "a.sh", "a.bat", "a.cmd", "a.ps1", "a.VBS"],
)
def test_active_content_refused_even_when_listed(name):
    files = good_files()
    files[f"extra/{name}"] = b"payload"
    manifest = make_manifest(files)
    assert code_of(build_bundle, manifest, files) == "active_content"
    assert code_of(read_bundle, hostile(manifest, files)) == "active_content"


# 8 import carries no rights
def test_rights_keys_are_dropped_and_reported():
    files = good_files()
    extra = {
        "acl": ["x"], "shares": [1], "owner": "o", "owner_id": 4,
        "user_id": 5, "permissions": {"w": True},
    }
    manifest = make_manifest(files, **extra)
    result = read_bundle(hostile(manifest, files))
    assert result.dropped_keys == ("acl", "owner", "owner_id", "permissions", "shares", "user_id")
    assert not set(extra) & set(result.manifest)
    assert result.manifest == make_manifest(files)


def test_nested_rights_keys_are_kept():
    files = good_files()
    manifest = make_manifest(files, meta={"owner": "keep"})
    result = read_bundle(hostile(manifest, files))
    assert result.manifest["meta"] == {"owner": "keep"}
    assert result.dropped_keys == ()


# 9 determinism
def test_build_is_deterministic_and_keeps_umlauts():
    files = good_files()
    manifest = make_manifest(files)
    first = build_bundle(manifest, files)
    second = build_bundle(dict(reversed(list(manifest.items()))), dict(reversed(list(files.items()))))
    assert first == second
    with zipfile.ZipFile(io.BytesIO(first)) as archive:
        names = archive.namelist()
        assert names == sorted(names)
        assert all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist())
        assert "Grüße".encode() in archive.read("manifest.json")
    assert read_bundle(first).manifest["text_revisions"][0]["text"] == "Grüße, Äpfel Öl"


def test_limits_are_validated():
    with pytest.raises(ValueError):
        BundleLimits(max_entries=True)
    with pytest.raises(ValueError):
        BundleLimits(max_ratio=0)
