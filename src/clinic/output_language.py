"""Default output language for LLM text (summary, title, chat).

In clinic mode the text is German unless the user chose another output
language. With clinic mode off the value passes through unchanged, so upstream
prompts stay byte-identical.
"""

from __future__ import annotations

from src.clinic.config import clinic_mode_enabled

CLINIC_DEFAULT_OUTPUT_LANGUAGE = 'German'


def effective_output_language(user_output_language):
    """Return the user's output language; in clinic mode fall back to German."""
    if user_output_language:
        return user_output_language
    if clinic_mode_enabled():
        return CLINIC_DEFAULT_OUTPUT_LANGUAGE
    return user_output_language
