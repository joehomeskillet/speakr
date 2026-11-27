# Operate local speech recognition

[Deutsch](../de/admin-guide/whisperx-setup.md)

The clinic fork uses local speech-recognition services. Cloud ASR connectors and cloud fallback are not offered. Run the clinic application with `CLINIC_MODE=1`; the local-address checks described here depend on clinic mode.

The registered connectors are `openai_whisper` for an OpenAI-compatible local service such as Speaches, `asr_endpoint` for a self-hosted `/asr` service, and `clinic_stub` for synthetic tests. `TRANSCRIPTION_CONNECTOR` selects one explicitly. Otherwise `ASR_BASE_URL` selects `asr_endpoint`; without it, the default is `openai_whisper`. The legacy `USE_ASR_ENDPOINT=true` also selects `asr_endpoint`, with a deprecation warning.

## Local CPU connector

Use a locally operated OpenAI-compatible endpoint. For example:

```bash
CLINIC_MODE=1
TRANSCRIPTION_CONNECTOR=openai_whisper
TRANSCRIPTION_BASE_URL=http://speaches:8000/v1
TRANSCRIPTION_API_KEY=not-needed-for-local
TRANSCRIPTION_MODEL=your-local-model
```

Replace the model identifier with one the service actually serves. The non-empty key is required by the connector; a placeholder is suitable only for a local service that does not require authentication. This example does not install or start Speaches.

In clinic mode, an empty or non-local `TRANSCRIPTION_BASE_URL` is refused. The validator accepts loopback, private and link-local addresses, single-label service names, and names ending in `.local`, `.internal`, `.lan` or `.localhost`. Use an approved local target and verify its actual routing. The text-model locality rule is stricter; see [Model configuration](model-configuration.md).

The connector's advertised file limit is 25 MiB. App-level chunking handles larger audio when enabled. `ENABLE_CHUNKING` defaults to `true`; `CHUNK_LIMIT` and `CHUNK_OVERLAP_SECONDS` govern the application's chunks. These are processing chunks, not browser upload sessions.

## Optional GPU target with CPU fallback

Setting `CLINIC_GPU_ASR_BASE_URL` in clinic mode wraps the configured CPU connector. The synthetic connector is excluded. The GPU target must be a local OpenAI-compatible service:

```bash
CLINIC_GPU_ASR_BASE_URL=http://gpu-asr:8000/v1
CLINIC_GPU_ASR_TIMEOUT=300
```

`CLINIC_GPU_ASR_API_KEY` falls back to the CPU key, then to `local`. For each request or audio chunk:

1. Speakr checks the GPU service's root `/healthz`, with a 5-second timeout.
2. A healthy target gets one GPU attempt, with no SDK retry.
3. An unhealthy or unreachable health endpoint, a GPU connection error, timeout or HTTP 5xx triggers one CPU attempt.
4. A CPU failure ends that attempt. Other GPU errors, including HTTP 4xx such as `model_not_found`, do not trigger CPU fallback.

The job queue can retry a transiently failed job; each new attempt starts at the GPU check again. A fallback does not mean that the whole job can run only once.

The bundled optional service is under `deployment/gpu-asr-service`. Its Compose snippet exposes port 8000, passes `/dev/dri`, and selects `GPU.0`. It is not the default ASR deployment. Its `/healthz` identifies the loaded model; the fallback can use that name when no explicit GPU model is set. The bundled service accepts its served model name and `whisper-1`.

Use **Verwaltung → System → Modellwahl** to set **GPU-Modell** and **CPU-Modell**. These controls are present in this build. A valid saved GPU override takes precedence over `CLINIC_GPU_ASR_MODEL` and the service-reported model. Selecting a name does not load a different model into the service. See [Model configuration](model-configuration.md) for catalog checks, inactive services and saved overrides.

## Timeouts and retries

| Path | Setting | Clinic default |
|---|---|---|
| OpenAI-compatible CPU connector | `TRANSCRIPTION_TIMEOUT` | `3600` seconds; must be positive |
| OpenAI-compatible CPU connector | `TRANSCRIPTION_MAX_RETRIES` | `0`; non-negative SDK retry count |
| GPU target | `CLINIC_GPU_ASR_TIMEOUT` | `300` seconds; no SDK retries |
| GPU health check | Fixed | `5` seconds |
| `asr_endpoint` | `ASR_TIMEOUT`, legacy `asr_timeout_seconds`, then system setting `asr_timeout_seconds` | `1800` seconds |
| Processing queue | `JOB_MAX_RETRIES` | `3` total attempts for transient failures |

