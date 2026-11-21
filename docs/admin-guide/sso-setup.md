# Sign-in and identity providers
<small>[Deutsch](../de/admin-guide/sso-setup.md)</small>

This page describes how people sign in to the clinic build and the state of each identity provider. Everything on this page applies when `CLINIC_MODE` is set to `1`, `true`, `yes` or `on`. With clinic mode off, the fork behaves like upstream Speakr, including its OIDC single sign-on.

In clinic mode, sign-in is local only. Microsoft Entra ID, an LDAP federation and Windows single sign-on (IWA) exist as prepared server-side profiles. All three are disabled, and no setting, environment variable or configuration file switches them on. The upstream OIDC single sign-on is inert.

## Status overview

| Function | Status | Note |
|---|---|---|
| Local sign-in with username or e-mail address and password | Available | The only working sign-in method. |
| Upstream OIDC single sign-on (`ENABLE_SSO`, `SSO_*`) | Upstream only | The routes refuse in clinic mode. |
| SSO API access tokens and `POST /api/v1/tokens/exchange` | Upstream only | Locked together with the personal API tokens. |
| Microsoft Entra ID | Prepared, disabled | Synthetic local tests only. No real tenant, no clinical approval. Gate G-ENTRA is open. |
| LDAP federation | Prepared, disabled | Profile check only. Gate G-LDAP is open. |
| Windows single sign-on (IWA) | Prepared, disabled | Contract code only. Gate G-IWA is open. |
| Directory comparison (**Verzeichnis**, directory) | Prepared, disabled | No directory provider is registered. |
| Reading the local Windows accounts of a workstation | Open gap | WG-ADM-009. |

None of this is a clinical approval. Gate G-IDENTITY (security acceptance of local accounts, bootstrap, passwords, sessions and administration) is also open. It blocks local sign-in with real data until that acceptance is given.

## Principles for every sign-in method

- **One account, one key.** Every sign-in method resolves to the existing internal user id (`user.id`). Dictations, pools, grants and releases reference this id, and audit rows name actor and target by this id, not by an e-mail address or an external subject.
- **No e-mail auto-linking.** A prepared external sign-in reaches an account only through an explicit mapping: for Entra, an issuer-and-subject link that an active user with **Identitätsadministration** requests and the target user confirms; for LDAP, a subject mapping by an active user with **Identitätsadministration** (prepared only, held in process memory only); for IWA, the Windows SID in a mapping list owned by the server configuration. E-mail address, UPN and display name are never used to find an account.
- **No automatic account creation.** There is no self-registration and no account creation on a first external sign-in. Accounts are created by an active account that holds both **Administrationsrolle** and **Identitätsadministration**; see [User management](user-management.md).
- **Rights come from the application.** Claims of a provider (roles, groups) never grant roles, pools or dictation access. Identity roles do not grant dictation access either. In **Benutzer und Rollen** > **Fachrechte** the UI states: **Der Zugriff auf ein konkretes Diktat bleibt zusätzlich an Benutzer, Pools und Objektberechtigungen gebunden. Eine Identitätsrolle gibt keinen Diktatzugriff.** (Access to a specific dictation also stays bound to user, pools and object permissions. An identity role gives no dictation access.)

## Local sign-in (Available)

The clinic sign-in page is always rendered without an SSO button, whatever `ENABLE_SSO` says. Its side panel **Identität und Zugriff** (identity and access) carries the tag **Lokal** (local) and the rows **Lokale Anmeldung: Aktiv** (local sign-in: active) and **Einmalanmeldung: Nicht eingerichtet** (single sign-on: not set up).

The form accepts a username or an e-mail address, plus the password. If the entry matches one account by username and another by e-mail address, the sign-in fails. A sign-in succeeds only when all of these hold:

