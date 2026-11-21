# Privacy and regulated data
<small>[Deutsch](../de/admin-guide/privacy.md)</small>

This page is for administrators of the web-only clinic dictation fork of Speakr v0.11.1 (pinned upstream base `48d3018`). It lists what the clinic build stores, sends to other services and keeps in the browser, which upstream features that would send or expose data are removed or locked, and where the evidence ends. The hosting, the surrounding systems and the people with access are outside the scope of this page. Nothing on this page is legal advice.

Everything below applies only when `CLINIC_MODE` is `1`, `true`, `yes` or `on`. The switch is read on every call. With clinic mode off, the route guard, the feature locks and the clinic-mode checks are inert. Features removed from the code stay absent, and the integrity triggers stay installed. This manual does not cover operation with clinic mode off.

## Status and evidence limits

- No compliance with HIPAA, the GDPR, the Swiss Federal Act on Data Protection or any other law or regulation is claimed for this build, and it is not certified under any of them. The build has no clinical approval, is not declared production-ready and does not reach 1:1 parity with the legacy dictation product.
- The contract file that clinic mode loads, `clinic-spec/config/dev.defaults.json`, is the development profile `local-web-parity-lab` with `synthetic_only: true`. Its `privacy` block sets `production_approved`, `allow_real_data` and `retention_policy_approved` to `false`.
- `clinic-spec/config/production.template.json` is not read at runtime. It has `enabled: false`, an empty `approved_gates` list, and `null` for every retention value (`policy_reference`, `audio_days`, `transcript_days`, `audit_days`, `backup_days`) and every recovery value (RPO, RTO, restore evidence).
- Tests with mocks, fixtures or synthetic data are not evidence for Philips devices, Microsoft Entra, a KIS (hospital information system) or foot pedals.
- The application does not encrypt data at rest. Database content, original files, working copies and exported files are stored as plain data.
- The web-parity gaps in `clinic-spec/planning/web-parity-gaps.json` stay open. The gaps that matter for privacy are named where they apply.

