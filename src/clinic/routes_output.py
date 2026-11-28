"""Output routes: targets, export requests and export status (P16 backend reachability).

Thin HTTP layer over `export` (DOCX/text render), `kis` (delivery ledger),
`output_destinations` (server alias placement) and `release` (the active
release). Nothing here renders, delivers or decides policy itself:

* Only the active release of a dictation can be exported. The request names the
  transcript revision it expects; a different or unreleased revision is a 409.
* File exports are rendered from the revision the release binds. The job keeps
  the release and revision ids and the content hash. The durable receipt lets any worker
  return the same job for a replay by the same actor. A failed render is stored on
  that same per-format receipt, so a reload still shows the failed target.
  The target list defaults to txt and docx. The clinic output page requests
  all four file formats with file_formats=all. GET .../exports/<job>/download
  returns those bytes again for the owning actor and audits
  recording.content.read. It does not prove a local save.
* Existing KIS ledger entries remain readable. New KIS delivery is disabled
  unless synthetic staging explicitly enables it and uses its mock adapter.
* The actor is always `current_user`. The injected per-object policy allows
  viewers to list disabled targets. Exportable revisions and jobs require
  `export` permission.
* GET .../output-destinations lists the server alias catalog (name, kind,
  label, version). It never returns a filesystem path. Optional placement
  fields on POST .../exports (`filename_pattern` or `manual_name`,
  `destination_alias`, `collision_policy`) write the rendered bytes under
  that alias. A client-supplied path is refused. Collision and write
  failures stay distinct, and `local_save_proven` stays false. A successful
  placement appends a `ClinicOutputReceipt`.
"""

import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from flask import Blueprint, Flask, Response, abort, jsonify, request, session
from flask_login import current_user
from flask_wtf.csrf import CSRFError, validate_csrf
from sqlalchemy import or_
from werkzeug.exceptions import HTTPException
from wtforms.validators import ValidationError

from src.clinic import docx_template, export as export_service, odt_output
from src.clinic import kis, parity_receipts, presets, release
from src.clinic import routes_templates
from src.clinic.parity_dispatch import CommandProblem
from src.clinic.config import FALLBACK_CLIENT_PROFILE, clinic_mode_enabled, config_dir, kis_delivery_enabled
from src.clinic.models import (
    ClinicAudioRevision, ClinicContextRevision, ClinicDelivery, ClinicDictation, ClinicRelease,
    ClinicTranscriptRevision,
)
from src.clinic.output import (
    CHANNEL_TEXT, FORMAT_PLAIN, ClinicOutputReceipt, OutputNameContext, OutputNameError,
    OutputReceiptError, expand_filename, record_output,
)
from src.clinic.output_attempts import OutputAttemptError, OutputSource, bind_output_source
from src.clinic.output_destinations import (
    KIND_NAMED, KIND_WORKFLOW, POLICIES, ApprovedAlias, PlaceRequest, place_export,
)
from src.database import db
from src.models import Recording, User
from src.services import audit

BASE = '/api/clinic/v1/dictations/<uuid:dictation_id>'
# The default target list stays txt and docx for existing clients.
# rtf and odt are accepted on POST /exports and on the download while clinic mode is on.
FILE_FORMATS = {'txt': export_service.render_text, 'docx': export_service.render_docx}
_CLINIC_FILE_FORMATS = {
    'rtf': export_service.render_rtf,
    'odt': export_service.render_odt,
}
KIS_FORMAT = 'kis'
FORMATS = (*FILE_FORMATS, KIS_FORMAT)
ACTION = 'export'
_DELIVERY_STATUS = {'ACKNOWLEDGED': 'delivered', 'FAILED': 'rejected', 'UNKNOWN': 'unknown', 'PENDING': 'pending'}
_PROBLEMS = {
    400: ('INVALID_PAYLOAD', 'Ungültige Anfrage'),
    401: ('UNAUTHORIZED', 'Anmeldung erforderlich'),
    403: ('FORBIDDEN', 'Zugriff verweigert'),
    404: ('NOT_FOUND', 'Diktat nicht gefunden'),
    409: ('EXPORT_CONFLICT', 'Export nicht möglich'),
    422: ('TEMPLATE_MARK_ERROR', 'Vorlage ohne eindeutige Einfügemarke'),
    503: ('KIS_UNAVAILABLE', 'KIS-Anbindung nicht verfügbar'),
}
_OPTIONAL_EXPORT_KEYS = frozenset({
    'filename_pattern', 'manual_name', 'destination_alias', 'collision_policy',
    'client_path', 'path', 'destination_path', 'browser_download',
})
_FORMAT_EXTENSION = {'txt': 'txt', 'rtf': 'rtf', 'docx': 'docx', 'odt': 'odt'}
_ALIAS_ACTIONS = frozenset({'export'})


