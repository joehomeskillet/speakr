"""Version-bound clinical release (R-020, T-020, T-061).

Three concepts stay apart and never share a function or a record type:

* Work-state transfer: handing the working state to another user. Recorded in
  ``ClinicWorkStateTransfer``. It binds no versions and is never a release.
* Release (Abnahme): an authorised human accepts exact audio, transcript and
  context revisions plus the text hash. Recorded in ``ClinicRelease``.
* Final KIS signature: external, out of scope here.

``ClinicRelease`` rows are never updated or deleted. A release stops being
active when a later revision exists; ``invalidate_on_change`` then appends a
``ClinicReleaseSupersession`` row. The bound ids stay readable.
Foreign-dictation revisions are rejected by the composite foreign keys of
``ClinicRelease``; the service check only gives an earlier, clearer error.
"""

import json
from datetime import datetime
from typing import Callable, Optional

from sqlalchemy import ForeignKeyConstraint, UniqueConstraint, func
from sqlalchemy.exc import IntegrityError

from src.database import db

from .audio_revisions import ClinicAudioSessionState
from .models import (
    ClinicAudioRevision,
    ClinicContextRevision,
    ClinicDictation,
    ClinicRelease,
    ClinicTranscriptRevision,
    _created_at,
    _uuid,
)


class ReleaseError(Exception):
    """A release or transfer request was refused."""


CLIPBOARD_DOCUMENT_VERSION = "clipboard_document_version"


class ClinicReleaseSupersession(db.Model):
    """Append-only mark: this release no longer matches the current heads."""

    __tablename__ = "clinic_release_supersession"

    id = _uuid()
    release_id = db.Column(db.String(36), nullable=False)
    reason = db.Column(db.String(80), nullable=False)
    superseded_at = db.Column(db.DateTime(timezone=True), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["release_id"], ["clinic_release.id"], name="fk_clinic_supersession_release"
        ),
        UniqueConstraint("release_id", name="uq_clinic_supersession_release"),
    )


class ClinicWorkStateTransfer(db.Model):
    """Handover of the working state. Not a release, binds no version."""

    __tablename__ = "clinic_work_state_transfer"

    id = _uuid()
    dictation_id = db.Column(db.String(36), nullable=False, index=True)
    from_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    to_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    transferred_at = db.Column(db.DateTime(timezone=True), nullable=False)
    created_at = _created_at()

    __table_args__ = (
        ForeignKeyConstraint(
            ["dictation_id"], ["clinic_dictation.id"], name="fk_clinic_transfer_dictation"
        ),
    )


# --- helpers ---------------------------------------------------------------

_REVISION_MODELS = {
    "audio": ClinicAudioRevision,
    "transcript": ClinicTranscriptRevision,
    "context": ClinicContextRevision,
}


def _require_aware(value: datetime, name: str) -> None:
    if value is None or value.tzinfo is None or value.utcoffset() is None:
        raise ReleaseError(name + " must be timezone-aware")


def _heads(model, dictation_id: str) -> set:
    """Leaf revision ids: rows no other revision of this dictation points to."""
    rows = db.session.query(model.id, model.parent_revision_id).filter(
        model.dictation_id == dictation_id
    ).all()
    parents = {parent for _id, parent in rows if parent is not None}
    return {rid for rid, _parent in rows if rid not in parents}


def current_audio_id(dictation_id: str) -> str | None:
    """Use the editor's play head; legacy audio without one needs exactly one leaf."""
    selected = db.session.query(ClinicAudioSessionState.current_revision_id).filter_by(
        dictation_id=dictation_id).scalar()
    if selected is not None:
        return selected
    leaves = _heads(ClinicAudioRevision, dictation_id)
    return next(iter(leaves)) if len(leaves) == 1 else None


def _is_head(model, dictation_id: str, revision_id: str) -> bool:
    if model is ClinicAudioRevision:
        return revision_id == current_audio_id(dictation_id)
    # A forked history has several leaves; then no single head exists and the
    # bound revision is not treated as current (fails closed).
    return _heads(model, dictation_id) == {revision_id}


def _check_authorization(decision) -> None:
    if not isinstance(decision, dict) or decision.get("allowed") is not True:
        raise ReleaseError("authorisation decision missing or denied")


def _existing(dictation_id, audio_id, transcript_id, context_id, text_hash,
              actor_user_id, document_type, release_type) -> Optional[ClinicRelease]:
    return (
        db.session.query(ClinicRelease)
        .filter_by(
            dictation_id=dictation_id,
            audio_revision_id=audio_id,
            transcript_revision_id=transcript_id,
            context_revision_id=context_id,
            text_hash=text_hash,
            actor_user_id=actor_user_id,
            document_type=document_type,
            release_type=release_type,
        )
        .order_by(ClinicRelease.created_at)
        .first()
    )


# --- release ---------------------------------------------------------------

