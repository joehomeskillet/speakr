"""Fixed clinic recording templates and their transcription parameters."""

REPORT = 'bericht'
SPEAKER_RANGES = {
    REPORT: (1, 1),
    'notiz': (1, 1),
    'sitzung': (2, 8),
    'patientengespraech': (2, 3),
}
PRIVATE_TYPES = tuple(kind for kind in SPEAKER_RANGES if kind != REPORT)
PRIVATE_WEB_SOURCE = 'clinic_private_upload'


def purge_guard_sql(dialect: str, dictation_id: str) -> str:
    """Trigger predicate for the only permitted deletion of immutable private content."""
    kinds = ', '.join("'" + kind + "'" for kind in PRIVATE_TYPES)
    deadline = ("julianday(private_purge_at) <= julianday('now')" if dialect == 'sqlite'
                else "private_purge_at <= (CURRENT_TIMESTAMP AT TIME ZONE 'UTC')")
    return (f'EXISTS (SELECT 1 FROM clinic_dictation WHERE id = {dictation_id} '
            f'AND recording_type IN ({kinds}) AND {deadline})')


def is_private_source(recording):
    """The worker recording of a private upload is protected before the clinic link exists."""
    return recording.processing_source == PRIVATE_WEB_SOURCE


def requires_diarization(kind: str) -> bool:
    return SPEAKER_RANGES[kind][0] > 1


def transcription_params(kind: str) -> dict:
    minimum, maximum = SPEAKER_RANGES[kind]
    return {'min_speakers': minimum, 'max_speakers': maximum,
            'diarize': requires_diarization(kind)}
