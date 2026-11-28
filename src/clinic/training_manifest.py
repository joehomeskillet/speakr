"""Reproducible training manifest from approved, aligned corrections.

Selection only. A sample is included when a human correction is the active
release, the caller is that author, the dictation is in the requested author
or author-pool scope, and — for acoustic material — every character has a
same-dictation text/audio link and the author consent is still granted.
Unreviewed ASR, unaligned inserts, withdrawn data and evaluation holdouts
are frozen as exclusions.

Nothing here starts a job, records ``ClinicAsrTrainingRun`` or creates a
voice profile. ``learning_claimed`` stays false: a capability probe is not
a completed adaptation. Patient subjects are refused.
"""

from __future__ import annotations

import hashlib
import json

from src.clinic.asr_capabilities import CAPABILITY_UNAVAILABLE, AsrCapability
from src.clinic.asr_profiles import SCOPES, TRAINING_KINDS
from src.clinic.config import clinic_mode_enabled
from src.clinic.correction import effective_links
from src.clinic.models import (
    ClinicAudioRevision,
    ClinicOriginal,
    ClinicRelease,
    ClinicTranscriptRevision,
)
from src.clinic.pools import grants_for
from src.clinic.release import active_release
from src.clinic.revisions import KIND_ASR, KIND_HUMAN, current_revision, text_hash
from src.clinic.voice_consent import PURPOSE as CONSENT_PURPOSE
from src.clinic.voice_consent import consent_history, consent_state
from src.database import db

FORMAT = "clinic-training-manifest-v1"
_AUTHOR_ROLE = "author"


class TrainingManifestError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def build_manifest(
    actor_id: int,
    *,
    scope: str,
    subject_user_id: int,
    kind: str,
    candidates: list[dict],
    pool_id: str | None = None,
    subject_role: str = _AUTHOR_ROLE,
    capability_registry=None,
    target=None,
) -> dict:
    """Return a canonical manifest. Same inputs yield the same ``manifest_hash``.

    ``candidates`` name the dictation and the transcript revision the caller
    read. A newer active release than that revision is ``stale_version`` and
    writes nothing. ``scope`` is ``author`` (the subject themself) or ``pool``
    (that subject's dictations in ``pool_id``). ``kind`` is one of
    ``TRAINING_KINDS``: ``language_model`` is text, ``acoustic`` is text+audio.
    """
    if not clinic_mode_enabled():
        raise TrainingManifestError("clinic_mode_required")
    if subject_role != _AUTHOR_ROLE:
        raise TrainingManifestError("patient_voice_forbidden")
    if scope not in SCOPES:
        raise TrainingManifestError("scope_invalid")
    if kind not in TRAINING_KINDS:
        raise TrainingManifestError("kind_invalid")
    if scope == "author" and pool_id is not None:
        raise TrainingManifestError("scope_invalid")
    if scope == "pool" and (not isinstance(pool_id, str) or not pool_id):
        raise TrainingManifestError("scope_invalid")
    if isinstance(actor_id, bool) or not isinstance(actor_id, int) or actor_id != subject_user_id:
        raise TrainingManifestError("unauthorized")

    consent = _consent(subject_user_id)
    items: list[dict] = []
    exclusions: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for candidate in candidates:
        decision = _classify(
            actor_id, scope, subject_user_id, pool_id, kind, candidate, consent, seen
        )
        if decision is None:
            continue
        bucket, row = decision
        (items if bucket == "item" else exclusions).append(row)
    items.sort(key=lambda row: (row["dictation_id"], row["transcript_revision_id"]))
    exclusions.sort(key=lambda row: (
        row["dictation_id"], row["transcript_revision_id"], row["reason"]
    ))
    capability = _capability(kind, capability_registry, target)
    payload = {
        "format": FORMAT,
        "kind": kind,
        "scope": scope,
        "subject_user_id": subject_user_id,
        "pool_id": pool_id,
        "actor_id": actor_id,
        "learning_claimed": False,
        "capability": capability["status"],
        "capability_evidence_ref": capability["evidence_ref"],
        "consent": consent,
        "items": items,
        "exclusions": exclusions,
    }
    return _seal(payload)


def _consent(user_id: int) -> dict:
    state = consent_state(user_id)
    granted = [
        row for row in consent_history(user_id)
        if row["purpose"] == CONSENT_PURPOSE and row["status"] == "granted"
    ]
    return {
        "user_id": user_id,
        "purpose": CONSENT_PURPOSE,
        "state": state.state,
        "statement_hash": granted[-1]["statement_hash"] if granted else None,
    }


def _capability(kind: str, registry, target) -> dict:
    if registry is None or target is None:
        return {"status": CAPABILITY_UNAVAILABLE, "evidence_ref": None}
    name = AsrCapability.ADAPTATION if kind == "acoustic" else AsrCapability.TRAINING
    found = registry.check(name.value, target)
    if found == CAPABILITY_UNAVAILABLE:
        return {"status": CAPABILITY_UNAVAILABLE, "evidence_ref": None}
    return {"status": "runtime_passed", "evidence_ref": found.evidence_ref}


