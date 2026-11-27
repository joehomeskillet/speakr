# Migrate an existing Speakr installation

[Deutsch](../de/admin-guide/migration-guide.md)

Rehearse migration on a restored copy before an upgrade. Record the database engine, application revision, volume mappings, account IDs, recording counts and ownership. Use synthetic material for functional checks. This procedure does not provide production or clinical acceptance.

The clinic fork publishes releases on `main`; `dev` is the development and integration branch. Select the intended clinic revision explicitly. Upstream Speakr configuration and behavior are not sufficient to operate the clinic build.

## 1. Back up and verify restoration

Back up a consistent database, uploads, the separate `CLINIC_ORIGINALS_ROOT`, the instance directory, any separately configured signing-key file and protected deployment configuration. Include the ordinary recording backend if it is an object store. Database rows and clinic originals must be restored together. See [File storage](storage.md).

Verify the restore before applying changes. Retain the pre-upgrade set until acceptance. Changing the application image alone cannot undo schema or accepted data changes.

## 2. Configure the clinic application

Build the selected clinic revision as described in [Installation](../getting-started/installation.md). Preserve database, storage and signing-key mappings. Set `CLINIC_MODE=1` and configure approved local transcription services. If a text model is configured, use an approved local endpoint; do not set only a text-model API key and rely on a default address. See [Model configuration](model-configuration.md).

Use the normal web sign-in with local accounts. Keep Entra and LDAP as prepared, disabled integrations; Windows single sign-on is not offered. Upstream `ENABLE_SSO` settings do not activate the clinic identity adapters. Keep `ENABLE_INQUIRE_MODE=false` for the clinic workspace. See [Sign-in and identity providers](sso-setup.md) and [Vector store](vector-store.md).

## 3. Initialize the schema

Start one normal application process against the restored database. Normal startup initializes the clinic tables and migrations. A `CLINIC_WEB_ONLY=1` process assumes that schema and storage already exist; it does not perform this initialization. Check startup errors before proceeding or adding web-only workers.

The account and recording takeover steps below are explicit; enabling clinic mode does not automatically adopt existing users or turn every recording into a clinic dictation.

## 4. Establish identity administration

Create the first clinic administrator with the application environment and database:

```bash
python scripts/clinic_bootstrap_admin.py --username <username> --email <email-address>
```

The script reads its password securely from a hidden prompt, standard input or the configured password environment variable. It creates both **Administrationsrolle** and **Identitätsadministration**. It does not add clinical permissions. Do not use the original Docker `ADMIN_*` variables as a replacement; their account lacks clinic policy.

An existing local account without policy can be adopted by a freshly authenticated identity administrator through `PUT /admin/users/<id>` with CSRF protection and exactly:

```json
{"adopt_existing_local":true,"expected_version":0}
```

Adoption preserves the internal ID, password, recording ownership and existing site-admin flag. It requires a supported local password hash and no external identity or link request. Review existing administrative flags before activation. Do not merge accounts based on matching email addresses. There is no adoption control in the UI; [User management](user-management.md) describes restrictions, activation codes and recovery gaps.

## 5. Set author pools and routing

Create author pools, add the intended authors and link them to staffed, currently valid writing teams. An owner without a routable author pool is skipped during takeover. Existing upstream groups do not automatically become clinic pools. Keep author resources and writing-team responsibility separate. See [Pools and permissions](group-management.md), including the API-only author-pool and routing operations.

## 6. Preview and take over recordings

Run inside the application environment:

```bash
python scripts/clinic_backfill_dictations.py --dry-run
```

Review every candidate and skip reason. The planner considers recordings with a stored audio locator and checks final processing state, an existing media file, ownership, an author pool and existing takeover receipts. Missing media, unfinished jobs or missing author pools require investigation, not forced acceptance.

The normal backfill command imports the application. Although the takeover dry run creates no dictations, normal startup initialization still applies; use the restored rehearsal instance first.

After approving the preview:

```bash
python scripts/clinic_backfill_dictations.py --apply
```

