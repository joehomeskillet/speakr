# Vector Store
<small>[Deutsch](../de/admin-guide/vector-store.md)</small>

Status in the clinic build: **Upstream only**.

In upstream Speakr, the admin tab **Vector store** manages Inquire Mode. Inquire splits each transcript into chunks, computes embeddings for them and stores them for semantic search on the Ask page. The clinic build does not offer this function. With `CLINIC_MODE` on, the tab is not rendered, the clinic route policy locks the Inquire routes except the per-recording index route, and no clinic page uses semantic search.

Transcripts are not chunked or embedded automatically as long as `ENABLE_INQUIRE_MODE` stays off. The manual upstream tools and routes named in the section "Keep Inquire indexing off" do not check this switch, so nobody may use them. This rests on configuration, not on a code check. To find dictations, the clinic build uses structured lists with metadata filters (section "Finding dictations in the clinic build").

## Status overview

| Component | Status | Behavior with `CLINIC_MODE` on |
|---|---|---|
| Admin tab **Vector store** (German UI: **Vektorspeicher**) | Upstream only | Not rendered. The clinic **Verwaltung** (administration) shell at `/admin` has no page for it. |
| Ask page `/inquire` (alias `/ask`) and the header button **Ask Ambrose** | Upstream only | The clinic header has no entry point. The route is refused, also with a local text model. |
| Inquire API: `/api/inquire/sessions`, `/api/inquire/search`, `/api/inquire/chat`, `/api/inquire/download`, `/api/inquire/available_filters`, `/api/v1/inquire` | Upstream only | Disabled by the clinic route policy (feature `inquire`). |
| Admin routes `/admin/inquire/status` and `/admin/inquire/process-recordings` (upstream button **Re-embed all recordings**) | Upstream only | Disabled by the clinic route policy (feature `inquire`). See the warning below. |
| Admin route `/api/admin/migrate_recordings` | Upstream only | Disabled (feature `generic_admin_content_access`, which the clinic defaults do not enable). |
| Per-recording index route `POST /api/recording/<recording_id>/process_chunks` | Upstream only | Not locked under `inquire`. The route policy treats the call as an edit of the named recording, and the handler accepts only the owner of the recording (for a clinic dictation, only with a valid edit lease). It chunks and embeds that recording without checking `ENABLE_INQUIRE_MODE`, clinic mode or whether `EMBEDDING_BASE_URL` is local. The call is not audited. No clinic page calls it. |
| Automatic chunking and embedding of transcripts (job type `index_chunks`, start-up backfill) | Upstream only | Switched off by `ENABLE_INQUIRE_MODE=false` (default). With `true` it runs without a clinic check. Keep it off. The manual paths `scripts/migrate_existing_recordings.py`, `/admin/inquire/process-recordings` and `POST /api/recording/<recording_id>/process_chunks` chunk transcripts whatever this switch says. |
| Search over transcript text in lists | Upstream only | No clinic page searches transcript text. The upstream endpoints `GET /api/recordings?q=` and `GET /api/v1/search` stay reachable for signed-in accounts. They match the transcript text of the recordings the caller can access (`/api/recordings`) or owns (`/api/v1/search`). No clinic page calls them. |

## How clinic mode blocks Inquire

Clinic mode is on when `CLINIC_MODE` is `1`, `true`, `yes` or `on`. Three independent layers then apply:

1. **Admin page.** `/admin` renders the clinic **Verwaltung** shell instead of the upstream tab strip. The vector store tab and its panel are not rendered, and the page's Inquire state is forced off. A clinic test in `tests/clinic/` checks that the tab markup is absent in clinic mode and present with clinic mode off.
2. **Route guard.** The clinic route policy locks the Ask page, the Inquire API and the two Inquire admin routes under the feature `inquire` (`403 clinic_feature_disabled`). The per-recording index route is an exception, see "Keep Inquire indexing off". The lock lifts only when every text-model endpoint is local, and even then a request that names no recording, such as the Ask page or the admin status route, gets `403 clinic_feature_unsupported`. A request that names recordings passes only when the caller may edit every one of them under the central policy. The lock rules are in [Ambrose Settings](ambrose.md); what counts as local is in [Model Configuration](model-configuration.md).
3. **Upstream switch.** Unless `ENABLE_INQUIRE_MODE=true` (default `false`), the Ask page redirects to the recordings list with a warning, and the Inquire API endpoints answer `403` (`inquire_disabled`, or `feature_disabled` for `/api/v1/inquire`). The admin routes `/admin/inquire/status` and `/admin/inquire/process-recordings` do not check this switch.

## Keep Inquire indexing off

