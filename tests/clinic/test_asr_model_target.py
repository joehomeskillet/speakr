"""Exercise the real GPU service's HTTP model contract using a synthetic inference engine."""
import io
from threading import Thread
from unittest.mock import patch

import pytest
from werkzeug.serving import make_server

from src.services.transcription.base import TranscriptionRequest
from src.services.transcription.registry import ConnectorRegistry
from src.services.transcription.exceptions import TranscriptionError
from test_asr_fallback import env, stubs  # noqa: F401
from test_gpu_asr_service_contract import make_client, svc  # noqa: F401
from test_wp_asr_engine_wire import PLAN_GPU, _actual, _job, _recording, _run, ctx  # noqa: F401


@pytest.mark.parametrize(("configured_gpu", "job_model"), [
    (None, None),
    (None, "cpu-override"),
    ("üö", "cpu-override"),
    ("whisper-1", "cpu-override"),
    ("whisper-large-v3-turbo-int8-ov", None),
    ("another-valid-model", None),
])
def test_upload_targets_the_single_model_loaded_on_the_gpu(env, stubs, svc, configured_gpu, job_model):
    app, engine = make_client(svc)
    server = make_server("127.0.0.1", 0, app.application)
    thread = Thread(target=server.serve_forever)
    thread.start()
    try:
        cpu = stubs("cpu")
        env.setenv("TRANSCRIPTION_MODEL", "Systran/faster-whisper-large-v3")
        env.setenv("TRANSCRIPTION_BASE_URL", cpu.url)
        env.setenv("CLINIC_GPU_ASR_BASE_URL", f"http://127.0.0.1:{server.server_port}/v1")
        if configured_gpu:
            env.setenv("CLINIC_GPU_ASR_MODEL", configured_gpu)
        connector = ConnectorRegistry().initialize_from_env()
        request = TranscriptionRequest(audio_file=io.BytesIO(b"synthetic-audio"), filename="a.wav",
                                       mime_type="audio/wav", model=job_model)
        if configured_gpu == "another-valid-model":
            with pytest.raises(TranscriptionError) as failure:
                connector.transcribe(request)
            assert failure.value.__cause__.status_code == 404
            assert len(engine.calls) == 0 and cpu.posts == 0
            return
        response = connector.transcribe(request)
        assert response.text == "Guten Tag"
        assert response.engine_actual == "gpu"
        assert len(engine.calls) == 1 and cpu.posts == 0
        assert request.model == job_model
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_failed_gpu_health_transcribes_the_same_job_on_cpu(ctx, env, stubs):
    gpu, cpu = stubs("gpu", health=503), stubs("cpu")
    env.setenv("TRANSCRIPTION_BASE_URL", cpu.url)
    env.setenv("TRANSCRIPTION_MODEL", "Systran/faster-whisper-large-v3")
    env.setenv("CLINIC_GPU_ASR_BASE_URL", gpu.url)
    env.setenv("CLINIC_GPU_ASR_MODEL", "whisper-large-v3-turbo-int8-ov")
    recording = _recording(ctx)
    job = _job(recording, {"clinic_asr": {"engine": PLAN_GPU}})
    connector = ConnectorRegistry().initialize_from_env()
    with patch.object(connector._cpu, "transcribe", wraps=connector._cpu.transcribe) as cpu_call:
        assert _run(recording, connector, job, transcription_model="Systran/faster-whisper-large-v3") is None
    assert cpu_call.call_args.args[0].model == "Systran/faster-whisper-large-v3"
    assert (gpu.posts, cpu.posts) == (0, 1)
    assert _actual(job) == "cpu"
    assert "Guten Tag" in recording.transcription
