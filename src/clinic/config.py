"""Clinic mode switch, contract loaders and feature locks.

Clinic mode is off unless CLINIC_MODE is a true value. With it off every
function here answers "nothing is locked", so upstream behaviour is unchanged.
With it on, features are denied by default: a feature is usable only when the
dev/production defaults name it and set it to ``true``.

The contract files (dev defaults, client capabilities, import-agent boundary)
are read from clinic-spec/config next to the repository root, or from the
directory in CLINIC_CONFIG_DIR. A missing file raises: clinic mode must not
start with an unknown policy.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

_TRUE = ('1', 'true', 'yes', 'on')

DEV_DEFAULTS_FILE = 'dev.defaults.json'
CLIENT_CAPABILITIES_FILE = 'client-capabilities.json'
IMPORT_AGENT_BOUNDARY_FILE = 'import-agent-boundary.json'

FALLBACK_CLIENT_PROFILE = 'managed_web_online'

# Upstream System Settings that the clinic policy caps. Value: (locking
# feature, value that is forced while the feature is locked). The ceiling is
# applied where settings are read (SystemSetting.get_setting), so an admin
# change through the stock settings API cannot re-enable the function (R-050).
SETTING_CEILINGS: dict[str, tuple[str, Any]] = {
    'enable_public_sharing': ('public_sharing', False),
    'enable_internal_sharing': ('internal_sharing', False),
    'api_query_tokens_enabled': ('token_query_parameters', False),
    'sso_api_tokens_enabled': ('personal_api_tokens', False),
    'enable_auto_deletion': ('auto_deletion', False),
    'global_retention_days': ('auto_deletion', 0),
}


def clinic_mode_enabled() -> bool:
    """True when CLINIC_MODE is on. Read on every call, never cached."""
    return os.environ.get('CLINIC_MODE', '').strip().lower() in _TRUE


def config_dir() -> Path:
    override = os.environ.get('CLINIC_CONFIG_DIR', '').strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[2] / 'clinic-spec' / 'config'


@lru_cache(maxsize=8)
def _load(directory: str, name: str) -> dict[str, Any]:
    with open(Path(directory) / name, encoding='utf-8') as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f'{name}: top level must be a JSON object')
    return data


def _contract(name: str) -> dict[str, Any]:
    return _load(str(config_dir()), name)


def dev_defaults() -> dict[str, Any]:
    return _contract(DEV_DEFAULTS_FILE)


def client_capabilities() -> dict[str, Any]:
    return _contract(CLIENT_CAPABILITIES_FILE)


def import_agent_boundary() -> dict[str, Any]:
    return _contract(IMPORT_AGENT_BOUNDARY_FILE)


def default_client_profile() -> str:
    """The default client profile, validated against the capability contract."""
    name = (dev_defaults().get('web_client') or {}).get('default_profile') or FALLBACK_CLIENT_PROFILE
    if name not in (client_capabilities().get('profiles') or {}):
        raise ValueError(f'unknown default client profile {name!r}')
    return name


def client_profile() -> str:
    """The client profile of the current request.

    The channel is a server decision (R-048). Nothing the client sends, not an
    X-Clinic-Channel header, a User-Agent or a Host, selects a profile; until a
    trusted ingress mapping exists every request gets the default profile.
    """
    return default_client_profile()


# Features that stay available, but only against a local text model (owner
# decision of 2026-11-17). They do not depend on the defaults flag.
LOCAL_MODEL_FEATURES = frozenset({'chat', 'inquire', 'generative_summary'})

_LOCAL_NETWORKS = tuple(ipaddress.ip_network(n) for n in (
    '127.0.0.0/8', '::1/128', '10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', 'fc00::/7',
))
DEFAULT_LOCAL_MODEL_HOSTS = ('speaches', 'host.docker.internal', 'localhost')
# The text model is required; the others fall back to it (chat, translation) or
# are optional (embedding service) and only count when they are set.
REQUIRED_MODEL_URL = 'TEXT_MODEL_BASE_URL'
OPTIONAL_MODEL_URLS = ('CHAT_MODEL_BASE_URL', 'TRANSLATION_MODEL_BASE_URL', 'EMBEDDING_BASE_URL')
_FORBIDDEN_URL_CHARS = re.compile(r'[\\\s\x00-\x1f\x7f@]')


def local_model_hosts() -> frozenset[str]:
    """Service names that count as local: CLINIC_LOCAL_MODEL_HOSTS (comma
    separated) or the default list. Loopback and private addresses always do."""
    raw = os.environ.get('CLINIC_LOCAL_MODEL_HOSTS')
    names = DEFAULT_LOCAL_MODEL_HOSTS if raw is None else raw.split(',')
    return frozenset(n.strip().lower() for n in names if n.strip())


def is_local_model_url(url: str | None) -> bool:
    """True when the URL names a loopback or private address, or a service name
    on the local-host list (local_model_hosts).

    A bare single-label name such as ai is NOT local: it may resolve to a
    public host through a search domain. A URL with a backslash, whitespace, a
    control character or userinfo is refused, because clients and parsers
    disagree on where its host ends. Empty, unparsable and public hosts are not
    local.
    """
    if not url or not url.strip():
        return False
    candidate = url.strip()
    if _FORBIDDEN_URL_CHARS.search(candidate):
        return False
    if '://' not in candidate:
        candidate = f'//{candidate}'
    try:
        parts = urlsplit(candidate)
        host = parts.hostname
        _ = parts.port  # raises ValueError for a port that is not a number
    except ValueError:
        return False
    if parts.scheme not in ('', 'http', 'https') or not host:
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host.lower() in local_model_hosts()
    return any(address in network for network in _LOCAL_NETWORKS)


def _model_url(name: str) -> str:
    """The variable as the LLM client reads it (src/services/llm.py): text after # is dropped."""
    return os.environ.get(name, '').split('#')[0].strip()


