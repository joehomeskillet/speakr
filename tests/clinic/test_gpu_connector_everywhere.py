"""Every non-upload transcription path reaches the GPU wrapper (WP gpu-connector-everywhere).

The reprocess job (job type reprocess_transcription) runs _run_audio_job ->
transcribe_audio_task -> transcribe_with_connector, which gets its connector
through get_connector(). The module-level transcribe() shortcut and every
create_connector() call use the same factory.
"""

import io
import pathlib
import re

import pytest

from src.clinic.asr_fallback import FallbackTranscriptionConnector
from src.services.transcription import registry as registry_module
from src.services.transcription.base import TranscriptionRequest

from test_asr_fallback import env, stubs  # noqa: F401  (fixtures)


@pytest.fixture
def global_registry(env, stubs):  # noqa: F811
    """Fresh global registry singleton, CPU and GPU stubs wired through the env."""
    cpu, gpu = stubs("cpu"), stubs("gpu")
    env.setenv("TRANSCRIPTION_BASE_URL", cpu.url)
    env.setenv("CLINIC_GPU_ASR_BASE_URL", gpu.url)
    reg = registry_module.get_registry()
    env.setattr(reg, "_active_connector", None)
    env.setattr(reg, "_connector_name", "")
    return cpu, gpu


def _request(model=None):
    return TranscriptionRequest(audio_file=io.BytesIO(b"audio-bytes"), filename="a.wav",
                                mime_type="audio/wav", model=model)


def test_reprocess_path_get_connector_uses_gpu(global_registry):
    cpu, gpu = global_registry
    # transcribe_with_connector() (upload, reprocess, retry) obtains its connector like this.
    from src.services.transcription import get_connector
    connector = get_connector()
    assert isinstance(connector, FallbackTranscriptionConnector)
    # Reprocess passes the model chosen for the recording as a per-request override.
    response = connector.transcribe(_request(model="cpu-only-model"))
    assert response.text.startswith("gpu:")
    assert (gpu.posts, cpu.posts) == (1, 0)


def test_module_level_transcribe_uses_gpu(global_registry):
    cpu, gpu = global_registry
    response = registry_module.transcribe(_request())
    assert response.text.startswith("gpu:")
    assert (gpu.posts, cpu.posts) == (1, 0)


def test_create_connector_wraps_asr_endpoint(env, stubs):  # noqa: F811
    env.setenv("ASR_BASE_URL", "http://127.0.0.1:9")
    env.setenv("CLINIC_GPU_ASR_BASE_URL", stubs("gpu").url)
    reg = registry_module.ConnectorRegistry()
    connector = reg.create_connector("asr_endpoint", reg._build_config_from_env("asr_endpoint"))
    assert isinstance(connector, FallbackTranscriptionConnector)
    assert connector.PROVIDER_NAME == "asr_endpoint"


def test_create_connector_leaves_stub_and_non_clinic_alone(env, stubs):  # noqa: F811
    env.setenv("CLINIC_GPU_ASR_BASE_URL", stubs("gpu").url)
    reg = registry_module.ConnectorRegistry()
    stub = reg.create_connector("clinic_stub", {"profile": "default"})
    assert not isinstance(stub, FallbackTranscriptionConnector)
    env.setenv("CLINIC_MODE", "0")
    whisper = reg.create_connector("openai_whisper", {"api_key": "k", "model": "m"})
    assert not isinstance(whisper, FallbackTranscriptionConnector)


def test_gpu_model_pin_replaces_request_override(env):
    env.setenv("CLINIC_GPU_ASR_MODEL", "gpu-model")
    pinned = FallbackTranscriptionConnector._gpu_request(_request(model="cpu-model"))
    assert pinned.model == "gpu-model"
    env.delenv("CLINIC_GPU_ASR_MODEL")
    kept = FallbackTranscriptionConnector._gpu_request(_request(model="cpu-model"))
    assert kept.model == "cpu-model"


def test_only_the_registry_constructs_connectors():
    """No second construction site that could bypass the wrapper."""
    src = pathlib.Path(registry_module.__file__).resolve().parents[2]
    pattern = re.compile(r"\b(OpenAIWhisperConnector|ASREndpointConnector|ClinicStubTranscriptionConnector)\(")
    allowed = {"asr_fallback.py"}  # builds the GPU target itself
    offenders = []
    for path in src.rglob("*.py"):
        if "connectors" in path.parts or path.name in allowed:
            continue
        if pattern.search(path.read_text(encoding="utf-8")):
            offenders.append(str(path.relative_to(src)))
    assert offenders == []
