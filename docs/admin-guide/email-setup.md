# Mail and notifications

[Deutsch](../de/admin-guide/email-setup.md)

The clinic fork does not offer mail of any kind. There is no SMTP configuration, no processing or notification mail, no EML download and no mail adapter. Do not configure `SMTP_*` variables for the clinic build; they have no supported function here.

Notifications are in-app only. Users open **Benachrichtigungen** (Notifications) from the user menu. The fork also does not offer self-registration, email verification or password-reset links by email. For account recovery, contact an authorised local identity administrator; [User management](user-management.md) describes local one-time codes and administrator recovery.

## In-app workflow notices

Workflow notices stay inside the application. Their fixed text contains no dictation transcript. The four events and intended recipients are:

| Notice | Recipient |
|---|---|
| **Abnahme angefordert** (acceptance requested) | Configured approvers |
| **Abnahme abgelehnt** (acceptance rejected) | Assigned writer, if any |
| **Diktat abgenommen** (dictation accepted) | Author and assigned writer |
| **Diktat fertiggestellt** (dictation completed) | Author |

The acting person is excluded. Delivery rechecks current routing and visibility, and duplicate attempts for the same transition do not create duplicate notices. A missing notice calls for checking the actual workflow transition and recipient. See [Pools and permissions](group-management.md) and [Audit log](audit-log.md).

The clinic fork does not offer [webhooks](webhooks.md) either. Notices are not sent to any external system.