- The account has a clinic account policy. An account created by the upstream Docker variables `ADMIN_USERNAME`, `ADMIN_EMAIL` and `ADMIN_PASSWORD` has none until it is adopted; see [User management](user-management.md).
- The status is **Aktiv** (active).
- A password is stored, as a bcrypt or scrypt hash. Other hash formats never match.
- The sign-in policy (**Anmelderichtlinie**) is **Nur lokal** (local only) or **Lokal und Entra** (local and Entra).
- The account is not under a login lock, per client address or account-wide; see [User management](user-management.md#login-lock).
- No password change is pending. After **Einmalcode ausstellen** (issue one-time code) for an active account, the account cannot sign in, not even with its old password, until the code is redeemed.

Every failure shows the same message, **Anmeldung fehlgeschlagen.** (Sign-in failed.), so an unknown, disabled or mistyped account cannot be told apart. There is no "Remember me": a clinic session is never a persistent sign-in. The session is bound to the account's security version. Deactivating the account, issuing a one-time code to reset the password, a change of the sign-in policy and a change of **Administrationsrolle** or **Identitätsadministration** raise that version and end existing sessions.

A successful sign-in writes the audit event `auth.login.succeeded` with `method` `password`; a failed one writes `auth.login.failed`. The lockout settings `login_max_failed_attempts` and `login_lockout_minutes` are described in [System settings](system-settings.md). The first administrator (**Administrationsrolle** and **Identitätsadministration**) is created once at the server console with `python scripts/clinic_bootstrap_admin.py --username <username> --email <email-address>`. The password comes from `--password-stdin`, from the environment variable `CLINIC_BOOTSTRAP_PASSWORD` (or the one named by `--password-env`) or from a hidden prompt. Account creation, one-time codes, deactivation and the protection of the last identity administrator are described in [User management](user-management.md).

### Sign-in policies (Anmelderichtlinie)

| Value | UI label | Local password | Entra (prepared) | Windows SSO (prepared) |
|---|---|---|---|---|
| `local_only` | **Nur lokal** (local only) | Allowed | No sign-in; only the link workflow | Allowed |
| `local_and_entra` | **Lokal und Entra** (local and Entra) | Allowed | Allowed | Allowed |
| `entra_only` | **Nur Entra** (Entra only) | Refused | Allowed | Refused |
| `device_only` | **Nur Gerät** (device only) | Refused | Refused | Refused |

New accounts get **Nur lokal**. An account without a clinic account policy is shown as **Keine Anmelderichtlinie** (no sign-in policy) and cannot sign in.

In **Benutzer und Rollen** > **Konten** (users and roles > accounts), the editor field **Anmelderichtlinie** lists **Nur lokal**, **Lokal und Entra** and **Nur Entra**, but only **Nur lokal** can be selected. The hint below it reads **Entra ist vorbereitet, aber noch nicht aktiviert.** (Entra is prepared but not yet activated.) The only policy change the server accepts is a switch to **Nur Entra**, for an active account with an active Entra link. While Entra is disabled no link can exist, so no change is possible. **Lokal und Entra** cannot be set through the web at all. A refused change gets `409` with the code `login_policy_not_permitted`; the UI shows **Diese Anmelderichtlinie ist für das Konto nicht verfügbar.** (This sign-in policy is not available for the account.)

The server never moves an account from **Nur Entra** back to a local policy, and it refuses a local one-time code for such an account (**Für dieses Konto ist kein lokaler Einmalcode möglich.**, no local one-time code is possible for this account). Disabling a provider therefore never restores password sign-in for an **Nur Entra** account.

## Upstream OIDC single sign-on (Upstream only)

Upstream Speakr signs users in through an OIDC provider, configured with `ENABLE_SSO` and the `SSO_*` variables (template `config/env.sso.example`). In clinic mode this path is inert:

- `/auth/sso/login`, `/auth/sso/callback` and `/auth/sso/link` redirect with the message **SSO is not configured. Contact the administrator.** (German UI: **SSO ist nicht konfiguriert. Wenden Sie sich an den Administrator.**). This also happens when `ENABLE_SSO=true` and every `SSO_*` variable is set.
- `/auth/sso/unlink` answers `403` with the code `provider_unavailable`.
- The single sign-on controls in the section **Anmeldung & Sicherheit** (Sign-in & security in the English UI) on the account page are hidden; the password section stays.

These variables have no effect on clinic sign-in. The upstream code defaults are in parentheses: `ENABLE_SSO` (`false`), `SSO_PROVIDER_NAME` (`SSO`), `SSO_CLIENT_ID`, `SSO_CLIENT_SECRET`, `SSO_DISCOVERY_URL`, `SSO_REDIRECT_URI`, `SSO_AUTO_REGISTER` (`true`), `SSO_ALLOWED_DOMAINS`, `SSO_DEFAULT_USERNAME_CLAIM` (`preferred_username`), `SSO_DEFAULT_NAME_CLAIM` (`name`), `SSO_DISABLE_PASSWORD_LOGIN` (`false`) and `SSO_REQUIRE_VERIFIED_EMAIL` (`true`).

The clinic template `clinic-spec/config/upstream.env.example` sets `ENABLE_SSO=false`, `SSO_AUTO_REGISTER=false`, `SSO_DISABLE_PASSWORD_LOGIN=false` and `SSO_REQUIRE_VERIFIED_EMAIL=true`. It notes that changing these flags does not make the clinical authentication complete.

So these upstream behaviors do not exist in the clinic build: automatic registration on the first SSO sign-in and the e-mail domain allowlist; automatic linking of an existing account by a matching e-mail address; the administrator password fallback when password sign-in is disabled; "Remember me" for SSO sign-ins; linking and unlinking SSO on the account page.

### SSO API access tokens (Upstream only)

Upstream Speakr lets other apps call its API with a user's OIDC access token, or exchange it for a stored API token. In clinic mode this is locked:

- The feature `personal_api_tokens` is `false` in `clinic-spec/config/dev.defaults.json`. The token routes and `POST /api/v1/tokens/exchange` answer `403` with `clinic_feature_disabled`.
- A request that authenticates with an API token and has no browser session is refused on every non-public route with `403` `clinic_feature_disabled`.
- The setting `sso_api_tokens_enabled` always reads `false`. Saving another value answers `403` `clinic_policy_ceiling`. A conflicting stored value is logged at startup as `clinic policy overrides stored setting sso_api_tokens_enabled=<stored> (effective: False)`.
- `sso_api_allowed_clients`, `sso_api_exchange_clients`, `sso_api_scopes`, `sso_api_client_names`, `sso_api_client_scopes` and their `SSO_API_*` variables have no effect.

Upstream Speakr documentation (behavior with clinic mode off, not an instruction for the clinic build): [SSO Setup (OIDC)](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/sso-setup.md)

## Microsoft Entra ID (Prepared, disabled)

### What exists

- **Adapter.** Authorization code flow with PKCE (`S256`), state and nonce, and an exact match of the redirect URI. The ID token is accepted only with `RS256`; the key is selected by `kid` from the provider key set. The checks are `iss`, `tid`, `aud`, `exp`, `nbf` and `iat` with a clock skew of 60 s (at most 300 s), `nonce` and `sub`. The subject is the `sub` claim. E-mail, UPN and name are ignored.
- **Server-side transport.** Timeouts of 3.05 s (connect) and 5 s (read), responses up to 128 KiB, no redirects, no environment proxies. The discovery document must name exactly the configured endpoints. Keys come from `<authority>/discovery/v2.0/keys`, with at most one key refresh per exchange. The client secret is read from a file at the code exchange only.
- **Provider routes and an explicit link workflow** (below).
- **Evidence.** The tests use synthetic keys, tokens, users and HTTP fixtures only. Their docstrings state that they are not proof of a real Entra sign-in, and the related acceptance entries stay `not_run`.

### Why it cannot be switched on

- The application mounts the provider routes with a default, disabled Entra configuration and a code exchange that always refuses. No admin setting, environment variable or runtime configuration file enables Entra. The transport is not instantiated anywhere in the application.
- The application does not read `clinic-spec/config/auth.hybrid.example.json`, `auth.local.example.json` or the `identity` block of `dev.defaults.json`. Editing them changes nothing.
- Even once wired, the profile counts as enabled only when `mode` is `hybrid`, `entra_enabled` is `true` and `activation.real_provider_approved` is `true`. Otherwise every entry point refuses before any DNS lookup, HTTP request, key fetch or database access.
- Activation needs a code change and the gate G-ENTRA: a real tenant and app registration, explicit links, Conditional Access and MFA, WebView SSO (gate wording), and a measured lifecycle and offboarding path. This page gives no activation steps, because none exist.

There is no lifecycle synchronization with Entra. A lock set only in Entra does not end Speakr sessions. Deactivating the account in Speakr (**Deaktivieren**, deactivate) raises its security version and ends its sessions.

### How a sign-in would map to an account

- An Entra sign-in reaches an account only through an active link (issuer and subject) that the link workflow created. The tenant must match. If the link holds an Entra object id, it is checked on every sign-in.
- The account must be **Aktiv** with **Nur Entra** or **Lokal und Entra**.
- Among active links there is one per issuer and subject, one per account, and one per tenant and object id. Revoked links stay as history. A new link needs a new administrative act.
- A refusal is logged with a fixed code only: `clinic entra: login denied (<code>)` with `no_link`, `tenant_mismatch`, `object_mismatch`, `account_not_active` or `policy_forbids_entra`. No token, code, state, nonce, verifier or secret is ever logged.

### Link workflow (prepared, API only)

No page in the admin UI starts or shows a link. The account list shows no Entra link status, although the specification asks for one. The prepared API works like this:

1. A user with **Identitätsadministration** and status **Aktiv** sends `POST /api/clinic/v1/identity/users/<id>/entra-link-requests` with `object_id`, `expected_version` and their own password, plus the header `X-CSRF-Token`. The answer is `201` with a `request_id`. The target account must be **Aktiv** with **Nur lokal** or **Lokal und Entra**. The event `auth.sso.link_requested` is written.
2. The target user sends `POST /api/clinic/v1/identity/providers/entra/link/start` with the `request_id` and their own password, and receives an `authorization_url`. The request is single use, expires after 600 s, and is bound to hashes of the state and of a value of the user's browser session.
3. The callback `GET /api/clinic/v1/identity/providers/entra/callback` checks that the `oid` claim equals the approved `object_id`, that the target account is still **Aktiv** with **Nur lokal** or **Lokal und Entra**, and that its account version is unchanged since the request. It then binds issuer and subject in one transaction. The event `auth.sso.linked` is written.

After a confirmed link, a **Nur lokal** account becomes **Nur Entra**. Its local password hash is removed, open one-time codes are revoked, and its security version is raised, so its sessions end. The last locally usable identity administrator is protected: the link is refused with a conflict.

Unlinking uses `POST /api/clinic/v1/identity/users/<id>/entra-links/<link_id>/unlink` with `expected_version` (the version of the link). It works while the provider is disabled and needs a session, `X-CSRF-Token` and **Identitätsadministration**. It is refused with `409` while the account is **Aktiv** with **Nur Entra**, because the account would lose every sign-in. The link row is kept as revoked, the security version is raised, and `auth.sso.unlinked` is written.

No web path links or unlinks the Entra identity of another administrator (an account with **Administrationsrolle** or **Identitätsadministration**). Such a request gets a generic `403`.

### Template keys (not loaded)

`clinic-spec/config/auth.hybrid.example.json` shows the shape a later Entra profile would have. It is a template only and is not loaded at runtime. Excerpt:

```json
{
  "mode": "hybrid",
  "entra_enabled": true,
  "entra": {
    "tenant_id": null,
    "client_id": null,
    "secret_file": null,
    "allowed_issuer": null,
    "discovery_url": null,
    "redirect_uri": null,
    "scopes": ["openid", "profile", "email"],
    "pkce": true
  },
  "activation": {
    "real_provider_approved": false,
    "approval_evidence": null,
    "notes": "Template only: null provider settings fail validation; do not deploy until G-ENTRA is approved."
  }
}
```

The prepared code would validate the keys as follows:

| Key | Rule |
|---|---|
| `tenant_id` | A GUID. `common`, `organizations` and `consumers` are refused. |
| `client_id` | Required. |
| `secret_file` | Path of a file that holds the client secret. The secret is never taken from an environment variable. |
| `allowed_issuer` | Exactly `https://<host>/<tenant>/v2.0`, with `login.microsoftonline.com`, `login.microsoftonline.us` or `login.partner.microsoftonline.cn` as host. |
| `discovery_url` | Exactly the issuer followed by `/.well-known/openid-configuration`. |
| `redirect_uri` | `https`, without a fragment. |
| `scopes` | Must contain `openid`; only `openid`, `profile` and `email` are allowed. |
| `pkce` | Must be `true`. |

The same file and `auth.local.example.json` record the identity contract as specification values: `user_key` `existing_user.id`, `self_registration` `false`, `jit_provisioning` `false`, `email_auto_link` `false`, `authorization_source` `application`, `default_account_policy` `local_only`, `default_after_entra_link` `entra_only`, `remove_local_hash_after_entra_only_link` `true` and `allow_implicit_password_fallback` `false`.

## LDAP federation (Prepared, disabled)

The application never binds to a directory and never stores a directory password. A later federation, if G-LDAP is approved, would be an identity provider in front of LDAPS. The code only checks a disabled profile:

- The profile check always reports the profile as disabled. It refuses with `ldap_capability_unavailable` when `enabled` or `ldap_enabled` is `true`, when the provider is not `ldaps_federation`, or when `server_side` is `false`.
- Directory password keys such as `bind_password` or `ad_password` are refused with `end_user_password_rejected`. `claim_role_sync` must be `false`. The lifecycle source is `application`, and the subject attributes are `issuer` and `subject`.
- Subject links are held in process memory only. A subject that contains `@` is refused as unstable. LDAP claims never grant roles or pools.
- The application does not use this profile anywhere. The directory routes only use its error type.

## Windows single sign-on (IWA) (Prepared, disabled)

The Windows SSO settings exist only in code and default to disabled. The application passes no IWA configuration and no replay store, so `POST /api/clinic/v1/identity/providers/iwa/login` answers `422`. The prepared contract:

- It trusts only an assertion that server middleware places in the WSGI key `clinic.iwa.assertion`. Requests that carry `X-IWA-*`, `X-Remote-User` or `X-Forwarded-User` headers are refused.
- The assertion needs an AD SID, is valid for 60 s and is claimed once against replay.
- The SID maps to `user.id` only through an exact, server-owned list. The account must be **Aktiv** with **Nur lokal** or **Lokal und Entra**.

No such middleware exists in the repository. The tests use a synthetic assertion and are not evidence of a Negotiate or AD sign-in.

## Provider status and API answers

`GET /api/clinic/v1/identity/providers` reports the three providers. In the current build all three are disabled:

```json
{"providers": [{"provider": "entra", "enabled": false},
               {"provider": "iwa", "enabled": false},
               {"provider": "ldap", "enabled": false}]}
```

The `ldap` value is fixed to `false` in code.

A disabled provider route answers `422` as `application/problem+json` with the title **Anbieter nicht aktiviert** (provider not activated) and the code `CAPABILITY_UNAVAILABLE`. It does so before any database access, link-request write or outbound call. With clinic mode off, these routes answer `404`.

| Route | Access |
|---|---|
| `GET /api/clinic/v1/identity/providers` | Public (runs before a session exists) |
| `POST .../providers/entra/login`, `GET .../providers/entra/callback`, `POST .../providers/iwa/login` | Public; the handlers check CSRF, state and browser binding themselves |
| `POST .../providers/entra/link/start` | Signed-in session, own password, `X-CSRF-Token` |
| `POST /api/clinic/v1/identity/users/<id>/entra-link-requests` | Signed-in session, **Identitätsadministration**, own password, `X-CSRF-Token` |
| `POST /api/clinic/v1/identity/users/<id>/entra-links/<link_id>/unlink` | Signed-in session, **Identitätsadministration**, `X-CSRF-Token` |

Other answers of these routes: `401` `UNAUTHORIZED` without a signed-in session. A wrong own password gives `403` `FORBIDDEN` without a log line. `409` `REVISION_CONFLICT` for a stale version, a link conflict, an unlink that would disable sign-in, or the protection of the last identity administrator. Other refusals give a generic `403` `FORBIDDEN` and the log line `clinic idp: refused (<code>)`. A body with unknown keys gives `INVALID_PAYLOAD`; an unexpected failure gives `DEPENDENCY_UNAVAILABLE`.

## Directory comparison (Prepared, disabled)

The page **Verwaltung** > **Konten und Pools** > **Verzeichnis** (administration > accounts and pools > directory, `/admin#/verzeichnis`) shows the heading **Verzeichnisabgleich** (directory comparison), the text **Der Verzeichnisabgleich ist nicht aktiviert.** (directory comparison is not activated) and the button **Lokale Konten verwalten** (manage local accounts). An account with **Administrationsrolle** but without **Identitätsadministration** sees **Der Verzeichnisabgleich erfordert Rechte zur Identitätsverwaltung.** (directory comparison requires identity administration rights). Accounts without **Administrationsrolle** do not see the entry **Verzeichnis**.

The directory routes need **Administrationsrolle** and **Identitätsadministration**, an active account, no pending password change and a matching clinic session. A `POST` also needs the header `X-CSRF-Token`. A body that names an actor, a user or a role is refused.

No directory provider is registered. `GET /api/clinic/v1/directory/capability` answers:

```json
{"directory_available": false, "local_accounts": true,
 "local_windows_accounts": "unavailable", "gap": "WG-ADM-009"}
```

Preview (`POST /api/clinic/v1/directory/preview`) and apply (`POST /api/clinic/v1/directory/apply`) answer `503` `ldap_capability_unavailable` with the title **Verzeichnis nicht verfügbar** (directory not available).

The prepared comparison, tested with synthetic snapshots only, works like this:

- It matches only the immutable object id of a directory entry against the object id of an active Entra link. E-mail address, account name and display name are never used.
- The possible actions are `create_pending_user`, `update_display_name` and `deactivate`. Nothing is deleted. Ambiguous entries and entries without an object id get no action.
- Apply is bound to the version and hash of the preview; otherwise it answers `409` `stale_version` or `stale_state`. `create_pending_user` is refused with `422` `explicit_link_required`. An identity administrator never deactivates or re-pools their own account through it. Selected changes go through the same account and pool services as manual changes.
- The review view appears only when a provider exists. Its labels include **Vorschau laden** (load preview), **Neue Identitäten** (new identities), **Änderungen** (changes), **Zu deaktivierende Identitäten** (identities to deactivate), **Poolzuordnungen** (pool assignments), **Auswahl prüfen** (check selection), **Änderungen bestätigen** (confirm changes), **Verbindlich anwenden** (apply) and **Es wird nichts gelöscht, Konten werden nur deaktiviert.** (nothing is deleted; accounts are only deactivated).

Reading the local Windows accounts of a workstation has no permitted browser or server path. The web-parity gap WG-ADM-009 stays open; the permitted web route is a server-side directory comparison plus local application accounts.

## Audit events and log lines

| Event | Written when | Detail keys |
|---|---|---|
| `auth.login.succeeded` | A sign-in succeeds | `method` (`password`; prepared: `entra`, `iwa`), `remember` (always `false` in clinic mode) |
| `auth.login.failed` | A sign-in, a code redemption or the check of the current password fails | `reason` (`bad_password`, `locked`, `bad_recovery_code`, `bad_current_password`), `method` (`password`, `recovery`, `password_change`) |
| `auth.login.locked` | The limit of wrong passwords is reached, per client address or account-wide | `minutes` |
| `auth.password.changed` | A one-time code is redeemed or the user changes the password | none |
| `auth.sso.link_requested` | Identity administration requests an Entra link (prepared) | none |
| `auth.sso.linked` | An Entra link is confirmed (prepared) | none |
| `auth.sso.unlinked` | An Entra link is revoked | none |

The upstream SSO routes that also write `auth.sso.linked` and `auth.sso.unlinked` are not reachable in clinic mode. The full catalog is in [Audit log](audit-log.md).

The identity code logs fixed codes only, for example `clinic entra: link <id> for user <id> created by <id>`, `clinic entra: link <id> revoked by <id>`, `clinic entra: login denied (<code>)` and `clinic idp: refused (<code>)`.

## Activation gates

| Gate | Missing evidence | Status |
|---|---|---|
| G-IDENTITY | Acceptance of local accounts, bootstrap, passwords, sessions and administration; MFA and protection-need decision; trusted ingress networks | Open |
| G-ENTRA | Real tenant and app registration, explicit links, Conditional Access and MFA, WebView SSO, measured lifecycle and offboarding path | Open |
| G-LDAP | Approved identity provider and LDAPS contract, TLS, mapping and lifecycle check | Open |
| G-IWA | Verified Windows single sign-on and confirmed AD comparison, separate from Entra | Open |

Mocks, fixtures and synthetic tests are not evidence of a working Entra, LDAP or Windows single sign-on.
