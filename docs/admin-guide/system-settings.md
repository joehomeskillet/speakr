# System Settings
<small>[Deutsch](../de/admin-guide/system-settings.md)</small>

This page describes the global settings of the clinic build: the settings on the **Systemeinstellungen** (System settings) page and the environment variables that control the whole instance. It covers only what takes effect with clinic mode on (`CLINIC_MODE` set to `1`, `true`, `yes` or `on`). Settings that still appear but have no effect in clinic mode are listed as **Upstream only**.

Status terms: see [Status vocabulary](index.md#status-vocabulary).

## Where to find the page and who may use it

Open the administration from the account menu (**Admin**) or at `/admin`. In the navigation group **System**, choose **Systemeinstellungen**. The direct link is `/admin#/system/einstellungen`. The group also holds **Systemstatistik** (system statistics, see [System Statistics](statistics.md)) and **System-Auditprotokoll** (system audit log, see [Audit log](audit-log.md)). These navigation labels are German only.

Access depends on the role:

- **Site administration**: only an account with the **Administrationsrolle** (administration role) opens `/admin` and reads or changes system settings. The clinic route guard refuses every other account with `403` before the page loads.
- **Local identity administration**: the flag **Identitätsadministration** (identity administration) adds nothing on this page. It is needed for account changes, see [User Management](user-management.md).
- **Pool leads**: a pool lead (**Leitung**) has no access to `/admin` and cannot change system settings. See [Group Management](group-management.md).
- **Release and original-audio export**: the rights **Freigeben** (release) and **Originalaudio exportieren** (export original audio) are rights on dictations. They are separate from system settings and are granted per account, see [User Management](user-management.md).

In clinic mode, the upstream admin tabs User management, Default prompts, Groups and Vector store are not rendered. Clinic pages such as **Vorgaben** (defaults), **Spalten** (columns) and **Kategorien** (categories) are separate pages of the administration with their own rules. They are not system settings, see [Other administration pages](index.md#other-administration-pages).

## How the page works

At the top is the panel **Transcription models**. Below it, every stored system setting is shown as a card, sorted by its key. The card title is the key itself, for example `transcript_length_limit`. Only the three retention settings have a label: **Automatic archival**, **Archive after days** and **Archive and preserve all data**. Each card shows the value type and either **Set in .env** or **Last updated** with a date. **Refresh** reloads the list.

The edit icon opens the dialog **Edit system setting**:

- **Save changes** stores the new value.
- **Use .env value** removes a saved value of a setting that falls back to `.env`, so that the `.env` value applies again. The button appears only for those settings, and only while a value is saved.
- The dialog checks some values before saving: `transcript_length_limit` must be at least 1000 characters, or **No limit (use entire transcript)** stores `-1`. `max_file_size_mb` must be between 1 and 10000. `asr_timeout_seconds` is entered as minutes and seconds and must be between 60 and 36000 seconds.
- The server checks the type (`string`, `integer`, `boolean`, `float`), a valid IANA zone for `default_timezone`, and the allowed values of `default_ui_theme`, `default_color_scheme`, `default_audio_player_position`, `deletion_mode` and `permission_create_groups`.

A saved value is read from the database on the next use, without a restart. Environment variables without a setting on this page take effect only after a restart of the application.

These keys are not shown in the list, because other editors manage them or because Speakr writes them itself: `transcription_models_visible_json`, `transcription_default_model`, the prompt keys `admin_default_summary_prompt`, `admin_default_title_prompt`, `admin_default_initial_prompt`, `admin_default_hotwords` and `admin_default_contextual_speaker_prompt`, the temperatures `llm_temperature_*`, and the voice keys `voice_space_current`, `voice_space_legacy` and `voice_embedding_reference`. In clinic mode the Default prompts tab is not rendered, so these prompts have no editor in the interface. See [Default Prompts](prompts.md).

Some cards hold values that Speakr writes itself, for example `embedding_identifier` and `changes_feed_since`. They are not settings for administrators.

### What is logged

Every change, and every **Use .env value**, writes the audit event `admin.setting.changed` in the same transaction as the change. The event stores the key when the application defines it, otherwise `other`. Old and new value are stored only for these keys: `audit_log_enabled`, `audit_log_retention_days`, `audit_log_store_ip`, `audit_log_record_views`, `audit_log_notice_dismissed`, `users_can_delete`, `enable_auto_deletion`, `global_retention_days`, `deletion_mode`, `recording_tombstone_days`, `webhook_delivery_retention_days`, `webhook_omit_titles`, `auto_export_delete_with_recording`, `video_retention`, `video_passthrough_asr`, `asr_async_jobs`, `max_concurrent_uploads`, `session_idle_timeout_minutes`, `api_query_tokens_enabled`, `sso_api_tokens_enabled`, `login_max_failed_attempts`, `login_lockout_minutes`, `max_file_size_mb`, `default_storage_quota_mb` and `transcript_length_limit`. For every other key, for example `asr_timeout_seconds` or `custom_banner`, only the key is stored. A change of an `audit_log_*` setting is recorded also while the audit log is off. A write refused by the clinic ceiling below is not recorded. See [Audit log](audit-log.md).

## Clinic ceilings

Two upstream settings are capped by the clinic policy. While their clinic features are locked, the value read by the application is always `false`:

| Setting | Locking feature | Effective value |
|---|---|---|
| `api_query_tokens_enabled` | `token_query_parameters` | `false` |
| `sso_api_tokens_enabled` | `personal_api_tokens` | `false` |

Both features are `false` in the clinic contract file `clinic-spec/config/dev.defaults.json` ([clinic fork source](https://github.com/joehomeskillet/speakr/blob/main/clinic-spec/config/dev.defaults.json)). Clinic features are denied by default and are not switched in the administration. Saving any other value than `false` or an empty value is refused with `403` and the code `clinic_policy_ceiling`. At start, a conflicting stored value is logged as the warning `clinic policy overrides stored setting <key>=<value> (effective: False)`.

The card in the list shows the stored value or the `.env` value, not the effective one. `api_query_tokens_enabled` can therefore be shown as enabled although it is `false` in effect. In clinic mode, a request authenticated with an API token instead of a browser session is refused with `403 clinic_feature_disabled`. The data aspects are described in [Privacy and regulated data](privacy.md).

## Settings that take effect, by purpose

### Uploads and storage

| Setting | Default | Effect in the clinic build | Status |
|---|---|---|---|
| `max_file_size_mb` | `250` (set at first start) | Size limit of an audio upload in MB. | Available |
| `max_audio_only_video_size_mb` | 4 × `max_file_size_mb` at first start | Size limit of an upload with a video file extension. | Available |
| `default_storage_quota_mb` | `0` (no limit) | **Speicherlimit (MB)** (storage limit) given to accounts created afterwards in **Benutzer und Rollen** (users and roles). Existing accounts keep their own limit. | Available |
| `max_concurrent_uploads` | `3` (`MAX_CONCURRENT_UPLOADS`) | Parallel uploads of the stock upload dialog. The clinic view **Neue Aufnahme** (new recording) sends one file per upload. | Available |

The clinic view **Neue Aufnahme** sends browser recordings and selected files to the stock upload route `POST /upload`, so these limits apply to dictation uploads too. The rules are:

- The request ceiling is the higher of the two limits. It is recalculated when either value is saved, and it applies in every case.
- With chunking on (`ENABLE_CHUNKING`, default `true`, or chunking inside the connector), the per-type limit is not enforced, and only the request ceiling applies.
- Without chunking, the per-type limit is enforced. A file with a video extension first gets the video limit. If the probe then finds no video stream, the audio limit applies.
- Without chunking, a video kept as audio only whose extracted audio is larger than `max_file_size_mb` is refused with `413` (`extracted_audio_too_large`). If the conversion runs after the upload is answered, the recording fails with the same reason instead.
- A browser recording is sent as `aufnahme.webm`. `.webm` counts as a video extension until the probe has read the file.
- `.ds2` and `.dss` dictation files are stored as uploaded, without conversion or chunking. The transcription job decodes them.
- An upload over a limit is answered with `413`.

### Notices for users

| Setting | Default | Effect in the clinic build | Status |
|---|---|---|---|
| `recording_disclaimer` | empty (off) | Markdown notice. It must be accepted before a recording starts. The clinic view **Neue Aufnahme** uses the stock recording start, so the notice also appears there. | Available |
| `upload_disclaimer` | empty (off) | Markdown notice of the stock upload dialog. The clinic view **Neue Aufnahme** uploads files with its own upload and does not show it. | Available, not shown in the clinic recording view |
| `custom_banner` | empty (off) | Markdown banner above the main content of the clinic app. Users can close it; it returns when the page is reloaded. | Available |
| `help_center_url`, `help_center_label` | empty | Link in the account menu, opened in a new tab. Without a label, **Help center** is shown. | Available |

### Sign-in and sessions

| Setting | Default | Effect in the clinic build | Status |
|---|---|---|---|
| `login_max_failed_attempts` | `5` | Wrong passwords for one account from one client address before the sign-in lock. `0` turns the lock off. | Available |
| `login_lockout_minutes` | `15` | Duration of a lock in minutes. A value below 1 is read as 15. | Available |
| `session_idle_timeout_minutes` | `0` (`SESSION_IDLE_TIMEOUT_MINUTES`) | Minutes without a request after which a browser session ends. `0`: no idle timeout. | Available |

The clinic sign-in counts wrong passwords in two ways:

- Per account and client address. An IPv6 address counts as its /64 network.
- Per account for all addresses together, with four times the threshold.

A locked account gets the same message as a wrong password: **Anmeldung fehlgeschlagen.** (sign-in failed). A correct password before the limit resets both counts. A wrong one-time code at activation or reset counts against the account the code belongs to. When a lock starts, the audit event `auth.login.locked` is written.

To end a lock, open the account in **Benutzer und Rollen**, section **Konten** (accounts), tick **Anmeldesperre aufheben** (lift sign-in lock) and save the form. The option appears only while the account is locked. Like every account change, it needs the **Administrationsrolle** and **Identitätsadministration**, and it writes `admin.user.unlocked`. In addition, `/login` (sign-in and one-time-code redemption) accepts 10 requests per minute from one client address. Without `RATELIMIT_STORAGE_URI` (for example a `redis://` address), each worker process counts separately. `RATELIMIT_ENABLED=false` turns the limit off. Behind a reverse proxy, the client address comes from the forwarded headers. Set `TRUSTED_PROXY_HOPS` and `TRUSTED_PROXIES` as described in [Reverse-proxy hops](../getting-started/installation.md#reverse-proxy-hops).

The idle timeout works as follows:

- With `session_idle_timeout_minutes` above `0`, the time of the last request is kept in the signed session cookie. The next request after the timeout is treated as signed out.
- Requests that an open page sends by itself (notification count, job queue status, CSRF token refresh) do not count as activity.
- The keys `identity.session_idle_minutes` (15) and `identity.session_absolute_hours` (8) in the clinic contract file are not read by the application. Only `session_idle_timeout_minutes` sets the idle timeout.
- The clinic sign-in never sets the "Remember me" cookie. `REMEMBER_COOKIE_DAYS` therefore has no effect on clinic sign-ins.

Account administration has its own rules (one-time codes, re-authentication within 5 minutes before account changes). They are described in [User Management](user-management.md).

### Speech recognition

Speech recognition is local only. The connectors, the GPU-first and CPU-second order and the model variables are described in [Local speech recognition](whisperx-setup.md) and [Model Configuration](model-configuration.md).

| Setting | Default | Effect in the clinic build | Status |
|---|---|---|---|
| `asr_timeout_seconds` | `1800` | Timeout of the `asr_endpoint` connector (`ASR_BASE_URL`, WhisperX or whisper-asr-webservice) only. Order: `ASR_TIMEOUT` in the environment, then a variable named `asr_timeout_seconds`, then this setting, then 1800. It is read again for every request. | Available, `asr_endpoint` only |
| `asr_async_jobs` | `true` (`ASR_ASYNC_JOBS`) | The `asr_endpoint` connector uses the asynchronous jobs API of the WhisperX ASR service (0.5.0 and later) when the service lists it in its `/health` answer. Off: every transcription uses the synchronous `/asr` request. | Available, `asr_endpoint` only |

`asr_timeout_seconds` does not govern the OpenAI-compatible local path (`TRANSCRIPTION_BASE_URL`, for example Speaches). That path uses `TRANSCRIPTION_TIMEOUT` (default 3600 s in clinic mode) and `TRANSCRIPTION_MAX_RETRIES` (default `0` in clinic mode). A GPU target uses `CLINIC_GPU_ASR_TIMEOUT` (default 300 s).

The panel **Transcription models** can be read, but it cannot be used in clinic mode. **Discover** and **Save selection** call routes that are locked under the clinic feature `cloud_provider_selection`. They answer `403 clinic_feature_disabled`. **Add model ID** only adds a row in the browser; saving it fails in the same way.

In clinic batch jobs, every initial prompt and hotword hint (account, tag, folder and admin defaults) is dropped unless `CLINIC_ASR_PROMPTS_ENABLED` is on; then prompt and hotwords share a cap of 55 words. Hotwords and prompts bias the decoder; they are not acoustic training. See [Default Prompts](prompts.md).

### Text model and summaries

| Setting | Default | Effect in the clinic build | Status |
|---|---|---|---|
| `transcript_length_limit` | `30000` | Maximum number of transcript characters sent to the text model for an automatic title or a summary. `-1`: no limit. | Available |
| `disable_auto_summarization` | `false` | `true`: no automatic summary after a transcription. | Available |
| `disable_auto_translation` | `true` | `false`: users may turn on **Auto-translate new recordings** in their preferences. | Available |

In the released build, a clinic transcription job does not call the text model itself. It queues a follow-up job on the summary queue for the title and, if automatic summaries apply, the summary. Automatic summaries apply when `disable_auto_summarization` is not `true`, the account's own switch is not off, and a text-model client exists. In clinic mode the account page does not show the **Auto-summarize** switch (the tab **Prompt options** is hidden), so each account keeps its stored value; the default is on.

Open gap: the automatic title, the automatic summary and the automatic translation job have no locality check of their own. Their routes for manual use are locked unless every text-model URL is local. The follow-up job, however, sends the transcript to whatever text model is configured. The title is generated whenever a text-model client exists and the recording has no user-chosen title (a file selected in **Neue Aufnahme** is sent without a title), even with `disable_auto_summarization` set to `true`. A text-model client exists as soon as `TEXT_MODEL_BASE_URL` or `TEXT_MODEL_API_KEY` is set; without `TEXT_MODEL_BASE_URL` it uses a hosted default address. Keep `TEXT_MODEL_BASE_URL` and every other model URL that is set on local services, see [Model Configuration](model-configuration.md). If that is not possible, leave both `TEXT_MODEL_BASE_URL` and `TEXT_MODEL_API_KEY` unset, so that no text-model client exists and no title or summary is generated, and leave `disable_auto_translation` at `true`.

### Retention and audit log

| Setting | Default | Effect in the clinic build | Status |
|---|---|---|---|
| `enable_auto_deletion` (**Automatic archival**) | `false` (`ENABLE_AUTO_DELETION`) | Turns on the daily archival run. It archives and never deletes. | Default off |
| `global_retention_days` (**Archive after days**) | `0` (`GLOBAL_RETENTION_DAYS`) | Days since creation before archival. `0`: only tag periods apply. | Available |
| `deletion_mode` (**Archive and preserve all data**) | `archive` (`DELETION_MODE`) | Legacy key. `archive`, `audio_only` and `full_recording` all archive and keep every file and row. | Available |
| `recording_tombstone_days` | `90` (`RECORDING_TOMBSTONE_DAYS`) | Days to keep changes-feed tombstones. Unchanged from upstream. | Available |
| `audit_log_enabled` | `true` (`AUDIT_LOG_ENABLED`) | General switch of the audit log. Forced clinic rows are written regardless. While it is off, administrators cannot open the transcript or audio of other users' dictations (`503 audit_unavailable`). | Available |
| `audit_log_retention_days` | `365` (`AUDIT_LOG_RETENTION_DAYS`) | Pruning cutoff. In clinic mode the daily pruning removes nothing and only logs how many rows it kept. | Available; pruning: Upstream only |
| `audit_log_store_ip` | `full` (`AUDIT_LOG_STORE_IP`) | `full`, `truncated` (IPv4 /24, IPv6 /48) or `off`. | Available |
| `audit_log_record_views` | `true` (`AUDIT_LOG_RECORD_VIEWS`) | `false` drops view and play events. Administrators then cannot open the audio of other users' dictations (`503 audit_unavailable`). | Available |
| `audit_log_notice_dismissed` | `false` | Whether the notice on the audit log page was dismissed. No other effect. | Available |

The recording that anchors a clinic dictation is created as exempt from automatic archival, so the retention run never archives a dictation. The details are in [Retention](retention.md), [Audit log](audit-log.md) and [Privacy and regulated data](privacy.md).

### Defaults for new accounts

| Setting | Default | Effect | Status |
|---|---|---|---|
| `default_timezone` | `UTC`, or the legacy `TIMEZONE` value at first start | Timezone for dates in generated titles for users whose browser has not reported one and who have not chosen one. | Available |
| `default_ui_theme` | `cards` (`DEFAULT_UI_THEME`) | Interface theme stored on accounts created afterwards. Allowed: `default`, `classic`, `workspace`, `focus`, `desk`, `cards`. | Available |
| `default_color_scheme` | `emerald` (`DEFAULT_COLOR_SCHEME`) | Color scheme stored on accounts created afterwards. Allowed: `blue`, `emerald`, `purple`, `rose`, `amber`, `teal`. | Available |
| `default_audio_player_position` | `top` (`DEFAULT_AUDIO_PLAYER_POSITION`) | Audio player position stored on accounts created afterwards: `top` or `bottom`. | Available |

The clinic account creation applies these three values to every new account. Existing accounts keep their own values.

## Video uploads and `VIDEO_PASSTHROUGH_ASR`

| Setting | Variable | Default | Status |
|---|---|---|---|
| `video_retention` | `VIDEO_RETENTION` | `false` | Default off |
| `video_passthrough_asr` | `VIDEO_PASSTHROUGH_ASR` | `false` | Default off |

The clinic view **Neue Aufnahme** offers audio files only. It accepts audio MIME types and the extensions `.wav`, `.mp3`, `.m4a`, `.aac`, `.ogg`, `.oga`, `.opus`, `.flac`, `.webm`, `.wma`, `.ds2` and `.dss`. A WebM file can still contain a video stream, and the upload route behind the view handles video as upstream does. A video can therefore reach the transcription only as such a file or through a direct request with a browser session to a stock upload route (`/upload`, `/share-target`, `/api/v1/recordings/upload`).

For such a file the two settings work as in upstream:

- With `VIDEO_RETENTION` on, the video stream is kept for playback. Audio is extracted to a temporary file for the transcription only.
- With `VIDEO_PASSTHROUGH_ASR` on, the original video file goes unchanged to the configured local ASR endpoint. Audio extraction, codec conversion and chunking are skipped. Audio uploads are not affected. In clinic mode the endpoint is the local connector, with the GPU-first and CPU-second order when a GPU target is set.

Turn `VIDEO_PASSTHROUGH_ASR` on only if your local ASR endpoint accepts video files. Standard ASR APIs, such as OpenAI-compatible transcription endpoints, reject raw video input, and the transcription of that file then fails with the error of the endpoint.

## Upstream settings without effect in clinic mode

These cards still appear in the list. Their values do not change the clinic build.

| Setting | Reason | Status |
|---|---|---|
| `permission_create_groups` | Upstream groups are locked (feature `internal_sharing`). | Upstream only |
| `users_can_delete` (`USERS_CAN_DELETE`) | Deleting recordings is locked in clinic mode (feature `clinical_user_hard_delete`). | Upstream only |
| `webhook_delivery_retention_days`, `webhook_omit_titles` | User webhooks are locked (feature `user_webhooks`), see [Webhooks](webhooks.md). | Upstream only |
| `sso_api_allowed_clients`, `sso_api_exchange_clients`, `sso_api_scopes`, `sso_api_client_scopes`, `sso_api_client_names` | Upstream SSO sign-in refuses in clinic mode, and API tokens are locked, see [Sign-in and identity providers](sso-setup.md). | Upstream only |
| `auto_export_delete_with_recording` | Belongs to the upstream automatic export (`ENABLE_AUTO_EXPORT`), and recordings cannot be deleted in clinic mode. | Upstream only |

`enable_folders` (default `false`) belongs to the upstream folders, but its value still has an effect. Leave it at `false`. With `true`, the stock account page **Konto** (account) shows a **Folders** subtab under **Tags & folders**, and the folder routes accept changes, so users can create folders with their own prompts and ASR defaults. The clinic views offer no folders. Status: Upstream only, keep off.

Upstream sections on incognito mode, What's New notices, public share pages and the user search for sharing have no counterpart in this build.

## Settings that fall back to .env

The settings below can be set on this page or in `.env`. Until an administrator saves a value on this page, the `.env` value applies and the card shows **Set in .env**. A saved value takes effect without a restart, and later changes to the variable are ignored. **Use .env value** returns to the variable. In `.env`, only `true` (any case) turns these booleans on.

| Setting | Variable | Default | Status |
|---|---|---|---|
| `api_query_tokens_enabled` | `API_QUERY_TOKENS_ENABLED` | `true` | Forced `false` by the clinic ceiling |
| `asr_async_jobs` | `ASR_ASYNC_JOBS` | `true` | Available, `asr_endpoint` only |
| `audit_log_enabled` | `AUDIT_LOG_ENABLED` | `true` | Available |
| `audit_log_record_views` | `AUDIT_LOG_RECORD_VIEWS` | `true` | Available |
| `audit_log_retention_days` | `AUDIT_LOG_RETENTION_DAYS` | `365` | Available; pruning: Upstream only |
| `audit_log_store_ip` | `AUDIT_LOG_STORE_IP` | `full` | Available |
| `auto_export_delete_with_recording` | `AUTO_EXPORT_DELETE_WITH_RECORDING` | `false` | Upstream only |
| `default_audio_player_position` | `DEFAULT_AUDIO_PLAYER_POSITION` | `top` | Available |
| `default_color_scheme` | `DEFAULT_COLOR_SCHEME` | `emerald` | Available |
| `default_ui_theme` | `DEFAULT_UI_THEME` | `cards` | Available |
| `deletion_mode` | `DELETION_MODE` | `archive` | Available, archives only |
| `enable_auto_deletion` | `ENABLE_AUTO_DELETION` | `false` | Default off |
| `global_retention_days` | `GLOBAL_RETENTION_DAYS` | `0` | Available |
| `help_center_label` | `HELP_CENTER_LABEL` | empty | Available |
| `help_center_url` | `HELP_CENTER_URL` | empty | Available |
| `max_concurrent_uploads` | `MAX_CONCURRENT_UPLOADS` | `3` | Available |
| `recording_tombstone_days` | `RECORDING_TOMBSTONE_DAYS` | `90` | Available |
| `session_idle_timeout_minutes` | `SESSION_IDLE_TIMEOUT_MINUTES` | `0` | Available |
| `sso_api_allowed_clients`, `sso_api_client_names`, `sso_api_client_scopes`, `sso_api_exchange_clients`, `sso_api_scopes` | `SSO_API_ALLOWED_CLIENTS`, `SSO_API_CLIENT_NAMES`, `SSO_API_CLIENT_SCOPES`, `SSO_API_EXCHANGE_CLIENTS`, `SSO_API_SCOPES` | empty; `read` for scopes | Upstream only |
| `sso_api_tokens_enabled` | `SSO_API_TOKENS_ENABLED` | `false` | Forced `false` by the clinic ceiling |
| `users_can_delete` | `USERS_CAN_DELETE` | `true` | Upstream only |
| `video_passthrough_asr` | `VIDEO_PASSTHROUGH_ASR` | `false` | Default off |
| `video_retention` | `VIDEO_RETENTION` | `false` | Default off |
| `webhook_delivery_retention_days` | `WEBHOOK_DELIVERY_RETENTION_DAYS` | `30` | Upstream only |
| `webhook_omit_titles` | `WEBHOOK_OMIT_TITLES` | `false` | Upstream only |

## Environment variables

These variables are set in the environment of the application and are not shown on the page. Change them and restart the application.

### Clinic mode

| Variable | Default | Effect | Status |
|---|---|---|---|
| `CLINIC_MODE` | unset (off) | `1`, `true`, `yes` or `on` turns on clinic mode: the route guard, the feature locks, the setting ceilings and the clinic administration. Read on every call. | Available |
| `CLINIC_CONFIG_DIR` | `clinic-spec/config` in the repository root | Directory of the contract files `dev.defaults.json`, `client-capabilities.json` and `import-agent-boundary.json`; the web application reads only the first two. A missing or unreadable file is an error when the policy is read; clinic mode never falls back to an open or unknown policy. A feature is usable only when the `features` map of `dev.defaults.json` sets it to `true`. Exceptions: `chat`, `inquire` and `generative_summary` are unlocked only by a local text model instead, see [Model Configuration](model-configuration.md). | Available |
| `CLINIC_WEB_ONLY` | `0` | `1`: a process that serves HTTP only, for an already initialized clinic deployment. It starts no queue and no background work, and it requires clinic mode. Any other value than `0` or `1` is refused. | Available |
| `CLINIC_PROFILE` | unset | `staging` uses the synthetic KIS adapter, so that final release can be tested with synthetic case data. Any other value keeps the KIS adapter disabled, and final release stays closed. `production`: the synthetic `clinic_stub` connector and the KIS mock refuse to run. | Simulated (`staging`) |

### Speech recognition and text model

Set and described in [Local speech recognition](whisperx-setup.md) and [Model Configuration](model-configuration.md): `TRANSCRIPTION_CONNECTOR`, `TRANSCRIPTION_BASE_URL`, `TRANSCRIPTION_MODEL`, `TRANSCRIPTION_TIMEOUT` and `TRANSCRIPTION_MAX_RETRIES`, the `CLINIC_GPU_ASR_*` variables, `CLINIC_ASR_PROMPTS_ENABLED`, `ASR_BASE_URL` and `ASR_TIMEOUT`, `TEXT_MODEL_BASE_URL`, `TEXT_MODEL_NAME`, `CLINIC_LOCAL_MODEL_HOSTS`, `CLINIC_LLM_TIMEOUT_SECONDS` (default 120) and `CLINIC_LLM_MAX_RETRIES` (default 2).

### Files, dictation formats and import

| Variable | Default | Effect | Status |
|---|---|---|---|
| `CLINIC_ORIGINALS_ROOT` | `UPLOAD_FOLDER` | Local root of the unchanged dictation originals, also with `FILE_STORAGE_BACKEND=s3`. See [File Storage](storage.md). | Available |
| `CLINIC_DSS_CODEC_BIN` | `/usr/local/bin/dss-decode` in the Docker image | Path of the pinned `dss-decode` binary. Without it, an uploaded `.ds2` or `.dss` file is stored, but its recording ends as failed. | Available |
| `CLINIC_IMPORT_ROOT_ALLOWLIST` | empty | Absolute roots, separated by `;`, inside which an approved import source must lie. Empty: every source root is refused. See [Device and folder import](recording-sessions.md#device-and-folder-import-admin-page-import). | Available |
| `CLINIC_AGENT_PROXY_SECRET`, `CLINIC_AGENT_MASTER_SECRET` | unset | Machine secrets of the Windows import agent, at least 32 bytes each. If one is missing or shorter than 32 bytes, a warning is logged and every agent request is answered with `401`. The channel itself is Prepared, disabled: no supported route, command or script registers an agent. Ingress contract: [Recording sessions](recording-sessions.md#import-agent-channel-and-ingress). | Prepared, disabled |

### Accounts

`CLINIC_PASSWORD_BLOCKLIST_FILE` (site password blocklist) and `CLINIC_BOOTSTRAP_PASSWORD` (password of the first administrator) are described in [User Management](user-management.md).

### Background processing

| Variable | Default | Effect |
|---|---|---|
| `JOB_QUEUE_WORKERS` | `2` | Threads for transcription jobs. |
| `SUMMARY_QUEUE_WORKERS` | `2` | Threads of the summary queue, which also runs the clinic title and summary follow-up jobs. |
| `CONVERT_QUEUE_WORKERS` | `2` | Threads that convert uploads after the upload is answered. `0` converts during the upload request. |
| `INDEX_QUEUE_WORKERS` | `1` | Threads for Inquire search chunks (upstream; only with `ENABLE_INQUIRE_MODE`). |
| `JOB_MAX_RETRIES` | `3` | Maximum attempts per job for transient errors (default 3 = at most three attempts in total). A permanent error fails the job at once. |

The workers are threads in the one process that owns the job queue. The other processes serve requests and queue jobs. In clinic mode, a process that lost the election tries again every 15 seconds, so another process takes over the queue at most 15 seconds plus the 120-second job lease after the owner has stopped.

With `CONVERT_QUEUE_WORKERS=0`, a web upload goes through the conversion step (codec conversion and, with `AUDIO_COMPRESS_UPLOADS`, compression of lossless audio) during the request, before its clinic original is stored. The clinic original then holds the file as it is after that step. With the default, a readable audio file reaches the clinic original as uploaded, and the conversion runs afterwards. `.ds2` and `.dss` files are never converted.

### Upload processing and cookies

| Variable | Default | Effect |
|---|---|---|
| `ENABLE_CHUNKING` | `true` | Server-side chunking for the ASR call; it also lifts the per-type upload limit. |
| `AUDIO_COMPRESS_UPLOADS` | `true` | Compresses lossless uploads (WAV, AIFF) in the conversion step. |
| `AUDIO_CODEC` | `mp3` | Target codec: `mp3`, `flac` or `opus`. |
| `AUDIO_BITRATE` | `128k` | Bitrate for lossy codecs. |
| `AUDIO_UNSUPPORTED_CODECS` | empty | Comma-separated codec names (for example `opus,vorbis`) removed from the list of codecs the transcription connector accepts as is; files in such a codec are converted in the conversion step. |
| `FFMPEG_TIMEOUT_SECONDS` | `3600` | Upper limit in seconds for each FFmpeg conversion and chunking subprocess; values below `30` count as `30`. Raise it for very long dictations on slow hardware. |
| `FFPROBE_TIMEOUT_SECONDS` | `60` | Upper limit in seconds for each FFprobe call that reads duration and codec; values below `10` count as `10`. |
| `SESSION_COOKIE_SECURE` | `false` | `true`: the session cookie is sent over HTTPS only. Set it for every deployment behind TLS. With `true`, sign-in over plain HTTP fails because the browser drops the cookie. A warning is logged when Speakr is reached over HTTPS while it is `false`, and also at startup when `APP_BASE_URL` is an https address and the value is `false`. |

### Upstream functions without a clinic check

| Variable | Default | Note | Status |
|---|---|---|---|
| `ENABLE_AUTO_EXPORT` | `false` | Upstream automatic export of each completed recording to a folder. This path has no clinic check. Clinic output goes through the release-bound **Ausgabe** (output) view. | Upstream only |
| `ENABLE_AUTO_PROCESSING` | `false` | Upstream watch folder. With `true`, a background monitor ingests every file dropped into the watch folder through the shared upload path, without upload limits and without a clinic check, and then deletes the file from the folder. It is a second local import path outside the import-agent boundary. Only the admin start, stop, trigger and configuration routes are locked (feature `auto_process`); the status route stays readable for site administrators. | Upstream only; keep unset |
| `AUTO_PROCESS_WATCH_DIR` | `/data/auto-process` | Watch folder of `ENABLE_AUTO_PROCESSING`. | Upstream only; keep unset |
| `AUTO_PROCESS_MODE` | `admin_only` | `admin_only` (files belong to the first account with the **Administrationsrolle**), `user_directories` or `single_user`. | Upstream only; keep unset |
| `AUTO_PROCESS_DEFAULT_USERNAME` | unset | Owner account for `single_user`. | Upstream only; keep unset |
| `AUTO_PROCESS_CHECK_INTERVAL` | `30` | Seconds between two scans of the watch folder. | Upstream only; keep unset |
| `AUTO_PROCESS_STABILITY_TIME` | `5` | Seconds a file size must stay unchanged before the file is ingested. | Upstream only; keep unset |
| `ENABLE_INQUIRE_MODE` | `false` | Upstream Inquire. In clinic mode it has no interface, but with `true` every completed recording is queued for indexing. The clinic template `clinic-spec/config/upstream.env.example` sets `false`. See [Vector Store](vector-store.md). | Upstream only |
| `DELETE_ORPHANED_SPEAKERS` | `false` | Belongs to the upstream speaker profiles, which are locked in clinic mode (feature `speaker_identification`). | Upstream only |
| `SKIP_EMAIL_DOMAIN_CHECK` | `false` | Read by the upstream admin script `scripts/create_admin.py`. The first administrator is created with `scripts/clinic_bootstrap_admin.py`, see [User Management](user-management.md). | Upstream only |

### Example

```bash
# Clinic mode
CLINIC_MODE=1

# HTTPS deployment
SESSION_COOKIE_SECURE=true

# Background processing (defaults)
JOB_QUEUE_WORKERS=2
SUMMARY_QUEUE_WORKERS=2
CONVERT_QUEUE_WORKERS=2
JOB_MAX_RETRIES=3

# Video passthrough (only for a local ASR endpoint that accepts video)
# VIDEO_PASSTHROUGH_ASR=false
```

## In verification: model selection and automatic dictation summaries

!!! note "In verification"
    The following is not part of the current released build. Labels and availability may change.

    - A separate page **Modellwahl** (model selection) in the group **System**, at `/admin#/system/modelle`, between **Systemeinstellungen** and **System-Auditprotokoll**. Only active site administrators can use it. It is described in [Model Configuration](model-configuration.md).
    - In clinic mode, the panel **Transcription models** is renamed **Modellkatalog für die Aufnahme** (model catalog for recording). Its description says that the catalog default applies to CPU transcription while no own CPU model is set in **Modellwahl**.
    - The selection is stored in the setting `clinic_task_models`. This key is hidden from the list, and a write through this page is refused with `400 use_task_models_api`. A change writes `admin.setting.changed` with the key only.
    - An automatic dictation summary is queued only for the first recognized text of a new dictation, only with a local text model, and only when `disable_auto_summarization` is not `true` and the author's own switch is not off. Existing dictations get no automatic summary.

## Troubleshooting

- **Upload refused with 413**: the file is over the request ceiling or, without chunking, over `max_file_size_mb` or `max_audio_only_video_size_mb`, or the extracted audio of a video is over `max_file_size_mb` (`extracted_audio_too_large`). The ingest check logs a line starting with `Upload failed: File too large`; a request above the request ceiling can be refused earlier without that line.
- **Save refused with 403 `clinic_policy_ceiling`**: the key is capped by the clinic policy, see [Clinic ceilings](#clinic-ceilings).
- **Discover or Save selection answers 403 `clinic_feature_disabled`**: the transcription model panel is locked in clinic mode.
- **A long WhisperX transcription times out**: raise `asr_timeout_seconds`, or check whether `ASR_TIMEOUT` is set, because it takes precedence. For Speaches or another OpenAI-compatible service, change `TRANSCRIPTION_TIMEOUT` instead.
- **A value in `.env` has no effect**: a value saved on this page takes precedence. Use **Use .env value**.

---

Upstream Speakr documentation (pinned original, not the clinic build): [System Settings](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/system-settings.md).
