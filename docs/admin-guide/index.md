# Admin Guide
<small>[Deutsch](../de/admin-guide/index.md)</small>

This section is for administrators of the clinic fork: a web-only clinic dictation build based on Speakr v0.11.1 (pinned upstream commit 48d3018). The pages describe the behavior with clinic mode switched on. Where an upstream Speakr feature is hidden, locked or removed in the clinic build, the page says so and names the clinic alternative.

The clinic build is a development state. Its loaded contract profile is `local-web-parity-lab` with `synthetic_only: true` and `allow_real_data: false`. Nothing in this manual is a clinical approval, a certification, a production release or a claim of 1:1 parity with the legacy dictation product.

## Clinic mode

- Clinic mode is on only when `CLINIC_MODE` is `1`, `true`, `yes` or `on`. The value is read on every call. With clinic mode off, the clinic routes, checks and administration pages described here do not apply, and upstream behavior is unchanged.
- In clinic mode, features are denied by default. A feature works only when `dev.defaults.json` names it under `features` and sets it to `true`. Exception: `chat`, `inquire` and `generative_summary` ignore their flag and depend on the locality of the text models (see Local-only processing below). The file is read from `clinic-spec/config` in the repository root, or from the directory in `CLINIC_CONFIG_DIR`. If a contract file is missing, every check that reads it fails with an error (fail closed); clinic mode never runs on an unknown policy. No admin page and no system setting switches these features.
- A route guard classifies every endpoint. An endpoint that is not in the route table is refused with `403 clinic_unclassified_route`. A route whose feature is locked is refused with `403 clinic_feature_disabled`.

## Where administration lives

The user menu entry **Administration** (English interface: **Admin**) opens `/admin`, the **Verwaltung** (administration). Only accounts with the **Administrationsrolle** (administration role) see the entry and reach the page; the guard refuses every other account with `403 forbidden`. Without a fragment (`#/…`), the landing page is **Benutzer und Rollen** (users and roles). The clinic navigation labels are German only.

The header menu **Verwaltung & Seiten** (administration and pages) links to some of the pages. Its sections **Organisation** (organization: **Schreibkräfte-Pools** (staff pools), **Diktierende und Autorenpools** (authors and author pools), **Benutzer und Rollen** (users and roles)) and **Sprache und Verarbeitung** (language and processing: **Wortschatz** (vocabulary), **Sprachbefehle und Vorlagen** (voice commands and templates), **ASR und Sprachprofile** (ASR and speech profiles), **Verarbeitung** (processing)), and the entry **Aktivität und Audit** (activity and audit) in the section **Betrieb und Zugriff** (operations and access), appear only for accounts with the **Administrationsrolle**. Two menu labels differ from the `/admin` navigation: **Sprachbefehle und Vorlagen** opens **Befehle und Vorlagen**, and **ASR und Sprachprofile** opens **Sprachprofile**. The menu entry **Dateien und Geräteimport** (files and device import) opens the user import view `/#/import`, not `/admin#/import`. All other pages are reached through the navigation in `/admin`.

