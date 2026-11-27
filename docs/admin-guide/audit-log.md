# Audit log

[Deutsch](../de/admin-guide/audit-log.md)

Open **Verwaltung → System → System-Auditprotokoll** (`/admin#/system/audit`) with the site administration role. Pool leadership or identity administration alone does not grant access. From an existing account in **Benutzer und Rollen**, **Systemaktivität dieses Kontos** opens the log with that account selected.

The log records sign-ins, content access, changes and administration in `audit_event`. It is enabled by default. It is distinct from the revision and workflow history of a dictation; not every operation in the clinic has an entry in this table.

## Find and export events

Filter by period, user, event group, event, recording ID, outcome or actor type. Dates in the server filters are UTC. The user filter matches the actor or target account. Clinic audit entries use the integer recording ID, usually the dictation's anchor recording ID, rather than the dictation UUID.

The browser-session endpoint `GET /admin/audit-log` returns the newest events first, with 50 rows by default and at most 500 per request. Refresh before paging through older results. The reader flushes events queued in its own process; events from another worker can arrive later.

Choose CSV for a spreadsheet or JSONL for automated analysis. `GET /admin/audit-log/export` applies the same filters and exports oldest first, bounded by the highest matching event ID when the export starts. Retain the filters, time zone and highest exported ID with an investigation. Reading and exporting the log produce `audit.viewed` and `audit.exported` events; repeated views are combined within a 30-minute window.

The clinic fork does not offer general API v1 access or personal API tokens for log collection. Machine credentials are reserved for the import-agent channel. Use an authorised administrator's browser session for this log.

## What a row contains

| Field | Meaning |
|---|---|
| `id`, `created_at` | Increasing event ID and UTC time |
| `event`, `event_group` | Event name and group: `auth`, `access`, `change`, `admin` or `audit` |
| `actor_kind`, `actor_user_id`, `actor_token_id` | Actor type and relevant account or token IDs; historical token events may remain |
| `recording_id`, `recording_owner_id`, `target_user_id`, `group_id` | IDs relevant to the event; not every event sets every field |
| `outcome` | `success`, `denied` or `failed` |
| `ip`, `user_agent` | Client address according to the IP setting, and at most 160 characters of user-agent information; omitted for system actors |
| `details` | Event-specific allowlisted values; oversized details are replaced by a truncation marker |

The allowlist excludes transcripts, summaries, notes, titles, filenames, search queries, passwords, one-time codes and provider error text. Account creation and historical account deletion can record a username. Treat addresses, user-agent information and exports as sensitive. Rows have no foreign keys to the referenced accounts or recordings, so those references can outlive the original objects. IDs of other tables can be reused on SQLite; interpret them together with time and deletion history.

## Clinic events

| Event or group | Meaning |
|---|---|
| `auth.login.succeeded`, `auth.login.failed`, `auth.login.locked`, `auth.logout`, `auth.password.changed` | Local sign-in, lockout, sign-out and password changes |
| `recording.content.read` | Dictation content or content-bearing metadata was read; `details.part` identifies detail, review, history, context, audio or summary |
| `recording.media.played`, `recording.media.downloaded` | Playback or a download through the recording media routes |
| `recording.edited` | Changed field names or workflow steps, not their contents |
| `recording.archived`, `recording.unarchived` | A recording archive transition, including its rule and scope |
| `recording.reprocessed`, `recording.job.canceled` | Requested processing or cancellation |
| `admin.user.*`, `admin.setting.changed` | Account and setting administration; the registry also contains historical upstream operations |
| `audit.viewed`, `audit.exported`, `audit.write_failed` | Access to the log and evidence of writer failures |

The current **KI-Zusammenfassung** panel records each state read as `recording.content.read` with `part=summary`, and each manual generation request as `recording.reprocessed` with `kind=summary`. **Modellwahl** changes are recorded as `admin.setting.changed` for `clinic_task_models`; the model configuration value is not copied into the audit details.

The legacy voice-data cleanup module registers `admin.upstream_voice.purge` when used. Its phase and counts distinguish a completed database step from remaining file work. An event name in the registry does not prove that a corresponding clinic UI or supported operation exists.

## Mandatory evidence and gaps

An active site administrator can open another user's transcript or audio through the dedicated admin content routes without a reason prompt. Each successful opening must have its audit row stored first; otherwise the server returns `503 audit_unavailable` and delivers no content. This read or playback does not grant editing, release or export rights.

Keep `audit_log_enabled` and `audit_log_record_views` enabled: disabling the former prevents these admin content openings, and disabling the latter also prevents admin audio openings. The metadata-only admin overview itself writes no content-read event. Each foreign dictation row returned to a site administrator through the worklist has its own `part=worklist` read event.

Recording archive transitions and their mandatory audit evidence commit together. Workflow send, acceptance steps and final release also require transactional evidence. The archive workflow's own entry is separate from a recording retention transition; see [Retention](retention.md).

Some ordinary reads are deduplicated. Refusals by the clinic route guard do not automatically produce an audit row. Copying text or saving an already delivered file in the browser is not visible to the server. Pool and grant history, consent history, output receipts and dictation revisions also have their own tables; this log is not a substitute for them.

## Configuration

Set these values in **Systemeinstellungen**. Saved settings take precedence over the corresponding environment variables.

| Setting | Environment | Default and effect |
|---|---|---|
| `audit_log_enabled` | `AUDIT_LOG_ENABLED` | `true`; turns ordinary events on or off. Audit operations, changes to audit settings and explicitly mandatory events remain recorded. |
| `audit_log_retention_days` | `AUDIT_LOG_RETENTION_DAYS` | `365`; `0` removes the cutoff. Clinic pruning retains rows even beyond the cutoff. |
| `audit_log_store_ip` | `AUDIT_LOG_STORE_IP` | `full`; `truncated` keeps the IPv4 /24 or IPv6 /48 prefix, `off` omits the address. |
| `audit_log_record_views` | `AUDIT_LOG_RECORD_VIEWS` | `true`; disabling it drops ordinary recording-view and playback events, not downloads or content-read events. |

## Retention, protection and failures

The daily clinic pruning job deletes no audit rows. It logs `Audit pruning withheld (separate_privileged_retention)` and the number beyond the cutoff. The privileged retention helper only checks a policy and counts eligible rows; it is not a deletion service. Archive and restore events are protected from ordinary pruning even outside clinic mode.

ORM listeners refuse changes and deletion of audit rows. A database-trigger installer exists in `src/clinic/audit_db_guards.py`, but startup does not invoke it. Do not assume protection against raw SQL from that module's presence. Restrict database roles and protect independent exports; a database owner or superuser remains an operational risk. Event-ID gaps alone do not prove deletion, because rollbacks and deduplication can also leave gaps.

If an expected event is missing after refreshing, inspect restricted service logs for `AUDIT-FALLBACK` and subsequent `audit.write_failed` rows. Keep the fallback evidence with the investigation. These records report failed writes; they do not reconstruct the missing database events automatically.

See [Privacy](privacy.md) for data handling and [Storage](storage.md) for backups.
