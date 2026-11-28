# Summary, title and recognition instructions

[Deutsch](../de/admin-guide/prompts.md)

The clinic build supplies German defaults for summaries and recording titles. The upstream **Default prompts** administration tab and the account tab **Prompt options** are hidden in clinic mode. There is no replacement clinic prompt editor. Existing specifically configured prompts can still override the shipped defaults.

## What the clinic defaults ask for

The defaults live in `src/clinic/prompts.py`. A database created in clinic mode is seeded with them. An unset admin prompt, or one still equal to the upstream default, resolves to the clinic prompt when clinic mode is on. A different saved admin prompt is preserved.

The summary prompt asks for a structured German summary of a medical dictation from an addiction clinic. It uses these headings, in this order:

- **Anlass / Fragestellung**
- **Suchtanamnese**
- **Aktueller Befund / Psychischer Status**
- **Diagnosen**
- **Medikation / Entzugsbehandlung**
- **Procedere / Nächste Schritte**
- **Offene Punkte / Unklarheiten**

It instructs the model to use only dictated information, omit sections unsupported by the transcript, invent no diagnoses, dosages or recommendations, and preserve numbers, dates and names. Unclear passages should be marked `«[bitte prüfen]»`. These are model instructions, not a code-enforced guarantee of factual correctness.

The title prompt is:

```text
- Titel auf Deutsch
- Höchstens 8 Wörter
- Keine Wendungen wie "Diktat zu" oder "Bericht über"
- Nur das Hauptthema nennen (z. B. Aufnahme, Verlauf, Entzug, Austritt)
```

The output-language instruction defaults to German in clinic mode unless the user's account specifies another output language. A generated title and a summary remain separate from the clinical transcript; neither is permission to rewrite it.

## Saved prompts and precedence

Visible tags with prompts take precedence over personal and administrative defaults. Their prompt texts are joined in tag order. Without such an override, a personal prompt takes precedence over the admin default, which in turn takes precedence over the shipped default. A higher-priority prompt replaces the lower instructions; it does not automatically inherit the clinic safeguards.

Folders are not offered by the clinic fork. On upgraded databases, a legacy folder association can still supply a prompt between the tag and personal levels; for summaries this applies only when generating for the recording owner. Review such inherited data rather than setting up folders as a clinic workflow.

For a revision-bound summary, prompt resolution uses the author for an automatic job and the requesting user for a manual job. Existing personal prompts can therefore produce different instructions for different requesters. The admin keys `admin_default_summary_prompt` and `admin_default_title_prompt` are still read but hidden from the general settings list. The hidden upstream editors are not a supported clinic configuration procedure.

A changed prompt affects later generation only. It does not regenerate existing titles or summaries. Before relying on the shipped template after an upgrade, check for existing tag, personal or administrative overrides.

## Current dictation summaries

The current clinic interface displays **KI-Zusammenfassung** in **Schreibplatz** and **Diktatdetails**. This feature is integrated. It is a read-only summary of the saved text revision, including saved corrections, not a chat assistant or an automatic rewrite of the dictation.

- The worker passes the revision text and resolved instructions to a ready local model. It adds no legacy recording context block containing dates, tags, profile fields or notes.
- Input is limited by `transcript_length_limit`; output by `SUMMARY_MAX_TOKENS`. The account's output language and summary temperature apply.
- Reading requires **Ansehen**; manual generation also requires **Bearbeiten** and a usable saved text of at least 10 characters.
- An eligible first ASR revision can queue an automatic summary. Opening the panel only reads its status. Later corrections make the previous summary stale and do not regenerate it.
- A reply must be usable and complete (`finish_reason=stop`). The worker rejects a result if the text revision, permissions or required local-model conditions changed before storage.

Choose **Textmodell** under [Modellwahl](model-configuration.md). That choice affects these dictation summaries, not title generation. The same page explains automatic-summary conditions, audit records and failure messages.

Compare a synthetic dictation's summary with the spoken source and saved transcript. Pay particular attention to negations, doses, dates and uncertainty markers. Fluent output alone establishes neither accuracy nor clinical suitability.

## Titles and older recording summaries

The separate follow-up job generates the recording title after transcription. For recordings linked to a clinic dictation it skips the stock summary; the clinic summary has its own revision-bound job. Unlinked legacy recordings can still use the older summary path and its recording context. Disabling automatic summarisation does not disable title generation.

The older title/summary path does not apply the revision-summary worker's local-client check. Use an explicit local `TEXT_MODEL_BASE_URL` for the deployment, or leave both text-model URL and key unset. A key alone can create a client with the hosted upstream default. See [model configuration](model-configuration.md).

## Recognition hints

Transcription hints address speech recognition, not summarisation. In clinic mode, batch jobs drop initial prompts and hotwords unless `CLINIC_ASR_PROMPTS_ENABLED` is enabled. When enabled, the initial prompt and hotwords share a 55-word cap: the initial prompt is retained first. The bundled GPU service also needs its own `ASR_PROMPTS_ENABLED` switch. Profile capabilities and service support still matter; a prompt switch does not establish acoustic training or recognition quality.

The upstream contextual speaker-naming step returns no result in clinic mode. Event/calendar extraction is not offered by the clinic fork.

See [local speech recognition](whisperx-setup.md) for vocabulary, profiles and operational checks.

## Limits and temperatures

| Setting | Default | Effect |
| --- | --- | --- |
| `disable_auto_summarization` | `false` | Stops automatic summaries when `true`; title generation remains separate |
| `transcript_length_limit` | `30000` | Maximum input characters; `-1` means no limit |
| `SUMMARY_MAX_TOKENS` | `3000` | Summary output limit |
| `TITLE_MAX_TOKENS` | `5000` | Title output limit |
| `CLINIC_LLM_TIMEOUT_SECONDS` / `CLINIC_LLM_MAX_RETRIES` | `120` / `2` | Per-call clinic limits |
| Summary temperature | `0.5` | Used unless a valid administrative override exists |
| Title temperature | `0.7` | Used unless a valid administrative override exists |

Temperature overrides are read on each request and must be between 0 and 2. Their upstream administration routes remain, but the temperature editor is hidden in clinic mode. Saving a supported setting uses the audit service; prompt contents are not stored as old/new values in the audit log.

[Original Speakr handbook at the archived revision](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/prompts.md)
