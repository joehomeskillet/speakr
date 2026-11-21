# Default Prompts
<small>[Deutsch](../de/admin-guide/prompts.md)</small>

This page covers the prompts that the clinic build sends to a text model (LLM): the summary prompt and the title prompt, where they come from, and who can change them. It also covers the transcription hints that some upstream prompt settings send to the speech recognizer. Everything described here applies only when `CLINIC_MODE` is on (`1`, `true`, `yes` or `on`).

In upstream Speakr, site administrators edit the default summary prompt, the default title prompt, the default transcription hints and the generation temperatures in the admin tab **Default prompts**. In clinic mode this tab is not rendered, and the clinic build has no other editor for these values. The clinic build ships German clinic defaults instead.

## Status overview

| Item | Status in the clinic build |
|---|---|
| Admin tab **Default prompts** (summary prompt editor, **Default transcription hints**, **Default title prompt**, temperature fields, "View full LLM prompt structure") | Upstream only: not rendered in clinic mode |
| Account tab **Prompt options** (personal summary and title prompt, **Auto-summarize** toggle) | Upstream only: not rendered in clinic mode |
| Shipped German clinic summary prompt and title prompt | Available |
| Prompt hierarchy tag > folder > user > admin default > shipped default | Available (code); the admin and user levels have no clinic UI |
| Tag and folder prompt fields under **Tags & folders** on the account page | Available (per user, not an admin setting) |
| Generation temperatures (`/admin/llm-temperatures`) | Available as an API only, no UI |
| Transcription hints (initial prompt, hotwords) reaching the recognizer | Default off (`CLINIC_ASR_PROMPTS_ENABLED`) |
| Contextual speaker naming prompt | Upstream only: the step returns nothing in clinic mode |
| Automatic stock title and summary after transcription | Default off (no LLM client until `TEXT_MODEL_BASE_URL` or `TEXT_MODEL_API_KEY` is set); locality check of the automatic title and summary job: Open gap |
| Automatic local dictation summary and its panel **KI-Zusammenfassung** (AI summary) in **Schreibplatz** (workspace) and **Diktatdetails** (dictation details) | In verification |

## Clinic stance on generated text

The binding work order of the fork forbids generative clinical rewriting ("keine generative klinische Umschreibung") and cloud speech recognition. In the code, a summary is always a separate text: the stock summary is stored in its own field of the recording, and the transcript is not changed. A summary is never inserted into, and never replaces, the dictation text.

The decision register (`clinic-spec/config/decisions.json`, decision D21) still lists "No LLM rewrite/chat/summaries/voiceprints/RAG/public share/personal webhooks". That file is labeled as development defaults, not clinical approval. The code instead keeps chat, Inquire and generative summaries available only against local models: they stay locked unless every configured model endpoint (text, chat, translation, embedding) is local (`LOCAL_MODEL_FEATURES` in `src/clinic/config.py`). The flag `features.generative_summary: false` in `clinic-spec/config/dev.defaults.json` does not decide this. The two positions are not reconciled in the specification. No summary feature of the fork has a clinical approval, and the available test evidence is synthetic only. Summary quality has not been measured.

## Shipped clinic prompts

