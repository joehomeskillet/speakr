# Clinic administration

[Deutsch](../de/admin-guide/index.md)

This guide describes the current clinic fork with `CLINIC_MODE` enabled. Recording, playback, correction and dictation management run in the web client. The only local application, `apps/windows-import`, imports files from approved devices and folders; it is not a native dictation, playback or Office-automation client.

The shipped `local-web-parity-lab` contract is for synthetic data and sets `allow_real_data` and `production_approved` to `false`. A feature described as available here is present in the code; that is not clinical approval, proof of real-device operation or a claim of complete legacy parity.

## Open administration

An account with **Administrationsrolle** (site administration) can open **Verwaltung** at `/admin`. Its landing page is **Benutzer und Rollen** (users and roles), which brings accounts, pool membership and **Fachrechte** (professional rights) together. The labels below match the German clinic interface.

| Navigation group | Pages and direct addresses | Guidance |
| --- | --- | --- |
| **Konten und Pools** | **Benutzer und Rollen** (`/admin#/benutzer`), **Schreibkräfte-Pools** (`/admin#/schreibkraefte-pools`), **Diktierende und Autorenpools** (`/admin#/diktierende`), **Verzeichnis** (`/admin#/verzeichnis`) | [Accounts](user-management.md), [pools](group-management.md), [identity providers](sso-setup.md) |
| **Inhalte** | **Kategorien** (`/admin#/kategorien`), **Regeln** (`/admin#/regeln`), **Spalten** (`/admin#/spalten`), **Wortschatz** (`/admin#/wortschatz`), **Befehle und Vorlagen** (`/admin#/befehle-vorlagen`) | Shared vocabulary, metadata and resource editors; [recognition guidance](whisperx-setup.md) |
| **Richtlinien** | **Abnahme** (`/admin#/abnahme`) | Per-dictation acceptance policy; [release rights](group-management.md) |
| **Betrieb** | **Vorgaben** (`/admin#/vorgaben`), **Sprachprofile** (`/admin#/sprachprofile`), **Import** (`/admin#/import`), **Verarbeitung** (`/admin#/verarbeitung`), **Aktivität und Audit** (`/admin#/aktivitaet`) | [Recording/import](recording-sessions.md), [local ASR](whisperx-setup.md), [statistics and job health](statistics.md) |
| **System** | **Systemstatistik** (`/admin#/system/statistik`), **Systemeinstellungen** (`/admin#/system/einstellungen`), **Modellwahl** (`/admin#/system/modelle`), **System-Auditprotokoll** (`/admin#/system/audit`) | [Statistics](statistics.md), [settings](system-settings.md), [models](model-configuration.md), [audit](audit-log.md) |

Most panels probe their API permissions before display. A refused probe can hide a page and show **Für dieses Konto nicht freigegeben: …**. A visible page does not establish that every action is enabled. In particular, the scoped statistics behind **Aktivität und Audit** lack an application workflow for granting access; the separate **Systemstatistik** remains available to site administration.

The header menu **Verwaltung & Seiten** links to selected administration pages. **Dateien und Geräteimport** opens the user import view `/#/import`, not the administration import page. The upstream user-management, default-prompt, groups and vector-store tabs are not the clinic navigation.

## Choose the correct role

| Role or right | What it grants | What it does not grant |
| --- | --- | --- |
| **Administrationsrolle** | Access to administration; several clinic actions additionally require a clinic account with status **Aktiv** | Automatic permission to edit, export or release other people's dictations |
| Active site administrator | Audited access to view/play clinic dictations and read their corrections; pool and permission administration | A general exemption from edit, export, release or original-audio rights |
| **Identitätsadministration** | Together with site administration and recent local authentication: create/edit accounts, manage role flags, deactivate/reactivate and issue permitted one-time codes | Additional dictation rights from the identity flag itself |
| **Leitung** (pool lead) | Coordinates assignment within the routed staff pool, subject to the assignment policy | Site administration, account administration or unrestricted content access |
| **Fachrechte** | Explicit actions scoped to own dictations, a pool or all dictations | Permission outside the granted action/scope |
| **Freigeben** | The separate release permission used by acceptance and release workflow actions | Implicit permission from being an administrator or author |
| **Originalaudio exportieren** | The separate original-audio export permission | Permission implied by playback or text export |

