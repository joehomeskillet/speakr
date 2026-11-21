---
layout: default
title: Local ASR Setup
parent: Admin Guide
nav_order: 8
---

# Local Speech Recognition (ASR) Setup
<small>[Deutsch](../de/admin-guide/whisperx-setup.md)</small>

In the clinic build, speech recognition runs only on local services. This page replaces the upstream WhisperX setup guide. It covers the local CPU service, the optional GPU service with CPU fallback, timeouts, status views, the optional speaker service and what hotwords can and cannot do.

The clinic rules on this page (address locality check, GPU fallback, clinic timeouts and retries, hint switch, German default language) apply only when `CLINIC_MODE` is `1`, `true`, `yes` or `on`. Independently of that, the clinic build knows only the `openai_whisper`, `asr_endpoint` and `clinic_stub` connectors; the upstream cloud connectors are absent even with clinic mode off. With clinic mode off, however, `TRANSCRIPTION_BASE_URL` is not checked, and an empty value sends audio to the default endpoint of the OpenAI SDK. `CLINIC_MODE` is off by default: run the clinic build with it on.

Service addresses, keys, timeouts and switches of speech recognition are environment variables of the deployment. Only `asr_timeout_seconds` and `asr_async_jobs` (both for `asr_endpoint` only) are in **Systemeinstellungen** (System settings); the model default is described in [Model Configuration](model-configuration.md). The connector, including the GPU wrapper, is built once per app process, so restart the app after you change an environment variable. There is no admin page that sets ASR service addresses or keys.

## Status overview

| Function | Status | Short note |
|---|---|---|
| Local OpenAI-compatible ASR service (for example Speaches) through the `openai_whisper` connector | Available | The service address must be local or private; otherwise the connector refuses to start. |
| GPU first, CPU second (bundled OpenVINO GPU service) | Default off | Active only when `CLINIC_GPU_ASR_BASE_URL` is set. One fallback to the CPU target, no GPU retry. |
| `asr_endpoint` connector (WhisperX, whisper-asr-webservice) | Available (unchanged upstream connector) | Its address `ASR_BASE_URL` is **not** checked for locality (Open gap; see the section on this connector below). |
| Cloud ASR connectors (OpenAI GPT-4o Transcribe, Azure OpenAI, Mistral, Tencent, Alibaba FunASR, Mossland, AssemblyAI, VibeVoice, OpenASR) | Upstream only | Removed from the clinic build. No code path falls back to a cloud recognizer. |
| Transcription hints (initial prompt, hotwords) | Default off | Dropped unless `CLINIC_ASR_PROMPTS_ENABLED` is on. |
| Word timestamps | Default off | `TRANSCRIPTION_WORD_TIMESTAMPS`. |
| Per-author server ASR profiles (language, model, hotword sets) | Prepared, disabled | Never applied in the current build; jobs use the German defaults. |
| **Optimierung** (optimization runs), pronunciation training, negative lexicon | Prepared, disabled | Every training run is refused. Nothing is trained or adapted. |
| Live ASR adapter (`CLINIC_LIVE_ASR_BASE_URL`) | Prepared, disabled | No route or job uses it. |
| Speaker service (own voice profile) | Default off | Separate local process, only with the user's own consent. |
| Voice-based author suggestion | Prepared, disabled | Code exists; no route or job calls it. |
| WhisperX service setup (Hugging Face token, pyannote), voice profiles from speaker embeddings, speaker identification | Upstream only | Speaker identification is locked in clinic mode; diarized embeddings are not stored. Diarization itself is requested by `asr_endpoint` by default (`ASR_DIARIZE`). |
| `clinic_stub` connector | Simulated | Synthetic test double; returns fixed sentences. |
| Per-task model selection (GPU and CPU model) | In verification | Not part of the current released build. |
| German clinical recognition quality | Open gap | Not measured: no word error rate on German clinic audio, no Philips recorder audio. |

## Connector selection

