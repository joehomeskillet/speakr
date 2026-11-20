# System statistics

[Deutsch](../de/admin-guide/statistics.md)

Open **System → Systemstatistik** with a site administrator account. Choose 7, 30 or 90 days. The overview compares that period through today with the preceding period of equal length, using calendar days in UTC. Review recording count, transcribed time, model requests and active users. Storage measures retained media. A recording created today and transcribed tomorrow contributes to two different dates.

The activity chart separates uploads, browser recordings and merged recordings. Compare the failure rate with the number of jobs before interpreting a spike. Recent failures can show raw error text and usernames. Limit distribution of captures. Open **Verarbeitung** to inspect the current state of a specific job and its available retry action.

## Costs and limits

The model breakdown separates text calls, embeddings and transcription. Daily and per-model transcription time is rounded to a tenth of a minute. The headline and monthly account totals use whole minutes. “Local” depends on `MODELS_ARE_LOCAL`. A zero estimate excludes electricity, hardware and staff time.

The user table always compares current calendar-month consumption with each account's token and transcription budgets, even if the selected chart covers 90 days. Check the limits in [User management](user-management.md) before attributing a rejection to lack of system capacity.

**Aktivität und Audit → Statistik** is a separate clinical aggregate for authorized authors or author pools. It uses Europe/Zurich time with an exclusive end. A site-admin login alone does not grant access. There is currently no ordinary administration form for those statistics grants. A value marked “Nicht erhoben” is unavailable.

[Original Speakr guide](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/statistics.md).
