# Choose local transcription and summary models

[Deutsch](../de/admin-guide/model-configuration.md)

Sign in with an active site administrator account and open **Verwaltung → System → Modellwahl**. The page separates GPU transcription, CPU transcription and the summary text model. For each, read the saved selection, the effective model and its source. An inactive path may have a saved model; this page does not run a health test.

1. Choose a known model from the local service catalogue. If the service reports no complete catalogue, enter a model identifier supported by your deployment. No service address or credential belongs in these fields.
2. Save a changed selection, reopen the page and verify the effective model. A cleared field inherits its default. CPU transcription can inherit the existing upload model catalogue default. The GPU uses its deployment or local service model; the summary inherits the configured local text model.
3. If another administrator saved first, reload the current values and reconcile the change. When a verified catalogue rejects an identifier, select one the service reports.

For automatic summaries the system setting must permit summarisation and the author’s own preference must enable it. Successful first transcription of an eligible dictation can then queue a revision-bound summary; opening **KI-Zusammenfassung** only reads its status. The transcript is separate. See [prompts](prompts.md) and [local ASR](whisperx-setup.md).

[Original Speakr handbook at the archived revision](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/model-configuration.md)