def text_model_is_local() -> bool:
    """True when every text, chat, translation and embedding endpoint is local.

    A transcript goes to any of them (summary, chat, translation, inquire
    chunks), so one public URL makes the whole set non-local.
    """
    if not is_local_model_url(_model_url(REQUIRED_MODEL_URL)):
        return False
    return all(is_local_model_url(_model_url(n)) for n in OPTIONAL_MODEL_URLS if _model_url(n))


def feature_locked(name: str) -> bool:
    """True when clinic mode is on and the feature is not explicitly enabled.

    Deny by default: a name the defaults do not know is locked. Chat, inquire
    and generative summary are locked unless the text model is local.
    """
    if not clinic_mode_enabled():
        return False
    if name in LOCAL_MODEL_FEATURES:
        return not text_model_is_local()
    return (dev_defaults().get('features') or {}).get(name) is not True


def setting_ceiling(key: str) -> tuple[bool, Any]:
    """(locked, forced value) for an upstream System Setting key."""
    ceiling = SETTING_CEILINGS.get(key)
    if ceiling is None or not feature_locked(ceiling[0]):
        return False, None
    return True, ceiling[1]


def find_setting_conflicts() -> list[dict[str, Any]]:
    """Stored System Settings that the clinic ceiling overrides (startup check).

    Needs an app context. Empty list when clinic mode is off.
    """
    if not clinic_mode_enabled():
        return []
    from src.models import SystemSetting

    conflicts = []
    for key in SETTING_CEILINGS:
        locked, forced = setting_ceiling(key)
        if not locked:
            continue
        row = SystemSetting.query.filter_by(key=key).first()
        if row is None or row.value is None:
            continue
        stored = row.value.strip().lower()
        if stored != str(forced).lower():
            conflicts.append({'key': key, 'stored': row.value, 'effective': forced})
    return conflicts
