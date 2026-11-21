# Webhooks
<small>[Deutsch](../de/admin-guide/webhooks.md)</small>

In the clinic build (`CLINIC_MODE` on), user webhooks are locked. The application sends no webhook
events, and users and administrators cannot create or manage webhooks. This page describes what is
locked, which webhook settings still appear and what they do, and the mechanisms the clinic build
implements instead: in-app workflow notices, the output view **Ausgabe** (Output) and the KIS interface,
which is simulated only.

## Status overview

| Function | Status in the clinic build |
|---|---|
| User webhooks (`/api/v1/webhooks`, account tab "Webhooks") | Upstream only (locked in clinic mode) |
| Polling API v1 with a personal API token | Upstream only (locked in clinic mode) |
| Web push notifications | Upstream only (locked in clinic mode) |
| In-app workflow notices (user menu, **Benachrichtigungen** / Notifications) | Available |
| Output view **Ausgabe**: TXT, DOCX, **Zwischenablage** (clipboard) | Available as a view. Every target needs an active final release, which is currently possible only with `CLINIC_PROFILE=staging` (Simulated). TXT and DOCX are rendered on the server, but no route returns the file. |
| KIS target in **Ausgabe**, KIS context for final release | Simulated (only with `CLINIC_PROFILE=staging`, synthetic MockKIS, no KIS evidence); otherwise the adapter is disabled and final release is refused |
| Reconciliation of a KIS delivery with an unclear outcome | Prepared, disabled (no route, no UI) |
| Server-side export folders, ODT output, DOCX bookmark templates | Prepared, disabled (library code, no route) |
| Insert into an open Word or Writer document, window or key automation, local mail client with audio attachment, Word add-in | Open gap (WG-EXP-004, WG-EXP-005, WG-EXP-007, WG-EXP-009, WG-EXP-010) |

## Webhooks in clinic mode

In clinic mode every feature switch is locked unless the contract file
`clinic-spec/config/dev.defaults.json` (or the same file in the directory named by `CLINIC_CONFIG_DIR`)
sets it to `true` under `features`. The exceptions are `chat`, `inquire` and `generative_summary`: they depend
on a local text model, not on this switch. The shipped file sets `"user_webhooks": false`. While this lock holds:

- **Routes refused.** The route policy lists all ten webhook endpoints (`webhooks.*`, mounted under
  `/api/v1/webhooks`: list, create, read, update, delete, rotate secret, test, list deliveries, read
  delivery, replay) as disabled under `user_webhooks`. The guard answers `403` with
  `"code": "clinic_feature_disabled"` and `"feature": "user_webhooks"`, before it checks the sign-in.
  A request that authenticates with an API token instead of a browser session is refused one step
  earlier with `"feature": "personal_api_tokens"`.
- **Nothing is dispatched.** Every webhook entry point checks the lock first: the event call
  (`emit_webhook_event`), the hook that queues `recording.updated`, and every pass of the dispatcher.
  No delivery row is created and no HTTP request leaves the server. `WEBHOOK_GLOBAL_ENABLED=true`
  cannot override the lock.
- **No UI.** On `/account` the "Webhooks" tab and its script `account-webhooks.js` are not rendered.
  A clinic test in `tests/clinic/` asserts this.
- **Log line.** In the process that runs the background work (not in a web-only worker with
  `CLINIC_WEB_ONLY=1`), the dispatcher thread still starts and logs
  `Webhook dispatcher started (interval=<n>s)`. It skips every pass while the lock holds.
- **No audit events.** The audit catalogue has no webhook events. See [Audit log](audit-log.md).

The binding decision D21 in `clinic-spec/config/decisions.json` excludes personal webhooks. Editing the
contract file is not an operating procedure for this build, and this manual gives no steps to unlock the
feature.

