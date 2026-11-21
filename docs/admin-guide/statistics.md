# System Statistics
<small>[Deutsch](../de/admin-guide/statistics.md)</small>

The clinic build has two statistics views. They count different things and have different access rules. Everything on this page applies with `CLINIC_MODE` on. With clinic mode off, `/admin` shows the upstream tab strip and the clinic statistics and job routes answer 404.

| View | Where | Who can open it | What it counts | Status |
|---|---|---|---|---|
| **Systemstatistik** (System statistics) | **Verwaltung** (administration) › **System** › **Systemstatistik**, `/admin#/system/statistik` | Accounts with the **Administrationsrolle** (administration role) | Stock recording rows, transcription and language-model usage, one row per account | Available (upstream tab, reused unchanged) |
| **Statistik** in **Aktivität und Audit** (Activity and audit) | **Verwaltung** › **Betrieb** (operations) › **Aktivität und Audit**, `/admin#/aktivitaet` | Active clinic accounts with an aggregate statistics grant | Clinic dictations by status, author, typist and period | Prepared, disabled: no grant can be created, so the page is withheld for every account |

The state of the processing jobs (queue, stuck and failed jobs) is shown on **Verarbeitung** (Processing), see [Processing health](#processing-health-verarbeitung).

## Who may view statistics

- **Site administration** (**Administrationsrolle**, `User.is_admin`): opens `/admin` and therefore **Systemstatistik**. The routes `GET /admin/stats/overview` and `GET /admin/stats` check only this flag. Every other account gets 403 from the clinic route guard before the handler runs.
- **Local identity administration** (**Identitätsadministration**): neither needed nor sufficient. Without the Administrationsrolle there is no access to `/admin`.
- **Pool leads** (**Leitung**): no access to **Systemstatistik**, because `/admin` requires the Administrationsrolle. A lead role gives no statistics grant either.
- **Statistics grant**: the clinic **Statistik** needs an aggregate grant of scope author or author pool. Site administration does not include such a grant; a test asserts that a site administrator without a grant gets 403. No interface, route or command creates a grant (see [Clinic statistics](#clinic-statistics)).
- **Release** (**Freigeben**) and **original-audio export** (**Originalaudio exportieren**): unrelated to statistics. A statistics grant gives no right to view, play, export, release or export original audio. These rights stay with the **Fachrechte** (professional rights), see [User Management](user-management.md).

## Systemstatistik

Status: Available. This is the upstream System Statistics tab, reused unchanged inside the clinic administration. The navigation label **Systemstatistik** is German only. The tab itself follows the interface language: the heading reads "System statistics" in English and "Systemstatistiken" in German. Viewing the tab writes no audit event.

### Period

Choose **7 days**, **30 days** or **90 days** ("7 Tage", "30 Tage", "90 Tage"). The default is 30. The API accepts only 7, 30 or 90 and uses 30 for any other value. The period ends today, and each headline figure is compared with the period of the same length before it. Days are the server's local calendar dates. Usage rows are dated with that local date, while recording rows are matched by their UTC creation time, so the two agree only on a server that runs in UTC. This tab does not use Europe/Zurich. The refresh button reloads the figures. The tab has no other filter and no export.

### What is counted in clinic mode

The tab reads the stock tables `Recording`, `TokenUsage` and `TranscriptionUsage`. It does not read clinic dictations. In clinic mode this has consequences:

- Every clinic dictation has its own anchor recording row in addition to the recording that carries the audio for transcription (the web upload recording, the working copy of an import, or the legacy recording taken over by the backfill script). The recording counts and the state counts therefore count recording rows, not dictations.
- The anchor row also carries the file size of the original, so the storage total adds it on top of the size of the transcribed recording.
- Anchor rows and the working copies of the import channel are created with the archive flag set. The **Archived** line of the Health card therefore includes every clinic dictation's anchor row and every import working copy, plus the recordings archived by retention.
- In **Recordings per day**, anchor rows count as **Upload** (they carry the column default `upload`), and the working copies of the import channel count as **Other**.

Use the clinic **Statistik** for dictation figures once it can be enabled; today it is withheld (see below).

### Overview cards

| Card (English / German) | What it shows |
|---|---|
| **Recordings** / **Aufnahmen** | Recording rows created in the period. |
| **Audio transcribed** / **Transkribiertes Audio** | Audio length recorded by successful transcriptions in the period (see rounding below). |
| **AI requests** / **KI-Anfragen** | Language-model calls in the period. Embedding requests are not counted. |
| **AI cost** / **KI-Kosten** | Language-model, embedding and estimated transcription cost together (see cost below). |
| **Active users** / **Aktive Benutzer** | Owners of at least one recording row created in the period, "of N users". |
| **Total storage** / **Gesamter Speicher** | File sizes of all recording rows whose audio was not removed, across all time. |

For **Recordings**, **Audio transcribed**, **AI requests** and **AI cost**, the line under the figure gives the change against the previous period ("… vs the previous period", "Same as the previous period" or "None in the previous period"). The line stays empty when **AI cost** reads "Local". **Active users** shows "of N users", **Total storage** "N recordings in total" (all recording rows, anchor rows and import working copies included).

**Recordings per day** stacks each day's new recording rows by source: Upload, Recorded, Merged and Other (German: Hochgeladen, Aufgenommen, Zusammengeführt, Sonstige). Below the chart are the number created, the **Median processing time** of the completed rows created in the period and **Failed** (count, total and percentage).

### Minutes and rounding

Transcription minutes come from the audio seconds that the worker records after a successful transcription. The rounding differs by place:

| Where | Rule |
|---|---|
| Card **Audio transcribed** and its comparison | Sum of the period's seconds, divided by 60 and rounded down to whole minutes. Shown as "N min" or "H h M min". |
| Chart **Audio transcribed per day (minutes)** | Per day: seconds divided by 60, rounded to one decimal. |
| **Cost by model**, volume of a transcription row | Rounded to one decimal per connector and model group, then summed per displayed name. The table shows the value rounded to whole minutes. |
| Users table, **Minutes this month** | Seconds of the current calendar month, rounded down to whole minutes. |

### Health

The Health card (**Zustand**) lists the current recording states across all time: Completed, Transcribing, Summarizing, Pending, Failed, Audio deleted and Archived. These are stock recording states of the rows described above, not the clinic workflow states (such as **Offen**, **In Prüfung** or **Freigegeben**: open, in review, released).

The line "Job queue: N waiting, M running" ("Warteschlange: …") counts all queued and running processing jobs; when there are any, "N failed in the last 7 days" follows. **Latest failed recordings** lists the five newest failed recording rows with their id, the owner's username, the date and the first 300 characters of the stored error message. This error text is not redacted. **Verarbeitung** lists failed processing jobs instead (not recording rows), with job type and error class only and without error text; see [Processing health](#processing-health-verarbeitung).

A queue that keeps growing points to a stopped or overloaded background worker. Repeated failures with the same error usually mean that the local transcription service is unreachable or that a model name is wrong.

### Voice embedding compatibility

The card is part of the reused tab but has no clinic function: in clinic mode the upstream speaker profiles are locked (feature `speaker_identification`), and voice embeddings of diarized speakers are not stored. The card appears only when the active transcription connector returns speaker embeddings, which is the case only for the `asr_endpoint` connector with `ASR_RETURN_SPEAKER_EMBEDDINGS=true`. Behind the clinic GPU/CPU fallback, the capabilities of the CPU connector decide.

### Usage and cost

The section shows **Language model tokens per day, by task**, **Audio transcribed per day (minutes)**, **Embedding tokens per day**, **Cost per month** (the last twelve calendar months, stacked by language model, embedding and transcription) and **Cost by model** (model, requests, volume, cost). When the text-model server reports cache hits, a line under the token chart gives the tokens read from and written to the prompt cache in the period ("Prompt cache: N tokens read from the cache, M written, in this period"). The line appears only when more than zero tokens were read from the cache. The token chart shows only operations that actually ran. Operations of features that are locked or removed in clinic mode (Inquire, upstream speaker identification, event extraction) do not occur in normal operation.

Cost figures are estimates, not invoices:

- **Language model and embeddings**: only the cost value that the model server returns in its response. A server that returns none is counted at $0.
- **Transcription**: estimated per connector from a fixed table. `openai_whisper` is counted at $0.006 per minute for every model, also when `TRANSCRIPTION_BASE_URL` points to a local service such as Speaches. `asr_endpoint` and `clinic_stub` are counted at $0.
- With the recommended local Speaches setup (connector `openai_whisper`), **AI cost** therefore shows an estimated transcription cost, although the audio stays on the local service.
- The value "Local" ("Lokal") replaces $0 only when the cost is exactly 0 and `MODELS_ARE_LOCAL=true` is set (default `false`). For embedding rows the embedding configuration decides.
- Usage is recorded under the connector name. A GPU target behind the clinic GPU/CPU fallback is recorded under the name of the CPU connector, so the statistics do not show whether GPU or CPU ran.

### Users

The Users table has one row for every account and opens sorted by **Storage**, largest first. Columns: **User**, **Recordings**, **Storage**, **Last recording**, **Tokens this month**, **Minutes this month** and **Cost this month** (German: Benutzer, Aufnahmen, Speicher, Letzte Aufnahme, Tokens diesen Monat, Minuten diesen Monat, Kosten diesen Monat). Recordings and storage cover all time; storage excludes recordings whose audio was removed. Tokens, minutes and cost cover the current calendar month, whatever period is selected. Click a column heading to sort by it (**User** ascending, the other columns descending); click it again to reverse the order.

Every site administrator sees the usernames and per-account usage of all accounts here.

When an account has a limit, a bar shows how much of it is used. The limits are set in the account editor under **Benutzer und Rollen** (users and roles) as **Speicherlimit (MB)** (storage limit), **Tokenlimit pro Monat** (token limit per month) and **Transkriptionslimit pro Monat (Sekunden)** (transcription limit per month, in seconds); see [User Management](user-management.md).

- Green: under 80% of the limit.
- Yellow: 80% up to under 100%.
- Red: 100% or more. The token and transcription budget checks then refuse further use until the next calendar month.

When any of the three limits reaches 80%, a warning icon appears next to the username: yellow, red from 100%, with the tooltip "At or above 80% of a monthly budget".

## Clinic statistics

Status: Prepared, disabled. Location: section **Statistik** on the page **Aktivität und Audit**.

### Why the page is withheld

- Aggregate grants are kept only in the memory of one server process. They are lost on restart and not shared between processes.
- Only the Python function `grant_aggregate()` creates a grant, and only tests call it. No route, administration page or command creates one. The Fachrechte editor rejects statistics rights.
- The administration probes `GET /api/clinic/v1/statistics/aggregates` when it loads. Without a grant this answers 403, so the page **Aktivität und Audit** is withheld for every account, site administrators included. The administration navigation shows **Für dieses Konto nicht freigegeben: … Aktivität und Audit.** (not released for this account). Opening `/admin#/aktivitaet` directly shows **Die Seite „Aktivität und Audit“ ist für dieses Konto nicht freigegeben.** (the page is not released for this account). In the header menu **Verwaltung & Seiten** (administration and pages), the entry **Aktivität und Audit** is hidden.
- The section **Verlauf eines Diktats** (history of a dictation) on the same page is hidden with it. A dictation's history (**Verlauf**) remains available in the dictation detail and in the work list. For the audit trail, see [Audit Log](audit-log.md).

The rest of this section describes the prepared behavior as the code implements it.

### Access rules

- A signed-in browser session is required (otherwise 401), and the clinic account must be active (otherwise 403).
- The account needs at least one aggregate grant of scope `author` (listed authors) or `author_pool` (listed author pools). Scope `all` is refused. Several grants are combined. Dictations outside the grants are left out before anything is counted. An account without a grant gets 403.
- The grant gives no audio, transcript, export or other dictation action.
- The response holds ids, counts and seconds only. People appear as "Benutzer <id>" (user id), never by name, and no dictation text is shown.

### What is counted

- One item per clinic dictation.
- Status: **Freigegeben** (released: an active release that is not superseded and whose audio, transcript and context revisions are still current), **In Prüfung** (in review: an edit lease, **Bearbeitungssperre**, from **Prüfung beginnen** (start review) that is still running) or **Offen** (open: everything else).
- **Schreibkraft** (typist): the holder of a running edit lease, otherwise the recipient of the latest work-state transfer, otherwise **nicht zugewiesen** (not assigned).
- Audio length: the audio duration stored on the dictation's anchor recording. A missing, negative or non-finite length is not summed; the affected bucket (table row) is marked **unvollständig** (incomplete). A bucket with dictations but no usable length shows **Nicht erhoben** (not collected), never zero. Empty period buckets filled in with zeros show a total of 00:00 and an average of **Nicht erhoben**.
- No minute rounding: the API returns seconds, and the page shows mm:ss or h:mm:ss with fractions of a second dropped. The average divides the known sum by the number of known lengths.

### Filters and grouping

| Field (German UI) | Meaning |
|---|---|
| **Zeitbasis** (time basis) | **Eingang des Diktats** (dictation received, default) or **Fertigstellung (Freigabe)** (completion = release). With completion, dictations without an active release are counted in **Ohne Zeitstempel (nicht gezählt)** (no time stamp, not counted). Processing and review times are not available as a basis. |
| **Von (einschliesslich)** / **Bis (ausschliesslich)** | From (inclusive) and to (exclusive). A date means midnight in Europe/Zurich. The window is half-open [from, to). |
| **Zeitraster** (grain) | **Tag** (day), **Woche** (week, starts on Monday) or **Monat** (month). Required when grouping by period. |
| **Gruppieren nach** (group by) | **Status**, **Diktiert von** (author), **Schreibkraft** (typist), **Zeitraum** (period). Grouping by period needs both dates and a grain. Empty periods are filled with zeros only when the grouping is the period alone. More than 4000 period buckets are refused. |

The result shows **Herkunft der Zahlen** (origin of the figures: **Zeitbasis** (time basis), **Zeitzone** (time zone), **Zeitfenster** (window), **Stand** (as of), **Ohne Zeitstempel (nicht gezählt)**) and a table with the grouping columns plus **Anzahl** (count), **Gesamtdauer** (total length), **Durchschnitt** (average), **Mit bekannter Dauer** (with known length) and **Vollständigkeit** (completeness).

The API is `GET /api/clinic/v1/statistics/aggregates` with the parameters `time_basis` (`created`|`completed`), `from`, `to`, `grain` (`day`|`week`|`month`), `group_by` (comma-separated `status`, `author`, `typist`, `period`) and `expected_version`. Any other parameter gives 400. The route's responses carry `Cache-Control: no-store`, refusals included; only successful responses also carry the header `X-Grant-Version`. Sending that value back as `expected_version` gives 409 when the grants have changed. The route writes nothing.

### Export

- The page has no CSV download. It states **CSV-Export: nur über den autorisierten Serverbefehl, nicht in dieser Ansicht.** (CSV export only through the authorized server command, not in this view).
- The module `src/clinic/statistics_export.py` contains a CSV renderer and a `main()` function with the arguments `--actor-id`, `--role` and `--output` (required) and `--time-basis`, `--start`, `--end`, `--grain`, `--group-by` and `--expected-version`. It writes UTF-8 without BOM with CRLF line ends, atomically, and prefixes formula-like cells with an apostrophe. Exit codes: 0 done, 2 invalid, 3 refused, 4 query or write failed.
- This command is not runnable as delivered. `clinic-statistics-export` is only the parser's program name; there is no entry point. A new process also has no grants, so a run would be refused with exit code 3 (`no_grant`).
- The export would log to the application logger `speakr.clinic.statistics_export` ("statistics export done …" or "statistics export refused … code=…"). It writes no audit row.

### Parity requirements not implemented

The parity requirements for statistics, PAR-ADM-008 (own administration and statistics accounts), PAR-OPS-005 (separately activatable, time-limited collection), PAR-OPS-006 (reports) and PAR-OPS-007 (CSV export by interface or command line), are recorded in the parity matrix (`clinic-spec/planning/parity-matrix.json`) as not started (`not_started`) and not run (`not_run`). Missing in particular: missed deadlines, deletions, processing, acceptance and completion times, and application usage. The clinic statistics count only volume and audio length by status, author, typist and period.

## Processing health (Verarbeitung)

Status: Available. **Verwaltung** › **Betrieb** › **Verarbeitung**, `/admin#/verarbeitung`; also in the header menu **Verwaltung & Seiten** › **Sprache und Verarbeitung** › **Verarbeitung**.

- The page reads `GET /api/clinic/v1/jobs/health` over the newest 5000 processing jobs.
- An active site administrator (Administrationsrolle and an active clinic account) sees all jobs. Other signed-in users see only jobs of dictations they may view.
- Figures: **Abgeschlossen** (completed: completed jobs out of all jobs read, with "Aufträge insgesamt: N", jobs in total), **Fehlgeschlagen** (failed, with the note **Manueller Versuch möglich**, manual retry possible), **Hängende Aufträge** (stuck jobs: running longer than 1800 seconds, note **Ohne Fortschritt**, no progress) and **Angezeigt** (shown, note **Nach Filter**, after filter).
- The list **Fehlgeschlagene Aufträge** (failed jobs) holds the ten newest failures with the columns **Auftrag** (job), **Art** (type), **Zustand** (state) and **Aktion** (action), plus a filter **Zustand**. **Zustand** reads "Fehlgeschlagen:" followed by the error class, or "Unbekannt:" (unknown) followed by the error class for a delivery with an unknown outcome. The page shows no error text. In its place the API returns only "Fehlerdetails ausgeblendet." (error details hidden); for a refused job of type **Sprachtraining** (speech training) it returns "Training nicht verfügbar" (training not available) with the error class `CAPABILITY_UNAVAILABLE`. Failed jobs that were already retried are left out.
- Below the list, **Wiederholungsregeln** (retry rules) state that corrected text revisions must not be replaced silently by a new recognition run, and that an unclear state needs reconciliation before anything is sent again.
- It shows no transcript, no job parameters and no audio path. The engine that actually ran (GPU or CPU) is therefore not shown here either.
- **Erneut versuchen** (retry) is allowed only for an active site administrator and needs a CSRF token, an `Idempotency-Key` and `If-Match` with the job's version. It queues one new job. A delivery with an unknown outcome shows **Abgleich prüfen** (check reconciliation) instead and is refused for retry; release and delivery jobs with their own operation key are not retried. A retry stores an idempotency receipt and writes no audit event.
- No alert or banner is raised for stuck jobs or an old queue.
- Native tray and autostart operation remains an open gap (WG-OPS-004, status open). Neither the page **Verarbeitung** nor the browser replaces it.

## Differences from upstream

- The upstream tab strip is replaced by the clinic administration; **Systemstatistik** sits in the **System** group next to **Systemeinstellungen** and **System-Auditprotokoll**.
- Event extraction is removed from the fork, so no event-extraction tokens appear.
- The transcription price table knows only `openai_whisper` and `asr_endpoint`; the cloud connectors of upstream are removed.
- The voice embedding card has no clinic function (see above).

Upstream Speakr documentation (pinned original, not the clinic behavior): [System Statistics](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/statistics.md)

---

Next: [System Settings](system-settings.md) →
