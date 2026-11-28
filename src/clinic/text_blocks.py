"""Bounded text resources and allowlisted local PNGs for clinic editors."""

from datetime import datetime, timezone
from pathlib import Path
import re
from uuid import uuid4

from sqlalchemy import select

from src.clinic.admin_dictation_access import _is_active_site_admin
from src.clinic.pools import ClinicAuthorPool, ClinicAuthorPoolMember, is_routable
from src.clinic.rich_text_doc import RichTextError, graphic_node, graphic_plain_text
from src.clinic.richtext_graphics import GraphicStore, _dimensions
from src.database import db

MAX_BODY_BYTES = 32 * 1024
MAX_TITLE = 120
MAX_TEXT = 4000
MAX_GRAPHICS = 4
MAX_PAGE = 50
POOL_ID = re.compile(r"[A-Za-z0-9_-]{1,36}")


class ClinicTextBlock(db.Model):
    __tablename__ = "clinic_text_block"
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid4()))
    scope = db.Column(db.String(8), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), index=True)
    pool_id = db.Column(db.String(36), db.ForeignKey("clinic_author_pool.id"), index=True)
    title = db.Column(db.String(MAX_TITLE), nullable=False)
    text = db.Column(db.String(MAX_TEXT), nullable=False)
    graphics = db.Column(db.JSON, nullable=False)
    revision = db.Column(db.Integer, nullable=False)
    __mapper_args__ = {"version_id_col": revision}
    __table_args__ = (
        db.CheckConstraint(
            "(scope = 'personal' AND user_id IS NOT NULL AND pool_id IS NULL) OR "
            "(scope = 'pool' AND user_id IS NULL AND pool_id IS NOT NULL) OR "
            "(scope = 'global' AND user_id IS NULL AND pool_id IS NULL)",
            name="ck_clinic_text_block_scope_owner",
        ),
    )

    def as_item(self) -> dict:
        return {"id": self.id, "scope": self.scope, "pool_id": self.pool_id,
                "title": self.title, "revision": self.revision}

    def as_detail(self) -> dict:
        parts = [self.text, *(graphic_plain_text(ref["alt_text"]) for ref in self.graphics)]
        return {**self.as_item(), "text": self.text, "graphics": self.graphics,
                "insert_text": "\n".join(part for part in parts if part)}


def eligible_blocks(actor_id: int):
    """Filter by live scope before ordering, pagination or lookup by ID."""
    pool = ClinicAuthorPool
    member = ClinicAuthorPoolMember
    now = datetime.now(timezone.utc)
    memberships = select(member.pool_id).where(member.user_id == actor_id)
    eligible_pools = select(pool.id).where(
        (pool.created_by == actor_id) | pool.id.in_(memberships),
        pool.active.is_not(False),
        (pool.valid_from.is_(None)) | (pool.valid_from <= now),
        (pool.valid_to.is_(None)) | (pool.valid_to > now),
    )
    return ClinicTextBlock.query.filter(
        (ClinicTextBlock.scope == "global") | (ClinicTextBlock.user_id == actor_id)
        | ClinicTextBlock.pool_id.in_(eligible_pools),
    )


def can_write(actor, block: ClinicTextBlock) -> bool:
    if block.scope == "personal":
        return block.user_id == actor.id
    if block.scope == "global":
        return _is_active_site_admin(db.session, actor)
    pool = db.session.get(ClinicAuthorPool, block.pool_id)
    return pool is not None and pool.created_by == actor.id and is_routable(pool)


def _bounded_text(value, limit: int) -> bool:
    return isinstance(value, str) and len(value) <= limit and "\0" not in value and not any(
        "\ud800" <= char <= "\udfff" for char in value
    )


def read_graphic(root: str, block: ClinicTextBlock, object_id: str) -> bytes:
    """Read an immutable asset from the server-provisioned directory for this scope."""
    if not re.fullmatch(r"[0-9a-f]{64}", object_id):
        raise RichTextError("invalid_graphic_reference")
    owner = str(block.user_id) if block.scope == "personal" else block.pool_id
    directory = Path(root).resolve() / "clinic/text-block-graphics/v1" / block.scope
    if owner is not None:
        if not POOL_ID.fullmatch(owner):
            raise RichTextError("invalid_graphic_reference")
        directory /= owner
    path = directory / object_id
    if path.resolve() != path or not path.is_file():
        raise RichTextError("graphic_not_found")
    data = GraphicStore._read(path, object_id)
    _dimensions(data)
    return data


def content_fields(body: dict, root: str, block: ClinicTextBlock) -> dict:
    title, text, refs = body.get("title"), body.get("text"), body.get("graphics", [])
    if not _bounded_text(title, MAX_TITLE) or not title.strip():
        raise ValueError("invalid title")
    if not _bounded_text(text, MAX_TEXT) or not isinstance(refs, list) or len(refs) > MAX_GRAPHICS:
        raise ValueError("invalid text or graphics")
    if not text.strip() and not refs:
        raise ValueError("empty text block")
    for ref in refs:
        if not isinstance(ref, dict) or ref.keys() != {"object_id", "alt_text"}:
            raise ValueError("invalid graphic reference")
        if not _bounded_text(ref["alt_text"], 200):
            raise ValueError("invalid alt text")
        graphic_node("reference", ref["object_id"], ref["alt_text"])
        read_graphic(root, block, ref["object_id"])
    return {"title": title.strip(), "text": text, "graphics": refs}
