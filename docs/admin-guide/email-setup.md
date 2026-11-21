# Email Setup
<small>[Deutsch](../de/admin-guide/email-setup.md)</small>

SMTP is not a start prerequisite in the clinic build. Sign-in, account activation, password resets and the clinic workflow notices need no mail server. Activation and reset use a local one-time code that is handed over outside the application. The login page has no form to redeem the code yet, and a locked-out administrator account has no recovery path (both Open gap, see [User management](user-management.md)). The only mail the code can send is the processing notification inherited from upstream Speakr. It is off unless you configure SMTP and a user opts in, and it has not been reviewed for clinical use.

## Status of the e-mail functions

| Function | Status | In the clinic build |
|----------|--------|---------------------|
| Processing notification ("transcription ready" / "transcription failed") | Default off | Needs SMTP and a per-user opt-in. Not gated by clinic mode. Not reviewed for clinical use. See [Processing notification mail](#processing-notification-mail). |
| Clinic workflow notices (**Abnahme angefordert**, **Abnahme abgelehnt**, **Diktat abgenommen**, **Diktat fertiggestellt**) | Available | In-app only, never sent by e-mail, no SMTP needed. See [Clinic workflow notices](#clinic-workflow-notices). |
| E-mail verification of new registrations | Upstream only | Removed together with self-registration. |
| Password reset by e-mail ("Forgot password?") | Upstream only | Removed. A person with **Administrationsrolle** (administration role) and **Identitätsadministration** (identity administration) issues a local one-time code instead; no web form redeems it yet (Open gap). See [User management](user-management.md). |
| Self-registration | Upstream only | Removed. Accounts are created by a person with **Administrationsrolle** and **Identitätsadministration**. |
| Sending original audio as an attachment or opening a local mail draft | Open gap | WG-EXP-009 stays open. No code path sends dictation audio or documents by e-mail. The permitted web route is an authorized original-audio download, which needs the separate right **Originalaudio exportieren** (export original audio); no clinic view currently offers that download. The module for an unsent `.eml` file has no caller. See [Group Management](group-management.md#original-audio-export-originalaudio-exportieren). |
| Webhooks and web push | Upstream only | Locked in clinic mode (feature locks `user_webhooks` and `push_notifications`); no operating procedure unlocks them. See [Webhooks](webhooks.md). |

## Sign-in and recovery without e-mail

- The fork has no registration, verification or password-reset routes. Of the stock sign-in endpoints, only the login page and the upstream SSO login and callback are public in clinic mode, and the SSO routes refuse there (see [Sign-in and identity providers](sso-setup.md)).
- The upstream variables `ENABLE_EMAIL_VERIFICATION` and `REQUIRE_EMAIL_VERIFICATION` are not read anywhere in the fork. Setting them has no effect.
- Accounts are created by an administrator who holds both **Administrationsrolle** (administration role) and **Identitätsadministration** (identity administration). Activation and password resets use a local one-time code from **Einmalcode ausstellen** (issue one-time code). The page shows the code once to the issuing person under **Einmalcode (wird nur jetzt angezeigt)** (one-time code, shown only now); the application does not deliver it by e-mail. A reset code ends all sessions of the account and blocks password sign-in until it is redeemed. No form on the login page redeems the code yet (Open gap). For the account of another administrator (**Administrationsrolle** or **Identitätsadministration**) the web refuses the code (`Administratorkonten werden nur über die Serverkonsole wiederhergestellt. Ein Einmalcode kann hier nicht ausgestellt werden.`), and the console recovery commands are not registered, so a locked-out administrator account cannot be recovered (Open gap). The first administrator is created at the server console with `scripts/clinic_bootstrap_admin.py`. The procedure and its current limitations are described in [User management](user-management.md).
- Every account still has an e-mail address. It is a sign-in identifier (the login field is labeled **E-Mail**, in the English interface **Email**, but it also accepts the username) and the recipient of the processing notification, if that is used.
- The clinic contract file `clinic-spec/config/dev.defaults.json` records `identity.smtp_required: false`, `identity.self_registration: false`, `identity.email_auto_link: false` and `features.email_exports: false`. The application reads this file at runtime for other values (for example the feature locks under `features`), but none of these four keys is evaluated anywhere. The behavior described on this page comes from the code itself.

## Clinic workflow notices

Status: Available. Four fixed German in-app notices replace e-mail for the dictation workflow: **Abnahme angefordert** (acceptance requested), **Abnahme abgelehnt** (acceptance rejected), **Diktat abgenommen** (dictation accepted) and **Diktat fertiggestellt** (dictation completed). They are never mailed, and no SMTP server is needed. They are delivered as `clinic_notify` jobs on the existing processing queue. Users find them in the user menu under **Benachrichtigungen** (Notifications).

Acceptance notices arise only for dictations for which an acceptance policy with approvers (**Berechtigte Freigebende**, authorized approvers) has been saved on the page **Abnahme** (field **Diktat-ID**, button **Richtlinie laden**). **Diktat fertiggestellt** needs a final release, and final release needs a trusted KIS context. KIS is Simulated: only `CLINIC_PROFILE=staging` uses the synthetic MockKIS adapter, which is no evidence of a KIS integration; otherwise final release is refused (`untrusted_context`), so **Diktat fertiggestellt** does not occur. See [Group Management](group-management.md#release-freigeben-and-acceptance-abnahme).

Recipients, triggers, delivery rules and the current display limitation: [Webhooks › In-app workflow notices](webhooks.md#alternative-in-app-workflow-notices).

## Processing notification mail

Status: Default off. This is the upstream Speakr notification, unchanged. No clinic-mode check applies to it, and it has not been reviewed for clinical use.

### When a mail is sent

All of the following must hold:

1. `SMTP_HOST`, `SMTP_USERNAME` and `SMTP_PASSWORD` are all set.
2. The recording owner has switched the notification on. It is off by default for every account.
3. The owner's e-mail address contains `@` and does not end in `@placeholder.local` (the upstream placeholder for SSO accounts without an address).
4. The job is a transcription job:
   - "Ready" mail: job type `transcribe` or `reprocess_transcription` completed and the recording is not in status `FAILED`.
   - "Failed" mail: job type `transcribe`, `reprocess_transcription`, `convert` or `merge` failed permanently, or a transcription ended without text or was stopped by the monthly transcription limit.

No mail is sent for summaries, background jobs or jobs that the user canceled. Each recording sends its own mail, so an import of fifty files can produce fifty mails. Dictations that clinic imports take in also create stock `transcribe` jobs for a working recording owned by the author, so the mail applies to them as well.

### What the mail contains

- Subject `Transcription ready: <recording title>` or `Transcription failed: <recording title>`. Without a title the subject uses "Untitled recording".
- A fixed English text with the username and the recording title; the "ready" mail adds the audio length and the transcription time when they are known.
- In the "failed" mail: the first line of the error text, cut to 200 characters, if there is one.
- A link to `<APP_BASE_URL>/recordings/<id>`, only when `APP_BASE_URL` is set.

This content leaves the system to the SMTP server and the recipient's mailbox. Recording titles can identify a patient. Leave the SMTP variables unset unless your organization has approved this content for e-mail; if you use it, use the institution's own mail server. See [Privacy](privacy.md).

Sending writes no audit event. The application log records "Email sent successfully" and "Sent completion email for recording <id> to user <id>" (or "Sent failure email …"). The recipient address is logged only at log level `DEBUG`.

### Where users switch it on

Users switch the mail on themselves: user menu **Konto** (Account, `/account`), tab **Einstellungen** (Preferences), section **Benachrichtigungen** (Notifications), option **E-Mail senden, wenn eine Transkription fertig ist** ("Email me when a transcription finishes").

The section is shown only when the conditions in steps 1 and 3 above are met. When SMTP is removed later, a stored preference is kept but no mail is sent.

### Configuration

Set the variables in the application environment (template: [`config/env.email.example`](https://github.com/joehomeskillet/speakr/blob/main/config/env.email.example), clinic fork) and restart the application.

| Variable | Default | Meaning |
|----------|---------|---------|
| `SMTP_HOST` | (empty) | SMTP server host name. Required. |
| `SMTP_PORT` | `587` | SMTP port. |
| `SMTP_USERNAME` | (empty) | SMTP login. Required. |
| `SMTP_PASSWORD` | (empty) | SMTP password. Required. |
| `SMTP_USE_TLS` | `true` | STARTTLS after connecting (typically port 587). |
| `SMTP_USE_SSL` | `false` | Implicit TLS (typically port 465). Takes precedence over `SMTP_USE_TLS`. |
| `SMTP_FROM_ADDRESS` | `noreply@yourdomain.com` | Sender address. |
| `SMTP_FROM_NAME` | `Speakr` | Sender display name. |
| `APP_BASE_URL` | (unset) | External address of the instance, without a trailing path. Used for the link in the mail. |

```bash
SMTP_HOST=<smtp-host>
SMTP_PORT=587
SMTP_USE_TLS=true
SMTP_USE_SSL=false
SMTP_USERNAME=<smtp-user>
SMTP_PASSWORD=<secret>
SMTP_FROM_ADDRESS=<sender-address>
SMTP_FROM_NAME=Speakr
APP_BASE_URL=https://<your-host>
```

Notes:

- `SMTP_USE_TLS` and `SMTP_USE_SSL` are on only for the value `true` (any case). With both off, the connection is unencrypted. Do not run it that way.
- The mail is sent from a queue worker without a request, so the link exists only with `APP_BASE_URL`. The link uses the stock path `/recordings/<id>` with the id of the stock recording row.
- If `SMTP_PORT` is set, it must be a number. An empty or non-numeric value stops the application at startup, even with SMTP unused; leave the variable out instead.
- The application always signs in to the SMTP server with `SMTP_USERNAME` and `SMTP_PASSWORD`; a relay without authentication is not supported.
- The template contains active example values for Gmail and commented examples for public providers. Copy only the lines you need, with the values of your own mail server.
- `APP_BASE_URL` is also read outside e-mail, for example for absolute links in document downloads and for the startup warning about an https address with `SESSION_COOKIE_SECURE` off.

### Troubleshooting

- The option is not shown on the account page: one of `SMTP_HOST`, `SMTP_USERNAME`, `SMTP_PASSWORD` is missing, or the account address is not deliverable (see step 3).
- Mail is not delivered: look in the application log for "SMTP authentication failed: …", "SMTP error sending email: …" or "Error sending email: …", and for the warnings "Completion email for job <id> failed: …" or "Failure email for job <id> failed: …". Check that the SMTP port is reachable from the application host.
- The mail has no link: `APP_BASE_URL` is not set.
- Mail is classified as spam: use a sender address of your own domain with SPF and DKIM configured on your mail server.

## Upstream reference

Upstream Speakr documentation (pinned original, not valid for the clinic build): [Email Verification & Password Reset](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/email-setup.md). It describes e-mail verification, password reset, registration settings and public mail providers, none of which apply here.

---

Next: [Sign-in and identity providers](sso-setup.md) →
