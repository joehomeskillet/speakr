"""DOCX and plain-text export of a bound clinic release.

Acceptance catalog entries T-021, WB-013, WB-014 and WB-016 stay not_run.
These tests cover the service rules behind released text, draft marking and
filename safety. Inserting into an open native Word document and native
file pickers are not implemented.
"""

import hashlib
import uuid
from io import BytesIO
from pathlib import PurePosixPath, PureWindowsPath

import pytest
from docx import Document
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError

from src.app import app, db
from src.clinic.export import (
    DOWNLOAD_STARTED,
    DRAFT_MARK,
    RENDERED,
    USER_CONFIRMED,
    ExportRefused,
    download_started,
    export_filename,
    render_docx,
    render_text,
    user_confirmed,
)
from src.clinic.models import (
    ClinicAudioRevision,
    ClinicContextRevision,
    ClinicDictation,
    ClinicOriginal,
    ClinicRelease,
    ClinicTranscriptRevision,
)
from src.models import Recording, User
from src.services.document import DOCX_MIMETYPE

BODY = 'GEBUNDEN Größe äöüÄÖÜß Straße'
OTHER = 'NEUER STAND darf nicht im Export stehen'
LONG = (
    'Größe & Prüfung <Straße> äöüÄÖÜß\n'
    + ('Übergröße und Prüfung der Straße. ' * 400)
    + '\n\n# nicht **umschreiben**'
)
HOSTILE = '../../etc/passwd\x00:\\..\\secret:*?"<>|'


@pytest.fixture
def clinic_db():
    """App context with SQLite foreign keys on for this test only."""
    with app.app_context():
        hook = None
        if db.engine.dialect.name == 'sqlite':
            def hook(dbapi_connection, _record):
                cursor = dbapi_connection.cursor()
                cursor.execute('PRAGMA foreign_keys=ON')
                cursor.close()

            event.listen(db.engine, 'connect', hook)
            db.session.remove()
            db.engine.dispose()
        db.session.rollback()
        yield
        db.session.rollback()
        if hook is not None:
            event.remove(db.engine, 'connect', hook)
            db.session.remove()
            db.engine.dispose()


def _account():
    suffix = uuid.uuid4().hex[:10]
    user = User(
        username='cl' + suffix,
        email=suffix + '@example.invalid',
        password='x',
    )
    db.session.add(user)
    db.session.flush()
    recording = Recording(user_id=user.id, title='clinic', status='PENDING')
    db.session.add(recording)
    db.session.flush()
    return user, recording


def _dictation(user, recording):
    row = ClinicDictation(recording_id=recording.id, author_user_id=user.id)
    db.session.add(row)
    db.session.flush()
    return row


def _audio(dictation, content_hash=None):
    row = ClinicAudioRevision(
        dictation_id=dictation.id,
        content_hash=content_hash or ('a' * 64),
    )
    db.session.add(row)
    db.session.flush()
    return row


def _original(dictation):
    row = ClinicOriginal(
        dictation_id=dictation.id,
        content_hash='o' * 64,
        size_bytes=128,
        storage_key='originals/' + uuid.uuid4().hex,
        receipt='stored',
        origin_ref='device:test',
    )
    db.session.add(row)
    db.session.flush()
    return row


def _transcript(dictation, digest=None, original=None, body='recognised text'):
    digest = digest or ('t' * 64)
    original = original or _original(dictation)
    row = ClinicTranscriptRevision(
        dictation_id=dictation.id,
        original_version_id=original.id,
        kind='ASR',
        model_ref='stub-asr:1',
        body=body,
        normalised_text_hash=digest,
    )
    db.session.add(row)
    db.session.flush()
    return row


def _context(dictation):
    row = ClinicContextRevision(
        dictation_id=dictation.id,
        source_system='kis',
        tenant_scope='site',
        case_ref='case-1',
        patient_ref='patient-1',
    )
    db.session.add(row)
    db.session.flush()
    return row


def _release(dictation, audio, transcript, context, actor):
    row = ClinicRelease(
        dictation_id=dictation.id,
        audio_revision_id=audio.id,
        transcript_revision_id=transcript.id,
        context_revision_id=context.id,
        text_hash=transcript.normalised_text_hash,
        actor_user_id=actor.id,
        document_type='report',
        release_type='clinical',
        authorization_decision='{}',
    )
    db.session.add(row)
    db.session.flush()
    return row


