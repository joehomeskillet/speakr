---
layout: default
title: Retention & Automatic Archival
parent: Admin Guide
nav_order: 7
---

# Automatic Archival and Retention Policies

The clinic fork moves eligible recordings into the archive. The retention run preserves stored media, original files, transcripts, metadata, child rows, clinical revisions and the exact versions referenced by releases. Archived recordings remain available through the archive filter, subject to the existing access permissions.

## Configuration

The existing configuration names remain accepted for compatibility. The admin page labels these settings **Automatic archival**, **Archive after days** and **Archive and preserve all data**.

```dotenv
ENABLE_AUTO_DELETION=false
GLOBAL_RETENTION_DAYS=90
DELETION_MODE=archive
```

| Setting | Behavior |
|---|---|
| `ENABLE_AUTO_DELETION` | Enables the retention archival sweep for both scheduled and administrator-triggered runs. |
| `GLOBAL_RETENTION_DAYS` | Days since recording creation before archival. With `0`, only applicable positive tag periods cause archival. |
| `DELETION_MODE=archive` | Archives eligible recordings and preserves their files and database rows. |
| Existing `audio_only` or `full_recording` mode | Uses the same preserving archival behavior. Existing configuration remains readable. |

Values saved in System Settings take precedence. Environment values apply until an administrator saves an override. The daily maintenance scheduler evaluates the current settings at run time and runs at 02:00 in server time. Tag-only policies also run when the global period is zero.

Archival retains storage usage. Capacity planning must account for the archived media and the growing audit journal.

## Eligibility and Policy Selection

The sweep considers recordings with status `COMPLETED` or `FAILED`. It reloads and locks each candidate before applying a policy. Recordings already archived by retention are skipped. The recording must have a creation time strictly earlier than the run time minus its effective retention period.

| Rule | Effect |
|---|---|
| Recording `deletion_exempt` | Keeps the recording out of automatic archival. |
| Applicable tag `protect_from_deletion` or `retention_days=-1` | Keeps the recording out of automatic archival. |
| Applicable positive tag periods | The shortest positive tag period replaces the global period. |
| No applicable positive tag period | Uses the positive global period, when configured. |
| No positive period | Keeps the recording in its current state. |

Group tags apply to a recording. Personal tags apply only when they belong to the recording owner. A recipient's private tags cannot change the retention policy for the owner's recording.

Folder retention and folder protection fields remain stored for compatibility. The current retention service evaluates recording exemptions, tags and the global setting. Folder-based archival needs a separate scope decision. Use a protected tag for an implemented exemption.

## Archive State and Restoration

Retention sets `recording.retention_archived_at`, which applies to every viewer. Personal archival uses the existing owner `is_archived` flag or a recipient's `SharedRecordingState.is_archived` flag. The effective archive state combines the recording-wide and personal states.

The regular recording list excludes archived items by default. Select the archive filter to see them. A user can archive or restore their personal view using the existing web controls. Only the owner can restore a recording-wide retention archive. That operation clears the retention timestamp and the owner's personal archive flag. Recipients' personal flags keep their existing values.

Restoring a recording keeps its creation date and retention policy. An old restored recording can therefore be archived again on the next sweep. Apply an exemption or adjust its policy when it should stay active.

The media and transcript remain available to authorized viewers after archival. A file that had already been removed before archival remains absent.

## Mandatory Audit Evidence

Every actual archive or restore transition writes the recording state, an `audit_event` row and an append-only `clinic_archive_event` row in one transaction. Failure to write either evidence row rolls back the transition. Repeating a request for the current state creates no additional transition.

| Evidence | Recorded value |
|---|---|
| Event | `recording.archived` or `recording.unarchived` |
| Actor | System for the scheduled sweep, signed-in administrator for a manually triggered sweep, session user or API token for a user action |
| Object | Recording ID, recording creation time, owner ID and target user where applicable |
| Time and revision | UTC event time and a monotonically increasing recording archive revision |
| Rule | `retention.global`, `retention.tag`, `user.archive` or `user.restore` |
| Scope | `recording` for retention and its restoration, `personal` for a personal change |
| Policy detail | Retention days and selected tag ID when applicable |

