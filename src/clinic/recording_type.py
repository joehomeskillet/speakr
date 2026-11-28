"""Fixed clinic recording templates and their transcription parameters."""

REPORT = 'bericht'
SPEAKER_RANGES = {
    REPORT: (1, 1),
    'notiz': (1, 1),
    'sitzung': (2, 8),
    'patientengespraech': (2, 3),
}


def requires_diarization(kind: str) -> bool:
    return SPEAKER_RANGES[kind][0] > 1


def transcription_params(kind: str) -> dict:
    minimum, maximum = SPEAKER_RANGES[kind]
    return {'min_speakers': minimum, 'max_speakers': maximum,
            'diarize': requires_diarization(kind)}
