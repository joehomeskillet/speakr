# Model Configuration
<small>[Deutsch](../de/admin-guide/model-configuration.md)</small>

This page describes how the clinic build chooses and reaches its models: the local speech recognition (ASR) model for transcription and the local text model for titles and summaries. Everything below applies with `CLINIC_MODE` on (`1`, `true`, `yes` or `on`). With clinic mode off, upstream Speakr behavior applies.

In the released clinic build, models are configured only through environment variables of the deployment. A per-task model selection page for administrators and automatic, revision-bound dictation summaries exist as a feature that is **in verification**; they are described at the end of this page and are not part of the released build.

## Status overview

| Area | Status |
|------|--------|
| Local ASR through an OpenAI-compatible service such as Speaches (`TRANSCRIPTION_BASE_URL`) | Available |
| WhisperX or whisper-asr-webservice (`ASR_BASE_URL`) | Available; locality check: Open gap (see below) |
| GPU first, CPU second fallback (`CLINIC_GPU_ASR_BASE_URL`) | Default off |
| Local text model for titles and summaries (`TEXT_MODEL_*`) | Default off (no LLM client until `TEXT_MODEL_BASE_URL` or `TEXT_MODEL_API_KEY` is set); locality check of the automatic title and summary job: Open gap |
| Model discovery and saving in the **Transcription models** panel | Upstream only (panel visible; **Discover** and **Save selection** answer 403 `clinic_feature_disabled`) |
| Model from a per-author server ASR profile | Prepared, disabled |
| Page **Modellwahl** (model selection), automatic dictation summaries, panel **KI-Zusammenfassung** (AI summary) | In verification |
| Cloud ASR connectors, hosted text-model providers, GPT-5 settings, separate chat and translation models, embedding models | Upstream only |

Speech recognition runs locally only. The build has no cloud ASR connector and no cloud fallback. Locality is enforced for `TRANSCRIPTION_BASE_URL` and `CLINIC_GPU_ASR_BASE_URL`; `ASR_BASE_URL` is not checked (Open gap, see below).

## Who configures what

- **Deployment operator (no application role).** All model endpoints, model names and keys are environment variables of the server process. Restart the application after changing them. No page lets anyone enter a model service URL or a model API key.
- **Site administration** (**Administrationsrolle**, stock `User.is_admin`). Opens **Verwaltung** › **System** › **Systemeinstellungen** (system settings) with the settings that touch models: `disable_auto_summarization`, `transcript_length_limit` and `asr_timeout_seconds` (see [System Settings](system-settings.md)). Each change is audited as `admin.setting.changed`.
- **Active site administration** (**Administrationsrolle** plus a clinic account with status **Aktiv** (active)). Only this role may use the **Modellwahl** page that is in verification.
- **Local identity administration** (**Identitätsadministration**) and **pool leads** (**Leitung**). No model configuration at all.
- **Fachrechte** (professional rights). In the summary feature that is in verification, reading a summary needs **Ansehen** (view) and requesting one needs **Bearbeiten** (edit) on the dictation. The separate rights **Freigeben** (release) and **Originalaudio exportieren** (original-audio export) play no part in model configuration. See [User Management](user-management.md).

## Speech recognition models (released build)

### Connectors

Only three transcription connectors are registered:

| Connector | Selected by | Use |
|-----------|-------------|-----|
| `openai_whisper` | `TRANSCRIPTION_BASE_URL` (default when `ASR_BASE_URL` is unset) | Local OpenAI-compatible ASR such as Speaches |
| `asr_endpoint` | `ASR_BASE_URL` (or the deprecated `USE_ASR_ENDPOINT=true`) | Self-hosted WhisperX or whisper-asr-webservice |
| `clinic_stub` | `TRANSCRIPTION_CONNECTOR=clinic_stub` only | Synthetic test double that returns fixed sentences; not speech recognition, refuses to run with `CLINIC_PROFILE=production` |

Selection order: `TRANSCRIPTION_CONNECTOR`, then `ASR_BASE_URL`, then `openai_whisper`. Any other connector name leaves the connector uninitialized: the startup log shows `Failed to initialize transcription connector: ... Unknown connector: <name>. Available: [...]`. Setup of the services themselves is described in [Local ASR setup](whisperx-setup.md).

