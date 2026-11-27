# File storage and recovery

[Deutsch](../de/admin-guide/storage.md)

A clinic dictation has an immutable original and receipt, working media and versioned text and audio edits. These are separate stores. Backing up only the ordinary upload directory or an S3 bucket is insufficient when the original store is elsewhere.

## Storage locations

| Data | Location |
|---|---|
| Accepted clinic originals | Local files under `CLINIC_ORIGINALS_ROOT/clinic/originals/v1/`; the receipt, size and content hash are in `clinic_original_version`. |
| Audio recorded into an edit | `CLINIC_ORIGINALS_ROOT/audio-segments/<dictation id>/` |
| Upload recordings, legacy recording media and import ASR working copies | The inherited recording storage backend, selected by `FILE_STORAGE_BACKEND` |
| Transcripts, revisions, edit decisions, context, releases, summaries, import and output receipts, audit evidence | Database tables |
| Server-side recording-session chunks | `UPLOAD_FOLDER/_sessions/` |
| Upload and conversion staging | `FILE_STORAGE_STAGING_DIR`, normally `UPLOAD_FOLDER/_staging` |
| Ordinary recording waveform cache | `UPLOAD_FOLDER/_peaks` |

The original store is always local to the application filesystem, even when ordinary recording media uses S3. A browser upload can therefore exist as both a recording file and an original; an imported file can exist as both an original and an ASR working copy. Plan capacity for both.

The clinic original is the exact byte stream accepted by the clinic ingest step. A browser upload may already have passed through upstream conversion before that step, depending on the upload path and worker configuration. Do not infer that every stored original equals the file originally selected in the browser without checking the receipt.

## Deployment configuration

The server operator configures storage through environment variables. The web administration does not move storage roots or switch backends. Account storage limits are separate; see [User management](user-management.md).

| Variable | Default | Purpose |
|---|---|---|
| `UPLOAD_FOLDER` | `/data/uploads` | Local upload root |
| `CLINIC_ORIGINALS_ROOT` | `UPLOAD_FOLDER` | Durable clinic originals and recorded audio segments |
| `FILE_STORAGE_BACKEND` | `local` | Ordinary recording media backend: `local` or `s3` |
| `FILE_STORAGE_KEY_PREFIX` | `recordings` | Prefix for ordinary recording objects; import working copies use their own keys |
| `FILE_STORAGE_STAGING_DIR` | `<UPLOAD_FOLDER>/_staging` | Local staging, also needed with S3 |

Keep the original root stable: clinic receipts keep relative keys, while import originals waiting for assignment also retain their storage root. The filesystem must support hard links and directory `fsync`. Check mounts, ownership and write permissions before ingesting data.

Do not overlap the original store with the ordinary recording-prefix tree. Inherited unreferenced-file cleanup understands recording references, not every clinic original, live phrase or audio segment. Setting the recording prefix to `clinic`, `audio-segments` or a parent of the original store can expose clinic data to that cleanup.

### S3-compatible recording storage

S3 support remains in the inherited backend. For clinic use, select an institution-controlled service and review its access and network policy. It does not move clinic originals, audio segments, session chunks or staging files to the bucket.

| Variable | Purpose |
|---|---|
| `S3_BUCKET_NAME` | Bucket; required to construct the S3 backend |
| `S3_REGION` | Provider region |
| `S3_ENDPOINT_URL` | Endpoint for an S3-compatible service |
| `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_SESSION_TOKEN` | Deployment credentials; session token is optional |
| `S3_USE_PATH_STYLE` | Path-style addressing; default `false` |
| `S3_VERIFY_SSL` | TLS certificate verification; default `true` |
| `S3_PRESIGN_TTL_SECONDS` | Lifetime of playback/download URLs; default `900` seconds |

Keep certificate verification enabled. The browser must be able to reach the endpoint used in a presigned URL. Such a URL can remain usable until its expiry, so protect it as a credential. The clinic fork does not offer public share links; the inherited public-link TTL is not a way to enable them.

Changing the backend does not migrate existing files. The upstream S3 migration utilities handle recording media, not the clinic original store. A bucket alone is therefore not a complete backup.

## Original integrity and revisions

Ingest writes a temporary file, flushes it, checks the declared size and SHA-256, and publishes it with a hard link without overwriting an existing original. It checks the published file again before storing the receipt. Repeating the same idempotency key with identical bytes reuses the accepted delivery; conflicting content is refused.

If the database transaction fails after publication, the file remains available for retry. It must not be removed merely because no receipt row is yet visible. Original filenames in `clinic/originals/v1/` derive from the idempotency key; the file's content hash is a separate receipt value.

SQLite and PostgreSQL triggers refuse updates and deletion of `clinic_original_version` and `clinic_transcript_revision`. Audio edits are non-destructive revision data over the original. Their ORM listeners refuse changes to append-only edit records, but the separate audio database-trigger installer is not called during startup. Filesystem access as the application user or root can still alter files; database immutability does not replace host protection.

Audio rendering checks its sources against the recorded hashes. Missing or changed sources can block playback or repeated recognition. A waveform cache cannot reconstruct missing audio samples.

### Startup sweep

Normal clinic database initialization scans the original store. A `CLINIC_WEB_ONLY=1` process skips initialization and this sweep. The log summarizes `scanned`, `removed_leftovers`, `incomplete`, `awaiting_row` and `errors`:

- `removed_leftovers`: an extra `.partial-` hard-link name was removed; its published file remains.
- `incomplete`: an old partial file has no published counterpart. It is reported, not deleted.
- `awaiting_row`: a published file has no `clinic_original_version` row. It is reported, not deleted; live phrases and imports waiting for assignment can also contribute to this count.
- `errors`: inspection or cleanup of an extra name failed.

Compare counts with an earlier start. Investigate unexpected increases after a crash or restore before removing anything. A sweep failure is logged but does not stop startup, so a running application is not proof that this check passed.

## Playback and original downloads

Playback, text export and original-audio export have separate permissions. A recording audio download requires the explicit `audio_export` right, including for the author. Site administration alone does not grant it.

The inherited recording download serves the media named by the recording's storage locator. It does not read the clinic original receipt or verify its hash; depending on the upload path, that media can be a converted working copy. Do not use successful playback or that download alone as evidence that the clinic original is recoverable.

For local DS2/DSS recording media, clinic playback can create a separate MP3 derivative while retaining the DS2/DSS file. With S3, the inherited media routes redirect to the stored object; a browser may not decode a DS2/DSS object. Check both the available source and the intended playback path when diagnosing missing sound.

The separate hash-verified original-output module is not connected to a supported user route. Creating a local mail draft with original audio remains open gap WG-EXP-009.

## Backups and recovery checks

1. Back up the database, ordinary media, the complete original root when separate, and the application instance directory as a consistent set. Protect deployment settings and signing-key backups separately with restricted access.
2. Include an S3 bucket when used, plus local session and staging locations appropriate to the recovery plan. Record which files are temporary and which are durable; a filename alone does not establish that distinction.
3. Restore into an isolated installation. Use a synthetic dictation to verify ownership, pool visibility, saved corrections and release/revision links after reload.
4. Play, seek and reopen the working audio. Separately compare the restored original's size and SHA-256 against its receipt and the synthetic source. A derivative is not a substitute for this check.
5. Record the backup date, restore result and any missing evidence without clinical content. Restrict access to the restored installation and backups.

Recovery values and restore evidence in the production template remain unset. Define them with the responsible operator. Automatic recording retention archives data; any file or object cleanup requires its own reviewed scope and recovery plan. See [Retention](retention.md) and [Privacy](privacy.md).
