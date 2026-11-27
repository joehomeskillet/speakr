# Local sign-in and identity providers

[Deutsch](../de/admin-guide/sso-setup.md)

With `CLINIC_MODE` enabled, sign-in is the normal web sign-in with local accounts. The login page shows **Lokale Anmeldung: Aktiv** and **Einmalanmeldung: Nicht eingerichtet**. Microsoft Entra ID may follow later; it does not work today. Prepared Entra and LDAP server-side code exists, but application startup leaves it disabled, and no administration setting or environment switch completes its integration.

Windows single sign-on is not offered in this fork: no integrated Windows authentication (IWA), no Negotiate and no Kerberos. The browser never signs a user in from a workstation's Windows account.

## Local access

Use a username or email address (the account's sign-in name; no mail is ever sent) and the local password. Sign-in requires an active clinic account policy, a stored supported password hash and policy **Nur lokal** or **Lokal und Entra**. A pending password change or login lock prevents access. **Keine Anmelderichtlinie** means that an existing account has no clinic account policy; it does not identify an external provider.

Clinic sign-in does not use **Remember me**. Sessions are tied to the account's security version. Deactivation, a reset-code issue and relevant role or sign-in-policy changes invalidate existing sessions. Sensitive identity administration additionally requires a local login within the last five minutes.

New accounts use **Nur lokal**. Entra policy choices in the account editor do not enable Entra. **Nur Gerät** and **Nur Entra** do not permit local password sign-in. Disabling an external provider does not silently restore local-password access to an Entra-only account.

The clinic fork does not offer self-registration, email verification or password reset by email. Identity administration creates accounts and issues local one-time codes. The login endpoint supports code redemption, but the login page currently has no redemption form. See [User management](user-management.md) for bootstrap, activation, lock recovery and account adoption.

## Account identity and permissions

- Every sign-in method must resolve to the existing internal `user.id`; dictation ownership and assignments stay attached to that ID.
- There is no linking by matching email address, UPN or display name, and no account creation on first external sign-in.
- External roles or group claims do not automatically grant clinic roles, pool membership or dictation permissions.
- Identity administration and clinical permissions remain separate. An identity-administration flag alone grants no dictation access.

## Upstream sign-in and tokens

The upstream OIDC sign-in and account-linking controls are inactive in clinic mode. `ENABLE_SSO` and `SSO_*` do not enable the clinic identity adapters. The clinic fork also does not offer general API v1 or SSO token exchange for arbitrary applications; tokens are reserved for the import agent. The system ceilings keep `sso_api_tokens_enabled` and query-parameter token access disabled.

## Prepared Entra adapter

The Entra code prepares authorization-code authentication with PKCE, state, nonce, issuer/tenant checks and an explicit link to an existing clinic account. The application mounts it with a disabled `EntraConfig` and a refusing exchange function. The example files `auth.hybrid.example.json` and `auth.local.example.json` are not loaded by that application wiring. Editing them does not activate sign-in.

The prepared linking flow requires an explicit identity-administration request and confirmation by the target user. It does not match accounts by email. A confirmed transition from local-only to Entra-only preserves the internal user ID, removes the local password hash, invalidates recovery codes and ends sessions. It protects the last locally usable identity administrator. These are prepared behaviors, not instructions to activate a live directory.

There is no Entra lifecycle synchronization in the running application wiring. Disabling a person only in Entra is not a substitute for deactivating their clinic account. Synthetic provider tests do not demonstrate tenant sign-in, Conditional Access, MFA or offboarding against a real provider.

## LDAP and Windows sign-in

The LDAP module validates a disabled server-side federation profile. The application does not instantiate a directory connection or consume end-user directory passwords. The profile does not provide live LDAP sign-in or role synchronization.

Windows single sign-on is not offered in this fork (see above). Client-supplied identity headers cannot create a sign-in. No browser operation reads a workstation's local Windows accounts; WG-ADM-009 remains open.

## Provider and directory status

`GET /api/clinic/v1/identity/providers` reports `entra`, `iwa` and `ldap` with `enabled: false` in the current application wiring. The `iwa` entry is a legacy placeholder that stays disabled; it is not a sign-in method of this fork. A disabled provider entry point returns `422 CAPABILITY_UNAVAILABLE` with **Anbieter nicht aktiviert**. These are clinic identity routes, not general API-token access.

Under **Verwaltung → Konten und Pools → Verzeichnis** (`/admin#/verzeichnis`), identity administrators see that directory comparison is not activated. **Lokale Konten verwalten** returns to account administration. The directory capability reports `directory_available: false`, `local_accounts: true`, `local_windows_accounts: "unavailable"` and gap `WG-ADM-009`. No directory provider is registered; preview and apply return `503 ldap_capability_unavailable`.

Prepared directory reconciliation uses immutable external object IDs and a reviewed preview; it does not match by name or email. It is not a demonstrated directory import or offboarding process. Keep local account administration as the operational path.

## Evidence limits

Provider activation needs completed wiring and real acceptance evidence for the chosen tenant or directory. Local fixtures and synthetic keys do not establish Entra or LDAP acceptance. Local-account security acceptance also remains a separate deployment requirement; this handbook describes code behavior, not production approval.

Local sign-in, failed attempts, lockout and password changes have audit event types. Prepared provider-link operations have separate SSO event types. See [Audit log](audit-log.md) for configuration and coverage, and [User management](user-management.md) for current account procedures.
