# User management

[Deutsch](../de/admin-guide/user-management.md)

With `CLINIC_MODE` enabled, open **Verwaltung → Konten und Pools → Benutzer und Rollen** (`/admin#/benutzer`). This replaces upstream user administration. The page combines **Konten** (accounts), read-only **Poolbezüge** (pool memberships) and **Fachrechte** (clinical permissions). The German labels below match the clinic interface.

## Roles and access

| Task | Required access |
| --- | --- |
| Open administration and read the account list | **Administrationsrolle** (site administration) |
| Create or change accounts, issue one-time codes, lift login locks or adopt an existing account | **Administrationsrolle** and **Identitätsadministration**, an active clinic account, no pending password change, a valid local session and a login within the last 300 seconds |
| Change clinical permissions or manage pools | Active site administrator; identity administration alone does not grant this authority |
| Coordinate assignment within a writing pool | **Leitung** (pool lead); this does not open `/admin` |

An active site administrator can view and play clinic dictations and read their corrections. This access does not grant editing, document export, final release, archiving or original-audio export on other people's dictations. **Identitätsadministration** adds no dictation access. See [Pools and permissions](group-management.md) for scoped grants and the administrator read-access exception to deny entries.

For a refused sensitive account change, sign out, sign in again and repeat it. A missing role returns `403 unauthorized`; an expired fresh-login window returns `403 reauth_required`. Reload after a version conflict and review the current account before saving again.

## Creating the first administrator

Run the bootstrap script once in the application environment, using the same database and deployment configuration as the application:

```bash
python scripts/clinic_bootstrap_admin.py --username <username> --email <email-address>
```

The password comes from `--password-stdin`, the environment variable named by `--password-env` (default `CLINIC_BOOTSTRAP_PASSWORD`), or a hidden terminal prompt. Do not place a password in a command-line argument. The script creates an active, local-only account with both administration flags. It grants no additional clinical permissions. A bootstrap marker or an existing identity administrator makes a repeated run leave the accounts unchanged.

Do not use upstream `ADMIN_USERNAME`, `ADMIN_EMAIL` and `ADMIN_PASSWORD` as clinic bootstrap. The upstream Docker script creates an account without clinic account policy, so it cannot sign in until explicitly adopted. Its username and email are already occupied; use different values for the first clinic identity administrator.

The code also defines `flask clinic-identity` console commands, but application startup does not register them. Do not assume that those commands are available in the deployed application.

## Accounts and one-time codes

1. Choose **Konto anlegen**. Enter a unique username, email and optional display name. The account starts as **Aktivierung ausstehend**, with **Nur lokal**, no password and no administration role.
2. Choose **Einmalcode ausstellen**. A pending account receives an activation code; an active local account receives a password-reset code.
3. Deliver the code through the site's approved confidential channel. It is shown once, expires after 30 minutes and can be used once. The server stores its hash. A new code invalidates older open codes.
4. The account holder redeems the code, sets and confirms a password, then signs in normally. Check that the account now shows **Aktiv**.

**Current interface gap:** the sign-in page has no code-redemption form. The signed-out, CSRF-protected `POST /login` endpoint accepts `purpose` (`activate_local` or `reset_local`), `code`, `new_password` and `confirmation`. It returns `204` on success and leaves the browser signed out. Use the site's approved activation procedure; creating the account alone does not make it usable.

Issuing a reset code ends existing sessions and prevents sign-in with the old password until redemption. Confirm that the holder can complete the activation procedure before issuing it. Disabled accounts and accounts without a local sign-in policy cannot receive a local code.

The web interface refuses recovery codes for administrator accounts; the server specifically prevents recovery of another account holding either administration flag. The defined console recovery command is not registered at application startup. Treat administrator password recovery as an operator procedure requiring a verified recovery path; the web UI does not supply one. An identity administrator can still lift an administrator account's login lock. Keep another usable local identity administrator.

## Passwords and login locks

Passwords must have 15–128 Unicode code points and must not match the blocklist. No composition rule applies, and passwords are not trimmed. `CLINIC_PASSWORD_BLOCKLIST_FILE` can add site-specific entries. New hashes use `scrypt:131072:8:1`; a valid legacy bcrypt hash is upgraded after successful sign-in.