A connector configuration error does not stop the start by itself. The startup log shows `Failed to initialize transcription connector: <reason>`, and a legacy check runs. The process exits only if that check fails too: with `ERROR: No transcription service configured!` when `USE_ASR_ENDPOINT` is not `true` and `TRANSCRIPTION_BASE_URL` or `TRANSCRIPTION_API_KEY` is empty, or with `ERROR: ASR enabled but ASR_BASE_URL not configured!` when `USE_ASR_ENDPOINT=true` and `ASR_BASE_URL` is empty. Otherwise the application starts, and every transcription job fails with the same configuration error until it is fixed.

### Local endpoint rule for ASR

In clinic mode the `openai_whisper` connector fails closed. An empty `TRANSCRIPTION_BASE_URL`, or one that is not local, raises `CLINIC_MODE is active: transcription base URL must point to a local or private service ...`. For ASR, local means: `localhost`, a loopback, private (RFC 1918, IPv6 ULA) or link-local address, any single-label host name (for example a compose service name such as `speaches`), or a name ending in `.local`, `.internal`, `.lan` or `.localhost`. The GPU target is checked the same way.

!!! warning "Open gap: `ASR_BASE_URL` is not checked"
    The `asr_endpoint` connector only checks that `ASR_BASE_URL` is set. A public address is not refused in clinic mode; at most a startup warning is logged for plain `http` to another host. Point `ASR_BASE_URL` only at a local service.

### ASR variables

| Variable | Default | Meaning |
|----------|---------|---------|
| `TRANSCRIPTION_CONNECTOR` | auto-detect | `openai_whisper`, `asr_endpoint` or `clinic_stub` |
| `TRANSCRIPTION_BASE_URL` | unset | Local OpenAI-compatible ASR, for example `http://speaches:8000/v1` |
| `TRANSCRIPTION_API_KEY` | unset | Must not be empty for `openai_whisper`; a local service accepts a placeholder such as `not-needed-for-local` |
| `TRANSCRIPTION_MODEL` | `whisper-1` | Model of `openai_whisper` (legacy name `WHISPER_MODEL`); the example configuration uses `Systran/faster-distil-whisper-large-v3` |
| `TRANSCRIPTION_TIMEOUT` | `3600` s in clinic mode | Read timeout of `openai_whisper`; must be a positive number, otherwise the connector is not initialized and the error names the variable (see Connectors above) |
| `TRANSCRIPTION_MAX_RETRIES` | `0` in clinic mode | SDK retries of `openai_whisper`; integer `>= 0`, otherwise the same error handling as for `TRANSCRIPTION_TIMEOUT` |
| `ASR_BASE_URL` | unset | WhisperX or whisper-asr-webservice; not locality-checked (Open gap) |
| `ASR_TIMEOUT` | `1800` s (through `asr_timeout_seconds`) | Timeout of `asr_endpoint`; takes precedence over the system setting `asr_timeout_seconds`, which applies only to this connector |
| `TRANSCRIPTION_MODELS_AVAILABLE` / `TRANSCRIPTION_MODEL_LABELS` | unset | Comma-separated model IDs and optional display names. Shown as the model list when no list is stored in the database; when a job's model is checked (step 2 below), these IDs are accepted in addition to a stored list |

### GPU first, CPU second (Default off)

With `CLINIC_GPU_ASR_BASE_URL` set, every job of the `openai_whisper` or `asr_endpoint` connector (never `clinic_stub`) goes to the GPU target first. The configured connector stays the CPU target.

| Variable | Default | Meaning |
|----------|---------|---------|
| `CLINIC_GPU_ASR_BASE_URL` | unset (fallback off) | OpenAI-compatible GPU ASR; must be local |
| `CLINIC_GPU_ASR_TIMEOUT` | `300` s | GPU request timeout; an invalid or non-positive value falls back to 300 |
| `CLINIC_GPU_ASR_API_KEY` | key of the CPU target, else `local` | Key for the GPU target |
| `CLINIC_GPU_ASR_MODEL` | unset | Pinned GPU model name |

Before each transcription request the application calls `/healthz` at the root of the GPU service (5 s timeout). If that call times out, is unreachable or answers non-2xx, or if the GPU request fails with a timeout, a connection error or HTTP 500 or higher, the same job runs once on the CPU target. The GPU target is not retried, and a CPU failure is the job's failure. Any other GPU error, for example HTTP 404 `model_not_found`, fails the job without CPU fallback. Log lines: `ASR GPU target <url> configured (timeout <n>s); CPU target stays the fallback` at startup, `ASR: sending job to GPU target <url>` per job, and `ASR fallback: GPU target <url> not used (<reason>); transcribing on CPU target <url>` on fallback.

