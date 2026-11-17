"""
Transcription connector implementations for local clinic ASR.
"""

from .openai_whisper import OpenAIWhisperConnector
from .asr_endpoint import ASREndpointConnector
from .clinic_stub import ClinicStubTranscriptionConnector

__all__ = [
    'OpenAIWhisperConnector',
    'ASREndpointConnector',
    'ClinicStubTranscriptionConnector',
]
