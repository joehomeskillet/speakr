# Migrate an existing Speakr installation

[Deutsch](../de/admin-guide/migration-guide.md)

Rehearse on a restored copy with synthetic material before scheduling an upgrade. Record the database engine, application revision, volume mapping, owner counts and current recording counts. Back up the database (a consistent SQLite copy or a PostgreSQL dump), uploads, separate `CLINIC_ORIGINALS_ROOT`, instance directory, signing-key file if configured, and protected deployment settings. Verify restoration before making changes.

1. Build the clinic fork from its public `main` branch as described in [Installation](../getting-started/installation.md). Set `CLINIC_MODE=1`, point transcription and optional text generation to approved local services and keep existing storage and signing-key mappings.
2. Start the regular application against the restored database. Its normal initialization creates the clinic tables. A `CLINIC_WEB_ONLY=1` instance assumes provisioned storage and schema and cannot perform this initialization.
3. A new installation's first identity administrator is created by the operator with `scripts/clinic_bootstrap_admin.py`. An existing Speakr user without clinic policy needs a separate identity-admin adoption. See [User management](user-management.md). Preserve the internal ID and verify its existing recording ownership.
4. Set up the author's pool and a valid routing destination. In the app container run `python scripts/clinic_backfill_dictations.py --dry-run`. Review counts and skip reasons, especially unfinished media, missing files and owners without a routable pool.
5. On an approved plan run `python scripts/clinic_backfill_dictations.py --apply`. A second dry run should propose no duplicate anchors. For missing audio revisions preview `python scripts/clinic_backfill_dictations.py --init-audio --dry-run` before `--init-audio --apply`. Repair needs real audio samples. Retain the result for any missing or empty source.
6. Sign in with an authorized synthetic user. Verify list visibility, playback and seeking, correction with save and reopen, and authorized document output. Reconcile counts by owner and scope. Keep the pre-upgrade set until acceptance.

## Connector and storage changes

Use a supported local transcription connector such as `openai_whisper` against a local compatible endpoint or the self-hosted `asr_endpoint`. Test the actual model, timing and supported diarization features with neutral speech. A disabled or incompatible connector must be investigated at its configured local endpoint. Public links, internal shares and cloud connectors do not migrate into clinic workflows. Tracking historical share rows does not re-enable the feature.

The upstream tools for normalizing legacy storage paths, moving ordinary recordings to S3 and backfilling durations apply to a different storage layer. They do not move clinic originals. New clinic deployments use persistent local storage. A rollback must match the authoritative database, files, keys and application versions; switching the image alone cannot undo accepted writes. See [File storage](storage.md).

[Original Speakr guide](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/migration-guide.md).
