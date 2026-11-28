# System settings

[Deutsch](../de/admin-guide/system-settings.md)

Open **Verwaltung → System → Systemeinstellungen**, at `/admin#/system/einstellungen`, as a site administrator. Identity administration and pool leadership do not independently grant access to this page. Dictation rights such as release and original-audio export remain separate; see [User management](user-management.md).

## Change and verify a setting

1. Record the current value and its scope before changing it.
2. Open the setting's edit dialog and save one change.
3. Reload the list and verify the saved value.
4. Test the affected operation with synthetic data. Inspect `admin.setting.changed` in the [audit log](audit-log.md) when investigating a change.

For supported settings, a database value overrides its environment fallback. **Use .env value** clears that override; the button is available only for environment-backed settings. Environment-only configuration is applied through the deployment's restart procedure. System settings are not a replacement for deployment configuration.

Changes record the setting key, and only an allowlist of settings records old and new values. Audit-setting changes are recorded even when the general audit switch is off. Do not expect secret values or complete setting contents in the audit log.

## Uploads, storage and notices

| Setting | Default | Effect |
|---|---|---|
| `max_file_size_mb` | `250` at first start | Audio upload limit |
| `max_audio_only_video_size_mb` | 4 × `max_file_size_mb` at first start | Video upload limit |
| `default_storage_quota_mb` | `0`, no limit | Default for newly created accounts; existing accounts retain their own quota |
| `max_concurrent_uploads` | `3` | Parallel uploads in the inherited upload dialog; the clinic recorder submits one file per upload |
| `recording_disclaimer` | empty | Notice accepted before starting a recording |
| `upload_disclaimer` | empty | Notice in the inherited upload dialog; the clinic recording page uses its own file-upload flow |
| `custom_banner` | empty | Dismissible banner on the main page |
| `help_center_url`, `help_center_label` | empty | Optional help link in the account menu |

The application request ceiling is the larger of the audio and video limits. Saving either limit recalculates it. With chunking enabled, the per-file limit can be bypassed by the ingestion rules, but the request ceiling still applies. Without chunking, the relevant file limit applies; extracted video audio can also be rejected as `extracted_audio_too_large`. Proxy limits and account storage quotas are separate.

The clinic recorder sends a whole take through `/upload`. Size the proxy for that request, not for server-session chunk sizes. See [Recording sessions](recording-sessions.md).

## Sign-in, sessions and account defaults

| Setting | Default | Effect |
|---|---|---|
| `login_max_failed_attempts` | `5` | Wrong-password threshold per account and client address; `0` disables the lock |
| `login_lockout_minutes` | `15` | Lock duration |
| `session_idle_timeout_minutes` | `0` | Idle-session timeout; `0` means no idle timeout |
| `default_timezone` | `UTC`, or the legacy `TIMEZONE` value at first start | Fallback timezone |
| `default_ui_theme` | `cards` | Theme for new accounts |
| `default_color_scheme` | `emerald` | Color scheme for new accounts |
| `default_audio_player_position` | `top` | Player position for new accounts |

The idle timeout counts user activity, not the application's excluded background polling requests. Account lock recovery and changes to existing accounts belong in **Benutzer und Rollen**, under their separate identity-administration checks. The appearance defaults do not rewrite existing accounts. See [User management](user-management.md) and [Sign-in](sso-setup.md).

## Speech recognition and summaries

Use **System → Modellwahl** for GPU and CPU ASR model identifiers and the dictation-summary text model. That page is part of this build. Service URLs and credentials stay in deployment configuration. The internal `clinic_task_models` setting is hidden from the general list; writes through the general settings endpoint are refused with `use_task_models_api`.

The inherited **Modellkatalog für die Aufnahme** panel still appears on the settings page. Its discovery and save routes are locked by `cloud_provider_selection` in clinic mode and can return `403 clinic_feature_disabled`. Adding a row in the browser does not mean it was saved. Use the clinic **Modellwahl** page for task assignments; see [Model configuration](model-configuration.md).

| Setting | Default | Effect |
|---|---|---|
| `asr_timeout_seconds` | `1800` seconds | `asr_endpoint` timeout only; `ASR_TIMEOUT` and legacy environment variable `asr_timeout_seconds` take precedence |
| `asr_async_jobs` | `true` | Use the WhisperX jobs API when the service advertises it |
| `transcript_length_limit` | `30000` characters | Transcript limit for text-model calls; `-1` removes this limit |
| `disable_auto_summarization` | `false` | Disable automatic summaries when `true` |

