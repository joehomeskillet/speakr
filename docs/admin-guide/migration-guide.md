# Migration Guide
<small>[Deutsch](../de/admin-guide/migration-guide.md)</small>

This page describes how to move an existing upstream Speakr installation and its database to the clinic build, how to upgrade from one clinic version to the next, and which upstream data and functions do not carry over. It also keeps the inherited reference for older transcription settings and for the upstream S3 migration scripts, adapted to the clinic build.

The clinic build is a web-only fork of Speakr v0.11.1 (pinned upstream base `48d3018`). The released version is the branch `main` of the clinic fork. Development happens on the branch `dev`, and contributions go to `dev`.

The clinic rules on this page apply only with `CLINIC_MODE` set to `1`, `true`, `yes` or `on`. With clinic mode off, the application behaves like upstream Speakr. The contract file that clinic mode loads (`clinic-spec/config/dev.defaults.json`, profile `local-web-parity-lab`, `synthetic_only: true`) holds development defaults, not a clinical approval. Nothing on this page makes an installation clinically approved or ready for production.

Upstream Speakr documentation (pinned original; covers the connector migration of Speakr v0.8 and the move of audio files to S3): [Migration Guide](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/migration-guide.md).

## Status overview

| Function | Status | Notes |
|---|---|---|
| Schema creation and migrations at every application start | Available | There is no separate migration command. |
| Clinic tables, integrity triggers and the pool validity columns | Available | Created by the migration section "clinic integrity". |
| First administrator (**Administrationsrolle** and **Identitätsadministration**) with `scripts/clinic_bootstrap_admin.py` | Available | Runs once. |
| Adoption of existing local accounts | Available | API call only; the page has no control for it. |
| Takeover of legacy recordings with `scripts/clinic_backfill_dictations.py` | Available | The owner needs an active author pool. |
| Read-only snapshot of accounts, pools and dictations with `scripts/clinic_roles_report.py` | Available | Prints ids and states, no names and no text. |
| Upstream administrator from `ADMIN_USERNAME`, `ADMIN_EMAIL` and `ADMIN_PASSWORD` | Available (upstream entrypoint); do not use | The entrypoint runs `scripts/docker_create_admin.py`, which creates an account with **Administrationsrolle** (administration role) but without clinic account policy. It cannot sign in until it is adopted (step 6). `scripts/create_admin.py` behaves the same. |
| Console commands `flask clinic-identity bootstrap` and `flask clinic-identity recovery` | Prepared, disabled | Defined in the code, but not registered at startup. |
| Published container image of the clinic build | Open gap | Build the image from the source. |
| Tool that moves data from SQLite to PostgreSQL | Open gap | Not in the repository. |
| Downgrade to an earlier clinic version | Open gap | No downgrade command. Restore the backup instead. |
| Upstream S3 migration scripts for stock media | Default off | The S3 backend is not the clinic storage decision (D06). |
| Model selection page **Modellwahl** and automatic dictation summaries | In verification | Not part of the current released build. |
| Shares and public links, self-registration, incognito mode, event extraction, cloud transcription connectors | Upstream only | Removed from the fork. |

## Before you start

- **No clinic image is published.** The publish workflow of the repository (`.github/workflows/docker-publish.yml`) and `config/docker-compose.example.yml` name the upstream image `learnedmachine/speakr` (upstream Speakr). `docker compose pull` with that file fetches upstream Speakr, not the clinic build. Build the clinic image from the branch `main` (step 2).
- **No environment template is complete.** No example file in the repository sets `CLINIC_MODE`. `clinic-spec/config/upstream.env.example` calls itself "Existing upstream flags; NOT a complete deployment or implementation." `config/env.transcription.example` still sets the hosted text model `https://openrouter.ai/api/v1` and the upstream variables `ADMIN_USERNAME`, `ADMIN_EMAIL` and `ADMIN_PASSWORD`. Both must be changed for the clinic build (step 3).
- **The installation guide is upstream text.** The [installation guide](../getting-started/installation.md) has no clinic content: no `CLINIC_MODE`, no first administrator, no account adoption, no takeover of recordings. Its update procedure with `docker compose pull` fetches upstream Speakr.
- **Who does it.** The steps below need access to the server and its environment. In the web administration, adoption needs an account with both **Administrationsrolle** (administration role) and **Identitätsadministration** (identity administration), the status **Aktiv** (active), no pending password change and a local sign-in in the last 300 seconds. Pool setup needs an active site administrator. See [User Management](user-management.md) for the two flags and [Group Management](group-management.md) for the pool roles.

## Upgrading an upstream installation to the clinic build

Run the steps in this order.

### 1. Back up

Back up before you change the image:

