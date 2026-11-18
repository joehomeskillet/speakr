"""In-memory registry of dictation drafts with an active draft per tab.

A user can hold several drafts at once and every browser tab points at one
active draft. A save carries the base version it was made against; a save
against a stale version raises ``DraftConflict`` with the current version
and text, so two tabs can never overwrite each other silently (no
last-write-wins). Discard is deliberate (``confirm=True``) and leaves a
readable tombstone in ``list_discarded``; nothing is deleted.

Non-scope: no Flask, no routes, no database access, no persistence and no
own clock — every call takes ``now`` (timezone-aware, normalised to UTC; a
naive datetime raises ``ValueError``). Routes, storage and cross-tab
fan-out are later wiring packages. ``tab_id`` on ``save`` is validated
context only; ``set_active`` is the only call that changes the active
selection.
"""

from __future__ import annotations

import unicodedata
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Callable

_TITLE_MAX_CHARS = 200
_TEXT_MAX_CHARS = 1_000_000


class DraftError(ValueError):
    """The draft operation was rejected before it changed state.

    ``code`` is a stable string: ``confirmation_required``, ``discarded``,
    ``not_found``, ``invalid_owner``, ``invalid_title``, ``invalid_text``,
    ``too_large``, ``invalid_draft_id``, ``invalid_tab_id``,
    ``invalid_version``, ``invalid_confirm`` or ``duplicate_id``.
    """

    def __init__(self, code: str, message: str | None = None):
        self.code = code
        super().__init__(message or code)


class DraftConflict(DraftError):
    """A save against a stale base version; the draft is unchanged.

    ``current_version`` and ``current_text`` describe the winning state so
    the losing tab can show the real conflict instead of guessing.
    """

    def __init__(self, current_version: int, current_text: str, message: str | None = None):
        self.current_version = current_version
        self.current_text = current_text
        super().__init__("version_conflict", message or "version_conflict")


@dataclass(frozen=True)
class Draft:
    """One dictation draft. Immutable; every change produces a new instance."""

    id: str
    owner_id: int
    title: str
    text: str
    version: int
    created_at: datetime
    updated_at: datetime
    discarded_at: datetime | None


def _default_id() -> str:
    return str(uuid.uuid4())


