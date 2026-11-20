# Audit log

[Deutsch](../de/admin-guide/audit-log.md)

Open **System → System-Auditprotokoll** as a site administrator. Filter by recording ID, period and outcome to investigate an access. To review an account, follow the activity link from its editor. Refresh before paging through older results. Pool leadership alone does not grant access to this log.

Rows record UTC time, actor type, event, outcome, relevant IDs and selected event details. Transcripts, summaries, filenames, passwords and search queries are excluded. Account creation and deletion can record a username. Treat IP addresses, user-agent information and exported logs as sensitive.

## Export and investigate gaps

Choose CSV for a spreadsheet or JSONL for automated processing. The export uses the current filters. Retain the filter, time zone and highest matching event ID with a reconciliation. A site-admin browser session can request up to 500 rows per page at `/admin/audit-log`. Personal API tokens are disabled in clinic mode. Reading and exporting the log also leave audit records.

Reads in another worker can appear after a short delay. If an expected event remains absent after refreshing, check restricted service logs for `AUDIT-FALLBACK` and `audit.write_failed`. An active site administrator can open another user's dictation through an audited read path. The service returns 503 if the mandatory evidence cannot be written. Workflow send and final release also leave mandatory evidence.

## Configuration

| Setting | Environment | Effect |
|---|---|---|
| `audit_log_enabled` | `AUDIT_LOG_ENABLED` | Defaults to true. Mandatory administrative and workflow events remain when disabled. |
| `audit_log_retention_days` | `AUDIT_LOG_RETENTION_DAYS` | Defaults to 365. Zero removes the cutoff. The daily clinic job retains rows beyond the cutoff. |
| `audit_log_store_ip` | `AUDIT_LOG_STORE_IP` | Defaults to full. Truncated keeps the IPv4 /24 or IPv6 /48 prefix. Off omits the address. |
| `audit_log_record_views` | `AUDIT_LOG_RECORD_VIEWS` | Defaults to true. Disabling ordinary views and plays still records downloads and exports. |

Database privileges and independently protected exports need their own operational checks. The event registry includes historical and feature-gated operations.

## Event registry

### Sign-in and tokens

`auth.login.succeeded`, `auth.login.failed`, `auth.login.locked`, `auth.logout`, `auth.password.changed`, `auth.sso.linked`, `auth.sso.unlinked`, `auth.sso.link_requested`, `token.created`, `token.changed`, `token.revoked`, `token.first_used`.

### Access

`recording.viewed`, `recording.content.read`, `recording.media.played`, `recording.media.downloaded`, `recording.downloaded`, `recording.exported`.

### Changes

`recording.edited`, `recording.deleted`, `recording.audio.deleted`, `recording.archived`, `recording.unarchived`, `recording.reprocessed`, `recording.job.canceled`.

### Administration

`admin.user.created`, `admin.user.changed`, `admin.user.password_set`, `admin.user.unlocked`, `admin.user.role_changed`, `admin.user.deleted`, `admin.setting.changed`, `admin.retention.run`, `admin.inquire.processed`, `group.created`, `group.changed`, `group.deleted`, `group.member.added`, `group.member.role_changed`, `group.member.removed`.

### Audit operations

`audit.viewed`, `audit.exported`, `audit.pruned`, `audit.write_failed`.

[Original Speakr guide](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/audit-log.md).
