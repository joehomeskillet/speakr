"""Training manifests from approved aligned corrections (PAR-ASR-004).

BT-ASR-004 stays the unchanged oracle and is not marked passed. These checks
cover selection, scope, stale approval and the refusal to claim learning.
"""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import event

from src.app import app, db
from src.clinic.asr_capabilities import (
    CAPABILITY_UNAVAILABLE,
    AsrCapability,
    AsrCapabilityRegistry,
    AsrTarget,
    CapabilityEvidence,
)
from src.clinic.asr_profiles import ClinicAsrTrainingRun, asr_status
from src.clinic.correction import set_manual_link
from src.clinic.models import (
    ClinicAudioRevision,
    ClinicContextRevision,
    ClinicDictation,
    ClinicOriginal,
)
from src.clinic.pools import add_author_member, create_author_pool
from src.clinic.release import create_release
from src.clinic.revisions import add_asr_revision, add_human_revision, text_hash
from src.clinic.training_manifest import TrainingManifestError, build_manifest
from src.clinic.voice_consent import ClinicVoiceEmbedding, grant_consent, revoke_consent
from src.models import Recording, User

ALLOWED = {"allowed": True, "role": "author", "rule": "release"}
WHEN = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
LATER = datetime(2026, 11, 18, 13, 0, tzinfo=timezone.utc)
ALIGNED = "Alpha Beta"
INSERT = "Alpha Beta extra"


@pytest.fixture
def clinic_db(clinic_on):
    with app.app_context():
        db.create_all()
        hook = None
        if db.engine.dialect.name == "sqlite":

            def hook(dbapi_connection, _record):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()

            event.listen(db.engine, "connect", hook)
            db.session.remove()
            db.engine.dispose()
        db.session.rollback()
        yield
        db.session.rollback()
        if hook is not None:
            event.remove(db.engine, "connect", hook)
            db.session.remove()
            db.engine.dispose()


def _user():
    suffix = uuid.uuid4().hex[:10]
    user = User(username="tm" + suffix, email=suffix + "@example.invalid", password="x")
    db.session.add(user)
    db.session.flush()
    return user


def _dictation(user, pool_id):
    recording = Recording(user_id=user.id, title="clinic", status="PENDING")
    db.session.add(recording)
    db.session.flush()
    row = ClinicDictation(
        recording_id=recording.id, author_user_id=user.id, pool_id=pool_id
    )
    db.session.add(row)
    db.session.flush()
    return row


def _original(dictation):
    row = ClinicOriginal(
        dictation_id=dictation.id, content_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        size_bytes=32, storage_key="originals/" + uuid.uuid4().hex, receipt="stored",
        origin_ref="device:synthetic",
    )
    db.session.add(row)
    db.session.flush()
    return row


def _audio(dictation):
    row = ClinicAudioRevision(dictation_id=dictation.id, content_hash=uuid.uuid4().hex)
    db.session.add(row)
    db.session.flush()
    return row


def _context(dictation):
    row = ClinicContextRevision(
        dictation_id=dictation.id, source_system="synthetic", tenant_scope="test",
        case_ref="case", patient_ref="anon",
    )
    db.session.add(row)
    db.session.flush()
    return row


def _release(user, dictation, audio, revision, context, when=WHEN):
    return create_release(
        dictation, audio, revision, context, revision.normalised_text_hash, user.id,
        ALLOWED, when, document_type="report", release_type="clinical",
    )


def _approved(user, body, pool_id, link_end=None):
    dictation = _dictation(user, pool_id)
    original = _original(dictation)
    asr = add_asr_revision(dictation, original, "asr", "stub-asr:1")
    human = add_human_revision(dictation, asr, body, user.id)
    audio = _audio(dictation)
    context = _context(dictation)
    end = len(human.body) if link_end is None else link_end
    set_manual_link(dictation, human, 0, end, 0, 1500, user.id)
    release = _release(user, dictation, audio, human, context)
    return {
        "dictation": dictation, "revision": human, "audio": audio,
        "context": context, "release": release,
    }


def _candidate(scene, **flags):
    row = {
        "dictation": scene["dictation"],
        "expected_revision_id": scene["revision"].id,
        "evaluation": False,
        "withdrawn": False,
    }
    row.update(flags)
    return row


def _ids(manifest):
    return [row["transcript_revision_id"] for row in manifest["items"]]


def _reason(manifest, revision_id):
    found = [
        row["reason"] for row in manifest["exclusions"]
        if row["transcript_revision_id"] == revision_id
    ]
    assert len(found) == 1
    return found[0]