Model name sent to the GPU target:

1. `CLINIC_GPU_ASR_MODEL`, when set.
2. Otherwise, for the bundled OpenVINO GPU service, the model that its `/healthz` reports.
3. Otherwise the same model name the CPU target would receive.

The bundled GPU service accepts only its `ASR_MODEL_NAME` (default `whisper-large-v3-turbo-int8-ov`) and `whisper-1`. Any other name gets HTTP 404 `model_not_found`, and the job fails. Service details: [Local ASR setup](whisperx-setup.md).

### Which ASR model a job uses

For each batch job the model is resolved in this order:

1. A model passed with the request (upload, reprocess), else the default transcription model of the first tag (in tag order) that sets one, else the folder default.
2. If an allowlist exists (the list stored by the **Transcription models** panel, `TRANSCRIPTION_MODELS_AVAILABLE`, or both), a value from step 1 that is on neither list is dropped with a log warning.
3. If step 1 gave no value, the stored admin default `transcription_default_model` applies, when set.
4. Otherwise the connector's own model (`TRANSCRIPTION_MODEL` for `openai_whisper`). `asr_endpoint` has no configured model: it sends the `model` query parameter only when steps 1 to 3 give a model; otherwise the service uses the model it has loaded.

The clinic dictation views offer no model choice. A value dropped in step 2 falls through to the connector's own model, not to the admin default.

### The Transcription models panel

**Systemeinstellungen** (system settings) still shows the upstream panel **Transcription models** (German UI: **Transkriptionsmodelle**). An administrator can read the stored list and default. In clinic mode the buttons **Discover** (**Erkennen**) and **Save selection** (**Auswahl speichern**) call routes under the feature lock `cloud_provider_selection`, which the clinic contract does not enable, so both answer 403 `clinic_feature_disabled`. A list or default stored earlier stays in effect and cannot be changed in the UI. To extend the allowlist, add IDs to `TRANSCRIPTION_MODELS_AVAILABLE`; entries of a stored list cannot be removed in clinic mode.

### Per-author ASR profiles (Prepared, disabled)

The server profile of an author can name a language and a model. It applies to a job only when the capability registry holds runtime evidence for the exact target, and the released build never fills that registry. Every job therefore runs with the clinic defaults (language `de` unless a request, tag, folder or the owner sets one) and records the reason in its job parameters. Hotwords and profile settings bias the decoder; they are not acoustic training. In clinic mode the initial prompt and the hotwords reach the recognizer only with `CLINIC_ASR_PROMPTS_ENABLED` on (`1`, `true`, `yes` or `on`; default off); then they share a cap of 55 words. See [Default Prompts](prompts.md) and [Local ASR setup](whisperx-setup.md).

## Text model for titles and summaries (released build)

### Variables

| Variable | Default | Meaning |
|----------|---------|---------|
| `TEXT_MODEL_BASE_URL` | code default `https://openrouter.ai/api/v1`, used only when set explicitly or a key exists | OpenAI-compatible endpoint of the text model |
| `TEXT_MODEL_API_KEY` | unset | Optional for a local server; without it the placeholder `not-needed` is sent |
| `TEXT_MODEL_NAME` | `openai/gpt-3.5-turbo` | Model ID; set it to an ID your local server serves |
| `CLINIC_LOCAL_MODEL_HOSTS` | `speaches,host.docker.internal,localhost` | Comma-separated host names that count as local; a set value replaces the default list |
| `CLINIC_LLM_TIMEOUT_SECONDS` | `120` | Per-call timeout of title and summary calls in clinic mode; replaces `LLM_REQUEST_TIMEOUT` (600) |
| `CLINIC_LLM_MAX_RETRIES` | `2` | Per-call retries in clinic mode; replaces `LLM_MAX_RETRIES`. Invalid values of both clinic variables fall back to their defaults |
| `LLM_CONNECT_TIMEOUT`, `LLM_WRITE_TIMEOUT` | `30` each | Connect and write timeouts in seconds of the text-model client (read timeout: `LLM_REQUEST_TIMEOUT`). They apply to the chat and translation calls; in clinic mode, title and summary calls replace them with the single per-call timeout `CLINIC_LLM_TIMEOUT_SECONDS` |
| `SUMMARY_MAX_TOKENS` | `3000` | Maximum output tokens of a summary |
| `TITLE_MAX_TOKENS` | `5000` | Maximum output tokens of a title call |
| `TEXT_MODEL_EXTRA_BODY` | unset | JSON object added to every text-model request (see below) |
| `PREFIX_CACHE_OPTIMIZED_PROMPTS` | `false` | Lays out title and summary prompts with a shared prefix (see below) |