!!! warning "Inquire indexing has no clinic check"
    Keep `ENABLE_INQUIRE_MODE` unset or set to `false`. The clinic template [`clinic-spec/config/upstream.env.example`](https://github.com/joehomeskillet/speakr/blob/main/clinic-spec/config/upstream.env.example) (clinic fork) sets `ENABLE_INQUIRE_MODE=false`. With `true`, the upstream indexing also runs in clinic mode:

    - When the processing of a recording completes, a job of type `index_chunks` is queued for it. This includes the recordings behind clinic dictations, which also carry the transcript text.
    - At every start, the migration step "inquire mode chunk backfill" chunks and embeds up to 10 completed recordings that have no chunks yet.
    - After a transcript edit or a speaker assignment through the upstream recording routes, an `index_chunks` job is queued as well. Renaming or merging speaker profiles does the same, but those routes are locked in clinic mode under the feature `speaker_identification`.

    None of these paths checks clinic mode, the `inquire` feature lock or whether the embedding endpoint is local. The chunks are stored as text in the table `transcript_chunk`. Vectors are produced only when an embedding backend is available. With `EMBEDDING_BASE_URL` set, the chunk text goes to that endpoint without a locality check. Otherwise a local sentence-transformers model (`EMBEDDING_MODEL`, default `all-MiniLM-L6-v2`) is used if the package is installed: the default image build (`LIGHTWEIGHT=0`) installs it from `requirements-embeddings.lock`, while `requirements.lock` (`LIGHTWEIGHT=1`) does not contain it, and the chunks are then stored without vectors. A non-local `EMBEDDING_BASE_URL` locks chat, Inquire and generative summaries in clinic mode, but it does not stop this indexing.

Checks for administrators:

- At start-up the application prints `Inquire Mode: Disabled (set ENABLE_INQUIRE_MODE=true to enable)`. Any other "Inquire Mode" line means the switch is on.
- Do not run `scripts/migrate_existing_recordings.py` or `scripts/migrate_docker.sh`, which calls it. The script chunks and embeds completed recordings and checks neither `ENABLE_INQUIRE_MODE` nor clinic mode.
- Do not send requests to `/admin/inquire/process-recordings`. With a fully local text model, a request from an account with the **Administrationsrolle** (administration role, site administration) can pass the guard when it names recordings that account may edit. The handler then ignores the named IDs, selects completed recordings by its own rules and chunks them, whether `ENABLE_INQUIRE_MODE` is on or not.
- Do not send requests to `POST /api/recording/<recording_id>/process_chunks`. Every account may call it for its own recordings, also with `ENABLE_INQUIRE_MODE=false`; for a clinic dictation the guard requires a valid edit lease. The call is not audited. With `EMBEDDING_BASE_URL` set, the chunk text goes to that endpoint, whether it is local or not.
- Leave `EMBEDDING_MODEL` and `EMBEDDING_BASE_URL` out of the clinic configuration. They configure only the upstream Inquire embeddings. Outside of them, only the clinic local-model check and the start-up warning about services reached over plain http read `EMBEDDING_BASE_URL`. Without it, no chunk text goes to an embedding endpoint. The default image build (`LIGHTWEIGHT=0`) still contains sentence-transformers, so an indexing path that runs anyway stores local vectors.

When `/admin/inquire/process-recordings` or `/api/admin/migrate_recordings` finds at least one recording to process, it writes `admin.inquire.processed` at the end of the run (details `processed`, `failed`, `total`, `force`). A call that finds nothing to process, or that aborts with an error before the end of the run, writes no event. `POST /api/recording/<recording_id>/process_chunks` is not audited. The clinic UI offers no way to trigger these routes. See [Audit Log](audit-log.md).

## Finding dictations in the clinic build

The clinic build finds dictations through lists with structured filters on metadata. Each list shows only the dictations the signed-in account may see. The visibility rules are described in [User Management](user-management.md) and [Group Management](group-management.md).

| View | Location | Filters | Search |
|---|---|---|---|
| **Arbeitsliste** (worklist) | `/#/arbeitsliste` | **Gespeicherte Filter** (saved filters): at most 20 per user, at most 20 conditions each, on status, priority, author, pool, title, created, deadline, assignee and audio length. Regular expressions are not allowed. The filters are stored per user in the database. | **Metadatensuche** (metadata search), at most 200 characters, evaluated on the server. Every word must occur in the title, notes or recipient of a dictation. |
| **Aufnahmen** (recordings) | `/#/aufnahmen` | Evaluated on the server: **Diktiert von** (dictated by), **Schreibkräfte-Pool** (staff pool), **Status der Aufnahme** (recording status) and **Zeitraum der Aufnahme** (recording period). | **Aufnahmen durchsuchen** (search recordings), placeholder "Person, Pool oder Fall-ID suchen …". It runs in the browser over the rows already loaded and matches author, pool, Fall-ID or dictation ID. |
| **Archiv** (archive) | `/#/archiv` | Only dictations in the workflow state **Zu archivieren** (to be archived), filtered by **Von** (from), **Bis** (to), **Diktiert von** and **Schreibkräfte-Pool**. | None. The view is read-only; see [Retention](retention.md). |

None of these searches reads transcript text. Inside an opened dictation, the correction editor offers **Suchen und Ersetzen** (find and replace) within the current text only.

## Upstream reference

Upstream Speakr documentation (pinned upstream version, not applicable to the clinic build): [Vector Store Management](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/vector-store.md). It describes the status cards, the embedding model, **Re-embed all** and embedding token tracking. None of these apply with `CLINIC_MODE` on.

## Related pages

- [Ambrose Settings](ambrose.md): status of the upstream assistant and chat in the clinic build.
- [Model Configuration](model-configuration.md): local text-model endpoints and `CLINIC_LOCAL_MODEL_HOSTS`.
- [Audit Log](audit-log.md): event catalog, including `admin.inquire.processed`.
- [Retention](retention.md): archival and the **Archiv** view.

---

Return to [Admin Guide](index.md)
