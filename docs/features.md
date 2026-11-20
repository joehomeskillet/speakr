# What the clinic workflow supports

[Deutsch](de/features.md)

| Task | How to use it | What to check |
|---|---|---|
| Record or upload | Open **Neue Aufnahme**, choose a microphone or audio file and save the draft. | The server acknowledges the stored audio. Submitting the draft is a separate step. |
| Recognize speech locally | Use the configured local ASR profile and watch the processing status. | Play a known sentence and compare its words with the saved transcript. Word-level seeking depends on timing data returned by the engine. |
| Assign and correct | Filter **Arbeitsliste** by pool or state, open the entry and take the available editing lease. | A second authorized reader may listen while somebody holds the edit lease. Reopen to verify your saved correction. |
| Edit audio | At the correction workspace open audio editing where permitted. Keep the original, save a new version and listen to the changed part. | Check which audio and text versions the current view uses before requesting recognition again. |
| Review and release | Follow the review policy. A named reviewer may need to start a review and accept before the final release. | A release binds specific text and audio versions. Original-audio download requires its own permission. |
| Prepare documents | For a released revision, the output page offers TXT and DOCX jobs. | The job can return a filename and digest. Verify receipt of actual bytes in the intended delivery channel before recording success. |

## Where desktop expectations differ

Batch recognition after capture is available. Continuous dictation from a live browser microphone with partial text, reliable reopening of an offline audio draft, and applying a centrally managed Word template and bookmarks to the released export still need end-to-end integration. The optional Windows agent imports approved recordings. Import-author assignment in Speakr does not program the author list on a Philips recorder.

See the [parity and acceptance roadmap](roadmap.md) for further limitations, including real hospital-system delivery. For screenshots, open the [gallery](screenshots.md).

**Video files in shared ingestion.** The inherited `VIDEO_PASSTHROUGH_ASR` setting defaults to false. When enabled, shared video ingestion sends the original video container to the selected ASR service. Standard audio-only backends may reject it. The clinic dictation picker accepts audio. Verify the intended private backend and input format before enabling this setting.

## Related upstream features

Clinic collaboration uses scoped pools, assignments and versioned review. Use [user management](admin-guide/user-management.md) for account roles and [group management](admin-guide/group-management.md) for grouping; verify the actual target account can see the intended dictation. Upstream folders, group sharing, public links, API tokens, Ambrose and cross-recording search have different data exposure and policy controls. Their individual [user-guide chapters](user-guide/index.md) explain when the clinic exposes them. Check [model configuration](admin-guide/model-configuration.md) before enabling a generative feature, and the [API reference](user-guide/api-reference.md) before authorizing an integration.

Original reference for upstream Speakr: [archived upstream page](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/features.md).
