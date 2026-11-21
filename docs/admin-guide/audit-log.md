# Audit log
<small>[Deutsch](../de/admin-guide/audit-log.md)</small>

The audit log records sign-ins, access to recordings and clinic dictations, changes and administrative actions. It is one database table, `audit_event`, written only through `src/services/audit.py`. Site administrators read it on the page **System-Auditprotokoll** (system audit log). The log is on by default, also after an upgrade from a release without it.

The log never holds the content of a dictation. What a row stores, and what it never stores, is listed on this page. How the log relates to the other stored data is described in [Privacy and regulated data](privacy.md). Everything specific to the clinic build applies with `CLINIC_MODE` on. With clinic mode off, the upstream behavior applies.

| Topic | Status in the clinic build |
|---|---|
| Recording of events, the page **System-Auditprotokoll**, CSV and JSONL export | Available |
| Audited opening of other users' transcripts and audio by a site administrator | Available |
| Daily removal of rows older than `audit_log_retention_days` | Upstream only: in clinic mode pruning removes nothing; the repository has no separately privileged deletion process. |
| Database triggers against changes of `audit_event` | Prepared, disabled: the installer exists, startup does not run it |
| Collection by an external system with an API token | Upstream only: API tokens are refused in clinic mode |
| Audit list across dictations in **Aktivität und Audit** (activity and audit) | Open gap: no route exists; the page has no such list and no CSV download |
| Audit entries of the per-task model selection and of the dictation summary | In verification (see [Features in verification](#features-in-verification)) |

## Where to view the log, and who may

**Path.** **Verwaltung** (administration) › **System** › **System-Auditprotokoll**, `/admin#/system/audit`. The navigation labels are shown in German only. The page itself is the upstream audit tab and follows the interface language: its heading reads "Audit log" in English and "Audit-Protokoll" in German.

**From an account.** On **Verwaltung** › **Konten und Pools** (accounts and pools) › **Benutzer und Rollen** (users and roles), section **Konten** (accounts), the button **Systemaktivität dieses Kontos** (system activity of this account) is shown while an existing account is being edited. It opens `/admin#/system/audit?konto=<id>` with the **User** filter set to that account. The upstream button "View activity" belongs to the upstream user list, which is not rendered in clinic mode.

**Who may read and export the log.**

- **Site administration** (**Administrationsrolle** (administration role), the flag `User.is_admin`): may read and export. The routes `GET /admin/audit-log` and `GET /admin/audit-log/export` check this flag. The clinic route guard refuses every other account with 403 before the handler runs; this refusal is not logged.
- **Local identity administration** (**Identitätsadministration**): not sufficient on its own. Without the **Administrationsrolle**, an account cannot open `/admin`.
- **Pool leads** (**Leitung**) and upstream group administrators: no access. An account that is only a group administrator gets 403 on `/admin` in clinic mode.
- The separate rights **Freigeben** (release) and **Originalaudio exportieren** (export original audio) neither grant nor require access to the log.

**Aktivität und Audit** in the group **Betrieb** (operations) is a different page. It shows the history of one dictation, built from revisions, releases, work-state transfers and archive records, and the clinic statistics. It does not read `audit_event`. The build offers no way to create a statistics grant, so the page is withheld for every account, see [System statistics](statistics.md).

## Content of a row

| Column | Content |
|---|---|
| `id` | A number that is never given out again (AUTOINCREMENT on SQLite, a sequence on PostgreSQL). A removed row leaves a gap. On PostgreSQL, gaps also arise from rolled-back actions and from de-duplicated repeats, so a gap alone is no proof of removal. Compare with your external JSONL copies (see [Append-only protection](#append-only-protection)). |
| `created_at` | Time of the event, in UTC |
| `event`, `event_group` | Name of the event (see [Events](#events)) and its group: `auth`, `access`, `change`, `admin` or `audit` |
| `actor_kind` | `session` (browser), `api_token`, `sso_token`, `system` (a scheduled job or a server-side script) or `anonymous` (not signed in, for example a refused sign-in). `public_link` is no longer a valid value or filter; the interface still carries a label for it. |
| `actor_user_id`, `actor_token_id` | Account and API token of the request, by id |
| `ip`, `user_agent` | Client address, subject to `audit_log_store_ip`, and the first 160 characters of the user agent. Both are empty for the actor `system`. |
| `recording_id`, `recording_owner_id` | Recording and, on stock routes, its owner at the time of the event. For a clinic dictation, `recording_id` is the integer id of the dictation's anchor recording, not the dictation UUID. The admin dictation overview `GET /api/admin/clinic/dictations` returns both ids. For the clinic content reads (`part` `detail`, `review`, `history`, `context` and the `audio_*` parts) and for lease rows, `recording_owner_id` is empty; for clinic workflow, release and acceptance rows it holds the dictation author. For the **Recording ID** filter, see [The page System-Auditprotokoll](#the-page-system-auditprotokoll). |
| `target_user_id`, `group_id` | Account or group that the event concerns |
| `outcome` | `success`, `denied` or `failed` |
| `details` | A few values of the event, from a fixed list of keys per event. A key that is not on the list is dropped with a warning. Details longer than 512 characters as JSON are replaced by `{"truncated": true}`. |

The table also has an internal column `dedup_key` for repeated reads. The routes and the exports do not return it.

Never stored in a row: titles, file names, transcript, summary and note text, search queries, chat messages, the free text of a rejection reason, names of API tokens and groups, e-mail addresses, passwords, one-time codes and error text. Accounts, recordings, tokens and groups are named by id. A username is stored in two events only, `admin.user.created` and `admin.user.deleted`, so that the rows of an account can still be attributed after its deletion. The page shows usernames by looking up the ids of the displayed rows at read time; a missing account is shown as "Deleted user #<id>".

A row has no foreign key to the recording, account or group it names. The rows therefore remain after a deletion.

## Events

The column **Clinic mode** uses the status vocabulary of this manual. The groups correspond to the **Events** filter of the page.

### Sign-in and session (group `auth`)

| Event | Recorded when | Details | Clinic mode |
|---|---|---|---|
| `auth.login.succeeded` | A sign-in succeeded | `method`, `remember`. The clinic sign-in page writes `method` `password` and `remember` `false`. | Available. The values `entra` and `iwa` of `method` belong to the identity providers: Prepared, disabled. |
| `auth.login.failed` | A sign-in or a password check was refused. One row per minute for each address and account. | `method`, `reason`. Clinic values: `bad_password` (wrong password or unknown account; with an unknown account, `target_user_id` is empty), `locked`, `bad_recovery_code` with `method` `recovery` (a one-time code could not be redeemed), `bad_current_password` with `method` `password_change` (own password change, written on every refusal). The address that was typed is never stored. | Available. The reasons `unknown_account`, `no_local_password`, `password_login_disabled` and `sso_error` come from the upstream sign-in and SSO routes: Upstream only. |
| `auth.login.locked` | The limit of wrong passwords was reached, per client address (`login_max_failed_attempts`) or account-wide (four times that limit) | `minutes` | Available |
| `auth.logout` | A signed-in user signed out | | Available |
| `auth.password.changed` | A user changed the own password, or set a password by redeeming an activation or reset code | | Available |
| `auth.sso.linked`, `auth.sso.unlinked` | An account was linked to, or unlinked from, an external identity | | Prepared, disabled (Entra link workflow; the upstream OIDC routes refuse in clinic mode). `auth.sso.unlinked`: the clinic unlink route also works while the provider is disabled, for links created earlier. |
| `auth.sso.link_requested` | An identity administrator approved a one-time request to link an account | | Prepared, disabled |
| `token.created`, `token.changed`, `token.revoked` | An API token was created, renamed or narrowed, or revoked | `token_id`, `scopes`, `expires`, `sso_client_id` (`token.changed`: `token_id`, `scopes`; `token.revoked`: `token_id`) | Upstream only: the token routes are locked (`personal_api_tokens`) |
| `token.first_used` | An API token was used for the first time | `token_id` | Upstream only: a request authenticated only by an API token is refused with 403 `clinic_feature_disabled` |

### Access to a recording (group `access`)

| Event | Recorded when | Details | Clinic mode |
|---|---|---|---|
| `recording.viewed` | A stock recording view was read: `GET /api/recordings/<id>` or `GET /api/v1/recordings/<id>`. One row per 30 minutes for each account or token and recording. | | Available on the stock routes. The clinic dictation views write `recording.content.read` instead. |
| `recording.content.read` | Content or content-bearing metadata of a dictation was delivered | `part` (see the table below) | Available |
| `recording.media.played` | Audio was requested for playback through the stock route `/audio/<id>` (one row per 30 minutes), or a site administrator opened the audio of a dictation (one row per opening) | | Available |
| `recording.media.downloaded` | Audio was requested as a download (`/audio/<id>?download=true`). One row per 5 minutes. | | Available with the grant **Originalaudio exportieren** (action `audio_export`); without it, the clinic guard refuses the request |
| `recording.downloaded` | A transcript, summary, notes or chat file was downloaded from a stock route | `part`, `format` | Transcript, summary and notes downloads: Upstream only (`stock_unversioned_export` is locked). Chat download (`part` `chat`, `format` `docx`): only when the text model is local (feature `chat`), otherwise locked. |
| `recording.exported` | Existing recordings were written to the export folder | `count` | Upstream only: the route is locked (`stock_unversioned_export`) |

Values of `part` in `recording.content.read`:

| `part` | Written by | Repeats |
|---|---|---|
| `detail` | `GET /api/clinic/v1/dictations/<id>/detail` (dictation details) | Every request |
| `review` | `GET /api/clinic/v1/dictations/<id>/review` | Every request |
| `history` | `GET /api/clinic/v1/dictations/<id>/history` | Every request |
| `context` | `GET /api/clinic/v1/dictations/<id>/context` (case context) | Every request |
| `audio_revision`, `audio_derived`, `audio_peaks` | The clinic audio routes: current audio revision, derived WAV for playback and editing, waveform peaks | Every request |
| `worklist` | `GET /api/clinic/v1/worklist/rows` for an active site administrator: one row for each returned dictation whose author and owner is another account. The worklist of any other account is not recorded. | Every request |
| `transcript` | Opening of a transcript by a site administrator (`GET /api/admin/clinic/dictations/<recording id>/transcript`), and the stock route `GET /api/v1/recordings/<id>/transcript` | Admin opening: every request. API v1: one row per 30 minutes. |
| `summary`, `notes`, `speakers` | The stock routes of API v1 | One row per 30 minutes |

A request for a stock recording that exists and that the account may not access is recorded as the same event with the outcome `denied`, once per minute, when the handler refuses it. A request that the clinic route guard refuses never reaches the handler and is not written to `audit_event`; see [Not recorded in this log](#not-recorded-in-this-log).

**Opening content as a site administrator.** An active site administrator (`User.is_admin` and a clinic account with status `active`) can open the transcript and the audio of any user's dictation through `GET /api/admin/clinic/dictations/<recording id>/transcript` and `…/audio`. There is no reason prompt and no confirmation. Each opening is recorded as `recording.content.read` with `part` `transcript`, or as `recording.media.played`, without a repeat window. The content is delivered only after the row is stored. Otherwise the answer is 503 `audit_unavailable`. Openings are therefore refused while `audit_log_enabled` is off, and audio openings also while `audit_log_record_views` is off. The listing of the overview itself (`GET /api/admin/clinic/dictations`, metadata only) is not recorded. With S3 storage, the audio is answered with a redirect to the storage, and the redirect counts as the opening.

Text copied or saved in the browser after delivery is not visible to the server and is not recorded.

### Changes (group `change`)

| Event | Recorded when | Details | Clinic mode |
|---|---|---|---|
| `recording.edited` | A recording or a clinic dictation was changed, or a clinic workflow step took effect | `fields`: names of fields or steps, never field contents. Lease entries also carry the dictation id, the generation and the previous holder id; an import assignment (not reachable) would store the delivery and intent ids. See the table below. | Available |
| `recording.deleted` | A recording was deleted | `reason`: `user`, `retention`, `account_deleted`, `ownerless`, `upload_canceled` or `merged` | Manual deletion is locked (`clinical_user_hard_delete`), and retention never deletes. Rows can still arise from the daily cleanup of recordings without an owner (`ownerless`, unchanged upstream behavior) and from the stock route that cancels an upload in progress (`upload_canceled`). Older rows can hold `retention`. |
| `recording.audio.deleted` | The audio of a recording was deleted and the text was kept | `reason`: `user` or `retention` | Upstream only: the audio deletion routes are locked, and retention never deletes |
| `recording.archived` | A recording entered the archive through retention or a user action | `rule`, `scope`, optional `retention_days` and `tag_id` | Available. Recorded also while the log is off. |
| `recording.unarchived` | A user restored an archived recording | `rule`, `scope`, optional `retention_days` and `tag_id` | Available. Recorded also while the log is off. |
| `recording.reprocessed` | A new transcription, summary, title or translation was started | `kind`: `transcription`, `summary`, `title` or `translation` | Available for `transcription` (edit right). `summary`, `title` and `translation` come from stock routes that are reachable only when the text model is local (feature `generative_summary`), otherwise locked. |
| `recording.job.canceled` | A waiting or running job of a recording was canceled | `kind`: the job type | Available |

Values of `fields` in `recording.edited`:

| `fields` | Recorded when | Always recorded |
|---|---|---|
| `title`, `participants`, `notes`, `summary`, `transcript`, `translation`, `speakers`, `meeting_date`, `deletion_exempt` | A stock edit route changed the field | No |
| `workflow_status`, `workflow_send` | A dictation was sent | Yes |
| `workflow_status` and `workflow_submit`, `workflow_accept`, `workflow_reject` or `workflow_reopen` | A dictation was submitted, accepted, rejected or reopened. On a rejection, `target_user_id` is the account the dictation returns to; the reason text is not stored. | Yes |
| `workflow_status`, `workflow_finalize` | **Endgültig freigeben** (final release) took effect | Yes |
| `workflow_status`, `workflow_acceptance_resubmitted` | A saved correction moved the text of an accepted dictation, which therefore went back for acceptance | Yes |
| `assignee` | A site administrator or a pool lead assigned the dictation; `target_user_id` is the new assignee | Yes |
| `acceptance_policy` | The acceptance policy (**Abnahme**) of a dictation was published. A refused attempt is recorded with the outcome `denied`. | The refusal: yes. The publication: no. |
| `title`, `priority`, `deadline_date`, `deadline_time`, `author_user_id`, `assignee_user_id`, `pool_id` | Dictation metadata was changed (rename, priority and deadline, reassignment) | No |
| `lease.acquire`, `lease.renew`, `lease.release`, `lease.takeover`, `lease.expire`, with `dictation:<id>`, `generation:<n>` and, on a takeover by another person or a release caused by reassignment, `from:<user id>` (then also in `target_user_id`) | A review lease (edit lock) was taken, renewed, released, taken over by another person or found expired. An expiry is recorded only when a later acquire or takeover finds it. Its row has `actor_kind` `system`, the former holder as `actor_user_id` and the expiry time as `created_at`. | No |
| `clinic_backfill` | The script `scripts/clinic_backfill_dictations.py --apply` created a dictation from a legacy recording (actor `system`) | No |
| `import_delivery_id`, `import_intent_id` | Assignment of an unassigned import (**Klärliste** (clarification list)). No web route calls the assignment, so this row is not written in the released build. | Yes |

"Always recorded" means the row is written also while `audit_log_enabled` is off. Such an action is committed together with its row. If the row cannot be added, the action fails (`audit_unavailable`), so the step never takes effect without its evidence.

Archive transitions commit the recording state, an `audit_event` row and a row of the append-only ledger `clinic_archive_event` in one transaction. Scheduled retention archival has the actor `system`; a manually started run has the administrator who started it. The rules are `retention.global`, `retention.tag`, `user.archive` and `user.restore`. Details are in [Automatic Archival and Retention Policies](retention.md).

### Administration (group `admin`)

| Event | Recorded when | Details | Clinic mode |
|---|---|---|---|
| `admin.user.created` | An identity administrator created an account (**Konto anlegen** (create account)) | `username`, `is_admin` (always `false` in clinic mode) | Available. The first administrator, created by `scripts/clinic_bootstrap_admin.py`, gets no row; the script writes a line to the application log only. |
| `admin.user.changed` | An account was changed | `fields`: names only. Clinic values: `username`, `email`, `name`, `storage_quota_mb`, `monthly_token_budget`, `monthly_transcription_budget`, `status`, `login_policy`, `is_admin`, `is_identity_admin`, `create_groups_granted`, `activate_local` or `reset_local` (a one-time code was issued), `recovery_refused` (a code for another administrator was refused), `local_account_policy_adopted` | Available |
| `admin.user.password_set` | An upstream administrator set the password of an account | `self` | Upstream only: in clinic mode, an identity administrator issues one-time codes instead |
| `admin.user.unlocked` | An identity administrator ended the sign-in lock of an account | | Available |
| `admin.user.role_changed` | The **Administrationsrolle** was given or taken away | `old`, `new` | Available. A change of **Identitätsadministration** appears as `is_identity_admin` in `admin.user.changed`. |
| `admin.user.deleted` | An upstream administrator deleted an account | `username`, `recordings_deleted` | Upstream only: accounts are deactivated, never deleted; the delete route is locked (403 `clinic_feature_disabled`, feature `clinical_user_hard_delete`) |
| `admin.setting.changed` | A system setting was saved or cleared | `key`. Old and new values (`old`, `new`) only for switch, number and choice settings of a fixed list, for example the four `audit_log_*` settings, the retention settings, the sign-in lock and `transcript_length_limit`. For other settings only the key. A key that the application does not define is stored as `other`. | Available. Clinic writers: a saved metadata column on **Spalten** (columns) is stored with the key `other`; a saved import routing on **Importzuordnung** (import routing) is stored with the key `clinic_import_routing:<config id>` and is always recorded. Approved import sources cannot be created through the web interface (no route or command creates one), so this row arises only for sources that already exist in the database. |
| `admin.retention.run` | Retention archival was started on the admin page (`POST /admin/auto-deletion/run`) | `checked`, `archived`, `already_archived`, `deleted_audio_only`, `deleted_full`, `errors`. The two `deleted_*` counters are always `0`, because every mode archives. | Available |
| `admin.inquire.processed` | An upstream administrator started the processing of recordings for Ask Ambrose | `processed`, `failed`, `total`, `force` | Upstream only: `/api/admin/migrate_recordings` is locked (`generic_admin_content_access`). `/admin/inquire/process-recordings` is unlocked only with a local text model (feature `inquire`), and the clinic route guard then refuses it with 403 `clinic_feature_unsupported`, because the request names no recording. |
| `group.created`, `group.changed`, `group.deleted` | A group was created, changed or deleted | `group.changed`: `fields` | Upstream only: group and team administration is locked (`internal_sharing`) |
| `group.member.added`, `group.member.role_changed`, `group.member.removed` | A member was added to a group, changed in role, or removed | `role`, `old_role` | Upstream only. Changes of pools, memberships and pool leads are written to the pool history instead, see below. |
| `admin.upstream_voice.purge` | Removal of upstream speaker profiles and voice data. The event is registered only when the purge function runs. | `operation`, `phase`, `counts` | Prepared, disabled: no route and no command calls the function |

### The log itself (group `audit`)

| Event | Recorded when | Details | Clinic mode |
|---|---|---|---|
| `audit.viewed` | The log was read. One row per 30 minutes for each site administrator. | `filters`: the names of the filters in use | Available |
| `audit.exported` | An export finished streaming | `filters`, `count`, `format` | Available |
| `audit.pruned` | Rows past the retention period were removed | `count`, `cutoff` | Not written in clinic mode, because pruning is withheld (see [Retention of audit rows](#retention-of-audit-rows)) |
| `audit.write_failed` | Events could not be stored (see [Failures](#failures)) | `count` | Available |

### Upstream events that are not part of this build

The upstream events of public share links and internal shares (`share.internal.*`, `share.public.*`), of incognito uploads, of self-registration and of password reset by e-mail are not part of this build and are not written. Rows already stored keep their names.

## Not recorded in this log

- Lists of recordings, searches, and the worklist of an account that is not an active site administrator
- Questions to the chat of a recording (reachable only with a local text model)
- The creation of a recording by an upload or an import
- Changes of tags, folders, and of the personal inbox and star state
- A request with an invalid API token
- The status requests that an open page sends on a timer, and the waveform of a stock recording (the clinic waveform route records `audio_peaks`)
- The statistics pages of a site administrator
- Text copied or saved in the browser
- **Refusals by the clinic route guard.** Only policy denials and unclassified routes are logged, in the application log at level WARNING on the logger `src.clinic.guard`: a policy denial as `clinic policy denied <action> <endpoint> on recording <id> for user <id>: <reason>`, an unclassified route as `clinic policy: unclassified route … refused`. All other guard refusals are not logged, among them locked features (403 `clinic_feature_disabled`), setting ceilings (403 `clinic_policy_ceiling`), 403 `clinic_scope_forbidden`, `clinic_no_target` and `clinic_feature_unsupported`, 400 `invalid_recording_id`, 404 for a missing recording, and the 403 that an account without the **Administrationsrolle** gets on `/admin` or `/admin/audit-log`. An attempt by such an account to open the audit log therefore leaves no trace.
- **Pools and Fachrechte.** Changes of pools, memberships, pool leads and **Fachrechte** (professional rights) are written with old and new values to the pool history `ClinicPoolHistory`. It is append-only in the application (ORM listener, no database trigger). No page shows that history. See [Group Management: Author Pools and Staff Pools](group-management.md).
- **Clinic output.** Export requests on **Ausgabe** (output), the file download of a dictation bundle and the clipboard output write no `audit_event` row. A copy that the browser reports as successful appends an append-only output receipt (`clinic_output_receipt`); a denial or an abort only updates the output attempt.
- **Jobs and statistics.** A retry on **Verarbeitung** (processing) stores an idempotency receipt only. The statistics export function writes to the application log only.
- **Voice consent.** Grants and revocations of voice consent are kept in the append-only table `clinic_voice_consent`, enrollments and deletions of voice embeddings in `clinic_voice_consent_audit`.

## Settings

The settings are changed on **Systemeinstellungen** (system settings, `/admin#/system/einstellungen`) under their key, or in `.env`. A value in `.env` applies until a site administrator saves a value; such a row shows "Set in .env". In `.env`, only `true` (in any case) switches a boolean setting on, so `AUDIT_LOG_ENABLED=1` turns the log off.

| Setting | Variable in `.env` | Default | Effect |
|---|---|---|---|
| `audit_log_enabled` | `AUDIT_LOG_ENABLED` | `true` | `false`: no event is recorded, with the exceptions below. |
| `audit_log_retention_days` | `AUDIT_LOG_RETENTION_DAYS` | `365` | Without clinic mode: days after which an ordinary row is removed; `0` keeps every row. In clinic mode nothing is removed, and the value sets only the cutoff of the withheld count and the minimum period of a privileged retention policy. An invalid value counts as `365`. |
| `audit_log_store_ip` | `AUDIT_LOG_STORE_IP` | `full` | `full`: the client address is stored. `truncated`: the `/24` network of an IPv4 address or the `/48` network of an IPv6 address. `off`: no address. An unknown value counts as `full`. |
| `audit_log_record_views` | `AUDIT_LOG_RECORD_VIEWS` | `true` | `false`: `recording.viewed` and `recording.media.played` are not recorded, and the audio opening by a site administrator is refused with 503. `recording.content.read`, downloads and exports are recorded in either case. |

Each change of these settings is recorded as `admin.setting.changed` with old and new value, also while the log is off.

While the log is off, these rows are still written: the events of the group `audit`, changes of the `audit_log_*` settings, archive and restore transitions, and the clinic steps marked "Always recorded" above. The openings of transcripts and audio by a site administrator are refused. All other events, including the clinic content reads, are not recorded while the log is off; the requests themselves are still served.

A change of `audit_log_store_ip` applies to new rows. Addresses in existing rows are not changed.

The client address is the address the application sees after its proxy handling. `TRUSTED_PROXY_HOPS` (default `1`) is the number of reverse proxies whose `X-Forwarded-For` header is trusted. Without `TRUSTED_PROXIES`, the header is trusted from any peer, and a startup warning says so. Behind a reverse proxy, set `TRUSTED_PROXIES` to its address or network. With no proxy in front of the application, set `TRUSTED_PROXY_HOPS=0`; otherwise a client can choose the address that is stored.

## Retention of audit rows

The daily maintenance thread runs at 2:00 server time: retention archival, the cleanup of recordings without an owner, then the pruning of the audit log.

- **Without clinic mode** (upstream behavior): rows older than `audit_log_retention_days` are removed in batches, and an `audit.pruned` row records the run.
- **In clinic mode:** pruning removes nothing. It writes the line `Audit pruning withheld (separate_privileged_retention): <n> row(s) past cutoff <cutoff> kept` at INFO level on the logger `speakr.audit`. No `audit.pruned` row is written, and the table grows without limit. Plan database storage and backups for this.
- `recording.archived` and `recording.unarchived` rows are never removed, in either mode.

The removal of audit rows in clinic mode is intended for a separately privileged retention process under an approved retention policy and its own database role. The code contains only the planning step `plan_privileged_retention`. It checks an approved policy (id, approver, approval time, reviewed version, not shorter than `audit_log_retention_days`) and counts the eligible rows; it never deletes (`performed_deletion` is `false`). With `audit_log_retention_days` set to `0`, every policy is refused. The repository has no process that deletes audit rows, and no approved audit retention period: `retention.audit_days` in `clinic-spec/config/production.template.json` is `null`. This process is not implemented. The specification sets no legal retention period; it must be decided as a data-protection and business decision before production use.

## Append-only protection

- **Application.** ORM listeners refuse every update and delete of an `AuditEvent` ("Audit events are append-only …"). The only delete statement of the application is the pruning, which removes nothing in clinic mode.
- **Database.** Raw SQL is not blocked in a running deployment. The installer `install_audit_db_guards` (triggers against UPDATE and DELETE on SQLite and PostgreSQL, plus TRUNCATE on PostgreSQL) exists, but startup does not call it; only tests do. Status: Prepared, disabled. A person with write access to the database can therefore change or delete rows.
- **Database role.** A PostgreSQL application role without DELETE privilege on `audit_event` is a deployment matter. The application does not create it. Its proof test runs only with `CLINIC_AUDIT_DB_PROOF=1` against a disposable PostgreSQL. Database owners and superusers keep full authority.
- **Other clinic evidence tables.** At startup, database triggers on SQLite and PostgreSQL are installed for the archive ledger `clinic_archive_event` ("clinic archive evidence is append-only"), for `clinic_output_receipt`, `clinic_marker_event`, `clinic_asr_profile_version`, `clinic_asr_training_run`, `clinic_voice_consent` and `clinic_voice_consent_audit`, and for the immutable originals and transcript revisions. Other database engines get no triggers.

A copy kept outside the reach of the server administrators is the protection against a later change of `audit_event`. Export the log regularly with **Export JSONL** and store the files on such a system.

## Request ids and correlation

- A row has no request id. Rows of one request are matched by `created_at`, `actor_user_id` and `recording_id`.
- Many error responses of the clinic dictation API (`application/problem+json`), for example of the workflow, history, audio and context routes, carry a `correlation_id`, a random UUID created for each response. The statistics, import and parity command routes answer without it. Error messages of clinic jobs carry their own id `<operation key>:<job id>`. Neither is stored in `audit_event`.
- The `Idempotency-Key` of clinic commands and the internal `dedup_key` are not part of an exported row.
- `id` is assigned in write order and never given out again. Because reads and sign-ins are written up to a second after they happen, two rows of the same second can be listed in either order; the time of each row is exact.

## The page System-Auditprotokoll

- **Table.** Newest events first, in write order. A click on a row shows the details, the affected account, the owner of the recording and the user agent.
- **Refresh.** **Refresh** reads the page again with the same filters. Before reading, the answering server process waits at most 0.25 seconds until its queued events are written. If its writer is busy longer, the list shows the rows written so far.
- **Filters.** **From**, **To**, **User** (a username or an id; matches events by that account and events that concern it; an unknown name matches nothing), **Events** (a group), **Recording ID**, **Result** and **Access by**. Dates are days in the time zone of the browser. **Clear filters** resets them.
- **Recording ID in clinic mode.** The clinic route guard checks the **Recording ID** filter (parameter `recording_id`) like a view of that recording, on the page and on the export. The filter passes for the anchor recording of a clinic dictation, because an active site administrator may view every dictation, and for recordings you may view by ownership or grant. It is refused for a deleted recording, a recording without an owner, and another user's stock recording without a grant; being a site administrator gives no shortcut there. The answer is 404 `not_found` (403 `forbidden` for a refused recording you may know of). The page then shows "The audit log could not be loaded.", and the export link returns the error instead of a file. A policy denial is logged as `clinic policy denied view <endpoint> on recording <id> …`; a missing recording is not logged. In that case, export without **Recording ID**, narrow the rows with **From**, **To**, **User** or **Events**, and filter the file by `recording_id`.
- **Paging.** **Older** and **Newer**, 50 rows at a time.
- **Export.** **Export CSV** and **Export JSONL** download the rows of the current filter, oldest first, with the columns unchanged and `Cache-Control: no-store`. In CSV, a text cell that begins with `=`, `+`, `-`, `@`, a tab or a carriage return is preceded by an apostrophe. An export is a snapshot: the serving process first waits at most 1 second for its queued events to be written, and the file ends at the highest matching id at the start of the export.
- **Notice.** Until a site administrator dismisses it, a notice states that events are recorded, whether addresses are stored and how long rows are kept. It is shown on every installation, new or upgraded. Dismissing saves `audit_log_notice_dismissed`, which is recorded as `admin.setting.changed` with old and new value, also while the log is off. The notice text still mentions shares, which are not part of this build. In clinic mode the stated retention period is not applied (see above).

### Ids that are used again

On SQLite, the id of a deleted recording or account can be given to a new one. In clinic mode, the manual deletion of recordings and accounts is locked, but the cleanup of recordings without an owner and the cancelling of an upload still delete recordings. Read the rows of a recording together with its `recording.deleted` rows: its history consists of the rows after the last `recording.deleted` row with that id. In clinic mode, the **Recording ID** filter is refused for a deleted id (see above). Find such rows with the **Events** filter set to **Changes**, or export without a recording filter and search the file for the `recording_id`. On PostgreSQL, ids are not given out again.

## Access for an external system

In clinic mode, the log can be read and exported only in the browser session of a site administrator. A request authenticated by an API token is refused with 403 `clinic_feature_disabled`, and API tokens cannot be created. The scheduled collection by a monitoring system with a full-access API token, as described upstream, has the status Upstream only.

The parameters of both routes are `from` and `to` (a date, or an ISO 8601 date and time; `to` is exclusive; a date without a time is midnight UTC), `user`, `group`, `event`, `recording_id`, `outcome`, `actor_kind`, `before_id` and `after_id`, and for the page route `limit` (1 to 500, default 50). The export route takes `format` (`csv` or `jsonl`). An invalid date, or an unknown group, event, outcome or actor kind, is refused with 400. In clinic mode, the clinic route guard checks `recording_id` as described under [The page System-Auditprotokoll](#the-page-system-auditprotokoll).

## Failures

Events of reads and sign-ins are queued and written by a background thread of each process about once per second, so that a request never waits for the log. Events of changes and administrative actions are written in the same database transaction as the action: such a row exists exactly when the action took effect.

Each worker process has its own queue (up to 10,000 events). Before the list or an export is read, only the queue of the answering process is written. An event queued in another process can therefore appear up to about a second later.

A batch that cannot be written is tried again after 1, 5, 15 and 60 seconds. After that, each event of the batch is written to the application log as one line `AUDIT-FALLBACK {json}` at level ERROR on the logger `speakr.audit`, and the next batch that is written adds an `audit.write_failed` row with the count. An event that finds the queue full is handled in the same way. The line holds the same values as the row, including the client address when addresses are stored. Keep these lines in the collector of the container log.

After a kill of the server process, up to one second of read and sign-in events can be missing. Events of changes are not affected.

## Upgrade

- **The log is on after the upgrade.** From the first start, events are recorded with the client address. The settings above change this.
- **Shares.** Public share links and internal shares are not part of this build. The table `share_audit_log` of earlier releases is neither copied into `audit_event` nor mapped; an existing table is left in place. Rows that an earlier version copied (with `imported` in the details) stay in `audit_event`.
- **Growth.** In clinic mode, rows are not removed, and the clinic content reads are recorded on every request. The table grows faster than an upstream installation with pruning.

## Features in verification

The following is not part of the current released build. Labels and availability may change.

- Saving the per-task model selection (**Modellwahl**, model selection) writes `admin.setting.changed` with the key `clinic_task_models` and no old or new model ids.
- Reading a dictation summary writes `recording.content.read` with `part` `summary`. A manual summary request writes `recording.reprocessed` with `kind` `summary`.

## Upstream documentation

Upstream Speakr documentation of this topic, at the pinned base: [Audit log (upstream Speakr documentation)](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/audit-log.md). Its sections on shares, incognito uploads, registration, password reset and collection with an API token do not apply to the clinic build.
