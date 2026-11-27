"""Synthetic local-store evidence; staging/UI parity remains a separate gate."""

import hashlib
import json
import struct
import zlib
from dataclasses import FrozenInstanceError, asdict

import pytest

from src.app import app, db
from src.clinic import policy
from src.clinic.models import ClinicDictation
from src.clinic.rich_text_doc import RichTextError
from src.clinic.richtext_graphics import MAX_BYTES, GraphicReference, GraphicStore


def chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def png(width=1, height=1, pixels=b"\x00\xff\x00\x00\xff", color=6):
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, color, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b""))


@pytest.fixture
def scopes(world, clinic_on):
    with app.app_context():
        rows = [ClinicDictation(recording_id=world[f"rec_{name}"], author_user_id=world[f"user_{name}"])
                for name in ("a", "b")]
        db.session.add_all(rows)
        db.session.commit()
        return [row.id for row in rows]


def test_two_sessions_round_trip_and_immutable_references(scopes, world, tmp_path):
    data = png()
    with app.app_context():
        store = GraphicStore(tmp_path)
        ref = store.put(scopes[0], data, actor_user_id=world["user_a"])
        assert store.put(scopes[0], data, actor_user_id=world["user_a"]) == ref
        assert ref.object_id == hashlib.sha256(data).hexdigest()
        assert (ref.width, ref.height, ref.size_bytes, ref.content_type) == (1, 1, len(data), "image/png")
        assert GraphicReference(**json.loads(json.dumps(asdict(ref)))) == ref
        with pytest.raises(FrozenInstanceError):
            ref.width = 2
        changed = store.put(scopes[0], png(pixels=b"\x00\x00\xff\x00\xff"), actor_user_id=world["user_a"])
        assert changed.object_id != ref.object_id
    # A new Flask/SQLAlchemy scoped session and store instance must read persisted data.
    with app.app_context():
        assert GraphicStore(tmp_path).get(scopes[0], ref.object_id, actor_user_id=world["user_a"]) == (ref, data)
        with pytest.raises(RichTextError, match="forbidden"):
            GraphicStore(tmp_path).get(scopes[0], ref.object_id, actor_user_id=world["user_b"])
        with pytest.raises(RichTextError, match="graphic_not_found"):
            GraphicStore(tmp_path).get(scopes[1], ref.object_id, actor_user_id=world["user_b"])
    assert not list(tmp_path.rglob(".partial-*"))


@pytest.mark.parametrize("actor", ["user_b", "user_admin"])
def test_wrong_ownership_cannot_write(scopes, world, tmp_path, actor):
    with app.app_context(), pytest.raises(RichTextError, match="forbidden"):
        GraphicStore(tmp_path).put(scopes[0], png(), actor_user_id=world[actor])
    assert list(tmp_path.iterdir()) == []


def test_policy_revocation_and_clinic_mode(scopes, world, tmp_path, monkeypatch):
    with app.app_context():
        store = GraphicStore(tmp_path)
        ref = store.put(scopes[0], png(), actor_user_id=world["user_a"])
        policy.register_grant_provider(lambda _: [policy.Deny(recording_ids=frozenset({world["rec_a"]}))])
        for action in (lambda: store.get(scopes[0], ref.object_id, actor_user_id=world["user_a"]),
                       lambda: store.put(scopes[0], png(), actor_user_id=world["user_a"])):
            with pytest.raises(RichTextError, match="forbidden"):
                action()
        policy.clear_providers()
        monkeypatch.setenv("CLINIC_MODE", "0")
        with pytest.raises(RichTextError, match="clinic_mode_disabled"):
            store.get(scopes[0], ref.object_id, actor_user_id=world["user_a"])


@pytest.mark.parametrize("data", [
    b"<svg onload='alert(1)'/>", b"<html><script>active</script></html>",
    png() + b"<script>active</script>",
    png()[:-12] + chunk(b"tEXt", b"<script>active</script>") + chunk(b"IEND", b""),
    b"x" * (MAX_BYTES + 1), png(width=4097), png(width=4096, height=4096),
    png()[:-1], png()[:45] + b"bad CRC" + png()[52:],
    png(pixels=b"\x05\xff\x00\x00\xff"), png(pixels=b""),
    png(pixels=b"\x00" * 100_000), b"", None,
], ids=["svg", "html", "trailing-active", "embedded-active", "bytes-limit", "width-limit",
        "pixel-limit", "truncated", "crc", "filter", "missing-pixels", "deflate-bomb", "empty", "type"])
def test_unsafe_or_invalid_images_are_refused(scopes, world, tmp_path, data):
    with app.app_context(), pytest.raises(RichTextError):
        GraphicStore(tmp_path).put(scopes[0], data, actor_user_id=world["user_a"])
    assert list(tmp_path.iterdir()) == []


def test_path_and_integrity_guards(scopes, world, tmp_path):
    with app.app_context():
        store = GraphicStore(tmp_path)
        ref = store.put(scopes[0], png(), actor_user_id=world["user_a"])
        for key in ("../../outside", "https://example.invalid/image.png", "A" * 64, None):
            with pytest.raises(RichTextError, match="invalid_graphic_reference"):
                store.get(scopes[0], key, actor_user_id=world["user_a"])
        path = next(tmp_path.rglob(ref.object_id))
        path.chmod(0o600)
        path.write_bytes(b"corrupted")
        for action in (lambda: store.get(scopes[0], ref.object_id, actor_user_id=world["user_a"]),
                       lambda: store.put(scopes[0], png(), actor_user_id=world["user_a"])):
            with pytest.raises(RichTextError, match="graphic_integrity_error"):
                action()
        assert path.read_bytes() == b"corrupted"  # A retry never overwrites existing bytes.
