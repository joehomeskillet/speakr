# Email, account recovery and internal notices

[Deutsch](../de/admin-guide/email-setup.md)

The clinic fork does not offer self-registration, email verification or password-reset links by email. SMTP is not required for local sign-in or account administration. Contact an authorised local identity administrator for recovery; follow [User management](user-management.md) for local one-time codes and administrator recovery.

Optional processing notifications remain available. They are separate from account recovery and from the clinic's in-app workflow notices.

## Processing notification mail

Mail is off by default for each account. Delivery requires all of the following:

1. `SMTP_HOST`, `SMTP_USERNAME` and `SMTP_PASSWORD` are set.
2. The owner has opted in under **Konto → Einstellungen → Benachrichtigungen** (Account → Preferences → Notifications), using **E-Mail senden, wenn eine Transkription fertig ist**.
3. The account has a usable email address containing `@`, rather than an upstream `@placeholder.local` address.
4. A relevant job completes or fails permanently. Completion mail applies to `transcribe` and `reprocess_transcription`; failure mail also covers `convert` and `merge`.

The account option appears only when SMTP and the address permit delivery. Removing SMTP stops delivery without clearing the saved preference. Summaries do not generate these messages. A canceled job does not send a completion or failure message.

Each message concerns one recording. The subject and body can contain the recording title and username. Completion mail can include the audio length and transcription time. Failure mail can include the first error line, truncated to 200 characters. A link to `/recordings/<id>` is included when `APP_BASE_URL` is set. No transcript or audio attachment is included in this processing notification.

These fields leave the application through the mail server and recipient's mailbox. Recording titles and errors can contain sensitive information. Use only an approved institutional mail system and content policy. A successful synthetic mail test proves transport, not clinical suitability.

## Configuration

Operations configures SMTP in the application environment:

| Variable | Default | Purpose |
|---|---|---|
| `SMTP_HOST` | empty | Mail server; required |
| `SMTP_PORT` | `587` | Numeric port |
| `SMTP_USERNAME` | empty | Login; required |
| `SMTP_PASSWORD` | empty | Password; required |
| `SMTP_USE_TLS` | `true` | STARTTLS |
| `SMTP_USE_SSL` | `false` | Implicit TLS; takes precedence over STARTTLS |
| `SMTP_FROM_ADDRESS` | `noreply@yourdomain.com` | Sender address |
| `SMTP_FROM_NAME` | `Speakr` | Sender display name |
| `APP_BASE_URL` | unset | External instance address for links |

The two TLS switches accept `true`, case-insensitively. Use the TLS mode and port required by the institutional server. The sender always authenticates with username and password; an unauthenticated relay is not supported. Keep `SMTP_PORT` absent rather than empty when using its default.

After applying a deployment change, test with a synthetic recording and an opted-in test account. Check the received content and link. For failures, inspect application log messages such as `SMTP authentication failed`, `SMTP error sending email` or `Error sending email`. Successful sending logs user and recording IDs; the recipient address is logged at `DEBUG`. Mail sending does not itself create an audit event.

## Clinic workflow notices

Workflow notices need no SMTP. Their fixed text contains no dictation transcript. The four events and intended recipients are:

| Notice | Recipient |
|---|---|
| **Abnahme angefordert** (acceptance requested) | Configured approvers |
| **Abnahme abgelehnt** (acceptance rejected) | Assigned writer, if any |
| **Diktat abgenommen** (dictation accepted) | Author and assigned writer |
| **Diktat fertiggestellt** (dictation completed) | Author |

The acting person is excluded. Delivery rechecks current routing and visibility, and duplicate attempts for the same transition do not create duplicate notices. A missing notice calls for checking the actual workflow transition and recipient, not SMTP. Users open **Benachrichtigungen** (Notifications) from the user menu.

Final completion depends on the release workflow and its KIS context. A notice is not proof of a real KIS delivery. Check **Ausgabe** and its receipt; a simulated result proves only the simulated path. See [Pools and permissions](group-management.md) and [Audit log](audit-log.md).

The clinic fork does not offer [webhooks](webhooks.md). Opening a local mail client with original audio attached remains the open web-parity gap WG-EXP-009; processing notification mail does not close it.
