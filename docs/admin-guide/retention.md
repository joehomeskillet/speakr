---
layout: default
title: Retention & Automatic Archival
parent: Admin Guide
nav_order: 7
---

# Automatic Archival and Retention Policies
<small>[Deutsch](../de/admin-guide/retention.md)</small>

In the clinic build, retention never deletes. Two separate mechanisms move data into an archive state:

- **Retention archival** (the daily sweep) marks old stock recordings (upstream `recording` rows) as archived for every viewer.
- **Dictation archival** moves a clinic dictation into the workflow state **Zu archivieren** (to be archived). It is a user action that needs its own right.

Neither mechanism removes a file or a database row. This page also lists the remaining maintenance paths that do delete data, the current state of the retention policy and the backup plan.

Upstream Speakr documentation (pinned original, describes deleting modes that do not apply to the clinic build): [Retention & Auto-Deletion](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/retention.md).

## Status Overview

| Function | Status | Notes |
|---|---|---|
| Retention archival sweep | Default off | `ENABLE_AUTO_DELETION=false`. Archives only. |
| Dictation action **Archivieren** (archive) | Available | Needs the right **Archivieren** in Fachrechte. |
| Clinic view **Archiv** (archive) | Available | Read only. |
| Deletion modes `audio_only` and `full_recording`, the **Audio deleted** filter, **Delete audio, keep transcript**, manual recording deletion | Upstream only | Every mode archives. Hard-delete routes are locked in clinic mode. |
| Approved clinic retention periods | Open gap | All retention values in `clinic-spec/config/production.template.json` are `null`. |
| Server-side backup expiry plan (`src/clinic/backup_retention_plan.py`) | Prepared, disabled | Plan only, no caller outside tests. |
| Backup folder retention of the Windows import agent | Default off | Deletes files in its backup folder when switched on. |
| Ownerless-recording cleanup | Available | Unchanged from upstream. Deletes recordings without an owner. Runs every day. |
| Audit log pruning | Upstream only (withheld in clinic mode) | Deletes nothing in clinic mode; the setting `audit_log_retention_days` is stored and shown (Available). See [Audit log](audit-log.md). |
| Upstream reset script `scripts/reset_db.py` | Available (upstream script); never run | Ships in the image and deletes data without asking. Never run it on a clinic installation. See [Upstream reset script](#upstream-reset-script). |

## What Archival Preserves

Retention archival sets `recording.retention_archived_at`, raises `recording.archive_revision` and writes the evidence described in [Mandatory audit evidence](#mandatory-audit-evidence). Dictation archival changes the workflow state and writes an archive book entry. Neither changes anything else:

- Stored media, transcripts, summaries, notes, tags and child rows of the recording stay unchanged.
- Clinic originals stay unchanged. Database triggers on SQLite and PostgreSQL reject `UPDATE` and `DELETE` on `clinic_original_version` and `clinic_transcript_revision`.
- Releases keep the exact audio, transcript and context revisions they bound. A later change appends a supersession row; the release row is not changed.
- A media file that an older software version had already removed stays absent.

Archival frees no storage. Capacity planning must include archived media and the growing audit journal.

## Configuration

The settings are edited by an administrator with the **Administrationsrolle** (administration role, `User.is_admin`) in **Verwaltung** (administration) › **System** › **Systemeinstellungen** (System settings, `/admin#/system/einstellungen`). The upstream key names stay for compatibility.

| Setting key | Environment variable | Default | Label (EN / DE) | Effect |
|---|---|---|---|---|
| `enable_auto_deletion` | `ENABLE_AUTO_DELETION` | `false` | Automatic archival / Automatische Archivierung | Turns the archival sweep on, for scheduled and manually triggered runs. In `.env`, only `true` (any case) counts as on. |
| `global_retention_days` | `GLOBAL_RETENTION_DAYS` | `0` | Archive after days / Archivieren nach Tagen | Days since creation before archival. With `0`, only positive tag periods apply. |
| `deletion_mode` | `DELETION_MODE` | `archive` (as shown in **Systemeinstellungen**; internal fallback `full_recording`, which also archives) | Archive and preserve all data / Archivieren und alle Daten erhalten | Legacy key. `archive`, `audio_only` and `full_recording` all archive. The edit dialog offers only `archive`. |

```dotenv
ENABLE_AUTO_DELETION=false
GLOBAL_RETENTION_DAYS=0
DELETION_MODE=archive
```

- A value saved in **Systemeinstellungen** takes precedence. The rows are created without a value, so the `.env` value applies until an administrator saves one.
- The sweep reads the current values on each run. No restart is needed.
- `config/env.asr.example`, `config/env.whisper.example` and `config/env.whisperx.example` still contain `GLOBAL_RETENTION_DAYS=90` and `DELETION_MODE=audio_only`, with upstream comments that describe deletion. These are example values, not defaults, and every mode archives.
- The start-up console line keeps the upstream wording: `Auto-deletion: Disabled (set ENABLE_AUTO_DELETION=true to enable)`, `Auto-deletion: Enabled (global retention: N days, mode: …)` or `Auto-deletion: Enabled but no global retention period set (configure GLOBAL_RETENTION_DAYS)`. It reads only `.env`, not values saved in **Systemeinstellungen**. With no value in `.env` or in **Systemeinstellungen**, **Systemeinstellungen** shows `archive`, while the code falls back to the upstream value `full_recording`, which also only archives; the start-up line then shows `mode: full_recording`.
- The entry `features.auto_deletion` in `clinic-spec/config/dev.defaults.json` is not read by the code. It does not switch archival on or off.
- Each change is audited as `admin.setting.changed` with the old and the new value (all three keys belong to the keys whose values are stored). See [System settings](system-settings.md).

## Schedule

One daily maintenance thread starts at 02:00 server-local time. Only one elected process runs it; a process started with `CLINIC_WEB_ONLY=1` starts no background work. The run has three steps in this order:

1. Retention archival, only when `enable_auto_deletion` is on. It also runs when the global period is `0`, so that tag-only policies apply.
2. The ownerless-recording cleanup, always (see [Paths that still delete data](#paths-that-still-delete-data)).
3. Audit log pruning, always. In clinic mode it deletes nothing (see [Audit log](audit-log.md)).

Log lines of the archival step: `Running scheduled archival...`, `Scheduled archival skipped (disabled)`, `Archived recording <id> after <n> retention days (…)`, `Error archiving recording <id> (<error type>)`, `Automatic archival completed: {…}` and `Scheduled archival completed: {…}`.

## Eligibility of Stock Recordings

The sweep considers recordings with status `COMPLETED` or `FAILED`. It locks each candidate and checks the status again. A recording already archived by retention is counted as `already_archived`. Its creation time must be strictly earlier than the run time minus the effective period.

| Rule | Effect |
|---|---|
| Recording flag `deletion_exempt` | The recording is not archived (counted as `exempted`). |
| Applicable tag with `protect_from_deletion` or `retention_days=-1` | The recording is not archived (counted as `exempted`). |
| Applicable positive tag periods | The shortest positive tag period replaces the global period. |
| No applicable positive tag period | The positive global period applies, when set. |
| No positive period | The recording stays as it is (counted as `skipped_no_retention`). |

Which tags apply:

- A group tag always applies. Group tag management is locked in clinic mode (feature `internal_sharing`), but group tags that already exist still count.
- A personal tag applies only when it belongs to the recording owner. Tags that other viewers attach to the recording do not change its period.
- Folder retention and folder protection fields are stored for compatibility and are not evaluated. Use a protected tag for an exemption.

Where the values are set:

- **Tag periods and protection:** avatar menu **Konto** (account) › tab **Tags & Ordner** (Tags & folders), in the tag editor. The section **Retention and automatic archival** / **Aufbewahrung und automatische Archivierung** is shown only while automatic archival is on. It has the field **Retention period (days)** / **Aufbewahrungsdauer (Tage)** and a checkbox. The checkbox currently shows the folder text **Folder protection (stored only)** / **Ordnerschutz (nur gespeichert)**; for a tag, the flag is evaluated, as its help text says: "Recordings with this tag are exempt from automatic archival." Set the protection while automatic archival is on: with archival off, a tag update that sends the protection (`retention_days=-1`) leaves it unchanged.
- **Recording exemption:** `POST /api/recordings/<id>/toggle_deletion_exempt`. In clinic mode the route guard needs the edit right on the recording, and for a recording linked to a clinic dictation also a current edit lease. The handler then admits only the owner or a site administrator. Each toggle is audited as `recording.edited` with the field `deletion_exempt` (only while the audit log is on). The clinic UI has no control for it, because the upstream recording views are not rendered in clinic mode.

## Clinic Dictations and the Sweep

Every clinic dictation has an anchor recording. Ingest creates it with `deletion_exempt=True` and the personal archive flag set, so the sweep skips it while the flag stays set. The clinic UI offers no control that clears it.

Uploads, imports and recordings taken over by the legacy backfill also have a separate stock recording: the upload recording, the import working copy for transcription or the legacy source recording. These rows are not created with the exemption. Once such a row is `COMPLETED` or `FAILED`, a positive policy can archive it. Archival deletes nothing, and the dictation's original stays in the original store in any case (see [Storage](storage.md)).

## Dictation Archival

**Status: Available.** It is meant for completed dictations, but the server accepts it from every workflow state except **Zu archivieren**; a confirmed request from that state is answered with 422 `TRANSITION_FAILED`.

**Where:** the workflow actions of a dictation offer **Archivieren** (archive) in the group **Archiv** (archive), and the review view offers the same action. The confirmation reads "Das Diktat wird archiviert. Es wird nichts gelöscht." (The dictation is archived. Nothing is deleted.)

**Who:** a user who holds the action **Archivieren** in **Fachrechte** (professional rights) for the dictation. Archive is not part of the safe own defaults, so authors need an explicit grant for their own dictations too. The site administrator flag does not include it, and neither do **Identitätsadministration** (identity administration) or **Leitung** (pool lead). Only an active site administrator changes Fachrechte, in **Benutzer und Rollen** (users and roles) › **Fachrechte** (see [User management](user-management.md)).

**API:** `POST /api/clinic/v1/dictations/<dictation uuid>/archive`

- Body exactly `{"action": "archive" | "delete", "confirm": true | false}`.
- Headers: `Idempotency-Key` (a UUID), `If-Match: "<dictation id>:<version>"` (428 without it) and the session CSRF token.
- `confirm: false` is a dry run. It changes no dictation, archive book entry or recording. Only the request receipt for its `Idempotency-Key` is stored, so a repeat with the same key returns the same answer. The answer has `dry_run` with `eligible`, `reason`, `legal_hold` and `would_delete`, which is always `false`.
- `confirm: true` moves the dictation to **Zu archivieren** from any other state.
- `"action": "delete"` needs the actions edit and delete. In clinic mode it leads to the same move to **Zu archivieren**. It is accepted only after a first `archive` request created the archive book entry; before that it is answered with 404. The Fachrechte UI does not offer the delete action.
- Without an archive book entry, the first `archive` request needs a current text revision, an audio revision and exactly one current context revision. Otherwise the UI does not offer **Archivieren**, and the endpoint answers 404 (`NOT_FOUND`, "Diktat nicht gefunden"), as for a missing right. An existing archive book entry keeps its stored links.

**What is written:**

- The archive book entry `clinic_archive_entry` keeps the links to the original, the audio revision, the transcript revision, the context revision and the active release, and a version. The stored result `clinic_archive_result` answers a repeated request with the same idempotency key. The application refuses deletes on both tables and updates on the result table (ORM listeners, no database triggers).
- The move itself writes no `audit_event` row.
- The anchor recording changes only when the dry run is eligible. Then the request archives it through the same path as the sweep, with `recording.archived` evidence. Because every anchor recording carries `deletion_exempt`, the dry run normally reports `legal_hold`, and `retention_changed` stays `false`.

## The Archiv View

**Status: Available.** Menu **Verwaltung & Seiten** (administration and pages) › **Diktat und Ausgabe** (dictation and output) › **Archiv** (`/#/archiv`).

- The view lists dictations in the workflow state **Zu archivieren**. It reads `GET /api/clinic/v1/recordings?status=zu_archivieren` and shows only dictations the caller may see in the worklist.
- It is read only. It has no restore, reopen or delete control. Note on the page: "Das Archiv wird nur gelesen. Bereits bestätigte Ausgaben bleiben unverändert." (The archive is read only. Confirmed outputs stay unchanged.)
- Columns: **Aufnahmezeit** (recording time), **Diktiert von** (dictated by), **Schreibkräfte-Pool** (staff pool), **Archiviert am** (archived on), **Aufbewahrung** (retention). Filters: **Von**, **Bis**, **Diktiert von**, **Schreibkräfte-Pool**.
- **Archiviert am** shows the retention archive time of the anchor recording when set, else the time of the archive book entry.
- The server sends no retention state, so **Aufbewahrung** always shows **Aufbewahrung nicht angegeben** (retention not stated).

## Retention Archive State and Restoration

Retention archival sets `retention_archived_at` and applies to every viewer. The owner's personal archive uses the `is_archived` flag. A recording counts as archived when either value is set.

In clinic mode the upstream recording sidebar with its archive filter is not rendered. The stock list `GET /api/recordings?archived=true` still returns the caller's own archived recordings.

Only the owner can lift a retention archive. Other callers get HTTP 403 `archive_restore_owner_required` ("Only the owner can restore a retention-archived recording.") from `POST /recording/<id>/toggle_archive` and from the recording update routes with `is_archived`. The bulk toggle (`POST /api/recordings/bulk-toggle`, `field: archive`) skips such a recording; it is missing from `affected_ids`. In clinic mode these routes need the edit right. A restore clears the retention timestamp and keeps the creation date, so the next sweep can archive the recording again. Set an exemption or change the tag period when it must stay active.

API v1 keeps returning archived and active recordings unless `archived=true` or `archived=false` is given. In clinic mode API tokens are refused, so API v1 is reachable only with a browser session. The old endpoint `/api/recordings/archived` lists recordings whose audio was removed (`audio_deleted_at`), not archived ones.

## Mandatory Audit Evidence

Every actual archive or restore transition of a recording writes three things in one transaction: the recording state, an `audit_event` row and an append-only `clinic_archive_event` row. If the audit row cannot be added, the transition is rolled back. A request for the current state creates no transition.

| Evidence | Recorded value |
|---|---|
| Event | `recording.archived` or `recording.unarchived` |
| Actor | Taken from the request: the system for the scheduled sweep, the signed-in administrator for a run through the endpoint, the session user for a user action |
| Object | Recording ID, recording creation time, owner ID and target user where applicable |
| Time and revision | UTC event time and the increasing recording archive revision |
| Rule (`details.rule`) | `retention.global`, `retention.tag`, `user.archive` or `user.restore` |
| Scope (`details.scope`) | `recording` for retention and its restoration, `personal` for a personal change |
| Policy detail | `retention_days` for retention rules, and `tag_id` when a tag period applied |

- Archive transitions are written even when `audit_log_enabled` is off.
- Audit pruning never removes `recording.archived` and `recording.unarchived`, with or without clinic mode.
- Database triggers on SQLite and PostgreSQL reject `UPDATE` and `DELETE` on `clinic_archive_event` ("clinic archive evidence is append-only"). The table has no foreign keys, so its rows outlive a later deletion of the recording.
- On PostgreSQL, concurrent writers wait for the row lock on the recording. A unique constraint on recording, creation time and revision also rejects conflicting ledger rows. A failed transaction leaves the previous state and can be retried.
- The sweep handles each recording in its own transaction. Recordings archived before a later failure stay archived. Check the `errors` counter and retry after fixing the cause.

## Administrator Endpoints

```http
POST /admin/auto-deletion/run
GET /admin/auto-deletion/stats
```

- Both need `User.is_admin`. In clinic mode both are ADMIN routes: the route guard refuses a caller without `User.is_admin` with 403 `forbidden` before the handler runs. The handler's own 403 `admin_required` applies only outside clinic mode.
- No page of the clinic admin calls them. API tokens are refused in clinic mode, so a call needs an administrator's browser session, and the `POST` also its CSRF token.
- With archival switched off, the run answers HTTP 200 with `{"error": "Automatic archival is not enabled", "code": "auto_deletion_disabled"}` (the message in the interface language) and writes no `admin.retention.run` row.

A run returns counters in this shape:

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

The two deletion counters always stay `0`. `checked` counts every `COMPLETED` or `FAILED` candidate, including those skipped later. A manual run is audited as `admin.retention.run` with `checked`, `archived`, `already_archived`, `deleted_audio_only`, `deleted_full` and `errors`. This row is not forced, so it is not written while the audit log is off.

The stats endpoint returns `enabled`, `global_retention_days`, `deletion_mode` (always `archive`), `eligible_count`, `exempted_count`, `no_retention_count` and `archived_count`. The three eligibility counts are computed only while archival is on. `archived_count` counts retention archives only.

The status card in **Systemstatistik** (System statistics) shows **Archived** / **Archiviert** for recordings with either archive flag. In clinic mode this count includes every dictation anchor recording and every import working copy, because ingest creates them with the personal archive flag.

## Manual Deletion in Clinic Mode

The feature `clinical_user_hard_delete` is locked in clinic mode because the defaults do not enable it. These routes therefore answer 403 `clinic_feature_disabled`: `recordings.delete_recording`, `recordings.delete_recording_audio`, `recordings.bulk_delete_recordings`, `api_v1.delete_recording`, `api_v1.delete_recording_audio`, `api_v1.batch_delete_recordings` and `admin.admin_delete_user`. Accounts are deactivated instead of deleted (see [User management](user-management.md)).

The archival sweep does not call the upstream orphan-speaker cleanup. Upstream speaker identification is locked in clinic mode.

## Paths That Still Delete Data

### Ownerless-Recording Cleanup

Unchanged from upstream (upstream Speakr issue #572) and not gated by clinic mode. It runs in every daily maintenance run, whether archival is on or not. No clinic decision covers this deletion. Account deletion is locked in clinic mode, and every path that creates a recording sets an owner, so candidates are rows that earlier software versions left without an owner.

- Two runs: a recording without an owner is only marked on the first run. A later run deletes it, with its media and dependent rows, once the mark is at least 20 hours old.
- Recordings with an owner are never candidates. A recording with a queued or running job, an active recording session or an unmerged upload part is skipped, and its wait starts again. A recording that regains an owner loses its mark.
- Each deletion is audited as `recording.deleted` with `reason: ownerless`, only while the audit log is on. A recording without an owner leaves no changes-feed tombstone.
- Media files without a row are only counted, never deleted.
- After deletions it runs the upstream orphan-speaker cleanup.
- Each run logs one line that begins with `Ownerless recording cleanup:`.

### Hourly Maintenance Loop

Interval `RECORDING_SESSION_CLEANUP_INTERVAL_SECONDS` (default `3600`; `0` or less turns the loop off). The first pass runs one interval after the start. Each pass handles these records:

| Records | Setting / variable | Default |
|---|---|---|
| Recording sessions whose last activity is older than the TTL. A browser recording with chunks is finalized into a recording, and a stalled finalization is queued again; any other session expires and its folder is removed. Session folders without a row are removed after the same time. | `RECORDING_SESSION_TTL_HOURS` (environment only) | `24` hours |
| Upload-join parts. Media and row are removed, unless the part still waits for its merge. | Fixed | `24` hours |
| Changes-feed tombstones of deleted recordings | `recording_tombstone_days` / `RECORDING_TOMBSTONE_DAYS` | `90` days |
| Webhook delivery records | `webhook_delivery_retention_days` / `WEBHOOK_DELIVERY_RETENTION_DAYS` | `30` days, `0` keeps them |

User webhooks are locked in clinic mode (see [Webhooks](webhooks.md)), so no new delivery records are created.

### Canceled Web Uploads

`POST /upload/cancel` deletes the upload recording that the same upload stored less than 15 minutes earlier, unless it is `COMPLETED`. The deletion is audited as `recording.deleted` with `reason: upload_canceled` (only while the audit log is on). A dictation already created from the upload keeps its anchor recording and its original. A cancel that arrives while the upload is still being stored discards the new upload recording before a dictation is created; this path writes no audit row.

### Files Without a Recording

`scripts/clean_unreferenced_files.py` (unchanged upstream) lists media files that no row refers to and, on request, deletes or moves them. It looks only at plain files directly in `UPLOAD_FOLDER` and at the tree below `UPLOAD_FOLDER/<FILE_STORAGE_KEY_PREFIX>` (default `recordings`), or the objects under that prefix on S3. It does not know the clinic original store; with the default `CLINIC_ORIGINALS_ROOT`, the originals lie below `UPLOAD_FOLDER/clinic/originals/v1/`, outside the scanned folders. The command starts no job workers or schedulers, so it can run while the server runs.

```bash
# Dry run: count, total size and the first 20 files; nothing is changed
docker compose exec app python scripts/clean_unreferenced_files.py
# Every unreferenced file and every row whose file is missing
docker compose exec app python scripts/clean_unreferenced_files.py --list
# Delete the files after a confirmation prompt
docker compose exec app python scripts/clean_unreferenced_files.py --delete
# Move the files into a quarantine folder after a confirmation prompt
docker compose exec app python scripts/clean_unreferenced_files.py --move-to <quarantine-dir>
```

| Option | Effect |
|---|---|
| (none) | Dry run. Nothing is changed. |
| `--list` | Every unreferenced file and every row whose file is missing are printed. |
| `--delete` | The files are deleted after a confirmation prompt. |
| `--move-to DIR` | The files are moved below `DIR` after a confirmation prompt, with their relative paths. Local storage only. |
| `--yes` | No confirmation prompt. |
| `--min-age-hours N` | Files changed within the last `N` hours are skipped (default `48`). |

- `DIR` must not be `UPLOAD_FOLDER` itself or lie in its `<FILE_STORAGE_KEY_PREFIX>` tree.
- S3 objects can only be deleted, not moved.
- References and file age are checked again before each removal, so a row created since the scan keeps its file.

Back up the uploads folder, or run `--move-to` first, before the first `--delete`.

### Backup Folder of the Windows Import Agent

Default off. With `BackupRetentionEnabled=true` (30 days) or `BackupRetentionDays` greater than 0, and a `BackupFolder` set, the agent deletes files in that folder whose last write time is at least that many days old. It deletes at most `BackupRetentionMaxBatch` files per pass (default 100, at most 10000) and skips `.part` files and names that contain `.import-hold-`. Each deletion is logged as `retention deleted <relative path>`.

- The keys belong to the `ImportAgent` section of the agent's `appsettings.json`.
- `BackupFolder` must be an absolute path that does not overlap an allowed root. With retention on and no `BackupFolder`, the agent does not start.
- The cleanup runs after every agent pass (`PollIntervalSeconds`, default `15`).

The server currently never permits the post-import move, so the agent does not fill this folder. Installation, update and rollback of the agent are not tested, and its hardware tests have not run.

### Upstream Watch Folder

With `ENABLE_AUTO_PROCESSING=true` (default `false`), the upstream watch folder ingests each dropped file and then deletes it from the watch folder. It has no clinic check. Leave the variable unset; see [System Settings](system-settings.md#upstream-functions-without-a-clinic-check).

### Upstream Reset Script

`scripts/reset_db.py` is an upstream development script. It is part of the repository and of the Docker image, which copies the whole source tree. It asks for no confirmation and does not check `CLINIC_MODE`.

- With a SQLite database (`sqlite:///…`), it deletes the database file named in the URI and its journal file, including all audit rows, revisions, the archive book and the account policies, and then creates an empty schema.
- It then deletes every file and every folder under `uploads` relative to the current working directory.
- With PostgreSQL, it removes no database data (it only creates missing tables), but it still clears `./uploads`.

Never run it on a clinic installation. To return to an earlier state, restore a backup instead (see [File Storage](storage.md#backups)).

## Retention Policy and Backup Plan

- **No approved clinic retention policy.** In `clinic-spec/config/production.template.json`, `retention.policy_reference`, `audio_days`, `transcript_days`, `audit_days` and `backup_days` are `null`. In `clinic-spec/config/dev.defaults.json`, `privacy.retention_policy_approved` is `false`. The application enforces no clinic retention period for audio, transcripts, audit rows or backups.
- **Server-side backup expiry plan: Prepared, disabled.** `src/clinic/backup_retention_plan.py` computes which backup copies of imported device files have expired (default 30 days, hold flag, unsafe paths rejected). It never touches the file system, and no code outside the tests calls it.
- **Backups.** Archived data stays in the database and in storage. A backup must cover the database, `UPLOAD_FOLDER` and `CLINIC_ORIGINALS_ROOT` when it lies elsewhere. See [Storage](storage.md). The recovery values (RPO, RTO, restore evidence) in the production template are `null`.
- **Audit rows.** In clinic mode the daily pruning deletes no audit row. A separately privileged retention process is prepared only as a plan function and does not run. See [Audit log](audit-log.md).

## Upgrade Behavior

At start, the clinic integrity migration creates `clinic_archive_event` and installs its triggers. Additive migrations add the nullable column `retention_archived_at` and the column `archive_revision` (`INTEGER NOT NULL DEFAULT 0`) to existing recording tables. Existing personal archive flags and recording content are kept and count as the starting state; no historical transition events are created. Repeated starts keep the archive evidence and revisions. Existing `deletion_mode` values archive from the first run after the upgrade. Files that older software versions had removed stay absent.

## Operational Checks

- Use `GET /admin/auto-deletion/stats` to check eligibility and exemptions before switching archival on.
- Check `recording.archived` and `recording.unarchived` in **System-Auditprotokoll** (system audit log, `/admin#/system/audit`) and the `errors` counter of each run.
- When a restore is refused, check that the caller owns the recording.
- When a restored recording is archived again, check its creation date and its effective tag or global period.
- Check the daily line `Ownerless recording cleanup:` for deleted recordings.

See [Audit log](audit-log.md) for event fields and audit settings, and [Privacy](privacy.md) for the data stores.