With clinic mode on, the shipped defaults for the summary and the title are German clinic prompts. They are defined in `src/clinic/prompts.py` ([clinic fork source](https://github.com/joehomeskillet/speakr/blob/main/src/clinic/prompts.py)); the upstream defaults in `src/config/prompts.py` stay unchanged.

**When they apply.** The clinic prompt replaces the admin default when the admin value (`admin_default_summary_prompt` or `admin_default_title_prompt`) is unset or still equal to the upstream default. Any other admin, user, tag or folder prompt wins, as upstream. A database initialized with clinic mode on is seeded with the clinic prompts. A database initialized without clinic mode holds the upstream default, which then resolves to the clinic prompt as soon as clinic mode is on.

**Summary prompt.** It asks for a structured German clinic summary of a medical dictation from an addiction clinic (withdrawal and addiction treatment), in Markdown, with these headings in this order:

- `Anlass / Fragestellung`
- `Suchtanamnese` (substances, consumption pattern, last intake, earlier withdrawals and therapies)
- `Aktueller Befund / Psychischer Status` (including withdrawal symptoms and named scores, for example CIWA-Ar, COWS)
- `Diagnosen` (as dictated; ICD-10 codes only when they are named)
- `Medikation / Entzugsbehandlung`
- `Procedere / Nächste Schritte`
- `Offene Punkte / Unklarheiten` (unclear or contradictory passages of the dictation)

Its rules tell the model to use content from the transcript only and invent nothing, to leave out a section the dictation says nothing about, to add no diagnoses, dosages or recommendations, to copy numbers, dosages, dates and names exactly as dictated, to write concise medical German, and to mark unclear or unintelligible speech recognition passages with `«[bitte prüfen]»` instead of guessing. These rules are instructions to the model. No code checks that a summary follows them.

**Title prompt.** Verbatim:

```text
- Titel auf Deutsch
- Höchstens 8 Wörter
- Keine Wendungen wie "Diktat zu" oder "Bericht über"
- Nur das Hauptthema nennen (z. B. Aufnahme, Verlauf, Entzug, Austritt)
```

**Output language.** In clinic mode, summaries and titles are requested in German unless the user's account has another output language set.

## Prompt hierarchy

The code keeps the upstream precedence for both prompts.

Summary: tag > folder > user > admin default > shipped default.

- **Tag:** every tag visible to the user that carries a summary prompt contributes; the prompts are joined in the order the tags were added.
- **Folder:** the folder prompt applies only when the summary is generated for the owner of the recording.
- **User:** the user's personal summary prompt.
- **Admin default:** `admin_default_summary_prompt`, with the clinic rule above.
- **Shipped default:** the German clinic prompt.

Title: tag > folder > user > admin default (`admin_default_title_prompt`) > shipped default, with the same joining rule for tag prompts.

A higher level replaces the lower levels; it does not extend them. A tag, folder or personal prompt therefore replaces the shipped clinic prompt including its rules (no invention, no added diagnoses, dosages or recommendations, `«[bitte prüfen]»` markers). Users can set tag and folder prompts themselves on the account page; the clinic build has no administrative control or check for them.

A prompt holds only instructions. The code adds the transcript and the output-language instruction, for titles also the rule to reply with the title alone, and for the stock summary the context block described below. A changed prompt applies only to summaries and titles generated afterwards; existing ones are not regenerated.

Where each level can be changed in the clinic build:

| Level | Where | Who |
|---|---|---|
| Tag, folder | Account page, tab **Tags & folders**, edit dialog of a tag or folder, tab **Summary & templates**: fields **Custom summary prompt** (tag) or **Custom prompt** (folder), and **Title prompt**. The folder subtab is shown only when folders are enabled (`enable_folders`). | The signed-in user, for their own tags and folders |
| User | No UI: the account tab **Prompt options** is not rendered. A personal prompt saved earlier still applies. | — |
| Admin default | No UI: the admin tab is not rendered, and the keys are hidden from the **Systemeinstellungen** (System settings) list. | See below |
| Shipped default | Code only | — |

The setting keys `admin_default_summary_prompt` and `admin_default_title_prompt` are still read. The stock endpoint `POST /admin/settings` still accepts them from a site administrator (**Administrationsrolle** (administration role), `User.is_admin`). The flag **Identitätsadministration** (identity administration), a pool lead (**Leitung**, lead) and the rights **Freigeben** (release) and **Originalaudio exportieren** (export original audio) do not grant this. While the audit log is on, each write is recorded in the **System-Auditprotokoll** (system audit log) as `admin.setting.changed` with the key name only, without old or new text (see [Audit Log](audit-log.md)). The clinic build has no UI and no supported clinic procedure for such a change. The request body is `{"key": "admin_default_summary_prompt", "value": "<prompt>", "setting_type": "string"}` (likewise for `admin_default_title_prompt`); a browser session also needs a valid CSRF token. An empty value, or the upstream default text, makes the shipped clinic prompt apply again. A changed admin default affects the summaries and titles of every user who has no tag, folder or personal prompt.

The keys `admin_default_initial_prompt`, `admin_default_hotwords` and `admin_default_contextual_speaker_prompt` are hidden from the settings list as well. The contextual speaker naming step returns no result in clinic mode, so its prompt has no effect.

## Generation temperatures

The temperatures for summary, title and chat requests have no UI in clinic mode. The API stays available to site administrators (`User.is_admin`):

- `GET /admin/llm-temperatures` returns, per kind, the effective value, its source (`admin` or `default`) and the default.
- `POST /admin/llm-temperatures` with `{"values": {"summary": 0.2, "chat": null}}` saves a value between 0 and 2; `null` or an empty string clears it. An unknown kind gives 400 `unknown_temperature`, an invalid value 400 `invalid_temperature`.

| Kind | Default |
|---|---|
| `summary` | 0.5 |
| `title` | 0.7 |
| `chat` | 0.7 |

Values are read on every request, so a change applies to the next request without a restart. While the audit log is on, each change is audited as `admin.setting.changed` with the key (`llm_temperature_summary`, `llm_temperature_title` or `llm_temperature_chat`) and no values. The upstream event-extraction temperature does not exist in the fork. Chat has no clinic UI (see [Ambrose](ambrose.md)).

## Where prompts are used in the released build

In clinic mode a transcription job never calls the text model itself. After the transcript is stored, it queues a follow-up job of type `summarize` on the summary queue. That job first runs the title step, then the summary when automatic summarization applies. Automatic summarization applies when all of these hold:

- the system setting `disable_auto_summarization` is not `true` (default `false`; shown under **Systemeinstellungen**);
- the user's own automatic summarization preference is not switched off (default on; its toggle **Auto-summarize** is in the hidden **Prompt options** tab, so users have no UI to change it in clinic mode);
- a text model is configured (`TEXT_MODEL_BASE_URL` or `TEXT_MODEL_API_KEY` is set).

The title step runs regardless of `disable_auto_summarization`. It calls the text model only when no naming template applies or the template uses the AI title.

The stock summary uses the recording's stored transcript, cut to `transcript_length_limit` characters (default 30000, `-1` = no limit). The request adds a context block with the current date, the recording date and title, folder, tags, the owner's name, job title and company when these are set, and the owner's notes on the recording when the owner chose to include them. Output is limited by `SUMMARY_MAX_TOKENS` (default 3000); titles by `TITLE_MAX_TOKENS` (default 5000). In clinic mode each text model call uses `CLINIC_LLM_TIMEOUT_SECONDS` (default 120) and `CLINIC_LLM_MAX_RETRIES` (default 2); see [Model Configuration](model-configuration.md).

The released clinic views **Schreibplatz** and **Diktatdetails** do not display a summary.

**Open gap.** This automatic follow-up path has no locality check of its own. It runs as soon as `TEXT_MODEL_BASE_URL` or `TEXT_MODEL_API_KEY` is set; with only a key set, the code default `https://openrouter.ai/api/v1`, a hosted service, applies. Only the manual stock routes (generate or reprocess a summary, regenerate a title, translate, start summarization) are locked in clinic mode unless every configured text, chat, translation and embedding URL is local. `disable_auto_summarization` stops automatic summaries but not automatic titles. Do not configure a hosted text model in clinic mode; see [Model Configuration](model-configuration.md).

## Transcription hints

The upstream card **Default transcription hints** (default initial prompt and hotwords) was part of the hidden admin tab. In clinic mode, batch transcription jobs drop the initial prompt and the hotwords from every source (upload or reprocess request, tag, folder, user, admin default) unless `CLINIC_ASR_PROMPTS_ENABLED` is on (default off). When it is on, prompt and hotwords share a cap of 55 words: the initial prompt is kept first, the hotwords get the remaining words. The bundled GPU speech recognition service has its own switch `ASR_PROMPTS_ENABLED` (default off); hints reach the GPU engine only when both switches are on. Hints bias decoding only; they are not acoustic training. Details: [Local ASR setup](whisperx-setup.md) and [System Settings](system-settings.md).

## In verification: automatic local dictation summary

> **In verification.** This feature is not part of the current released build. Labels and availability may change. The description follows the code under verification.

The feature adds a read-only panel **KI-Zusammenfassung** (AI summary) to the clinic views **Schreibplatz** (collapsed, loaded when opened) and **Diktatdetails** (expanded). The labels are German only.

**Prompt.** The summary uses the same hierarchy as above (tag > folder > user > admin default > shipped default, German clinic prompt in clinic mode). It is resolved for the user the job runs for: the author for an automatic summary, the requesting user for a manual one. The folder level applies only when that user owns the dictation's recording.

**Input.** The model receives the current saved text revision of the dictation, which may already be corrected, not the raw recognition text. The text is cut to `transcript_length_limit`. Unlike the stock summary, no context block (date, title, tags, user profile) is added. The output language is the user's output language, German by default.

**Limits.** `SUMMARY_MAX_TOKENS` (default 3000) and the `summary` temperature apply. A reply whose finish reason is not `stop`, or that is empty, fails the job. The request ignores proxy environment variables and does not follow redirects. Provider errors are replaced by a generic message, so that error text cannot quote the transcript. In clinic mode the call also uses `CLINIC_LLM_TIMEOUT_SECONDS` and `CLINIC_LLM_MAX_RETRIES`.

A summary is produced only with a ready local text model; there is no hosted fallback. The model can be chosen per task on the system page **Modellwahl** (model selection), field **Textmodell** (text model), which is also in verification and applies only to these dictation summaries. Only an active site administrator (**Administrationsrolle** (administration role) plus a clinic account with status **Aktiv** (active)) can open and save **Modellwahl**; **Identitätsadministration** (identity administration), **Leitung** (lead), **Freigeben** (release) and **Originalaudio exportieren** (export original audio) do not grant it.

The automatic trigger, who may read or request a summary, revision binding, audit events and test evidence are described in [Model Configuration](model-configuration.md), section "Automatic dictation summaries (In verification)".

## Related settings

| Setting | Default | Effect |
|---|---|---|
| `disable_auto_summarization` (Systemeinstellungen) | `false` | `true` stops automatic summaries for all users; titles still run |
| `transcript_length_limit` (Systemeinstellungen) | `30000` | Characters of text sent for a summary or title; `-1` = no limit; audited with old and new value |
| `SUMMARY_MAX_TOKENS` (env) | `3000` | Maximum output tokens of a summary |
| `TITLE_MAX_TOKENS` (env) | `5000` | Maximum output tokens of a title |
| `CLINIC_LLM_TIMEOUT_SECONDS` / `CLINIC_LLM_MAX_RETRIES` (env) | `120` / `2` | Per-call limits in clinic mode |
| `TEXT_MODEL_BASE_URL` (env) | code default `https://openrouter.ai/api/v1`, used only when set or when a key exists | Text model endpoint; it and every set `CHAT_`/`TRANSLATION_`/`EMBEDDING_` URL must be local for the locked generative features |
| `CLINIC_LOCAL_MODEL_HOSTS` (env) | `speaches,host.docker.internal,localhost` | Comma-separated service names that count as local; a set value replaces the default list (loopback and private addresses always count) |
| `CLINIC_ASR_PROMPTS_ENABLED` (env) | off | Lets initial prompt and hotwords reach the recognizer (55-word cap) |

## Upstream reference

The upstream page explains the **Default prompts** editor, the "View full LLM prompt structure" view, prompt-writing strategies and creative tag prompt examples (recipes, lectures, code reviews, brainstorming). None of this is a clinic procedure: the editor is not rendered in clinic mode, and the examples do not fit clinical dictation.

Upstream Speakr documentation (pinned original, upstream only): [Default Prompts](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/prompts.md)
