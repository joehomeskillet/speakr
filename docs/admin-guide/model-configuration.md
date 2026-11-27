# Choose local transcription and summary models

[Deutsch](../de/admin-guide/model-configuration.md)

With `CLINIC_MODE` on, the current clinic build offers **Verwaltung → System → Modellwahl** for GPU transcription, CPU transcription and the dictation summary text model. These controls and revision-bound **KI-Zusammenfassung** are integrated features, not a separate preview. Deployment operators configure service addresses and credentials; active site administrators select model identifiers in the application.

Cloud ASR connectors and cloud fallback are not offered by the clinic fork. The OpenAI-compatible connector is retained for local services such as Speaches, not as an instruction to use a cloud service.

## Configure local services

Only `openai_whisper`, `asr_endpoint` and the synthetic `clinic_stub` connector are registered. An explicit `TRANSCRIPTION_CONNECTOR` wins; otherwise `ASR_BASE_URL` selects `asr_endpoint` (the deprecated `USE_ASR_ENDPOINT=true` also does so), and the default is `openai_whisper`. The stub is not speech recognition and refuses the production profile.

| Variable | Purpose |
| --- | --- |
| `TRANSCRIPTION_BASE_URL` | Local OpenAI-compatible CPU ASR endpoint, for example `http://speaches:8000/v1` |
| `TRANSCRIPTION_API_KEY` | Non-empty credential required by the connector; use a placeholder only when the local service does not require authentication |
| `TRANSCRIPTION_MODEL` | Deployment model for `openai_whisper`; defaults to `whisper-1`, with `WHISPER_MODEL` retained as a legacy name |
| `ASR_BASE_URL` | Local WhisperX or whisper-asr-webservice endpoint for `asr_endpoint` |
| `CLINIC_GPU_ASR_BASE_URL` | Optional local GPU target; unset means no GPU-first fallback wrapper |
| `CLINIC_GPU_ASR_MODEL` | Optional GPU deployment model; otherwise the recognised GPU service model or CPU connector model supplies the default |
| `TEXT_MODEL_BASE_URL` | Explicit local OpenAI-compatible text endpoint |
| `TEXT_MODEL_NAME` | Model served by that local endpoint; set this explicitly rather than relying on the upstream model name |
| `TEXT_MODEL_API_KEY` | Local service credential when required; without a key the text client sends `not-needed` |
| `CLINIC_LOCAL_MODEL_HOSTS` | Text-model hostname allowlist; defaults to `speaches,host.docker.internal,localhost` |

Restart the application after deployment-environment changes. The model selection page accepts no endpoint URL or API key.

### Locality checks

The `openai_whisper` connector and GPU target reject an empty or non-local base URL in clinic mode. The ASR rule accepts loopback, private and link-local addresses, single-label service names and names ending in `.local`, `.internal`, `.lan` or `.localhost`. The `asr_endpoint` connector does **not** enforce this locality check: operations must keep `ASR_BASE_URL` local. See [local ASR setup](whisperx-setup.md) for fallback, timeouts and service checks.

The text-model rule is stricter. `TEXT_MODEL_BASE_URL` must be local, and every set `CHAT_MODEL_BASE_URL`, `TRANSLATION_MODEL_BASE_URL` and `EMBEDDING_BASE_URL` must also be local. One non-local optional endpoint locks chat, Inquire and generative summaries together.

For text models, local means loopback (`127.0.0.0/8`, `::1`), private networks (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `fc00::/7`) or a hostname in `CLINIC_LOCAL_MODEL_HOSTS`. A custom list replaces the defaults. A single-label hostname not on that list does not qualify. URLs containing user information, backslashes or whitespace are rejected. Use an explicit `http` or `https` URL.

Example deployment values; replace the port and model placeholders with those of the local services:

```ini
CLINIC_MODE=1
TRANSCRIPTION_BASE_URL=http://speaches:8000/v1
TRANSCRIPTION_API_KEY=not-needed-for-local
TRANSCRIPTION_MODEL=<local-asr-model>
CLINIC_LOCAL_MODEL_HOSTS=speaches,host.docker.internal,localhost,llm
TEXT_MODEL_BASE_URL=http://llm:<port>/v1
TEXT_MODEL_NAME=<local-text-model>
ENABLE_INQUIRE_MODE=false
```

Do not set `TEXT_MODEL_API_KEY` alone. Without an explicit base URL, the legacy client uses a hosted default. No text client is created if both the key and explicit base URL are absent. Some upstream example files still name hosted text services; they are not clinic-ready configurations.

## Use Modellwahl

Sign in with an active site administrator account: **Administrationsrolle** plus a clinic account with status **Aktiv**. Open `/admin#/system/modelle`. Identity administration or pool leadership alone grants no access.

1. Read the saved selection, effective identifier and source for **GPU-Modell**, **CPU-Modell** and **Textmodell**.
2. Choose an identifier from the local service catalog. If no complete catalog is available, enter an identifier actually supported by the deployment. This free-text fallback validates the identifier's syntax, not whether a model is loaded.
3. Select **Modellwahl speichern**, then check the effective values. Clear a field or choose **Vorgabe übernehmen** to inherit its default.
4. If another administrator saved first, use **Neu laden** and reconcile the values before saving again. A stale revision returns `409 stale_revision`.

Catalog requests query the local CPU/text service's model list and the GPU service's `/healthz`. The GPU catalog recognises the bundled OpenVINO service's loaded model and `whisper-1`. A verified catalog produces a drop-down; a saved identifier missing from it appears disabled. A different identifier outside that catalog is refused. Without a catalog, CPU suggestions come from the existing recording-model list and `TRANSCRIPTION_MODELS_AVAILABLE`.

