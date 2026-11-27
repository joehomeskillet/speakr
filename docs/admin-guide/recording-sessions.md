# Recording sessions and interrupted uploads

[Deutsch](../de/admin-guide/recording-sessions.md)

The clinic recorder captures audio in browser memory and sends the finished take in one upload. Server-side upload sessions, live dictation and the import agent are separate paths. Their settings do not turn the clinic recorder into a crash-recovery service.

## Clinic recorder: Neue Aufnahme

Open **Neue Aufnahme** (New recording), at `/#/neu`. **Diktiert von** (Dictated by) shows the signed-in person and cannot be changed here. Under **Quelle** (Source), choose **Aufnehmen** (Record) or **Datei hochladen** (Upload file).

For microphone recording, enter a title before starting. The controls allow starting, pausing, continuing, stopping and listening back. **Entwurf sichern** (Save draft) sends the finished take to the server. A selected audio file uses the same upload path. The page accepts a non-empty audio file, including supported dictation files with `.ds2` or `.dss` extensions.

The page sends one multipart `POST /upload`; it does not stream recording chunks or split the file into upload slices. `ENABLE_SERVER_RECORDING_CHUNKS` does not change this. Each author needs a routable author pool. Without one, the server refuses the upload as a clinic dictation with `clinic_no_author_pool`. See [Pools](group-management.md).

Wait for **Gespeichert. Noch nicht an den Schreibdienst gesendet.** and the dictation ID before closing the page. Saving a draft and submitting it are separate actions. **Einreichen** (Submit) needs the author's edit right, a draft with a valid audio revision and routing to an active staff pool. After submission, use **Zur Arbeitsliste** or **Öffnen** to check the result.

## Missing receipts and recovery

- If a save fails, keep the tab open. **Erneut sichern** or **Erneut hochladen** retries the in-memory take or selected file.
- Before retrying after an uncertain response, check the author's list. An upload can have reached the server even though the browser did not receive its receipt.
- For a selected file, keep the source file and retry from it after connectivity returns.
- Leaving the recording page discards an unsent take. Reloading, closing the tab or a browser crash loses the in-memory copy. An upload already in flight is not canceled when the page is left.

The clinic recorder does not intentionally persist audio or its title in IndexedDB, `localStorage` or `sessionStorage`. The upstream failed-upload and crash-recovery stores are also denied by the clinic persistence gate. This is not a guarantee that a browser or device leaves no residual data; see [Privacy](privacy.md).

## Limits and proxy settings

| Setting | Default | Applies to |
|---|---|---|
| `RECORDING_MAX_HOURS` | `8` | Recording duration ceiling |
| `RECORDING_CHUNK_SECONDS` | `5`, clamped to 1–60 | Browser capture timeslice; clinic slices stay in memory |
| `max_file_size_mb` | `250` at first start | Audio file limit when chunking does not bypass the per-file check |
| `max_audio_only_video_size_mb` | 4 × `max_file_size_mb` at first start | Video input limit; the application request ceiling uses the larger limit |

The browser recorder also has a size cap of 200 MiB and warns near 80% of that cap. Application storage quotas, the request ceiling and reverse-proxy limits are separate checks. Raising a storage quota does not raise an upload limit.

Because the clinic recorder sends the whole take in one request, size the proxy body limit for the whole expected file and allow enough time for ingestion. A 16 MiB chunk limit on another upload path does not protect this request from a smaller proxy limit. See [System settings](system-settings.md) for upload and video rules.

## Server-side upload sessions

The inherited `/upload/session` endpoints remain available to signed-in users and restrict each session to its owner. They support recorder streams and sliced large-file uploads. The normal clinic **Neue Aufnahme** page uses neither transport.

| Variable | Default | Purpose |
|---|---|---|
| `ENABLE_SERVER_RECORDING_CHUNKS` | `false` | Stream chunks from the upstream recorder, not the clinic recorder |
| `RECORDING_SESSION_MAX_CHUNK_BYTES` | `16777216` (16 MiB) | Maximum chunk or slice |
| `RECORDING_SESSION_MAX_BYTES_PER_USER` | `5368709120` (5 GiB) | Soft cap on unfinished sessions per user |
| `RECORDING_SESSION_TTL_HOURS` | `24` | Inactivity cutoff for cleanup |
| `RECORDING_SESSION_CLEANUP_INTERVAL_SECONDS` | `3600` | Cleanup interval; zero or less disables the sweep |
| `RECORDING_SESSION_COMMIT_BATCH_SIZE` | `1` | Keep this default for chunk bookkeeping |

Chunks live under `UPLOAD_FOLDER/_sessions/`. Finalizing a sliced upload reassembles and ingests the file; allow disk headroom for the joined copy and a sufficiently long proxy timeout. Finalizing a recorder session creates a recording and queues stitching. That recorder-session result does not automatically become a clinic dictation; use the checked [legacy migration procedure](migration-guide.md) where applicable.

The cleanup sweep can finalize abandoned recorder sessions with chunks, requeue stuck stitching, or expire incomplete uploads. Expired temporary chunks and orphaned session directories can be removed. This temporary-upload housekeeping is separate from [recording retention](retention.md), which archives data.

## Live dictation

**Live-Diktat**, at `/#/live`, selects an existing dictation. Session start, pause, continue, stop and reconnect controls are present, as are server routes for storing final phrases and resuming from receipts. Live actions need the dictation's edit right. A live speech lock expires after 60 seconds without a renewing action; reconnect checks whether the caller still owns the session.

The shipped page does not connect audio capture or a recognition feed to those phrase routes. The local live-ASR adapter exists, but setting `CLINIC_LIVE_ASR_BASE_URL` alone does not wire it into the page. Do not present this page as a working live-recognition pipeline. Switching between live drafts returns `CAPABILITY_UNAVAILABLE`.

Dictation into foreign Windows fields, foreign-window speech locks and native focus binding remain open gaps WG-LIVE-002, WG-LIVE-006 and WG-LIVE-007.

## Offline use and imports

The server chooses the client profile; client-supplied headers do not select it. Without a trusted mapping, requests use the configured default, `managed_web_online`. Managed offline storage is not enabled: its capability route answers that offline storage is not approved because no trusted browser and grant provider are wired. The **Offline und Wiederherstellung** page therefore offers no local recovery.

**Dateien und Geräteimport** and the administration's **Import** page concern approved device and filesystem sources. `apps/windows-import` is the only local agent; it imports files and does not record or play audio or automate another application. Registration and source-provisioning service code are not a complete deployment procedure. Simulator output does not prove Philips-device compatibility. Source cleanup requires the separate receipt and policy rules; it is not browser recording recovery.

For completed uploads, **Verwaltung → Betrieb → Verarbeitung** shows subsequent processing jobs. Check the actual dictation, playback and transcript when verifying success; a queue status alone does not prove recognition quality. See [Local speech recognition](whisperx-setup.md).