Source (clinic fork): [src/clinic/route_policy.py](https://github.com/joehomeskillet/speakr/blob/main/src/clinic/route_policy.py),
[src/services/webhook_dispatch.py](https://github.com/joehomeskillet/speakr/blob/main/src/services/webhook_dispatch.py).

### Remaining webhook settings and variables

In site administration, **Systemeinstellungen** (System settings, `/admin#/system/einstellungen`) still lists two
webhook settings. Only an account with the **Administrationsrolle** (administration role) can change them. While
the audit log is on, each change is audited as `admin.setting.changed` with the old and the new value.

| Setting | Environment variable | Default | Effect in clinic mode |
|---|---|---|---|
| `webhook_omit_titles` | `WEBHOOK_OMIT_TITLES` | `false` | None. No payload is built. |
| `webhook_delivery_retention_days` | `WEBHOOK_DELIVERY_RETENTION_DAYS` | `30` | Removes only delivery rows that already exist, for example from operation before clinic mode was switched on. `0` keeps them. |

The removal runs in the background cleanup thread, every `RECORDING_SESSION_CLEANUP_INTERVAL_SECONDS`
(default `3600`), and logs `Pruned <n> webhook delivery record(s)`. With `RECORDING_SESSION_CLEANUP_INTERVAL_SECONDS`
set to `0` or less, the cleanup thread, and with it this removal, does not run.

The shipped env examples (`config/env.*.example`) list further webhook variables, commented out. They are
upstream only and have no effect in clinic mode: `WEBHOOK_GLOBAL_ENABLED`, `WEBHOOK_MAX_PER_USER`,
`WEBHOOK_DELIVERY_TIMEOUT_SECONDS`, `WEBHOOK_MAX_ATTEMPTS`, `WEBHOOK_AUTOPAUSE_FAILURES`,
`WEBHOOK_TRIAL_INTERVAL_SECONDS`, `WEBHOOK_INTRANET_HOST_ALLOWLIST` and `WEBHOOK_SECRET_GRACE_HOURS`.
`WEBHOOK_DISPATCHER_INTERVAL_SECONDS` (default `5`, at least `1`) only sets how often the idle dispatcher wakes
up; its value appears in the log line `interval=<n>s`.

### Webhook data from an earlier upstream instance

When an existing Speakr instance is switched to clinic mode, its rows in the tables `webhook` and
`webhook_delivery` stay in the database. Nothing is sent, and no route or page shows or manages them.
Delivery rows, including queued ones, are removed by age as described above. Webhook rows are not removed.

- A delivery row holds the payload that was sent (recording id, title and original file name unless
  `webhook_omit_titles` was on, error text), the receiver's HTTP status and the first 2,000 characters of its
  answer.
- A webhook row keeps the name, target URL, subscribed events and the HMAC signing secret (and a previous secret
  during a rotation).

### Upstream behavior with clinic mode off

With `CLINIC_MODE` off, none of these locks apply and the webhook feature behaves as in upstream Speakr,
with one difference: the fork sends no share, calendar-event or shared-recording events. The available
event types are `recording.created`, `recording.transcription.started`,
`recording.transcription.completed`, `recording.transcription.failed`, `recording.summary.completed`,
`recording.summary.failed`, `recording.updated`, `recording.deleted` and `webhook.test`.

Upstream Speakr documentation (not valid for the clinic build):
[Webhooks, upstream Speakr](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/webhooks.md).

## Alternative: in-app workflow notices

**Status: Available.** The clinic build informs users about acceptance and completion through in-app
notices. Nothing leaves the server, no SMTP server is needed, and there is nothing to configure.

**Where.** In the page header, the user menu (the avatar with the initials) shows an unread badge and the
entry **Benachrichtigungen** (English interface: **Notifications**). Each notice has **Ansehen** (View)
and a dismiss button (**Ausblenden**, Dismiss). The header polls the unread count every 60 seconds.

| Event | Stored title (fixed German text) | Recipients | Triggered by |
|---|---|---|---|
| `acceptance_requested` | **Abnahme angefordert** (acceptance requested) | Approvers of the published acceptance policy of the dictation: listed approvers and members of approver author pools, minus the deny list | **Zur Abnahme einreichen** (submit for acceptance); also when the text changes after an acceptance and a new acceptance is required |
| `acceptance_rejected` | **Abnahme abgelehnt** (acceptance rejected) | The current assignee, that is the person the dictation is returned to | **Zur Korrektur zurückweisen** (return for correction) |
| `accepted` | **Diktat abgenommen** (dictation accepted) | Author and assignee | **Zusätzlich abnehmen** (accept) |
| `completed` | **Diktat fertiggestellt** (dictation completed) | Author | **Endgültig freigeben** (final release) |

Rules, as implemented:

- The acting user is never notified.
- Each recipient is checked again when the notice is delivered. An active account with the
  **Administrationsrolle** (administration role) always passes. Every other recipient needs the `view` action on the dictation through the pool policy;
  a recipient who fails the check is dropped.
- A notice is delivered as a job of type `clinic_notify` through the existing job queue. The job
  overview **Verarbeitung** lists it as **Benachrichtigung**.
- Each recipient gets at most one notice per transition (key
  `clinic.wf:<event>:<dictation>:<transition>`). A retried job delivers only to recipients whose notice
  could not be stored. A dismissed notice is not brought back.
- The stored text is a fixed German title and body, for example "Das Diktat wartet auf Ihre Abnahme."
  It contains no transcript and no patient data. Only the context keys `count`, `priority_label` and
  `deadline_date` are allowed, and the current triggers pass none.

Constraints:

- **Diktat fertiggestellt** needs a final release. Outside the staging profile, final release is not
  possible (see [KIS interface](#kis-interface-simulated)), so this notice does not occur there.
- The panel renders a notice from its message key (`clinic.workflow.<event>`) through the interface
  translations. The shipped locale files contain no entry for these keys, so according to the code the
  panel shows the key instead of the stored German title and body.
- According to the code, the **Ansehen** link points to `/clinic/dictations/<id>`, a path the application does
  not route, so it opens an error page. Open the dictation from **Arbeitsliste** (worklist) instead.
- Workflow notices are never sent by e-mail or webhook.

The upstream completion and failure e-mail is separate. It has no clinic gate and is described in
[Email Setup](email-setup.md).

## Alternative: output view "Ausgabe"

**Status: Available** (as a view), with the constraints below. In an opened dictation, the view **Ausgabe**
(Output, `#/diktat/ausgabe`) sits next to **Details**, **Prüfen und Freigeben**, **Verarbeitung** and
**Verlauf**. The table **Ausgabeziele** (output targets) lists **Textdatei (TXT)**, **Word-Dokument (DOCX)** and
**KIS**, each with **Ausgabe starten** (start output) and its own status. The panel **Zwischenablage**
(clipboard) with **Als Text kopieren** (copy as text) and **Mit Formatierung kopieren** (copy with formatting)
appears only when a released version exists. Without a release the view shows
**Keine freigegebene Version. Eine Ausgabe ist erst nach der Freigabe möglich.** (no released version; output
is possible only after release). Final release needs a trusted KIS context, so outside `CLINIC_PROFILE=staging`
no output target can be used (see [KIS interface](#kis-interface-simulated)).

- **Rights.** Output needs the Fachrecht **Exportieren** (Export, action `export`), also for
  **Zwischenablage**. Accounts with the **Administrationsrolle** (administration role, site administration) do
  not hold it by default. Final release (**Endgültig freigeben**) needs the separate right **Freigeben**
  (release), and original-audio export the separate right **Originalaudio exportieren** (export original audio).
  See [User Management](user-management.md) and [Group Management](group-management.md).
- **TXT, DOCX and KIS** output only the transcript revision of the active release. Any other revision is
  refused with `409` `NOT_RELEASED` ("Nur die freigegebene Version kann ausgegeben werden"). Each
  request needs a CSRF token and a UUID `Idempotency-Key`. For TXT and DOCX, a repeated request with the same
  key replays the stored result. For KIS, every repeated request returns the recorded outcome of that release,
  whatever key it sends.
- **A ready file job is not a saved file.** Status `ready` means the file was rendered on the server. The
  view shows **Datei erzeugt (noch nicht gespeichert)** (file created, not yet saved). The clinic build has no
  route that returns the rendered TXT or DOCX file. The job keeps only the file name, MIME type and SHA-256, so
  nothing can be downloaded from **Ausgabe**.
- **Zwischenablage** copies the released revision through the browser Clipboard API, as an explicit
  user action ("Explizite Browseraktion. Es wird nichts automatisch übertragen."). The server serves the text
  only while the released revision is still the current text; otherwise it answers `409` and the panel shows
  **Die Version ist veraltet. Bitte neu laden.** (the version is outdated, please reload). The server prepares
  the formats and records the outcome the browser reports (copied, denied or aborted). Only `copied`
  appends an output receipt. There is no operating-system window, focus or key automation.
- **API only, no clinic view:** `GET /api/clinic/v1/bundles/<dictation-id>/export` with `format=txt`, `rtf` or
  `sdict` returns the current text (no release needed) to a signed-in browser session with **Exportieren**, on
  the default client profile `managed_web_online`. An `.sdict` bundle contains an empty audio file, no
  dictation audio.
- **Prepared, disabled:** approved server-side export folders (server alias, collision policy `fail`,
  `version` or `unique_name`), ODT output from a template and versioned DOCX bookmark templates exist
  as library code only. No route uses them.
- **Open gaps:** inserting into an open Word or Writer document (WG-EXP-004, WG-EXP-005), window and key
  automation (WG-EXP-007), opening a local mail client with an audio attachment (WG-EXP-009) and a Word
  add-in (WG-EXP-010) stay open. The clinic build offers no equivalent.

## KIS interface (Simulated)

The KIS (clinical information system) is not a webhook target. A KIS delivery goes only through the
configured KIS adapter, and the adapter depends only on `CLINIC_PROFILE`:

| `CLINIC_PROFILE` | Adapter | Effect |
|---|---|---|
| unset or any other value | `DisabledAdapter` | Refuses everything. Nothing is sent. |
| `staging` | `MockKisAdapter` | Synthetic, scriptable test double that keeps documents in memory only. |
| `production` | `DisabledAdapter` | `MockKisAdapter` refuses to start under this profile. |

No environment variable, system setting or configuration file selects a real KIS adapter. A vendor
adapter is prepared in code: it stays disabled until all ten manufacturer contract fields and a transport
are supplied, and nothing in the application builds it. The `kis` sections of
`clinic-spec/config/dev.defaults.json` and `production.template.json` (for example
`automatic_retry_unknown: false`) are declarative and not read by the code. The production gate G-KIS is
open: the manufacturer contract (WebView, context and rights, write, receipt, lookup, addendum) is missing.

With `DisabledAdapter`:

- **Endgültig freigeben** (final release) is refused with `422` `UNTRUSTED_CONTEXT`.
- Without an active release (the normal case, because final release fails), **Ausgabe** shows
  **Keine freigegebene Version.** for TXT, DOCX and KIS, and an output request is refused with `409`
  `NOT_RELEASED`. Only if an active release already exists (for example from earlier staging operation with the
  same database) does the KIS target show **nicht verfügbar** (not available), and a KIS request answers `503`
  `KIS_UNAVAILABLE` ("Keine KIS-Anbindung konfiguriert"). In no case is a row written to the delivery ledger
  `clinic_delivery`.

With `CLINIC_PROFILE=staging`, a synthetic case ID in the format `SYN-0001` (`SYN-` and 4 to 8
digits) can be bound through `/api/clinic/v1/dictations/<id>/context`. Only in this profile, the dictation
view **Details** shows the section **Fall-ID verknüpfen** (link case ID) with the field **Synthetische Fall-ID**
(synthetic case ID) and the button **Fall-ID verknüpfen**. Binding needs view and edit rights on the dictation
and a current edit lock. Without the lock, the route answers `423` ("Eine aktive Bearbeitungssperre ist
erforderlich"), and the view shows **Bearbeitung zuerst anfordern.** (request editing first). If the staging
profile or an enabled `MockKisAdapter` is missing, the route answers `503` ("Fall-ID-Simulation nicht
verfügbar"). It also needs the client profile `managed_web_online` or `managed_web_offline` (otherwise `403`). Results with
`MockKisAdapter` are not evidence of a KIS integration.

### KIS context

- A KIS context (source system, tenant, case reference, patient reference) is created only through a grant
  that the server verifies: it is bound to the acting user and the dictation, expires within 60 seconds and has
  a single-use nonce. Context revisions are append-only. No resolver for a real KIS grant exists in the clinic
  build. The only caller is the staging simulation: the route `/api/clinic/v1/dictations/<id>/context` builds
  the grant on the server from the entered `SYN-` case ID (tenant `staging`, random patient reference).
  Nothing external is verified, and this is not evidence of a KIS integration.
- The binding work order forbids deriving the KIS context from a window name, the page DOM or the
  transcript. The context fields are taken only from the verified grant.
- Final release accepts only the current context revision bound to a consumed grant. A disabled adapter,
  or a mock adapter under `CLINIC_PROFILE=production`, is never trusted.
- A KIS delivery sends only the context revision bound to the release, only when its four context fields
  are present, and only when the adapter accepts the context.

### Unclear outcome (UNKNOWN): reconcile before any retry

- The delivery ledger row is committed as `PENDING` before the adapter is called. The ledger key is
  `export:<release id>`, so each release is sent at most once to a target.
- A timeout or an unreadable answer sets the row to `UNKNOWN`. An unexpected error leaves the row
  `PENDING`, which blocks in the same way. A repeated request then sends nothing and returns `UNKNOWN`
  ("outcome unclear: reconcile first"). **Ausgabe** shows
  **Unbekannt: Bitte Status abgleichen, nicht erneut senden.** (unknown: reconcile the status, do not send
  again). Its button **Status prüfen** (check status) only reads the stored state and never sends again.
- Only a reconciliation through the adapter's lookup resolves the state. "Confirmed" marks the delivery
  as delivered. "Absent" permits exactly one new attempt. "Unknown" keeps it blocked. Each reconciliation
  and each permitted retry is appended as a history row (`<key>#hNNNN:<KIND>`), and history rows are
  never changed.
- The application currently enqueues no output job (`clinic_output`); KIS output from **Ausgabe** goes
  directly through the delivery ledger. If such a job exists and failed after the delivery started,
  **Verarbeitung** lists it with the state `UNKNOWN`. Instead of **Erneut versuchen** (retry) it shows
  **Abgleich prüfen** (check reconciliation). The button sends nothing and runs no reconciliation; it only shows
  the hint "Das Ergebnis ist unbekannt. Bitte im Abgleich prüfen." A retry request through the API is refused
  with `422` `RECONCILE_REQUIRED`.
- Retrying any other failed job in **Verarbeitung** is offered and accepted only for active accounts with the
  **Administrationsrolle** (administration role). Other users see jobs read-only.
- No route and no page runs the reconciliation. It exists only as a service function, and no real KIS
  adapter exists to run it against. An `UNKNOWN` delivery therefore stays blocked, and the code never
  retries it automatically.

Source (clinic fork): [src/clinic/kis.py](https://github.com/joehomeskillet/speakr/blob/main/src/clinic/kis.py).

## What is not an alternative

- **Polling the API with a token.** In clinic mode, a request that authenticates with an API token and
  has no browser session is refused with `403` `clinic_feature_disabled` (`personal_api_tokens`). The
  token routes are disabled too.
- **Web push.** The push routes (`push.*`) are disabled under `push_notifications`.
- **E-mail as a KIS or webhook substitute.** See [Email Setup](email-setup.md).

## Related pages

- [System Settings](system-settings.md)
- [User Management](user-management.md)
- [Group Management](group-management.md)
- [Audit log](audit-log.md)
- [Privacy and regulated data](privacy.md)
- [Email Setup](email-setup.md)
