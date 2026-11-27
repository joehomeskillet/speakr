"""Gate providers for the web finalize path (W11 review markers, trusted context)."""

from sqlalchemy import select
from sqlalchemy.orm import aliased

from src.database import db

from . import caution_words, revisions
from .kis import PRODUCTION_PROFILE, KisAdapter, _profile
from .models import ClinicContextRevision, ClinicDictation, ClinicTranscriptRevision
from .context_grants import ClinicContextGrantUse


def open_review_flags(dictation: ClinicDictation, head: ClinicTranscriptRevision) -> int:
    """Count open W11 hits on the head plus ASR revisions off its lineage.

    An ASR flag closes once a human revision takes it into the head's parent chain.
    """
    return len(caution_words.open_review_hits(dictation, head)) + sum(
        1 for row in revisions.history(dictation)
        if row.id != head.id and revisions.is_flagged(dictation, row)
    )


def trusted_context(context: ClinicContextRevision, adapter: KisAdapter | None = None) -> bool:
    """True only for the current context revision bound to a consumed KIS grant.

    The grant was verified, unexpired (<= 60 s at bind time) and single-use (unique
    nonce) when ``bind_context_grant`` appended the revision; here the durable
    provenance row is required. No adapter, a disabled adapter, or a mock adapter
    in the production profile is never trusted.
    """
    if (adapter is None or not adapter.enabled
            or (adapter.is_mock and _profile() == PRODUCTION_PROFILE)):
        return False
    if db.session.get(ClinicContextGrantUse, context.id) is None:
        return False
    child = aliased(ClinicContextRevision)
    superseded = select(child.id).where(
        child.dictation_id == context.dictation_id, child.parent_revision_id == context.id,
    ).exists()
    return db.session.scalar(select(~superseded)) is True