Administrators do not set other users' passwords. Users can change their own password on the account page with the current password. The server's 15-character minimum applies even where an upstream hint still says eight. Changing a password invalidates open codes and other sessions.

`login_max_failed_attempts` defaults to 5 and `login_lockout_minutes` to 15. An account is locked per client address at the configured threshold and account-wide at four times that threshold. Setting the attempts value to 0 disables the lock. Failed sign-ins use the same generic message. In the account editor, **Anmeldesperre aufheben** lifts an existing lock after identity verification.

## Editing, deactivation and limits

Search or filter the account list, then choose **Bearbeiten**. The editor shows profile fields, limits, **Zuletzt aktiv**, usage, sign-in policy and administration flags. **Übernehmen** saves and keeps it open; **Speichern und schließen** saves and closes it. **Systemaktivität dieses Kontos** opens the system audit log filtered to the account.

Removing **Administrationsrolle** also removes **Identitätsadministration**. Changing either flag ends the target account's sessions. The last active, locally usable identity administrator is protected from deactivation, role removal and a transition to Entra-only.

Use **Deaktivieren** to withdraw access. It ends sessions and invalidates open codes while retaining dictations, history and audit rows. **Reaktivieren** restores **Aktiv**, or **Aktivierung ausstehend** when no password exists. The clinic build does not offer account deletion.

| Field | Unit and clearing behavior |
| --- | --- |
| **Speicherlimit (MB)** | Megabytes; blank on creation uses `default_storage_quota_mb`, blank when editing removes the individual limit |
| **Tokenlimit pro Monat** | Monthly text-model tokens; blank removes the limit |
| **Transkriptionslimit pro Monat (Sekunden)** | Monthly transcription seconds, not minutes; blank removes the limit |

The form accepts positive whole numbers up to 2147483647. Zero is not a valid form entry; the account API also accepts zero to clear an individual limit. Review actual consumption in [System statistics](statistics.md). A budget refusal is not proof of missing processing capacity.

## Sign-in policy and existing accounts

**Nur lokal** is the supported new-account policy. **Lokal und Entra** and **Nur Entra** describe prepared identity policies; Entra is disabled. **Nur Gerät** does not allow local password sign-in. **Keine Anmelderichtlinie** means no clinic account policy exists, not that a directory provider has been detected. There is no email-based account linking. See [Sign-in and identity providers](sso-setup.md).

For an existing local account without clinic policy, a freshly authenticated identity administrator can submit this CSRF-protected request:

```http
PUT /admin/users/<id>
Content-Type: application/json

{"adopt_existing_local":true,"expected_version":0}
```

The body must contain exactly these two keys. Adoption requires a supported local password hash and no external identity or link request. It retains the internal ID, password, ownership and existing site-administration flag, creates an active local-only policy and does not add identity-administration rights. Inspect existing administrative flags first: activating a site administrator enables its cross-dictation reads. Ask the holder to change the legacy password after sign-in. There is no adoption button.

## Pool memberships and clinical permissions

**Poolbezüge** shows the selected account's author and writing pools; memberships are edited on the pool pages. In **Fachrechte**, choose the user, **Geltungsbereich** (**Eigene Diktate**, an author **Pool**, or **Alle Diktate**), **Wirkung** (**Erlauben** or **Verweigern**) and explicit actions, then save. **Entziehen** revokes an entry. Reload on a stale revision.

Authors can view, play and edit their own dictations unless a matching deny applies. Other actions need explicit permissions, including document export, final release, archiving and **Originalaudio exportieren** (`audio_export`). Original-audio export is separate from text export and is not included in administrator playback access. Pool membership or assignment alone does not grant editing. See [Pools and permissions](group-management.md) before granting additional scope.

## Audit and removed features

Account creation, changes, role changes, login unlocks, one-time-code issuance and password changes have audit event types. Account changes record field names, not password or code values. Pool and clinical-permission changes use the separate append-only pool history. The bootstrap writes an application log line. See [Audit log](audit-log.md) for configuration and coverage limits.

The clinic fork does not offer self-registration, email verification, password reset by email, public share links or internal shares. General API v1 access is removed; tokens are reserved for the import agent. These upstream functions cannot be enabled by account permissions.