def _corpus():
    author = _user()
    other = _user()
    pool = create_author_pool("Pool " + uuid.uuid4().hex[:8], actor_id=author.id)
    add_author_member(pool.id, author.id, actor_id=author.id)
    aligned = _approved(author, ALIGNED, pool.id)
    inserted = _approved(author, INSERT, pool.id, link_end=len(ALIGNED))
    holdout = _approved(author, "Nur Auswertung", pool.id)
    withdrawn = _approved(author, "Zurueckgezogen", pool.id)
    loose = _approved(author, "Ohne Pool", None)
    foreign_dict = _dictation(other, None)
    foreign_asr = add_asr_revision(foreign_dict, _original(foreign_dict), "fremd", "stub-asr:1")
    foreign = {"dictation": foreign_dict, "revision": foreign_asr}
    asr_dict = _dictation(author, pool.id)
    asr = add_asr_revision(asr_dict, _original(asr_dict), "ungeprueft", "stub-asr:1")
    unreviewed = {"dictation": asr_dict, "revision": asr}
    grant_consent(
        actor_user_id=author.id, user_id=author.id,
        statement_text="Ich erlaube die lokale Anpassung aus meinen Korrekturen.",
    )
    candidates = [
        _candidate(aligned),
        _candidate(aligned),
        _candidate(inserted),
        _candidate(holdout, evaluation=True),
        _candidate(withdrawn, withdrawn=True),
        _candidate(loose),
        _candidate(foreign),
        _candidate(unreviewed),
    ]
    return author, pool, {
        "aligned": aligned, "insert": inserted, "holdout": holdout,
        "withdrawn": withdrawn, "loose": loose, "foreign": foreign,
        "asr": unreviewed, "candidates": candidates,
    }


def _runs():
    return db.session.query(ClinicAsrTrainingRun).count()


def _embeddings():
    return db.session.query(ClinicVoiceEmbedding).count()


def test_selects_approved_aligned_and_freezes_exclusions(clinic_db):
    author, pool, scene = _corpus()
    args = dict(
        scope="author", subject_user_id=author.id, kind="language_model",
        candidates=scene["candidates"],
    )
    text = build_manifest(author.id, **args)
    again = build_manifest(author.id, **args)
    acoustic = build_manifest(
        author.id, scope="author", subject_user_id=author.id, kind="acoustic",
        candidates=scene["candidates"],
    )
    pooled = build_manifest(
        author.id, scope="pool", subject_user_id=author.id, pool_id=pool.id,
        kind="language_model", candidates=scene["candidates"],
    )
    aligned_id = scene["aligned"]["revision"].id

    assert text == again
    assert text["learning_claimed"] is False
    assert text["capability"] == CAPABILITY_UNAVAILABLE
    assert text["consent"]["state"] == "granted"
    assert len(text["consent"]["statement_hash"]) == 64
    assert _ids(text).count(aligned_id) == 1
    assert aligned_id in _ids(acoustic)
    assert scene["insert"]["revision"].id in _ids(text)
    assert scene["loose"]["revision"].id in _ids(text)
    assert _reason(acoustic, scene["insert"]["revision"].id) == "unaligned_insert"
    assert _reason(text, scene["asr"]["revision"].id) == "unreviewed_asr"
    assert _reason(acoustic, scene["asr"]["revision"].id) == "unreviewed_asr"
    assert _reason(text, scene["holdout"]["revision"].id) == "evaluation"
    assert _reason(text, scene["withdrawn"]["revision"].id) == "withdrawn"
    assert _reason(text, scene["foreign"]["revision"].id) == "unauthorized_scope"
    assert _reason(pooled, scene["loose"]["revision"].id) == "unauthorized_scope"
    assert aligned_id in _ids(pooled)
    assert scene["loose"]["revision"].id not in _ids(pooled)

    item = next(row for row in acoustic["items"] if row["transcript_revision_id"] == aligned_id)
    assert item["text_hash"] == text_hash(ALIGNED)
    assert item["audio_hash"] == scene["aligned"]["audio"].content_hash
    assert item["audio_revision_id"] == scene["aligned"]["audio"].id
    original = db.session.get(ClinicOriginal, scene["aligned"]["revision"].original_version_id)
    assert item["original_hash"] == original.content_hash
    assert item["approval"]["release_id"] == scene["aligned"]["release"].id
    assert item["approval"]["actor_user_id"] == author.id
    assert item["approval"]["text_hash"] == item["text_hash"]
    text_item = next(row for row in text["items"] if row["transcript_revision_id"] == aligned_id)
    assert text_item["audio_hash"] is None
    assert text["manifest_hash"] != acoustic["manifest_hash"]
    assert asr_status(author.id)["training_proven"] is False
    assert _runs() == 0
    assert _embeddings() == 0

    revoke_consent(actor_user_id=author.id, user_id=author.id)
    withdrawn = build_manifest(
        author.id, scope="author", subject_user_id=author.id, kind="acoustic",
        candidates=scene["candidates"],
    )
    assert aligned_id not in _ids(withdrawn)
    assert _reason(withdrawn, aligned_id) == "withdrawn"
    assert withdrawn["manifest_hash"] != acoustic["manifest_hash"]
    assert withdrawn["learning_claimed"] is False
    assert _runs() == 0
    assert _embeddings() == 0