def _problem(status: int, code: str | None = None, *, title: str | None = None, **extra) -> Response:
    default_code, default_title = _PROBLEMS[status]
    response = jsonify(type='about:blank', title=title or default_title, status=status, code=code or default_code,
                       correlation_id=str(uuid4()), **extra)
    response.status_code = status
    response.mimetype = 'application/problem+json'
    return response


def configured_adapter(app: Flask) -> kis.KisAdapter:
    """Same selection as finalisation: configured adapter, MockKIS on staging, else disabled."""
    import os

    adapter = app.extensions.get('clinic_kis_adapter')
    if adapter is None:
        staging = os.environ.get('CLINIC_PROFILE', '').strip().lower() == 'staging'
        adapter = app.extensions.setdefault(
            'clinic_kis_adapter', kis.MockKisAdapter() if staging else kis.DisabledAdapter())
    return adapter


def _leaf(model, dictation: ClinicDictation):
    heads = release._heads(model, dictation.id)
    return db.session.get(model, next(iter(heads))) if len(heads) == 1 else None


def output_source(dictation: ClinicDictation) -> OutputSource:
    """Server-resolved current binding of a dictation; fails closed without a single head."""
    rows = (_leaf(ClinicAudioRevision, dictation), _leaf(ClinicTranscriptRevision, dictation),
            _leaf(ClinicContextRevision, dictation))
    if any(row is None for row in rows):
        raise OutputAttemptError('dictation has no single current revision')
    return bind_output_source(dictation, *rows)


def _job_view(job: dict) -> dict:
    return {key: value for key, value in job.items()
            if key not in ('actor_id', 'request', 'stable_docx_zip', 'renamed', 'placement_only')}


def file_formats() -> dict:
    """Renderers a POST or a download may use. rtf and odt need clinic mode."""
    if not clinic_mode_enabled():
        return dict(FILE_FORMATS)
    return {**FILE_FORMATS, **_CLINIC_FILE_FORMATS}


def destination_catalog() -> dict[str, ApprovedAlias]:
    """Server alias catalog. Missing or unreadable config is an empty catalog.

    A row without an absolute directory, a known kind, or a label is skipped.
    The client never supplies this list.
    """
    path = Path(config_dir()) / 'output-destinations.json'
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}
    rows = raw.get('aliases') if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        return {}
    catalog: dict[str, ApprovedAlias] = {}
    for row in rows:
        alias = _alias_row(row)
        if alias is not None and alias.name not in catalog:
            catalog[alias.name] = alias
    return catalog


def _alias_row(row: object) -> ApprovedAlias | None:
    if not isinstance(row, dict):
        return None
    name = row.get('name')
    root = row.get('root')
    kind = row.get('kind')
    label = row.get('label')
    version = row.get('version')
    if (not isinstance(name, str) or name.strip() == '' or '/' in name or '\\' in name
            or not isinstance(root, str) or not Path(root).is_absolute()
            or kind not in (KIND_WORKFLOW, KIND_NAMED)
            or not isinstance(label, str) or label.strip() == ''
            or type(version) is not int or version < 1):
        return None
    profiles = row.get('profiles')
    if profiles is None:
        allowed = frozenset({FALLBACK_CLIENT_PROFILE})
    elif isinstance(profiles, list) and profiles and all(isinstance(item, str) and item for item in profiles):
        allowed = frozenset(profiles)
    else:
        return None
    return ApprovedAlias(
        name=name, root=root, actions=_ALIAS_ACTIONS, profiles=allowed,
        version=version, kind=kind, label=label,
    )


def _destination_view(alias: ApprovedAlias) -> dict:
    """Public alias fields. The directory stays on the server."""
    return {
        'name': alias.name, 'kind': alias.kind, 'label': alias.label, 'version': alias.version,
        'local_save_proven': False,
    }


def _attachment_name(filename: str) -> str:
    """One quoted filename token. Quotes and path separators are not kept."""
    cleaned = ''.join(ch for ch in (filename or 'export.bin') if ch not in '"\\\r\n/' and ord(ch) >= 32)
    return cleaned or 'export.bin'


def _job_template(template: dict | None) -> dict:
    """Fields a later download needs to reproduce the same bytes."""
    if template is None:
        return {}
    return {'template_id': template['id'], 'text_mode': template['text_mode']}


