"""Clinic mode keeps upstream voice-embedding writers inert.

Voice embeddings of diarized speakers (patients included) are never stored
without the user's own consent; that path is voice_consent.enrol. Existing
rows and files are not touched.
"""

from src.clinic.config import clinic_mode_enabled


def diarized_embeddings_to_store(embeddings):
    """What the diarized ASR run may keep in recording.speaker_embeddings."""
    if clinic_mode_enabled():
        return None
    return embeddings or None