- the database: the SQLite file (default URI `sqlite:////data/instance/transcriptions.db`) or a PostgreSQL dump;
- `UPLOAD_FOLDER` (default `/data/uploads`);
- the bucket, when `FILE_STORAGE_BACKEND=s3`;
- `.env` and the session signing key: `SECRET_KEY`, or the generated key file (`SECRET_KEY_FILE`, else `secret_key` next to the SQLite database, else in `/data/instance`).

Back up the database before the file stores. The complete backup list of a running clinic installation, including `CLINIC_ORIGINALS_ROOT`, is in [File storage](storage.md#backups). No restore has been tested, and the production template has no approved recovery values.

### 2. Build the clinic image

Check out the branch `main` of the clinic fork and build the image from its Dockerfile ([Dockerfile, clinic fork](https://github.com/joehomeskillet/speakr/blob/main/Dockerfile)):

```bash
docker build -t <image>:<tag> .
```

- `--build-arg LIGHTWEIGHT=1` installs `requirements.lock` instead of `requirements-embeddings.lock`.
- The build pins its inputs: the Python base image by digest, the Python packages with `--require-hashes`, and FFmpeg, the `dss-decode` binary and Tailwind with SHA-256 checks.
- The image sets `CLINIC_DSS_CODEC_BIN=/usr/local/bin/dss-decode` for DS2 and DSS dictation files, and gunicorn serves on port 8899. Outside Docker, set `CLINIC_DSS_CODEC_BIN` yourself. Without it, an uploaded `.ds2` or `.dss` file is stored, but its recording ends with the status FAILED.
- The image contains the clinic contract files in `clinic-spec/config`.

Use this image in your deployment instead of `learnedmachine/speakr`.

### 3. Adjust the environment

| Variable | Action | Reason |
|---|---|---|
| `CLINIC_MODE` | Set to `1`. | Turns on clinic mode. The value is read on every call. |
| `ADMIN_USERNAME`, `ADMIN_EMAIL`, `ADMIN_PASSWORD` | Remove. | The Docker entrypoint would create an administrator without clinic account policy, who cannot sign in. Use the bootstrap script instead (step 5). |
| `TRANSCRIPTION_BASE_URL` | Point it to a local OpenAI-compatible ASR service, for example `http://speaches:8000/v1`. | In clinic mode an empty or non-local URL is refused; step 4 describes the effect on the start. Local means a loopback, private or link-local address, a single-label host name, or a name that ends in `.local`, `.internal`, `.lan` or `.localhost`. |
| `ASR_BASE_URL` | Use only a local WhisperX or whisper-asr-webservice host. | This URL is not checked for locality in clinic mode. |
| `TRANSCRIPTION_CONNECTOR` | `openai_whisper`, `asr_endpoint` or empty. | The upstream cloud connectors are removed. Their names fail with `Unknown connector: <name>. Available: [...]`. `clinic_stub` is a synthetic test connector and produces no real transcripts. |
| `TEXT_MODEL_BASE_URL`, `TEXT_MODEL_API_KEY` | Set both to a local text model, or remove both. | A hosted URL locks chat, Inquire and the generative summary routes in clinic mode. Without either value, no LLM client is created. Setting only `TEXT_MODEL_API_KEY` also creates a client, which uses the default hosted address `https://openrouter.ai/api/v1`. See the warning below. |
| `ENABLE_INQUIRE_MODE` | `false` | With `true`, every start still chunks and embeds up to 10 existing recordings, and every finished recording is queued for indexing, without a clinic check. |
| `SQLALCHEMY_DATABASE_URI` | Keep it, or point it to PostgreSQL. | Decision D04: PostgreSQL in integration and production. No tool in the repository moves data from SQLite to PostgreSQL. |
| `CLINIC_ORIGINALS_ROOT` | Optional. | Root of the clinic original store. Default: `UPLOAD_FOLDER`. |
| `CLINIC_CONFIG_DIR` | Usually unset. | Default: `clinic-spec/config` in the application directory. A missing contract file is an error. |
| `CLINIC_PROFILE` | Unset or `production`; never `staging` outside a test installation. | Unset: the KIS adapter is disabled, so final release stays closed. `staging`: synthetic mock KIS adapter (Simulated; no KIS evidence). `production`: `clinic_stub` and the mock KIS adapter refuse to run. See [Group Management](group-management.md). |
| `CLINIC_AGENT_PROXY_SECRET`, `CLINIC_AGENT_MASTER_SECRET` | Only with the Windows import agent: at least 32 bytes each, for example `<secret>`. | Without valid values, every agent request is refused with 401. The import-agent channel is Prepared, disabled: no supported route, command or script registers an agent. See [Recording sessions](recording-sessions.md#import-agent-channel-and-ingress). |
| `CLINIC_PASSWORD_BLOCKLIST_FILE` | Optional. | Site password blocklist, one entry per line. |

!!! warning "Automatic titles and summaries have no locality check"
    In clinic mode, every finished transcription queues a follow-up job. When an LLM client exists, this job sends the transcript to the text model for the title, and for the summary unless automatic summaries are switched off. This path has no locality check of its own (open gap). An LLM client is created as soon as `TEXT_MODEL_BASE_URL` or `TEXT_MODEL_API_KEY` is set; without `TEXT_MODEL_BASE_URL` it uses the hosted default `https://openrouter.ai/api/v1`. With a hosted address, transcripts go to that service. Never set only `TEXT_MODEL_API_KEY`. Set both values to a local text model, or remove both. The local-model rule is in [Model Configuration](model-configuration.md#the-local-model-rule).

Further notes:

- **Retention.** `ENABLE_AUTO_DELETION`, `GLOBAL_RETENTION_DAYS` and `DELETION_MODE` keep their names, but every mode only archives from the first run after the upgrade. See [Retention](retention.md#upgrade-behavior).
- **Upstream flags without effect.** `ENABLE_PUBLIC_SHARING` and `ENABLE_INTERNAL_SHARING` are not read. `ENABLE_SSO` and the `SSO_*` variables do not enable sign-in in clinic mode. `WEBHOOK_GLOBAL_ENABLED` has no effect while webhooks are locked. See [System Settings](system-settings.md#upstream-settings-without-effect-in-clinic-mode) and [Sign-in and identity providers](sso-setup.md).
- **Speech recognition.** CPU and GPU setup, timeouts and the single CPU fallback are in [Local speech recognition](whisperx-setup.md).

### 4. Start the application

The first start with the clinic image upgrades the schema. There is no separate migration command: no Alembic and no `flask db upgrade`.

At every start that initializes the database, the application:

1. runs `db.create_all()` and creates missing tables;
2. takes the migration lock: an advisory lock on PostgreSQL, a lock file in the temporary directory on SQLite. After 120 seconds without the lock it continues, because every migration is idempotent. Each process runs the migrations once (the entrypoint, the admin script, each gunicorn worker);
3. creates the ledger table `schema_migrations` if it is missing;
4. runs the migration sections one after another. The first section, "clinic integrity", creates all missing `clinic_*` tables, runs the create steps of the clinic modules and installs the database triggers. One create step adds the nullable columns `active`, `valid_from` and `valid_to` to `clinic_author_pool` and `clinic_staff_pool` where they are missing; `NULL` in `active` means active. The section "core recording and user columns" widens `user.password` from VARCHAR(60) to TEXT, which the clinic password hashes (scrypt) need; existing hashes are copied unchanged (log: `Widened user.password from VARCHAR(60) to TEXT`);
5. sweeps the clinic original store, in clinic mode only (see [File storage](storage.md#startup-sweep)).

The triggers exist on SQLite and PostgreSQL only. They refuse `UPDATE` and `DELETE` on `clinic_original_version` and `clinic_transcript_revision`, and make `clinic_output_receipt`, `clinic_marker_event`, `clinic_asr_profile_version`, `clinic_asr_training_run`, `clinic_voice_consent` and `clinic_voice_consent_audit` append-only, and require a rising `generation` in `clinic_review_lease`. Other database engines get no triggers.

Check the start log:

| Log line | Meaning |
|---|---|
| `DATABASE MIGRATION: <n> section(s) failed: <names>` (ERROR) | One or more sections failed. The application keeps starting, and the web interface shows no banner. Each section is retried at the next start. The following ERROR lines of the banner also name the issue tracker of upstream Speakr; they are inherited from upstream. |
| `Clinic original sweep: scanned=<n> removed_leftovers=<n> incomplete=<n> awaiting_row=<n> errors=<n>` | The sweep of the original store ran. |
| `Transcription connector initialized: <name>` | The connector started. |
| `Failed to initialize transcription connector: Failed to initialize connector '<name>': …` (ERROR), then `Falling back to legacy transcription configuration validation` | The connector was refused (empty or non-local URL, empty `TRANSCRIPTION_API_KEY`, unknown name). If `TRANSCRIPTION_BASE_URL` or `TRANSCRIPTION_API_KEY` is empty and `USE_ASR_ENDPOINT` is not `true`, the start then ends with `ERROR: No transcription service configured!` (exit code 1): the entrypoint stops, and so does every gunicorn worker that loads the application. If both are set, for example a non-local URL with a key, the application starts, logs `Using Whisper API: <url>`, and no transcription works. |
| `clinic policy overrides stored setting <key>=<value> (effective: False)` (WARNING) | An upstream token setting is stored as enabled, but the clinic ceiling forces it off. See [System Settings](system-settings.md#clinic-ceilings). |

The Docker entrypoint checks only for the SQLite file `/data/instance/transcriptions.db`. With PostgreSQL, it prints `Database doesn't exist. Creating new database...` at every start. The message has no meaning there; the schema work runs when the application is loaded.

**Web-only processes.** `CLINIC_WEB_ONLY=1` makes a process a temporary HTTP worker for an already initialized clinic installation. Such a process requires clinic mode, runs no DDL, no migration and no sweep, and checks only that the table `clinic_dictation` exists. It needs the existing upload folder and the existing session signing key and never generates a key. A value other than `0` or `1` stops the entrypoint with exit code 64. After every upgrade, start a normal process first.

### 5. Create the first administrator

After the upgrade, no existing account can sign in (step 6). Create the first administrator once at the server console, in the environment of the application:

```bash
python scripts/clinic_bootstrap_admin.py --username <username> --email <email-address> [--name "<display name>"]
```

The password comes from `CLINIC_BOOTSTRAP_PASSWORD` (or the variable named by `--password-env`), from `--password-stdin` or from a hidden prompt. It is never printed. It needs 15 to 128 characters. The account gets **Administrationsrolle** and **Identitätsadministration**, the sign-in policy **Nur lokal** (local only), and no clinical role. A second run prints `Already initialised; nothing changed.`.

Usernames and e-mail addresses of existing upstream accounts are taken. Choose new ones, otherwise the script ends with `Error: account data rejected (duplicate).` and exit code 2. Options, outputs and exit codes are in [User Management](user-management.md#creating-the-first-administrator).

### 6. Adopt existing accounts

Existing upstream accounts have no clinic account policy. In clinic mode, the local sign-in accepts only an active account with the sign-in policy **Nur lokal** (local only) or **Lokal und Entra** (local and Entra). An account without policy is refused with `Anmeldung fehlgeschlagen.` (sign-in failed).

Find the accounts without policy on the page **Benutzer und Rollen** (users and roles, `/admin#/benutzer`) with the filter **Keine Anmelderichtlinie** (no sign-in policy), or in the output of `scripts/clinic_roles_report.py` (step 9): in `accounts`, such an account has `"status": null`.

An identity administrator adopts one account at a time. The page has no control for this:

```http
PUT /admin/users/<id>
Content-Type: application/json

{"adopt_existing_local": true, "expected_version": 0}
```

The body must contain exactly these two keys. The request needs the session of an account with both **Administrationsrolle** and **Identitätsadministration**, the status **Aktiv**, no pending password change and a local sign-in no older than 300 seconds, and the CSRF token of that session. Details are in [User Management](user-management.md#existing-accounts-without-account-policy).

- **Result.** The account gets the status **Aktiv** (active) and the sign-in policy **Nur lokal**. **Identitätsadministration** stays off. The audit log records `admin.user.changed` with the field `local_account_policy_adopted`.
- **Refusals.** HTTP 409 with `Diese Änderung ist im aktuellen Kontozustand nicht möglich.` (this change is not possible in the current account state) and the code `no_local_credential` (no bcrypt or scrypt password hash, for example an account that only used single sign-on), `external_identity_present` (an upstream SSO link, an Entra link or a link request), or `existing_account_policy` (the account already has a policy that is not active **Nur lokal**). Any other body gives 400 `invalid_request`.
- **Administrators.** The upstream administration flag is kept. An adopted upstream administrator is therefore an active site administrator: this account sees every clinic dictation and can open any transcript and audio, and each opening is audited. Remove the **Administrationsrolle** where it is not wanted (see [User Management](user-management.md#editing-roles)).
- **Passwords.** The existing password stays valid. The sign-in does not apply the 15-character minimum to it. A bcrypt hash is replaced by the clinic scheme at the next successful sign-in.
- **Accounts that cannot be adopted.** The clinic build offers them no sign-in: the upstream single sign-on routes refuse in clinic mode. A new local account is a separate user with its own id.
- **Rights.** An adopted account has only the safe own defaults (view, play and edit own dictations). **Exportieren** (export), **Freigeben** (release), **Originalaudio exportieren** (original-audio export) and every other action need Fachrechte. See [User Management](user-management.md).

### 7. Set up author pools

A recording becomes a dictation only if its owner is in an active author pool that is inside its validity window. This applies to the takeover of legacy recordings as well as to new web recordings and uploads.

- Author pools can be created only through the API: `POST /api/clinic/v1/pool-admin/pools` with `kind` set to `author` and a `name`, by an active site administrator.
- Members are added on the page **Diktierende und Autorenpools** (authors and author pools, `/admin#/diktierende`) in the editor **Pools verwalten** (manage pools).
- Create staff pools (**Schreibkräfte-Pools**) on the page **Schreibkräfte-Pools** (`/admin#/schreibkraefte-pools`). Typists see dictations through staff pools that are linked to the author pool. Linking a staff pool to an author pool is API-only: `POST /api/clinic/v1/pool-admin/pools/author/<pool_id>/links` with `{"staff_pool_id": "<id>"}`.
- Keep each author in exactly one author pool. The server does not refuse a second membership. The takeover uses the first routable pool in id order.

All routes and pages are described in [Group Management](group-management.md).

### 8. Take over legacy recordings

Recordings from the upstream installation have no clinic dictation. Until they are taken over, they are missing under **Aufnahmen** (recordings) and in the **Arbeitsliste** (worklist).

Run the script inside the application container, with the environment of the application:

```bash
# Dry run (default): one line per candidate, nothing changed
python scripts/clinic_backfill_dictations.py

# Create the dictations
python scripts/clinic_backfill_dictations.py --apply

# List dictations without an initial audio revision; with --apply, create them
python scripts/clinic_backfill_dictations.py --init-audio
python scripts/clinic_backfill_dictations.py --init-audio --apply
```

What the takeover does:

- It considers every recording with a stored audio path. Only recordings with the status COMPLETED or FAILED are taken over.
- It creates one dictation per recording through the same original store as a web upload. The author is the recording owner, the pool is the owner's author pool. The dictation starts in the workflow state **Neu angelegt** (newly created).
- The time of the dictation is the meeting date of the recording when it is set, otherwise its creation time. The new anchor recording carries the meeting date, the status and the duration of the source.
- A completed transcript becomes the first ASR text revision of the dictation. If storing it fails, the whole dictation is rolled back.
- The takeover copies only the audio, the transcript text, the meeting date, the status and the duration. Title, summary, notes, tags and folder of the source recording are not copied to the dictation; they stay unchanged on the source recording in the database.
- The source recording and its file are only read, never changed or deleted. The audio then exists twice: as the source and as the clinic original. Plan disk space for this (see [File storage](storage.md)).
- Each recording is its own transaction, so a failure skips only that recording. The idempotency key is `legacy:<recording id>`, so a second `--apply` creates nothing.
- Each created dictation is audited as `recording.edited` with `actor_kind` `system` and the field `clinic_backfill`.

Output lines carry ids, the action and the reason only, never text: `recording=<id> owner=<id> action=<create|skip> reason=<reason>`, with ` dictation=<id>` after a creation. The last line is `dry run: <n> to create, <m> skipped; nothing changed` or `created dictations: <ids>` (or `none`).

| Reason | Action | Meaning |
|---|---|---|
| `with_transcription` | create | Taken over with its transcript as the first ASR revision. |
| `without_transcription` | create | Taken over without text, for example a FAILED recording. |
| `already_linked` | skip | Already taken over, or already linked by the web upload. |
| `not_finished` | skip | Not COMPLETED or FAILED. A later run picks it up. |
| `no_audio_file` | skip | The audio file is missing in storage. |
| `no_author_pool` | skip | The owner is in no routable author pool. Set one up (step 7) and run again. |
| `asr_failed` | skip | Storing the transcript failed. The dictation was rolled back; a new run retries. |
| `ingest_<code>` | skip | The original store refused the file. |
| `ingest_failed` | skip | Another error. Check the log line `legacy backfill: recording <id> failed (<type>)`. |

**`--init-audio`** repairs dictations without an initial audio revision. It does not load the full application, so it runs no migration and no sweep. It needs an existing database; on SQLite, the dry run opens the file read-only. Each line reads `dictation=<id> action=<action> reason=<reason>`, with the reasons `missing_audio_revision` (dry run), `initialized`, `already_initialized`, `no_original`, `no_dictation`, `original_read_failed`, `invalid_storage_key`, `database_failed` (action `error`), and the probe codes `missing_original_file`, `original_hash_mismatch`, `audio_too_short` and `audio_<status>` (for example `audio_decoder_missing` without `CLINIC_DSS_CODEC_BIN`).

Exit codes: `0` done, `2` clinic mode is off (`Error: clinic mode is off (CLINIC_MODE).`), `1` other error (with `--init-audio`, also when a database write failed).

### 9. Check the result

- `python scripts/clinic_roles_report.py` prints a read-only JSON snapshot: accounts with their flags and policy status, pools, memberships, links, Fachrechte, dictations with workflow state and revisions, job counts by type and status, the most frequent content reads, and the last 10 originals with `source_recording_id` for taken-over recordings. It does not load the web application and runs no migration. It needs `CLINIC_MODE` and `SQLALCHEMY_DATABASE_URI`; a SQLite path must be absolute. It prints ids and states, no names.
- Sign in with an adopted account and open the **Arbeitsliste**.
- Signed in, `GET /api/system/info` shows the active transcription connector.
- In **System-Auditprotokoll** (system audit log, `/admin#/system/audit`), check `admin.user.changed` for the adoptions and `recording.edited` for the takeover. See [Audit log](audit-log.md).

## What does not carry over

| Upstream data or function | In the clinic build |
|---|---|
| Public share links and internal shares | Upstream only. The code is removed. The tables `share`, `internal_share`, `shared_recording_state` and `share_audit_log` stay in the database without a model. The clinic build does not run the upstream copy step of `share_audit_log` into `audit_event`. An installation that ran upstream Speakr v0.11.1 has already copied those rows at startup; they stay in `audit_event` with upstream share event codes (`share.internal.*`, `share.public.*`) that the clinic event catalog does not list. |
| Self-registration, e-mail verification, password reset by e-mail | Upstream only. Removed. Accounts are created by an identity administrator and activated with a one-time code. See [User Management](user-management.md) and [Email Setup](email-setup.md). |
| Incognito mode, event extraction and calendar export | Upstream only. Removed. |
| Cloud transcription connectors | Upstream only. Removed. Only `openai_whisper`, `asr_endpoint` and `clinic_stub` remain. |
| Upstream single sign-on | Upstream only. The routes refuse in clinic mode, and SSO-linked accounts cannot be adopted. See [Sign-in and identity providers](sso-setup.md). |
| Existing upstream accounts | Cannot sign in until adopted (step 6). |
| Existing recordings | Not shown under **Aufnahmen** and in the **Arbeitsliste** until taken over (step 8). |
| Title, summary, notes, tags and folder of taken-over recordings | Not copied to the dictation (step 8). |
| Groups, team administrators, group tags and group folders | Upstream only. Locked (feature `internal_sharing`). Existing upstream groups, their members and team-admin roles do not become staff pools. A staff pool is stored as a new row in the same `group` table. A pool name that an upstream group already uses is refused with 409 `name_taken`. Personal tags stay available. See [Group Management](group-management.md). |
| Speaker profiles and voice samples | Upstream only. The routes are locked. A purge function for the stored upstream voice data exists, but nothing calls it. See [Privacy](privacy.md). |
| Personal API tokens | Upstream only. Requests with a token are refused with 403 `clinic_feature_disabled`. |
| Webhooks | Upstream only. Locked; nothing is dispatched. See [Webhooks](webhooks.md). |
| Inquire, Ambrose and the vector store | Upstream only. Hidden or locked. See [Ambrose](ambrose.md) and [Vector store](vector-store.md). |
| Stored default prompts | The admin tab is hidden. The German clinic prompts apply when the stored admin prompt is unset or equal to the upstream default; another stored prompt still wins. See [Prompts](prompts.md). |
| Deletion by retention, manual deletion of recordings | Retention only archives. Manual deletion is locked in clinic mode. See [Retention](retention.md). |
| Stored token settings `api_query_tokens_enabled`, `sso_api_tokens_enabled` | Forced to `false` by the clinic ceiling. See [System Settings](system-settings.md#clinic-ceilings). |

## Upgrading between clinic versions

The released version is the branch `main` of the clinic fork.

1. Back up the database first, then the file stores (see [File storage](storage.md#backups)).
2. Build the image from the new state of `main` (step 2 above).
3. Start one normal process first, not a web-only process. It runs the migrations of the new version. Check the log lines of step 4.
4. Then start the web-only processes, if you use them.

Migrations are idempotent, and a failed section is retried at the next start. There is no downgrade command. The pool column migration has a downgrade function, but no command calls it. To go back to an earlier version, restore the database and file backups taken before the upgrade. The same applies to a return to upstream Speakr: after a sign-in, the clinic build replaces bcrypt hashes with scrypt hashes, which upstream Speakr cannot verify. After a restore, the startup sweep reports files without a row as `awaiting_row`; keep them (see [File storage](storage.md#startup-sweep)).

### Model selection and dictation summaries (In verification)

The page **Modellwahl** (model selection) and automatic dictation summaries are not part of the current released build. Labels and availability may change. In the code under verification, an upgrade to that version behaves as follows:

- The start creates the new table `clinic_dictation_summary` like every other clinic table. No data is migrated.
- Only an active site administrator can open **Modellwahl** (under **Verwaltung › System**, administration › system; `/admin#/system/modelle`). The selection is stored in the system setting `clinic_task_models`. Without a saved selection, every task inherits the default of the installation or of the local service. A saved model identifier applies only while the respective service is local.
- An automatic summary is created only for the first ASR text of a new dictation. It needs a ready local text model, automatic summaries not switched off (system setting `disable_auto_summarization` or the user's own preference), and view and edit rights of the author. Existing dictations, including taken-over ones, get none automatically.
- A user with edit rights can request a summary once the current text has at least 10 characters and the local text model is ready; without a ready local model the request is refused with 503.
- For dictations linked to a source recording, the follow-up job creates only the title, also when it was queued before the upgrade. For taken-over dictations, the stock summary of the source recording is shown as outdated.

See [Model Configuration](model-configuration.md#automatic-dictation-summaries-in-verification).

## Transcription settings from older Speakr releases

Speakr v0.8 introduced transcription connectors. Older environment variables still work in the clinic build.

### Deprecated variables

| Deprecated variable | Status | Migration |
|---|---|---|
| `USE_ASR_ENDPOINT=true` | Still works, logs a deprecation warning | Set `ASR_BASE_URL` instead. |
| `WHISPER_MODEL` | Still read when `TRANSCRIPTION_MODEL` is empty; no warning is logged | Use `TRANSCRIPTION_MODEL`. |

### Connector selection

| Variable | Description |
|---|---|
| `TRANSCRIPTION_CONNECTOR` | Explicit connector: `openai_whisper`, `asr_endpoint` or `clinic_stub`. Optional. |
| `TRANSCRIPTION_MODEL` | Model name for `openai_whisper`. |

The connector is chosen in this order:

1. `TRANSCRIPTION_CONNECTOR`, when it is set;
2. `asr_endpoint`, when `ASR_BASE_URL` is set (or the deprecated `USE_ASR_ENDPOINT=true`);
3. `openai_whisper` with `TRANSCRIPTION_MODEL`, else `WHISPER_MODEL`, else `whisper-1`.

The upstream step that selected OpenAI Transcribe for `gpt-4o` model names does not exist in the clinic build.

### Examples

From the legacy ASR configuration:

```bash
# Before
USE_ASR_ENDPOINT=true
ASR_BASE_URL=http://whisperx-asr:9000

# After: ASR_BASE_URL alone selects asr_endpoint
ASR_BASE_URL=http://whisperx-asr:9000
```

`ASR_BASE_URL` must point to a local host. The application does not check it. `ASR_DIARIZE` defaults to `true` for this connector. In clinic mode, voice embeddings of diarized speakers are not stored and upstream speaker identification is locked; see [Local speech recognition](whisperx-setup.md).

From a legacy Whisper configuration to a local OpenAI-compatible service (values from `config/env.transcription.example`):

```bash
# Before
TRANSCRIPTION_BASE_URL=https://api.openai.com/v1
TRANSCRIPTION_API_KEY=<secret>
WHISPER_MODEL=whisper-1

# After
TRANSCRIPTION_BASE_URL=http://speaches:8000/v1
TRANSCRIPTION_API_KEY=not-needed-for-local
TRANSCRIPTION_MODEL=Systran/faster-distil-whisper-large-v3
```

In clinic mode, `TRANSCRIPTION_BASE_URL` must be set and local. An empty value is refused, and the application does not start (`ERROR: No transcription service configured!`, step 4); it does not fall back to a hosted API. A non-local value is refused as well; with a key set, the application starts, but no transcription works. The connector needs a non-empty `TRANSCRIPTION_API_KEY`, also for a local service.

### Chunking

| Connector | Chunking |
|---|---|
| `asr_endpoint` | Handled by the ASR service. With `ASR_ENABLE_CHUNKING=true` (default `false`), the application splits files itself, based on `ASR_MAX_DURATION_SECONDS` (default `7200`). |
| `openai_whisper` | The application splits large files: `ENABLE_CHUNKING` (default `true`), `CHUNK_LIMIT` (for example `20MB`) and `CHUNK_OVERLAP_SECONDS` (default `3`). |
| `clinic_stub` | Handled by the connector; the application does not split files. |

### Upstream UI changes

The upstream page lists changes of the stock recording view (speaker identification button, speaker counts at reprocessing, bubble view). They describe upstream Speakr. In clinic mode, upstream speaker identification is locked. Upstream only.

### Checking the connector

- Look for deprecation warnings in the application log, for example `USE_ASR_ENDPOINT=true is deprecated.`.
- Signed in, `GET /api/system/info` shows the connector and its capabilities. Only an account with **Administrationsrolle** (administration role) also sees the service addresses. The legacy field `whisper_endpoint` shows `https://api.openai.com/v1` when `TRANSCRIPTION_BASE_URL` is unset, for example with the `asr_endpoint` connector. It is a default label only; nothing is sent there. With `openai_whisper` and no `TRANSCRIPTION_BASE_URL`, the application does not start (step 4).
- Upload a synthetic test file and check that it is transcribed.

## Upstream S3 migration scripts

Status: Default off. The S3 backend is unchanged from upstream and works for stock media. Decision D06 in `clinic-spec/config/decisions.json` reads "Existing local persistent storage adapter; no new S3 cluster", so object storage is not the clinic storage plan. What the bucket holds in the clinic build is described in [File storage](storage.md#s3-backend-in-the-clinic-build).

The scripts work on `recording.audio_path` only. They never read, move or delete clinic originals or audio segments. The anchor recordings of clinic dictations have no audio path, so the scripts do not touch them. Run them inside the application container.

### Phase 1: keep the local backend

With `FILE_STORAGE_BACKEND=local` (the default), all reads and writes go through the storage service but still use local files.

### Phase 2: normalize legacy paths

Older recordings can have absolute or relative `audio_path` values. This script converts them to the `local://` locator format without moving files:

```bash
python scripts/migrate_local_paths_to_local_locator.py --dry-run
python scripts/migrate_local_paths_to_local_locator.py
```

| Flag | Description |
|---|---|
| `--dry-run` | Preview without writing to the database |
| `--limit N` | Process only the first N records |
| `--recording-id ID` | Process one recording |
| `--only-user ID` | Process the recordings of one user |
| `--allow-missing-file` | Normalize even if the local file is missing |
| `--report-jsonl <path>` | Write a JSONL report of all actions |

The script is idempotent.

### Phase 3: send new uploads to S3

```bash
FILE_STORAGE_BACKEND=s3
S3_BUCKET_NAME=<bucket>
S3_ENDPOINT_URL=<endpoint>          # for MinIO or another S3-compatible store
S3_ACCESS_KEY_ID=<access-key-id>
S3_SECRET_ACCESS_KEY=<secret>
S3_USE_PATH_STYLE=true              # required for MinIO
```

After a restart, new uploads go to the bucket, and existing `local://` recordings are still served from the local filesystem. Clinic originals stay local in every case.

!!! note "CORS for presigned URLs"
    The application serves S3 audio with a redirect to a presigned URL. When the web origin and the object store origin differ, the bucket must allow cross-origin requests from the web origin of your installation. At minimum allow the methods `GET` and `HEAD`, the header `Range` (recommended for seeking) and your web origin.

    Example for AWS S3 (adjust the origin):

    ```json
    [
      {
        "AllowedHeaders": ["*"],
        "AllowedMethods": ["GET", "HEAD"],
        "AllowedOrigins": ["https://speakr.example.com"],
        "ExposeHeaders": ["Accept-Ranges", "Content-Length", "Content-Range", "Content-Type", "ETag"],
        "MaxAgeSeconds": 3000
      }
    ]
    ```

    For MinIO, configure equivalent rules for the bucket.

### Phase 4: move existing files to S3

```bash
python scripts/migrate_local_recordings_to_s3.py --dry-run
python scripts/migrate_local_recordings_to_s3.py --limit 100
```

| Flag | Description |
|---|---|
| `--dry-run` | Preview without uploading or changing the database |
| `--limit N` | Migrate at most N records per run |
| `--recording-id ID` | Migrate one recording |
| `--only-user ID` | Migrate the recordings of one user |
| `--verify-size` | Compare the uploaded size with the local size. Always on; the flag cannot switch it off. |
| `--delete-local-after-success` | Delete the local file after a successful upload |
| `--report-jsonl <path>` | Write a JSONL report |

The script skips recordings that are already on `s3://`, recordings without a `local://` locator, recordings with the status PROCESSING or QUEUED, and recordings whose local file is missing. It is safe to run again. Run it in batches until nothing is left.

### Optional: backfill cached durations

Older recordings can have no cached `audio_duration_seconds`. This script fills it with `ffprobe`, for `local://` and `s3://` recordings:

```bash
python scripts/backfill_audio_duration_seconds.py --dry-run
python scripts/backfill_audio_duration_seconds.py --report-jsonl <report-file>.jsonl
```

It changes only recordings where the value is empty, and skips recordings whose audio was deleted and recordings with the status PROCESSING or QUEUED. Flags: `--dry-run`, `--limit N`, `--recording-id ID`, `--only-user ID`, `--report-jsonl <path>`, `--ffprobe-timeout SECONDS` (default `30`).

### Phase 5: remove local copies

The migration script keeps the local files unless you pass `--delete-local-after-success`. Clinic originals are never part of this step.

### Checking the S3 migration

1. Playback of migrated stock recordings redirects to presigned URLs.
2. New uploads have an `audio_path` that starts with `s3://`.
3. A repeated recognition works; the worker copies the object to a temporary local file for it.
4. Older recordings have an `audio_duration_seconds` value after the backfill.

In clinic mode, retention only archives and manual deletion is locked, so neither removes bucket objects. The ownerless-recording cleanup still deletes recordings without an owner, with their media; see [Retention](retention.md).

### Rollback

1. Set `FILE_STORAGE_BACKEND=local`.
2. Restart the application.
3. New uploads go to local storage again.
4. Existing `s3://` recordings keep working as long as the S3 settings stay configured.

Both backends can be used side by side.