### Understand effective values

| Display | Meaning |
| --- | --- |
| **Wirksam: …** | An effective identifier is known for the configured local path. This is not proof that a real inference request will succeed. |
| **Wirksam: Keine Modellkennung gemeldet** | No identifier can be confirmed, including a saved/default identifier absent from a verified catalog. Requests may still send that identifier and be rejected by the service. |
| **Derzeit inaktiv: Kein Modell aktiv** | The path is not configured as local, or the summary client does not meet the local-model checks. A saved selection can still exist. |

Sources are **Hier festgelegt**, **Installationsvorgabe**, **Vom lokalen Dienst**, **Bestehender Standard** and **Keine Vorgabe**. The page queries catalogs but does not run a transcription or summary acceptance test.

| Task | Saved selection | Inherited default |
| --- | --- | --- |
| CPU transcription | Overrides the request model while the CPU endpoint is local | Existing `transcription_default_model`, otherwise connector default; request/tag choices can still take precedence when no task override is saved |
| GPU transcription | Overrides the deployment GPU identifier and reported service model | `CLINIC_GPU_ASR_MODEL`, otherwise recognised service model, otherwise CPU connector model |
| Dictation summary | Applies only to revision-bound clinic summaries | `TEXT_MODEL_NAME`; titles and other text-model operations retain their existing model configuration |

Selections are read for new model requests. They do not reprocess completed work and do not load a different model into the service. A running chunked ASR job can read a changed selection between chunk requests; avoid changing it during a comparison run.

Each save is stored with a revision in `clinic_task_models` and emits `admin.setting.changed` through the audit service, without old/new model identifiers. The generic system-settings endpoint refuses writes to this key; use **Modellwahl**.

The separate **Modellkatalog für die Aufnahme** panel in **Systemeinstellungen** describes the existing recording-model list. Its upstream discovery/save routes remain locked in clinic mode. A previously stored list/default still applies; this panel is not the task-model editor.

## Revision-bound dictation summaries

**KI-Zusammenfassung** is collapsed in **Schreibplatz** and expanded in **Diktatdetails**. Opening it reads the status. The summary is separate from the transcript and never edits or replaces the dictation text.

Reading requires **Ansehen**. A manual request additionally requires **Bearbeiten**, a valid session/CSRF token, a usable saved text of at least 10 characters and a ready local text model. Site administration's content access does not itself grant edit permission. The buttons are **Zusammenfassung erstellen** and **Zusammenfassung neu erstellen**.

The model receives the current saved text revision, including saved corrections, cut to `transcript_length_limit` (default 30000 characters; `-1` means no limit). Unsaved edits are not included. The summary uses the [resolved prompt](prompts.md) and the selected **Textmodell**. It follows no redirects and ignores proxy environment variables. Before storing the result, the worker rechecks permissions, account state, local-model availability and the text revision.

One summary is stored per dictation, bound to its source revision. A later text revision makes it stale; the panel says **Der Bezug zur aktuellen Textfassung ist nicht bestätigt.** Historical recording summaries without a confirmed revision are also stale. Corrections do not automatically request another summary.

The first successful ASR revision can queue an automatic summary when:

- `disable_auto_summarization` is not `true`;
- the author's `auto_summarization` preference is not off;
- the author may view and edit the dictation;
- the first saved ASR text is usable and at least 10 characters long;
- a local text model is ready.

There is no automatic backfill for existing dictations. Repeated recognition and later corrections do not trigger this first-revision job. The summary runs on the existing background summary queue; opening the panel is not its trigger. Each read is audited as `recording.content.read` with `part=summary`; each manual request as `recording.reprocessed` with `kind=summary`.

Without a ready local model, no automatic summary is queued and the create button is absent; a direct request returns 503. Existing stored summaries remain readable. A missing usable current text gives 409; insufficient edit rights give 403. These conditions differ from a failed background generation, which is shown as **Die Erstellung ist fehlgeschlagen.**

## Title processing and limits

For recordings linked to clinic dictations, the older follow-up job generates only the title; it skips the stock recording summary. The revision-bound summary uses its own job. Unlinked legacy recordings can still use the stock summary path.

The title and legacy summary paths do not have the revision-summary worker's local-client check. Configure a local text endpoint for the entire deployment, or leave text-model configuration unset. `disable_auto_summarization` stops automatic summaries, not title generation. The legacy client can also inherit proxy environment settings; check that local traffic stays local.

| Limit | Default |
| --- | --- |
| `CLINIC_LLM_TIMEOUT_SECONDS` | 120 seconds per title/summary call |
| `CLINIC_LLM_MAX_RETRIES` | 2 |
| `SUMMARY_MAX_TOKENS` | 3000 output tokens |
| `TITLE_MAX_TOKENS` | 5000 output tokens |

`TEXT_MODEL_EXTRA_BODY`, when used, must be a JSON object accepted by the local server; it is merged last and can override request parameters. Invalid JSON/object values make requests fail. Keep `PREFIX_CACHE_OPTIMIZED_PROMPTS` off unless the changed prompt layout has been checked for the deployment. Model selection and fluent generated text do not establish clinical suitability.

After configuration changes, run a synthetic dictation through the intended CPU/GPU path and inspect the summary separately. For `model_not_found`, check the identifier actually served: GPU 4xx errors do not trigger CPU fallback. For a disabled summary, check all configured text-model URLs, not only `TEXT_MODEL_BASE_URL`.

[Original Speakr handbook at the archived revision](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/model-configuration.md)