def _failed_file_job(job_id, fmt, dictation_id, release_id, revision_id, actor_id, reason,
                     template: dict | None = None) -> dict:
    """Durable per-target failure. No file bytes and no exception text."""
    return {
        'id': job_id, 'kind': 'file', 'status': 'failed', 'format': fmt,
        'dictation_id': dictation_id, 'release_id': release_id, 'revision_id': revision_id,
        'reason': reason, 'actor_id': actor_id, **_job_template(template),
    }


def _latest_file_results(actor_id: int, release_id: str) -> dict:
    """Newest file receipt per format for this actor and release."""
    offered = file_formats()
    digests = [f'{release_id}:{fmt}' for fmt in offered]
    rows = (
        db.session.query(parity_receipts.ClinicParityReceipt)
        .filter(or_(parity_receipts.ClinicParityReceipt.digest.in_(digests),
                    parity_receipts.ClinicParityReceipt.digest.startswith(f'{release_id}:download:')))
        .all()
    )
    rows.sort(key=lambda row: (row.created_at, row.scope_key))
    found = {}
    for row in rows:
        result = row.result if isinstance(row.result, dict) else {}
        if result.get('kind') != 'file' or result.get('actor_id') != actor_id:
            continue
        fmt = result.get('format')
        if fmt in offered:
            found[fmt] = result
    return found


_OPTIONAL_FIELDS = frozenset({'template_id', 'text_mode'})
_DOCX_MIME = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
_ODT_MIME = 'application/vnd.oasis.opendocument.text'


