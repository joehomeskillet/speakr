# Ambrose settings
<small>[Deutsch](../de/admin-guide/ambrose.md)</small>

In upstream Speakr, Ambrose is the AI assistant: the recording chat, the Ask page and an optional agent that searches the user's library. In the clinic build, Ambrose has the status **Upstream only**. Its code is still part of the build. With `CLINIC_MODE` on, no page shows it, and its routes are locked unless every text-model endpoint is local (see [How the lock works](#how-the-lock-works)). No page in **Verwaltung** (administration) and no system setting turns it on. This holds for every role: site administration (**Administrationsrolle**), identity administration (**Identitätsadministration**) and pool leads (**Leitung**) have no control over it.

## Status in the clinic build

| Upstream part | Status | Behavior with `CLINIC_MODE` on |
|---|---|---|
| Header button **Ask Ambrose** | Upstream only | Not rendered. The clinic header shows the menu **Verwaltung & Seiten** and the button **Neue Aufnahme** (new recording) instead. |
| Recording chat | Upstream only | The chat panel is part of the upstream detail view, which clinic mode does not render. The chat routes are locked under the feature `chat` while a model endpoint is not local. |
| Ask page (`/inquire`, `/ask`) and the agent | Upstream only | No link in the clinic UI. The routes are locked under the feature `inquire` while a model endpoint is not local. |
| Admin tab **Vector store** and the Inquire admin routes | Upstream only | The tab is not rendered. `admin.admin_inquire_status` and `admin.admin_process_recordings_for_inquire` are locked under `inquire`. See [Vector Store](vector-store.md). |
| Account tab **Prompt options** with **What Ambrose can read** | Upstream only | Not rendered. |
| Account field **Assistant's name** (tab **Preferences**, section **Assistant**; German UI: **Name des Assistenten**) | Upstream only (shown, no effect) | Still shown on `/account`. Only the chat and the Ask page use it, and the clinic UI shows neither, so it changes nothing in the clinic UI. |
| Incognito recordings and their chat | Upstream only | Removed from the fork. `ENABLE_INCOGNITO_MODE` is not read. |

## Why Ambrose is not part of the clinic build

