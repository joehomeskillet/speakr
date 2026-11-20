# Retention and archiving

[Deutsch](../de/admin-guide/retention.md)

The clinic retention sweep archives eligible ordinary recordings. It does not permanently delete them. Retention settings are read at run time; the scheduled check runs at 02:00 server time. Check the applicable age and tag policy as well as ownership before interpreting the counters. Folder settings are not evaluated by this sweep.

Dictation anchor recordings are exempt from that generic sweep. A completed clinical dictation follows its own explicit archive action: an authorised user with an **archive** grant confirms the command. Check its resulting status and the entry in **Archiv**. The archive view is read-only; a missing duration appears as **Aufbewahrung nicht angegeben**. Release permission alone is insufficient. Manual hard deletion remains locked in clinic mode.

Agree the production retention periods and restoration responsibility locally before relying on the schedule. Archive events and the compulsory audit entry are recorded together. For an ordinary upstream recording the owner’s restore flow is distinct from clinical dictation archiving. See [audit evidence](audit-log.md) and [storage](storage.md).

[Original Speakr handbook at the archived revision](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/retention.md)
