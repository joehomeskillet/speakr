# Finding dictations without a vector index

[Deutsch](../de/admin-guide/vector-store.md)

The upstream vector store serves cross-recording semantic search. In the clinic edition its admin tab is hidden and the administration routes for Inquire are disabled. There is no clinic action to re-embed the archive. Keep the upstream inquiry indexing switch disabled for the clinic deployment.

To locate work, open the **Arbeitsliste** and apply the available pool, assignee or workflow filters. An author can also open their **Aufnahmen** list and search by the identifiers visible to that account. If a dictation is missing, check the filter, pool routing and reading permission before treating it as a search problem.

The summary of one authorised dictation uses its own text revision. It requires the local [text model](model-configuration.md), not a cross-recording vector index. The [privacy guide](privacy.md) describes the remaining data boundaries.

[Original Speakr handbook at the archived revision](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/vector-store.md)
