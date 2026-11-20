# Troubleshooting

[Deutsch](de/troubleshooting.md)

Start with a single synthetic dictation. Note the app commit, browser, time, recording identifier and operation. Distinguish a failure to store the sound from recognition, an edit conflict and a delivery failure; they have different recovery paths.

| Symptom | Check | Next action |
|---|---|---|
| Start shows the upstream interface | Check the image tag, source commit and whether the app process receives `CLINIC_MODE=true`. | Build the fork and launch the configured clinic image. An environment-file edit takes effect on a newly created container. |
| Sign-in fails after installation | Verify that the one-time clinic bootstrap targeted the same database and that the account has an active clinic login policy. | Sign in using the chosen login and verify the server’s time and HTTPS. Use identity administration for recovery. A repeated bootstrap cannot reset a password. |
| No microphone or silent recording | Check the browser’s site permission, secure origin, selected input and operating system permission. | Record one spoken sentence and listen back. A non-empty WAV upload isolates the microphone from the server playback path. |
| Playback is silent or stops | Check whether time advances, the output device/volume and whether the source contains actual audio samples. | Compare against a known recording. For DSS/DS2, check decoder availability and source duration without altering the original. |
| Recognition stays queued or fails | Open **Verarbeitung**. Look at queue state, permitted service URL, served model identifier and the failure class. | Restore the local service or correct its model binding. Use the controlled retry on the affected synthetic job. Do not resubmit the dictation to generate a new job. |
| A pool entry is missing or read-only | Reset filters, compare the identifier and confirm the author, pool, current state and edit holder. | Ask the authorized administrator to check assignment and scope. For a conflict, reopen the current revision before continuing. |
| Word highlighting is absent | Verify whether this audio revision has ASR word timings. | Test ordinary playback independently; sentence-level transcription does not supply word alignment. |
| A document or KIS receipt is missing | Compare release ID, document revision and delivery/job status. | Verify the downloaded file or target receipt separately. For an unknown hospital delivery, reconcile with the target before retrying. |

For slow rendering, compare a short and a long synthetic transcript in the same browser. For request timeouts or upload failures, check proxy limits, volume free space and operator-visible processing status. Keep identifiers, timings and sanitized error classes in the incident report. Use [installation](getting-started/installation.md) for settings and [the job queue](advanced/job-queue-system.md) for retry behaviour.

Original reference for upstream Speakr: [archived upstream page](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/troubleshooting.md).
