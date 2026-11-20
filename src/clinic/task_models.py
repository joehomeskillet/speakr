"""Model identifiers for clinic tasks. Transport URLs and credentials stay in deployment."""

import json
import os
import re
from urllib.parse import urlsplit

import httpx
from flask import has_app_context
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from src.clinic import config
from src.database import db
from src.models import SystemSetting

KEY = 'clinic_task_models'
TASKS = ('asr_gpu', 'asr_cpu', 'summary')
# Supports e.g. namespace/model, GGUF names and Ollama's model:tag.
_ID = re.compile(r'[\w][\w.+:-]*(?:/[\w][\w.+:-]*)*')


class ModelProblem(ValueError):
    pass


def model_id(value):
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if (not isinstance(value, str) or len(value) > 200 or not value.isprintable()
            or ':/' in value or not _ID.fullmatch(value.strip())):
        raise ModelProblem('invalid_model')
    return value.strip()


def _safe_id(value):
    try:
        return model_id(value)
    except ModelProblem:
        return None


def _decode(raw):
    try:
        data = json.loads(raw) if raw else {}
        if not isinstance(data, dict) or not isinstance(data.get('models', {}), dict):
            data = {}
    except (ValueError, TypeError):
        data = {}
    revision = data.get('revision')
    revision = revision if isinstance(revision, str) and re.fullmatch(r'[a-f0-9]{32}', revision) else '0'
    return raw, revision, {k: _safe_id(data.get('models', {}).get(k)) for k in TASKS}


def stored():
    raw = db.session.query(SystemSetting.value).filter_by(key=KEY).scalar()
    return _decode(raw)


def override(task):
    if not config.clinic_mode_enabled() or not has_app_context():
        return None
    # ASR deliberately releases its session before an external request. Model
    # resolution must not reopen a transaction that lasts throughout inference.
    if db.session().in_transaction():
        return stored()[2][task]
    with db.engine.connect() as conn:
        raw = conn.scalar(select(SystemSetting.value).where(SystemSetting.key == KEY))
    return _decode(raw)[2][task]


def asr_is_local(url):
    from src.services.transcription.local_only import is_local_base_url
    if not isinstance(url, str) or not url or re.search(r'[\\\s@]', url):
        return False
    try:
        return urlsplit(url).scheme in ('http', 'https') and is_local_base_url(url)
    except ValueError:
        return False


def gpu_choices(url):
    from src.clinic.asr_fallback import health_url, served_gpu_model

    if not asr_is_local(url):
        return []
    try:
        with httpx.Client(trust_env=False, follow_redirects=False, timeout=2) as client:
            response = client.get(health_url(url))
            served = _safe_id(served_gpu_model(response.json())) if response.is_success else None
    except (httpx.HTTPError, ValueError):
        return []
    return list(dict.fromkeys([served, 'whisper-1'])) if served else []


def _model_catalog(base_url, *, endpoint=False):
    suffix = '/v1/models' if endpoint else '/models'
    try:
        with httpx.Client(trust_env=False, follow_redirects=False, timeout=1.5) as client:
            response = client.get(str(base_url).rstrip('/') + suffix)
            body = response.json() if response.is_success else None
    except (httpx.HTTPError, ValueError):
        return []
    if not isinstance(body, dict) or body.get('has_more') or not isinstance(body.get('data'), list):
        return []
    items = body['data']
    ids = [_safe_id(item.get('id')) if isinstance(item, dict) else None for item in items]
    return list(dict.fromkeys(ids)) if all(ids) else []


