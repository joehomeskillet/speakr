"""x26: per-call engine.actual and W9 prompts/hotwords OFF on every clinic path.

Stub connectors only. Not GPU evidence.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.clinic.asr_fallback import FallbackTranscriptionConnector
from src.services.transcription.base import TranscriptionResponse

from tests.clinic.test_wp_asr_engine_wire import (  # noqa: F401  (fixtures and helpers)
    ADMIN, PLAN_GPU, _actual, _connector, _job, _recording, _request, _run, ctx,
)


class _Stub:
    CAPABILITIES = 0
    PROVIDER_NAME = "stub"
    SPECIFICATIONS = None
    SUPPORTS_TASK_RESUME = False

    def __init__(self, text, gate=None, entered=None):
        self.config = {"base_url": "http://stub.test"}
        self._text, self._gate, self._entered = text, gate, entered

    def transcribe(self, request):
        if self._entered is not None:
            self._entered.set()
        if self._gate is not None:
            assert self._gate.wait(5)
        return TranscriptionResponse(text=self._text)


def test_two_concurrent_jobs_on_one_connector_keep_their_own_engine():
    gate_gpu, gate_cpu = threading.Event(), threading.Event()
    in_gpu, in_cpu = threading.Event(), threading.Event()
    gpu = _Stub("gpu", gate_gpu, in_gpu)
    cpu = _Stub("cpu", gate_cpu, in_cpu)
    shared = FallbackTranscriptionConnector(cpu, gpu, "http://gpu.test")
    results = {}

    def run(name, healthy):
        with patch.object(FallbackTranscriptionConnector, "_gpu_healthy", return_value=healthy):
            results[name] = shared.transcribe(SimpleNamespace(audio_file=None, model=None))

    # Job A is served by the GPU, job B (unhealthy GPU) by the CPU fallback.
    # B finishes first and A last, so shared state would show B's engine to A.
    thread_a = threading.Thread(target=run, args=("a", None))
    thread_a.start()
    assert in_gpu.wait(5)
    thread_b = threading.Thread(target=run, args=("b", "unhealthy: test"))
    thread_b.start()
    assert in_cpu.wait(5)
    gate_cpu.set()
    thread_b.join(5)
    gate_gpu.set()
    thread_a.join(5)
    assert results["a"].engine_actual == "gpu"
    assert results["b"].engine_actual == "cpu"


def test_job_records_the_engine_of_its_own_response_not_the_connector_state(ctx):
    rec = _recording(ctx)
    job = _job(rec, {"clinic_asr": {"engine": PLAN_GPU}})
    connector = _connector(last_engine="gpu")  # stale state from another job
    connector.transcribe.return_value.engine_actual = "cpu"
    _run(rec, connector, job)
    assert _actual(job) == "cpu"


def test_chunked_run_with_mixed_engines_records_none():
    from src.tasks.processing import _single_engine
    assert _single_engine(["gpu", "gpu"]) == "gpu"
    assert _single_engine(["gpu", "cpu"]) is None
    assert _single_engine([None]) is None


def test_clinic_mode_without_job_record_sends_no_admin_prompt_or_hotwords(ctx):
    rec = _recording(ctx)
    connector = _connector()
    _run(rec, connector, None, hotwords="Eigen", initial_prompt="Eigen")
    request = _request(connector)
    assert request.prompt is None and request.hotwords is None
    assert ADMIN["admin_default_hotwords"] not in str(request)


def test_clinic_mode_off_keeps_admin_defaults(ctx, monkeypatch):
    monkeypatch.delenv("CLINIC_MODE")
    rec = _recording(ctx)
    connector = _connector()
    _run(rec, connector)
    request = _request(connector)
    assert request.prompt == "Admin Prompt" and request.hotwords == "AdminWort"
