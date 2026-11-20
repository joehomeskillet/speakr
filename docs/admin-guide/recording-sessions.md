# Recording sessions and interrupted uploads

[Deutsch](../de/admin-guide/recording-sessions.md)

The normal clinic recorder captures in the browser and submits the finished recording in one multipart upload. Ask the user to wait for the receipt and the new dictation identifier before closing the tab. A disconnected tab or a closed browser is not a recoverable copy: the clinic browser does not persist recording audio in IndexedDB.

**If the receipt is missing:** check the author’s list before uploading again. If the source was an existing file, keep the original and retry from that file after connectivity returns. If the recording remains in the open tab, follow the failure message there. An upstream server-chunk switch does not convert the clinic recorder into a crash-recovery service.

A large-file upload path can use server-side upload sessions. On that path the defaults are 16 MiB per chunk, 5 GiB per user and expiry after 24 hours of inactivity. Check proxy upload limits against the applicable transport. Live dictation and agent imports have separate workflows. See [recording audio](../user-guide/recording.md).

[Original Speakr handbook at the archived revision](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/recording-sessions.md)
