"""DOCX and plain-text export of one clinic dictation revision.

A release is rendered only from the transcript revision it binds. The
dictated text is copied as paragraphs and is not rewritten. A draft is
rendered only when ``allow_draft=True`` and carries the marking
``ENTWURF – nicht freigegeben`` in the header and at the start of the body.

``rendered``, ``download_started`` and ``user_confirmed`` are separate
outcome records for later wiring. Starting a download does not prove that
the file was stored on the client.

Not implemented, and not claimed here: inserting into an already open
native Word document (WB-013) and native file pickers (WB-014).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO

from src.clinic.models import (
    ClinicAudioRevision,
    ClinicContextRevision,
    ClinicRelease,
    ClinicTranscriptRevision,
)
from src.database import db
from src.file_exporter import sanitize_export_filename
from src.services.document import DOCX_MIMETYPE, add_document_header, new_document

DRAFT_MARK = 'ENTWURF \u2013 nicht freigegeben'
RENDERER_VERSION = 'clinic-export-1'

RENDERED = 'rendered'
DOWNLOAD_STARTED = 'download_started'
USER_CONFIRMED = 'user_confirmed'

TEXT_MIMETYPE = 'text/plain; charset=utf-8'
_RELEASED_TITLE = 'Freigegebenes Diktat'
_ID_CHARS = frozenset('0123456789abcdefABCDEF-')


class ExportRefused(Exception):
    """The export was not rendered. ``reason`` is a stable code."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class ExportOutcome:
    """One audit fact. ``local_save_proven`` is never set because a download started."""

    action: str
    release_id: str | None
    dictation_id: str
    transcript_revision_id: str
    audio_revision_id: str | None
    context_revision_id: str | None
    text_hash: str
    sha256: str
    filename: str
    mimetype: str
    mode: str
    local_save_proven: bool


@dataclass(frozen=True)
class RenderedExport:
    """Bytes of one render, plus the ``rendered`` outcome. Not a download and not a save."""

    content: bytes
    filename: str
    mimetype: str
    sha256: str
    release_id: str | None
    dictation_id: str
    transcript_revision_id: str
    audio_revision_id: str | None
    context_revision_id: str | None
    text_hash: str
    mode: str
    outcome: ExportOutcome


@dataclass(frozen=True)
class _Source:
    mode: str
    body: str
    release_id: str | None
    dictation_id: str
    transcript_revision_id: str
    audio_revision_id: str | None
    context_revision_id: str | None
    text_hash: str
    released_at: datetime | None
    document_type: str
    release_type: str


def export_filename(dictation_id: str, revision_id: str, extension: str, *, mode: str, label: str = '') -> str:
    """One path segment. User-controlled path characters are not kept."""
    if extension not in ('.txt', '.docx'):
        raise ExportRefused('unsupported_extension')
    if mode not in ('released', 'draft'):
        raise ExportRefused('unsupported_source')
    stem = _plain(f'clinic-{mode}-{_id_token(dictation_id)}-{_id_token(revision_id)}')
    extra = _plain(label)
    if extra:
        stem = _plain(f'{stem}-{extra}')
    if not stem or '/' in stem or '\\' in stem or '..' in stem:
        stem = f'clinic-{mode}-{_id_token(dictation_id)}-{_id_token(revision_id)}'
    return stem + extension


def render_text(release_or_draft, *, allow_draft: bool = False, filename_label: str | None = None) -> RenderedExport:
    """UTF-8 text of the bound revision, or of a draft when explicitly allowed."""
    return _render(release_or_draft, kind='text', allow_draft=allow_draft, filename_label=filename_label)


def render_docx(release_or_draft, *, allow_draft: bool = False, filename_label: str | None = None) -> RenderedExport:
    """DOCX of the bound revision, or of a draft when explicitly allowed."""
    return _render(release_or_draft, kind='docx', allow_draft=allow_draft, filename_label=filename_label)


def download_started(rendered: RenderedExport) -> ExportOutcome:
    """Record that a download response was started.

    HTTP success is not a local save. This record never sets ``local_save_proven``.
    """
    return _outcome(rendered, DOWNLOAD_STARTED, False)


def user_confirmed(rendered: RenderedExport, *, confirmed_by_user: bool) -> ExportOutcome:
    """Record an explicit user statement that the file was saved locally.

    A download status code is not that statement. Only ``confirmed_by_user=True``
    produces the record.
    """
    if confirmed_by_user is not True:
        raise ExportRefused('user_confirmation_required')
    return _outcome(rendered, USER_CONFIRMED, True)


def _render(release_or_draft, *, kind: str, allow_draft: bool, filename_label: str | None) -> RenderedExport:
    source = _resolve(release_or_draft, allow_draft)
    draft = source.mode == 'draft'
    header = _header_lines(source)
    extension = '.txt' if kind == 'text' else '.docx'
    label = source.document_type if filename_label is None else filename_label
    filename = export_filename(
        source.dictation_id,
        source.transcript_revision_id,
        extension,
        mode=source.mode,
        label=label,
    )
    body = (DRAFT_MARK + '\n' + source.body) if draft else source.body
    if kind == 'text':
        content = ('\n'.join(header) + '\n\n' + body).encode('utf-8')
        mimetype = TEXT_MIMETYPE
    else:
        title = DRAFT_MARK if draft else _RELEASED_TITLE
        content = _docx_bytes(title, header, _paragraphs(body))
        mimetype = DOCX_MIMETYPE
    digest = hashlib.sha256(content).hexdigest()
    outcome = ExportOutcome(
        action=RENDERED,
        release_id=source.release_id,
        dictation_id=source.dictation_id,
        transcript_revision_id=source.transcript_revision_id,
        audio_revision_id=source.audio_revision_id,
        context_revision_id=source.context_revision_id,
        text_hash=source.text_hash,
        sha256=digest,
        filename=filename,
        mimetype=mimetype,
        mode=source.mode,
        local_save_proven=False,
    )
    return RenderedExport(
        content=content,
        filename=filename,
        mimetype=mimetype,
        sha256=digest,
        release_id=source.release_id,
        dictation_id=source.dictation_id,
        transcript_revision_id=source.transcript_revision_id,
        audio_revision_id=source.audio_revision_id,
        context_revision_id=source.context_revision_id,
        text_hash=source.text_hash,
        mode=source.mode,
        outcome=outcome,
    )


