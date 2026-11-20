# File storage and recovery

[Deutsch](../de/admin-guide/storage.md)

A clinic dictation has a source receipt, an immutable original and one or more working revisions. The original is written byte for byte under `CLINIC_ORIGINALS_ROOT`, which defaults to `UPLOAD_FOLDER`. Its relative directory is `clinic/originals/v1/`. The original stays there even if a different file-storage backend handles the ASR working copy. An edit creates a new version. Playback, document export and download of the unchanged original use separate permissions. Original download requires an explicit `audio_export` grant even for the author.

## Plan a backup

1. Back up the database, uploads, original store when separate, and the application instance directory together. Protect the deployment settings and signing-key backup separately, with restricted access.
2. Restore the set into an isolated installation. Verify that a synthetic dictation still belongs to its author and that a saved correction survives a reload.
3. Play the working audio, seek and reopen. For a synthetic case with a separately authorized original-download account, compare the original's size and SHA-256 with the source receipt. Playing a derivative alone cannot establish that the original is recoverable.
4. Record the backup date and restore result without clinical content. Review access and retention with the operator responsible for backups.

## Diagnose missing audio

Check the application's volume mounts, free space, file permissions and the original store used by the running image. The startup log summarizes the original sweep (`scanned`, `removed_leftovers`, `incomplete`, `awaiting_row`, `errors`). An incomplete receipt or missing source needs investigation. A waveform cache can be regenerated from good audio. It cannot reconstruct missing audio samples.

The clinic storage design uses persistent local storage. The inherited `FILE_STORAGE_BACKEND` can move ordinary recording objects between local and S3-compatible storage, with `FILE_STORAGE_KEY_PREFIX` and `FILE_STORAGE_STAGING_DIR` controlling key prefix and temporary work. Upstream S3 migration utilities do not migrate clinic originals. A bucket alone is an incomplete backup. Automatic recording retention archives data. Treat any cleanup of files or object keys as a separate, reviewed operation.

[Original Speakr guide](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/storage.md).
