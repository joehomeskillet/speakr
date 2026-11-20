# Connecting a local recognition service

[Deutsch](../de/advanced/custom-connectors.md)

The [connector interface](https://github.com/joehomeskillet/speakr/blob/main/src/services/transcription/base.py) transports audio and returns text, segments and any supported timing data. Select the connector that matches the API served by the model. The name `openai_whisper` describes a protocol here; clinic processing requires a local/private destination.

| Server API | Application configuration | First test |
|---|---|---|
| Multipart `POST /asr` | `TRANSCRIPTION_CONNECTOR=asr_endpoint`, `ASR_BASE_URL` set to the private service root | Upload a short WAV. Check the server returns a transcript and the worklist marks recognition complete. |
| Local OpenAI-compatible transcription | `TRANSCRIPTION_CONNECTOR=openai_whisper`, `TRANSCRIPTION_BASE_URL` set to the local API base (often ending in `/v1`), `TRANSCRIPTION_MODEL` set to its served model | Check the advertised model name, submit the same WAV and compare words and duration. Configure the service’s required authentication privately. |
| Optional local GPU with local CPU fallback | Configure the OpenAI-compatible CPU connector, then the GPU URL and exact served `CLINIC_GPU_ASR_MODEL` for the clinic wrapper. | Confirm the job used the intended engine. A transport failure, server error or failed health check may use CPU; an invalid request or model name needs correction. |

## Implementing another local connector

Extend `BaseTranscriptionConnector` in the transcription connector package. Implement request transformation and response parsing; return a `TranscriptionResponse` and advertise only capabilities actually supplied. Register the connector in the [registry](https://github.com/joehomeskillet/speakr/blob/main/src/services/transcription/registry.py), including setting validation. Apply the existing local URL guard before transferring audio. Keep retryable transport failures distinct from unsupported features and permanent configuration errors.

Use synthetic audio for the normal upload, reprocess and retry paths. Verify missing words, timing units, language and speaker fields with a real backend response. Run `python -m pytest tests/test_connector_architecture.py tests/clinic/test_gpu_connector_everywhere.py` in the project test environment. Keep endpoint credentials, raw audio and transcript bodies out of diagnostic logs.

For the optional GPU path, `CLINIC_GPU_ASR_BASE_URL` selects the private GPU endpoint and `CLINIC_GPU_ASR_MODEL` selects the model served there. Probe the service’s root `/healthz` on its private network. A healthy service can still reject an unrecognized model. In a custom implementation, `_validate_config()` rejects invalid settings; `transcribe(request)` returns the response. Supply `duration` in seconds and real segment/word timing where available. Omit missing speaker information instead of assigning a person from an audio filename.

Original reference for upstream Speakr: [archived upstream page](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/advanced/custom-connectors.md).
