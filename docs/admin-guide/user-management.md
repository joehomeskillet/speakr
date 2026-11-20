# User management

[Deutsch](../de/admin-guide/user-management.md)

Open **Benutzer und Rollen → Konten** in administration. Search the account list or filter by status and login policy. “Keine Anmelderichtlinie” means an existing account has no clinic login policy. It does not identify that person's directory provider. The supported new-account policy here is **Nur lokal**. Check **Poolbezüge** for membership and **Fachrechte** for scoped clinical permissions.

## Create or recover a local account

1. Sign in as an active site administrator with identity-administration rights. Sensitive account writes require a fresh login within five minutes. Create the account with its unique username, email and display name. It starts pending activation.
2. Select **Einmalcode ausstellen**. The code is shown once, expires after 30 minutes, and is for one use. Deliver it to the account holder using the approved confidential channel. Never retain it in a ticket or screenshot.
3. The clinic login endpoint supports signed-out, CSRF-protected activation or password recovery. The stock sign-in page currently lacks a code-redemption form. Use the site's approved activation process. The holder sets and confirms their own password and then signs in normally.
4. Reopen the account to verify active status. A login lock can be lifted with the account's unlock action after identity verification. Disable an account to withdraw access. The final active local identity administrator is protected.

For a pre-clinic local account, a freshly authenticated identity administrator can submit `PUT /admin/users/<id>` with CSRF and exactly `{"adopt_existing_local":true,"expected_version":0}`. This API-only adoption preserves its internal ID, password and ownership. Inspect existing administrative flags before adoption because activating an existing site administrator enables audited cross-dictation reads. On a version conflict reload and review the new state before saving again.

## Limits, membership and permissions

Set storage in MB, monthly tokens and monthly transcription **in seconds** on the account. Zero clears that individual limit. Review actual consumption in [System statistics](statistics.md). Pool membership and leadership establish routing and assignment. Use the separate clinical grant with the narrowest author or pool scope for additional actions. A denial takes precedence. Confirm both the authorized action and the excluded scope with synthetic material.

An author can usually open, play and edit their own dictations. Document export, final release, archive and original-audio download are separate actions. Even the author needs `audio_export` to download the unchanged original. Technical administration permits audited reads in its defined scope and confers no general right to edit or release other users' dictations.

[Original Speakr guide](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/user-management.md).