| Group | Page | Deep link |
|---|---|---|
| **Konten und Pools** (accounts and pools) | **Benutzer und Rollen** (users and roles) | `/admin#/benutzer` |
| | **Schreibkräfte-Pools** (staff pools) | `/admin#/schreibkraefte-pools` |
| | **Diktierende und Autorenpools** (authors and author pools) | `/admin#/diktierende` |
| | **Verzeichnis** (directory) | `/admin#/verzeichnis` |
| **Inhalte** (content) | **Kategorien** (categories) | `/admin#/kategorien` |
| | **Regeln** (rules) | `/admin#/regeln` |
| | **Spalten** (columns) | `/admin#/spalten` |
| | **Wortschatz** (vocabulary) | `/admin#/wortschatz` |
| | **Befehle und Vorlagen** (commands and templates) | `/admin#/befehle-vorlagen` |
| **Richtlinien** (policies) | **Abnahme** (acceptance) | `/admin#/abnahme` |
| **Betrieb** (operations) | **Vorgaben** (defaults) | `/admin#/vorgaben` |
| | **Sprachprofile** (speech profiles) | `/admin#/sprachprofile` |
| | **Import** ([Device and folder import](recording-sessions.md#device-and-folder-import-admin-page-import)) | `/admin#/import` |
| | **Verarbeitung** (processing) | `/admin#/verarbeitung` |
| | **Aktivität und Audit** (activity and audit) | `/admin#/aktivitaet` |
| **System** | **Systemstatistik** (system statistics) | `/admin#/system/statistik` |
| | **Systemeinstellungen** (system settings) | `/admin#/system/einstellungen` |
| | **System-Auditprotokoll** (system audit log) | `/admin#/system/audit` |

- For accounts with the **Administrationsrolle**, the app shell forwards its nine links `/#/verwaltung/<slug>` (`schreibkraefte-pools`, `diktierende`, `benutzer`, `wortschatz`, `befehle-vorlagen`, `sprachprofile`, `import`, `verarbeitung`, `aktivitaet`) to `/admin#/<slug>`. Other accounts stay in the shell; a page whose probe is refused shows **Diese Seite ist für dieses Konto nicht freigegeben.** (this page is not enabled for this account).
- When `/admin` loads, it probes most pages with a GET request to their API; **Regeln**, **Verzeichnis** and the **System** pages are shown without a probe. While the probe runs, the page shows **Berechtigungen werden geprüft …** (checking permissions). A page whose API answers 401 or 403 for the account is withheld and listed in the notice **Für dieses Konto nicht freigegeben: …** (not enabled for this account). A network error or a 404 leaves the page visible with its own state.
- **Aktivität und Audit** is withheld for every account in the released build, administrators included. Its probe needs a statistics grant, and statistics grants can only be created by server code that nothing in the application calls. Status: Prepared, disabled. See [System statistics](statistics.md).
- The **System** pages are the upstream statistics, settings and audit-log tabs, reused in the clinic navigation. The upstream tabs **User management**, **Default prompts**, **Groups** and **Vector store** are not rendered in clinic mode.

## Other administration pages

These pages of `/admin` are not system settings. Each has its own rules. **Wortschatz** is described in [Local speech recognition](whisperx-setup.md#the-wortschatz-page), **Import** in [Recording sessions](recording-sessions.md#device-and-folder-import-admin-page-import), **Abnahme** under [Release right](#release-right) and in [Group management](group-management.md#release-freigeben-and-acceptance-abnahme).

| Page | Scope | Status |
|---|---|---|
| **Kategorien** (categories) | Category sets visible to the signed-in account | Saving: Open gap; enforcement of required sets: Available |
| **Regeln** (rules) | One dictation | Available |
| **Spalten** (columns) | System | Available |
| **Befehle und Vorlagen** (commands and templates) | Own account, author pool or system | Commands and templates: Available; **Bausteine** (text blocks): Open gap |
| **Vorgaben** (defaults) | Own account only | Available |

### Kategorien

- The page lists under **Kategorien der Gruppe** (categories of the group) the category sets that the signed-in account may use: system sets, sets of its author pools and its own sets. The editor offers **Kategorie hinzufügen** (add category), **Entfernen** (remove), **Pflichtfeld** (required field), **Speichern** (save) and **Vorschau Sendeformular** (preview of the send form).
- **Speichern** always fails with **Das Speichern ist fehlgeschlagen.** (saving failed): the page sends the command `save_category_set`, which is not in the parity command catalog, and the server refuses unknown commands. No route, page or script creates or changes a category set.
- Enforcement works: while a required set is visible to an author, saving the dictation properties without a valid category of that set is refused with `422 missing_category`.

### Regeln

- Enter the **Diktat-ID** (dictation ID) and click **Regeln laden** (load rules). The panel **Regeln für Metadaten** (rules for metadata) takes up to 20 rules (**Muster**, pattern, at most 256 characters; **Zielfeld**, target field; **Ersetzung**, replacement) and an **Eingabetext** (input text).
- **Vorschau** (preview) shows **Bisheriger Wert** and **Neuer Wert** (old and new value). **Übernehmen** (apply) writes only what the preview showed: the server recomputes the preview and refuses the write if the rules or the input changed. The rules themselves are not stored.
- Target fields are title, dictation name, remarks, notes, priority, urgency, deadline and due date. Context and identity fields (user, author, patient, case, integration, client and account IDs) are refused.
- Preview and apply need **Bearbeiten** (edit) on that dictation; the **Administrationsrolle** alone is not enough. An applied change is audited like other metadata changes of the dictation.

### Spalten

- **Metadatenspalten** (metadata columns) edits the **Spaltendefinitionen** (column definitions) with **Bezeichnung** (label), **Anzeigetyp** (display type) and an optional XML path. Every signed-in account reads the active definitions; active site administrators also see inactive ones.
- Only an active site administrator saves (`403 POLICY_DENIED` otherwise). The page allows at most 30 columns (**Es sind höchstens 30 Spalten möglich.**). A column name starts with a letter and has at most 64 letters, digits or underscores. An XML path is a chain of at most eight plain element names separated by `/`, at most 256 characters, without attributes, wildcards or predicates.
- Each save is audited as `admin.setting.changed` with the key `other`, without the column name or values, while `audit_log_enabled` is on.

### Befehle und Vorlagen

- The page has the parts **Befehle** (voice commands), **Bausteine** (text blocks) and **Vorlagen** (templates).
- **Bausteine** shows **Bausteine sind noch nicht verfügbar: Die Serverressource fehlt. Diese Teilanforderung bleibt offen.** (text blocks are not available yet; the server resource is missing; this partial requirement stays open). Status: Open gap.
- Personal commands and templates belong to the signed-in account. Pool commands and pool templates can be saved by any member of the author pool, system templates by any account with the **Administrationsrolle**; see [Effects on shared pool resources](group-management.md#effects-on-shared-pool-resources).
- Templates are DOCX or ODT files of at most 2 MiB. Saving commands and templates writes no audit row.

### Vorgaben

- **Empfänger und Arbeitsplatz** (recipient and workstation) sets the **Standardempfänger** (default recipient: **Alle berechtigten Personen**, all eligible persons; **Bestimmte Person**, a specific person; **Personalpool**, staff pool; **Selbstkorrektur**, self-correction) and **Abweichende Auswahl beim Diktat** (other choice when dictating: **Nur der Standard**, default only; **Nur Personalpools**, staff pools only; **Frei wählbar**, free choice).
- It edits only the signed-in account's own recipient default; the server refuses any other target. It is not a system-wide default. A person or staff pool as default needs the account's own author pool; otherwise the page shows **Kein Autorenpool zugewiesen.** (no author pool assigned).
- The recipient default is stored per account. The last selection and the **Arbeitsplatz-Einstellungen** (workstation settings) are kept in process memory only: they are lost on a restart and can differ between worker processes. Values fixed by a clinic ceiling show **Zentral festgelegt** (set centrally).
- No audit row is written.

## Roles and rights

The clinic build separates administration from access to dictation content. Keep these roles and rights apart. Each is granted separately.

### Site administration

- Set by: a local identity administrator, who sets the **Administrationsrolle** (`User.is_admin`) in **Benutzer und Rollen**.
- Gives: access to `/admin` and to all routes the guard classifies as administrative.
- Several clinic functions also need an *active* site administrator: the **Administrationsrolle** plus a clinic account with status **Aktiv** (active). A deactivated administrator, or one without a clinic account row, is not an active site administrator. Functions that check this include Fachrechte changes, setting pool leads, publishing the acceptance policy, job retry in **Verarbeitung**, and the admin dictation overview.
- Content access: an active site administrator may view and play every clinic dictation and read its corrections. In the worklist, an active site administrator sees every dictation; each foreign row that the server actually returns is audited as `recording.content.read` with `part=worklist`. The admin endpoints `/api/admin/clinic/dictations/<id>/transcript` and `/audio` open any user's transcript or audio with no reason prompt. They write the audit row first (`recording.content.read` with `part=transcript`, or `recording.media.played`). If the row cannot be stored, the answer is `503 audit_unavailable` and nothing is delivered.
- Assignment: an active site administrator may assign or reassign any dictation without the Fachrecht **Ansehen** (view) and without a lead role; a deny for **Neu zuordnen** (reassign) still blocks it.
- Does not give: edit, export, release, original-audio export, archive or delete on other people's dictations. These need Fachrechte, like for any other user.

### Local identity administration

- Set by: the flag **Identitätsadministration** (identity administration) on the account. The first administrator (**Administrationsrolle** and **Identitätsadministration**) is created once on the server with `scripts/clinic_bootstrap_admin.py`; see [User management](user-management.md).
- Gives: account administration in **Benutzer und Rollen** › **Konten** (accounts): create accounts, edit them, deactivate and reactivate them, change the two role flags and issue one-time codes (**Einmalcode ausstellen**, issue one-time code). This needs both **Administrationsrolle** and **Identitätsadministration**, status **Aktiv**, no pending password change, a matching local session and a sign-in within the last 300 seconds. Otherwise the server refuses with `403 unauthorized` or `403 reauth_required`; the panel shows **Keine Berechtigung für diese Aktion.** (no permission for this action) or **Bitte erneut anmelden und die Änderung nochmals ausführen.** (please sign in again and repeat the change).
- One-time codes cannot be issued for other administrator or identity-administrator accounts (`admin_recovery_forbidden`); those accounts are recovered only through the server console. Details: [User management](user-management.md).
- Does not give: any additional dictation access. The flag itself grants none; because it works only together with the **Administrationsrolle**, an active identity administrator has the site administrator's content access (view, play, read corrections) and nothing more. The account panel states: **Identitätsrollen geben keinen Zugriff auf Diktate. Fachrechte werden separat erteilt.** (identity roles give no access to dictations; professional rights are granted separately). Identity administrators may read a user's Fachrechte, but only an active site administrator changes them.
- Removing the **Administrationsrolle** also removes **Identitätsadministration**. Administrators never set passwords in clinic mode, and accounts are deactivated, not deleted.

### Pool leads

- Set by: an active site administrator marks a member of a **Schreibkräfte-Pool** (staff pool) as **Leitung** (lead). Internally this reuses the upstream group-admin role of the pool's group.
- Gives: in the worklist, the lead sees unassigned, non-draft dictations of the author pools routed to the led staff pool. The lead may assign or reassign them when the lead holds the Fachrecht **Ansehen** (view) and has no deny for **Neu zuordnen** (reassign). The lead may read the usage counts of the led pool.
- Does not give: creating, renaming or deactivating pools, changing memberships, setting leads, changing Fachrechte, or opening `/admin`.
- A deactivated clinic account ends all lead functions, and assigning needs an active clinic account.
- For leads without the **Administrationsrolle**, the user menu still shows the upstream entry **Gruppen** (English interface: **Groups**), which links to `/group-management`. In clinic mode that page is refused with `403 clinic_feature_disabled`.

See [Pools (group management)](group-management.md).

### Fachrechte (professional rights)

- Fachrechte are per-user grants in **Benutzer und Rollen** › **Fachrechte**. Each entry has an effect (**Erlauben** / **Verweigern**, allow / deny), a scope (**Eigene Diktate**, **Pool**, **Alle Diktate**: own dictations, pool, all dictations) and the actions it covers. A matching deny always wins.
- Only an active site administrator changes Fachrechte. Other viewers see **Nur Lesezugriff. Änderungen sind nur durch Administration möglich.** (read-only; changes only by administration).
- Safe own defaults: an author may view, play and edit their own dictations. Every other action, even on their own dictations, needs an explicit grant.

### Release right

- The action **Freigeben** (release) is a separate Fachrecht. Nobody holds it by default, site administrators included.
- It is needed for **Zur Abnahme einreichen** (submit for acceptance; together with **Bearbeiten**, edit), **Zusätzlich abnehmen** (accept), **Zur Korrektur zurückweisen** (return for correction), **Wieder öffnen** (reopen) and **Endgültig freigeben** (release finally).
- The **Abnahme** (acceptance) page sets, per dictation (loaded by **Diktat-ID**, dictation ID), whether acceptance is required (**Freigabe erforderlich**, acceptance required) and who may accept (**Berechtigte Freigebende**, authorized approvers). Only an active site administrator publishes it. Without a published policy, acceptance is not required. Before the first publication the checkbox shows the dictation's current workflow setting (off by default); it is preset on only for a dictation that has no workflow entry yet.
- Final release also needs a trusted KIS context. Status of the KIS: Simulated (only with `CLINIC_PROFILE=staging`, synthetic MockKIS, no KIS evidence); otherwise the adapter is disabled and final release is refused.

### Original-audio export right

- The action **Originalaudio exportieren** (export original audio) is separate from **Exportieren** (text export).
- The audio download of the original file requires it. It is not an own default, and site administrators do not get it from their content access.
- Opening a local mail client with the audio attached is open gap WG-EXP-009.

## Status vocabulary

All administration pages use these terms.

| Status | Meaning |
|---|---|
| Available | Part of the released clinic build and usable in clinic mode. |
| Default off | Part of the released build, but off until the documented switch is set: an environment variable set on the server by operations (example: GPU-first speech recognition, `CLINIC_GPU_ASR_BASE_URL`) or a system setting changed by the site administration (example: **Automatic archival**). |
| Prepared, disabled | Code exists, but the released build has no supported switch that turns it on. Tests are synthetic only; there is no real tenant or directory and no clinical approval. Examples: Microsoft Entra sign-in, Windows SSO (IWA), the directory comparison, statistics grants, managed offline storage. |
| Simulated | Works only against fixtures or a simulation, never against the real system. Examples: the KIS (mock adapter with `CLINIC_PROFILE=staging`) and the Philips device side (import agent simulator mode and fixtures). Not evidence for a real device or KIS. |
| In verification | Not part of the current released build. Labels and availability may change. Currently only the admin page **Modellwahl** (model selection for GPU and CPU transcription and for the summary) and the automatic dictation summary bound to the first speech-recognition revision, with its own local-model check. The released build instead runs the upstream title and summary follow-up job (see Local-only processing). |
| Upstream only | Exists in upstream Speakr, but is hidden, locked or removed in the clinic build. |
| Open gap | A documented gap that stays open. This includes every web-parity gap in `clinic-spec/planning/web-parity-gaps.json` and the further gaps named on the individual pages. |

The web-parity register ([clinic fork source](https://github.com/joehomeskillet/speakr/blob/main/clinic-spec/planning/web-parity-gaps.json)) currently lists 13 gaps, all with status `open`: WG-ADM-009, WG-LIVE-002, WG-LIVE-006, WG-LIVE-007, WG-IMP-009, WG-IMP-010, WG-EXP-004, WG-EXP-005, WG-EXP-007, WG-EXP-009, WG-EXP-010, WG-OPS-003 and WG-OPS-004. The register sets `full_parity_claim_allowed` to `false`.

## Local-only processing

- Speech recognition is meant to run on local services only; there is no cloud connector and no cloud fallback. The clinic build registers three transcription connectors: `openai_whisper` (an OpenAI-compatible local service such as Speaches), `asr_endpoint` (WhisperX or whisper-asr-webservice) and `clinic_stub` (a synthetic test double). The upstream cloud connectors are removed.
- In clinic mode, the `openai_whisper` connector refuses an empty or non-local `TRANSCRIPTION_BASE_URL` with a configuration error; the GPU target `CLINIC_GPU_ASR_BASE_URL` is checked the same way. This ASR check is wider than the text-model check below: it also accepts link-local addresses, single-label host names and names ending in `.local`, `.internal`, `.lan` or `.localhost`. See [Local speech recognition](whisperx-setup.md).
- Open gap: `ASR_BASE_URL` of the `asr_endpoint` connector is not checked for locality. Point it at local hosts only.
- GPU first, CPU second (status: Default off): when `CLINIC_GPU_ASR_BASE_URL` is set, a job goes to the GPU service first. On a connection error, a timeout, an HTTP 5xx answer or an unhealthy `/healthz`, the same job runs once on the CPU target. The GPU is not retried; a CPU failure ends this attempt, and whether the job runs again is decided by the job queue (see [Local speech recognition](whisperx-setup.md)).
- Hotwords, vocabulary and profile settings are decoder hints, not acoustic training. In clinic mode, transcription hints are dropped unless `CLINIC_ASR_PROMPTS_ENABLED` is on. Optimization runs started in **Sprachprofile** with **Optimierung starten** (start optimization) are queued, and the processing service then refuses them with `CAPABILITY_UNAVAILABLE`; no model is trained or adapted.
- Local AI (status in the clinic UI: Upstream only): the routes for chat, Inquire and generative summaries are feature-locked in clinic mode unless `TEXT_MODEL_BASE_URL` is local and every `CHAT_MODEL_BASE_URL`, `TRANSLATION_MODEL_BASE_URL` and `EMBEDDING_BASE_URL` that is set is local too. Here, local means loopback, a private address, or a host in `CLINIC_LOCAL_MODEL_HOSTS` (default `speaches,host.docker.internal,localhost`); other single-label host names do not count. Lifting the lock adds no clinic UI. Requests that name no recording are still refused with `403 clinic_feature_unsupported`, and other requests need the edit right (**Bearbeiten**) on each named recording. See [Ambrose settings](ambrose.md) and [Vector store](vector-store.md).
- Automatic title and summary (status: Default off, no LLM client until `TEXT_MODEL_BASE_URL` or `TEXT_MODEL_API_KEY` is set; locality check of the automatic title and summary job: Open gap): in clinic mode, the released build queues a follow-up job for the recording's title and summary after every transcription. This is the upstream recording summary, not the automatic dictation summary with the status In verification. The job has no locality check of its own. The AI title is requested whenever an LLM client exists and the recording has no user-chosen title (a naming template without an AI title part also skips it). `disable_auto_summarization` and the user's automatic-summary switch skip only the summary step. An LLM client exists as soon as `TEXT_MODEL_BASE_URL` or `TEXT_MODEL_API_KEY` is set; without `TEXT_MODEL_BASE_URL` it uses a hosted default address. Leave both unset, or set `TEXT_MODEL_BASE_URL` to a local service. Never set `TEXT_MODEL_API_KEY` alone.
- The binding work order rules out cloud ASR and generative rewriting of clinical text.

Details: [Local speech recognition](whisperx-setup.md), [Model configuration](model-configuration.md).

## Topic map

| Page | What it covers in the clinic build | Status |
|---|---|---|
| Admin Guide (this page) | Clinic mode, administration navigation, roles and rights, status vocabulary, local-only processing | Available |
| [User management](user-management.md) | **Benutzer und Rollen** (users and roles): **Konten** (accounts), **Poolbezüge** (pool memberships), **Fachrechte** (professional rights); first administrator (**Administrationsrolle** and **Identitätsadministration**); one-time codes instead of admin-set passwords; deactivation instead of deletion | Available; Entra sign-in policies: Prepared, disabled |
| [Pools (group management)](group-management.md) | Author pools and staff pools, pool leads, deactivation and validity windows, release and original-audio rights; upstream groups and teams | Pools: Available; upstream groups: Upstream only |
| [System statistics](statistics.md) | **Systemstatistik** (upstream usage statistics) and the scoped clinic statistics in **Aktivität und Audit** | Systemstatistik: Available; clinic statistics: Prepared, disabled |
| [System settings](system-settings.md) | **Systemeinstellungen**: global settings, clinic setting ceilings, audited changes | Available |
| [Default prompts](prompts.md) | Shipped German clinic summary and title prompts; the upstream prompt editor tab is not shown | Shipped prompts: Available; prompt editor: Upstream only |
| [Model configuration](model-configuration.md) | Local text and transcription models set by environment; the per-task **Modellwahl** page | Environment configuration: Available; **Modellwahl**: In verification |
| [Ambrose settings](ambrose.md) | Upstream assistant (chat and Inquire); no clinic UI | Upstream only |
| [Vector store](vector-store.md) | Upstream semantic search index; tab not shown in clinic mode | Upstream only |
| [Recording sessions](recording-sessions.md) | Clinic recorder upload, no audio kept in browser storage, upstream session limits for the remaining upload paths | Available |
| [Webhooks](webhooks.md) | User webhooks are locked in clinic mode; in-app workflow notices instead | Webhooks: Upstream only |
| [Email setup](email-setup.md) | SMTP is optional; e-mail verification and password reset are removed; the upstream completion mail is not reviewed for clinical use | Processing notification mail: Default off; verification and reset: Upstream only |
| [Sign-in and identity providers](sso-setup.md) | Local sign-in only; upstream OIDC SSO is refused; Entra, Windows SSO (IWA) and LDAP profiles | OIDC: Upstream only; Entra, IWA, LDAP: Prepared, disabled |
| [Local speech recognition](whisperx-setup.md) | Local ASR services, optional GPU service with one CPU fallback, synthetic test connector; WhisperX as upstream reference | Available; GPU fallback: Default off |
| [Retention](retention.md) | The retention sweep archives and never deletes; the remaining deleting paths are listed on the page; dictation archiving through the workflow; read-only **Archiv** view | Automatic archival: Default off; approved retention policy: Open gap |
| [Privacy and regulated data](privacy.md) | What is stored where, browser storage, own-voice consent, offline storage | Available; managed offline storage: Prepared, disabled |
| [Audit log](audit-log.md) | **System-Auditprotokoll**, event catalog, audit settings; pruning withheld in clinic mode | Available |
| [File storage](storage.md) | Immutable local originals under `CLINIC_ORIGINALS_ROOT`; upstream local and S3 backends for recording media | Available |
| [Migration guide](migration-guide.md) | Upgrading an existing instance to the clinic build: first administrator, account adoption, takeover of legacy recordings | Available |

## Upstream reference

Upstream Speakr documentation: [Admin Guide (pinned original)](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/index.md). It describes upstream Speakr, not the clinic build.