def _pair(body=BODY):
    author, recording_a = _account()
    _other, recording_b = _account()
    dict_a = _dictation(author, recording_a)
    dict_b = _dictation(_other, recording_b)
    audio = _audio(dict_a)
    original = _original(dict_a)
    transcript = _transcript(dict_a, digest='a' * 64, original=original, body=body)
    _transcript(dict_a, digest='b' * 64, original=original, body=OTHER)
    context = _context(dict_a)
    return author, dict_a, dict_b, audio, transcript, context


def _released():
    author, dict_a, dict_b, audio, transcript, context = _pair()
    release = _release(dict_a, audio, transcript, context, author)
    return release, transcript, dict_b


def _text_parts(content):
    decoded = content.decode('utf-8')
    head, body = decoded.split('\n\n', 1)
    return head, body


def _docx_lines(content):
    document = Document(BytesIO(content))
    lines = [paragraph.text for paragraph in document.paragraphs]
    if lines and lines[0] == '':
        lines = lines[1:]
    return lines


def _assert_single_segment(name):
    assert PurePosixPath(name).name == name
    assert PureWindowsPath(name).name == name
    assert '/' not in name
    assert '\\' not in name
    assert '..' not in name
    assert '\x00' not in name
    assert ':' not in name


def test_released_export_contains_exactly_the_bound_text(clinic_db):
    release, transcript, _dict_b = _released()
    text = render_text(release)
    docx = render_docx(release)

    head, body = _text_parts(text.content)
    assert body == transcript.body == BODY
    assert OTHER not in text.content.decode('utf-8')
    assert 'Status: freigegeben' in head
    assert f'Freigegeben am: {release.created_at.isoformat()}' in head
    assert release.id in head
    assert transcript.id in head
    assert release.audio_revision_id in head
    assert release.context_revision_id in head
    assert release.text_hash in head
    assert DRAFT_MARK not in text.content.decode('utf-8')

    lines = _docx_lines(docx.content)
    n = BODY.count('\n') + 1
    assert '\n'.join(lines[-n:]) == BODY
    assert OTHER not in '\n'.join(lines)
    assert 'Status: freigegeben' in lines
    assert f'Freigegeben am: {release.created_at.isoformat()}' in lines
    assert any(release.id in line for line in lines)
    assert any(transcript.id in line for line in lines)
    assert DRAFT_MARK not in '\n'.join(lines)

    for rendered, mimetype in ((text, 'text/plain; charset=utf-8'), (docx, DOCX_MIMETYPE)):
        assert rendered.mimetype == mimetype
        assert rendered.sha256 == hashlib.sha256(rendered.content).hexdigest()
        assert rendered.release_id == release.id
        assert rendered.transcript_revision_id == transcript.id
        assert rendered.audio_revision_id == release.audio_revision_id
        assert rendered.context_revision_id == release.context_revision_id
        assert rendered.text_hash == transcript.normalised_text_hash
        assert rendered.mode == 'released'
        assert rendered.outcome.action == RENDERED
        assert rendered.outcome.local_save_proven is False
        assert rendered.outcome.sha256 == rendered.sha256


def test_export_refuses_hash_mismatch(clinic_db):
    release, _transcript, _dict_b = _released()
    release.text_hash = 'f' * 64
    for render in (render_text, render_docx):
        with pytest.raises(ExportRefused) as caught:
            render(release)
        assert caught.value.reason == 'hash_mismatch'


@pytest.mark.parametrize('field', ['transcript', 'audio', 'context'])
def test_export_refuses_revision_of_another_dictation(clinic_db, field):
    release, transcript, dict_b = _released()
    if field == 'transcript':
        foreign = _transcript(dict_b, digest='c' * 64, body='FREMD')
        release.transcript_revision_id = foreign.id
        release.text_hash = foreign.normalised_text_hash
    elif field == 'audio':
        release.audio_revision_id = _audio(dict_b, content_hash='c' * 64).id
    else:
        release.context_revision_id = _context(dict_b).id
    for render in (render_text, render_docx):
        with pytest.raises(ExportRefused) as caught:
            render(release)
        assert caught.value.reason == 'foreign_revision'
    assert transcript.body == BODY


def test_persisted_foreign_revision_still_rejected_by_the_database(clinic_db):
    """The service check does not replace the composite foreign key."""
    author, dict_a, dict_b, audio, transcript, context = _pair()
    foreign = _transcript(dict_b, digest='c' * 64, body='FREMD')
    db.session.add(ClinicRelease(
        dictation_id=dict_a.id,
        audio_revision_id=audio.id,
        transcript_revision_id=foreign.id,
        context_revision_id=context.id,
        text_hash=foreign.normalised_text_hash,
        actor_user_id=author.id,
        document_type='report',
        release_type='clinical',
        authorization_decision='{}',
    ))
    with pytest.raises(IntegrityError):
        db.session.flush()