Authors have safe defaults to view, play and edit their own dictations. Other actions require explicit rights. Matching denies normally win; active site administrators' view/play/correction-read access is an explicit policy exception and is audited. A read or playback grant never implies permission to change the dictation.

Local account administration requires both role flags, an active account and recent local authentication. One-time codes replace administrator-set passwords. Accounts are deactivated rather than deleted. See [user management](user-management.md) for account recovery, limits and the reauthentication conditions.

Author pools organise author resources and visibility. Staff pools organise responsibility for processing. They are distinct, with explicit routing between them. See [group management](group-management.md) for membership, leadership, validity and assignment rules.

The **Abnahme** page configures whether a dictation requires acceptance and who may accept. Publishing the policy requires active site administration. Final release also depends on the workflow and trusted KIS context; a simulated adapter is not proof of a working clinical KIS integration.

## Clinic mode and processing

`CLINIC_MODE` accepts `1`, `true`, `yes` or `on`. Its contract files come from `clinic-spec/config` or `CLINIC_CONFIG_DIR`. Features are denied by default; unknown routes are refused. Ordinary system settings cannot lift clinic policy ceilings. Chat, Inquire and generative-summary locks have a specific local-model rule rather than following their development-default flags.

Speech recognition uses local services. The OpenAI-compatible ASR path and optional GPU target check their endpoint's locality; the `asr_endpoint` connector does not, so operations must keep `ASR_BASE_URL` local. GPU-first processing is optional and falls back once to the CPU path for the documented failure cases. Hotwords and profile hints are not acoustic training. See [local ASR setup](whisperx-setup.md).

**Modellwahl** and revision-bound **KI-Zusammenfassung** are part of the current build. A local text model, permissions and summary preferences determine whether a summary can be generated. The transcript remains separate. Older title/recording-summary paths and upstream indexing have additional configuration boundaries; see [models](model-configuration.md), [prompts](prompts.md) and [vector search](vector-store.md). The clinic workspace offers no [Ambrose chat interface](ambrose.md).

## Removed upstream features

The clinic fork does not offer event/calendar extraction, public share links, internal shares, webhooks, the general API v1, incognito mode, cloud ASR connectors, folders, self-registration, mail verification, password reset by mail, the What's-New popup or hard automatic deletion. API tokens are reserved for the import agent. Retention archives instead of deleting. See [webhooks](webhooks.md), [email and recovery](email-setup.md) and [retention](retention.md).

## Status and evidence limits

| Term | Meaning in this guide |
| --- | --- |
| Available | Implemented in the current clinic code, subject to its permissions and configuration |
| Default off | A documented configuration switch exists, but the function is off by default |
| Prepared, disabled | Code or a contract exists, but the application does not expose a supported active integration |
| Simulated | Uses fixtures or a mock; not evidence for a real device, directory or KIS |
| Upstream only | An upstream function is hidden, locked or unavailable in the clinic workflow |
| Open gap | A requirement remains unresolved; an available partial web alternative does not close it |

The web-parity register `clinic-spec/planning/web-parity-gaps.json` keeps 13 gaps open and sets `full_parity_claim_allowed=false`: WG-ADM-009, WG-LIVE-002, WG-LIVE-006, WG-LIVE-007, WG-IMP-009, WG-IMP-010, WG-EXP-004, WG-EXP-005, WG-EXP-007, WG-EXP-009, WG-EXP-010, WG-OPS-003 and WG-OPS-004. These include native window/Office interaction, device writes, global hotkeys and background-client behavior. Do not interpret the web workflow or import agent as closing them.

For an existing installation, start with the [migration guide](migration-guide.md). Plan [storage and recovery](storage.md), [archiving](retention.md) and [privacy](privacy.md) together; UI availability alone is not proof that backups, retention or clinical access are correctly operated.

[Original Speakr handbook at the archived revision](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/index.md)
