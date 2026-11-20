# Email, account recovery and internal notices

[Deutsch](../de/admin-guide/email-setup.md)

A clinic installation starts without SMTP. New accounts are administered locally. There is no self-registration, email-verification process or password-reset link by email. A user who loses access should contact the authorised local identity administrator for the account’s recovery procedure.

The inherited, optional transcription notification is different. If the operator configures SMTP and a user opts in under **Konto → Einstellungen**, a completed or failed transcription job may send an email. The recording title can appear in the subject and a failure can include an abbreviated error. Consider those fields when evaluating the mail system and the recipients. A synthetic delivery test verifies mail transport only.

Requests for review, rejection, acceptance and completion are [in-app workflow notices](webhooks.md). They stay within the application and do not require SMTP. Use the **Ausgabe** view and its receipt to verify a formal delivery.

[Original Speakr handbook at the archived revision](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/email-setup.md)
