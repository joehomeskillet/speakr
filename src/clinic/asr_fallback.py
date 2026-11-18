"""GPU first, CPU second: automatic ASR fallback for clinic mode.

CLINIC_GPU_ASR_BASE_URL names an OpenAI-compatible GPU ASR service. When it is
set and clinic mode is on, the registry wraps the configured connector (the
Speaches/CPU target) in FallbackTranscriptionConnector. A job goes to the GPU
target first. On a connection error, a timeout, an HTTP 5xx answer, or an
unhealthy /healthz the same job runs once on the CPU target, with one log line
that names the reason and the target. There is no retry on the GPU target and
no second fallback: a CPU failure is the job's failure.

Both targets are OpenAIWhisperConnector instances, so the verbose_json and
segment handling is the same code for both.
"""

from __future__ import annotations

import dataclasses
import logging
import os
from typing import Any, Optional
from urllib.parse import urlsplit

import httpx
import openai

from src.services.transcription.base import (
    BaseTranscriptionConnector,
    TranscriptionRequest,
    TranscriptionResponse,
)
from src.services.transcription.exceptions import TranscriptionError

from .config import clinic_mode_enabled

logger = logging.getLogger(__name__)

GPU_URL_ENV = 'CLINIC_GPU_ASR_BASE_URL'
GPU_TIMEOUT_ENV = 'CLINIC_GPU_ASR_TIMEOUT'
GPU_KEY_ENV = 'CLINIC_GPU_ASR_API_KEY'
GPU_MODEL_ENV = 'CLINIC_GPU_ASR_MODEL'
DEFAULT_GPU_TIMEOUT = 300.0
HEALTH_TIMEOUT = 5.0


def gpu_base_url() -> str:
    """The GPU target URL, text after # dropped; empty when not configured."""
    return os.environ.get(GPU_URL_ENV, '').split('#')[0].strip()


def gpu_timeout() -> float:
    raw = os.environ.get(GPU_TIMEOUT_ENV, '').strip()
    try:
        value = float(raw) if raw else DEFAULT_GPU_TIMEOUT
    except ValueError:
        return DEFAULT_GPU_TIMEOUT
    return value if value > 0 else DEFAULT_GPU_TIMEOUT


def fallback_enabled() -> bool:
    return clinic_mode_enabled() and bool(gpu_base_url())


def health_url(base_url: str) -> str:
    """/healthz at the root of the service, whatever path the base URL carries."""
    parts = urlsplit(base_url if '://' in base_url else f'http://{base_url}')
    return f'{parts.scheme}://{parts.netloc}/healthz'


def _fallback_reason(error: BaseException) -> Optional[str]:
    """Why a GPU failure may fall back to the CPU; None for any other failure."""
    cause = error.__cause__ or error
    if isinstance(cause, openai.APITimeoutError):
        return 'timeout'
    if isinstance(cause, openai.APIConnectionError):
        return 'connection error'
    if isinstance(cause, openai.APIStatusError) and cause.status_code >= 500:
        return f'HTTP {cause.status_code}'
    return None


class FallbackTranscriptionConnector(BaseTranscriptionConnector):
    """Runs a request on the GPU connector and falls back once to the CPU one."""

    def __init__(self, cpu: BaseTranscriptionConnector, gpu: BaseTranscriptionConnector,
                 gpu_url: str, cpu_url: str = ''):
        self._cpu = cpu
        self._gpu = gpu
        self._gpu_url = gpu_url
        self._cpu_url = cpu_url or str(getattr(cpu, 'config', {}).get('base_url') or 'cpu')
        self.config = cpu.config
        self.CAPABILITIES = cpu.CAPABILITIES
        self.PROVIDER_NAME = cpu.PROVIDER_NAME
        self.SPECIFICATIONS = cpu.SPECIFICATIONS
        self.SUPPORTS_TASK_RESUME = cpu.SUPPORTS_TASK_RESUME

    def __getattr__(self, name: str) -> Any:
        # Only reached for names not set above: timeout, model, client, ...
        if name in ('_cpu', '_gpu'):
            raise AttributeError(name)
        return getattr(self._cpu, name)

    def _validate_config(self) -> None:
        return None

    def _gpu_healthy(self) -> Optional[str]:
        """None when /healthz answers 2xx, else the reason it does not."""
        try:
            response = httpx.get(health_url(self._gpu_url), timeout=HEALTH_TIMEOUT)
        except httpx.TimeoutException:
            return 'unhealthy: /healthz timeout'
        except httpx.HTTPError as exc:
            return f'unhealthy: /healthz unreachable ({type(exc).__name__})'
        if response.status_code >= 300:
            return f'unhealthy: /healthz HTTP {response.status_code}'
        return None

    def _log_fallback(self, reason: str) -> None:
        logger.warning('ASR fallback: GPU target %s not used (%s); transcribing on CPU target %s',
                       self._gpu_url, reason, self._cpu_url)

    @staticmethod
    def _rewind(request: TranscriptionRequest) -> None:
        try:
            request.audio_file.seek(0)
        except (AttributeError, OSError, ValueError):
            pass

    @staticmethod
    def _gpu_request(request: TranscriptionRequest) -> TranscriptionRequest:
        """A per-request model override (reprocess) names a CPU model; the GPU
        target keeps CLINIC_GPU_ASR_MODEL when that is set."""
        model = os.environ.get(GPU_MODEL_ENV, '').strip()
        if model and request.model:
            return dataclasses.replace(request, model=model)
        return request

    def transcribe(self, request: TranscriptionRequest) -> TranscriptionResponse:
        reason = self._gpu_healthy()
        if reason is None:
            logger.info('ASR: sending job to GPU target %s', self._gpu_url)
            try:
                return self._gpu.transcribe(self._gpu_request(request))
            except TranscriptionError as exc:
                reason = _fallback_reason(exc)
                if reason is None:
                    raise
        self._log_fallback(reason)
        self._rewind(request)
        return self._cpu.transcribe(request)

    def list_models(self):
        return self._cpu.list_models()

    def health_check(self) -> bool:
        return self._cpu.health_check()

    @classmethod
    def get_config_schema(cls) -> dict:
        return {}


def wrap_with_gpu_fallback(cpu: BaseTranscriptionConnector) -> BaseTranscriptionConnector:
    """Wrap the OpenAI-compatible CPU connector when a GPU target is configured.

    Every connector type is wrapped (openai_whisper and asr_endpoint), except
    the clinic_stub test double and an already wrapped connector. Without
    clinic mode or without CLINIC_GPU_ASR_BASE_URL it is returned unchanged.
    """
    from src.services.transcription.connectors.clinic_stub import ClinicStubTranscriptionConnector
    from src.services.transcription.connectors.openai_whisper import OpenAIWhisperConnector

    if (not fallback_enabled() or isinstance(cpu, (FallbackTranscriptionConnector, ClinicStubTranscriptionConnector))):
        return cpu
    url = gpu_base_url()
    timeout = gpu_timeout()
    gpu = OpenAIWhisperConnector({
        'api_key': os.environ.get(GPU_KEY_ENV, '').strip() or cpu.config.get('api_key') or 'local',
        'base_url': url,
        'model': os.environ.get(GPU_MODEL_ENV, '').strip() or cpu.config.get('model', 'whisper-1'),
    })
    # One attempt only: the fallback is the retry.
    gpu.client = gpu.client.with_options(max_retries=0, timeout=timeout)
    logger.info('ASR GPU target %s configured (timeout %ss); CPU target stays the fallback', url, timeout)
    return FallbackTranscriptionConnector(cpu, gpu, url)