class _TemplateExportError(Exception):
    """A template export was refused before bytes existed. ``code`` is a stable HTTP code."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _optional_template(body):
    """``None`` when the body is a plain export, a problem response when it is not.

    ``template_id`` and ``text_mode`` are optional and only valid together, on a
    docx or odt export. Every other shape stays the existing 400.
    """
    extra = _OPTIONAL_FIELDS & set(body)
    if not extra:
        return None
    if (extra != _OPTIONAL_FIELDS or body['format'] not in ('docx', 'odt')
            or not isinstance(body.get('template_id'), str)
            or body.get('text_mode') not in ('plain', 'formatted')):
        return _problem(400, detail='template_id und text_mode gelten nur gemeinsam für docx oder odt')
    try:
        template_id = str(UUID(body['template_id']))
    except ValueError:
        return _problem(400, detail='template_id ist keine UUID')
    row = db.session.get(presets.ClinicTemplate, template_id)
    # A foreign or inactive template is the same 404 as an unknown one.
    if row is None or not routes_templates._can_read_template(current_user.id, row):
        abort(404)
    expected = _DOCX_MIME if body['format'] == 'docx' else _ODT_MIME
    if row.mime_type != expected:
        return _problem(409, 'TEMPLATE_FORMAT_MISMATCH',
                        detail='Die Vorlage passt nicht zum angeforderten Format')
    if not row.insertion_mark:
        return _problem(422, 'TEMPLATE_MARK_ERROR', detail='Die Vorlage hat keine Einfügemarke')
    return {'id': row.id, 'text_mode': body['text_mode']}


def _template_of_job(job: dict) -> dict | None:
    """The template a stored job was rendered with, or ``None`` for a plain export."""
    template_id = job.get('template_id')
    if not template_id:
        return None
    return {'id': template_id, 'text_mode': job.get('text_mode')}


def _template_row(template: dict) -> presets.ClinicTemplate:
    row = db.session.get(presets.ClinicTemplate, template['id'])
    if row is None or not row.is_active:
        raise _TemplateExportError('TEMPLATE_NOT_FOUND')
    return row


def _render_with_template(active: ClinicRelease, fmt: str, template: dict):
    """Insert the released revision body into the permitted template, unchanged."""
    row = _template_row(template)
    source = export_service._resolve(active, allow_draft=False)
    text = source.body
    try:
        if fmt == 'docx':
            content = docx_template.insert_into_template(
                bytes(row.content_bytes), row.insertion_mark, text, text_mode=template['text_mode'])
        else:
            content = _odt_from_template(active, row, text, template['text_mode'])
    except docx_template.TemplateMarkError as exc:
        code = 'TEMPLATE_MACRO' if exc.code == 'macro' else 'TEMPLATE_MARK_ERROR'
        raise _TemplateExportError(code) from exc
    except odt_output.OdtOutputError as exc:
        # macro_refused is the package guard. Every other refusal is the mark:
        # missing, ambiguous, or a template that is not the stored ODT.
        code = 'TEMPLATE_MACRO' if exc.code == 'macro_refused' else 'TEMPLATE_MARK_ERROR'
        raise _TemplateExportError(code) from exc
    digest = hashlib.sha256(content).hexdigest()
    filename = export_service.export_filename(
        source.dictation_id, source.transcript_revision_id,
        '.docx' if fmt == 'docx' else '.odt', mode=source.mode, label=source.document_type)
    mimetype = _DOCX_MIME if fmt == 'docx' else _ODT_MIME
    outcome = export_service.ExportOutcome(
        action=export_service.RENDERED, release_id=source.release_id,
        dictation_id=source.dictation_id, transcript_revision_id=source.transcript_revision_id,
        audio_revision_id=source.audio_revision_id, context_revision_id=source.context_revision_id,
        text_hash=source.text_hash, sha256=digest, filename=filename,
        mimetype=mimetype, mode=source.mode, local_save_proven=False,
    )
    return export_service.RenderedExport(
        content=content, filename=filename, mimetype=mimetype, sha256=digest,
        release_id=source.release_id, dictation_id=source.dictation_id,
        transcript_revision_id=source.transcript_revision_id,
        audio_revision_id=source.audio_revision_id, context_revision_id=source.context_revision_id,
        text_hash=source.text_hash, mode=source.mode, outcome=outcome,
    )


def _odt_from_template(active: ClinicRelease, row, text: str, text_mode: str) -> bytes:
    """ODT through ``odt_output.render_odt_output``. It has no formatted switch.

    A formatted request is a mark error rather than a guess. This does not drive
    Writer, so WG-EXP-005 stays open.
    """
    if text_mode != 'plain':
        raise odt_output.OdtOutputError('mark_not_found')
    from src.clinic.output_attempts import OutputSource
    from src.clinic.revisions import text_hash
    from src.clinic.rich_text_doc import Node, to_plain_text
    from src.clinic.richtext import SCHEMA_VERSION, CanonicalDocument, Segment

    lines = text.split('\n') if text else ['']
    nodes = tuple(
        Node(f'p{index}', 'paragraph', (Segment(id=f's{index}', text=line),))
        for index, line in enumerate(lines)
    )
    document = CanonicalDocument(nodes)
    source = OutputSource(
        active.dictation_id, active.audio_revision_id, active.transcript_revision_id,
        active.context_revision_id, 'template-export', text_hash(to_plain_text(document)),
        'template-export', True,
    )
    produced = odt_output.render_odt_output(
        document, template=bytes(row.content_bytes), template_hash=row.content_hash,
        insertion_mark=row.insertion_mark, source=source, expected_version=SCHEMA_VERSION,
        allowed=True,
    )
    return produced.data


def create_blueprint(
    *, authorize: Callable[[User, str, ClinicDictation], bool],
    adapter_for: Callable[[], kis.KisAdapter],
    jobs: dict | None = None,
) -> Blueprint:
    """Mount with the trusted per-object policy and the configured KIS adapter."""
    bp = Blueprint('clinic_output', __name__)

    def dictation_of(dictation_id: UUID) -> ClinicDictation:
        dictation = db.session.get(ClinicDictation, str(dictation_id))
        if dictation is None or not authorize(current_user, ACTION, dictation):
            abort(404)
        return dictation

    def request_id() -> str:
        value = request.headers.get('Idempotency-Key', '')
        try:
            if str(UUID(value)) != value.lower():
                raise ValueError
        except ValueError:
            abort(400)
        return value.lower()

    @bp.before_request
    def authenticate() -> None:
        if not clinic_mode_enabled():
            abort(404)
        if not (current_user.is_authenticated and current_user.is_active
                and session.get('_user_id') == current_user.get_id()):
            abort(401)
        if request.method == 'POST':
            try:
                validate_csrf(request.headers.get('X-CSRFToken') or request.headers.get('X-CSRF-Token'))
            except ValidationError:
                abort(403)

    @bp.errorhandler(HTTPException)
    def http_problem(error: HTTPException) -> Response | HTTPException:
        if isinstance(error, CSRFError):
            return _problem(403)
        return _problem(error.code) if error.code in _PROBLEMS else error

    @bp.after_request
    def no_store(response: Response) -> Response:
        response.headers['Cache-Control'] = 'no-store'
        return response

    @bp.get(BASE + '/output-targets')
    def targets(dictation_id: UUID) -> Response:
        dictation = db.session.get(ClinicDictation, str(dictation_id))
        if dictation is None:
            abort(404)
        can_export = authorize(current_user, ACTION, dictation)
        # The app's audio and correction wiring maps correction_read to the shared view action.
        if not can_export and not authorize(current_user, 'correction_read', dictation):
            abort(404)

        active = release.active_release(dictation) if can_export else None
        gate = None
        if not can_export:
            gate = 'forbidden'
        elif active is None:
            gate = 'not_released'
        known = _latest_file_results(current_user.id, active.id) if active is not None else {}
        listed = []
        include_all = request.args.get('file_formats') == 'all'
        formats = file_formats() if include_all else FILE_FORMATS
        for fmt in formats:
            item = {'id': fmt, 'channel': 'file', 'available': gate is None, 'reason': gate}
            if fmt in known:
                job = known[fmt]
                status = job.get('status')
                # Old DOCX hashes include the render clock. Keep the receipt and offer a fresh render.
                if include_all and fmt == 'docx' and status == 'ready' and not job.get('stable_docx_zip'):
                    status = 'stale'
                item.update(status=status, job_id=job.get('id'))
                if job.get('renamed') and status == 'ready':
                    item['filename'] = job['filename']
            listed.append(item)
        if kis_delivery_enabled():
            adapter = adapter_for()
            kis_reason = gate or (None if adapter.enabled and adapter.is_mock else 'kis_unavailable')
            listed.append({'id': KIS_FORMAT, 'channel': 'kis', 'available': kis_reason is None,
                           'reason': kis_reason})
        listed.append({'id': 'clipboard', 'channel': 'clipboard', 'available': can_export,
                       'reason': None if can_export else gate})
        return jsonify(dictation_id=dictation.id,
                       released_revision_id=None if active is None else active.transcript_revision_id,
                       targets=listed)

    @bp.get(BASE + '/output-destinations')
    def destinations(dictation_id: UUID) -> Response:
        """Approved alias catalog for this dictation. Paths are not included."""
        dictation_of(dictation_id)
        profile = FALLBACK_CLIENT_PROFILE
        listed = [
            _destination_view(alias) for alias in destination_catalog().values()
            if 'export' in alias.actions and profile in alias.profiles
        ]
        listed.sort(key=lambda item: item['name'])
        return jsonify(dictation_id=str(dictation_id), destinations=listed, local_save_proven=False)

    @bp.post(BASE + '/exports')
    def request_export(dictation_id: UUID) -> Response:
        dictation = dictation_of(dictation_id)
        key = request_id()
        body = request.get_json(silent=True)
        offered = (*file_formats(), KIS_FORMAT)
        if not isinstance(body, dict) or not isinstance(body.get('revision_id'), str):
            abort(400)
        extra = set(body) - {'revision_id', 'format'}
        if extra - (_OPTIONAL_EXPORT_KEYS | _OPTIONAL_FIELDS) or body.get('format') not in offered:
            abort(400)
        export_extra = extra & _OPTIONAL_EXPORT_KEYS
        placing = 'destination_alias' in body
        name = _name_choice(body) if export_extra else None
        if export_extra and (not clinic_mode_enabled() or body['format'] == KIS_FORMAT or name is None):
            abort(400)
        if placing:
            collision_policy = body.get('collision_policy', 'fail')
            if not isinstance(collision_policy, str) or collision_policy not in POLICIES:
                abort(400)
        elif export_extra and (body.get('browser_download') is not True
                               or export_extra - {'filename_pattern', 'manual_name', 'browser_download'}):
            abort(400)
        if placing and 'browser_download' in body:
            abort(400)
        template = _optional_template(body)
        if isinstance(template, Response):
            return template
        revision = db.session.get(ClinicTranscriptRevision, body['revision_id'])
        if revision is None or revision.dictation_id != dictation.id:
            abort(404)
        active = release.active_release(dictation)
        if active is None or active.transcript_revision_id != revision.id:
            return _problem(409, 'NOT_RELEASED', detail='Nur die freigegebene Version kann ausgegeben werden')
        if body['format'] == KIS_FORMAT:
            return _kis_export(dictation, active)
        filename = None
        if name is not None and not placing:
            pattern, manual = name
            try:
                filename = expand_filename(
                    pattern if pattern is not None else manual.replace('%', '%%'),
                    _name_context(dictation, active), extension=_FORMAT_EXTENSION[body['format']],
                )
            except OutputNameError:
                return _problem(400, 'INVALID_FILENAME')
        rendered = _file_export(dictation, active, body['format'], key, body, filename, template)
        if not placing or rendered.status_code not in (200, 201):
            return rendered
        return _place_file_export(dictation, active, body, rendered)

    def _file_export(dictation: ClinicDictation, active: ClinicRelease, fmt: str, key: str,
                     body: dict, filename: str | None, template: dict | None = None) -> Response:
        actor_id = current_user.id
        dictation_id = dictation.id
        release_id = active.id
        revision_id = active.transcript_revision_id
        job_id = str(uuid5(NAMESPACE_URL, f'speakr/file-export/{actor_id}/{dictation_id}/{key}'))
        scope = (actor_id, 'file_export', dictation_id, job_id)
        # The template id and text mode belong to the idempotency digest: the same
        # key with another template is a conflict, not a silent second render.
        base_digest = f'{release_id}:{fmt}' if template is None else (
            f'{release_id}:{fmt}:{template["id"]}:{template["text_mode"]}')
        if 'destination_alias' in body:
            digest = base_digest + ':placement:' + _options_digest(body)
        elif filename is not None:
            digest = f'{release_id}:download:{fmt}:' + _options_digest(body)
            if template is not None:
                digest += f':{template["id"]}:{template["text_mode"]}'
        else:
            digest = base_digest

        def render_job():
            # A renderer error used to roll back with no receipt, so the target vanished on reload.
            try:
                rendered = (_render_with_template(active, fmt, template) if template is not None
                            else file_formats()[fmt](active))
            except _TemplateExportError as refused:
                db.session.rollback()
                return _failed_file_job(
                    job_id, fmt, dictation_id, release_id, revision_id, actor_id, refused.code, template)
            except export_service.ExportRefused as refused:
                db.session.rollback()
                return _failed_file_job(
                    job_id, fmt, dictation_id, release_id, revision_id, actor_id, refused.reason, template)
            except Exception:
                db.session.rollback()
                return _failed_file_job(
                    job_id, fmt, dictation_id, release_id, revision_id, actor_id, 'render_failed', template)
            job = {
                'id': job_id, 'kind': 'file', 'status': 'ready', 'format': fmt,
                'dictation_id': dictation_id, 'release_id': release_id,
                'revision_id': rendered.transcript_revision_id, 'filename': filename or rendered.filename,
                'mimetype': rendered.mimetype, 'sha256': rendered.sha256, 'actor_id': actor_id,
                **_job_template(template),
            }
            # Plain placements are not browser downloads; template placements stay downloadable for P-071.
            if 'destination_alias' in body and template is None:
                job['placement_only'] = True
            if filename is not None:
                job['renamed'] = True
            if fmt == 'docx':
                job['stable_docx_zip'] = True
            return job

        try:
            job, replayed = parity_receipts.run_once_with_replay(scope, digest, render_job)
        except CommandProblem as conflict:
            return _problem(409, str(conflict))
        if jobs is not None:
            # Preserve the optional caller-supplied index of observed jobs.
            jobs[(actor_id, dictation_id, key)] = jobs[('id', job_id)] = job
        if job.get('status') == 'failed' and not replayed and job.get('reason') != 'render_failed':
            reason = job.get('reason')
            if reason in ('TEMPLATE_MARK_ERROR', 'TEMPLATE_MACRO'):
                return _problem(422 if reason == 'TEMPLATE_MARK_ERROR' else 409, reason,
                                format=fmt, target_status='failed', id=job['id'])
            return _problem(409, 'EXPORT_REFUSED', reason=reason, format=fmt,
                            target_status='failed', id=job['id'])
        response = jsonify(_job_view(job) | {'replayed': replayed})
        response.status_code = 200 if replayed else 201
        return response

    def _place_file_export(
        dictation: ClinicDictation, active: ClinicRelease, body: dict, placed: Response,
    ) -> Response:
        """Write the rendered bytes under the named server alias and record the attempt."""
        job = placed.get_json(silent=True) or {}
        if job.get('status') != 'ready' or job.get('sha256') is None:
            return placed
        if job.get('replayed'):
            return _problem(409, 'DESTINATION_UNCONFIRMED')
        pattern, manual = _name_choice(body)
        fmt = job['format']
        try:
            template = _template_of_job(job)
            rendered = (_render_with_template(active, fmt, template) if template is not None
                        else file_formats()[fmt](active))
        except (_TemplateExportError, export_service.ExportRefused):
            return _problem(409, 'EXPORT_REFUSED', reason='render_failed', format=fmt)
        if rendered.sha256 != job.get('sha256'):
            return _problem(409, 'EXPORT_CONFLICT', detail='Gerenderte Datei stimmt nicht mit der Quittung überein')
        alias_name = body['destination_alias']
        catalog = destination_catalog()
        known = catalog.get(alias_name) if isinstance(alias_name, str) else None
        request_row = PlaceRequest(
            alias=alias_name if isinstance(alias_name, str) else '',
            alias_version=known.version if known is not None else 0,
            action='export',
            profile=FALLBACK_CLIENT_PROFILE,
            context=_name_context(dictation, active),
            policy=body.get('collision_policy', 'fail'),
            content=rendered.content,
            actor_user_id=current_user.id,
            pattern=pattern,
            manual_name=manual,
            extension=_FORMAT_EXTENSION[body['format']],
            client_path=_client_path(body),
        )
        receipt = place_export(catalog, request_row)
        if receipt.outcome == 'created':
            _remember_placement(dictation, active, receipt)
        return _placement_response(placed, receipt)

    def _kis_export(dictation: ClinicDictation, active: ClinicRelease) -> Response:
        # The ledger key binds to the release, not the HTTP request: at most one send per
        # release and target; a retry after UNKNOWN goes through kis.reconcile only.
        if not kis_delivery_enabled():
            return _problem(503, export_status='unavailable', detail='KIS-Ausgabe ist deaktiviert')
        adapter = adapter_for()
        if not adapter.enabled or not adapter.is_mock:
            return _problem(503, export_status='unavailable', detail='Keine KIS-Anbindung konfiguriert')
        context = db.session.get(ClinicContextRevision, active.context_revision_id)
        result = kis.deliver(active, context, adapter, f'export:{active.id}',
                             datetime.now(timezone.utc))
        if result.outcome == kis.REFUSED:
            return _problem(409, 'KIS_REFUSED', reason=result.reason)
        response = jsonify({
            'id': result.delivery_id, 'kind': 'kis', 'status': _status_of(result.outcome),
            'format': KIS_FORMAT, 'dictation_id': dictation.id, 'release_id': active.id,
            'revision_id': active.transcript_revision_id, 'replayed': result.replayed,
        })
        response.status_code = 200 if result.replayed else 201
        return response

    def _status_of(outcome: str) -> str:
        return {kis.DELIVERED: 'delivered', kis.REJECTED: 'rejected'}.get(outcome, 'unknown')

    @bp.get(BASE + '/exports/<uuid:job_id>/download')
    def download_export(dictation_id: UUID, job_id: UUID) -> Response:
        """Bytes of one ready file export, for the actor who rendered it.

        The receipt stores no bytes. The same renderer and the same release
        produce them again, and the stored sha256 must match. A failed job,
        another actor, or a missing receipt is 404. The download is not a
        proof that the file was stored on the client.
        """
        dictation = dictation_of(dictation_id)
        scope = (current_user.id, 'file_export', dictation.id, str(job_id))
        receipt = db.session.get(parity_receipts.ClinicParityReceipt, parity_receipts.scope_key(scope))
        job = receipt.result if receipt is not None and isinstance(receipt.result, dict) else None
        if (job is None or job.get('kind') != 'file' or job.get('status') != 'ready' or job.get('placement_only')
                or job.get('actor_id') != current_user.id or job.get('format') not in file_formats()):
            abort(404)
        active = db.session.get(ClinicRelease, job.get('release_id'))
        if (active is None or active.dictation_id != dictation.id
                or active.transcript_revision_id != job.get('revision_id')):
            abort(404)
        try:
            template = _template_of_job(job)
            rendered = (_render_with_template(active, job['format'], template) if template is not None
                        else file_formats()[job['format']](active))
        except (_TemplateExportError, export_service.ExportRefused):
            abort(404)
        if (rendered.sha256 != job.get('sha256')
                or (not job.get('renamed') and rendered.filename != job.get('filename'))
                or hashlib.sha256(rendered.content).hexdigest() != job.get('sha256')):
            abort(409)
        export_service.download_started(rendered)
        audit.record(
            'recording.content.read', recording_id=dictation.recording_id,
            actor_kind='session', actor_user_id=current_user.id, details={'part': 'export'},
        )
        response = Response(rendered.content, mimetype=rendered.mimetype.split(';')[0])
        response.headers['Content-Type'] = rendered.mimetype
        filename = _attachment_name(job['filename'])
        if job.get('renamed') and not filename.isascii():
            response.headers['Content-Disposition'] = (
                f'attachment; filename="{_attachment_name(rendered.filename)}"; '
                f"filename*=UTF-8''{quote(filename, safe='')}"
            )
        else:
            response.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
        response.headers['X-Content-SHA256'] = rendered.sha256
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    @bp.get(BASE + '/exports/<uuid:job_id>')
    def export_status(dictation_id: UUID, job_id: UUID) -> Response:
        dictation = dictation_of(dictation_id)
        scope = (current_user.id, 'file_export', dictation.id, str(job_id))
        receipt = db.session.get(parity_receipts.ClinicParityReceipt, parity_receipts.scope_key(scope))
        if receipt is not None:
            return jsonify(_job_view(receipt.result))
        row = db.session.get(ClinicDelivery, str(job_id))
        owner = None if row is None else db.session.get(ClinicRelease, row.release_id)
        if owner is None or owner.dictation_id != dictation.id:
            abort(404)
        return jsonify(id=row.id, kind='kis', status=_DELIVERY_STATUS.get(row.state, 'unknown'),
                       format=KIS_FORMAT, dictation_id=dictation.id, release_id=owner.id,
                       revision_id=owner.transcript_revision_id)

    return bp


def _options_digest(body: Mapping) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def _name_choice(body: Mapping) -> tuple[str, None] | tuple[None, str] | None:
    """Exactly one of pattern or manual name. Both, neither, or a non-string is refused."""
    pattern = body.get('filename_pattern', None)
    manual = body.get('manual_name', None)
    if pattern is not None and manual is not None:
        return None
    if isinstance(pattern, str) and pattern != '':
        return pattern, None
    if isinstance(manual, str) and manual != '':
        return None, manual
    return None


def _client_path(body: Mapping) -> str | None:
    """Any client path field is the refusal signal. The value is never used as a path."""
    for key in ('client_path', 'path', 'destination_path'):
        if key in body:
            return '' if body[key] is None else str(body[key])
    return None


def _name_context(dictation: ClinicDictation, active: ClinicRelease) -> OutputNameContext:
    """Placeholder values taken from the recording and the release, never from the request."""
    recording = db.session.get(Recording, dictation.recording_id)
    author = db.session.get(User, dictation.author_user_id)
    creator = db.session.get(User, active.actor_user_id)
    created = recording.created_at if recording is not None else None
    if created is not None and created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    title = recording.title if recording is not None and recording.title else None
    return OutputNameContext(
        author=None if author is None else author.username,
        title=title,
        created_on=created,
        service_staff=None if author is None else author.username,
        document_creator=None if creator is None else creator.username,
    )


def _remember_placement(dictation: ClinicDictation, active: ClinicRelease, receipt) -> None:
    """Append the placement to the output ledger. A repeat of the same file is one row."""
    target = f'{receipt.alias}/{receipt.filename}'
    if len(target) > 255:
        target = target[:255]
    existing = (
        db.session.query(ClinicOutputReceipt.id)
        .filter(
            ClinicOutputReceipt.dictation_id == dictation.id,
            ClinicOutputReceipt.transcript_revision_id == active.transcript_revision_id,
            ClinicOutputReceipt.actor_user_id == receipt.actor_user_id,
            ClinicOutputReceipt.target == target,
        )
        .first()
    )
    if existing is not None:
        return
    revision = db.session.get(ClinicTranscriptRevision, active.transcript_revision_id)
    if revision is None:
        return
    try:
        record_output(
            dictation, revision, receipt.actor_user_id, CHANNEL_TEXT, target,
            text_format=FORMAT_PLAIN, output_at=receipt.at,
        )
        db.session.commit()
    except OutputReceiptError:
        db.session.rollback()


def _placement_response(placed: Response, receipt) -> Response:
    """Keep the file job and add the alias result. Collision and write errors differ."""
    job = placed.get_json(silent=True) or {}
    body = {
        **job,
        'destination': {
            'alias': receipt.alias,
            'alias_version': receipt.alias_version,
            'kind': receipt.kind,
            'label': receipt.label,
            'policy': receipt.policy,
            'requested_filename': receipt.requested_filename,
            'filename': receipt.filename,
            'relative_path': receipt.relative_path,
            'outcome': receipt.outcome,
            'reason': receipt.reason,
            'sha256': receipt.sha256,
            'size': receipt.size,
            'local_save_proven': False,
        },
        'local_save_proven': False,
    }
    if receipt.outcome == 'created':
        response = jsonify(body)
        response.status_code = placed.status_code
        return response
    if receipt.outcome == 'collision':
        return _problem(
            409, 'NAME_COLLISION', detail='Dateiname ist bereits vergeben',
            reason=receipt.reason, local_save_proven=False, destination=body['destination'],
            id=job.get('id'), format=job.get('format'),
        )
    if receipt.outcome == 'write_error':
        return _problem(
            422, 'DESTINATION_WRITE_FAILED', title='Ablage fehlgeschlagen',
            reason=receipt.reason, local_save_proven=False, destination=body['destination'],
            id=job.get('id'), format=job.get('format'),
        )
    return _problem(
        409, 'DESTINATION_REJECTED', detail='Ablageziel abgelehnt',
        reason=receipt.reason, local_save_proven=False, destination=body['destination'],
        id=job.get('id'), format=job.get('format'),
    )