def create_release(
    dictation: ClinicDictation,
    audio_revision: ClinicAudioRevision,
    transcript_revision: ClinicTranscriptRevision,
    context_revision: ClinicContextRevision,
    text_hash: str,
    actor_user_id: int,
    authorization_decision: Optional[dict],
    now: datetime,
    *,
    document_type: str,
    release_type: str,
    lease_check: Optional[Callable[[], bool]] = None,
) -> ClinicRelease:
    """Bind exact revisions and the text hash. Retry with equal input returns the same row.

    ``authorization_decision`` is the caller's permission decision, a dict with
    ``allowed: True``; it is stored with the release. ``lease_check`` is an
    optional injected callable that returns False when the caller's review
    lease generation is stale. Flushes inside a savepoint; the caller commits.
    """
    _require_aware(now, "now")
    revisions = (
        ("audio", audio_revision),
        ("transcript", transcript_revision),
        ("context", context_revision),
    )
    for name, revision in revisions:
        if revision.dictation_id != dictation.id:
            raise ReleaseError(name + " revision belongs to another dictation")
    if transcript_revision.normalised_text_hash != text_hash:
        raise ReleaseError("text hash does not match the bound transcript revision")
    _check_authorization(authorization_decision)

    found = _existing(
        dictation.id, audio_revision.id, transcript_revision.id, context_revision.id,
        text_hash, actor_user_id, document_type, release_type,
    )
    if found is not None:
        return found

    if lease_check is not None and not lease_check():
        raise ReleaseError("review lease generation is stale")
    for name, revision in revisions:
        if not _is_head(_REVISION_MODELS[name], dictation.id, revision.id):
            raise ReleaseError(name + " revision is not the current head")

    from .richtext import ClinicRichTextSnapshot

    version = db.session.query(func.max(ClinicRichTextSnapshot.version)).filter_by(
        revision_id=transcript_revision.id).scalar()
    # The immutable release records which formatting was available at acceptance.
    evidence = {**authorization_decision, CLIPBOARD_DOCUMENT_VERSION: version or 0}
    release = ClinicRelease(
        dictation_id=dictation.id,
        audio_revision_id=audio_revision.id,
        transcript_revision_id=transcript_revision.id,
        context_revision_id=context_revision.id,
        text_hash=text_hash,
        actor_user_id=actor_user_id,
        document_type=document_type,
        release_type=release_type,
        authorization_decision=json.dumps(evidence, sort_keys=True, ensure_ascii=False),
        created_at=now,
    )
    try:
        with db.session.begin_nested():
            db.session.add(release)
    except IntegrityError as error:
        raise ReleaseError("release rejected by database constraint") from error
    return release


def _release_is_current(release: ClinicRelease) -> bool:
    return (
        _is_head(ClinicAudioRevision, release.dictation_id, release.audio_revision_id)
        and _is_head(ClinicTranscriptRevision, release.dictation_id,
                     release.transcript_revision_id)
        and _is_head(ClinicContextRevision, release.dictation_id,
                     release.context_revision_id)
    )


def _is_superseded(release_id: str) -> bool:
    return (
        db.session.query(ClinicReleaseSupersession.id)
        .filter_by(release_id=release_id)
        .first()
        is not None
    )


def active_release(dictation: ClinicDictation) -> Optional[ClinicRelease]:
    """Newest release whose bound revisions are all still the current heads."""
    candidates = (
        db.session.query(ClinicRelease)
        .filter_by(dictation_id=dictation.id)
        .order_by(ClinicRelease.created_at.desc(), ClinicRelease.id)
        .all()
    )
    for release in candidates:
        if not _is_superseded(release.id) and _release_is_current(release):
            return release
    return None


def invalidate_on_change(
    dictation: ClinicDictation, now: datetime, reason: str = "revision_changed"
) -> list:
    """Append a supersession row for every release that no longer matches the heads.

    Idempotent; never touches the release rows. Returns the newly superseded
    releases. Flushes; the caller commits.
    """
    _require_aware(now, "now")
    releases = db.session.query(ClinicRelease).filter_by(dictation_id=dictation.id).all()
    changed = []
    for release in releases:
        if _is_superseded(release.id) or _release_is_current(release):
            continue
        db.session.add(
            ClinicReleaseSupersession(
                release_id=release.id, reason=reason, superseded_at=now
            )
        )
        changed.append(release)
    db.session.flush()
    return changed


# --- work-state transfer (not a release) -----------------------------------

def transfer_work_state(
    dictation: ClinicDictation,
    from_user_id: int,
    to_user_id: int,
    now: datetime,
    *,
    lease_check: Optional[Callable[[], bool]] = None,
) -> ClinicWorkStateTransfer:
    """Hand the working state to another user. Creates no release, binds no version."""
    _require_aware(now, "now")
    if from_user_id == to_user_id:
        raise ReleaseError("transfer needs a different recipient")
    if lease_check is not None and not lease_check():
        raise ReleaseError("review lease generation is stale")
    row = ClinicWorkStateTransfer(
        dictation_id=dictation.id,
        from_user_id=from_user_id,
        to_user_id=to_user_id,
        transferred_at=now,
    )
    db.session.add(row)
    db.session.flush()
    return row
