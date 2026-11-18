"""Short LLM call limits and the title/summary hand-off (CLINIC_MODE only).

On staging a local LLM stopped answering; the upstream client waits 600 s per
attempt and retries, so both transcription workers sat in title/summary calls
for over 15 minutes and no new recording was picked up. Here a call times out
fast with few retries, and a transcription job never calls the LLM itself: it
enqueues a follow-up job on the summary queue.
"""
from __future__ import annotations

import os

DEFAULT_TIMEOUT_SECONDS = 120
DEFAULT_MAX_RETRIES = 2


def _number(name: str, default, cast):
    try:
        return cast(os.environ.get(name, '').strip() or default)
    except ValueError:
        return default


def call_options() -> dict:
    """Per-call ``with_options`` arguments of the OpenAI client (retries back off in the SDK)."""
    return {
        'timeout': _number('CLINIC_LLM_TIMEOUT_SECONDS', DEFAULT_TIMEOUT_SECONDS, float),
        'max_retries': _number('CLINIC_LLM_MAX_RETRIES', DEFAULT_MAX_RETRIES, int),
    }


def limited(client):
    """The client with the clinic limits, or the client itself outside CLINIC_MODE."""
    from src.clinic.config import clinic_mode_enabled
    return client.with_options(**call_options()) if clinic_mode_enabled() else client
