# Desktop parity and acceptance

[Deutsch](de/roadmap.md)

Use the [clinic requirement register](https://github.com/joehomeskillet/speakr/blob/main/clinic-spec/planning/parity-matrix.json) when evaluating a move from DNS-Comfort. It contains 101 required behaviours. Its original evidence fields record no direct reference-versus-target comparison. A count of requirements is not a count of delivered features.

## What a team can pilot now

With synthetic material, record or upload a short dictation, run local batch transcription, hand it to a scoped writing pool, check playback and save a correction. After an audio cut, revisit the text and audio revision identifiers and listen across the cut. Check assignment, review and release with separate accounts. A successful exchange with a mock hospital system establishes the application contract only.

## Open software paths

| Area | Missing connection or evidence | Acceptance exercise |
|---|---|---|
| Live dictation | The session and phrase store exist. The live view still lacks the full microphone-to-stream path for partial text during capture. | Speak two sentences, see partial text before Stop, interrupt the connection, and count exactly two final phrases after reconnect. |
| Voice activation | The server has a threshold detector. The normal browser recorder does not apply its pre-roll and post-roll controls. | Record silence, a quiet first syllable and speech. Listen to both boundaries in the saved audio. |
| Offline recovery | Managed-profile encryption and storage components exist. The regular offline view lacks its storage/reopen integration. The normal bundle path cannot round-trip audio. | Close and reopen a managed test browser, unlock, recheck permissions and resume once. Independently reopen an exported `.sdict` and compare audio, text and anchors. |
| Voice resources | Local read-aloud, effective negative-word suppression and production training from approved corrections lack a complete working engine path. | Hear the selected text, compare the same test audio with and without suppression, or validate a resulting model checkpoint and its rollback. |
| Released documents | The standard page renders TXT/DOCX and records a job receipt. Managed DOCX templates/bookmarks and a complete file hand-off from that page are still open. RTF support elsewhere does not establish this workflow. | Select a versioned template, obtain the actual file, open it independently and verify text, bookmarks and formatting. |

## External dependencies

The [web parity register](https://github.com/joehomeskillet/speakr/blob/main/clinic-spec/planning/web-parity-gaps.json) retains 13 conflicts, including global hotkeys, typing into another application, desktop Office automation and recorder configuration or firmware. The Windows component imports files from approved sources. Confirm real recorder transport, pedal behaviour, Entra account switching and hospital-system acknowledgement on the intended equipment. Evaluate recognition errors on an approved representative corpus, with human review of negations, numbers and medication names.

Original reference for upstream Speakr: [archived upstream page](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/roadmap.md).
