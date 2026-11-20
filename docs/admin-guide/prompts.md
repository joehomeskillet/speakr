# Summary, title and recognition instructions

[Deutsch](../de/admin-guide/prompts.md)

The clinic edition supplies German defaults for summaries and recording titles. The summary prompt asks for the reason for the dictation, history, current findings, diagnoses, medication, next steps and unresolved points. It directs the model to use the dictated information and identify uncertainty with **[bitte prüfen]**. A generated heading or fluent sentence is not evidence that the speaker said it.

Open a synthetic dictation with a saved transcript and compare its **KI-Zusammenfassung** with the spoken source. If the text changes, check which revision the summary describes before requesting another one. The generated title and the clinical transcript are separate outputs.

The upstream **Standard-Prompts** editor is hidden in clinic mode. An existing, specifically configured higher-priority prompt can still override the shipped default; legacy upstream defaults resolve to the clinic templates. Transcription hints address recognition, not summarisation. They require the local ASR capability and the configured prompt switch. Choose the text model under [Modellwahl](model-configuration.md).

[Original Speakr handbook at the archived revision](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/prompts.md)
