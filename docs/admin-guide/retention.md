# Retention and archiving

[Deutsch](../de/admin-guide/retention.md)

The clinic fork does not offer hard automatic deletion of dictations or recording media. Retention archives eligible recordings and preserves their files and database rows. The inherited deletion-mode names do not enable deletion. Manual hard deletion is also locked in clinic mode; accounts are deactivated instead of deleted.

Two different mechanisms remain: the daily retention sweep for ordinary recording rows, and the explicit **Archivieren** action for a clinic dictation. Archiving frees no storage. Include archived audio, revisions and audit evidence in capacity and backup planning.

## Automatic archival settings

Open **Verwaltung → System → Systemeinstellungen** as a site administrator. The old setting names remain for compatibility.

| Setting | Environment variable | Default and effect |
|---|---|---|
| `enable_auto_deletion` | `ENABLE_AUTO_DELETION` | `false`; enables automatic archival, including manual runs of the sweep. |
| `global_retention_days` | `GLOBAL_RETENTION_DAYS` | `0`; a positive value archives recordings after that many days since creation. With `0`, only applicable positive tag periods count. |
| `deletion_mode` | `DELETION_MODE` | The interface offers `archive`. Historical `audio_only` and `full_recording` values also archive and preserve all data. |

Saved system settings take precedence over environment variables. The sweep reads the settings at runtime; changing them needs no restart. The internal environment fallback still uses `full_recording`, and older examples describe deletion. Neither changes the current archive-only behavior. The development contract's `features.auto_deletion` flag does not control this sweep.

An elected background process runs daily maintenance at 02:00 server-local time. It runs enabled archival, ownerless-recording cleanup and audit pruning. A process with `CLINIC_WEB_ONLY=1` starts no background work. In clinic mode audit pruning keeps all rows; see [Audit log](audit-log.md).

## Which recordings are eligible

The sweep considers `COMPLETED` and `FAILED` recording rows and rechecks each under a lock. It skips recordings already archived by retention and those without a positive period or a sufficiently old creation date.

| Existing field or rule | Effect |
|---|---|
| `deletion_exempt` | Exempts the recording from automatic archival. |
| Applicable tag with `protect_from_deletion` or `retention_days=-1` | Exempts the recording. |
| Applicable positive tag periods | The shortest positive period overrides the global period. |
| No applicable positive tag period | The positive global period applies, if configured. |

An owner's personal tags and existing group tags count. Another user's personal tag does not change the owner's retention period. The clinic fork does not offer folders; inherited folder retention fields are not evaluated.

Clinic ingest creates a dictation's anchor recording with `deletion_exempt=True`, so the generic sweep normally skips it. A separate upload recording, import working copy or legacy source recording is not covered by that exemption and may be archived once eligible. Its clinic original remains intact.

The administrative browser-session endpoints retain their old names:

- `GET /admin/auto-deletion/stats` reports enablement, period, exemptions and eligible or archived counts.
- `POST /admin/auto-deletion/run` runs the enabled sweep and requires the administrator's session and CSRF token. No clinic administration page calls this endpoint automatically.

A run reports `checked`, `archived`, `already_archived`, `exempted`, `skipped_no_retention` and `errors`. The compatibility counters `deleted_audio_only` and `deleted_full` stay zero. Each recording commits separately: a later error does not undo earlier successful archives. Review errors before retrying. A run while archival is disabled reports `auto_deletion_disabled` and changes nothing.

## Archive a clinic dictation

Use **Archivieren** in the dictation's workflow actions or review view. The confirmation says **Das Diktat wird archiviert. Es wird nichts gelöscht.** The user needs an explicit **Archivieren** (`archive`) grant for that dictation. Author ownership, site administration, identity administration, pool leadership or release permission alone does not provide it.

The action is intended for completed work, but the server accepts a transition from any workflow state other than **Zu archivieren**. Confirming it moves the dictation to **Zu archivieren**. The first archive entry needs a current text revision, an audio revision and one unambiguous current context revision; if these are missing, the action is unavailable. An existing entry keeps its recorded links.

The archive book preserves references to the original, audio, text, context and active release, with a version and idempotent result. It does not remove these objects. The workflow transition is distinct from recording retention: an exempt anchor normally does not acquire a retention timestamp, and the workflow archive entry itself is not a `recording.archived` audit event.

Open **Verwaltung & Seiten → Diktat und Ausgabe → Archiv** (`/#/archiv`) to inspect the result. This read-only view lists visible dictations in **Zu archivieren**. It offers date, author and staff-pool filters, but no restore, reopen or delete control. Where no retention state is supplied, **Aufbewahrung nicht angegeben** means the retention information is absent; it does not mean that the dictation will be deleted.

## Archive evidence and restoration

A recording retention transition sets `retention_archived_at`. The owner's personal archive uses `is_archived`; either value makes the recording archived. Only the owner may clear a retention archive through the inherited recording restore path, subject to the clinic edit permission. This is not a restore action in the clinic **Archiv** view. Restoration does not change the creation date, so the next sweep can archive the recording again if its period and exemption remain unchanged.

Every actual recording archive or restore transition writes its state, an `audit_event` row and an append-only `clinic_archive_event` row in one transaction. Missing mandatory evidence rolls the transition back. These events remain enabled when ordinary audit logging is off. SQLite and PostgreSQL triggers protect the archive evidence against updates and deletion. The evidence includes actor, recording, time, archive revision, rule, scope and applicable period or tag.

## Maintenance is not retention

Archive-only retention is not a guarantee that no maintenance code can delete data. Existing upstream maintenance still handles ownerless recordings, abandoned upload sessions and temporary upload parts. Ownerless cleanup first marks a recording, then can remove it on a later run once the mark is at least 20 hours old; active jobs and sessions defer removal. Inspect the daily `Ownerless recording cleanup:` log and investigate unexpected candidates.

Canceling an unfinished upload can remove its upload recording. A clinic original already accepted into the original store is separate. Revoking own-voice consent deletes that user's enrolled voice vectors while retaining consent history. These are specific lifecycle operations, not an approved policy for deleting completed dictations.

Do not use inherited cleanup or reset scripts as a clinic retention procedure. In particular, `scripts/clean_unreferenced_files.py` does not know all clinic original references, and `scripts/reset_db.py` is destructive development tooling. Keep the original store outside the recording-prefix tree scanned by ordinary media cleanup. Leave the upstream watch-folder mechanism (`ENABLE_AUTO_PROCESSING`) off. Any file cleanup or import-agent backup expiry needs its own reviewed scope and recovery plan.

## Policy and recovery

The repository supplies no approved production retention periods. `clinic-spec/config/production.template.json` leaves the policy reference and audio, transcript, audit and backup periods unset. The development configuration explicitly has `retention_policy_approved=false`. The backup-expiry helper computes a plan only; it is not a running retention service.

Agree periods, exceptions, accountable operators and restore responsibility locally before relying on a schedule. Back up the database, upload storage and the separate original store together. Test recovery with synthetic data, including ownership, revisions, audio and audit evidence. Previously deleted media cannot be recovered by switching to archival.

See [File storage](storage.md), [Audit log](audit-log.md) and [Privacy](privacy.md).