The system setting `asr_timeout_seconds` does not control Speaches through `openai_whisper` or the GPU target. `TRANSCRIPTION_MAX_RETRIES=0` prevents SDK retries, not processing-queue retries. Account for the whole retry budget when sizing processing capacity.

Deployment addresses, keys and connector configuration are loaded when the connector is constructed. Apply such changes through the deployment's restart procedure. The `asr_endpoint` timeout is read again for requests. Model identifiers can be changed through **Modellwahl** without changing service addresses.

## Local WhisperX or whisper-asr-webservice

`asr_endpoint` uses `ASR_BASE_URL` and optional `ASR_API_KEY`. **The connector does not enforce locality of `ASR_BASE_URL`.** Point it only at an approved local service; the lack of this check is not permission to use cloud ASR.

- With `ASR_ASYNC_JOBS=true` (default), or the corresponding system setting `asr_async_jobs`, a service advertising the jobs API can receive an asynchronous job that Speakr polls. Otherwise the connector uses synchronous `/asr`.
- `ASR_ENABLE_CHUNKING` defaults to `false`; `ASR_MAX_DURATION_SECONDS` defaults to `7200` for that connector's app-level chunking.
- `ASR_DIARIZE` defaults to `true`; set it to `false` if diarization is not intended. The clinic contract's `asr.diarization` field does not control this connector.
- Clinic mode does not retain diarized speakers' voice embeddings as upstream speaker profiles. Speaker identification is outside the default clinic workflow.

## Recognition hints, timestamps and training limits

Clinic transcription hints are off by default. `CLINIC_ASR_PROMPTS_ENABLED` allows initial prompts and hotwords through with a shared limit of 55 words. The bundled GPU service has its own `ASR_PROMPTS_ENABLED` switch, also off by default; both switches must allow hints for that GPU engine to receive them. See [Prompts](prompts.md).

Hotwords, vocabulary and profile settings are decoder hints, not acoustic training. Per-author profile application depends on matching runtime capability evidence. Creating an optimization run does not prove adaptation: the processing queue currently refuses `asr_training` with `CAPABILITY_UNAVAILABLE`. Do not describe word-list storage or synthetic tests as improved clinical recognition.

`TRANSCRIPTION_WORD_TIMESTAMPS` requests word timestamps when enabled in clinic mode. Confirm that the chosen local service returns actual word timing, then compare several highlighted words with playback. Segment start times alone cannot supply word-level alignment.

**Erneut transkribieren** (Repeat transcription) in **Schreibplatz** needs the edit right and current edit lease. The result is checked against its source revisions; later human corrections must not be overwritten by an outdated ASR result. See the [user workflow](../user-guide/recording.md).

The separate local speaker service is optional for consented own-voice enrollment. It is configured with `CLINIC_SPEAKER_SERVICE_URL`; the client does not check that URL for locality, so operations must keep it local. Its diarization helper is not connected to the processing pipeline. See [Privacy](privacy.md) for consent and revocation. The live-ASR adapter is also not connected to the shipped live page; see [Recording sessions](recording-sessions.md).

## Verify operation and diagnose failures

1. Upload a short neutral speech sample and wait for its processing result.
2. Listen to the audio and compare the transcript. Check word timing separately if required.
3. When GPU fallback is configured, inspect `/healthz` and application log lines beginning `ASR fallback:`. Keep the CPU target functional.
4. Inspect **Verwaltung → Betrieb → Verarbeitung** for failed or stuck jobs. An active site administrator can retry failed jobs; do not infer recognition quality from a green job status.

| Symptom | Check |
|---|---|
| Local-address configuration error | `TRANSCRIPTION_BASE_URL` and `CLINIC_GPU_ASR_BASE_URL` |
| Unknown connector | Only the three registered connectors above are supported |
| GPU `model_not_found` | Saved GPU override, `CLINIC_GPU_ASR_MODEL` and the served model name; this error does not fall back |
| Frequent CPU fallbacks | GPU health, connectivity, timeout and service log |
| Hints have no effect | Both hint switches and actual engine support; a saved word list is not training |
| `.ds2` or `.dss` decode failure | `CLINIC_DSS_CODEC_BIN`; the Docker image uses `/usr/local/bin/dss-decode` |

`clinic_stub` is an offline synthetic test double, not speech recognition. It returns fixed text and refuses `CLINIC_PROFILE=production`. No simulator, health response or synthetic test establishes German clinical recognition quality or Philips-device compatibility.
