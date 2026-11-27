# Protecting dictation data

[Deutsch](../de/admin-guide/privacy.md)

This page describes the current web-only clinic build with `CLINIC_MODE` enabled. Clinic checks are not a production approval. The shipped development configuration is synthetic-only, with `allow_real_data`, `production_approved` and `retention_policy_approved` set to `false`. Production retention and recovery values remain unset. Mocks and fixtures are not evidence for Philips devices, Entra, KIS or foot pedals, and the web-parity gaps remain open.

## Data and access

A dictation can have an original audio file and hash receipt, ASR working media, text and audio revisions, context revisions, summaries, releases, workflow history, import receipts and output records. An import agent's inventory and transfer records can also identify a source file. Include all these stores and their backups in the local access review.

| Data | Main storage |
|---|---|
| Immutable original and recorded audio segments | Local `CLINIC_ORIGINALS_ROOT`, defaulting to `UPLOAD_FOLDER` |
| Ordinary recording media and ASR working copies | Local or S3-compatible recording backend |
| Full text revisions, context, summaries, releases and receipts | Database |
| Import inventory, ingest state and transferred chunks | Database; accepted originals are stored separately |
| Voice consent and enrolled voice vectors | Database; consent history and consent audit are separate from the access log |
| Audit events | `audit_event`; exported copies need separate protection |
| Conversion, session and waveform data | Local staging, session and cache directories |

The application does not itself encrypt these server stores at rest. Protect database volumes, media, temporary locations, backups and deployment secrets. A person with host or database privileges can access data beyond the web application's permissions. See [File storage](storage.md) for the distinction between originals and working copies.

Use the narrowest applicable pool and individual **Fachrechte**. An active site administrator has audited read and playback access to other users' dictations. Editing, release, original-audio export and archiving need separate rights. Identity administration alone grants no dictation access; pool leadership is limited to its routed scope. An audio download needs an explicit `audio_export` grant, even for the author.

The dedicated admin transcript/audio routes store their access evidence before delivery and return `503 audit_unavailable` when this fails. Keep audit logging and playback logging enabled. Do not interpret an audited read as permission to edit or redistribute the content. See [Audit log](audit-log.md).

## Processing stays local

The clinic fork does not offer cloud ASR connectors or cloud fallback. The remaining connectors are `openai_whisper` for a local OpenAI-compatible service, `asr_endpoint` for a local ASR service, and the synthetic `clinic_stub`. The OpenAI-compatible CPU address `TRANSCRIPTION_BASE_URL` and optional GPU address `CLINIC_GPU_ASR_BASE_URL` are checked for locality in clinic mode. The inherited `ASR_BASE_URL` path has no equivalent locality check; configure it only for an institution-controlled local host.

**KI-Zusammenfassung** is part of the current clinic UI. It reads a saved text revision, leaves the dictation text unchanged and binds its result to that revision. Later corrections can make the summary stale. Generation requires a local text model and the relevant view/edit permissions; there is no hosted fallback for this revision-bound summary. **Modellwahl** stores model identifiers, not service URLs or credentials.

For text-model features, `TEXT_MODEL_BASE_URL` must be local, and each configured `CHAT_MODEL_BASE_URL`, `TRANSLATION_MODEL_BASE_URL` and `EMBEDDING_BASE_URL` must also be local. The allowlist is `CLINIC_LOCAL_MODEL_HOSTS`, defaulting to `speaches,host.docker.internal,localhost`; loopback and private IP addresses are also accepted. Other service names need explicit inclusion. See [Model configuration](model-configuration.md).

This check does not protect every inherited background path. The automatic title step and summaries for recordings without a linked clinic dictation still use the inherited text-model client. Setting `TEXT_MODEL_API_KEY` without a local `TEXT_MODEL_BASE_URL` selects a hosted default. Configure a local text endpoint or leave both unset. `disable_auto_summarization` stops automatic summaries, not automatic titles.

Keep `ENABLE_INQUIRE_MODE=false`: inherited transcript indexing is not protected by the clinic route lock. If configured, an embedding endpoint receives transcript passages. The clinic UI offers no Ambrose chat or cross-recording Ask screen. The optional speaker service at `CLINIC_SPEAKER_SERVICE_URL` also lacks a locality check; leave it unset or use a reviewed local service.

Local does not mean encrypted in transit. An `http://` endpoint sends data over its network path without TLS. Review the actual endpoints, network boundaries and certificates. Hotwords and vocabulary settings are decoder hints, not evidence of acoustic training or measured recognition quality.

## Browser storage and offline use