The script stores a clinic original, creates an anchor and a draft workflow entry, and imports a completed transcript as the first ASR revision. It preserves the source recording and file. The anchor receives the source meeting date, processing status and cached audio duration. The takeover does not copy the old title, notes or tags into the new dictation. It uses the original owner and a stable takeover key; repeated runs do not create duplicate anchors.

A source summary is not a current, revision-bound clinic summary. Where it is displayed as legacy material, it remains outdated. Existing dictations do not receive a new automatic summary merely because the application was upgraded.

## 7. Repair missing initial audio revisions

Some existing dictations need an initial audio revision for playback. Preview this separately:

```bash
python scripts/clinic_backfill_dictations.py --init-audio --dry-run
```

Then apply the reviewed repair:

```bash
python scripts/clinic_backfill_dictations.py --init-audio --apply
```

This path checks the original file and hash, probes real audio duration and creates a revision only where one is missing. It needs the actual stored media and decoder support. A missing, empty or unreadable original is not a successful repair. Retain the per-dictation result, including skip and error reasons.

## 8. Verify operation and counts

1. Run `python scripts/clinic_roles_report.py` for a read-only snapshot of IDs, roles, policies, pools, grants, dictation state and jobs.
2. Sign in as an authorized synthetic user. Verify worklist and recording visibility within the intended scope.
3. Open audio, play and seek; correct text, save and reopen it. Check authorized document output and a denied scope.
4. Reconcile counts by owner and takeover receipt. System recording totals include clinic anchors and are not dictation counts.
5. Repeat the takeover dry run and check that already imported material does not create duplicates.
6. Inspect startup logs and the relevant system audit entries. Account adoption uses `admin.user.changed`; takeover uses `recording.edited`.

The report and a healthy endpoint do not replace actual playback, correction or output checks.

## Integrated model selection and summaries

The current code includes **Verwaltung → System → Modellwahl** and revision-bound **KI-Zusammenfassung**. They are not pending handbook features. An active site administrator can save per-task model identifiers in `clinic_task_models`; service endpoints and credentials remain deployment configuration.

Automatic summaries are tied to the first ASR revision of eligible new dictations and require a ready local text model, enabled automatic summaries and the author's view/edit rights. An upgrade does not backfill summaries for existing dictations. Authorized users can request a summary for usable current text; stored summaries become stale when their source revision no longer matches. See [Model configuration](model-configuration.md) and [Prompts](prompts.md).

## Removed upstream functions

The clinic fork does not offer event/calendar extraction, public share links, internal shares, webhooks, general API v1, incognito mode, cloud ASR connectors, folders, self-registration, mail of any kind (no SMTP, no notification mail, no EML download), password reset by email, Windows single sign-on (no IWA, Negotiate or Kerberos), PDF or image attachments, document import, Citrix/RDP/ICA, the What's-New popup or hard automatic deletion. API tokens remain only for the import agent. Historical database rows do not restore these functions. Retention archives instead of deleting; see [Retention](retention.md).

## Connector and storage compatibility

Supported transcription connectors are `openai_whisper` against a local compatible endpoint, the self-hosted `asr_endpoint`, and the synthetic `clinic_stub`. `TRANSCRIPTION_CONNECTOR` selects explicitly; otherwise `ASR_BASE_URL` selects `asr_endpoint`, with the legacy `USE_ASR_ENDPOINT` path still recognized. Use `TRANSCRIPTION_MODEL` instead of the older `WHISPER_MODEL`. Test the configured model and supported diarization with neutral speech. Do not use cloud URLs from old examples.

The legacy tools `migrate_local_paths_to_local_locator.py`, `migrate_local_recordings_to_s3.py` and `backfill_audio_duration_seconds.py` act on ordinary recording media, not the clinic originals store. New clinic storage uses persistent local originals. A migration of the ordinary backend is a separate reviewed operation and does not complete clinic-original migration.

For rollback, restore the matching database, files, keys and application version. A successful clinic login can replace a legacy bcrypt hash with scrypt; do not assume the original application can authenticate the changed database. Keep the pre-upgrade restore set until the whole acceptance sequence succeeds.
