# User Management
<small>[Deutsch](../de/admin-guide/user-management.md)</small>

This page describes how accounts are administered in the clinic build, that is, with `CLINIC_MODE` set to `1`, `true`, `yes` or `on`. Without clinic mode, none of the clinic checks, routes or pages described here exist, and upstream Speakr behavior applies.

In clinic mode, the upstream **User Management** tab of `/admin` is not rendered. Accounts are administered on the clinic page **Benutzer und Rollen** (Users and roles). Local accounts are the only sign-in method that works. Nobody in the administration sets or sees passwords: an identity administrator (with the **Administrationsrolle**) creates an account and issues a one-time code.

Upstream Speakr documentation of this topic (pinned original, not valid for the clinic build): [User Management (upstream)](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/user-management.md).

The clinic administration labels exist in German only. Labels are quoted in bold, followed by an English gloss in parentheses.

## Status overview

| Function | Status |
|---|---|
| Local accounts: create, edit, deactivate, reactivate, one-time codes | Available |
| First administrator (**Administrationsrolle** and **Identitätsadministration**) through `scripts/clinic_bootstrap_admin.py` | Available |
| **Fachrechte** (professional rights), including release and original-audio export | Available |
| Form on the login page to redeem a one-time code | Open gap (server endpoint only, no form) |
| One-time code or console recovery for an administrator account | Open gap (see below) |
| Adoption of an existing account without clinic account policy | Available (API only, no UI control) |
| Entra sign-in and linking (**Lokal und Entra**, **Nur Entra**) | Prepared, disabled (synthetic local tests only, no real tenant, no clinical approval) |
| Directory comparison (**Verzeichnis**), LDAP, Windows single sign-on | Prepared, disabled |
| Reading the local Windows accounts of a workstation (WG-ADM-009) | Open gap |
| Admin-set passwords, account deletion, public sharing permission, **Can create groups**, OIDC single sign-on | Upstream only |

## Who may do what

The clinic build separates four kinds of rights. None of them implies another.

**Administrationsrolle** (administration role) is site administration. It is the stock Speakr admin flag of the account. It opens `/admin` and the system pages (see [System Settings](system-settings.md), [System Statistics](statistics.md) and [Audit Log](audit-log.md)). An *active site administrator* is an account with this role and the account status **Aktiv** (Active).

**Identitätsadministration** (identity administration) is local identity administration. It is a flag of the clinic account policy. It counts only together with the **Administrationsrolle**: without that role the account cannot open `/admin` at all.

**Leitung** (lead) is the pool lead of a **Schreibkräfte-Pool** (staff pool). The extra powers of a lead apply only to the led pool, and the lead role does not open `/admin`. Leads are set on the pool pages by an active site administrator, not here; see [Group Management](group-management.md).

