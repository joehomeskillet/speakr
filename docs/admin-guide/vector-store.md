# Finding dictations without a vector index

[Deutsch](../de/admin-guide/vector-store.md)

The upstream vector store supports cross-recording semantic search through Inquire. The clinic fork does not offer this search interface or an administration action to re-embed the archive. Its **Vector store** tab is hidden, and no clinic page uses the upstream semantic index.

## Find dictations through metadata

Each list applies the signed-in account's visibility rules. Check filters, pool routing and **Ansehen** (view) permission before treating a missing dictation as a search problem.

| View | Where | Search and filters |
| --- | --- | --- |
| **Arbeitsliste** (worklist) | `/#/arbeitsliste` | **Metadatensuche** and **Gespeicherte Filter** search or filter metadata such as title, status, priority, author, pool and assignee. Up to 20 saved filters per user and 20 conditions per filter are supported; regular expressions are refused. |
| **Aufnahmen** (recordings) | `/#/aufnahmen` | Server filters select author, staff pool, recording status and period. The text search runs only over already loaded rows, not the entire database. |
| **Archiv** (archive) | `/#/archiv` | Read-only view for the workflow state **Zu archivieren**, with period, author and staff-pool filters. See [retention](retention.md). |

These lists do not provide semantic search over transcripts. Inside an opened dictation, **Suchen und Ersetzen** operates on that dictation's text.

## Keep upstream indexing off

Keep `ENABLE_INQUIRE_MODE` unset or `false`, as in `clinic-spec/config/upstream.env.example`. The setting controls automatic indexing after processing and the startup backfill. These background paths do not enforce the clinic Inquire feature lock. Enabling indexing does not add a clinic search page.

Do not use the upstream migration/re-embedding tools (`scripts/migrate_existing_recordings.py`, `scripts/migrate_docker.sh`) or the per-recording `process_chunks` action as clinic administration procedures. Manual indexing paths do not all check `ENABLE_INQUIRE_MODE` or the locality of the embedding endpoint. When `EMBEDDING_BASE_URL` is configured, indexed text can be sent there; otherwise an installed local embedding backend can produce vectors. A hidden tab alone is therefore not a guarantee that no index exists.

Leave embedding-service settings out of a clinic deployment that does not need this upstream index. Backups of an upgraded installation must still account for any existing transcript chunks and vectors; see [storage](storage.md) and [privacy](privacy.md).

## Route locks and summaries

The `inquire` feature is locked unless all configured text-model endpoints satisfy the clinic local-model rule. Even with local endpoints, a request without a recording target, including the Ask page, is refused as `403 clinic_feature_unsupported`. The remaining per-recording handlers have separate permission checks. This is not a supported clinic search workflow; see [Ambrose](ambrose.md).

**KI-Zusammenfassung** uses the saved text revision of one authorised dictation. It requires a local [text model](model-configuration.md), not a cross-recording vector index. Do not enable Inquire to obtain summaries.

[Original Speakr handbook at the archived revision](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/vector-store.md)
