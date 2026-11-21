# Recording sessions
<small>[Deutsch](../de/admin-guide/recording-sessions.md)</small>

This page explains how audio gets from the browser to the server in the clinic build, what the browser keeps, and what an administrator can configure or see. It covers the clinic recorder **Neue Aufnahme** (New recording), the live dictation page **Live-Diktat** (Live dictation), the client profiles for own devices (BYOD) and the managed offline profile, the admin page **Import** for device and folder import with the import-agent channel, and the upstream server-side recording sessions that remain in the code.

Everything clinic-specific on this page applies only with `CLINIC_MODE` set to `1`, `true`, `yes` or `on`. With clinic mode off, upstream behavior applies unchanged.

Upstream Speakr documentation (pinned original): [Server-side recording sessions](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/recording-sessions.md)

## Status overview

| Function | Status | Short description |
|---|---|---|
| Clinic recorder **Neue Aufnahme** | Available | Records in browser memory and sends the finished take in one upload. The server creates a dictation draft. |
| File upload on **Neue Aufnahme** | Available | One upload request per file, the same path as a recorded take. |
| Browser crash recovery and retry store (IndexedDB) | Upstream only | Denied in clinic mode. The browser stores no audio. |
| Server-side recording sessions for the recorder (`ENABLE_SERVER_RECORDING_CHUNKS`) | Upstream only | Streaming of the stock recorder. Never used by **Neue Aufnahme**, whatever the flag says. The code default is `false`, and `clinic-spec/config/upstream.env.example` sets `true`. |
| Session endpoints `/upload/session…` | Available | Still reachable for signed-in users. They are not used by the clinic pages. |
| **Live-Diktat**: session start, pause, continue, stop, speech lock, resume | Available | Server side and page controls exist. |
| **Live-Diktat**: durable phrase storage | Prepared, disabled | The server route exists, but the shipped page sends no phrases. |
| **Live-Diktat**: audio capture and live recognition feeding phrases | Prepared, disabled | The shipped page has no capture or recognition connected, so no phrases arrive. |
| Switching between several live drafts | Prepared, disabled | The route answers `CAPABILITY_UNAVAILABLE`. |
| Separate BYOD client profile | Prepared, disabled | Every request currently gets the profile `managed_web_online`. |
| Managed offline profile (encrypted local drafts) | Prepared, disabled | Synthetic tests only. Gate G-MANAGED-WEB-OFFLINE is open. |
| Live dictation into foreign Windows fields, window binding, always-on-top | Open gap | WG-LIVE-002, WG-LIVE-006, WG-LIVE-007 |
| Admin page **Import** (import jobs, device inventory, import routing, clarification list) | Available as a view | The lists stay empty in the released build; assignment and retry have no server route. See [Device and folder import](#device-and-folder-import-admin-page-import). |
| Import-agent channel (`/api/clinic/v1/agent/`) | Prepared, disabled | No supported way registers an agent. The Philips device side is Simulated. |

## Clinic recorder: Neue Aufnahme

### Where users find it

- Header button **Neue Aufnahme**, or the menu **Verwaltung & Seiten** (Administration and pages), section **Diktat und Ausgabe** (Dictation and output), entry **Neue Aufnahme**.
- Direct link: `/#/neu`.

### Flow

1. **Diktiert von** (Dictated by) shows the signed-in person. It cannot be changed on this page, and there is no author or pool picker.
2. **Quelle** (Source) offers **Aufnehmen** (Record) or **Datei hochladen** (Upload file).
3. For a recording, a **Titel** (Title) is required before **Aufnahme starten** (Start recording) is possible. The controls are **Pausieren**, **Fortsetzen**, **Aufnahme beenden**, **Zurückspulen**, **Abspielen** and **Abbrechen** (pause, resume, stop, rewind, play, cancel). The page records from the microphone.
4. **Entwurf sichern** (Save draft) sends the take to the server. The page note reads: "„Entwurf sichern“ speichert die Aufnahme auf dem Server. Mit „Einreichen“ geben Sie sie an den Schreibdienst weiter." (Save draft stores the recording on the server; Submit hands it to the typing service.)
5. After a server receipt, the page shows "Gespeichert. Noch nicht an den Schreibdienst gesendet." (Saved, not yet sent to the typing service) and the dictation ID only, without title or patient data. The actions are **Einreichen** (Submit), **Zur Arbeitsliste** (To the worklist), **Öffnen** (Open) and **Weitere Aufnahme** (Another recording).

A selected file must be non-empty and have an audio MIME type or one of the extensions `wav`, `mp3`, `m4a`, `aac`, `ogg`, `oga`, `opus`, `flac`, `webm`, `wma`, `ds2` or `dss`.

### Transport: one upload, no chunk streaming

The page sends a recorded take and a selected file the same way: one multipart `POST /upload`, after capture ends. A recorded take carries `title` and `from_recorder=true`. The page does not open a server-side recording session and does not use the sliced-upload transport, even when `ENABLE_SERVER_RECORDING_CHUNKS=true`.

The upload runs through the normal ingestion pipeline. In clinic mode the server then creates the dictation:

- Author is the signed-in uploader. Pool is that user's author pool. Inactive or out-of-window pools are skipped.
- A user in no routable author pool is refused with HTTP 403 `clinic_no_author_pool` ("Ihr Benutzerkonto ist keinem Autorenpool zugeordnet. Die Aufnahme wurde nicht als Diktat angelegt."). The page itself shows only its general failure text. Set up author pools first, see [Group management](group-management.md).
- The idempotency key is `web:<user>:<hash of the content>`, because this page sends no client upload ID. The same bytes uploaded again by the same user return the existing dictation.
- The file is copied into the clinic originals store under `CLINIC_ORIGINALS_ROOT` (default `UPLOAD_FOLDER`) as the upload pipeline stored it. With the default `CONVERT_QUEUE_WORKERS=2`, a readable audio file arrives as uploaded and is converted afterwards in the upload recording only. With `CONVERT_QUEUE_WORKERS=0`, it is converted during the request first. `.ds2` and `.dss` files are never converted. See [File storage](storage.md).
- The new dictation starts in workflow status draft. A failure removes the half-made upload recording and answers with "Die Aufnahme konnte nicht als Diktat angelegt werden. Bitte den Vorgang wiederholen."

### Submitting (Einreichen)

**Einreichen** calls the workflow command `send` on the dictation. It is refused unless:

- the caller is the author and holds `edit` on the dictation,
- the dictation is still a draft (otherwise "Das Diktat ist kein neuer Entwurf mehr. Bitte den aktuellen Stand öffnen."),
- an audio revision exists ("Für diesen Entwurf liegt keine gültige Audiofassung vor."), and
- the author pool is routed to an active staff pool ("Kein Schreibkräfte-Pool zugeordnet.").

A successful send writes the audit event `recording.edited` with the fields `workflow_status` and `workflow_send`, even while the general audit switch is off. If that row cannot be written, the send fails with `audit_unavailable`. See [Audit log](audit-log.md).

### Limits that apply to a take

| Limit | Value | Source |
|---|---|---|
| In-browser size of one take | Automatic stop at 200 MB, warning at 80 % | Stock capture code, fixed value |
| Duration of one take | `RECORDING_MAX_HOURS`, default `8`, warning at 80 % | Environment variable, rendered into the page |
| MediaRecorder timeslice | `RECORDING_CHUNK_SECONDS`, default `5`, clamped to 1-60 | Environment variable. On this page the slices stay in browser memory. |
| Upload size | The request ceiling always applies. It is the larger of `max_file_size_mb` (seeded 250) and `max_audio_only_video_size_mb` (seeded 4 × `max_file_size_mb`). It is set at startup and recomputed when either setting is saved. The per-file limit `max_file_size_mb` applies only while chunking is off. | [System settings](system-settings.md) |
| Storage quota | Checked once when a take starts: no new take starts when the quota is full. A check that fails to run does not block. Not checked again at the end for a take recorded in a signed-in browser (`from_recorder=true`). Selected files are checked. | Stock capture code, ingestion pipeline |
| Reverse proxy | Body size limit and read timeout of your proxy | Your proxy configuration |

Because the whole take travels in one request, the reverse proxy must accept a request body as large as the largest expected take or file, and its read timeout must cover the in-request ingestion. A take above the proxy limit cannot be saved. It then exists only in browser memory until the page is left (see the next section).

Example for nginx. `client_max_body_size` is the request ceiling, here for the seeded values (`max_file_size_mb` 250, `max_audio_only_video_size_mb` 1000):

```nginx
location = /upload {
    proxy_pass http://speakr_upstream;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    client_max_body_size 1000m;
    proxy_read_timeout 600s;
}
```

Every `location` block needs the `proxy_set_header` lines shown. The application reads the forwarded headers through its trusted-proxy handling (`TRUSTED_PROXY_HOPS`, `TRUSTED_PROXIES`, see [System settings](system-settings.md)).

The `recording` block in `clinic-spec/config/dev.defaults.json` (`max_minutes` 30, `max_original_bytes`, `recorder_timeslice_ms`, `max_browser_ram_bytes` and others) is a contract value. No code reads it, so changing it changes nothing.

## What the browser keeps, and what happens after a failure

- Audio and title stay in browser memory only. The recorder page writes nothing to IndexedDB, `localStorage` or `sessionStorage`.
- The stock crash-recovery store and failed-upload store (IndexedDB) are gated in clinic mode. They are allowed only when the page element `#app` carries `data-clinic-persistent-audio="True"`. No template renders this attribute, so persistence is always denied.
- If **Entwurf sichern** fails, the page shows "Senden fehlgeschlagen. Der Entwurf wurde nicht gesendet. Die Aufnahme wird nicht lokal gespeichert." The take stays in memory as long as the page stays open, and **Erneut sichern** (Save again) retries it. For a file the texts are "Hochladen fehlgeschlagen. …" and **Erneut hochladen** (Upload again).
- A failed upload of a browser recording through the stock upload queue shows "Der Upload ist fehlgeschlagen. Die Aufnahme wird nicht lokal gespeichert. Bitte erneut aufnehmen."
- A reload, a closed tab, a browser crash or leaving the page before a confirmed save loses the take. Nothing is on the server yet, and nothing is on the device. Leaving the page discards an unsent take. An upload already in flight is not cancelled.

## Live dictation: Live-Diktat

### Where users find it

Menu **Verwaltung & Seiten** > **Diktat und Ausgabe** > **Live-Diktat**, or `/#/live`. Live dictation works on an existing dictation. In the field **Diktat suchen** (Search dictation), the user picks an own open dictation ("Eigenes offenes Diktat nach Name, Fall-ID oder Datum auswählen. Eine bekannte Diktat-ID ist optional möglich." — pick an own open dictation by name, case ID or date; a known dictation ID can be entered instead) and clicks **Live-Diktat öffnen** (Open live dictation), or opens `?dictation=<id>`.

The page controls are **Starten**, **Pausieren**, **Fortsetzen** and **Beenden** (start, pause, continue, stop). **Verbindung wiederherstellen** (Reconnect) appears after a lost connection or a lost holder role, and **Erneut speichern** (Save again) appears for unsaved phrases.

### Server-side behavior

- One live session belongs to one dictation and one holder, the person who dictates. Its states are `RECORDING`, `PAUSED` and `ENDED`. The database allows only one open session per dictation.
- Every live action needs a signed-in browser session, the `X-CSRF-Token` header and the `edit` right on the dictation. Pause, continue, phrase storage and stop are for the holder only.
- **Speech lock:** while a session is open and its lock is valid, other users cannot change the dictation text. Each holder action renews the lock to 60 seconds from now. This value is a code constant, not a setting. After 60 seconds of inactivity the lock lapses: the same holder can resume, and another user with `edit` can start a new session, which ends the old one with reason `expired`. There is no heartbeat route. The shipped page renews the lock only when the holder starts, resumes, pauses or continues. After 60 seconds without such an action, the next pause or continue is refused, and the page shows "Sie sind nicht mehr der aktive Sprecher. Neue Sätze werden nicht gespeichert." (You are no longer the active speaker; new phrases are not saved). **Verbindung wiederherstellen** resumes the holder's own session, as long as no other user has taken the dictation over since.
- **Durable phrases:** each final phrase is stored with its audio. The audio is written to the clinic originals store and flushed to disk (fsync) before the phrase row and the session receipt are committed. A phrase counts as **Gespeichert** (Saved) only when the server receipt matches that session, generation and sequence. Each phrase is bound to the dictation's current audio and text revision.
- **Reconnect:** the resume call returns the stored generation, sequence and audio offset, plus at most 50 stored phrases after the last sequence the browser knows. The server can fence out an older tab by a client ID, but the shipped page sends none.
- **Stop:** the page first saves all unsaved phrases. If that fails, it shows "Beenden nicht möglich: Es gibt nicht gespeicherte Sätze." (Cannot stop: there are unsaved phrases) and sends nothing. The server refuses `stop_live_session` with `REVISION_CONFLICT` when the sent last sequence and generation do not match the stored position.
- The page keeps phrases, partial text and audio references in memory only.

| Route | Purpose |
|---|---|
| `POST /api/clinic/v1/parity/commands` with `start_live_session` | Open a session |
| `POST /api/clinic/v1/live/sessions/<id>/pause` and `…/continue` | Pause and continue |
| `POST /api/clinic/v1/live/sessions/<id>/phrases` | Store one final phrase with its audio |
| `POST /api/clinic/v1/live/dictations/<id>/resume` | Resume from the stored position |
| `POST /api/clinic/v1/parity/commands` with `stop_live_session` | End the session |
| `POST /api/clinic/v1/live/drafts/<id>/switch` | Draft switch. Not wired: answers `CAPABILITY_UNAVAILABLE`. |

Responses of the routes under `/api/clinic/v1/live/` carry `Cache-Control: private, no-store`. With clinic mode off, the live routes answer 404.

### What is not connected

- The shipped **Live-Diktat** page has no audio capture and no recognition feed. It can start, pause, continue, stop and resume a session, but no phrases arrive, and the list stays at "Noch kein Text. Mit der Aufnahmetaste beginnen."
- The local live ASR adapter (`CLINIC_LIVE_ASR_BASE_URL`, `CLINIC_LIVE_ASR_TIMEOUT`, `CLINIC_LIVE_ASR_API_KEY`, `CLINIC_LIVE_ASR_MODEL`) exists in code and accepts only a local endpoint. No route or job uses it, so setting these variables has no effect.
- The live session code writes no audit event for start, pause, continue, phrase storage, resume or stop.
- Live dictation into foreign Windows text fields, speech lock bound to a foreign window and native focus binding stay open gaps (WG-LIVE-002, WG-LIVE-006, WG-LIVE-007). The web route is the live editor in the browser plus a deliberate clipboard or file export.

Speech recognition stays local only. There is no browser speech service and no cloud fallback. For the batch recognition that runs after an upload, see [Local speech recognition](whisperx-setup.md).

## Own devices (BYOD) and the managed offline profile

### Client profiles

The client profile is a server decision. A header, user agent, host name or query flag never selects it. No trusted ingress mapping exists yet, so every request, from a managed workstation or a personal device, gets the default profile `managed_web_online` from `web_client.default_profile` in `dev.defaults.json`.

| Profile | Audio capture | Persistent audio in the browser | Document export | UI label |
|---|---|---|---|---|
| `managed_web_online` | Browser | No | Permission-controlled | Verwaltetes Web, nur online |
| `byod_internal` | Browser, foreground | No | No | Eigenes Gerät, nur online |
| `kis_webview` | Browser, if the host permits | No | Manufacturer contract and policy | KIS-Ansicht, nur online |
| `managed_web_offline` | Browser | Encrypted, opt-in, only after its gate | Permission-controlled | Verwaltetes Web, Offline freigegeben |

The profiles come from `clinic-spec/config/client-capabilities.json`. In all online profiles the browser keeps no audio. Gate G-BYOD (approved device matrix, internal TLS/DNS, residual-risk and interruption sign-off) is open, so BYOD with real data is not approved.

### Managed offline profile

- `GET /api/clinic/v1/offline-web/capability` (clinic mode, signed-in session, `Cache-Control: no-store`) always answers `available: false` with "Offline-Speicherung ist nicht freigegeben." No trusted browser verification and no grant provider are wired.
- `web_client.managed_offline_enabled` and `web_client.byod_persistence_enabled` are `false` in `dev.defaults.json`. Setting `managed_offline_enabled` to `true` alone does not enable anything, because the route still has no verified browser and no grant.
- The page **Offline und Wiederherstellung** (Offline and recovery, menu section **Betrieb und Zugriff** (Operations and access), `/#/offline`) shows "Offline-Speicherung ist nicht freigegeben. Es wird nichts lokal gespeichert und es gibt keine lokale Wiederherstellung. Bereits auf dem Server bestätigte Daten bleiben über die Arbeitsliste erreichbar."
- The encryption and storage modules (AES-GCM, PBKDF2-SHA-256, purge on logout) exist in code, but no storage adapter is provided, so they store nothing.
- Gate G-MANAGED-WEB-OFFLINE (browser persistence, key, revocation, quota, eviction, crash and user-change tests, residual-risk sign-off) is open. The tests of the offline policy are synthetic.

## Device and folder import (admin page Import)

The admin page **Import** (`/admin#/import`, group **Betrieb** (operations)) is the web view of the dictation file import from approved devices and folders. Its title is **Dateien und Geräteimport** (files and device import). The header menu entry with the same name opens the user view `/#/import` with the same panels.

Status: the page is Available as a view. In the released build its lists stay empty, because nothing creates import jobs, approved sources or agent deliveries (see [Import agent channel and ingress](#import-agent-channel-and-ingress)). The Philips device side is Simulated.

**Access**

- `/admin` probes the page with `GET /api/clinic/v1/imports/intents`. The probe passes only for an account with the **Administrationsrolle** (administration role) and a clinic account with the status **Aktiv** (active). The import routes refuse every account without an active clinic account with `403`.
- The server code also accepts the actions `import` and `manage_import_sources`, but they are not grantable actions: the **Fachrechte** (professional rights) editor and the rights API refuse them. In practice only active site administrators use the page.

**Panels**

| Panel | Content | Status |
|---|---|---|
| **Importaufträge** (import jobs) | Import jobs created by the signed-in account, with **Quelle** (source), **Dateien** (files), **Status** and **Erstellt** (created). Failed or expired jobs show **Erneut versuchen** (retry). Empty list: **Keine Importaufträge vorhanden.** | Available as a view; the list stays empty, because no route, command or script creates an import job. The catalog commands `queue_import`, `configure_routing` and `request_source_cleanup` have no handler. **Erneut versuchen**: Open gap, no server route exists. |
| **Geräteinventar** (device inventory) | Files reported by registered import agents, with **Quelle**, **Datei** (file), **Grösse** (size), **Aufnahmedatum** (recording date) and **Stand** (snapshot). The panel states **Nur Ansicht. Konfiguration und Firmware-Schreiben sind nicht verfügbar.** (view only; configuration and firmware writing are not available). Without a report: **Es liegt keine Meldung eines Importagenten vor. Es wird keine Geräteerkennung angezeigt.** | Read only. Device data: Simulated. Writing a device configuration (WG-IMP-009) and writing firmware (WG-IMP-010) stay Open gap. |
| **Importzuordnung** (import routing) | Approved sources (**Gerät** or **Ordner**, device or folder) whose root lies inside `CLINIC_IMPORT_ROOT_ALLOWLIST`, each with its **Importautor** (import author) and an optional **Autorenpool** (author pool). Without a source: **Keine lokal freigegebene Quelle zur Verwaltung vorhanden.** | Available for existing sources. No source can be created on the web or by a shipped command, so the list stays empty. |
| **Klärliste** (clarification list) | Delivered files without a unique assignment, with **Zuweisen** (assign) and **Zuweisung bestätigen** (confirm assignment). Empty list: **Keine Dateien in Klärung.** | Visible to active site administrators and, in `/#/import`, to leads of the pools that a source routes to. **Zuweisung bestätigen**: Open gap, no server route exists; the action fails and writes no audit row (see [Audit log](audit-log.md)). |

**Changing the import routing**

1. On **Importzuordnung**, choose **Gerät … zuordnen** or **Ordner … zuordnen** (assign device or folder) for the source.
2. Choose the **Importautor**: any account with an active clinic account.
3. Optionally choose one of that author's author pools under **Autorenpool**, or keep **Ohne Poolvorgabe** (no pool preset).
4. Click **Speichern** (save). The page shows **Importzuordnung gespeichert.** (import routing saved).

Only an active site administrator can save. Each save carries the version of the source; if the source changed in the meantime, the page shows **Die Zuordnung wurde inzwischen geändert. Bitte erneut auswählen.** Each save is audited as `admin.setting.changed` with the key `clinic_import_routing:<source id>`, also while `audit_log_enabled` is off; if that row cannot be written, the save fails.

### Import agent channel and ingress

Status: Prepared, disabled.

- The Windows import agent (`apps/windows-import`) is the only local agent. It calls the server routes under `/api/clinic/v1/agent/`; the server never opens a connection to the agent.
- The released build has no supported way to put an agent into service. Registering an agent with its credential fingerprint, creating an approved source and creating an import job exist as server functions, but no route, command or script calls them; only tests do.
- The device side is Simulated: the simulator mode of the agent and the test fixtures are no evidence for Philips devices.

The channel uses a machine mTLS contract. TLS with client-certificate verification ends at a trusted reverse proxy, which must do the following for every request to `/api/clinic/v1/agent/`:

1. Verify the client certificate of the agent.
2. Set `X-Clinic-Client-Cert-Sha256` to the SHA-256 fingerprint of the verified certificate, in hexadecimal (colons, spaces and letter case are ignored).
3. Set `X-Clinic-Proxy-Secret` to the value of `CLINIC_AGENT_PROXY_SECRET` (at least 32 bytes, for example `<secret>`).
4. Overwrite or remove both headers on every request, so that a client can never pass its own values through.

A request without the correct secret, without a fingerprint, with a malformed or unknown fingerprint, or from a disabled agent gets `401`. These routes never read the session cookie and are exempt from CSRF. `CLINIC_AGENT_PROXY_SECRET` and `CLINIC_AGENT_MASTER_SECRET` need at least 32 bytes each; without them, a random value per process is used and every agent request gets `401` (see [System Settings](system-settings.md#files-dictation-formats-and-import)).

## Server-side recording sessions (upstream mechanism)

Upstream Speakr can stream recorder chunks to the server while the user records. The code is still in the clinic build. Its status here:

- The clinic recorder never uses it. The stock recorder code turns streaming off whenever clinic mode is on and the current view is the clinic recording page.
- `ENABLE_SERVER_RECORDING_CHUNKS` defaults to `false`. The clinic template `clinic-spec/config/upstream.env.example` sets it to `true`. Either value has no effect on **Neue Aufnahme**. Do not treat it as a clinic recorder switch.
- The endpoints are classified `AUTH` in clinic mode, so any signed-in user can call them. A synthetic test confirms that `POST /upload/session` returns 201 in clinic mode.
- A recorder session that is finalized, by the user or by the cleanup sweep, becomes a recording with source `recording_session`. In clinic mode that recording gets no clinic dictation when it is finalized. A later run of `scripts/clinic_backfill_dictations.py --apply` can take a final one (status `COMPLETED` or `FAILED`) over as a dictation, see [Migration guide](migration-guide.md).
- A sliced file upload that is finalized through `finalize-upload` is ingested like `POST /upload`. In clinic mode it therefore creates a dictation, with the same author-pool rule as above.
- The stock upload code sends files larger than 16 MiB as slices. This transport has no clinic-specific gate. The clinic pages do not use it.
- The stock upload dialog, with its own recorder buttons, is still part of the clinic page code and has no clinic-mode gate. The deep link `/?upload=1` that opens it does not check clinic mode either. Whether users can reach the dialog in the clinic UI was not verified. To keep its recorder on the single-upload path, leave `ENABLE_SERVER_RECORDING_CHUNKS` at the code default `false` instead of the template value `true`.

### Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/upload/session` | Create a session. `{mime_type}` for a recorder session, `{filename, total_bytes}` for a sliced file upload. |
| POST | `/upload/session/<id>/chunks/<N>` | Append chunk N. The body is raw bytes. |
| GET | `/upload/session/<id>` | Status of a session |
| POST | `/upload/session/<id>/finalize` | Recorder session: create a recording in status `STITCHING` and queue a `stitch` job |
| POST | `/upload/session/<id>/finalize-upload` | Sliced upload: join the slices and ingest them in the request |
| DELETE | `/upload/session/<id>` | Abort and remove the stored chunks |

All endpoints require login, and a session is visible only to its owner (other users get 404). The `/upload/session` paths are exempt from the IP rate limit.

### Configuration

| Variable | Default | Purpose |
|---|---|---|
| `ENABLE_SERVER_RECORDING_CHUNKS` | `false` | Streaming for the stock recorder only. No effect on the clinic recorder. Only the string `true` (any case) enables it; `1`, `yes` and `on` do not. |
| `RECORDING_SESSION_TTL_HOURS` | `24` | Sessions whose `last_seen_at` is older are handled by the cleanup sweep. |
| `RECORDING_SESSION_MAX_BYTES_PER_USER` | `5368709120` (5 GiB) | Soft per-user cap on unfinished sessions. Above it the API answers 507. |
| `RECORDING_SESSION_MAX_CHUNK_BYTES` | `16777216` (16 MiB) | Cap per chunk or slice. Larger chunks get 413. |
| `RECORDING_SESSION_ALLOWED_MIME_TYPES` | `audio/webm,audio/ogg,audio/mp4,audio/mpeg,audio/wav,audio/x-m4a,video/webm,video/mp4` | Allowed types for recorder sessions. Not checked for sliced uploads. |
| `RECORDING_SESSION_CLEANUP_INTERVAL_SECONDS` | `3600` | Interval of the cleanup sweep. `0` or less turns it off. |
| `RECORDING_SESSION_INGEST_HEARTBEAT_SECONDS` | `30` (minimum 5) | Liveness stamp of an in-request ingest. After three missed stamps another finalize may take the session over. |
| `RECORDING_SESSION_COMMIT_BATCH_SIZE` | `1` | Leave at 1. Above 1 the chunk endpoint only flushes its bookkeeping instead of committing it. The upstream documentation reports that this stalls the chunk sequence. |
| `RECORDING_MAX_HOURS` | `8` | Duration ceiling of a recording, at least 1 minute. Also applies to the clinic recorder. |
| `RECORDING_CHUNK_SECONDS` | `5` | MediaRecorder timeslice, clamped to 1-60 seconds |
| `RECORDING_VIDEO_KBPS` | `2500` | Video bitrate for the stock tab or screen capture, clamped to 100-50000. The clinic recorder records the microphone only. |

### Cleanup and storage

- Chunks are stored under `UPLOAD_FOLDER/_sessions/<id>/` as `chunk-000001.bin` and following, with a `session.json` copy of the database row. A sliced upload also writes `joined.bin` during its finalize, so it briefly needs about twice the file size.
- The cleanup sweep auto-finalizes an abandoned recorder session that has chunks. It marks an abandoned recorder session without chunks, and every abandoned sliced upload, as `expired` and removes its directory. It re-enqueues the stitch job of a session stuck in `finalizing`. It also removes orphaned session directories older than `RECORDING_SESSION_TTL_HOURS`. It logs a line such as "Recording-session cleanup: … auto-finalized, … re-enqueued, … expired, … orphaned dir(s) removed".
- `DELETE` removes the chunks at once. It is refused with 409 for a finalized session (`session_already_finalized`) and for a sliced upload that is finalizing (`session_finalizing`).
- `finalize` answers 202 at once. The stitch job runs afterwards and uses `ffmpeg`. Without `ffmpeg`, a single-segment session is kept as the raw byte-joined stream without remux (log warning "ffmpeg not found; using raw byte-joined stream without remux"). A resumed multi-segment session fails in the stitch job with "ffmpeg binary not found on server PATH", and the recording is marked `FAILED`.

### Reverse proxy for these paths

Chunk and slice requests are at most `RECORDING_SESSION_MAX_CHUNK_BYTES` (16 MiB). `finalize-upload` ingests in the request, like `POST /upload`, and needs a long read timeout. Example for nginx:

```nginx
location /upload/session/ {
    proxy_pass http://speakr_upstream;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    client_max_body_size 32m;
    proxy_read_timeout 30s;
}

location ~* ^/upload/session/.+/finalize(-upload)?$ {
    proxy_pass http://speakr_upstream;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 600s;
    proxy_send_timeout 600s;
}
```

For the clinic recorder, size the limits of `/upload` instead (see [Limits that apply to a take](#limits-that-apply-to-a-take)). Every block needs the usual `proxy_set_header` lines.

## What administrators can configure and see

- **No admin page** in **Verwaltung** (Administration) shows recordings in progress, live sessions or server-side recording sessions, and none configures the recorder. The settings are environment variables and system settings.
- **Configure:** `RECORDING_MAX_HOURS`, `RECORDING_CHUNK_SECONDS`, the reverse proxy body limit and read timeout, `max_file_size_mb` in [System settings](system-settings.md), `CLINIC_ORIGINALS_ROOT` (see [File storage](storage.md)), and the `RECORDING_SESSION_*` variables if the session endpoints are used.
- **Prerequisites for users:** each author needs a routable author pool, or uploads are refused. **Einreichen** needs an author pool routed to an active staff pool. See [Group management](group-management.md) and [User management](user-management.md).
- **Rights:** live dictation needs `edit` on the dictation. An active site administrator (role **Administration**, checkbox **Administrationsrolle** (administration role), plus a clinic account with status **Aktiv** (active), see [Admin Guide](index.md)) can view and play every clinic dictation and read its corrections, but has no `edit`. `edit` comes only from explicitly granted rights (**Fachrechte**, professional rights, see [Group management](group-management.md)). **Identitätsadministration** (identity administration) gives no access to dictations. Only site administrators change `max_file_size_mb`.
- **See:** the admin page **Verarbeitung** (Processing; group **Betrieb** (Operations) in `/admin`, `/admin#/verarbeitung`; header menu section **Sprache und Verarbeitung** (Speech and processing)) shows the transcription jobs that follow an upload. An active site administrator sees every job and may retry a failed one. Every other account sees only jobs of dictations it may view. The audit log shows `recording.edited` for each submission. Disk usage of `UPLOAD_FOLDER/_sessions/` and the originals store is visible on the server only.
- **Not configurable:** the speech lock timeout (60 seconds), the 50-phrase resume window, browser persistence and the client profile mapping.