**Fachrechte** (professional rights) decide what an account may do with dictations, for example **Freigeben** (Release) and **Originalaudio exportieren** (Export original audio). They are set in the **Fachrechte** section of **Benutzer und Rollen**. Two Fachrecht actions are held by nobody by default, site administrators included: **Freigeben** and **Originalaudio exportieren**; see [Release right](index.md#release-right) and [Original-audio export right](index.md#original-audio-export-right) in the Admin Guide.

| Task | Required |
|---|---|
| Open **Benutzer und Rollen** and read the account list | **Administrationsrolle** |
| Create, edit, deactivate and reactivate accounts, change roles, issue one-time codes, lift a login lock, adopt an account | **Administrationsrolle** and **Identitätsadministration**, status **Aktiv**, no pending password change, a local session whose security version is current, and a sign-in within the last 300 seconds |
| Change Fachrechte | Active site administrator. **Identitätsadministration** is neither needed nor sufficient. |
| Read the Fachrechte list of an account (API) | Active site administrator, active identity administrator, or the account itself |
| Set or remove a pool lead | Active site administrator |
| Directory comparison (**Verzeichnis**) | **Administrationsrolle** and **Identitätsadministration** (prepared, disabled) |
| View and play any clinic dictation, read its corrections, use the admin dictation overview | Active site administrator. Opening a transcript or audio through the admin routes, and each foreign row in the worklist, is audited; the metadata overview is not. |
| Edit, export, release, export original audio, archive or delete another user's dictation | A matching Fachrecht. The **Administrationsrolle** does not grant these actions. |

The server refuses an account write without **Identitätsadministration** with `403 unauthorized` (`Identitätsadministration erforderlich.`); the page shows `Keine Berechtigung für diese Aktion.`. A write more than 300 seconds after the last local sign-in is refused with `403 reauth_required`; the page then shows `Bitte erneut anmelden und die Änderung nochmals ausführen.`. Sign out, sign in again and repeat the change.

The **Fachrechte** section shows the note `Der Zugriff auf ein konkretes Diktat bleibt zusätzlich an Benutzer, Pools und Objektberechtigungen gebunden. Eine Identitätsrolle gibt keinen Diktatzugriff.` (Access to a dictation stays bound to users, pools and object permissions. An identity role gives no dictation access.)

## Creating the first administrator

A new clinic installation has no account that can sign in. Create the first administrator once at the server console with `scripts/clinic_bootstrap_admin.py` ([source, clinic fork](https://github.com/joehomeskillet/speakr/blob/main/scripts/clinic_bootstrap_admin.py)). Run it in the environment of the application, for example inside the running app container, with the same configuration as the application, including `CLINIC_MODE`. The script itself does not check `CLINIC_MODE`.

```bash
python scripts/clinic_bootstrap_admin.py --username <username> --email <email-address> [--name "<display name>"]
```

The password is never a command-line argument. The script reads it from the first source that applies:

1. `--password-stdin`: the first line of standard input.
2. The environment variable named by `--password-env`, by default `CLINIC_BOOTSTRAP_PASSWORD`, if it is set and not empty.
3. On a terminal: a hidden prompt (`Initial administrator password:`, then `Repeat password:`).

The password is never printed or logged. It must satisfy the [password policy](#passwords).

The script creates the identity tables if they are missing. It then creates one account with **Administrationsrolle** and **Identitätsadministration**, the status **Aktiv**, the sign-in policy **Nur lokal** (Local only) and no clinical role and no Fachrecht. A marker row makes the step run once only: as soon as a marker or any identity administrator exists, a second run changes nothing.

| Output | Exit code |
|---|---|
| `First identity administrator '<username>' created.` | 0 |
| `Already initialised; nothing changed.` | 0 |
| `Error: no password supplied (environment variable, --password-stdin or terminal).` (also when the two prompts differ) | 2 |
| `Error: password rejected (<code>).` with `too_short`, `too_long`, `blocklisted` or `invalid_type` | 2 |
| `Error: account data rejected (<code>).` with `invalid_username`, `invalid_email` or `duplicate` | 2 |
| `Error: bootstrap failed.` | 1 |

The username is 2 to 20 characters long, the e-mail address at most 120 characters. The bootstrap writes the log line `clinic identity: first identity administrator <id> created` and no audit log row.

**Do not use the upstream variables `ADMIN_USERNAME`, `ADMIN_EMAIL` and `ADMIN_PASSWORD` for the clinic build.** The Docker entrypoint then runs `scripts/docker_create_admin.py`, which creates an account with the administration flag but without clinic account policy. In clinic mode such an account cannot sign in until an identity administrator adopts it (see [Existing accounts without account policy](#existing-accounts-without-account-policy)). Its username and e-mail address are then taken, so the bootstrap needs different ones.

The console commands `flask clinic-identity bootstrap` and `flask clinic-identity recovery` are defined in the code, but the application does not register them at startup; only tests do. They are not available in a deployed instance.

## The page Benutzer und Rollen

In clinic mode, `/admin` shows the **Verwaltung** (Administration) navigation. **Benutzer und Rollen** is in the group **Konten und Pools** (Accounts and pools), next to **Schreibkräfte-Pools**, **Diktierende und Autorenpools** (Authors and author pools) and **Verzeichnis** (Directory). It is the landing page of `/admin`; the deep link is `/admin#/benutzer`. Each page is probed when `/admin` loads. A page whose API refuses the account is hidden and listed in the notice `Für dieses Konto nicht freigegeben: …` (Not released for this account).

The page has three sections:

- **Konten** (Accounts): the account list with the editor.
- **Poolbezüge** (Pool memberships): the author pools and staff pools of one selected account, read only.
- **Fachrechte** (professional rights): the Fachrechte of one selected account.

The button **Konto anlegen** (Create account) sits in the page header. Below the list, tiles show **Benutzerkonten** (accounts), **Aktive Konten** (active), **Deaktivierte Konten** (deactivated) and **Administration** (accounts with the administration role).

### Account list

The table **Lokale Konten** (Local accounts) has the columns **Benutzername** (Username), **E-Mail-Adresse**, **Rollen** (Roles: **Administration**, **Identitätsadministration** or **Keine**), **Status** and **Anmelderichtlinie** (Sign-in policy). Statuses are **Aktiv**, **Deaktiviert** (Deactivated) and **Aktivierung ausstehend** (Activation pending). Each row has **Bearbeiten** (Edit), **Einmalcode ausstellen** (Issue one-time code) and, depending on the status, **Deaktivieren** (Deactivate, for **Aktiv**) or **Reaktivieren** (Reactivate, for **Deaktiviert**). An account with **Aktivierung ausstehend** shows neither of these two buttons, so it cannot be deactivated from the list.

Above the table are **Suche** (search in username and e-mail address), **Rolle**, **Status**, **Anmelderichtlinie** and **Sortierung** (Sort). The sort options include **Zuletzt aktiv** (most recently active) and **Längste Inaktivität** (longest inactive), which find unused accounts. The list shows 15 accounts per page. **Filter zurücksetzen** clears the filters.

### Account editor

**Bearbeiten** opens the editor with **Benutzername** (2 to 20 characters), **E-Mail-Adresse**, **Anzeigename** (Display name, at most 100 characters) and the limits **Speicherlimit (MB)** (storage limit), **Tokenlimit pro Monat** (monthly token limit) and **Transkriptionslimit pro Monat (Sekunden)** (monthly transcription limit in seconds). For an existing account it also shows:

- **Zuletzt aktiv** (last active; **Noch nie** for never), the number of dictations and the storage in use. The last-active time is written at most once every five minutes per account.
- The token and transcription usage of the current month.
- The checkbox **Anmeldesperre aufheben** (Lift login lock), only while the account is locked (see [Login lock](#login-lock)).
- **Anmelderichtlinie** with the hint `Entra ist vorbereitet, aber noch nicht aktiviert.` (Entra is prepared but not yet activated).
- Under **Rollen**: **Administrationsrolle** and **Identitätsadministration**.

**Übernehmen** (Apply) saves and keeps the editor open; **Speichern und schließen** (Save and close) saves and closes it; **Abbrechen** (Cancel) discards the changes. For an account being edited, **Systemaktivität dieses Kontos** (System activity of this account) opens the **System-Auditprotokoll** filtered to that account (`/admin#/system/audit?konto=<id>`).

Every change carries the version of the account that was loaded. If someone else changed the account in the meantime, the change is refused, the current state is reloaded and the page shows `Das Konto wurde inzwischen geändert oder die Änderung ist nicht möglich. Der aktuelle Stand wurde geladen; Ihre Eingaben wurden verworfen.`. Account rows, drafts and one-time codes are kept in memory only; nothing is written to browser storage.

## Creating and activating an account

**Konto anlegen** asks for **Benutzername**, **E-Mail-Adresse**, optionally **Anzeigename**, and the limits. An empty **Speicherlimit (MB)** shows **Systemstandard** (system default): the account then gets the value of the system setting `default_storage_quota_mb`, where 0 means no limit. The other empty limits show **Unbegrenzt** (unlimited). After **Übernehmen**, the editor stays open on the new account; **Speichern und schließen** closes it. New accounts also start with the [defaults for new accounts](system-settings.md#defaults-for-new-accounts) of the system settings (interface theme, color scheme, audio player position).

The new account has the status **Aktivierung ausstehend**, the sign-in policy **Nur lokal**, no password, no role and no Fachrecht. The create request cannot carry roles, status, sign-in policy, a lock lift or the group-creation option: such a request is refused with `409` and `Konto zuerst anlegen und aktivieren.`. A request that names a password is refused with `400 use_recovery_code` (`Bitte einen lokalen Einmalcode ausstellen.`). A request that names roles, pools or clinical grants is refused with `403 explicit_grant_required` (`Fachrechte müssen separat erteilt werden.`). The username and e-mail address must not belong to another account (`Benutzername oder E-Mail-Adresse ist bereits vergeben.`).

### One-time codes

**Einmalcode ausstellen** creates a one-time code:

- For an account with **Aktivierung ausstehend**, the code activates the account (purpose `activate_local`).
- For an active account with a local sign-in policy, the code resets the password (purpose `reset_local`). The reset ends all sessions of the account at once and marks a pending password change. Until the code is redeemed, the account cannot sign in with its old password. Because no form redeems codes (see below), a reset code blocks the sign-in of the account until the code is redeemed through the server endpoint.

The code has 256 bits, is valid for 30 minutes and can be used once. The server stores only its SHA-256 hash and sends the response with `Cache-Control: no-store`. The page shows it once under **Einmalcode (wird nur jetzt angezeigt)** (One-time code, shown only now); **Code ausblenden** hides it. A code that is lost cannot be shown again; issue a new one. A new code voids every older open code of the account, and so do deactivation and a password change.

A code is refused:

- for the account of another administrator (**Administrationsrolle** or **Identitätsadministration**): `Administratorkonten werden nur über die Serverkonsole wiederhergestellt. Ein Einmalcode kann hier nicht ausgestellt werden.`. The page refuses this before sending for every account with the **Administrationsrolle**. A refusal by the server is recorded in the audit log.
- for a deactivated account or an account whose sign-in policy is not local: `Für dieses Konto ist kein lokaler Einmalcode möglich.`; for an account without account policy: `Für dieses Konto fehlt eine Anmelderichtlinie.`.

**Not available: no form redeems the code.** The server accepts the redemption as a `POST` to `/login` (JSON or form data) with the fields `purpose` (`activate_local` or `reset_local`), `code`, `new_password` and `confirmation`. `confirm_password` is accepted in place of `confirmation`; a mismatch gives `400 password_mismatch`. The request needs the session cookie and a CSRF token of the same session (form field `csrf_token` or header `X-CSRFToken`; `GET /api/csrf-token` returns one). The clinic login page only offers username or e-mail address and password; it has no form for this request. A successful redemption sets the password, activates a pending account, clears the login lock of the account and signs the browser out (`204`); the user then signs in normally. An invalid, expired or used code gives `Der Einmalcode ist ungültig, abgelaufen oder wurde bereits verwendet.` and counts as a failed sign-in of the account it belongs to. A password that the password policy rejects leaves the code unused. A signed-in browser gets `409 sign_out_first` (`Bitte zuerst abmelden.`).

**Administrator accounts: no one-time code from the web.** The web refuses one-time codes for other accounts that currently hold **Administrationsrolle** or **Identitätsadministration**, and the console commands are not registered. Another identity administrator can lift a login lock on an administrator account with **Anmeldesperre aufheben** (see [Login lock](#login-lock)). To give an administrator a new password, another identity administrator must first remove the roles (audited as `admin.user.role_changed` and `admin.user.changed`), issue a reset code, and set the roles again after the code has been redeemed. Keep at least two active local identity administrators.

## Passwords

- Length 15 to 128 Unicode code points. There are no composition rules, and the password is not trimmed or truncated.
- A built-in blocklist of common passwords applies, compared after Unicode normalization and case folding, and again with all whitespace removed. `CLINIC_PASSWORD_BLOCKLIST_FILE` (unset by default) names an additional, site-specific blocklist: one entry per line, lines starting with `#` are comments.
- Hashes use `scrypt:131072:8:1`. An upstream bcrypt hash is accepted and replaced with the current scheme after the next successful sign-in.
- No administration role sets passwords in clinic mode. Passwords are set only by redeeming a one-time code, by the bootstrap script, or by the user.
- A signed-in user changes their own password on the account page; the current password is required, all other sessions end and open codes are voided. The dialog there still shows the upstream hint of at least 8 characters; the server refuses passwords shorter than 15 characters.

## Editing roles

**Administrationsrolle** and **Identitätsadministration** are set in the editor under **Rollen**.

- Removing the **Administrationsrolle** also removes **Identitätsadministration**.
- A change of either role ends all sessions of the changed account.
- The last active, locally usable identity administrator cannot be deactivated, lose a role or be switched to **Nur Entra**: `Zuerst eine weitere lokale Identitätsadministration aktivieren.` (First activate another local identity administrator).
- `POST /admin/users/<id>/toggle-admin` with the body `{"expected_version": <version>}` switches the **Administrationsrolle** through the same checks. It is refused for the caller's own account (`400 invalid_role_target`).

## Deactivating instead of deleting

Accounts are never deleted in clinic mode. **Deaktivieren** sets the status **Deaktiviert**, ends all sessions of the account and voids its open one-time codes. A deactivated account cannot sign in. Its dictations, history and audit rows stay.

**Reaktivieren** sets the status back to **Aktiv**, or to **Aktivierung ausstehend** if the account has no password.

`DELETE /admin/users/<id>` is locked by the feature lock `clinical_user_hard_delete` and answers `403 clinic_feature_disabled`; the handler itself also refuses with `disable_instead` (`Bitte das Konto deaktivieren.`).

## Limits

The three limit fields keep the upstream per-user budgets. An empty field means no limit.

- **Speicherlimit (MB)**: storage quota in megabytes. An upload that would exceed it is refused (`507`, code `storage_quota_exceeded`); recordings whose audio was removed do not count.
- **Tokenlimit pro Monat**: token budget of the current month for requests to the text model, for example titles, summaries, chat and inquire. Chat, inquire and summaries are available only with a local text model (see [Model Configuration](model-configuration.md)). From 80 % the server writes a warning to its log; at 100 % the request is refused with the budget message.
- **Transkriptionslimit pro Monat (Sekunden)**: transcription budget in seconds. The budget of the recording owner is checked when a transcription job runs; at 100 % the recording fails with the budget message, without retry.

The page accepts whole numbers from 1 to 2147483647. The upstream rules for the token budget (steps of 10,000, at least 100,000) and the upstream transcription budget in minutes are not used here. Upstream cost estimates per cloud connector do not apply: the clinic build uses no cloud ASR. In clinic mode the `openai_whisper` connector accepts only a local or private address; `ASR_BASE_URL` is not checked and must point to a local service (see [Model Configuration](model-configuration.md)).

## Login lock

The system settings `login_max_failed_attempts` (default 5, 0 turns the lock off) and `login_lockout_minutes` (default 15) apply; see [System Settings](system-settings.md). After that many failed sign-ins of an account from one client address, sign-ins of the account from that address are refused for that many minutes. A second, account-wide counter locks the account from every address at four times the per-address threshold. A locked sign-in gets the same message as a wrong password, `Anmeldung fehlgeschlagen.` (Sign-in failed).

An identity administrator lifts the lock in the editor, also for an administrator account: open **Bearbeiten**; a locked account shows the checkbox **Anmeldesperre aufheben**. Tick it and save with **Übernehmen** or **Speichern und schließen**. The account list does not show the lock. A successful redemption of a one-time code also clears it.

## Sign-in policy and identity binding

Each account has an **Anmelderichtlinie** (sign-in policy):

| Label | Value | Meaning |
|---|---|---|
| **Nur lokal** (Local only) | `local_only` | Local password. Default for every new account. |
| **Lokal und Entra** (Local and Entra) | `local_and_entra` | Local password and Entra. Prepared, disabled. |
| **Nur Entra** (Entra only) | `entra_only` | Entra only. Prepared, disabled. |
| **Nur Gerät** (Device only) | `device_only` | No local password sign-in. |
| **Keine Anmelderichtlinie** (No sign-in policy) | none | Account without clinic account policy; no local sign-in. |

A local sign-in needs the status **Aktiv**, a stored password and the policy **Nur lokal** or **Lokal und Entra**. In the editor only **Nur lokal** can be selected; for an account without policy the field is locked. The server refuses every policy change unless the target is **Nur Entra** and the account has an active Entra link; such a link cannot be created while Entra is disabled. The page then shows `Diese Anmelderichtlinie ist für das Konto nicht verfügbar.`. **Lokal und Entra** cannot be set through the web at all.

Identity binding follows the clinic specification: every sign-in method uses the same internal user id, and accounts are never linked by e-mail address. In the prepared Entra profile, linking is explicit: an identity administrator approves the link, and the user starts it after a fresh local sign-in. There is no e-mail matching, and no web path links another administrator. This workflow has API routes but no page, and the account list shows no link status. The Entra providers are disabled and their routes answer `422` with the title `Anbieter nicht aktiviert`. The login page shows **Lokale Anmeldung: Aktiv** and **Einmalanmeldung: Nicht eingerichtet** (single sign-on not set up), and the upstream OIDC single sign-on routes refuse in clinic mode. See [Sign-in and identity providers](sso-setup.md).

## Existing accounts without account policy

Accounts created before clinic mode, or by the upstream `ADMIN_*` variables, have no clinic account policy. They appear with **Keine Anmelderichtlinie** and cannot sign in. An identity administrator adopts such an account with:

```http
PUT /admin/users/<id>
Content-Type: application/json

{"adopt_existing_local": true, "expected_version": 0}
```

The body must contain exactly these two keys. The usual write rules apply: identity administrator, recent sign-in, CSRF token. The account needs a stored bcrypt or scrypt password hash and no external identity (no upstream SSO link, no Entra link or link request). The adopted account gets the status **Aktiv** and the policy **Nur lokal**; **Identitätsadministration** stays off, and its administration flag is not changed. The stored password is kept; the clinic password policy is not checked for it. Ask the user to set a new password on the account page after the first sign-in. The page has no control for this; it is an API call. See step 6 of the [Migration Guide](migration-guide.md#6-adopt-existing-accounts).

## Directory comparison (Verzeichnis)

The page **Verzeichnis** (`/admin#/verzeichnis`) shows the heading **Verzeichnisabgleich** (directory comparison) with `Der Verzeichnisabgleich ist nicht aktiviert.` and the button **Lokale Konten verwalten** (Manage local accounts), which opens **Benutzer und Rollen**. Without identity rights it shows `Der Verzeichnisabgleich erfordert Rechte zur Identitätsverwaltung.`.

Status: Prepared, disabled. No directory provider is registered in the application, so the capability answer is always "not available", and preview and apply answer `503` with `Verzeichnis nicht verfügbar`. The prepared comparison works on directory object IDs that the identity administrator confirms. The preview lists new identities, display-name changes, accounts to deactivate and pool-membership changes for managed pools (**Poolzuordnungen**, pool assignments). Apply updates display names, deactivates accounts and changes memberships. It refuses to create accounts (`422 explicit_link_required`), never deletes an account, and never deactivates the acting identity administrator's own account or changes its pool memberships.

Open gap WG-ADM-009: reading the local Windows accounts of a workstation has no permitted web path. The permitted alternatives are a server-side directory comparison and local application accounts.

## Pool memberships (Poolbezüge)

The section **Poolbezüge** shows, for the account chosen under **Benutzer**, its **Autorenpools** (author pools) and **Schreibkräfte-Pools** (staff pools), or `Keine Poolzugehörigkeit` (no pool membership). It is read only: `Mitgliedschaften werden unter «Pools verwalten» geändert.` (Memberships are changed under Manage pools). A member of a staff pool that is linked to an author pool gets **Ansehen** (View) on the dictations of that author pool; every other action needs a Fachrecht. Pools, memberships and pool leads are described in [Group Management](group-management.md).

## Fachrechte (professional rights)

Only an active site administrator changes Fachrechte; **Identitätsadministration** is neither needed nor sufficient. Active identity administrators and the account itself may read the list of an account; nobody else may.

To add an entry:

1. On **Benutzer und Rollen**, open the section **Fachrechte** and choose the account under **Benutzer**. The panel **Berechtigungen** (Permissions) shows `Fachrechte eines Benutzers: erlauben oder verweigern, je Geltungsbereich und Aktion. Eine Verweigerung gewinnt immer.`
2. Set **Geltungsbereich** (Scope): **Eigene Diktate** (own dictations), **Pool** with an **Autorenpool** (author pool), or **Alle Diktate** (all dictations).
3. Set **Wirkung** (Effect): **Erlauben** (Allow) or **Verweigern** (Deny).
4. Choose at least one of the **Aktionen** (Actions), for example **Freigeben** (Release) or **Originalaudio exportieren** (Export original audio).
5. Click **Speichern** (Save).

**Entziehen** (Revoke) removes an entry from **Bestehende Einträge** (Existing entries). If the **Stand** (revision) changed in the meantime, the write is refused (`409`) and the page shows `Der Stand ist veraltet. Bitte den aktuellen Stand laden und die Änderung wiederholen.` with **Aktuellen Stand laden** (Load current state). A change applies to the next request of the account. Fachrechte cannot delegate pool administration, rights administration or statistics rights (see [System Statistics](statistics.md)). They do reach beyond single dictations in one respect: an allow entry with scope **Pool** or **Alle Diktate** that names **Bearbeiten** or **Freigeben** also gives authority over the shared vocabulary of the author pool; see [Effects on shared pool resources](group-management.md#effects-on-shared-pool-resources).

The safe defaults, the precedence of deny entries, the preview **Wirksames Ergebnis nach dem Speichern** and the further actions that only the API accepts are described in [Group Management](group-management.md#fachrechte-professional-rights).

### Release (Freigeben)

Nobody holds **Freigeben** by default, accounts with the **Administrationsrolle** included. It is needed for final release (**Endgültig freigeben**), for accepting or rejecting in the acceptance step (**Abnahme**) and for reopening; **Zur Abnahme einreichen** (submit for acceptance) and reassignment need **Bearbeiten** and **Freigeben**. Release gates, the KIS context and the acceptance policy are described in [Group Management](group-management.md#release-freigeben-and-acceptance-abnahme).

### Original-audio export (Originalaudio exportieren)

**Originalaudio exportieren** is separate from **Exportieren** (text export). It is not an own default, and the view and play access of site administrators does not include it. The routes that check it and the unwired mail draft (open gap WG-EXP-009) are described in [Group Management](group-management.md#original-audio-export-originalaudio-exportieren).

## What is audited

Account administration writes these events to the audit log (see [Audit Log](audit-log.md) for the full catalog):

| Event | When | Details |
|---|---|---|
| `admin.user.created` | Account created | `username`, `is_admin` (always `false` here) |
| `admin.user.changed` | Profile, limits, status, roles or sign-in policy changed; one-time code issued; code refused for an administrator; account adopted | `fields`: the names of the changed fields only, never values; for codes `activate_local` or `reset_local`, for a refusal `recovery_refused`, for adoption `local_account_policy_adopted` |
| `admin.user.role_changed` | **Administrationsrolle** changed | `old`, `new` |
| `admin.user.unlocked` | Login lock lifted | none |
| `auth.password.changed` | Code redeemed, or own password changed | none |
| `auth.login.succeeded` | Local sign-in | `method` (`password`), `remember` (`false`) |
| `auth.login.failed` | Wrong password, locked account, invalid code, wrong current password | `reason` (`bad_password`, `locked`, `bad_recovery_code`, `bad_current_password`), `method` (`password`, `recovery`, `password_change`) |
| `auth.login.locked` | Login lock set | `minutes` |
| `recording.content.read` | An active site administrator opens a transcript through the admin route, or gets another user's row in the worklist | `part`: `transcript` or `worklist` |
| `recording.media.played` | An active site administrator plays audio through the admin route | none |

A change of **Identitätsadministration** alone appears only as `admin.user.changed` with the field `is_identity_admin`.

Rows are written while `audit_log_enabled` is on (default on); `recording.media.played` only while `audit_log_record_views` is on; `auth.login.failed` is written at most once per minute per address and account (see [Audit Log](audit-log.md)).

Not in the audit log:

- Changes of Fachrechte, pools, memberships and pool leads. They are written with their details to the separate pool history, which the application keeps append-only and which no page or route displays. The application log has a line per change (`clinic pool: <change> <entity> <id> by <actor>`).
- The bootstrap of the first administrator (log line only).

The events `admin.user.password_set` and `admin.user.deleted` do not arise in clinic mode, because no administration role sets passwords and accounts are not deleted.

## Upstream user features not in the clinic build

These parts of the upstream page do not apply in clinic mode (Upstream only):

- **Add User** with a password, and admin-set passwords in the edit dialog.
- Deleting a user with all recordings.
- The public sharing permission (globe icon); public sharing is removed from the fork.
- **Can create groups** (upstream; in the clinic editor the checkbox **Gruppen erstellen**, Create groups, under **Rollen**): the option appears only while the feature `internal_sharing` is unlocked, which the clinic configuration does not do. Upstream groups and teams are locked; see [Group Management](group-management.md).
- The user table with sortable columns, the **Last active** column and **View activity**; replaced by the account list, **Zuletzt aktiv** in the editor and **Systemaktivität dieses Kontos**.
- The scoped user list for group admins: in clinic mode, accounts without the **Administrationsrolle** cannot open `/admin`.
- Linking and unlinking OIDC single sign-on on the account page.

---

Next: [Group Management](group-management.md) →
