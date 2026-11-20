# Workflow notifications and external delivery

[Deutsch](../de/admin-guide/webhooks.md)

Personal webhooks are disabled in clinic mode. There is no account tab for registering a callback, and enabling an upstream webhook setting does not enable delivery. Do not use a webhook, a personal API token or a push notification as a substitute for a clinical delivery receipt.

Open the in-app notifications for the account involved in the workflow. The supported notices are **Abnahme angefordert**, **Abnahme abgelehnt**, **Diktat abgenommen** and **Diktat fertiggestellt**. They use fixed event labels and recipient rules; the message does not include dictated patient text. If a notice is missing, check the actual workflow transition and its intended recipient.

For delivery, use the dictation’s **Ausgabe** view and check the recorded outcome. A KIS connection needs its own configured and accepted integration. A simulated KIS result demonstrates the application path only. Imported files and their receipts belong to the import centre. [Audit log](audit-log.md) · [Email](email-setup.md)

[Original Speakr handbook at the archived revision](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/webhooks.md)
