# Use Speakr as a browser app

English · [Deutsch](../de/user-guide/pwa.md)

Open the clinic's HTTPS address in the supported browser. If the browser offers **Install app** or **Add to Home Screen**, you may use it according to workstation policy. Launching that icon opens the same clinic site and requires the usual authenticated session. Microphone access is a separate site permission.

Select **Verbindungsstatus / Entwürfe** in the header if a transfer or save is interrupted. The current service worker caches static interface files. Clinical audio, recordings, API responses and account pages bypass that cache. An installed icon does not provide an offline copy of a dictation.

The standard clinic screen does not currently provide persistent managed audio-draft recovery. When connectivity returns, check the server-confirmed status before resubmitting. Retain an original upload until its receipt arrives. Sign out on a shared workstation. If the interface looks outdated after a release, reload the tab normally before troubleshooting further.

[Original Speakr guide](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/user-guide/pwa.md)
