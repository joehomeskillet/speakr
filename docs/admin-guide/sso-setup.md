# Local sign-in and identity providers

[Deutsch](../de/admin-guide/sso-setup.md)

Use the local sign-in form with the account created by the identity administrator. **Einmalanmeldung: Nicht eingerichtet** means the instance offers no active single sign-on. An email address matching an external directory entry does not join the accounts. The upstream OIDC configuration, account-linking controls and SSO API tokens are inactive in clinic mode.

The code prepares an explicit Entra link to a particular existing account. On a confirmed transition to Entra-only, the internal user ID and dictation assignments are preserved, the local password and outstanding recovery codes are invalidated, and existing sessions end. An administrator must preserve a usable local identity administrator. This is a prepared transition, not a procedure to enable against a live directory.

LDAP and integrated Windows sign-in are also prepared but disabled. Local identity tests do not establish that a particular Entra tenant, domain controller or workstation has passed acceptance. Use [account administration](user-management.md) for today’s local access and recovery.

[Original Speakr handbook at the archived revision](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/sso-setup.md)
