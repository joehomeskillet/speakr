"""Several server-side live drafts, one recording holder, explicit discard.

Draft identity, text, versions and tombstones come from ``DraftRegistry``.
The open-session rule comes from ``live_session``: one open session per
dictation. This module adds the cross-draft rule: a user records into at
most one draft. Switching pauses the previous holder and drops its phrase
token, so a phrase captured before the switch cannot land on the draft that
was just selected.

Discard needs ``confirm=True``. It ends that draft's live session and leaves
the registry tombstone. The committed original-audio reference and the draft
audit stay readable. Nothing here deletes audio bytes or audit rows.

shortcut: the audio reference and the draft audit live on this object, same
as the registry. Durable ``ClinicOriginal`` rows and the recording audit
ledger belong to the live HTTP package.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from src.clinic.draft_registry import Draft, DraftError, DraftRegistry
from src.clinic import live_session as live

IDLE = "idle"
ARCHIVED = "archived"


class LiveDraftError(DraftError):
    """A live-draft rule failed. ``code`` stays stable for the caller."""


@dataclass(frozen=True)
class LiveDraft:
    """One user-visible draft. ``id`` is the dictation id and does not change."""

    id: str
    owner_id: int
    title: str
    text: str
    version: int
    state: str
    audio_ref: str | None
    holder_token: int | None
    discarded_at: datetime | None


@dataclass(frozen=True)
class DraftAudit:
    """One append-only draft action. Discard does not remove entries."""

    draft_id: str
    action: str
    at: datetime


class LiveDrafts:
    """Coordinator over one ``DraftRegistry`` and ``live_session``."""

    def __init__(self) -> None:
        self._queue: list[str] = []
        self.registry = DraftRegistry(id_factory=self._next_id)
        self._owners: dict[str, int] = {}
        self._state: dict[str, str] = {}
        self._sessions: dict[str, str] = {}
        self._live: dict[int, str] = {}
        self._tokens: dict[str, int] = {}
        self._generation: dict[int, int] = {}
        self._audio: dict[str, str] = {}
        self._audit: list[DraftAudit] = []

    def create(
        self, owner_id: int, title: str, now: datetime, dictation_id: str, text: str = ""
    ) -> LiveDraft:
        """Open a draft whose id is ``dictation_id``. No live session yet."""
        self._owner(owner_id)
        key = _dictation_id(dictation_id)
        if key in self._owners:
            raise DraftError("duplicate_id", "dictation id is already a draft")
        self._queue.append(key)
        try:
            draft = self.registry.create(owner_id, title, now, text)
        finally:
            if self._queue and self._queue[0] == key:
                self._queue.pop(0)
        self._owners[draft.id] = draft.owner_id
        self._state[draft.id] = IDLE
        self._audit_add(draft.id, "created", draft.created_at)
        return self._view(draft)

    def list(self, owner_id: int) -> list[LiveDraft]:
        """Open drafts of this owner, newest update first."""
        self._owner(owner_id)
        return [self._view(draft) for draft in self.registry.list(owner_id)]

    def list_archived(self, owner_id: int) -> list[LiveDraft]:
        """Tombstones of this owner. Text, audio and audit stay readable."""
        self._owner(owner_id)
        return [self._view(draft) for draft in self.registry.list_discarded(owner_id)]

    def switch(self, owner_id: int, draft_id: str, tab_id: str, now: datetime) -> LiveDraft:
        """Select a draft and pause the previous recording holder.

        The selected draft does not start recording. Its phrase token is
        cleared, so an in-flight phrase cannot append here.
        """
        self._require_open(owner_id, draft_id)
        self.registry.set_active(owner_id, tab_id, draft_id)
        if self._live.get(owner_id) != draft_id:
            self._pause_holder(owner_id, now)
            self._tokens.pop(draft_id, None)
        self._audit_add(draft_id, "switched", now)
        return self._view(self._open(owner_id, draft_id))

    def resume(self, owner_id: int, draft_id: str, tab_id: str, now: datetime) -> LiveDraft:
        """Record into this draft. Pauses any other holder. Same id comes back.

        Repeating it while this draft is already recording renews the lock
        and keeps the phrase token. It does not open a second session.
        """
        draft = self._require_open(owner_id, draft_id)
        self.registry.set_active(owner_id, tab_id, draft_id)
        if self._live.get(owner_id) == draft_id and draft_id in self._tokens:
            live.heartbeat(self._sessions[draft_id], owner_id, now)
            self._audit_add(draft_id, "resumed", now)
            return self._view(draft)
        # Start the target first. A failure leaves the previous holder recording.
        session_id = self._sessions.get(draft_id)
        if session_id is None:
            session = live.start(draft_id, owner_id, now)
            self._sessions[draft_id] = session.id
        else:
            live.continue_recording(session_id, owner_id, now)
        if self._live.get(owner_id) not in (None, draft_id):
            self._pause_holder(owner_id, now)
        self._state[draft_id] = live.RECORDING
        self._live[owner_id] = draft_id
        self._generation[owner_id] = self._generation.get(owner_id, 0) + 1
        self._tokens[draft_id] = self._generation[owner_id]
        self._audit_add(draft_id, "resumed", now)
        return self._view(self._open(owner_id, draft_id))

    def append_phrase(
        self,
        owner_id: int,
        draft_id: str,
        phrase: str,
        base_version: int,
        holder_token: int,
        tab_id: str,
        now: datetime,
    ) -> LiveDraft:
        """Append ``phrase`` only to the current recording draft and token.

        Any other target or token raises and leaves every draft's text as it was.
        """
        draft = self._require_open(owner_id, draft_id)
        if self._live.get(owner_id) != draft_id or self._state.get(draft_id) != live.RECORDING:
            self._audit_add(draft_id, "phrase_rejected", now)
            raise LiveDraftError("wrong_draft", "phrase is not for the live draft")
        if holder_token != self._tokens.get(draft_id):
            self._audit_add(draft_id, "phrase_rejected", now)
            raise LiveDraftError("stale_holder", "holder token does not match the live draft")
        if not isinstance(phrase, str) or phrase.strip() == "":
            raise LiveDraftError("invalid_phrase", "phrase must be a non-empty string")
        saved = self.registry.save(
            owner_id, draft_id, draft.text + phrase, base_version, tab_id, now
        )
        self._audit_add(draft_id, "phrase", now)
        return self._view(saved)

    def save(
        self,
        owner_id: int,
        draft_id: str,
        text: str,
        base_version: int,
        tab_id: str,
        now: datetime,
    ) -> LiveDraft:
        """Persist text on purpose. A stale ``base_version`` changes nothing."""
        self._require_open(owner_id, draft_id)
        saved = self.registry.save(owner_id, draft_id, text, base_version, tab_id, now)
        self._audit_add(draft_id, "saved", now)
        return self._view(saved)

    def cancel(self, owner_id: int, draft_id: str, now: datetime) -> LiveDraft:
        """Close without discarding. The last saved text stays in the list."""
        self._require_open(owner_id, draft_id)
        if self._live.get(owner_id) == draft_id:
            self._pause_holder(owner_id, now)
        self._audit_add(draft_id, "cancelled", now)
        return self._view(self._open(owner_id, draft_id))

    def commit_audio(self, owner_id: int, draft_id: str, audio_ref: str, now: datetime) -> str:
        """Store the original-audio reference once. A repeat of the same ref is a no-op."""
        self._require_open(owner_id, draft_id)
        if not isinstance(audio_ref, str) or audio_ref.strip() == "":
            raise LiveDraftError("invalid_audio", "audio_ref must be a non-empty string")
        current = self._audio.get(draft_id)
        if current is not None:
            if current == audio_ref:
                return current
            raise LiveDraftError("audio_committed", "original audio is immutable")
        self._audio[draft_id] = audio_ref
        self._audit_add(draft_id, "audio_committed", now)
        return audio_ref

    def discard(self, owner_id: int, draft_id: str, *, confirm: bool, now: datetime) -> LiveDraft:
        """Archive the draft only when ``confirm`` is true. Audio and audit stay."""
        self._require_known(owner_id, draft_id)
        if self._open_or_none(owner_id, draft_id) is None:
            raise DraftError("discarded", "this draft is discarded")
        if confirm is not True:
            try:
                self.registry.discard(owner_id, draft_id, confirm=confirm, now=now)
            except DraftError as exc:
                if exc.code == "confirmation_required":
                    self._audit_add(draft_id, "discard_refused", now)
                raise
            raise LiveDraftError("discard_failed", "discard did not stop")
        self._release(owner_id, draft_id, now)
        archived = self.registry.discard(owner_id, draft_id, confirm=True, now=now)
        self._audit_add(draft_id, "archived", now)
        return self._view(archived)

    def original_audio(self, owner_id: int, draft_id: str) -> str | None:
        """The committed original-audio reference, including after archive."""
        self._require_known(owner_id, draft_id)
        return self._audio.get(draft_id)

    def audit(self, owner_id: int, draft_id: str) -> tuple[DraftAudit, ...]:
        """Append-only actions for this draft, including after archive."""
        self._require_known(owner_id, draft_id)
        return tuple(entry for entry in self._audit if entry.draft_id == draft_id)

    def _next_id(self) -> str:
        if not self._queue:
            raise LiveDraftError("dictation_required", "a dictation id is required")
        return self._queue.pop(0)

    def _owner(self, owner_id: int) -> int:
        self.registry.list(owner_id)
        return owner_id

    def _require_known(self, owner_id: int, draft_id: str) -> None:
        self._owner(owner_id)
        if not isinstance(draft_id, str) or draft_id.strip() == "":
            raise DraftError("invalid_draft_id", "draft_id must be a non-empty string")
        owner = self._owners.get(draft_id)
        if owner is None:
            raise LiveDraftError("not_found", "no such draft")
        if owner != owner_id:
            raise LiveDraftError("unauthorized", "draft belongs to another user")

    def _require_open(self, owner_id: int, draft_id: str) -> Draft:
        self._require_known(owner_id, draft_id)
        draft = self._open_or_none(owner_id, draft_id)
        if draft is None:
            raise DraftError("discarded", "this draft is discarded")
        return draft

    def _open(self, owner_id: int, draft_id: str) -> Draft:
        draft = self._open_or_none(owner_id, draft_id)
        if draft is None:
            raise LiveDraftError("not_found", "no such draft")
        return draft

    def _open_or_none(self, owner_id: int, draft_id: str) -> Draft | None:
        for draft in self.registry.list(owner_id):
            if draft.id == draft_id:
                return draft
        return None

    def _pause_holder(self, owner_id: int, now: datetime) -> None:
        current = self._live.get(owner_id)
        if current is None:
            return
        live.pause(self._sessions[current], owner_id, now)
        self._state[current] = live.PAUSED
        self._tokens.pop(current, None)
        self._live[owner_id] = None
        self._audit_add(current, "paused", now)

    def _release(self, owner_id: int, draft_id: str, now: datetime) -> None:
        session_id = self._sessions.get(draft_id)
        if session_id is None or self._state.get(draft_id) == live.ENDED:
            return
        live.end(session_id, owner_id, now)
        self._state[draft_id] = live.ENDED
        self._tokens.pop(draft_id, None)
        if self._live.get(owner_id) == draft_id:
            self._live[owner_id] = None

    def _audit_add(self, draft_id: str, action: str, now: datetime) -> None:
        self._audit.append(DraftAudit(draft_id, action, _moment(now)))

    def _view(self, draft: Draft) -> LiveDraft:
        if draft.discarded_at is not None:
            state = ARCHIVED
            token = None
        else:
            state = self._state.get(draft.id, IDLE)
            token = self._tokens.get(draft.id) if state == live.RECORDING else None
        return LiveDraft(
            id=draft.id,
            owner_id=draft.owner_id,
            title=draft.title,
            text=draft.text,
            version=draft.version,
            state=state,
            audio_ref=self._audio.get(draft.id),
            holder_token=token,
            discarded_at=draft.discarded_at,
        )


def _dictation_id(dictation_id: str) -> str:
    if not isinstance(dictation_id, str) or dictation_id.strip() == "":
        raise DraftError("invalid_draft_id", "dictation id must be a non-empty string")
    return dictation_id


def _moment(now: datetime) -> datetime:
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(timezone.utc)