Only three transcription connectors exist in the clinic build: `openai_whisper`, `asr_endpoint` and `clinic_stub`. The app picks one in this order:

1. `TRANSCRIPTION_CONNECTOR`, if set (`openai_whisper`, `asr_endpoint` or `clinic_stub`). Any other name fails with `Unknown connector: <name>. Available: [...]`.
2. `asr_endpoint`, if `ASR_BASE_URL` is set (or the deprecated `USE_ASR_ENDPOINT=true`, which logs a deprecation warning).
3. Otherwise `openai_whisper`, with the model from `TRANSCRIPTION_MODEL`, then the legacy `WHISPER_MODEL`, then `whisper-1`.

For a clinic installation, use `openai_whisper` with a local service. The startup log names the result, for example `Initialized transcription connector: openai_whisper`.

## CPU path: local OpenAI-compatible service

The recommended path is an OpenAI-compatible service such as Speaches, reached through the `openai_whisper` connector. The repository does not ship a Speaches deployment. The example file [config/env.transcription.example](https://github.com/joehomeskillet/speakr/blob/main/config/env.transcription.example) (clinic fork source) expects a service named `speaches` on the same Docker network:

```bash
CLINIC_MODE=1
TRANSCRIPTION_CONNECTOR=openai_whisper
TRANSCRIPTION_BASE_URL=http://speaches:8000/v1
TRANSCRIPTION_API_KEY=not-needed-for-local
TRANSCRIPTION_MODEL=Systran/faster-distil-whisper-large-v3
```

Rules in clinic mode:

- **The address must be local (fail closed).** `TRANSCRIPTION_BASE_URL` must point to loopback (`localhost`, 127.0.0.0/8, ::1), a private address (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, fc00::/7), a link-local address, a single-label host name such as a Compose service name, or a name ending in `.local`, `.internal`, `.lan` or `.localhost`. An empty value is also refused, so the OpenAI default never applies. The connector then fails with `CLINIC_MODE is active: transcription base URL must point to a local or private service ...`.
- **A key is required.** `TRANSCRIPTION_API_KEY` must not be empty. A local service without authentication accepts any placeholder.
- **Model.** The model value in the example file is a starting point only. No model has been benchmarked on German clinic audio. The model the request names can also come from an upload, a tag, a folder or the admin default; see [Model Configuration](model-configuration.md).
- **Large files.** The connector accepts files up to 25 MB. Larger files are split by the app (`ENABLE_CHUNKING`, default `true`; size from `CHUNK_LIMIT`, `20MB` in the example file; overlap `CHUNK_OVERLAP_SECONDS`, default `3`).

The `asr_endpoint` connector targets services that expose a `/asr` endpoint (whisper-asr-webservice, WhisperX). Connect an OpenAI-compatible service such as Speaches through `openai_whisper`.

## GPU path with automatic CPU fallback

**Status: Default off.** The GPU path is used only when clinic mode is on and `CLINIC_GPU_ASR_BASE_URL` is set. The app then wraps the configured connector (`openai_whisper` or `asr_endpoint`; never `clinic_stub`). This covers upload, reprocessing, retries and chunked jobs.

```bash
CLINIC_GPU_ASR_BASE_URL=http://gpu-asr:8000/v1
# CLINIC_GPU_ASR_TIMEOUT=300
# CLINIC_GPU_ASR_API_KEY=<secret>
# CLINIC_GPU_ASR_MODEL=
```

- The GPU target is an OpenAI-compatible client. Its address must also be local; a non-local value makes the connector start fail.
- Text after `#` in the address is ignored.
- `CLINIC_GPU_ASR_API_KEY` falls back to the CPU key, then to `local`.

### Order and fallback conditions

For each job (and each chunk):

1. The app calls `/healthz` at the root of the GPU address (5 s timeout).
2. If the health check answers 2xx, the job goes to the GPU target, with one attempt and no retry.
3. The same job runs **once** on the CPU target if one of these happens:
   - `/healthz` times out, is unreachable or answers with a status of 300 or higher;
   - the GPU request times out, has a connection error or answers HTTP 500 or higher.
4. A CPU failure ends this attempt. Within an attempt there is no second fallback and no cloud fallback. Whether the job runs again is decided by the job queue (see the section Timeouts and retries below); each new attempt starts again with `/healthz`.

Any other GPU error ends the attempt **without** CPU fallback, for example `404 model_not_found`, `400 invalid_audio` or `413 payload_too_large`.

### GPU model name

- With `CLINIC_GPU_ASR_MODEL` set, every GPU request uses that name.
- Without it, and when `/healthz` identifies the bundled OpenVINO service (`model_loaded: true` and an `openvino_genai` version), the request uses the model name reported there.
- Otherwise the request uses the CPU model or the per-request model.

The bundled service accepts only its own model name and `whisper-1`. Any other name gives `404 model_not_found` and fails the job without fallback. Set the pin only to one of these two names.

### Log lines

| When | Level | Text |
|---|---|---|
| Connector start | INFO | `ASR GPU target <url> configured (timeout <n>s); CPU target stays the fallback` |
| Each GPU job | INFO | `ASR: sending job to GPU target <url>` |
| Fallback | WARNING | `ASR fallback: GPU target <url> not used (<reason>); transcribing on CPU target <url>` |

Fallbacks are logged only; they are not written to the audit log. Each clinic job stores the planned engine (`selected`, `fallback`) and, after the run, the engine that ran (`actual`) in a `clinic_asr` record of its processing job. No page displays this record.

### Bundled OpenVINO GPU service

**Status: Default off.** The service lives in [deployment/gpu-asr-service](https://github.com/joehomeskillet/speakr/blob/main/deployment/gpu-asr-service/docker-compose.snippet.yml) (clinic fork source). A comment in the compose snippet states that the service is not yet the default ASR. It is not part of a default deployment.

- **API.** `POST /v1/audio/transcriptions` (OpenAI-compatible, `json` or `verbose_json`) and `GET /healthz`, on port 8000.
- **Engine.** OpenVINO GenAI `WhisperPipeline` on the Intel GPU device `GPU.0`, model `whisper-large-v3-turbo-int8-ov`.
- **Build.** The image build downloads the Intel GPU runtime packages and the model files at a pinned model revision. The build fails if a SHA-256 checksum does not match (`model.sha256`). The model is not in the repository.
- **Runtime.** One gunicorn worker and one engine thread: requests run strictly one after another. Audio is decoded in memory with ffmpeg and not stored.
- **Health.** `/healthz` returns `status`, `model_loaded`, `model`, `device` and `versions`; it answers 503 while the model is not loaded. The Docker health check calls `curl http://127.0.0.1:8000/healthz` every 30 s.
- **Timeout.** A request longer than `ASR_TIMEOUT_S` answers `504 engine_timeout`. In Speakr this counts as HTTP 5xx and triggers the CPU fallback.
- **Devices.** The snippet passes `/dev/dri` into the container and adds the render group from `RENDER_GID` (default `109`, the group of `/dev/dri/renderD*`).

Environment variables of the GPU service container (not of Speakr):

| Variable | Default | Meaning |
|---|---|---|
| `ASR_MODEL_DIR` | `/models/whisper-large-v3-turbo-int8-ov` | Model directory in the container |
| `ASR_MODEL_NAME` | `whisper-large-v3-turbo-int8-ov` | Served model name; requests may name this or `whisper-1` |
| `ASR_DEVICE` | `GPU.0` | OpenVINO device |
| `ASR_TIMEOUT_S` | `300` | Maximum time per request, in seconds |
| `ASR_PROMPTS_ENABLED` | `0` | Prompt switch of the service (`1`, `true`, `yes` = on) |
| `ASR_PROMPT_MAX_WORDS` | `55` | Word budget for the prompt; can only be lowered |
| `ASR_MAX_UPLOAD_BYTES` | `536870912` (512 MiB) | Upload limit; above it `413 payload_too_large` |

Check the service from a container on the same network:

```bash
curl http://gpu-asr:8000/healthz
```

## Timeouts and retries

| Path | Setting | Clinic default | Note |
|---|---|---|---|
| CPU target (`openai_whisper`) | `TRANSCRIPTION_TIMEOUT` (seconds, > 0) | 3600 | Without clinic mode: SDK default 600 s |
| CPU target (`openai_whisper`) | `TRANSCRIPTION_MAX_RETRIES` (integer >= 0) | 0 | Without clinic mode: SDK default 2. An invalid value of either variable is a configuration error. |
| GPU target | `CLINIC_GPU_ASR_TIMEOUT` | 300 | Invalid or <= 0 gives 300. No SDK retries; within an attempt the CPU fallback takes the place of a retry. |
| GPU health check | fixed | 5 s | |
| `asr_endpoint` | `ASR_TIMEOUT`, then the legacy env var `asr_timeout_seconds`, then the system setting `asr_timeout_seconds`, then 1800 | 1800 (seeded setting) | Read on each call. Applies only to `asr_endpoint`. |
| Job queue (all paths) | `JOB_MAX_RETRIES` | 3 | Maximum attempts per job, transient errors only; see [System Settings](system-settings.md). |

After each failed attempt the job queue decides whether the job runs again. An error that is not classified as permanent, for example a timeout, a connection error or HTTP 5xx, re-queues the whole job until `JOB_MAX_RETRIES` attempts are used (default `3`, so at most three attempts in total). With a GPU target, each new attempt starts again with `/healthz`, then GPU, then CPU. A permanent error fails the job at once: HTTP 400, 401, 402, 403, 404 or 413, or an error message with a pattern such as `model not found` or `invalid file format`. After a timeout on the CPU target, the same audio is therefore sent again, despite `TRANSCRIPTION_MAX_RETRIES=0`; plan for up to three CPU requests of up to 3600 s each per job.

The system setting `asr_timeout_seconds` (shown in **Systemeinstellungen** (System settings), in seconds) has no effect on the Speaches path or the GPU target. See [System Settings](system-settings.md). A change of a system setting is audited as `admin.setting.changed`; for `asr_timeout_seconds` only the key name is stored.

## Repeated recognition and late results

- **Erneut transkribieren** (repeat transcription) in the **Schreibplatz** (correction view) needs an open edit lease, playable audio, a saved text and no unsaved changes. The new result is checked against the current text, audio revision and source job; a stale or duplicate result is not applied. The server also requires the right **Bearbeiten** (Edit) on the dictation and the current edit lease (`lease_generation`). It refuses the request with `recording_already_processing` while an ASR job for the dictation is queued or running. For the edit right, see [User Management](user-management.md).
- If the current text is a human correction, the ASR result is kept as a side revision. The human revision stays current.
- The ASR profile view reports the wait policy: manual entry (**Manuell schreiben**, write manually) is offered after 120 s, or after the profile's `skip_wait_minutes` if that value is at most 14 minutes (a larger value is ignored and 120 s applies); ASR counts as timed out after 900 s. A late ASR result is kept as a side revision and never replaces manual work.
- Failed jobs can be retried on the **Verarbeitung** (Processing) page; see the section Status and health below.

## Language and transcription hints

- **Language.** A clinic job without a language from the request, tag, folder or owner gets `de` (German). The value `auto` forces automatic detection.
- **Hints off by default.** In clinic mode, batch jobs drop the initial prompt and hotwords from every source (admin default, user, tag, folder, upload) unless `CLINIC_ASR_PROMPTS_ENABLED` is `1`, `true`, `yes` or `on`. A job without a clinic record also runs with hints off.
- **Word cap.** With the switch on, initial prompt and hotwords share one budget of 55 words; the initial prompt is counted first. The `openai_whisper` connector sends both together as one `prompt` field.
- **GPU service.** The bundled GPU service has its own switch `ASR_PROMPTS_ENABLED` (default off). With it off, it discards the prompt and logs `prompt/hotwords ignored: ASR_PROMPTS_ENABLED is off (<n> words)`. Hints reach the GPU engine only when both switches are on.
- The admin default hotwords and initial prompt (`admin_default_hotwords`, `admin_default_initial_prompt`) therefore have no effect in clinic mode by default. See [Prompts](prompts.md).

### Hotwords are not training

Hotwords, prompts, vocabulary and profile settings bias the decoder. They do not train or adapt the acoustic or language model.

- **Wortschatz** (vocabulary) entries are not sent with the ASR request.
- Per-author server profiles apply to a job only with recorded runtime evidence for the exact target. The current build records no such evidence, so every job runs on the German defaults (Prepared, disabled).
- **Optimierung starten** (start optimization) on the **Sprachprofile** page creates a job of type `asr_training` only when the run is scheduled (**Lauf eingeplant.**, run scheduled). The other outcomes create no new job: a run for the same material is already active, the start is outside the time window or deferred because another run is active, the minimum data are missing, or scheduling fails. The queue refuses every such job with `CAPABILITY_UNAVAILABLE`; the job overview shows **Training nicht verfügbar** (training not available). No model is trained.
- Pronunciation training and the negative lexicon answer `CAPABILITY_UNAVAILABLE` without evidence. Saving a list or a phonetic hint is not proof of any effect on recognition.
- Correction-based adaptation stays an open requirement under the open production gate `G-ASR-PARITY`.

The page itself states: **"Eine Wortliste ersetzt kein Akustiktraining."** (A word list does not replace acoustic training.)

### The Wortschatz page

**Wortschatz** (vocabulary; `/admin#/wortschatz`, header menu section **Sprache und Verarbeitung**) has the tabs **Wörter** (words), **Vorschläge** (proposals), **Aussprache** (pronunciation) and **Enginewirkung** (engine effect).

- **Wörter:** under **Aktueller Bereich** (current scope), choose **Persönlich** (personal, the **Eigene Liste**, own list) or **Autorenpool** (author pool) with **Autorenpool wählen** (choose author pool). Each entry has a **Schreibweise** (written form), a **Gesprochen** (spoken) form and the status **Aktiv** or **Unterdrückt** (suppressed). The actions are **Wortliste importieren** (import word list), **Markierten Text hinzufügen** (add selected text), **Unterdrücken** (suppress), **Reaktivieren** (reactivate), **Wortliste exportieren** (export word list) and **Wirksame Liste exportieren** (export effective list).
- Every signed-in account may change its own personal list. Only a member of an author pool may open the pool list, and changing it needs further authority; see [Effects on shared pool resources](group-management.md#effects-on-shared-pool-resources). A site administrator who is not a member of the author pool cannot open its list.
- **Vorschläge:** the review queue for proposed vocabulary changes; one of its decisions is **Für den Pool übernehmen** (accept for the pool). It stays empty in the released build, because no code creates proposals. The tab then shows **Keine Vorschläge zu prüfen.** (no proposals to review).
- Word list entries are not sent with the ASR request and are no acoustic training (see above).

## Status and health

There is no admin page that shows the live health of the ASR or speaker services. Use these views:

| Where | Who | What it shows |
|---|---|---|
| **Verwaltung** › **Betrieb** › **Sprachprofile** (Speech profiles), `/admin#/sprachprofile`; header menu entry **ASR und Sprachprofile** | Active site administrators; the page is withheld for other accounts | Panel **Spracherkennung** (speech recognition) for the signed-in account. **Verwendete Erkennung** (engine used) shows **Nicht ermittelt** (not determined), because no capability evidence is recorded; it does not show the engine that ran. **Eingabehinweise (Prompts)** always shows **Aus** (off), also when `CLINIC_ASR_PROMPTS_ENABLED` is on. **Fähigkeiten und Nachweise** (capabilities and evidence) shows no capability as **Nachgewiesen** (proven). |
| **Verwaltung** › **Betrieb** › **Verarbeitung** (Processing), `/admin#/verarbeitung` | Active site administrators see all jobs; other accounts see only jobs of dictations they may view | Job counts, stuck jobs (processing longer than 1800 s), the last failures with **Fehlerdetails ausgeblendet.** (error details hidden). No transcript, parameters or audio path. **Erneut versuchen** (retry) is allowed only for active site administrators. |
| Account page (`/account`), tab **About**, section **Speech recognition** | Every signed-in user sees the model; only site administrators also see **Endpoint** | The CPU connector's model and address, not the GPU target. Without `TRANSCRIPTION_BASE_URL`, the endpoint shows the text `https://api.openai.com/v1` as a display default only; in clinic mode such a connector cannot start. |
| App log | Whoever has access to the deployment's application log (not a right in the web interface) | Connector start and fallback lines (see above). At startup, one warning per ASR address that uses plain `http` to another machine (`ASR_BASE_URL`, `TRANSCRIPTION_BASE_URL`), private addresses included; loopback, single-label and Docker host names are exempt. |

An active site administrator means: the **Administrationsrolle** (administration role) plus a clinic account with the status **Aktiv** (active). An **Optimierung** run only covers the calling administrator's own approved corrections. See [User Management](user-management.md).

## Speaker service (optional)

**Status: Default off.** The speaker service is a separate local process for consented own-voice profiles. Transcription does not need it.

- Source: `deployment/speaker-service`. CPU only, port 8090, models pinned by SHA-256 at build time, one worker, runs as a non-root user.
- Endpoints: `GET /healthz`, `POST /embed`, `POST /diarize` (raw WAV body). `/healthz` answers 503 while the models are not loaded.
- Container limits: `SPEAKER_MAX_BYTES` (default 268435456, 256 MiB), `SPEAKER_MIN_EMBED_SECONDS` (default `3.0`, seconds), `SPEAKER_MIN_DIARIZE_SECONDS` (default `1.0`, seconds).
- Speakr reaches it only through `CLINIC_SPEAKER_SERVICE_URL`, with a 120 s timeout (health check 5 s) and no retries. Without the variable, no voice profile can be created. Open gap: Speakr does not check this address for locality. Point it only at a local service.
- It is used only for the user's own voice enrollment after consent (**Eigenes Stimmprofil (freiwillig)**, own voice profile, voluntary). Voice-based author suggestion is Prepared, disabled. The processing pipeline does not call `/diarize`.
- Consent and revocation are described in [Privacy](privacy.md).

## asr_endpoint connector (WhisperX, whisper-asr-webservice)

**Status: Available (unchanged upstream connector), not the recommended clinic path.**

> **Open gap: `ASR_BASE_URL` is not checked for locality.** In clinic mode only `TRANSCRIPTION_BASE_URL` and `CLINIC_GPU_ASR_BASE_URL` are checked. A public `ASR_BASE_URL` is not refused; the only protection is the startup warning for plain `http`. Point `ASR_BASE_URL` only at a local or private host.

If you run a self-hosted WhisperX or whisper-asr-webservice on a local host:

- `ASR_BASE_URL`, `ASR_API_KEY` (default empty).
- Timeout: see the table above (default 1800 s from the system setting).
- `ASR_ASYNC_JOBS` / system setting `asr_async_jobs` (default `true`): when the service advertises the WhisperX jobs API in `/health`, the audio is submitted as a job and polled until it is done. The connector then deletes the job and its transcript on the service; it also deletes it after a failure on the service and after a cancel. A retry of the Speakr job resumes a saved job. Services without the jobs API, or `ASR_ASYNC_JOBS=false`, get the synchronous `/asr` request. See [System Settings](system-settings.md). A change is audited as `admin.setting.changed` with old and new value.
- A model name is sent only when the request names one; otherwise the service uses its own default model. With `CLINIC_ASR_PROMPTS_ENABLED` on, this connector sends the initial prompt and the hotwords as the separate parameters `initial_prompt` and `hotwords`.
- `ASR_ENABLE_CHUNKING` (default `false`) and `ASR_MAX_DURATION_SECONDS` (default `7200`) enable app-level chunking.
- **Diarization.** The clinic contract value `asr.diarization: false` in `clinic-spec/config/dev.defaults.json` is not read by the code. With this connector, diarization follows `ASR_DIARIZE` (default `true`), plus `ASR_MIN_SPEAKERS` and `ASR_MAX_SPEAKERS`. Set `ASR_DIARIZE=false` if the service should not diarize.
- **Speaker embeddings (Upstream only).** In clinic mode, voice embeddings of diarized speakers are not stored on the recording, and the upstream speaker identification routes are locked. `ASR_RETURN_SPEAKER_EMBEDDINGS` therefore gives no voice profiles.
- The GPU fallback also wraps this connector: GPU first, then `asr_endpoint` as the CPU target.

The upstream WhisperX service setup (Hugging Face token, pyannote model agreements, NVIDIA GPU tuning, voice profiles) is Upstream only. It is not part of the clinic build and has not been reviewed for clinical use.

## Synthetic test connector

**Status: Simulated.** `TRANSCRIPTION_CONNECTOR=clinic_stub` selects an offline test double. It opens no network connection and recognizes no speech. It accepts only PCM WAV and returns four fixed sentences ("Synthetischer Diktattext eins." … "vier."), marked `synthetic` and `not_asr_quality_proof`. It refuses to run with `CLINIC_PROFILE=production` and is never wrapped by the GPU fallback. Never use it for real dictations.

## Per-task model selection (in verification)

**Status: In verification. Not part of the current released build; labels and availability may change.**

The page **Modellwahl** (model selection) under **Verwaltung** › **System** (in verification) stores model identifiers per task. For speech recognition, its panel **Transkription** (transcription) has two fields:

- **GPU-Modell** (GPU model) for the GPU target of the fallback, and **CPU-Modell** (CPU model) for the configured (CPU) connector.
- A saved **CPU-Modell** replaces the model of the request only while the CPU target URL is local.
- A saved **GPU-Modell** takes precedence over `CLINIC_GPU_ASR_MODEL` and over the model the GPU service reports, only while the GPU URL is local. It only sets the model name sent with the request; it does not load or switch a model on the service.

The page also sets the summary text model (**Zusammenfassung** › **Textmodell**, summary › text model). Full description, including catalogs, the status lines **Wirksam** (effective) and **Derzeit inaktiv** (currently inactive), access and audit: [Model Configuration](model-configuration.md).

## Speakr environment variables

| Variable | Default | Meaning |
|---|---|---|
| `CLINIC_MODE` | off | Turns on all clinic ASR rules on this page |
| `TRANSCRIPTION_CONNECTOR` | auto-detect | `openai_whisper`, `asr_endpoint` or `clinic_stub` |
| `TRANSCRIPTION_BASE_URL` | unset | Local OpenAI-compatible ASR; required and local in clinic mode |
| `TRANSCRIPTION_API_KEY` | unset | Must not be empty; any placeholder for a local service |
| `TRANSCRIPTION_MODEL` (legacy `WHISPER_MODEL`) | `whisper-1` | CPU model |
| `TRANSCRIPTION_TIMEOUT` | 3600 in clinic mode | Read timeout, seconds |
| `TRANSCRIPTION_MAX_RETRIES` | 0 in clinic mode | SDK retries |
| `JOB_MAX_RETRIES` | `3` | Maximum attempts per job for transient errors; see [System Settings](system-settings.md) |
| `TRANSCRIPTION_WORD_TIMESTAMPS` | off | `1`, `true`, `yes`: request word timestamps (clinic mode only; the GPU target inherits it) |
| `TRANSCRIPTION_MODELS_AVAILABLE` | empty | Model allowlist; see [Model Configuration](model-configuration.md) |
| `CLINIC_ASR_PROMPTS_ENABLED` | off | Lets initial prompt and hotwords through, 55-word cap |
| `CLINIC_GPU_ASR_BASE_URL` | unset | Enables GPU first, CPU second; must be local |
| `CLINIC_GPU_ASR_TIMEOUT` | 300 | GPU request timeout, seconds |
| `CLINIC_GPU_ASR_API_KEY` | CPU key, else `local` | Key for the GPU target |
| `CLINIC_GPU_ASR_MODEL` | unset | Pinned GPU model name |
| `ASR_BASE_URL` | unset | `asr_endpoint` address; not checked for locality |
| `ASR_API_KEY` | empty | Key for `asr_endpoint` |
| `ASR_TIMEOUT` | unset (setting: 1800) | `asr_endpoint` timeout; overrides the system setting |
| `ASR_ASYNC_JOBS` | `true` | WhisperX jobs API, when advertised |
| `ASR_DIARIZE` | `true` | Diarization request of `asr_endpoint` |
| `ASR_MIN_SPEAKERS` / `ASR_MAX_SPEAKERS` | unset | Speaker count hints of `asr_endpoint` |
| `ASR_RETURN_SPEAKER_EMBEDDINGS` | `false` | Upstream only; embeddings are not stored in clinic mode |
| `ASR_ENABLE_CHUNKING` / `ASR_MAX_DURATION_SECONDS` | `false` / `7200` | App-level chunking for `asr_endpoint` |
| `USE_ASR_ENDPOINT` | `false` | Deprecated; use `ASR_BASE_URL` |
| `CLINIC_SPEAKER_SERVICE_URL` | unset | Local speaker service |
| `CLINIC_PROFILE` | unset | `production` makes `clinic_stub` refuse to run |
| `CLINIC_LIVE_ASR_BASE_URL` | unset | Live ASR adapter; not wired (Prepared, disabled) |

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `CLINIC_MODE is active: transcription base URL must point to a local or private service ...` | `TRANSCRIPTION_BASE_URL` or `CLINIC_GPU_ASR_BASE_URL` is empty or public. Use a local or private address. |
| `Unknown connector: <name>. Available: [...]` | `TRANSCRIPTION_CONNECTOR` names a connector that the clinic build removed. |
| `TRANSCRIPTION_TIMEOUT=... is invalid` | Use a positive number of seconds (`TRANSCRIPTION_MAX_RETRIES`: an integer >= 0). |
| Many `ASR fallback: GPU target ... not used` warnings | GPU service down or unhealthy. Check `/healthz` and the GPU container log. |
| Jobs fail with `model_not_found` on the GPU | `CLINIC_GPU_ASR_MODEL` or the request names a model the GPU service does not serve. No fallback for 4xx answers. |
| Hotwords seem to have no effect | Expected: hints are off by default (`CLINIC_ASR_PROMPTS_ENABLED`, and `ASR_PROMPTS_ENABLED` on the GPU service). |
| **Verwendete Erkennung: Nicht ermittelt** | Expected in the current build; see the section Status and health. |
| `.ds2` / `.dss` recordings end as failed | Outside Docker, set `CLINIC_DSS_CODEC_BIN` to the `dss-decode` binary; the Docker image sets it to `/usr/local/bin/dss-decode`. |

## Upstream reference

The original WhisperX instructions (service repository, Hugging Face token, pyannote agreements, GPU tuning, voice profiles) are not instructions for the clinic build. Upstream Speakr documentation: [WhisperX Setup (pinned upstream version)](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/whisperx-setup.md).