class DraftRegistry:
    """In-memory drafts per owner with an active draft per browser tab."""

    def __init__(self, id_factory: Callable[[], str] | None = None):
        self._id_factory: Callable[[], str] = id_factory or _default_id
        self._drafts: dict[str, Draft] = {}
        self._active: dict[tuple[int, str], str] = {}

    def create(self, owner_id: int, title: str, now: datetime, text: str = "") -> Draft:
        """Start a new draft at version 1."""
        moment = _require_now(now)
        owner = _require_owner(owner_id)
        clean_title = _require_title(title)
        clean_text = _require_text(text)
        draft_id = self._new_id()
        draft = Draft(
            id=draft_id,
            owner_id=owner,
            title=clean_title,
            text=clean_text,
            version=1,
            created_at=moment,
            updated_at=moment,
            discarded_at=None,
        )
        self._drafts[draft_id] = draft
        return draft

    def list(self, owner_id: int) -> list[Draft]:
        """Non-discarded drafts of this owner, newest update first."""
        _require_owner(owner_id)
        drafts = [
            draft
            for draft in self._drafts.values()
            if draft.owner_id == owner_id and draft.discarded_at is None
        ]
        drafts.sort(key=lambda draft: draft.updated_at, reverse=True)
        return drafts

    def list_discarded(self, owner_id: int) -> list[Draft]:
        """Tombstones of this owner, newest update first; text stays readable."""
        _require_owner(owner_id)
        drafts = [
            draft
            for draft in self._drafts.values()
            if draft.owner_id == owner_id and draft.discarded_at is not None
        ]
        drafts.sort(key=lambda draft: draft.updated_at, reverse=True)
        return drafts

    def set_active(self, owner_id: int, tab_id: str, draft_id: str) -> None:
        """Point one tab at one of this owner's drafts."""
        owner = _require_owner(owner_id)
        tab = _require_tab_id(tab_id)
        key = _require_draft_id(draft_id)
        draft = self._require_draft(owner, key)
        if draft.discarded_at is not None:
            raise DraftError("discarded", "a discarded draft cannot become active")
        self._active[(owner, tab)] = draft.id

    def active(self, owner_id: int, tab_id: str) -> Draft | None:
        """The active draft of this tab, or None when the tab has none."""
        owner = _require_owner(owner_id)
        tab = _require_tab_id(tab_id)
        draft_id = self._active.get((owner, tab))
        if draft_id is None:
            return None
        draft = self._drafts.get(draft_id)
        if draft is None or draft.owner_id != owner or draft.discarded_at is not None:
            return None
        return draft

    def save(
        self,
        owner_id: int,
        draft_id: str,
        text: str,
        base_version: int,
        tab_id: str,
        now: datetime,
    ) -> Draft:
        """Store ``text`` when ``base_version`` is still current, else conflict."""
        moment = _require_now(now)
        owner = _require_owner(owner_id)
        key = _require_draft_id(draft_id)
        clean_text = _require_text(text)
        _require_version(base_version)
        _require_tab_id(tab_id)
        draft = self._require_draft(owner, key)
        if draft.discarded_at is not None:
            raise DraftError("discarded", "this draft is discarded")
        if base_version != draft.version:
            raise DraftConflict(draft.version, draft.text)
        saved = replace(draft, text=clean_text, version=draft.version + 1, updated_at=moment)
        self._drafts[saved.id] = saved
        return saved

    def discard(
        self, owner_id: int, draft_id: str, *, confirm: bool, now: datetime
    ) -> Draft:
        """Tombstone the draft after explicit confirmation; active tabs clear."""
        moment = _require_now(now)
        owner = _require_owner(owner_id)
        key = _require_draft_id(draft_id)
        if not isinstance(confirm, bool):
            raise DraftError("invalid_confirm", "confirm must be a bool")
        draft = self._require_draft(owner, key)
        if draft.discarded_at is not None:
            raise DraftError("discarded", "this draft is already discarded")
        if not confirm:
            raise DraftError("confirmation_required", "discard requires confirm=True")
        discarded = replace(draft, discarded_at=moment)
        self._drafts[discarded.id] = discarded
        for active_key, active_id in list(self._active.items()):
            if active_key[0] == owner and active_id == discarded.id:
                del self._active[active_key]
        return discarded

    def _new_id(self) -> str:
        draft_id = self._id_factory()
        if not isinstance(draft_id, str) or draft_id == "":
            raise DraftError("invalid_draft_id", "id factory must return a non-empty string")
        if draft_id in self._drafts:
            raise DraftError("duplicate_id", "id factory returned an id already in use")
        return draft_id

    def _require_draft(self, owner_id: int, draft_id: str) -> Draft:
        draft = self._drafts.get(draft_id)
        if draft is None or draft.owner_id != owner_id:
            raise DraftError("not_found", "no such draft for this owner")
        return draft


def _require_now(now: datetime) -> datetime:
    if not isinstance(now, datetime) or now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(timezone.utc)


def _require_owner(owner_id: int) -> int:
    if isinstance(owner_id, bool) or not isinstance(owner_id, int) or owner_id <= 0:
        raise DraftError("invalid_owner", "owner_id must be a positive int")
    return owner_id


def _require_title(title: str) -> str:
    if not isinstance(title, str):
        raise DraftError("invalid_title", "title must be a str")
    normalised = unicodedata.normalize("NFC", title)
    if normalised.strip() == "":
        raise DraftError("invalid_title", "title must not be empty or whitespace-only")
    if any(unicodedata.category(char) == "Cc" for char in normalised):
        raise DraftError("invalid_title", "title must not contain control characters")
    if len(normalised) > _TITLE_MAX_CHARS:
        raise DraftError("invalid_title", f"title is limited to {_TITLE_MAX_CHARS} characters")
    return normalised


def _require_text(text: str) -> str:
    if not isinstance(text, str):
        raise DraftError("invalid_text", "text must be a str")
    normalised = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    if any(
        unicodedata.category(char) == "Cc" and char not in "\t\n"
        for char in normalised
    ):
        raise DraftError(
            "invalid_text",
            "text must not contain control characters other than tab and newline",
        )
    if len(normalised) > _TEXT_MAX_CHARS:
        raise DraftError("too_large", f"text is limited to {_TEXT_MAX_CHARS} characters")
    return normalised


def _require_draft_id(draft_id: str) -> str:
    if not isinstance(draft_id, str) or draft_id.strip() == "":
        raise DraftError("invalid_draft_id", "draft_id must be a non-empty string")
    return draft_id


def _require_tab_id(tab_id: str) -> str:
    if not isinstance(tab_id, str) or tab_id.strip() == "":
        raise DraftError("invalid_tab_id", "tab_id must be a non-empty string")
    return tab_id


def _require_version(base_version: int) -> int:
    if isinstance(base_version, bool) or not isinstance(base_version, int):
        raise DraftError("invalid_version", "base_version must be an int")
    return base_version