def _resolve(release_or_draft, allow_draft: bool) -> _Source:
    if isinstance(release_or_draft, ClinicRelease):
        return _bound_release(release_or_draft)
    if isinstance(release_or_draft, ClinicTranscriptRevision):
        if not allow_draft:
            raise ExportRefused('draft_not_explicit')
        return _bound_draft(release_or_draft)
    raise ExportRefused('unsupported_source')


def _bound_release(release: ClinicRelease) -> _Source:
    # no_autoflush: a dirty hash or a swapped revision id must be refused here.
    # Flushing it first would surface a database error instead of this check.
    with db.session.no_autoflush:
        transcript = db.session.get(ClinicTranscriptRevision, release.transcript_revision_id)
        audio = db.session.get(ClinicAudioRevision, release.audio_revision_id)
        context = db.session.get(ClinicContextRevision, release.context_revision_id)
    for row in (transcript, audio, context):
        if row is None:
            raise ExportRefused('missing_revision')
        if row.dictation_id != release.dictation_id:
            raise ExportRefused('foreign_revision')
    if transcript.normalised_text_hash != release.text_hash:
        raise ExportRefused('hash_mismatch')
    return _Source(
        mode='released',
        body=transcript.body,
        release_id=release.id,
        dictation_id=release.dictation_id,
        transcript_revision_id=transcript.id,
        audio_revision_id=audio.id,
        context_revision_id=context.id,
        text_hash=release.text_hash,
        released_at=release.created_at,
        document_type=release.document_type or '',
        release_type=release.release_type or '',
    )


def _bound_draft(transcript: ClinicTranscriptRevision) -> _Source:
    if not transcript.id or not transcript.dictation_id:
        raise ExportRefused('missing_revision')
    return _Source(
        mode='draft',
        body=transcript.body or '',
        release_id=None,
        dictation_id=transcript.dictation_id,
        transcript_revision_id=transcript.id,
        audio_revision_id=None,
        context_revision_id=None,
        text_hash=transcript.normalised_text_hash,
        released_at=None,
        document_type='',
        release_type='',
    )


def _header_lines(source: _Source) -> list[str]:
    if source.mode == 'draft':
        return [
            DRAFT_MARK,
            'Status: Entwurf',
            f'Diktat-ID: {source.dictation_id}',
            f'Textrevision: {source.transcript_revision_id}',
            f'Text-Hash: {source.text_hash}',
            f'Renderer: {RENDERER_VERSION}',
        ]
    released_at = source.released_at.isoformat() if source.released_at is not None else ''
    return [
        'Status: freigegeben',
        f'Freigegeben am: {released_at}',
        f'Freigabe-ID: {source.release_id}',
        f'Diktat-ID: {source.dictation_id}',
        f'Textrevision: {source.transcript_revision_id}',
        f'Audiorevision: {source.audio_revision_id}',
        f'Kontextrevision: {source.context_revision_id}',
        f'Text-Hash: {source.text_hash}',
        f'Dokumenttyp: {source.document_type}',
        f'Freigabeart: {source.release_type}',
        f'Renderer: {RENDERER_VERSION}',
    ]


def _paragraphs(body: str) -> list[str]:
    """Split on newlines only. The characters of ``body`` are not changed."""
    if body == '':
        return []
    return body.split('\n')


def _docx_bytes(title: str, header_lines: list[str], paragraphs: list[str]) -> bytes:
    doc = new_document()
    add_document_header(doc, title, header_lines)
    for paragraph in paragraphs:
        doc.add_paragraph(paragraph)
    buffer = BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def _outcome(rendered: RenderedExport, action: str, local_save_proven: bool) -> ExportOutcome:
    return ExportOutcome(
        action=action,
        release_id=rendered.release_id,
        dictation_id=rendered.dictation_id,
        transcript_revision_id=rendered.transcript_revision_id,
        audio_revision_id=rendered.audio_revision_id,
        context_revision_id=rendered.context_revision_id,
        text_hash=rendered.text_hash,
        sha256=rendered.sha256,
        filename=rendered.filename,
        mimetype=rendered.mimetype,
        mode=rendered.mode,
        local_save_proven=local_save_proven,
    )


def _plain(value: str) -> str:
    """Filename residue: no separators, no ``..``, unicode letters kept."""
    cleaned = sanitize_export_filename(value or '')
    while '..' in cleaned:
        cleaned = cleaned.replace('..', '.')
    return cleaned.strip(' .')


def _id_token(value: str | None) -> str:
    cleaned = ''.join(ch for ch in (value or '') if ch in _ID_CHARS)
    return cleaned or 'unknown'
