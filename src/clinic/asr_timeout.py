"""Request timeout and SDK retries for the OpenAI-compatible ASR connector.

The OpenAI SDK defaults (600 s read timeout, 2 internal retries) abort long CPU
transcriptions and then re-send them, so the aborted requests keep loading the
ASR server. TRANSCRIPTION_TIMEOUT (seconds) and TRANSCRIPTION_MAX_RETRIES
override them. Unset with clinic mode off: nothing is passed, upstream
behaviour. Unset with clinic mode on: 3600 s and 0 retries.
"""

from __future__ import annotations

import os
from typing import Any

from .config import clinic_mode_enabled

TIMEOUT_ENV = 'TRANSCRIPTION_TIMEOUT'
MAX_RETRIES_ENV = 'TRANSCRIPTION_MAX_RETRIES'
CLINIC_DEFAULT_TIMEOUT = 3600.0
CLINIC_DEFAULT_MAX_RETRIES = 0


def _read(name: str, parse, check, expect: str, default):
    raw = os.environ.get(name, '').strip()
    if not raw:
        return default
    try:
        value = parse(raw)
    except ValueError:
        value = None
    if value is None or not check(value):
        raise ValueError(f'{name}={raw!r} is invalid: expected {expect}')
    return value


def client_options() -> dict[str, Any]:
    """Keyword arguments for OpenAI(...): empty, or timeout and/or max_retries.

    Raises ValueError with the variable name for a value that is not a
    positive number of seconds / a non-negative integer.
    """
    clinic = clinic_mode_enabled()
    timeout = _read(TIMEOUT_ENV, float, lambda v: 0 < v < float('inf'),
                    'a positive number of seconds', CLINIC_DEFAULT_TIMEOUT if clinic else None)
    retries = _read(MAX_RETRIES_ENV, int, lambda v: v >= 0,
                    'a non-negative integer', CLINIC_DEFAULT_MAX_RETRIES if clinic else None)
    options: dict[str, Any] = {}
    if timeout is not None:
        options['timeout'] = timeout
    if retries is not None:
        options['max_retries'] = retries
    return options