Status terms: see [Status vocabulary](index.md#status-vocabulary).

## Default deny in clinic mode

- Every request endpoint is looked up in the clinic route-policy table. An endpoint that the table does not list is refused with 403 `clinic_unclassified_route` and a warning log line.
- A feature is usable only when the `features` map of `dev.defaults.json` (or of the directory named by `CLINIC_CONFIG_DIR`) names it with the value `true`. A name that the file does not know is locked. A route of a locked feature answers 403 `clinic_feature_disabled`.
- The switches are in the contract file, not in the admin interface. Every upstream feature that sends or exposes data is `false` there: `public_sharing`, `personal_api_tokens`, `token_query_parameters`, `user_webhooks`, `auto_deletion`, `email_exports`, `speaker_identification` and others. `internal_sharing`, `push_notifications` and `cloud_provider_selection` are not named and therefore locked. This manual gives no instruction to change these values.
- Chat, Inquire and the generative summary do not follow their flag. They depend only on a local text model (see [Optional local AI](#optional-local-ai)).

## Local-only processing

### Speech recognition

Status: Available. Speech recognition runs on services that the institution operates. There is no cloud speech recognition and no cloud fallback.

- Only three transcription connectors are registered: `openai_whisper` (an OpenAI-compatible local service such as Speaches, address in `TRANSCRIPTION_BASE_URL`), `asr_endpoint` (WhisperX or whisper-asr-webservice, address in `ASR_BASE_URL`) and `clinic_stub`. All other upstream connectors are removed (Upstream only). Any other `TRANSCRIPTION_CONNECTOR` value fails with `Unknown connector: <name>`.
- The contract declares `asr.cloud_fallback`, `asr.realtime_cloud_fallback`, `asr.diarization` and `asr.llm_postprocessing` as `false`. The code does not read these values; the behavior described on this page comes from the code. The only fallback in the code goes from a local GPU service to the local CPU service.
- In clinic mode the `openai_whisper` connector fails closed. An empty or non-local `TRANSCRIPTION_BASE_URL` stops the connector with `CLINIC_MODE is active: transcription base URL must point to a local or private service ...`. This includes the OpenAI default address. Local means: a loopback address (`localhost`, `127.0.0.0/8`, `::1`), a private address (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `fc00::/7`), a link-local address (`169.254.0.0/16`, `fe80::/10`), a host name without a dot, or a name that ends in `.local`, `.internal`, `.lan` or `.localhost`.
- Open gap: the `asr_endpoint` connector checks only that `ASR_BASE_URL` is set. Its address is not checked for locality in clinic mode. Point `ASR_BASE_URL` only at a host of the institution.
- Default off: GPU first, CPU second. With `CLINIC_GPU_ASR_BASE_URL` set, a job goes to the GPU service first. On a timeout, a connection error, an HTTP 5xx answer or an unhealthy `/healthz`, the same job runs once on the CPU service. The GPU target uses the same connector class, so the same locality check applies to its address.
- Default off: transcription hints. In clinic mode the initial prompt and the hotwords are dropped from every source (admin defaults, user, tag, folder) unless `CLINIC_ASR_PROMPTS_ENABLED` is on. Then prompt and hotwords share a cap of 55 words.
- Hotwords and profile settings only bias the decoding, and entries of the vocabulary (**Wortschatz** (vocabulary)) are not sent with the batch recognition request. None of them is acoustic training. Runs started with **Optimierung starten** (start optimization) are refused with `CAPABILITY_UNAVAILABLE`, and no model is trained or adapted.
- `clinic_stub` returns fixed synthetic sentences. It is a test double and refuses to run with `CLINIC_PROFILE=production`.
- At startup one warning per service is logged when `ASR_BASE_URL`, `TRANSCRIPTION_BASE_URL`, a text, chat or translation model address or `EMBEDDING_BASE_URL` uses plain `http://` to another machine. Private ranges are reported too, because the content still crosses a network. The warning does not block the start.
- The recognition quality for German clinical dictation has not been measured.

Setup details: [Local speech recognition](whisperx-setup.md) and [Model Configuration](model-configuration.md).

### Optional local AI

- No language-model client exists unless `TEXT_MODEL_BASE_URL` or `TEXT_MODEL_API_KEY` is set. Without `TEXT_MODEL_BASE_URL`, the client uses the code default `https://openrouter.ai/api/v1`, a hosted service.
- Chat, Inquire and the generative summary routes are locked unless `TEXT_MODEL_BASE_URL` is local and each of `CHAT_MODEL_BASE_URL`, `TRANSLATION_MODEL_BASE_URL` and `EMBEDDING_BASE_URL` is local when it is set. One public address locks all three features. For text models, local means a loopback or private address or a host name in `CLINIC_LOCAL_MODEL_HOSTS` (default `speaches,host.docker.internal,localhost`). Any other host name without a dot is not local, and an address with user information, a backslash or whitespace is refused.
- The clinic interface has no chat and no Ask Ambrose interface (see [Ambrose Settings](ambrose.md)).
- Open gap: automatic title and summary. In clinic mode a finished transcription queues a `summarize` job for the title and, when automatic summaries apply, the summary of the stock recording row. This path has no locality check of its own. With only `TEXT_MODEL_API_KEY` set, the client uses the hosted default `https://openrouter.ai/api/v1`, and the automatic title and summary send the transcript there. The same happens with a hosted `TEXT_MODEL_BASE_URL`. Configure a local text model or none. The system setting `disable_auto_summarization` (default `false`) and the per-user automatic summary switch stop the summary, not the title.
- Open gap: with `ENABLE_INQUIRE_MODE=true` (default `false`), every completed recording queues an index job for transcript passages and embeddings, without a clinic check. Keep `ENABLE_INQUIRE_MODE` off.
- No language model rewrites the dictation text. A transcript revision is either `ASR` or `HUMAN` (`TRANSCRIPT_KINDS` in `src/clinic/models.py`).
- In verification: an AI summary per dictation, shown read-only in the panel **KI-Zusammenfassung** (AI summary). It runs only when clinic mode is on, the generative summary is unlocked, a language-model client exists and its address is local. There is no hosted fallback. The request ignores proxy environment variables, does not follow redirects, and replaces a provider error with a generic message so that the error text cannot quote the transcript. The input is the current saved text revision, cut to `transcript_length_limit` (default 30000 characters). The summary never changes the dictation text. It is created automatically only for the first recognized text of a new dictation, and only when `disable_auto_summarization` is off, the author's own automatic-summary switch is on and the author has the view and edit rights. A user with the edit right can request it on the current saved text (at least 10 characters) with **Zusammenfassung erstellen** (create summary) or **Zusammenfassung neu erstellen** (create again). Every read of the panel's state, including the polls while a summary is being created, is recorded as `recording.content.read` with `part=summary`, and every request as `recording.reprocessed` with `kind=summary`. With this feature, the working copy of a linked dictation gets only a title, no stock summary. The admin page **Modellwahl** (model selection), also in verification, stores model names only; service addresses and keys stay in environment variables. These features are not part of the current released build; labels and availability may change.

### Speaker service

Status: Default off. `CLINIC_SPEAKER_SERVICE_URL` names a separate local process that computes voice embeddings. Each call is tried once, with a timeout of 120 s (5 s for the health check). Open gap: the address is not checked for locality. Set it only to a host of the institution. The service is used only for consented voice profiles (see [Voice profiles](#voice-profiles-opt-in-only)).

## Data stored by the clinic build

The upstream tables remain in the database (SQLite by default, PostgreSQL as an option). In clinic mode the stock `recording` row of the transcription working copy holds the recognized transcript and, when a text model is configured, the title and the summary. The anchor recording of the dictation takes over the title unless it already has one. The clinic build adds the following stores and files.

| Data | Where | Protection |
|---|---|---|
| Dictation originals, written bit for bit and fsynced | Files under `CLINIC_ORIGINALS_ROOT` (default `UPLOAD_FOLDER`) at `clinic/originals/v1/<SHA-256 of the idempotency key>`; receipt with hash and size in `clinic_original_version` | Always the local file system, whatever `FILE_STORAGE_BACKEND` says. Database triggers refuse UPDATE and DELETE of the receipt. |
| Working copy for transcription | Storage backend, key `clinic/imports/asr/<id>/Diktat.<ext>`, and a stock `recording` row with the file name `Diktat.<ext>` | Goes to the S3 bucket when `FILE_STORAGE_BACKEND=s3`. |
| Audio and chunks during conversion and transcription | Temporary folder of the container | Deleted when the job ends; can remain after a crash until the container is recreated. |
| Chunks of a stock browser recording and slices of a large stock upload in progress | `UPLOAD_FOLDER/_sessions/<session id>/` | Removed at finalization; abandoned sessions are handled by the hourly cleanup (see [Deletion, archival and retention](#deletion-archival-and-retention)). |
| Files in conversion | `UPLOAD_FOLDER/_staging/`, or `FILE_STORAGE_STAGING_DIR` | Local file system. |
| Waveform data | `UPLOAD_FOLDER/_peaks/` | Removed with the audio. |
| Playable MP3 of a DS2/DSS dictation | `<stem>.playback.mp3` next to the audio file of the recording | Derived copy for the browser player; the original is unchanged. |
| Every recognized and every human text revision | `clinic_transcript_revision` (full text) | Database triggers refuse UPDATE and DELETE. |
| Audio edits | `clinic_audio_revision` and the audio edit tables | Non-destructive edit list over the original. The ORM refuses UPDATE and DELETE on the edit tables; their database triggers are prepared but not installed at startup. |
| Case context: source system, tenant scope, case reference and patient reference | `clinic_context_revision` | One row per revision. |
| Releases with the exact audio, text and context revisions and the text hash; KIS delivery ledger with payload hash and external document id | `clinic_release`, `clinic_delivery` | Delivery history rows are appended, never changed. |
| Voice consent history (status, purpose, SHA-256 of the declaration), voice embeddings (vectors only, no audio), consent audit rows | `clinic_voice_consent`, `clinic_voice_embedding`, `clinic_voice_consent_audit` | Consent and consent audit are append-only through database triggers. |
| Import agent: inventory (relative path, size, modification time, SHA-256), intents, ingest sessions, upload chunks with audio bytes, reported outcomes | `clinic_agent_inventory`, `clinic_agent_intent`, `clinic_agent_ingest`, `clinic_agent_chunk`, `clinic_agent_outcome` | Chunks are deleted when the ingest is stored or aborted. Outcomes are never changed by the channel. |
| Activation and reset codes | `clinic_local_recovery` | Only the SHA-256 of the code is stored. |
| Output receipts | `clinic_output_receipt` | Append-only through database triggers. |
| Pool, membership, lead and grant changes with old and new values | `clinic_pool_history` | The ORM refuses UPDATE and DELETE; there is no database trigger. No page lists the rows. |
| Audit log | `audit_event` | See [Audit Log](audit-log.md). |

With `PRESERVE_TEMP_AUDIO=true`, a debug copy of every converted file stays in the temporary folder; leave it unset.

Tables of public links and internal shares from an earlier release (`share`, `internal_share`, `shared_recording_state`, `share_audit_log`) are not read, written or dropped by this build.

The import agent secrets (`CLINIC_AGENT_PROXY_SECRET`, `CLINIC_AGENT_MASTER_SECRET`) are read from environment variables. Where to store originals, working copies and backups is described in [File Storage](storage.md).

## Originals and revisions are immutable

- Database triggers refuse UPDATE and DELETE of originals and transcript revisions. SQLite answers `clinic original is immutable` or `clinic original cannot be deleted`, and `clinic transcript revision is immutable` or `clinic transcript revision cannot be deleted`. PostgreSQL answers `clinic original is immutable` or `clinic transcript revision is immutable` for both operations. Other database engines get no trigger.
- The anchor recording of a dictation keeps an empty `audio_path`, so a stock conversion job cannot rewrite the original.
- At startup in clinic mode the original store is swept, with one log line: `Clinic original sweep: scanned=… removed_leftovers=… incomplete=… awaiting_row=… errors=…`. Only leftovers of interrupted writes (`.partial-` hard links) are removed; incomplete files and files without a row are reported, never deleted.

## Deletion, archival and retention

- Status: Upstream only. Manual hard deletion is locked: the stock delete routes for recordings and their audio, the bulk and API v1 deletes and the admin user delete answer 403 `clinic_feature_disabled`. Accounts are deactivated instead of deleted (see [User Management](user-management.md)).
- Automatic retention only archives. Every `DELETION_MODE` value archives and keeps files and rows. Dictation anchor recordings are created exempt from retention, so the sweep skips them unless the exemption is turned off (the clinic UI has no control for it; see [Retention](retention.md)). No approved clinic retention policy exists. See [Retention](retention.md).
- In clinic mode the daily audit pruning deletes nothing and logs `Audit pruning withheld (separate_privileged_retention): N row(s) past cutoff ... kept`. In clinic mode no process deletes audit rows; the separately privileged retention process is not part of the repository.
- What is still deleted in clinic mode:
    - all enrolled voice embeddings of a user when that user revokes the voice consent;
    - upload chunks of the import agent, once the ingest is stored or aborted;
    - recordings without an owner, by the daily cleanup at 02:00 server time after a mark from the previous run (unchanged upstream);
    - tombstones, webhook delivery rows and abandoned recording sessions (a browser recording with chunks is finished as a recovered recording instead), by the hourly upstream cleanup (unchanged upstream); `RECORDING_SESSION_CLEANUP_INTERVAL_SECONDS=0` turns this cleanup off.
- Backups are outside the application. A backup must cover `CLINIC_ORIGINALS_ROOT`, which holds the originals even with `FILE_STORAGE_BACKEND=s3`, and must receive the same protection as the live system.

## Browser storage, BYOD and offline use

### Client profiles

The client profile is a server decision. No header, user agent or host name sent by the browser selects it. Until a trusted ingress mapping exists, every request gets the default profile `managed_web_online`. The profiles in `clinic-spec/config/client-capabilities.json`:

| Profile | UI label | Audio kept in the browser | Document export |
|---|---|---|---|
| `managed_web_online` (default) | **Verwaltetes Web, nur online** (managed web, online only) | No | Controlled by rights |
| `byod_internal` | **Eigenes Gerät, nur online** (own device, online only) | No | No |
| `kis_webview` | **KIS-Ansicht, nur online** (KIS view, online only) | No | By manufacturer contract and policy |
| `managed_web_offline` | **Verwaltetes Web, Offline freigegeben** (managed web, offline approved) | Encrypted, opt-in after gate `G-MANAGED-WEB-OFFLINE` | Controlled by rights |

Because no request is mapped to `byod_internal` today, the profile rules that depend on it (for example the refusal of bundle exports for unmanaged clients) do not take effect yet. The browser storage rules below apply to every browser.

### What the browser keeps

Status: Available.

- The clinic recorder (**Neue Aufnahme** (new recording)) keeps audio and title in memory only and never writes to IndexedDB, `localStorage` or `sessionStorage`. After a failed upload it shows: **Der Upload ist fehlgeschlagen. Die Aufnahme wird nicht lokal gespeichert. Bitte erneut aufnehmen.** (The upload failed. The recording is not stored locally. Please record again.)
- The IndexedDB stores of the stock recorder and upload, `SpeakrRecordings` (crash recovery) and `SpeakrFailedUploads` (retry), are gated in clinic mode. They are used only when the page carries `data-clinic-persistent-audio="True"`, and no template renders that attribute.
- The voice consent view and the offline modules write nothing to browser storage, and no login or session token is read or written by them.
- Clinic values kept in `localStorage`: the foot pedal key settings of the workstation (key codes, step seconds, auto-rewind) and, per user, the id of the last selected worklist filter. The filters themselves are stored on the server.
- Not clinic-gated (unchanged upstream): interface preferences (theme, column widths, volume, playback rate), the id of the last selected recording, the defaults of the upload dialog, and for a stock upload larger than 16 MiB that is sent in slices, the file name, size and modification time with its session id for up to 24 hours. `sessionStorage` holds the time zone and a dismissed microphone notice.
- The service worker is registered on the signed-in pages, the clinic pages included, when the browser has a secure context (`https`, or `http` on `localhost` or `127.0.0.1`). Its Cache Storage (`speakr-static-<version>`) holds only application files under `/static/`. No page and no response under `/api/`, `/audio/`, `/recording/` or `/admin` is stored.
- At sign-out, the server sets the cookie `speakr_signed_out` (no data), and the sign-in page that follows clears Cache Storage, the `sessionStorage` of the tab, the IndexedDB stores `SpeakrRecordings` and `SpeakrFailedUploads`, and the `localStorage` entry `speakr.slicedUploads`. An idle-timeout logout is not a sign-out and clears nothing. Interface preferences, the pedal settings, the worklist filter id, the stock recorder's session id (`speakr.serverRecordingSession`, written only with `ENABLE_SERVER_RECORDING_CHUNKS=true`, default `false`) and the `sessionStorage` of other open tabs stay.
- The application cannot guarantee that a browser keeps no data: downloads, the browser cache and operating-system copies are outside its control.

### Managed offline profile

Status: Prepared, disabled.

- `GET /api/clinic/v1/offline-web/capability` (clinic mode, browser session, `Cache-Control: no-store`) always answers `available: false` with the message `Offline-Speicherung ist nicht freigegeben.` (offline storage is not approved). No trusted browser verification and no grant provider are wired.
- The contract sets `web_client.managed_offline_enabled` and `web_client.byod_persistence_enabled` to `false`; the code reads only the first. The profile `managed_web_offline` has `enabled_by_default: false`.
- Even when wired, the server allows offline storage only for the profile `managed_web_offline`, a verified browser, a current server grant with version, quota and key policy `encrypted_user_unlock`, and `managed_offline_enabled: true`.
- The browser modules seal each record with AES-GCM, bound to user, profile, dictation and sequence. The data key is wrapped with a key derived from the user's passphrase (PBKDF2-SHA-256, 600000 iterations) and is held in memory only. There is no default storage adapter. A revoked grant and a sign-out purge the stored records. Without a grant, nothing is stored.
- The view **Offline und Wiederherstellung** (offline and recovery) in the menu **Verwaltung & Seiten** (administration and pages) shows: **Offline-Speicherung ist nicht freigegeben. Es wird nichts lokal gespeichert und es gibt keine lokale Wiederherstellung. Bereits auf dem Server bestätigte Daten bleiben über die Arbeitsliste erreichbar.** (Offline storage is not approved. Nothing is stored locally and there is no local recovery. Data already confirmed on the server stays reachable through the worklist.)

## Tokens and secrets

- No token or secret is kept in browser storage. The session cookie is `HttpOnly` and cannot be read by scripts, the CSRF token comes with each page, and no clinic module writes a token to `localStorage`, `sessionStorage` or IndexedDB.
- No token in the URL. The setting `api_query_tokens_enabled` is forced to `false` in clinic mode. A token in the `?token=` query parameter authenticates nothing, on any method. A settings write that tries to raise the value is refused with 403 `clinic_policy_ceiling`, and a conflicting stored value is logged at startup as `clinic policy overrides stored setting <key>=<value> (effective: False)`.
- Personal API tokens are locked (Upstream only). A request that authenticates with a token instead of a browser session is refused with 403 `clinic_feature_disabled` on every non-public route. Token management and the SSO token exchange are disabled, and `sso_api_tokens_enabled` is forced to `false`.
- Sign-in is local only; the upstream OIDC routes refuse in clinic mode, and Microsoft Entra is prepared, disabled (see [Sign-in and identity providers](sso-setup.md)). Local passwords have 15 to 128 characters, a built-in blocklist that `CLINIC_PASSWORD_BLOCKLIST_FILE` extends, and scrypt hashes. Administrators never set passwords. A one-time code (**Einmalcode ausstellen** (issue one-time code)) has 256 bits, is valid for 30 minutes and once, is shown once with `Cache-Control: no-store` and is stored as SHA-256 only. See [User Management](user-management.md).
- The import agent authenticates with a machine credential at a trusted mTLS ingress, never with a session. `CLINIC_AGENT_PROXY_SECRET` and `CLINIC_AGENT_MASTER_SECRET` need at least 32 bytes each. When one is missing, a random value per process is used, every agent request answers 401, and the log says `Clinic import agent: <NAME> is not set or shorter than 32 bytes; agent requests are refused until it is configured`.
- Session cookies are `HttpOnly` and `SameSite=Lax`. The `Secure` flag is set only with `SESSION_COOKIE_SECURE=true` (default `false`). The cookie for **Angemeldet bleiben** (English interface: **Remember me**) lasts `REMEMBER_COOKIE_DAYS` days (default 30) and is not accepted while an idle timeout is set. The idle timeout is the system setting `session_idle_timeout_minutes` (default 0, no timeout).
- Open gap: the contract values `identity.session_idle_minutes` (15) and `identity.session_absolute_hours` (8) are not read by the code.

## Voice profiles: opt-in only

- Status: Upstream only. Upstream speaker identification is locked (`speaker_identification`): its routes are disabled, voice embeddings of diarized speakers, patients included, are not stored on the recording, and the contextual naming of speakers by a language model returns nothing in clinic mode.
- Diarization: with `asr_endpoint`, a job asks the service for diarization unless `ASR_DIARIZE=false` (default `true`). Speaker labels such as `SPEAKER_00` are then stored in the segment list of the working copy's transcript; the clinic text revision holds the segment text without labels. Voice embeddings returned for diarized speakers are discarded in clinic mode; keep `ASR_RETURN_SPEAKER_EMBEDDINGS` unset (default `false`).
- Status: Available. A user may consent to a voice profile of their own voice only, for the purpose `author_recognition`. The routes act on the signed-in user only: `GET /api/clinic/v1/voice-consent`, `POST /api/clinic/v1/voice-consent/grant` and `POST /api/clinic/v1/voice-consent/revoke`, plus the enrollment API under `/api/clinic/v1/voice/`. Writes to `/api/clinic/v1/voice-consent` need the browser session, an `X-CSRF-Token` header and an `Idempotency-Key` of 8 to 255 characters; a body that names another subject (for example `user_id`, `actor`, `patient_id`, `role`, `is_admin`) is refused with 403 `forbidden_field`. Writes to the enrollment API need the browser session, a CSRF header, an `Idempotency-Key` of 8 to 128 characters and `If-Match`; a sample is a WAV body (16 kHz, mono, 16-bit, 3 to 30 seconds). Neither an administrator nor a service can consent or enrol a voice for someone else.
- Stored: each grant and each revocation as a new row with the SHA-256 of the declaration shown, not its text; enrolled embeddings as vectors only, never audio. The temporary WAV file of an enrollment is deleted after the embedding. Enrollment without active consent is refused.
- A revocation deletes every enrolled embedding of the user, in every embedding space, in the same transaction, and writes an audit row with the number of deleted vectors. The consent history stays. Address-book speaker profiles of other people are not touched.
- Where users find it: **Verwaltung & Seiten** (administration and pages) > **Einstellungen** (settings). Accounts with the **Administrationsrolle** also see the panel **Eigenes Stimmprofil (freiwillig)** (own voice profile, optional) on the admin page **Sprachprofile** (speech profiles), reached through the menu entry **ASR und Sprachprofile**. That panel too covers only the administrator's own voice. Labels: **Einwilligen** (consent), **Einwilligung widerrufen** (revoke consent), **Widerruf bestätigen** (confirm revocation); states **Keine Einwilligung erteilt**, **Einwilligung erteilt**, **Einwilligung widerrufen**; after a revocation **Einwilligung widerrufen. Stimmprofil und Vorschläge sind entfernt.** (consent revoked; voice profile and suggestions are removed).
- Available (API only): enrollment. No page records an enrollment sample, and the embedding needs the speaker service (Default off). Prepared, disabled: the author suggestion from consented profiles is not wired to any route. A suggestion would only ever be a proposal that a person confirms, and patients never get a profile.
- Prepared, disabled: a clean-up of upstream speaker data (speaker rows, samples, quotes and speaker names in transcripts) exists in code but has no command and no route.

## Access by administrators and other roles

The clinic build separates site administration (**Administrationsrolle** (administration role)), local identity administration (**Identitätsadministration** (identity administration)), pool leads (**Leitung** (lead), limited to their pools) and the rights for release (**Freigeben** (release)) and for original-audio export (**Originalaudio exportieren** (export original audio)). Details: [User Management](user-management.md) and [Group Management](group-management.md).

- An active site administrator (administration role and an active clinic account) may view and play every clinic dictation and read its corrections. Editing, export, release, original-audio export, archiving and deletion need explicit rights (**Fachrechte**) like for anyone else.
- The admin dictation overview returns metadata only and writes no audit row. The transcript and audio of any user's dictation open without a confirmation or reason prompt. Each opening is recorded without de-duplication, as `recording.content.read` with `part=transcript` or as `recording.media.played`. The audit row is stored first; when it cannot be stored, the answer is 503 `audit_unavailable`. With `audit_log_enabled` off, such openings are refused, and with `audit_log_record_views` off, audio openings are refused too.
- Each foreign row that the worklist returns to a site administrator is recorded as `recording.content.read` with `part=worklist`.
- An identity role gives no dictation access. The UI says: **Identitätsrollen geben keinen Zugriff auf Diktate. Fachrechte werden separat erteilt.** (identity roles give no access to dictations; professional rights are granted separately).
- A stock audio download (`download=true`) needs the right `audio_export`. It is not an own default, and site administrators do not get it from their role.
- **Systemstatistik** (system statistics) shows usernames and the five latest failed recordings with up to 300 characters of their stored error text (unchanged upstream; see [Logs](#logs)). See [System Statistics](statistics.md).
- A person with access to the server or the database can read everything. Through the application, audit rows cannot be changed (the ORM refuses it). The database triggers for `audit_event` exist in code but are not installed at startup, and a database role without DELETE on `audit_event` is a deployment matter.

## Outbound connections

| Destination | Data | Status and control |
|---|---|---|
| Local speech recognition | Audio, language, and hints only with `CLINIC_ASR_PROMPTS_ENABLED` | Available. `TRANSCRIPTION_BASE_URL` and `CLINIC_GPU_ASR_BASE_URL` must be local. `ASR_BASE_URL` is not checked (Open gap). |
| Transcription service, at startup | Bundled test clip, no user data | Only with `asr_endpoint` and `ASR_RETURN_SPEAKER_EMBEDDINGS=true`, when no reference exists or the configuration changed. Off with `DISABLE_VOICE_EMBEDDING_CHECK=true`. |
| Text model (title, summary; chat and Inquire only when local) | Transcript and prompts | Default off (no LLM client until `TEXT_MODEL_BASE_URL` or `TEXT_MODEL_API_KEY` is set). Locality check of the automatic title and summary job: Open gap. |
| Embedding service | Transcript passages | Only with `ENABLE_INQUIRE_MODE=true`; keep it off. |
| Speaker service | Audio of a consented enrollment sample | Default off. `CLINIC_SPEAKER_SERVICE_URL`; its locality is not checked (Open gap). |
| KIS | Released documents | Simulated (only with `CLINIC_PROFILE=staging`, synthetic in-memory MockKIS, no KIS evidence); otherwise the adapter is disabled, sends nothing, and final release is refused. |
| Import agent | Inbound only: the agent connects to the server through the mTLS ingress | Prepared, disabled. The server routes under `/api/clinic/v1/agent/` exist, but no supported route, command or script registers an agent, creates an approved source or creates an import intent. The Philips device side is Simulated (simulator mode and fixtures, no device evidence). The server opens no connection to the agent. Ingress contract: [Recording sessions](recording-sessions.md#import-agent-channel-and-ingress). |
| Webhooks | None | Upstream only. Dispatch is off while `user_webhooks` is locked, whatever `WEBHOOK_GLOBAL_ENABLED` says. See [Webhooks](webhooks.md). |
| Web push | None | Upstream only. The push routes are disabled. |
| SMTP server | Upstream completion and failure mail, with the recording title in the subject | Not clinic-gated. Sent only when `SMTP_HOST`, `SMTP_USERNAME` and `SMTP_PASSWORD` are set and the user opted in. Not reviewed for clinical use; leave SMTP unset. See [Email Setup](email-setup.md). |
| OIDC identity provider | None | Upstream only. The SSO routes refuse in clinic mode. |
| S3 storage | Transcription working copies | Only with `FILE_STORAGE_BACKEND=s3`. Originals stay local. |

Requests use the scheme of the configured address. With an `http://` address, audio and text cross the network unencrypted. The plain-http startup warning does not cover `CLINIC_GPU_ASR_BASE_URL` and `CLINIC_SPEAKER_SERVICE_URL`; check their scheme yourself.

The default Content-Security-Policy limits scripts, styles, fonts and connections to the origin of the instance (inline scripts and styles are allowed). `CONTENT_SECURITY_POLICY` replaces it, and `SECURITY_HEADERS_ENABLED=false` turns the security headers off; do not widen or disable them.

In-app workflow notices (for example **Abnahme angefordert** (acceptance requested)) stay inside the application. They use fixed German texts and carry no patient data.

## Logs

Logging is unchanged from upstream. All output goes to the standard output of the process. The application creates no log file, and no HTTP access log is configured for the application server. The retention of the output depends on the container logging driver and on any log collector of the host.

| `LOG_LEVEL` | Content of the log |
|---|---|
| `ERROR`, `WARNING` | Failures, with ids. An error of a transcription service, a language-model provider or the embedding API is logged as its class, provider, HTTP status, error code and length, without its text. |
| `INFO` (default) | In addition: ids, lengths, counts, durations, model names and service addresses. No transcript, summary, title, prompt, hotword or search query. Users, groups, tags, folders, speakers, naming templates and recordings are named by id. A file is named by a label without its original file name. |
| `DEBUG` | In addition: titles, summaries, prompts, model replies, search queries, hotwords, usernames, group, tag and speaker names, original file names, recipient addresses of sent email, and the full text and traceback of provider errors. |

`DEBUG` is unsuitable for regulated data, except for a short diagnosis on a test instance.

Errors of the operating system, the database and FFmpeg are logged with their text at `WARNING` and `ERROR`. In the upload, conversion, probe, chunking, watch folder, export, storage deletion and FFmpeg code, a file named in such text is replaced by its label; in the traceback of an error logged above `DEBUG`, the same replacement is applied to the exception text. In other code a file path can appear in the message of an error line.

An audit row that cannot be written is logged at `ERROR` as one line that starts with `AUDIT-FALLBACK`. The line holds ids and no content; see [Audit Log](audit-log.md).

### Error text shown to the user

The error text of a failed job is stored on the job and on the recording, shown in place of the transcript or the summary, listed in **Systemstatistik** (system statistics), and sent in the upstream failure email when SMTP is configured. No failure webhook is sent while `user_webhooks` is locked. The text is built as follows:

- The failed step and the HTTP status are kept.
- From a structured error body, only the message field of the provider (`error`, `error.message`, `message`, `detail`, `msg`, `error_description`, `description` or `reason`) is kept, cut to 300 characters, with its error code and type.
- An unstructured body, such as a traceback, is kept in a bounded form in which credentials and directories are replaced.
- Every passage of 20 or more characters, and every quoted string of 4 or more characters, that also occurs in the transcript, the summary, the notes, the title, the participants, the original file name, the initial prompt or the hotwords of the recording is replaced by `[content removed]`.
- The result is limited to 500 characters, with both ends kept.

A repetition of fewer than 20 characters outside quotation marks is not detected. Text taken from the audio itself, such as a transcribed passage in the error of a transcription service, is not detected either.

## Removed or locked upstream features

| Upstream feature | Status in the clinic build |
|---|---|
| Other transcription connectors: hosted (OpenAI Transcribe, Azure OpenAI, Mistral, AssemblyAI, Alibaba FunASR, Tencent, Mossland) and self-hosted (VibeVoice, OpenASR) | Upstream only: removed from the code. |
| Public share links | Upstream only: removed from the code. |
| Internal shares, groups, group tags and folders | Upstream only: locked (`internal_sharing`). |
| Incognito mode | Upstream only: removed from the code. |
| Event extraction and calendar export | Upstream only: removed from the code. |
| Self-registration, e-mail verification, password reset by e-mail | Upstream only: removed. Account recovery is the local one-time code. |
| User webhooks | Upstream only: locked (`user_webhooks`). |
| Personal API tokens and `?token=` | Upstream only: locked (`personal_api_tokens`, `token_query_parameters`). |
| Web push | Upstream only: locked (`push_notifications`). |
| OIDC single sign-on | Upstream only: the routes refuse in clinic mode. |
| Chat, Ask Ambrose, Inquire | Upstream only: no clinic interface; routes locked unless every text-model address is local (see [Ambrose Settings](ambrose.md)). |
| Speaker identification and speaker profiles from recordings | Upstream only: locked; replaced by the opt-in voice profile. |
| Upload from external voice recorder apps | Upstream only: locked (`external_ingest`). |
| Unversioned stock exports (Word downloads, template download) | Upstream only: locked (`stock_unversioned_export`). |
| Manual deletion of recordings and accounts | Upstream only: locked (`clinical_user_hard_delete`). |
| Automatic export (`ENABLE_AUTO_EXPORT`) and the watch folder (`ENABLE_AUTO_PROCESSING`) | Upstream only (not clinic-gated): the code still runs when a variable is set; only the admin start, stop, trigger and configuration routes of the watch folder are locked (`auto_process`). Leave both variables unset. See [System Settings](system-settings.md#upstream-functions-without-a-clinic-check). |

## Checklist for a clinic deployment

1. Set `CLINIC_MODE=1` and keep the contract files in `clinic-spec/config` (or in `CLINIC_CONFIG_DIR`). If a contract file is missing or unreadable, requests fail with an error; the build never falls back to upstream defaults.
2. Point `TRANSCRIPTION_BASE_URL` (and `CLINIC_GPU_ASR_BASE_URL`, if used) at local services. If `ASR_BASE_URL` is used, point it only at a host of the institution: it is not checked. With `asr_endpoint`, set `ASR_DIARIZE=false` unless the institution wants speaker labels, and leave `ASR_RETURN_SPEAKER_EMBEDDINGS` unset.
3. Leave `TEXT_MODEL_BASE_URL` and `TEXT_MODEL_API_KEY` unset, or point the text model to a local service. Leave `CHAT_MODEL_BASE_URL`, `TRANSLATION_MODEL_BASE_URL` and `EMBEDDING_BASE_URL` unset or local.
4. Keep `ENABLE_INQUIRE_MODE` off, and leave `ENABLE_AUTO_EXPORT`, `ENABLE_AUTO_PROCESSING` and `PRESERVE_TEMP_AUDIO` unset.
5. Leave `SMTP_HOST` unset.
6. Set `CLINIC_SPEAKER_SERVICE_URL` only to a local service, or leave it unset.
7. Terminate TLS at a reverse proxy, set `SESSION_COOKIE_SECURE=true`, set `TRUSTED_PROXY_HOPS` to the number of proxies, and protect or turn off the access log of the proxy. Set `session_idle_timeout_minutes` to the idle time the institution allows.
8. Place `UPLOAD_FOLDER`, `CLINIC_ORIGINALS_ROOT`, the database and the backups on encrypted volumes. Back up `CLINIC_ORIGINALS_ROOT` together with the database.
9. Keep `FILE_STORAGE_BACKEND=local`, or use an S3 service of the institution for the working copies.
10. Keep `LOG_LEVEL` at `INFO` or higher, and set a size limit and a retention period for the container logs.
11. Keep `audit_log_enabled` and `audit_log_record_views` on, decide on `audit_log_store_ip`, and export the audit log regularly from an admin browser session (see [Audit Log](audit-log.md)).
12. Configure `CLINIC_AGENT_PROXY_SECRET` and `CLINIC_AGENT_MASTER_SECRET` (at least 32 bytes each) only together with a trusted mTLS ingress. The import-agent channel is Prepared, disabled; see [Recording sessions](recording-sessions.md#import-agent-channel-and-ingress).
13. Do not use `CLINIC_PROFILE=staging` with real data: it uses the synthetic KIS mock.
14. Keep a list of the people with access to the host, the database and the backups. Each of them can read all dictations.
15. Ask users on shared computers to sign out and close the other tabs, or to use one browser profile per person; see [What the browser keeps](#what-the-browser-keeps).

## Open gaps

- `ASR_BASE_URL` and `CLINIC_SPEAKER_SERVICE_URL` are not checked for locality.
- The automatic title and summary of the current release are not locality-gated, and Inquire indexing with `ENABLE_INQUIRE_MODE=true` has no clinic check.
- No approved retention policy exists, and the separately privileged process for deleting audit rows is not part of the repository.
- The contract session limits (15 minutes idle, 8 hours absolute) are not enforced.
- No client is mapped to the BYOD profile, so its export refusal does not take effect.
- The audit log is not protected against a person with database access, and the application has no encryption at rest.
- Stock browser entries that are not clinic-gated can hold the file name of a large upload for up to 24 hours.
- Recognition quality, Entra, the KIS integration, Philips devices and foot pedals have no real-world evidence.
- Web parity: a local mail draft with the original audio (WG-EXP-009) and the reading of local Windows accounts (WG-ADM-009) stay open.

## Upstream documentation

Upstream Speakr documentation (pinned original; describes upstream Speakr, not this build): [Privacy and regulated data](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/privacy.md).