def test_unauthorized_actor_is_refused(clinic_db):
    author, pool, scene = _corpus()
    stranger = _user()
    runs = _runs()
    for scope, extra in (("author", {}), ("pool", {"pool_id": pool.id})):
        with pytest.raises(TrainingManifestError, match="^unauthorized$"):
            build_manifest(
                stranger.id, scope=scope, subject_user_id=author.id,
                kind="language_model", candidates=scene["candidates"], **extra,
            )
    assert _runs() == runs
    assert _embeddings() == 0


def test_stale_version_retry_writes_nothing(clinic_db):
    author = _user()
    approved = _approved(author, ALIGNED, None)
    grant_consent(
        actor_user_id=author.id, user_id=author.id, statement_text="Lokale Anpassung."
    )
    runs = _runs()
    current = _candidate(approved)
    first = build_manifest(
        author.id, scope="author", subject_user_id=author.id, kind="language_model",
        candidates=[current],
    )
    assert first["items"][0]["transcript_revision_id"] == approved["revision"].id

    newer = add_human_revision(
        approved["dictation"], approved["revision"], ALIGNED + " neu", author.id
    )
    for _attempt in (1, 2):
        with pytest.raises(TrainingManifestError, match="^stale_version$"):
            build_manifest(
                author.id, scope="author", subject_user_id=author.id,
                kind="language_model", candidates=[current],
            )
    assert _runs() == runs
    assert _embeddings() == 0

    _release(
        author, approved["dictation"], approved["audio"], newer, approved["context"], LATER
    )
    refreshed = build_manifest(
        author.id, scope="author", subject_user_id=author.id, kind="language_model",
        candidates=[_candidate({"dictation": approved["dictation"], "revision": newer})],
    )
    assert _ids(refreshed) == [newer.id]
    assert _runs() == runs


def test_capability_gap_does_not_claim_learning(clinic_db):
    author = _user()
    approved = _approved(author, ALIGNED, None)
    grant_consent(
        actor_user_id=author.id, user_id=author.id, statement_text="Lokale Anpassung."
    )
    target = AsrTarget(
        base_url="http://127.0.0.1:9/asr", engine="local-asr", engine_revision="rev-1",
        model="gpu-de", model_revision="model-1", parameters_json="{}",
    )
    registry = AsrCapabilityRegistry([CapabilityEvidence(
        capability=AsrCapability.TRAINING, target=target, test_set_id="synthetic-set",
        evidence_ref="synthetic://evidence/training", outcome="runtime_passed",
    )])
    common = dict(
        scope="author", subject_user_id=author.id, candidates=[_candidate(approved)],
        capability_registry=registry, target=target,
    )
    text = build_manifest(author.id, kind="language_model", **common)
    acoustic = build_manifest(author.id, kind="acoustic", **common)
    assert text["capability"] == "runtime_passed"
    assert text["capability_evidence_ref"] == "synthetic://evidence/training"
    assert text["learning_claimed"] is False
    assert _ids(text) == [approved["revision"].id]
    assert acoustic["capability"] == CAPABILITY_UNAVAILABLE
    assert acoustic["learning_claimed"] is False
    assert _ids(acoustic) == [approved["revision"].id]
    assert asr_status(author.id)["training_proven"] is False
    assert _runs() == 0
    assert _embeddings() == 0


def test_patient_voice_profile_is_refused(clinic_db):
    author = _user()
    with pytest.raises(TrainingManifestError, match="^patient_voice_forbidden$"):
        build_manifest(
            author.id, scope="author", subject_user_id=author.id, kind="acoustic",
            candidates=[], subject_role="patient",
        )
    assert _runs() == 0
    assert _embeddings() == 0


def test_clinic_mode_off_refuses(clinic_off):
    with app.app_context():
        with pytest.raises(TrainingManifestError, match="^clinic_mode_required$"):
            build_manifest(
                1, scope="author", subject_user_id=1, kind="language_model", candidates=[]
            )