- **Patient data.** Clinic transcripts contain patient data. With every question, the upstream recording chat puts the transcript into the model prompt, up to the system setting `transcript_length_limit` (default 30,000 characters). The Ask page and the semantic search work on an index of transcript chunks and their embeddings.
- **No generative clinical rewriting.** The binding work order (`clinic-spec/CLAUDE.md`) rules out cloud ASR and generative clinical rewriting. The decision register `clinic-spec/config/decisions.json`, which is labeled as development defaults and not as clinical approval, lists D21: "No LLM rewrite/chat/summaries/voiceprints/RAG/public share/personal webhooks". The code deviates from this for a local text model: `chat`, `inquire` and `generative_summary` are unlocked when every text-model endpoint is local (see [How the lock works](#how-the-lock-works)). The AI summary in verification builds on this.
- **Personal upstream feature.** Ambrose is a per-user assistant with its own name per user and per-user read toggles, and it answers questions across the recordings a user can open. Clinic work runs on dictations in the **Arbeitsliste** (worklist) and the **Schreibplatz** (typing workspace). Access is decided per dictation by pools and **Fachrechte** (professional rights), see [User Management](user-management.md).

## How the lock works

In clinic mode, a route guard classifies every endpoint (source: [route policy, clinic fork](https://github.com/joehomeskillet/speakr/blob/main/src/clinic/route_policy.py)). The Ambrose routes are in two locked groups:

| Feature | Endpoints |
|---|---|
| `chat` | `recordings.chat_with_transcription` (`POST /chat`), `recordings.download_chat_word`, `api_v1.chat_with_recording` |
| `inquire` | every `inquire.*` endpoint (Ask page, sessions, search, chat, download, filters), `api_v1.inquire_v1`, `admin.admin_inquire_status`, `admin.admin_process_recordings_for_inquire` |

Unlike other clinic features, `chat` and `inquire` do not follow their flag in `dev.defaults.json` in `clinic-spec/config/` (or in the directory set by `CLINIC_CONFIG_DIR`). Together with `generative_summary`, they stay locked unless every text-model endpoint is local:

- `TEXT_MODEL_BASE_URL` is set and local.
- `CHAT_MODEL_BASE_URL`, `TRANSLATION_MODEL_BASE_URL` and `EMBEDDING_BASE_URL` are local when they are set. One non-local URL locks all three features.
- Local means a loopback or private address (`127.0.0.0/8`, `::1`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `fc00::/7`) or a host name listed in `CLINIC_LOCAL_MODEL_HOSTS` (default `speaches,host.docker.internal,localhost`). Any other host name is not local, including a bare single-label name. A value for `CLINIC_LOCAL_MODEL_HOSTS` replaces the default list; it does not extend it. An empty value leaves only loopback and private addresses as local. A URL with userinfo, a backslash, whitespace or a control character, or with a scheme other than `http` or `https`, never counts as local; a URL without a scheme is allowed. Text after `#` is ignored.

While a feature is locked, its routes answer `403` with the code `clinic_feature_disabled`. The guard writes no log line and no audit row for these refusals.

If every set model endpoint is local, the lock lifts, but the clinic UI still has no chat panel, no Ask page and no header button. A direct request then gets this treatment:

- A chat request is decided per recording by the central policy, as an edit of that recording. A caller without the edit right gets `403 forbidden` or `404 not_found`. For a clinic dictation the request must also carry a valid edit lock (`lease_generation`); otherwise it gets `400 invalid_lease_generation` or `409`.
- A request that names no recording, such as the Ask page itself (`GET /inquire` or `/ask`), is refused with `403 clinic_feature_unsupported`.
- The Inquire handlers also refuse unless `ENABLE_INQUIRE_MODE=true`: the JSON routes under `/api/inquire/` with `403 inquire_disabled`, `api_v1.inquire_v1` with `403 feature_disabled`.
- For `POST /chat`, token use is recorded under the operation type `chat` and counts against the account's **Tokenlimit pro Monat** (monthly token limit). `api_v1.chat_with_recording` passes no operation type, so it neither checks this limit nor records its token use.

## Upstream settings without a clinic function

These variables are still read by the code, except `ENABLE_INCOGNITO_MODE`. They are upstream-only settings, not clinic instructions: do not use them to set up an assistant in the clinic build.

| Variable | Upstream purpose | Clinic build |
|---|---|---|
| `ENABLE_INQUIRE_MODE` | Ask page, header button and search index | Keep `false` (code default, also set in `clinic-spec/config/upstream.env.example`). See the warning below. |
| `ENABLE_INQUIRE_AGENT`, `INQUIRE_AGENT_TOOL_MODE`, `INQUIRE_AGENT_MAX_STEPS`, `INQUIRE_AGENT_TOOL_RESULT_TOKENS`, `INQUIRE_AGENT_TIMEOUT_SECONDS`, `INQUIRE_CONTEXT_BUDGET_TOKENS` | The agent and its limits | No clinic UI. |
| `INQUIRE_DEFAULT_ALLOW_SUMMARIES`, `INQUIRE_DEFAULT_ALLOW_NOTES` | Defaults of **What Ambrose can read** | The account tab is not rendered. |
| `CHAT_MODEL_*`, `CHAT_MAX_TOKENS` | Separate chat model and answer length | No clinic UI. A non-local `CHAT_MODEL_BASE_URL` locks chat, Inquire and the generative summary. |
| `CHUNK_TARGET_CHARS`, `CHUNKING_STRATEGY`, `EMBEDDING_MODEL`, `EMBEDDING_BASE_URL` | Search index | See [Vector Store](vector-store.md). A non-local `EMBEDDING_BASE_URL` locks chat, Inquire and the generative summary. |
| `ENABLE_INCOGNITO_MODE` | Incognito recordings | Removed from the fork, not read. |

!!! warning "`ENABLE_INQUIRE_MODE=true` indexes transcripts without a clinic check"
    With `ENABLE_INQUIRE_MODE=true`, every recording that finishes processing gets an `index_chunks` job, which cuts the transcript into chunks and embeds them. A change to the transcript or to speaker names through upstream routes queues the rebuild again. At startup, the app also chunks up to 10 completed recordings that have no chunks yet. None of these paths checks clinic mode, the `inquire` lock or whether `EMBEDDING_BASE_URL` is local. Embeddings are computed in the app with sentence-transformers unless `EMBEDDING_BASE_URL` is set; then the chunks go to that endpoint. Keep `ENABLE_INQUIRE_MODE=false`.

## What the clinic build offers instead

The clinic build has no assistant that answers questions about dictations.

- **Finding dictations.** The **Arbeitsliste** (worklist) has a **Metadatensuche** (metadata search) and **Gespeicherte Filter** (saved filters) on fields such as status, priority, author, pool, title, creation date and deadline.
- **Working with the text.** The text is corrected by hand in the **Schreibplatz**.

### Title and summary step in the released build

The released build still runs the upstream title and summary step after each transcription. In clinic mode, this step is a follow-up job on the summary queue. Without a configured text model (neither `TEXT_MODEL_BASE_URL` nor `TEXT_MODEL_API_KEY` set), both parts are skipped. Otherwise the title part can ask the text model for a title, and the summary part sends the transcript to the text model unless the system setting `disable_auto_summarization` is `true` or the author's account setting for automatic summaries is off. If only `TEXT_MODEL_API_KEY` is set, the client uses the code default `https://openrouter.ai/api/v1`, a hosted service. This step does not apply the local-URL rule from [How the lock works](#how-the-lock-works). The summary is stored with the recording, but the clinic UI does not show it. Site administration (**Administrationsrolle**) sets `disable_auto_summarization` on **Systemeinstellungen** (system settings) in **Verwaltung**. The account setting for automatic summaries defaults to on; the clinic UI has no switch for it, because the account tab **Prompt options** is not rendered. See [Model Configuration](model-configuration.md).

### In verification: KI-Zusammenfassung

!!! note "In verification"
    This feature is in verification. It is not part of the current released build, and its labels and availability may change.

In the current released build, the clinic UI shows no AI summary. The feature in verification adds a read-only panel **KI-Zusammenfassung** (AI summary). It is not an assistant: it takes no questions and never changes the dictation text.

- The panel appears in the **Schreibplatz**, collapsed, and loads when it is opened. In **Diktatdetails** (dictation details) it is expanded once the details have loaded.
- A summary is bound to the text revision it was made from. After a later correction it counts as stale, and the panel shows "Der Bezug zur aktuellen Textfassung ist nicht bestätigt." A correction does not trigger a new summary.
- Automatic summary: when the first speech-recognition text of a new dictation is stored, a summary job is queued if all of these hold: the system setting `disable_auto_summarization` is not `true`, the author's account setting for automatic summaries is not off (see above), the author holds the rights **Ansehen** (view) and **Bearbeiten** (edit) on the dictation, the text has at least 10 characters, and a local text model is ready. Existing dictations get no new summary automatically. A summary that the released build stored for the recording is shown as stale.
- For a recording linked to a dictation, the feature no longer runs the upstream summary step after transcription; the upstream title step still runs.
- Manual request: **Zusammenfassung erstellen** or **Zusammenfassung neu erstellen** (create or re-create the summary). The button appears only with the **Bearbeiten** right on the dictation, when the current text has at least 10 characters and a local text model is ready.
- Reading needs the **Ansehen** right on the dictation. Each read is audited as `recording.content.read` with `part=summary`, each manual request as `recording.reprocessed` with `kind=summary`.
- "Local text model ready" means: clinic mode is on, the `generative_summary` lock is lifted (the local-URL rule above), and the text-model client points at a local URL. Otherwise a manual request answers `503` and the panel shows "Der lokale KI-Dienst ist derzeit nicht verfügbar." Reading a stored summary still works; the button is not shown. There is no hosted fallback. The same local text model also lifts the `chat` and `inquire` locks: direct chat requests then work for callers with the edit right, although the clinic UI shows no chat (see [How the lock works](#how-the-lock-works)).
- The summary uses the summary prompt resolution described in [Prompts](prompts.md). Its model can be set as **Textmodell** on the system page **Modellwahl**, which is also in verification (see [Model Configuration](model-configuration.md)).

## Upstream reference

The upstream page covers the chat model, the agent's tool modes, its limits, usage tracking and the search index: [Ambrose settings (upstream Speakr documentation)](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/ambrose.md). It describes upstream Speakr, not the clinic build.