def _classify(actor_id, scope, subject_user_id, pool_id, kind, candidate, consent, seen):
    if not isinstance(candidate, dict):
        raise TrainingManifestError("candidate_invalid")
    dictation = candidate.get("dictation")
    expected = candidate.get("expected_revision_id")
    evaluation = candidate.get("evaluation", False)
    withdrawn = candidate.get("withdrawn", False)
    if (
        getattr(dictation, "id", None) is None
        or not isinstance(expected, str)
        or not expected
        or not isinstance(evaluation, bool)
        or not isinstance(withdrawn, bool)
    ):
        raise TrainingManifestError("candidate_invalid")
    key = (dictation.id, expected)
    if key in seen:
        return None
    seen.add(key)
    if not _in_scope(dictation, scope, subject_user_id, pool_id):
        return "exclusion", _exclusion(dictation.id, expected, "unauthorized_scope")
    if not _visible(actor_id, dictation):
        raise TrainingManifestError("unauthorized")

    revision = db.session.get(ClinicTranscriptRevision, expected)
    if revision is None or revision.dictation_id != dictation.id:
        raise TrainingManifestError("stale_version")
    release = active_release(dictation)
    head = current_revision(dictation)
    if release is not None and (
        release.transcript_revision_id != expected or head is None or head.id != expected
    ):
        raise TrainingManifestError("stale_version")
    if release is None:
        if _released_before(dictation.id, expected):
            raise TrainingManifestError("stale_version")
        reason = "unreviewed_asr" if revision.kind == KIND_ASR else "not_approved"
        return "exclusion", _exclusion(dictation.id, expected, reason, text_hash(revision.body))
    if revision.kind != KIND_HUMAN:
        return "exclusion", _exclusion(
            dictation.id, expected, "unreviewed_asr", text_hash(revision.body)
        )

    digest = text_hash(revision.body)
    if digest != revision.normalised_text_hash or digest != release.text_hash:
        raise TrainingManifestError("hash_mismatch")
    original = db.session.get(ClinicOriginal, revision.original_version_id)
    if original is None or original.dictation_id != dictation.id:
        raise TrainingManifestError("source_missing")
    base = _exclusion(dictation.id, expected, "", digest, release.id)
    if withdrawn or consent["state"] == "revoked":
        base["reason"] = "withdrawn"
        return "exclusion", base
    if evaluation:
        base["reason"] = "evaluation"
        return "exclusion", base
    if kind == "acoustic":
        if consent["state"] != "granted":
            base["reason"] = "consent_required"
            return "exclusion", base
        audio = db.session.get(ClinicAudioRevision, release.audio_revision_id)
        if audio is None or audio.dictation_id != dictation.id or not _aligned(dictation, revision):
            base["reason"] = "unaligned_insert"
            return "exclusion", base
        return "item", _item(dictation, revision, digest, original, release, audio)
    return "item", _item(dictation, revision, digest, original, release, None)


def _in_scope(dictation, scope, subject_user_id, pool_id) -> bool:
    if dictation.author_user_id != subject_user_id:
        return False
    if scope == "pool":
        return dictation.pool_id == pool_id
    return True


def _visible(actor_id, dictation) -> bool:
    return dictation.is_report and "view" in grants_for(actor_id, dictation)["allowed"]


def _released_before(dictation_id: str, revision_id: str) -> bool:
    return (
        db.session.query(ClinicRelease.id)
        .filter_by(dictation_id=dictation_id, transcript_revision_id=revision_id)
        .first()
        is not None
    )


def _aligned(dictation, revision) -> bool:
    covered = bytearray(len(revision.body))
    if not covered:
        return False
    for link in effective_links(dictation, revision):
        if link.dictation_id != dictation.id or link.transcript_revision_id != revision.id:
            return False
        if link.text_start < 0 or link.text_end > len(covered):
            return False
        covered[link.text_start:link.text_end] = b"\x01" * (link.text_end - link.text_start)
    return all(covered)


def _exclusion(dictation_id, revision_id, reason, digest=None, release_id=None) -> dict:
    row = {
        "dictation_id": dictation_id,
        "transcript_revision_id": revision_id,
        "reason": reason,
    }
    if digest is not None:
        row["text_hash"] = digest
    if release_id is not None:
        row["release_id"] = release_id
    return row


def _item(dictation, revision, digest, original, release, audio) -> dict:
    return {
        "dictation_id": dictation.id,
        "transcript_revision_id": revision.id,
        "text_hash": digest,
        "original_hash": original.content_hash,
        "audio_revision_id": None if audio is None else audio.id,
        "audio_hash": None if audio is None else audio.content_hash,
        "approval": {
            "release_id": release.id,
            "actor_user_id": release.actor_user_id,
            "text_hash": release.text_hash,
            "audio_revision_id": release.audio_revision_id,
        },
    }


def _seal(payload: dict) -> dict:
    body = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    sealed = dict(payload)
    sealed["manifest_hash"] = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return sealed