The clinic recorder keeps its pending audio in memory and does not intentionally persist it in IndexedDB, `localStorage` or `sessionStorage`. If saving fails, keep the page open and use **Erneut sichern** or **Erneut hochladen** while the take or selected file is still available. Leaving or reloading the page, or a browser crash, loses an unsent in-memory take. **Entwurf sichern** stores on the server. The inherited audio recovery stores are blocked by default in clinic mode.

This does not guarantee a browser without traces. Interface preferences, pedal settings and the last selected worklist-filter ID can persist. The inherited sliced-file upload path can retain a filename, size, modification time and upload-session ID in browser storage. Downloads, browser caches and operating-system copies are outside the application's control. Use synthetic dictations for screenshots and support reproduction; avoid clinical content in account names and operational notes.

The service worker caches application files under `/static/`, not authenticated content responses. Sign out on shared computers and close other application tabs; do not assume an idle timeout clears every browser store or another tab's state.

Client classification is a server decision, not a browser header or user-agent claim. Current requests use the default `managed_web_online` profile because trusted ingress mapping is not wired. A server-classified BYOD profile does not permit persistent audio or bundle export, but an arbitrary personal device is not automatically recognised as BYOD by this build.

Managed offline storage is prepared but disabled. `GET /api/clinic/v1/offline-web/capability` reports `available: false`; no trusted-browser and grant provider is connected. **Offline und Wiederherstellung** therefore offers no local recovery. Data already confirmed on the server remains accessible through the worklist.

## Identity, credentials and optional voice profiles

Use local account creation and one-time recovery codes through [User management](user-management.md). Session cookies are `HttpOnly` and `SameSite=Lax`; their `Secure` flag requires `SESSION_COOKIE_SECURE=true`. Deploy behind HTTPS and configure `session_idle_timeout_minutes` to the site's agreed idle limit. The development contract's 15-minute idle and 8-hour absolute values are not a substitute for enforced session settings.

The clinic fork does not offer self-registration, email verification, password reset by email, general API v1 or personal API tokens. Token-based machine access is reserved for the import-agent channel. Query-string tokens are disabled. Keep credentials out of URLs, browser storage, screenshots and support logs; protect import-agent deployment secrets and its trusted mTLS ingress separately.

Voice consent covers only the signed-in person's own voice for author recognition. Neither an administrator nor a service may consent or enrol for someone else; patients are not enrolled through this feature. Consent history stores the declaration hash and status. Enrollment stores vectors, not the temporary sample audio. Revocation removes that user's enrolled vectors in the same transaction as the revocation and consent-audit entry; the history remains.

Users manage consent in **Einstellungen**; the **Eigenes Stimmprofil (freiwillig)** panel on **Sprachprofile** also concerns only the current administrator's own voice. Enrollment is an API capability, not a sample recorder in the clinic UI. The prepared author-suggestion component is not proof of a working device or speaker-identification workflow.

## Retention, outgoing data and logs

The clinic fork does not offer hard automatic deletion. The retention sweep archives recording rows and keeps media; clinic dictations use the explicit archive action. Daily clinic audit pruning deletes no rows. Specific lifecycle cleanup still exists for temporary uploads, ownerless recording rows and revoked voice vectors. Define retention and backup responsibilities locally; see [Retention](retention.md).

Public share links, internal shares, webhooks, folders, incognito mode, event/calendar extraction and the What's-New popup are not offered by the clinic fork. They are not alternate ways to deliver or protect a dictation.

In-app workflow notices stay in the application. Optional inherited SMTP processing notifications can carry recording titles outside it; leave SMTP unset unless the site has reviewed that use. See [Email setup](email-setup.md). An S3 backend and configured model services are also data destinations, not just technical settings. KIS and identity-provider preparation does not establish approval for a real external system.

Keep `LOG_LEVEL` at `INFO` or higher when handling sensitive data. Debug logging can expose prompts, model replies, names and provider error details. Audit detail allowlists do not sanitize every application, proxy or container log. Protect those logs and exported audit files, restrict access, and define their retention outside the application.

## Operational review

Before a locally approved deployment, verify local model endpoints and TLS, narrow roles and pool grants, secure cookies and session limits, storage mounts and encryption, independent audit protection, and a complete restore test. Keep `ENABLE_INQUIRE_MODE`, `ENABLE_AUTO_PROCESSING` and `ENABLE_AUTO_EXPORT` off, and leave `PRESERVE_TEMP_AUDIO` unset. These inherited background mechanisms are not a clinic approval workflow.

Record unresolved evidence explicitly. Local-address validation gaps, browser-profile mapping, disabled managed offline storage, production retention/recovery approval and the open web-parity register remain limitations. A green synthetic test does not close them.
