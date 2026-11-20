"""The registry's GPU job path must preserve timed words from verbose_json."""
import threading

import pytest
from flask import request
from werkzeug.serving import make_server

from test_asr_fallback import env, run, stubs  # noqa: F401
from test_gpu_asr_service_contract import make_client, svc  # noqa: F401


@pytest.mark.parametrize("base_kind", ["openai_whisper", "asr_endpoint"])
@pytest.mark.parametrize("enabled", [True, False])
def test_gpu_requests_and_retains_only_enabled_word_times(env, stubs, svc, enabled, base_kind):
    client, engine = make_client(svc)
    app = client.application
    granularities = []

    @app.before_request
    def capture_request():
        if request.method == "POST":
            granularities.extend(request.form.getlist("timestamp_granularities[]"))

    server = make_server("127.0.0.1", 0, app)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    cpu = stubs("cpu")
    gpu_url = f"http://127.0.0.1:{server.server_port}/v1"
    env.setenv("CLINIC_GPU_ASR_BASE_URL", gpu_url)
    env.setenv("CLINIC_GPU_ASR_MODEL", "whisper-large-v3-turbo-int8-ov")
    env.setenv("TRANSCRIPTION_CONNECTOR", base_kind)
    env.setenv("ASR_BASE_URL", cpu.url)
    env.setenv("TRANSCRIPTION_WORD_TIMESTAMPS", "true" if enabled else "false")
    try:
        _, result = run(env, cpu)
    finally:
        server.shutdown()
        worker.join()
        server.server_close()
    assert result.engine_actual == "gpu" and cpu.posts == 0 and len(engine.calls) == 1
    assert granularities == (["segment", "word"] if enabled else ["segment"])
    words = result.segments[0].words
    if enabled:
        assert [(w["word"], w["start"], w["end"]) for w in words] == [
            ("Guten", 0.0, 0.7), ("Tag", 0.8, 1.5),
        ]
    else:
        assert words is None
