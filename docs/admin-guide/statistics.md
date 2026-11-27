# System statistics

[Deutsch](../de/admin-guide/statistics.md)

The clinic build has separate system-usage and clinical-aggregate views. They count different objects and use different access rules.

| View | Location | Access and availability |
| --- | --- | --- |
| **Systemstatistik** | **Verwaltung → System**, `/admin#/system/statistik` | Available to site administrators |
| **Aktivität und Audit → Statistik** | `/admin#/aktivitaet` | Prepared clinical aggregates; the application has no operational grant-management path, so the page is withheld without a statistics grant |
| **Verarbeitung** | **Verwaltung → Betrieb**, `/admin#/verarbeitung` | Processing-job health and controlled retry |

Identity-administration and pool-lead roles alone do not open system statistics. A site-administration role alone does not grant access to the separate clinical aggregates.

## Period and overview

In **Systemstatistik**, choose 7, 30 or 90 days; the default is 30. The selected period ends today and is compared with the preceding period of equal length. The implementation uses the server's current calendar date (`date.today()`), while recording timestamps use UTC. Do not assume that this view applies Europe/Zurich boundaries. A recording created today and transcribed tomorrow contributes to different dates in recording and usage totals.

The overview shows recording count, transcribed audio, model requests, estimated cost, active users and storage. The activity chart separates uploads, browser recordings, merged recordings and other sources. Health shows recording states, waiting and running jobs and recent failures. Compare a failure percentage with the number of rows before interpreting a spike.

**These are recording rows, not clinic dictation counts.** A clinic dictation has an anchor recording as well as a source or working recording. The system view reads `Recording`, `TokenUsage` and `TranscriptionUsage`; anchor rows can therefore increase recording counts and storage totals. Archived anchor and import-working rows also affect the archive count. The storage figure sums retained recording-media sizes; it is not a measurement of every byte in the originals store, caches or backups.

Recent failed recordings can show usernames and raw stored error text. Limit distribution of screenshots or exported captures. The processing view described below redacts job error details instead.

## Usage, costs and limits

The model breakdown separates text-model calls, embeddings and transcription. Transcription duration is rounded to a tenth of a minute in daily and per-model calculations; the headline and monthly account totals use whole minutes. These rounded display values are not the budget's underlying seconds.

Cost figures are estimates, not invoices:

- Text-model and embedding costs depend on what the model server reports; no reported cost is counted as zero.
- The transcription price table assigns `openai_whisper` $0.006 per minute, including when it calls a local service such as Speaches. `asr_endpoint` and the synthetic `clinic_stub` do not receive that charge estimate.
- `MODELS_ARE_LOCAL=true` changes a zero-cost display to **Local**/**Lokal**; it does not prove endpoint locality or make a nonzero estimate disappear.
- Usage behind GPU/CPU fallback is attributed to the CPU connector name. The totals do not identify which execution device ran.
- Electricity, hardware and staff costs are outside these figures.

The user table shows every account's recording and storage totals plus current-calendar-month token use, transcription time and estimated cost. These monthly columns remain monthly even when the chart covers 90 days. Budget bars warn from 80% and reach the limit at 100%. Set the limits under [User management](user-management.md): storage in MB, tokens per month and transcription **in seconds** per month. Check those limits before diagnosing a refusal as insufficient system capacity.

Event/calendar extraction and cloud ASR connectors are not offered in the clinic fork. Historical usage rows or upstream labels do not re-enable them. The remaining upstream voice-embedding diagnostic card is not evidence that clinic speaker enrollment is available or accepted.

## Clinical aggregates: prepared, unavailable through ordinary administration

The clinical statistics code counts clinic dictations within explicit author or author-pool grants. It does not derive these grants from site administration, identity administration, pool leadership or ordinary **Fachrechte**. A statistics grant gives no audio, transcript, export or release permission.

The grant store is process-local memory. Only tests call `grant_aggregate()`; the application has no page, endpoint or operator command that provisions these grants. They would not survive a restart or automatically reach another process. Without a grant, the statistics probe returns `403`, and the administration withholds **Aktivität und Audit**. Do not present this as an enabled reporting service.

The prepared aggregation behavior is:

- One item per clinic dictation, grouped by status, author, typist or period.
- Status distinguishes a current release, an active review lease and other open dictations; it is not the full workflow-state catalogue.
- Time basis is receipt or completion by release. A date bound is midnight in **Europe/Zurich**; the end is exclusive. Period grouping supports days, Monday-based weeks and months.
- Duration uses recorded seconds. Missing or invalid durations make a result incomplete. **Nicht erhoben** means unavailable, not zero.
- Results contain IDs, counts and durations, not transcript text or audio.

The aggregate route is `GET /api/clinic/v1/statistics/aggregates`. It is a clinic session route, not access through the removed general API v1. The UI has no CSV download. Prepared export code is not an operational export service without the missing authorization wiring. Dictation history remains available from dictation views even when this aggregate page is withheld.

## Processing health and retry

Open **Verarbeitung** to inspect processing jobs. Its health endpoint reads at most the newest 5000 jobs. An active site administrator sees all jobs; other authenticated users see only jobs of dictations they may view. The response exposes identifiers, types, states, timestamps and redacted error classes, not transcripts, audio paths, request parameters or raw job errors.

The page shows completed, failed and stuck jobs and recent failures. A running job older than 1800 seconds counts as stuck. Investigate the configured local service and worker state; a growing queue alone does not identify the cause.

**Erneut versuchen** requires an active site administrator and a versioned, CSRF-protected, idempotent request. It queues a new job. A legacy delivery job with an unknown outcome shows **Abgleich prüfen** and cannot be retried here. Delivery to the KIS is not offered in this fork (the KIS integration is read-only), so such rows come only from historical or simulated data. Release jobs carrying their own operation key also require their dedicated recovery path.

The page does not provide native tray/autostart operation; WG-OPS-004 remains open. See [Local speech recognition](whisperx-setup.md) for local service checks and [Audit log](audit-log.md) for audit coverage.
