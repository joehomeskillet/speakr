# Frequently asked questions

[Deutsch](de/faq.md)

**Who can see my dictation?** The usual author scope is the author’s own material. Pool membership, explicit grants, active administrative read access and denials affect visibility. Editing, release, document export and original-audio download have separate checks. Test scope by signing in with another approved test account and looking up the same synthetic identifier.

**Can I use a phone or a recorder file?** Use the mobile web interface over HTTPS. Allow microphone access if recording, or use the file picker for an existing recording. Browser recording formats vary. A WAV with known speech is a useful first test. DSS/DS2 playback depends on a readable source and the bundled decoder. Claiming an author code in a file does not authenticate a speaker.

**Does recognition need the Internet?** Audio goes to the configured local recognition server. A web browser still needs a connection to the application. Saving an offline draft for later recovery remains an open workflow. See the [roadmap](roadmap.md) before relying on persistence during a network outage.

**How long can a recording be? Which languages and speakers are recognized?** Check the selected model, upload limits, proxy timeout and available storage. Test the longest intended recording and its language. Speaker labels and word timing depend on the connector and its response. An unknown speaker needs human identification. For a mobile or ARM host, validate the container build, decoder and local model separately; this guide’s build example targets Linux x86-64.

**Can I ask questions across recordings, or send audio to a cloud provider?** Inquire, sharing and upstream chat features have separate policy controls. A visible feature in upstream documentation does not grant access here. Check the permissions and approved local models for your installation. Tencent Cloud ASR is excluded from the clinic configuration.

**What do titles and summaries depend on?** Text generation uses a separately configured local text model and feature permissions. A completed transcript is available for human correction independently. Compare any generated wording with the saved revision and original audio.

**Can I make a Word file?** The release output page accepts TXT and DOCX jobs. It records a job result for a specified revision. A receipt alone does not verify download, Word-template application or delivery to a hospital system. See [document output limits](roadmap.md).

**How do I protect or restore data?** Back up the database, audio storage and the stable application secret through your approved operator procedure. Restore them together into an isolated installation and use a synthetic recording to check playback, correction and permissions. Collect consent and retention requirements before recording. See [administration](admin-guide/index.md).

**What should I include in a support request?** Use a synthetic reproducer, app commit, browser version, selected model identifier, operation and displayed status. Remove private hostnames, user or patient details, transcript text, cookies and credentials. Start with [troubleshooting](troubleshooting.md).

Original reference for upstream Speakr: [archived upstream page](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/faq.md).