Evidence contains identifiers and fixed policy metadata. Archive transitions require auditing even when the general audit logging switch is off. A context that suppresses audit writing prevents the transition. The audit pruning job preserves archive and restore events. Database triggers reject changes to the durable archive ledger, whose identifiers also survive subsequent permitted account or recording deletion.

Concurrent writers serialize on the recording row on PostgreSQL. A unique recording-generation/revision constraint additionally rejects conflicting ledger entries. A failed or conflicting transaction leaves the prior state intact and can be retried.

The retention run handles each recording in a separate transaction. Successfully archived recordings remain committed when a later candidate fails. Inspect the `errors` counter and retry failed candidates after correcting the cause.

## Administrator Endpoints

The existing URLs remain available to authenticated administrators.

```http
POST /admin/auto-deletion/run
GET /admin/auto-deletion/stats
```

A successful sweep returns counters in this shape.

```json
{
  "checked": 150,
  "archived": 45,
  "already_archived": 28,
  "deleted_audio_only": 0,
  "deleted_full": 0,
  "exempted": 12,
  "skipped_no_retention": 6,
  "errors": 0
}
```

The two historical deletion counters remain zero. `checked` covers settled candidates, including those subsequently skipped. Counts may also leave candidates within their retention period. The stats endpoint reports `deletion_mode` as `archive` and counts recording-wide retention archives in `archived_count`. The dashboard archive count includes both owner-personal and retention archives, counting a recording once.

## API Compatibility

API v1 keeps its existing default of including both active and archived recordings. Clients can request `archived=false` or `archived=true` to select the effective state for the caller. Recording-wide archival updates the change feed's `is_archived` field for synchronization. The existing manual save, toggle, bulk update and API update routes use the same audited transition service.

The historical `/api/recordings/archived` endpoint continues to list recordings whose audio was removed, identified by `audio_deleted_at`. The archive filter in the regular recording list selects the current archive state.

## Existing Manual Deletion and Independent Maintenance

The manual recording and media deletion routes retain their existing behavior and access checks. An archived recording remains subject to an explicitly permitted manual deletion. The archival sweep preserves speaker profiles and embeddings and no longer invokes orphan-speaker cleanup.

The following existing services have their own lifecycle. Their interaction with the clinic-wide preservation requirement is recorded under **OFFENE FRAGEN** in the work-package report. They require a separate decision before a claim covering every automatic cleanup path is possible.

## Deletion Records for API Clients