@pytest.mark.parametrize('render', [render_text, render_docx])
def test_draft_without_explicit_flag_is_refused(clinic_db, render):
    _author, dict_a, _dict_b, _audio, transcript, _context = _pair()
    with pytest.raises(ExportRefused) as caught:
        render(transcript)
    assert caught.value.reason == 'draft_not_explicit'
    with pytest.raises(ExportRefused) as caught:
        render(transcript, allow_draft=False)
    assert caught.value.reason == 'draft_not_explicit'
    assert dict_a.id == transcript.dictation_id


def test_draft_marking_present_in_docx_and_text(clinic_db):
    _author, _dict_a, _dict_b, _audio_row, transcript, _context = _pair(body=LONG)
    text = render_text(transcript, allow_draft=True)
    docx = render_docx(transcript, allow_draft=True)

    head, body = _text_parts(text.content)
    assert head.startswith(DRAFT_MARK)
    assert body.startswith(DRAFT_MARK + '\n')
    assert body[len(DRAFT_MARK) + 1:] == LONG
    assert text.release_id is None
    assert text.mode == 'draft'
    assert text.text_hash == transcript.normalised_text_hash

    lines = _docx_lines(docx.content)
    n = LONG.count('\n') + 1
    assert lines[0] == DRAFT_MARK
    assert lines[-(n + 1)] == DRAFT_MARK
    assert '\n'.join(lines[-n:]) == LONG
    assert lines.count(DRAFT_MARK) >= 2
    assert '**umschreiben**' in '\n'.join(lines)
    assert 'äöüÄÖÜß' in '\n'.join(lines)
    Document(BytesIO(docx.content))


def test_filename_sanitising(clinic_db):
    release, transcript, _dict_b = _released()
    release.document_type = '../../etc/passwd'
    rendered = render_text(release)
    _assert_single_segment(rendered.filename)
    assert rendered.filename.endswith('.txt')
    assert rendered.filename.startswith('clinic-released-')
    assert transcript.id in rendered.filename

    labelled = render_docx(release, filename_label=HOSTILE)
    _assert_single_segment(labelled.filename)
    assert labelled.filename.endswith('.docx')
    assert labelled.filename.startswith('clinic-released-')
    assert transcript.id in labelled.filename

    direct = export_filename(
        transcript.dictation_id,
        transcript.id,
        '.docx',
        mode='draft',
        label=HOSTILE,
    )
    _assert_single_segment(direct)
    assert direct.endswith('.docx')
    assert transcript.id in direct

    umlaut = export_filename('x', 'y', '.txt', mode='released', label='Ärztlicher Bericht')
    _assert_single_segment(umlaut)
    assert 'Ärztlicher Bericht' in umlaut


def test_docx_roundtrip_umlauts_and_long_text(clinic_db):
    # Revisions are append-only: bind the release to a revision created with LONG.
    author, dict_a, _dict_b, audio, transcript, context = _pair(body=LONG)
    release = _release(dict_a, audio, transcript, context, author)
    rendered = render_docx(release)
    document = Document(BytesIO(rendered.content))
    lines = [paragraph.text for paragraph in document.paragraphs]
    if lines and lines[0] == '':
        lines = lines[1:]
    n = LONG.count('\n') + 1
    assert '\n'.join(lines[-n:]) == LONG
    assert 'äöüÄÖÜß' in LONG
    assert len(LONG) > 8000
    assert rendered.sha256 == hashlib.sha256(rendered.content).hexdigest()
    assert rendered.content[:2] == b'PK'


def test_download_outcome_does_not_prove_a_local_save(clinic_db):
    release, _transcript, _dict_b = _released()
    rendered = render_text(release)
    started = download_started(rendered)
    confirmed = user_confirmed(rendered, confirmed_by_user=True)

    assert [rendered.outcome.action, started.action, confirmed.action] == [
        RENDERED, DOWNLOAD_STARTED, USER_CONFIRMED,
    ]
    assert started.local_save_proven is False
    assert started.sha256 == rendered.sha256
    assert started.release_id == release.id
    assert confirmed.local_save_proven is True
    assert confirmed is not started
    assert confirmed is not rendered.outcome
    with pytest.raises(ExportRefused) as caught:
        user_confirmed(rendered, confirmed_by_user=False)
    assert caught.value.reason == 'user_confirmation_required'
    with pytest.raises(TypeError):
        user_confirmed(rendered, True)
