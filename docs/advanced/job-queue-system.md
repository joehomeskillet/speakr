# Processing jobs and retries

[Deutsch](../de/advanced/job-queue-system.md)

The existing database-backed `ProcessingJob` and `FairJobQueue` handle transcription, summaries, indexing and conversion. Opening an audio player is independent of these workers. A dictation’s workflow state, a processing job’s status, and an external delivery acknowledgement are three different records.

1. Open **Verarbeitung** for the permitted recording or job overview. Record the job type and status. A queued transcription has not produced a transcript yet. A completed transcription can still be waiting for a human correction.
2. For a delayed job, check the local model’s readiness and whether the canonical app has its background workers. A web-only replica serves requests; a process started with `SPEAKR_SKIP_BACKGROUND_TASKS=true` for a maintenance command does not run the queue.
3. For a failed job, verify service reachability, the model name and the current input revision. Use the allowed retry action on the existing job when offered. Refresh the overview and confirm a terminal result on that dictation.
4. If delivery to an external system is ambiguous, reconcile the receipt with that system first. An unknown result cannot establish that delivery failed. For text changes, keep the corrected human revision while reviewing the resulting recognition version.

Separate worker limits apply to transcription, summary, indexing and conversion. Measure waiting time and runtime by job type before increasing concurrency. More workers competing for the same GPU or disk may lengthen jobs. Use the [queue implementation](https://github.com/joehomeskillet/speakr/blob/main/src/services/job_queue.py) for the exact mapping and supported environment variables.

## Worker limits

The process reads `JOB_QUEUE_WORKERS` (transcription, default 2), `SUMMARY_QUEUE_WORKERS` (2), `INDEX_QUEUE_WORKERS` (1) and `CONVERT_QUEUE_WORKERS` (2) at startup. Fair scheduling shares turns across users. Stitching a recording occupies the transcription category before recognition. For a capacity test, submit two permitted synthetic recordings from separate accounts, observe the order and durations, then repeat under the same load after changing one limit.

Original reference for upstream Speakr: [archived upstream page](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/advanced/job-queue-system.md).
