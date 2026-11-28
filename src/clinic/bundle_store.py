"""Persist portable dictations in the existing text and audio revision chains."""

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy.exc import SQLAlchemyError

from src.clinic import audio_render, audio_revisions, audio_segments, decoder, lease, live_session, release, revisions, workflow
from src.clinic.models import ClinicDictation, ClinicOriginal, ClinicTranscriptRevision
from src.clinic.parity_dispatch import CommandProblem
from src.clinic.richtext import CanonicalDocument, to_plain_text
from src.clinic.routes_bundle import BundleSession, StaleVersion, text_to_document
from src.database import db
from src.services.storage.locator import local_path_from_key


class ClinicBundleDocument(db.Model):
    """Portable rich text and its optional audio binding for one transcript revision."""

    __tablename__ = 'clinic_bundle_document'

    text_revision_id = db.Column(db.ForeignKey(ClinicTranscriptRevision.id), primary_key=True)
    audio_revision_id = db.Column(db.ForeignKey(audio_revisions.ClinicAudioRevision.id))
    segment_id = db.Column(db.ForeignKey(audio_revisions.ClinicAudioSegment.id))
    payload = db.Column(db.JSON, nullable=False)


def text_version(dictation):
    return db.session.query(ClinicTranscriptRevision).filter_by(dictation_id=dictation.id).count()


def audio_synced(document, audio_revision):
    if document is None:
        return None
    return audio_revision is not None and document.audio_revision_id == audio_revision.id


def lock_dictation(dictation_id):
    if db.session.get_bind().dialect.name == 'sqlite':
        # Serialize receipt lookup and writes with the audio/correction endpoints on SQLite.
        db.session.query(ClinicDictation).filter_by(id=dictation_id).update(
            {ClinicDictation.id: ClinicDictation.id}, synchronize_session=False)
    return db.session.get(ClinicDictation, dictation_id, populate_existing=True, with_for_update=True)


def _render(dictation, audio, load_sources):
    edl = audio_revisions.get_revision_edl(dictation, audio)
    return audio_render.render_audio(edl, load_sources(dictation, edl)).wav


def load(dictation, root, load_sources, *, include_audio=True):
    head, version = revisions.current_revision_with_count(dictation)
    audio = audio_revisions.current_audio_revision(dictation)
    if head is None and audio is None:
        return None
    saved = db.session.get(ClinicBundleDocument, head.id) if head else None
    synced = audio_synced(saved, audio)
    document = (CanonicalDocument.from_payload(saved.payload) if saved else
                text_to_document((head.body if head else '').encode('utf-8')))
    if saved and saved.audio_revision_id and not synced:
        document = CanonicalDocument(tuple(replace(node, runs=tuple(
            replace(run, audio_anchor_id=None) for run in node.runs)) for node in document.nodes))
    if not include_audio:
        return BundleSession(document, b'', version)
    wav = b''
    try:
        if synced:
            segment = db.session.get(audio_revisions.ClinicAudioSegment, saved.segment_id)
            if segment is not None and segment.dictation_id == dictation.id:
                wav = Path(local_path_from_key(str(root), segment.storage_key)).read_bytes()
                if sha256(wav).hexdigest() != segment.content_hash:
                    wav = b''
        elif audio is not None:
            wav = _render(dictation, audio, load_sources)
    except (OSError, ValueError):
        wav = b''
    return BundleSession(document, wav, version, synced and bool(wav))


def _assert_writable(dictation, actor, now):
    flow = db.session.get(workflow.ClinicWorkflowDictation, dictation.id, with_for_update=True)
    if flow is not None and (flow.status != workflow.DRAFT or dictation.author_user_id != actor):
        raise CommandProblem('POLICY_DENIED')
    held = lease.current(dictation.id)
    if held is not None and held.expires_at > now and held.holder_user_id != actor:
        raise StaleVersion()
    if live_session.active_session(dictation.id, now) is not None:
        raise StaleVersion()


def _store_audio(dictation, audio, root):
    if decoder.detect_container(audio[:16]) != 'wav':
        raise CommandProblem('INVALID_PAYLOAD')
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix='.clinic-bundle-', dir=root) as directory:
        source = Path(directory) / 'import.wav'
        source.write_bytes(audio)
        decoded = decoder.decode_to_wav(source, directory)
        if decoded.status in (decoder.DECODER_MISSING, decoder.TIMEOUT):
            raise OSError('bundle audio decoder unavailable')
        if decoded.status != decoder.OK or decoded.duration_s < 0.001:
            raise CommandProblem('INVALID_PAYLOAD')
        # Publish the verified input. Playback decodes it through the normal source loader.
        original = replace(decoded, output_path=str(source), output_sha256=decoded.input_sha256)
        return audio_segments.persist_segment(dictation, root, original, int(decoded.duration_s * 1000))


@contextmanager
def persist(dictation, actor, document, audio, expected, root, load_sources):
    """Stage one import under the dictation lock until its receipt has committed."""
    published = None
    try:
        if text_version(dictation) != expected:
            raise StaleVersion()
        now = datetime.now(timezone.utc)
        _assert_writable(dictation, actor, now)
        head = revisions.current_revision(dictation)
        previous = audio_revisions.current_audio_revision(dictation)
        if previous is None:
            raise OSError('dictation has no audio revision')
        original = None
        if head is None:
            initial = audio_revisions.get_revision_edl(dictation, previous)
            original = db.session.get(ClinicOriginal, next((e.source_id for e in initial.entries
                                      if e.source_type == audio_revisions.SOURCE_ORIGINAL), None))
            if original is None:
                raise OSError('dictation has no original for the first text revision')
        new_audio = segment = None
        if audio is not None:
            segment, published = _store_audio(dictation, audio, root)
            new_audio = audio_revisions.replace_audio(dictation, segment)
        else:
            try:
                _render(dictation, previous, load_sources)
            except audio_revisions.AudioRevisionError as error:
                raise OSError('dictation audio unavailable') from error
        head = revisions.add_human_revision(dictation, head, to_plain_text(document), actor,
                                            original_version=original, force_new=True)
        db.session.add(ClinicBundleDocument(text_revision_id=head.id, payload=document.to_payload(),
                                            audio_revision_id=new_audio.id if new_audio else None,
                                            segment_id=segment.id if segment else None))
        dictation.version_counter += 1
        release.invalidate_on_change(dictation, now)
        yield expected + 1
    except BaseException:
        if published is not None:
            published.unlink(missing_ok=True)
        raise


def run_locked(scope, digest, operation, run_once, authorize):
    """Commit the import and receipt together, with the same lock held for replays."""
    from flask import abort

    try:
        target = lock_dictation(scope[2])
        if target is None or not authorize(scope[0], 'import', target):
            abort(404)
        return run_once(scope, digest, operation)
    except SQLAlchemyError as error:
        db.session.rollback()
        raise CommandProblem('DEPENDENCY_UNAVAILABLE') from error
