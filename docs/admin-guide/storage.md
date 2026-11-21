# File Storage
<small>[Deutsch](../de/admin-guide/storage.md)</small>

The clinic build keeps audio in two separate places:

- The **clinic original store** holds the immutable original of every clinic dictation, the audio of final live-dictation phrases and the audio segments recorded in the audio editor. It is always a directory on the local filesystem of the app, set with `CLINIC_ORIGINALS_ROOT`.
- The **storage backend** from upstream Speakr (`local` or `s3`, set with `FILE_STORAGE_BACKEND`) holds the media of stock recordings: the upload recording of a browser upload or browser recording, the working copy of an import that is sent to speech recognition, and the recordings that existed before the installation was switched to the clinic build. A stock recording is a row of the upstream Speakr recording model (`recording`), as opposed to a clinic dictation. On this page, "stock" always means this upstream model and its routes.

The clinic rules on this page apply with `CLINIC_MODE` on. With clinic mode off, a web upload creates no dictation, the startup sweep of the original store does not run, and the storage backend behaves as in upstream Speakr.

Upstream Speakr documentation (pinned original; describes the two backends without the clinic original store): [File Storage](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/storage.md).

## Status overview

| Function | Status | Notes |
|---|---|---|
| Clinic original store on the local filesystem | Available | Independent of `FILE_STORAGE_BACKEND`. |
| Database triggers on originals and transcript revisions | Available | SQLite and PostgreSQL only. |
| Startup sweep of the original store | Available | Clinic mode only; reports, removes only leftover hard links. |
| Non-destructive audio editing, rendered on request | Available | Edits never write the original. |
| Database triggers on the audio edit tables | Prepared, disabled | The installer exists but startup does not call it. The application refuses changes through ORM listeners only. |
| Local storage backend for stock media | Available | Default; unchanged upstream. |
| S3-compatible storage backend for stock media | Default off | Unchanged upstream. Not part of the clinic storage decision (see [S3 backend in the clinic build](#s3-backend-in-the-clinic-build)). |
| Hash-verified original-audio output with an unsent `.eml` file | Prepared, disabled | The module has no caller. A local mail draft with the audio stays an open gap (WG-EXP-009). |
| Removal of stored files or bucket objects by retention | Upstream only | The retention sweep archives and deletes nothing; the remaining deleting paths are listed in [Retention](retention.md#paths-that-still-delete-data). |
| Approved backup and restore values | Open gap | Recovery values in the production template are `null`. See [Backups](#backups). |

## Who configures storage

- All settings that decide where data is stored are environment variables of the deployment (`.env` or the container environment). None of them is a system setting: **Systemeinstellungen** (System settings) does not list them, and no page of the web administration changes them. The operator of the server sets them. The **Administrationsrolle** (administration role) has no storage control in the web UI.
- Changes take effect when the app processes are restarted.
- The per-account limit **Speicherlimit (MB)** (storage limit) is set in **Benutzer und Rollen** (Users and roles). Only a local identity administrator can change it: an account with **Administrationsrolle** (administration role) and **Identitätsadministration** (identity administration), status **Aktiv** (active), and a sign-in within the last 300 seconds. The **Administrationsrolle** alone is not enough. When an account is created with an empty field, it gets the system setting `default_storage_quota_mb` (default `0`, no limit; see [System settings](system-settings.md)). When an account is edited, an empty field removes the limit. See [User management](user-management.md) for the full requirements.

## Where data is stored

| Data | Location | Store |
|---|---|---|
| Original of a dictation (browser upload, browser recorder, import agent, legacy backfill) | `<CLINIC_ORIGINALS_ROOT>/clinic/originals/v1/<sha256>`. The file name is the SHA-256 of the idempotency key, not of the content, and has no extension. | Clinic original store, always local |
| Audio of a final live-dictation phrase | Same folder; the file name is the SHA-256 of `live:<session id>:<sequence>`. The row is in `clinic_live_phrase`. | Clinic original store, always local |
| Audio segment recorded in the audio editor for an insert or an overwrite | `<CLINIC_ORIGINALS_ROOT>/audio-segments/<dictation id>/<uuid>.wav` | Clinic original store, always local |
| Upload recording of a browser upload or browser recording | `<FILE_STORAGE_KEY_PREFIX>/<YYYY>/<MM>/<recording id>/<timestamp>_<file name>` | Storage backend |
| Working copy of an import for speech recognition | `clinic/imports/asr/<sha256>/Diktat.<ext>`. `<sha256>` is the file name of the original in the original store; `<ext>` is the detected container format, `bin` when unknown. The key prefix is not applied. | Storage backend |
| Browser playback copy of a DS2/DSS stock recording (clinic mode) | `<stem>.playback.mp3` next to the stock file (MP3, 64 kbit/s, 16 kHz, mono). Created at transcription, or on the first playback or waveform request. | Local storage backend only. With `s3`, transcription writes the copy next to its temporary download in the system temporary directory, where no process removes it. |
| Recordings from before the switch to the clinic build, also when the legacy backfill takes them over | Their existing locator, unchanged | Storage backend, or an older local path |
| Upload chunks of the import agent | Database table `clinic_agent_chunk`. The chunks are deleted when the ingest is stored or aborted. Chunks of sessions that end `INVALID`, expire without an abort or stay `UNKNOWN` are not removed; no cleanup job exists. | Database |
| Chunks of server-side recording sessions | `UPLOAD_FOLDER/_sessions/<session id>/` | Always local; see [Recording sessions](recording-sessions.md) |
| Transcript revisions, the audio edit list, releases, export and output receipts | Database tables | Database |
| Rendered audio of the current edit state, waveform of an audio revision, TXT, DOCX and bundle exports | Not stored. They are computed for each request. | None |
| Waveform cache of stock recordings | `UPLOAD_FOLDER/_peaks` | Always local |
| Staging of uploads and conversions | `FILE_STORAGE_STAGING_DIR` | Always local |

A browser upload therefore exists at least twice: as the upload recording in the storage backend and as the original in the original store. An import exists as the original and as its working copy, and a legacy takeover as the source recording and the original.

## Configuration

### Clinic original store

| Variable | Default | Description |
|----------|---------|-------------|
| `CLINIC_ORIGINALS_ROOT` | value of `UPLOAD_FOLDER` | Root of the original store. Originals and audio segments are always written here on the local filesystem, whatever `FILE_STORAGE_BACKEND` says. |
| `UPLOAD_FOLDER` | `/data/uploads` | Local upload root of the app, and the default root of the original store. |

- A process started with `CLINIC_WEB_ONLY=1` requires the existing upload directory, runs no migration and no sweep, and writes no schema.
- Keep the root path stable. `clinic_original_version` stores each key relative to the root. Imports that still wait on the **Klärliste** (clarification list) are different: their rows in `clinic_import_original` also store the absolute root path in effect at import, and a later assignment opens the file there.
- `CLINIC_ORIGINALS_ROOT` must be on a filesystem that supports hard links and `fsync` on directories. Originals and audio segments are published with `os.link`. When the link fails, the delivery fails: a browser upload is refused with 500 `clinic_ingest_failed`, a recorded segment with 503.

!!! warning "Key prefix and original store must not overlap"
    Never set `FILE_STORAGE_KEY_PREFIX` to `clinic`, `audio-segments` or a path below them, and never place `CLINIC_ORIGINALS_ROOT` inside `UPLOAD_FOLDER/<FILE_STORAGE_KEY_PREFIX>`. `scripts/clean_unreferenced_files.py` walks that tree and counts as referenced only the paths that stock recording rows and upload join parts store. It would list originals, live-phrase audio and audio segments as unreferenced, and `--delete` or `--move-to` would remove them.

### Storage backend (unchanged upstream)

All settings are read from the environment. Only `FILE_STORAGE_BACKEND` is required for local; the `S3_*` values are required when the backend is `s3`. In the clinic build these settings govern the stock recording media and the import working copies. They never move clinic originals or audio segments.

| Variable | Default | Description |
|----------|---------|-------------|
| `FILE_STORAGE_BACKEND` | `local` | Storage backend: `local` or `s3`. |
| `FILE_STORAGE_KEY_PREFIX` | `recordings` | Key prefix (path) for stored recordings within the backend. Import working copies do not use it. |
| `FILE_STORAGE_STAGING_DIR` | `<UPLOAD_FOLDER>/_staging` | Local directory for staging uploads and conversion before the final store. |
| `S3_BUCKET_NAME` | (none) | Target bucket. Required when the backend is `s3`. Without a bucket, no S3 backend is built. |
| `S3_REGION` | (none) | Region for the bucket. Required by most providers. |
| `S3_ENDPOINT_URL` | (none) | Service endpoint. Leave unset for AWS S3; set it for MinIO and other S3-compatible providers. A value without a scheme gets `https://`. |
| `S3_ACCESS_KEY_ID` | (none) | Access key. |
| `S3_SECRET_ACCESS_KEY` | (none) | Secret key. |
| `S3_SESSION_TOKEN` | (none) | Optional session token for temporary credentials. |
| `S3_USE_PATH_STYLE` | `false` | Use path-style addressing. Required for MinIO; leave `false` for providers that use virtual-hosted-style. |
| `S3_VERIFY_SSL` | `true` | Verify TLS certificates. Set `false` only for a local MinIO without TLS. |
| `S3_PRESIGN_TTL_SECONDS` | `900` | Lifetime of presigned URLs for playback and download (15 minutes). |
| `S3_PRESIGN_PUBLIC_TTL_SECONDS` | `300` | Shorter presign lifetime read by the storage factory (5 minutes). The audio routes in this build pass `is_public=False` and use `S3_PRESIGN_TTL_SECONDS`. Public share links do not exist in this build. |

- `S3_INTRANET_ENDPOINT_URL` is read at start, but no code uses it. It has no effect.
- S3 support needs the `boto3` package. The lock files pin `boto3==1.43.118`, and an image built from the fork installs it from them.

## Clinic originals

### How an original is written

1. The bytes are written to a temporary `.partial-` file in the target directory and flushed to disk (`fsync`).
2. Their SHA-256 must match the declared hash. Otherwise the file is discarded and the delivery is refused.
3. The file is published under its final name with a hard link. An existing file of that name is never replaced: it must hold the same bytes, otherwise the delivery is refused as a conflict.
4. The directory is flushed, and the published file is hashed again.
5. Only then is the row written to `clinic_original_version`: hash, size, the key relative to the root and a JSON receipt. The dictation and its anchor recording are created in the same transaction. The anchor recording has no `audio_path`, so a stock convert job cannot rewrite the original, and it carries `deletion_exempt`.

Consequences:

- When the database commit fails, the file stays without a row. The same delivery again reuses the file and adds the row. The log shows `clinic ingest <key>: receipt transaction failed after the file was stored`.
- The same idempotency key with the same bytes returns the existing receipt. It adds no second file and no second row.
- An original can be at most 2,147,483,647 bytes.
- When the original cannot be decoded at ingest, the receipt is kept and the first audio revision is deferred (log line `clinic ingest: initial audio revision deferred (<code>)`). `python scripts/clinic_backfill_dictations.py --init-audio` lists the dictations without an audio revision; with `--apply` it creates the missing revisions.

Which bytes become the original:

- **Browser upload and browser recorder.** The file that the upload pipeline stored for the upload recording at that moment. With convert workers (`CONVERT_QUEUE_WORKERS`, default `2`), a readable audio file is stored as uploaded and converted later in the upload recording only. With `CONVERT_QUEUE_WORKERS=0`, the upload pipeline converts every file during the request, so the original store receives the converted file. See [Recording sessions](recording-sessions.md) for the upload paths.
- **DS2 and DSS dictation files.** The original and the stock file are never converted. Transcription decodes them. In clinic mode, the stock playback route and an active site administrator's opening of a dictation's audio serve a separate MP3 copy stored next to the stock file (see [Where data is stored](#where-data-is-stored)). The stock download returns the DS2/DSS file itself. With `s3`, both routes redirect to the DS2/DSS object, which browsers cannot play.
- **Import agent.** The bytes assembled from the upload chunks at finalize.
- **Legacy backfill** (`scripts/clinic_backfill_dictations.py`). A copy of the stock recording's audio, read through the storage backend. The source recording and its file are only read. See [Migration guide](migration-guide.md).

### Immutability and verification

- Database triggers on SQLite and PostgreSQL reject `UPDATE` and `DELETE` on `clinic_original_version` and `clinic_transcript_revision`. On SQLite the messages are "clinic original is immutable" and "clinic original cannot be deleted" for originals, and "clinic transcript revision is immutable" and "clinic transcript revision cannot be deleted" for transcript revisions. On PostgreSQL, one trigger per table rejects both `UPDATE` and `DELETE` with "clinic original is immutable" or "clinic transcript revision is immutable". The migration section "clinic integrity" installs them at every start that initializes the database. Other database engines get no triggers.
- No application path rewrites or deletes an original file once it is published and its second hash matches, even when its row is never written. Before that point, the application removes only its own temporary names: every delivery removes its `.partial-` spool name, also when the declared hash does not match, and a just-published file whose second hash fails is deleted again before any row exists. The [startup sweep](#startup-sweep) removes leftover `.partial-` hard links.
- The filesystem does not protect the files. Originals are created with mode `0600` and are not made read-only; recorded audio segments are set to `0400`. A person with filesystem access as the app user or as root can change or delete them.
- Reads check the hash. Every rendering verifies each source against the SHA-256 in its row ("stored audio source does not match its hash"). Playback and waveform requests then answer 503, and a repeated recognition refuses with `reprocess_audio_unavailable`. A repeated recognition of a dictation without an audio revision reads the original directly, checks SHA-256 and size, and refuses with `reprocess_audio_integrity_failed` on a mismatch.

### Startup sweep

In clinic mode, every start that initializes the database sweeps `<CLINIC_ORIGINALS_ROOT>/clinic/originals/v1`. A process with `CLINIC_WEB_ONLY=1` does not run it.

| Case | Meaning | Action |
|---|---|---|
| `removed_leftovers` | A `.partial-` name that is a hard link of a final file (crash between publication and cleanup) | The extra name is removed. The bytes stay under the final name. |
| `incomplete` | A `.partial-` file without a final file and older than one hour | Reported only. It may hold the only copy of a dictation. |
| `awaiting_row` | A final file without a `clinic_original_version` row | Reported only. A repeated delivery adds the row. Audio files of live-dictation phrases and imports still waiting on the **Klärliste** have no such row either and are counted here as well. |

Log lines:

- `Clinic original sweep: scanned=<n> removed_leftovers=<n> incomplete=<n> awaiting_row=<n> errors=<n>` (INFO).
- `clinic ingest sweep: {…}` with the same counts, as WARNING when `incomplete` or `errors` is not zero.
- `Clinic original sweep failed: <error>` (ERROR). A failed sweep never stops the start.

Check `incomplete` and `awaiting_row` after a crash or a restore before you remove any file by hand. The log shows counts only, not file names, and no admin page lists the files. Live-dictation phrase audio and imports waiting on the **Klärliste** keep `awaiting_row` above zero in normal operation, so compare the counts with those of an earlier start.

## Audio edits, renders and exports

**Audio editing.** Edits are non-destructive: an edit decision list over the immutable original, with at least 16 undo steps. The tables `clinic_audio_edit_operation`, `clinic_audio_revision`, `clinic_audio_revision_snapshot`, `clinic_audio_segment` and `clinic_audio_action_log` are append-only through ORM listeners. Their database triggers are prepared but not installed at start, so raw SQL is not blocked. `clinic_audio_session_state` holds the current undo and redo stacks and is updated.

**Recorded segments.** `POST /api/clinic/v1/dictations/<dictation id>/audio-segments` accepts WAV, WebM, Ogg or MP4 up to 32 MiB. The audio is decoded to a 16 kHz mono 16-bit WAV of at most 64 MiB and published with a hard link under `audio-segments/`. An existing file is never replaced. When the request does not commit, the published file is removed again. Once its row is committed, the application never removes it.

**Rendered audio.** `GET /api/clinic/v1/dictations/<dictation id>/audio/derived` renders the current edit state in memory for every request, as mono 16-bit PCM WAV. Nothing is stored, and the answer carries `Cache-Control: no-store`. Each delivery is audited as `recording.content.read` with `part=audio_derived`. The waveform of an audio revision for the correction view is computed from such a rendering on request. It is not cached in `_peaks`, and each computation is audited with `part=audio_peaks`. A repeated recognition renders the current edit state into a temporary directory.

**Text exports.** In the view **Ausgabe** (output), TXT and DOCX are rendered in memory from the revision that the active release binds. The job record keeps release, revision, file name and SHA-256 in a database receipt. No route returns the file bytes; the status reads **Datei erzeugt (noch nicht gespeichert)** (file created, not yet saved). The bundle download `GET /api/clinic/v1/bundles/<dictation id>/export?format=txt|rtf|sdict` builds the file from the current revision for each request and stores nothing. Unmanaged (BYOD) clients get 403.

**Original audio.** The right **Originalaudio exportieren** (export original audio, action `audio_export`) controls the stock download `/audio/<recording id>?download=true`. That route serves the file of the stock recording from the storage backend (a redirect with `s3`). It does not read the original store and does not check the file against the original's hash. For a browser upload, that file can be the converted copy (see [Which bytes become the original](#how-an-original-is-written)), so the download is not guaranteed to equal the original. The hash-verified output module for originals has no caller. See [User management](user-management.md) for the right.

## S3 backend in the clinic build

Status: Default off. The S3 code is unchanged from upstream and works for stock media. Decision D06 in `clinic-spec/config/decisions.json` reads "Existing local persistent storage adapter; no new S3 cluster", so object storage is not the clinic storage plan.

With `FILE_STORAGE_BACKEND=s3`:

- Upload recordings and import working copies of dictations go to the bucket. The bucket then holds dictation audio and needs the same protection as the app server. See [Privacy](privacy.md).
- Clinic originals, audio segments, the waveform cache, the staging folder and the session folder `_sessions` stay on the local filesystem. The `uploads` folder therefore still holds recording audio and must stay in the backup.
- At a browser upload, the app downloads the upload recording from the bucket once to hash it and to write the original.

### How audio is delivered

In S3 mode, the audio routes return a `302` redirect to a short-lived presigned URL instead of streaming the file through the application. This covers the stock playback and download route and an active site administrator's opening of a dictation's audio (`/api/admin/clinic/dictations/<id>/audio`), which writes its audit row before the redirect. URLs expire according to `S3_PRESIGN_TTL_SECONDS`. Until then, the URL works for anyone who holds it. The rendered audio of the clinic editor never comes from the bucket, because it is computed from the local original store.

!!! warning "The presigned URL must be reachable by the browser"
    Because the browser is redirected to the object store, the value of `S3_ENDPOINT_URL` (or the AWS endpoint for your region) must be resolvable and reachable from the client, not only from the application container. If you place the app behind a reverse proxy, the object store endpoint must also be reachable by clients.

    When the app origin and the object store origin differ, the bucket must allow cross-origin requests from your web origin. The S3 part of the [Migration guide](migration-guide.md) has example CORS rules for AWS S3 and MinIO.

Provider examples (AWS S3, MinIO, Backblaze B2, Cloudflare R2, Wasabi) are in the upstream documentation linked above. Treat them as upstream reference, not as a clinic deployment recipe.

### Operational notes (unchanged upstream)

- **Mixed storage.** Speakr reads from both backends at once. After switching to `s3`, existing local recordings continue to be served from local storage and only new uploads go to the bucket.
- **Reprocessing and transcription.** Workers materialize an object to a temporary local file for transcription, then remove the temporary file. Large files therefore incur a download on each processing run.
- **Migrating existing recordings.** Switching the backend only affects new uploads. The S3 migration scripts in the [Migration guide](migration-guide.md) work on `recording.audio_path` only. They never read, move or delete clinic originals or audio segments.

## Waveform cache

This cache serves the stock peaks route `/audio/<recording id>/peaks` of stock recordings. The clinic correction view does not use it (see [Audio edits, renders and exports](#audio-edits-renders-and-exports)).

The waveform is computed from the audio the first time it is requested, and it is cached as a peaks file in `UPLOAD_FOLDER/_peaks`. In clinic mode, the peaks of a DS2/DSS stock recording are computed from its playback copy, which this request creates when it is missing.

- **Size.** About 1 MB per hour of audio, and at most about 4 MB for one recording.
- **Location.** The folder is always on the local filesystem of the app, also with `FILE_STORAGE_BACKEND=s3`. In S3 mode, the audio is downloaded once to a temporary file for the computation.
- **Backups.** The folder can be left out of backups. A missing peaks file is computed again on the next request.
- **Cleanup.** A peaks file is deleted with its recording or its audio, and it is replaced when the audio changes. The folder is skipped by `scripts/clean_unreferenced_files.py`.
- **Read-only storage.** When `UPLOAD_FOLDER` cannot be written, no peaks are computed, and one warning per app process is written to the log. Peaks files that already exist are still served.

## Retention, deletion and cleanup

- **Retention archives only.** No retention mode removes a file or a bucket object. Clinic dictation anchors are exempt from the retention sweep. Archival frees no storage. See [Retention](retention.md).
- **Manual deletion is locked.** The recording and audio deletion routes answer 403 `clinic_feature_disabled` in clinic mode (feature lock `clinical_user_hard_delete`).
- **Ownerless-recording cleanup.** Unchanged upstream. It still deletes recordings without an owner, with their media. See [Retention](retention.md#paths-that-still-delete-data).
- **Unreferenced files.** `scripts/clean_unreferenced_files.py` looks only at plain files directly in `UPLOAD_FOLDER` and at the tree below `<FILE_STORAGE_KEY_PREFIX>` (on S3, the objects under that prefix). It does not know the original store. With the default root and the default key prefix `recordings`, the folders `clinic/` and `audio-segments/` lie outside the scanned area, and so do the import working copies, whose keys start with `clinic/` (see the warning in [Clinic original store](#clinic-original-store)). No row points to the playback copies of DS2/DSS stock recordings, so the script lists those below the key prefix as unreferenced. A missing copy is created again on the next playback, and the copy is deleted with its recording. Options and examples are in [Retention](retention.md#paths-that-still-delete-data).
- **Capacity.** Once their row is committed, originals, live-phrase audio and audio segments are never removed by the application, and uploads, imports and legacy takeovers are stored at least twice (see [Where data is stored](#where-data-is-stored)). Plan disk space for the original store, the stock media with the playback copies, and the database growth together, including the agent chunk table `clinic_agent_chunk`.

## Backups

These notes follow from how the code writes data. No backup or restore procedure has been tested.

A complete backup covers:

- the database: the SQLite file (default `/data/instance/transcriptions.db`, set with `SQLALCHEMY_DATABASE_URI`; the app runs SQLite in WAL mode, so copy it together with its `-wal` and `-shm` files while the app is stopped, or use SQLite's online backup), or a PostgreSQL dump;
- `UPLOAD_FOLDER`: stock media and, with the default root, the original store with its audio segments. `_peaks` and the `.playback.mp3` copies can be left out; both are created again on request;
- `CLINIC_ORIGINALS_ROOT`, when it lies outside `UPLOAD_FOLDER`. It holds originals also with `s3`;
- the bucket, when `FILE_STORAGE_BACKEND=s3`; its durability depends on the provider;
- `.env` and the session signing key: `SECRET_KEY`, or the generated key file (`SECRET_KEY_FILE`, else `secret_key` next to the SQLite database, else in `/data/instance`). A process with `CLINIC_WEB_ONLY=1` never generates a key and refuses to start without it.

Order and restore:

- Back up the database before the file stores. The application writes an original before its row and never deletes it, so such a database copy references no original that the later file copy lacks. The reverse order can leave rows whose file is missing; playback of those dictations then fails with 503. This order protects the original store only. Stock media can be replaced by a conversion or deleted between the two copies, so the database copy can point to a stock file that the later file copy no longer has. For a consistent copy of both, stop the app processes and workers first.
- After a restore, the startup sweep reports files without a row as `awaiting_row`. Keep them; they may be the only copy.
- Restore the files with the owner of the app process. The originals are created with mode `0600`.

Not established:

- No approved recovery values exist. In `clinic-spec/config/production.template.json`, `recovery.rpo_by_failure_domain`, `recovery.rto_by_data_class`, `recovery.restore_evidence` and `retention.backup_days` are `null`. No restore has been tested.
- The Windows import agent keeps an encrypted spool on its workstation (`SpoolMaxBytes`, default 268435456 bytes). It is not part of the server backup, and the installation of the agent on real workstations is not tested.