The OpenAI-compatible CPU connector instead uses `TRANSCRIPTION_TIMEOUT` and `TRANSCRIPTION_MAX_RETRIES`; the GPU target uses `CLINIC_GPU_ASR_TIMEOUT`. See [Local speech recognition](whisperx-setup.md).

Automatic clinic summaries are bound to a text revision and are queued for the first recognised text of a new dictation only when the local model, author permissions and automatic-summary preferences permit it. The system switch and the author's stored automatic-summary preference both apply. Manual generation uses the latest saved text. Existing dictations do not receive automatic summaries merely because the feature is present. See [Model configuration](model-configuration.md) and [Prompts](prompts.md) for the separate title path and model-locality limits.

## Archiving and audit

The clinic fork does not offer hard automatic deletion. The inherited retention keys now control archival and preserve recording files and data:

| Setting | Default | Clinic meaning |
|---|---|---|
| `enable_auto_deletion` | `false` | **Automatische Archivierung**: enable the daily archival run |
| `global_retention_days` | `0` | **Archivieren nach Tagen**: age since creation; `0` uses only tag periods |
| `deletion_mode` | `archive` | Preserve files and data; legacy `audio_only` and `full_recording` values also archive |
| `audit_log_enabled` | `true` | General audit switch; mandatory clinic events have additional rules |
| `audit_log_retention_days` | `365` | Retention value; clinic-mode audit pruning is withheld |
| `audit_log_store_ip` | `full` | `full`, `truncated` or `off` |
| `audit_log_record_views` | `true` | View and playback event recording |

Clinic dictation workflow archiving is separate from the inherited recording sweep. Do not assume an archived item is a backup or that changing audit retention deletes clinic audit rows. See [Retention](retention.md), [Audit log](audit-log.md) and [Storage](storage.md).

## Video input

`video_passthrough_asr` / `VIDEO_PASSTHROUGH_ASR` defaults to `false`. In the video upload path, enabling it sends the original video to the configured ASR backend without ordinary extraction, conversion or chunking. Audio-only uploads are unaffected. Enable it only for an approved local connector tested with that container; ordinary audio-only transcription endpoints reject video.

This is separate from `video_retention` / `VIDEO_RETENTION`, also off by default, which controls keeping the video stream for playback. The clinic recording page offers audio files, but a `.webm` file can still contain video.

## Clinic policy limits and deployment settings

While their clinic features are locked, `api_query_tokens_enabled` and `sso_api_tokens_enabled` are effectively `false`. Attempts to enable them return `403 clinic_policy_ceiling`. A displayed stored value is not proof that a feature is usable.

The clinic fork does not offer public or internal shares, webhooks, general API v1 access, incognito mode, cloud ASR, organizer folders, self-registration, mail of any kind (no SMTP, no notification mail, no EML download; notifications are in-app only), password reset by mail, calendar extraction or a What's-New popup. API tokens are reserved for the import-agent purpose. Inherited settings do not restore these removed features.

Operations manages these values outside the settings page:

| Configuration | Purpose |
|---|---|
| `CLINIC_MODE`, `CLINIC_CONFIG_DIR` | Clinic policy and contract location |
| `TRANSCRIPTION_*`, `ASR_*`, `CLINIC_GPU_ASR_*` | Local recognition endpoints and connector settings |
| `TEXT_MODEL_BASE_URL`, `TEXT_MODEL_NAME`, `TEXT_MODEL_API_KEY` | Local text service; never set a key alone with no approved local URL |
| `CLINIC_ORIGINALS_ROOT` | Durable originals store; defaults to `UPLOAD_FOLDER` |
| `CLINIC_DSS_CODEC_BIN` | Decoder for `.ds2` and `.dss` |
| `JOB_QUEUE_WORKERS`, `SUMMARY_QUEUE_WORKERS`, `CONVERT_QUEUE_WORKERS` | Worker counts, each defaulting to `2` |
| `JOB_MAX_RETRIES` | Processing attempts, default `3` |
| `SESSION_COOKIE_SECURE`, trusted-proxy configuration | HTTPS cookies and trusted forwarded headers |

Keep upstream automatic watch-folder ingestion, automatic exports and Inquire indexing off. Their presence in inherited code is not an approved clinic workflow. Local file import belongs to the import agent. See [Migration](migration-guide.md) before carrying environment values from an upstream installation forward.