When an existing manual or independent cleanup path deletes a recording, Speakr keeps a small record of the deletion: the recording id, the user, the time and the reason. No title or content is kept. API clients read these records from the [changes feed](../user-guide/api-reference.md#changes-feed) to remove their own copy of the recording.

The records are removed after `RECORDING_TOMBSTONE_DAYS` days (default 90), checked every hour. A client whose last sync is older than that starts a full sync again.

```bash
# Days to keep deletion records for API clients (default 90)
RECORDING_TOMBSTONE_DAYS=90
```

## Webhook Delivery Records

Every webhook delivery is stored with its payload. The payload holds the recording id and, unless `webhook_omit_titles` is on, the title of the recording. These records are removed `webhook_delivery_retention_days` days after their creation (default 30), checked every hour. With `0`, the records are kept without limit. Details are given in [Webhooks](webhooks.md#retention-of-delivery-records).

```bash
# Days to keep webhook delivery records (default 30, 0 keeps them)
WEBHOOK_DELIVERY_RETENTION_DAYS=30
```

## Exported Files

With automatic export, the exported file of a deleted recording is renamed with the `[deleted]_` prefix and kept. With `AUTO_EXPORT_DELETE_WITH_RECORDING=true`, the file is deleted together with the recording, when an existing deletion path removes the recording. Archival preserves the exported file. See [Exported files of deleted recordings](system-settings.md#exported-files-of-deleted-recordings).

## Recordings Without an Owner

Before the fix for #572, only the media of a deleted user's recordings was removed. The rows, with their transcripts, summaries, notes and embeddings, were kept without an owner. These leftovers are removed by a daily step at 2:00 AM. The step runs whether or not retention archival is enabled. This independent cleanup still deletes ownerless records and is an open scope question for the clinic deployment.

- **Two runs**: a recording without an owner is only marked on the first run. It is deleted on a later run, together with its media, shares and embeddings, once the mark is at least 20 hours old.
- **Exclusions**: recordings with an owner are never candidates. A recording with a queued or running job, an active recording session or an unmerged upload part is skipped, and its wait starts again on the next run. A recording that regains an owner loses its mark.
- **Files on disk**: media files without a recording are only counted in the log and never deleted by this step. To remove them, use the command in [Files Without a Recording](#files-without-a-recording).

Each run is logged as one line beginning with `Ownerless recording cleanup:`, with the counts of marked, deleted and skipped recordings.

## Files Without a Recording

In older releases, a recording's media file could remain in the uploads folder after the recording itself was deleted. Such files take up space and belong to no user. You can list them, and remove them on request, with an admin command run inside the container:

```bash
# Dry run: count, total size and the first 20 files; nothing is changed
docker compose exec app python scripts/clean_unreferenced_files.py

# Every file, and every recording whose file is missing
docker compose exec app python scripts/clean_unreferenced_files.py --list

# Delete the files, after a confirmation prompt
docker compose exec app python scripts/clean_unreferenced_files.py --delete

# Move the files to a quarantine folder instead, keeping their relative paths
docker compose exec app python scripts/clean_unreferenced_files.py --move-to /data/quarantine
```

| Option | Effect |
|---|---|
| (none) | Dry run: the count, total size and first 20 files are printed and nothing is changed. |
| `--list` | Every unreferenced file and every row whose file is missing are printed. |
| `--delete` | The unreferenced files are deleted after a confirmation prompt. |
| `--move-to DIR` | The unreferenced files are moved below `DIR` after a confirmation prompt. Local storage only; `DIR` must lie outside the scanned folders. |
| `--yes` | No confirmation prompt is shown, for use in scripts. |
| `--min-age-hours N` | Files changed within the last `N` hours are left alone (default 48). |

**References.** A file is in use when a recording (`audio_path`), an upload part awaiting a join (`audio_path`) or a recording session (`upload_filename`) points to it. Paths are resolved by the storage layer, so `local://` locators, older absolute paths such as `/data/uploads/...` and older relative paths all count. A file with the same name as a recording whose file is missing is also kept, since it may be that recording's file after the uploads folder moved.

**Scope.** Only plain files directly in `UPLOAD_FOLDER` (the layout of older releases) and the files below `UPLOAD_FOLDER/recordings` (or the configured `FILE_STORAGE_KEY_PREFIX`) are examined. The in-progress folders `_sessions` and `_staging`, the waveform cache `_peaks` (see [Waveform cache](storage.md#waveform-cache)), all other top-level folders, folders whose name starts with `_` or `.`, hidden files and symbolic links are skipped. A path that resolves outside the uploads folder is never touched. Before each file is removed, its references and age are checked again.

**S3 storage.** With `FILE_STORAGE_BACKEND=s3`, the objects under the key prefix in the configured bucket are examined in the same way and can be deleted with `--delete`. `--move-to` is not available for S3 objects.

**Rows without a file.** Recordings and upload parts whose file is missing are reported and left unchanged. Recordings whose audio was deleted on purpose, for example by a historical audio-only retention policy, are not reported.

No job workers or schedulers are started by the command, so you can run it while the server is running. A backup of the uploads folder, or a first pass with `--move-to`, is advisable before the first `--delete`.


## Upgrade Behavior

Startup creates the archive evidence table and its immutability guards. Additive migration creates nullable `retention_archived_at` and non-null `archive_revision` with default `0` on existing recording tables. Existing personal archive values and recording content are retained. Existing archive flags are treated as the starting state, without inventing historical transition events.

Repeated startup retains archive evidence and revisions. Existing deletion-mode values use archival immediately after the upgrade. Stored files previously removed by older releases remain absent.

## Operational Checks

Use the retention stats endpoint to inspect eligibility and exemptions. Check `recording.archived` and `recording.unarchived` in the audit log and the sweep's `errors` counter. When restoration is refused, verify that the caller owns the recording-wide archive. When restored recordings reappear in the archive, check their original creation date and effective tag/global policy.

See [Audit log](audit-log.md) for event fields and auditing settings.