def catalog():
    from src.clinic.asr_fallback import gpu_base_url
    from src.clinic.dictation_summary import local_model_ready
    from src.services import llm
    from src.services.transcription import get_connector
    from src.services.transcription.exceptions import ConfigurationError

    try:
        cpu = get_connector()
        url = str(cpu.config.get('base_url') or '')
        cpu_local = asr_is_local(url)
        cpu_default = getattr(cpu, 'model', None)
        cpu_ids = _model_catalog(url, endpoint=cpu.PROVIDER_NAME == 'asr_endpoint') if cpu_local else []
    except (ConfigurationError, ValueError):
        cpu_local, cpu_default, cpu_ids = False, None, []
    summary_local = local_model_ready()
    summary_ids = _model_catalog(llm.client.base_url) if summary_local else []
    return dict(cpu_local=cpu_local, cpu_default=cpu_default, summary_local=summary_local,
                choices=dict(asr_gpu=gpu_choices(gpu_base_url()), asr_cpu=cpu_ids, summary=summary_ids))


def _entry(saved, inherited, source, active, verified, suggestions=()):
    inherited = _safe_id(inherited)
    effective = saved or inherited
    if not active or (verified and effective not in verified):
        effective = None
    return dict(override=saved, effective=effective, active=bool(active), choices=list(verified or suggestions),
                choices_strict=bool(verified), source='override' if saved else source if inherited else 'unset')


def representation(revision, saved, catalog_info):
    from src.clinic.asr_fallback import GPU_MODEL_ENV, gpu_base_url
    from src.config.app_config import TRANSCRIPTION_MODELS_AVAILABLE
    from src.services import llm
    from src.services.transcription_defaults import resolve_transcription_model

    cpu_default, choices = catalog_info['cpu_default'], catalog_info['choices']
    default = resolve_transcription_model(None)
    try:
        labels = json.loads(SystemSetting.get_setting('transcription_models_visible_json', '') or '[]')
    except (TypeError, ValueError):
        labels = []
    candidates = [item.get('value') if isinstance(item, dict) else item for item in labels] if isinstance(labels, list) else []
    cpu_choices = list(dict.fromkeys(v for item in [*candidates, *TRANSCRIPTION_MODELS_AVAILABLE] if (v := _safe_id(item))))
    gpu_pin = os.environ.get(GPU_MODEL_ENV, '').strip()
    gpu_default = gpu_pin or (choices['asr_gpu'][0] if choices['asr_gpu'] else cpu_default)
    return dict(revision=revision, models={
        'asr_cpu': _entry(saved['asr_cpu'], default or cpu_default, 'existing_default' if default else 'deployment',
                          catalog_info['cpu_local'], choices['asr_cpu'], cpu_choices),
        'asr_gpu': _entry(saved['asr_gpu'], gpu_default if gpu_base_url() else None,
                          'service' if choices['asr_gpu'] and not gpu_pin else 'deployment', asr_is_local(gpu_base_url()), choices['asr_gpu']),
        'summary': _entry(saved['summary'], llm.TEXT_MODEL_NAME, 'deployment', catalog_info['summary_local'], choices['summary']),
    })


def save(expected, values, choices):
    from src.services import audit
    if not isinstance(values, dict) or set(values) != set(TASKS):
        raise ModelProblem('invalid_model')
    values = {task: model_id(values[task]) for task in TASKS}
    raw, revision, saved = stored()
    if expected != revision:
        raise ModelProblem('stale_revision')
    for task, candidate in values.items():
        if choices[task] and candidate and candidate != saved[task] and candidate not in choices[task]:
            raise ModelProblem('invalid_model')
    from uuid import uuid4
    revision = uuid4().hex
    encoded = json.dumps(dict(revision=revision, models=values), sort_keys=True)
    table = SystemSetting.__table__
    if db.session.query(SystemSetting.id).filter_by(key=KEY).scalar() is None:
        insert = pg_insert if db.engine.dialect.name == 'postgresql' else sqlite_insert
        stmt = insert(table).values(key=KEY, value=encoded, setting_type='string').on_conflict_do_nothing(index_elements=['key'])
    else:
        stmt = update(table).where(table.c.key == KEY, table.c.value == raw).values(value=encoded)
    if db.session.execute(stmt).rowcount != 1:
        db.session.rollback()
        raise ModelProblem('stale_revision')
    audit.setting_changed(KEY, None, None)
    db.session.commit()
    return revision, values
