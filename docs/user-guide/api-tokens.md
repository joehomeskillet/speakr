# Authentication for users and import agents

English · [Deutsch](../de/user-guide/api-tokens.md)

For normal work, use the clinic's web sign-in. The authenticated session identifies the person creating, correcting or reviewing each dictation. Personal API tokens from the original Speakr guide are outside the clinic user workflow.

**Uploading a file yourself?** Choose **Neue Aufnahme → Datei hochladen**, then **Entwurf sichern**. The resulting draft belongs to the signed-in author. Choose **Einreichen** when it is ready for the writing service.

**Connecting an approved import device?** An operator registers the separate Windows importer and its permitted sources. The agent authenticates on its dedicated channel through the trusted ingress. Its device identity and import receipt do not confer playback rights or identify a dictating author. Report an unassigned import through **Dateien und Geräteimport**.

A rejected browser request may require signing in again or checking object permissions. Never put session cookies or access tokens into a link, support screenshot or message. [Clinic API reference](api-reference.md).

[Original Speakr guide](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/user-guide/api-tokens.md)
