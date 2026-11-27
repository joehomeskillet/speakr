# What replaces Ambrose in the clinic workflow?

[Deutsch](../de/admin-guide/ambrose.md)

The upstream Ambrose assistant offers recording chat and a separate Ask view. The clinic workspace has no Ambrose button, chat panel or cross-recording Ask screen. Neither an assistant name nor an upstream inquiry setting adds these controls. The administration tabs **Default prompts** and **Vector store**, and the account tab **Prompt options**, are hidden in clinic mode.

## Working with one dictation

Open the dictation from **Aufnahmen** (recordings) or **Arbeitsliste** (worklist). Listen to the relevant passage and compare it with the saved transcript. For a concise account, open **KI-Zusammenfassung** in **Schreibplatz** (typing workspace) or **Diktatdetails** (dictation details).

This summary panel is part of the current clinic build. It accepts no questions and does not change the dictation text. Its summary belongs to a saved text revision, not to a search across other recordings. After a correction, the earlier summary is marked as stale. Unsaved edits are not included.

- Reading requires **Ansehen** (view) on the dictation.
- **Zusammenfassung erstellen** or **Zusammenfassung neu erstellen** requires **Bearbeiten** (edit), a usable saved text of at least 10 characters and a configured local text model.
- The first eligible speech-recognition revision can queue an automatic summary when both the system and the author's preference permit it. Later corrections do not regenerate it automatically.
- Opening the panel reads its status; it does not request a new summary. A stored summary remains readable when the model service is unavailable.

See [model selection and summaries](model-configuration.md) for the local-service checks, permissions and error states, and [prompts](prompts.md) for the generated text's instructions. Compare generated content with the source; a fluent summary is not evidence of clinical correctness.

## Remaining upstream code

The presence of chat and Inquire handlers does not mean the clinic interface offers an assistant. In clinic mode their feature locks depend on a local `TEXT_MODEL_BASE_URL` and on every configured chat, translation and embedding endpoint also being local. Even when this lock lifts, the guard refuses an Inquire request without a recording target as `403 clinic_feature_unsupported`. Per-recording calls remain subject to content permissions and, for edits of clinic dictations, the edit lease.

Keep `ENABLE_INQUIRE_MODE=false`. Upstream indexing paths do not all enforce the clinic local-model rule; enabling them can process transcripts without adding a clinic search interface. See [finding dictations without a vector index](vector-store.md).

Incognito recordings are not offered by the clinic fork.

[Original Speakr handbook at the archived revision](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/ambrose.md)