No language-model client is created unless `TEXT_MODEL_BASE_URL` is set explicitly or `TEXT_MODEL_API_KEY` exists. Without a client there are no titles or summaries, and nothing is sent.

### The local-model rule

In clinic mode the stock summary, title and translation routes (`generate_summary_endpoint`, `reprocess_summary`, `regenerate_title`, `translate_transcription`, `api_regenerate_title`, `start_summarization`) answer 403 `clinic_feature_disabled` unless the text model is local. The same rule locks chat and Inquire. The rule counts as met only when:

- `TEXT_MODEL_BASE_URL` is set and local, and
- each of `CHAT_MODEL_BASE_URL`, `TRANSLATION_MODEL_BASE_URL` and `EMBEDDING_BASE_URL` that is set is local too. One public URL locks all of these features.

For text models, local means a loopback or private address (`127.0.0.0/8`, `::1`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `fc00::/7`) or a host on `CLINIC_LOCAL_MODEL_HOSTS`. This is stricter than the ASR rule: a bare single-label name that is not on the list is **not** local, and a URL with user info (`@`), a backslash or whitespace is refused. The flag `features.generative_summary` in the clinic defaults file does not decide this; only the locality of the URLs does.

!!! warning "Shipped examples are not clinic-ready"
    The example files `config/env.asr.example`, `config/env.transcription.example` ([clinic fork source](https://github.com/joehomeskillet/speakr/blob/main/config/env.transcription.example)), `config/env.whisper.example` and `config/env.whisperx.example` all set `TEXT_MODEL_BASE_URL=https://openrouter.ai/api/v1`, a hosted service. Replace it with a local endpoint before you enable clinic mode.

Example with a local text-model service named `llm` (placeholders in angle brackets):

```bash
CLINIC_MODE=1

# Speech recognition
TRANSCRIPTION_BASE_URL=http://speaches:8000/v1
TRANSCRIPTION_API_KEY=not-needed-for-local
TRANSCRIPTION_MODEL=Systran/faster-distil-whisper-large-v3

# Text model: the service name must be on the local-host list
CLINIC_LOCAL_MODEL_HOSTS=speaches,host.docker.internal,localhost,llm
TEXT_MODEL_BASE_URL=http://llm:<port>/v1
TEXT_MODEL_NAME=<model-id>
```

The released text-model client and the `openai_whisper` connector do not switch off the HTTP proxy environment variables of the process (for example `HTTPS_PROXY`). Make sure such settings do not route local model traffic out of the network.

### Request parameters and prefix caching

`TEXT_MODEL_EXTRA_BODY` holds a JSON object that is merged last into every request to the text model, so it overrides fields such as `max_tokens`. Use it for server-specific options of a local server, for example to turn off thinking for a Qwen3 model on vLLM, SGLang or llama.cpp:

```bash
TEXT_MODEL_EXTRA_BODY={"chat_template_kwargs": {"enable_thinking": false}}
```

A value that is not a JSON object is logged at startup, and requests to the text model fail until it is corrected. When the server rejects a request, the log names the added fields.

`PREFIX_CACHE_OPTIMIZED_PROMPTS=true` gives the title and summary prompts a byte-identical system message and transcript-first user block. A local inference server with automatic prefix caching (for example vLLM started with `--enable-prefix-caching`) can then reuse the transcript prefix between the two calls. With the flag on, both prompts use a shared system message and a reworded task block after the transcript, so the output can differ. Default off; check summary quality before you enable it. Prompt texts and their order of precedence are described in [Default Prompts](prompts.md).

### Automatic titles and summaries in the released build

In clinic mode a transcription job never calls the language model itself. It queues a `summarize` follow-up job on the summary queue. That job writes the title and, when automatic summarization applies, the stock summary of the recording. Automatic summarization applies when the system setting `disable_auto_summarization` is not `true`, the user's preference `auto_summarization` is not off, and a language-model client exists. In clinic mode the switch for that preference (**Auto-summarize**) sits on the account tab **Prompt options**, which is not rendered, so the preference keeps its default (on) unless it is changed through the API.

!!! warning "Open gap: automatic titles and summaries are not locality-checked"
    The automatic follow-up job has no locality check of its own; the local-model rule above guards only the stock summary and title routes. Whether titles and summaries of transcripts stay local therefore depends on configuring a local `TEXT_MODEL_BASE_URL`. Do not configure a hosted text model in clinic mode. Never set `TEXT_MODEL_API_KEY` without a local `TEXT_MODEL_BASE_URL`: a key alone creates a client with the code default `https://openrouter.ai/api/v1`. Leave both unset, or point them at a local service.

Title and summary calls count against the user's **Tokenlimit pro Monat** (monthly token limit), see [User Management](user-management.md). Language-model usage per model appears in [System Statistics](statistics.md).

## Model selection page "Modellwahl" (In verification)

**Status: In verification.** This page is not part of the current released clinic build. Labels and availability may change. The description follows the code of the feature as it currently stands. All labels are German only.

### Location and access

- **Verwaltung** › group **System** › **Modellwahl**, between **Systemeinstellungen** and **System-Auditprotokoll**; address `/admin#/system/modelle`. Rendered only with clinic mode on.
- Breadcrumb `Speakr › Verwaltung › Modellwahl`, heading **Modellwahl**, subline "Lokale Modelle für neue Verarbeitungsaufträge festlegen." (set local models for new processing jobs).
- Page and API (`GET`/`PUT /api/clinic/v1/admin/task-models`) are for an active site administrator only: `User.is_admin` plus a clinic account with status **Aktiv** (active). An identity administrator without the **Administrationsrolle**, a pool lead or a deactivated administrator gets 403; a pool lead does not see the link. Without a sign-in the API answers 401, a `PUT` without a valid CSRF token 403, and with clinic mode off 404.

### Fields

| Panel | Field | Task |
|-------|-------|------|
| **Transkription** — "Überträgt die Aufnahme in Text." | **GPU-Modell** | GPU target of the ASR fallback |
| | **CPU-Modell** | Configured (CPU) ASR connector |
| **Zusammenfassung** — "Fasst das gespeicherte Transkript zusammen." | **Textmodell** | Clinic dictation summaries only |

The page stores model identifiers only. Service URLs and keys stay in the deployment configuration. An identifier has at most 200 printable characters, consists of letters, digits and `_ . + : -` in segments separated by `/`, and contains no `:/`. Values such as `https://…`, `file:/…`, `../private` or a line break are refused with 400 `invalid_model`.

### Verified local catalogs

For each task the server asks the local service which models it serves. Each request ignores proxy environment variables, follows no redirect and has a short timeout (2 s for the GPU health check, 1.5 s for catalogs). A catalog is queried only when the service URL is local.

- **GPU-Modell:** `/healthz` at the root of `CLINIC_GPU_ASR_BASE_URL`. Only the bundled OpenVINO service counts (`model_loaded` true and an `openvino_genai` version). The choices are its reported model and `whisper-1`.
- **CPU-Modell:** `GET <base URL>/models` of the configured connector (`/v1/models` for `asr_endpoint`).
- **Textmodell:** `GET <text-model base URL>/models`.

A response with `has_more`, without a `data` list, or with any invalid ID counts as no catalog.

With a verified catalog the field is a drop-down whose first option is **Vorgabe übernehmen** (use the default). A saved ID that is no longer in the catalog is shown as a disabled option. Saving an ID outside the catalog is refused with 400, unless the value is unchanged. Without a verified catalog the field is free text. For **CPU-Modell** it suggests the IDs of the list stored in the panel **Modellkatalog für die Aufnahme** (model catalog for recording, see below) and of `TRANSCRIPTION_MODELS_AVAILABLE`. Any valid ID is then accepted, so check that the service actually serves it.

### Inactive service versus missing selection

Below each field the page shows one of three lines:

| Line | Meaning |
|------|---------|
| **Wirksam: <ID>** (effective) | The service is active and this ID is sent with new requests. For an inherited CPU value, a model chosen at upload or set on a tag or folder still takes precedence |
| **Wirksam: Keine Modellkennung gemeldet** (effective: no model ID reported) | The service is active, but the page cannot confirm an ID: nothing is saved or inherited, or the saved or inherited ID is not in the verified catalog. In the latter case new requests still send that ID, and the local service may refuse it |
| **Derzeit inaktiv: Kein Modell aktiv** (currently inactive: no model active) | The task's local service is not active: the CPU connector URL is not local, `CLINIC_GPU_ASR_BASE_URL` is unset or not local, or no local text model is ready |

Each line also names the source of the value: **Hier festgelegt** (set here), **Installationsvorgabe** (deployment default), **Vom lokalen Dienst** (reported by the local service), **Bestehender Standard** (existing default) or **Keine Vorgabe** (no default). Footer: "Ohne eigene Auswahl gilt die Vorgabe der Installation oder des lokalen Dienstes."

Inherited values when nothing is saved:

- **CPU-Modell:** the stored `transcription_default_model` (**Bestehender Standard**), otherwise the connector's configured model (**Installationsvorgabe**). `asr_endpoint` has no configured model; without a stored default the line shows **Keine Vorgabe**.
- **GPU-Modell** (only with `CLINIC_GPU_ASR_BASE_URL` set): `CLINIC_GPU_ASR_MODEL` (**Installationsvorgabe**), otherwise the model the GPU service reports (**Vom lokalen Dienst**), otherwise the CPU connector's configured model (**Installationsvorgabe**).
- **Textmodell:** `TEXT_MODEL_NAME` (**Installationsvorgabe**).

### Effect of a saved selection

- The server reads the selection from the database on every model request, so a saved value applies at once to new requests. Completed jobs are not reprocessed. A running chunked job reads the selection for each chunk request and can switch models mid-job; its text revision then records `<connector>:mixed`. An empty value (**Vorgabe übernehmen** or a cleared field) returns to the inherited default.
- A saved **CPU-Modell** replaces the model of the request, including a choice at upload or reprocess and tag or folder defaults, but only while the CPU target URL is local.
- A saved **GPU-Modell** takes precedence over `CLINIC_GPU_ASR_MODEL` and over the model the service reports, but only while the GPU URL is local. It only sets the model name sent with the request; it does not load or switch a model on the GPU service.
- **Textmodell** applies only to clinic dictation summaries. Titles and all other language-model calls keep `TEXT_MODEL_NAME`.

### Saving, conflicts and errors

**Modellwahl speichern** (save model selection) stays disabled until a value changes. Success shows "Modellwahl gespeichert." Saving uses a revision: if someone else saved in between, the server answers 409 `stale_revision`, the page shows "Die Modellwahl wurde zwischenzeitlich geändert. Bitte den aktuellen Stand neu laden." with the button **Neu laden** (reload), and the draft is kept. Other messages: "Für die Modellwahl fehlt die Administratorberechtigung." (403), "Mindestens eine Modellkennung ist für den lokalen Dienst ungültig." (400), "Die Modellwahl konnte nicht geladen werden." and "Die Modellwahl konnte nicht gespeichert werden."

### Storage, audit and logs

- The selection is stored as the system setting `clinic_task_models` (a JSON object with revision and models). **Systemeinstellungen** does not list this key, and a generic `POST /admin/settings` for it answers 400 `use_task_models_api`.
- Each save writes `admin.setting.changed` with the key `clinic_task_models`. Old and new model IDs are not recorded. See [Audit Log](audit-log.md).
- The language-model log line and the token usage record name the model actually used. An ASR text revision records the model of the response as `<connector>:<model>`; mixed chunk models are recorded as `<connector>:mixed`, and an `asr_endpoint` response without a submitted model name (including a resumed job) as `asr_endpoint:unspecified`.
- In **Systemeinstellungen**, the panel **Transcription models** is renamed **Modellkatalog für die Aufnahme** (model catalog for recording) with the note "Sichtbare Modelle und Anzeigenamen für die Aufnahme. Der Katalogstandard gilt für die CPU-Transkription, solange in der Modellwahl keine eigene CPU-Kennung festgelegt ist."

## Automatic dictation summaries (In verification)

**Status: In verification.** Not part of the current released clinic build. Labels and availability may change. The summary is a separate, read-only text: it never edits, replaces or is inserted into the dictation text. Verification so far uses synthetic data only; no statement is made about summary quality or clinical suitability.

### Where it appears

The panel **KI-Zusammenfassung** (AI summary) appears in the **Schreibplatz**, collapsed and loaded only when opened, and in the **Diktatdetails**, expanded once the detail has loaded. The text is rendered as limited Markdown (headings, lists, paragraphs, bold, italic); links and images appear as plain text and raw HTML as literal text.

Status texts: "Für dieses Diktat liegt noch keine KI-Zusammenfassung vor." (none yet), "Die Zusammenfassung wird nach der Transkription automatisch erstellt." (waiting for the first transcription), "Die KI-Zusammenfassung wartet auf die Verarbeitung." (queued), "Die KI-Zusammenfassung wird erstellt …" (processing), "Die Erstellung ist fehlgeschlagen." (failed) and "Der Bezug zur aktuellen Textfassung ist nicht bestätigt." (stale). While the panel is open and a job is pending, it refreshes every 2.5 s. The buttons **Zusammenfassung erstellen** (create) and **Zusammenfassung neu erstellen** (recreate) appear only for users with edit rights, while a local model is ready and the current saved text has at least 10 usable characters, and not while a job is pending or an error message is shown; with an error message the panel offers **Stand erneut laden** (reload status) instead. With unsaved edits the panel notes "Die Zusammenfassung verwendet die zuletzt gespeicherte Textfassung."

### Bound to a text revision

Each dictation has at most one stored summary, bound to the exact text revision it describes. Its status is `ready` only while that revision is current. After a later correction it becomes `stale`; it is not regenerated automatically. Older stock summaries of the recording are shown only as `stale`, error markers as `failed` without text, and a summary from an ambiguous historical source recording is not shown.

### Permissions and audit

| Action | Requirement | Otherwise |
|--------|-------------|-----------|
| Read (`GET /api/clinic/v1/dictations/<id>/summary`) | **Ansehen** (view) on the dictation, active account | 404 |
| Request (`POST`, same address) | **Bearbeiten** (edit), CSRF token | 403 |
| | A local model is ready | 503 `SUMMARY_UNAVAILABLE`, UI "Der lokale KI-Dienst ist derzeit nicht verfügbar." |
| | A current text of at least 10 characters | 409, UI "Zum Erstellen wird ein aktueller Diktattext benötigt." |

An active site administrator can read summaries through the view right of site administration, but gets 403 on a request without the edit right. Each read, including each automatic refresh, is audited as `recording.content.read` with `part=summary`; each manual request as `recording.reprocessed` with `kind=summary`.

### Processing

- The summary runs as job type `clinic_dictation_summary` on the existing summary queue (`ProcessingJob`/`JobQueue`), in the background; the recording is not set to processing. A repeated request while a job is queued creates no second job.
- Input is the current saved text revision, which may be corrected, not the raw ASR text. It is cut to `transcript_length_limit` (default 30000 characters, `-1` = no limit). The prompt follows the existing hierarchy described in [Default Prompts](prompts.md). Output is limited by `SUMMARY_MAX_TOKENS` (3000), and the reply must end with `finish_reason` `stop`.
- The model is the saved **Textmodell**, otherwise `TEXT_MODEL_NAME`. The request ignores proxy environment variables and follows no redirect. Provider errors are replaced by a generic message so that the error text cannot quote the transcript.
- Before using the text and again after the model call, the worker checks the user's view and edit rights, the account, a ready local model, the automatic setting for automatic jobs, and an unchanged text revision. If anything changed, the job fails without storing a result.

### Automatic summary after the first transcript

The first successful ASR revision of a new clinic dictation (job type `transcribe`) queues a summary job in the same transaction. The job runs as the author. All of these must hold:

- the system setting `disable_auto_summarization` is not `true`;
- the author's preference `auto_summarization` is not off;
- a local model is ready;
- the author has view and edit rights on the dictation;
- the first ASR text is usable and at least 10 characters long.

There is no backfill. Existing dictations, repeated recognition, later ASR deliveries and human corrections trigger no automatic summary; a user with edit rights can request one manually. If the author's automatic setting is switched off after the job was queued, the job fails without a model call.

With this feature, the stock follow-up job (`summarize`) writes only the title, and no stock summary, for recordings that belong to a clinic dictation; the dictation's summary is bound to its first ASR revision.

### Without a local text model

A local model is ready only when clinic mode is on, the local-model rule above is met, a language-model client exists and its base URL is local. Otherwise no automatic job is queued, no create button is shown, a request answers 503, and **Modellwahl** shows **Derzeit inaktiv: Kein Modell aktiv** for **Textmodell**. There is no fallback to a hosted model.

## Upstream only

These parts of the upstream page do not apply to the clinic build. The variables may still exist in the code, but they are not clinic instructions:

- Hosted text-model providers (OpenRouter, OpenAI, Azure AI Foundry and other gateways) and cost optimization. In clinic mode a hosted URL locks the stock summary routes, and the automatic follow-up path must not be pointed at one (see the open gap above).
- GPT-5 settings (`GPT5_REASONING_EFFORT`, `GPT5_VERBOSITY`, `GPT5_FORCE_MODE`, `CHAT_GPT5_*`, `TRANSLATION_GPT5_FORCE_MODE`) and cache breakpoints for Anthropic models (`TEXT_MODEL_CACHE_BREAKPOINTS`).
- Separate chat and translation models (`CHAT_MODEL_*`, `TRANSLATION_MODEL_*`, `CHAT_MAX_TOKENS`, `ENABLE_STREAM_OPTIONS`): chat and translation have no clinic UI. If their URLs are set, they must be local, or the local-model features lock.
- Embedding models (`EMBEDDING_*`) for Inquire; see [Ambrose settings](ambrose.md) and [Vector Store](vector-store.md). A set, non-local `EMBEDDING_BASE_URL` also locks the local-model features.
- Cloud transcription connectors (OpenAI Transcribe, Azure OpenAI, Mistral Voxtral and its chunking, VibeVoice and others) are removed from the fork.
- Model curation as described upstream: the **Default Prompts** tab is not rendered in clinic mode, and the **Transcription models** panel in **Systemeinstellungen** cannot discover or save (see above).

Upstream Speakr documentation (pinned original): [Model Configuration](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/model-configuration.md).

## Troubleshooting

- **Transcription jobs fail, and the startup log shows `Failed to initialize transcription connector: ... CLINIC_MODE is active: transcription base URL must point to a local or private service`.** `TRANSCRIPTION_BASE_URL` or `CLINIC_GPU_ASR_BASE_URL` is not local. Jobs fail until this is fixed.
- **Transcription jobs fail, and the startup log shows `Failed to initialize transcription connector: ... Unknown connector: ...`.** `TRANSCRIPTION_CONNECTOR` names a removed connector; use `openai_whisper`, `asr_endpoint` or `clinic_stub`.
- **Transcription jobs fail, and the startup log shows `Failed to initialize transcription connector: ... TRANSCRIPTION_TIMEOUT=... is invalid`** (or the same for `TRANSCRIPTION_MAX_RETRIES`). Use a positive number of seconds or a non-negative integer.
- **The application exits at startup with `ERROR: No transcription service configured!`** (or `ERROR: ASR enabled but ASR_BASE_URL not configured!`). The connector failed and the legacy check failed too: `TRANSCRIPTION_BASE_URL` or `TRANSCRIPTION_API_KEY` is empty (or `USE_ASR_ENDPOINT=true` without `ASR_BASE_URL`). The preceding `Failed to initialize transcription connector: ...` line names the connector error.
- **Summary or title routes answer 403 `clinic_feature_disabled`.** The text model is not local: check `TEXT_MODEL_BASE_URL` and every set `CHAT_MODEL_BASE_URL`, `TRANSLATION_MODEL_BASE_URL` and `EMBEDDING_BASE_URL`, and add a service name to `CLINIC_LOCAL_MODEL_HOSTS` if needed.
- **Discover** (**Erkennen**) or **Save selection** (**Auswahl speichern**) **answers 403.** Expected in clinic mode (see the **Transcription models** panel above).
- **GPU jobs fail with `model_not_found`.** The model name does not match the GPU service. Remove or correct `CLINIC_GPU_ASR_MODEL`; 4xx errors do not fall back to the CPU.
- **Check a change.** Restart the application. In the startup log, confirm `Transcription connector initialized: <name>`, with the GPU fallback also `ASR GPU target <url> configured (timeout <n>s); CPU target stays the fallback`, and `LLM client initialized: <url> / <model>` (or `Failed to initialize LLM client: ...` when no text model is configured). Then run one synthetic test dictation.
- **Which values are in effect.** Each text-model call logs `LLM call: operation=..., model=..., max_tokens=...`.
- **Cut-off or empty summaries.** The log says `Raise SUMMARY_MAX_TOKENS or limit reasoning with TEXT_MODEL_EXTRA_BODY.` Raise `SUMMARY_MAX_TOKENS` or turn off thinking with `TEXT_MODEL_EXTRA_BODY`.
- **Slow or hanging language model.** In clinic mode each call stops after `CLINIC_LLM_TIMEOUT_SECONDS` (120 s) with at most `CLINIC_LLM_MAX_RETRIES` (2) retries; transcription workers are not blocked, because titles and summaries run on the summary queue.
