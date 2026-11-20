# System settings

[Deutsch](../de/admin-guide/system-settings.md)

Open **System → Systemeinstellungen** as a site administrator. Select a setting, record its current value and scope, save one change, then reload and check the saved value. Database settings take precedence over their environment fallback where supported. Clearing an override restores that fallback. Environment-only values take effect at application startup. Review `admin.setting.changed` in the [audit log](audit-log.md) when investigating a change.

| Task | Where to check |
|---|---|
| Limit a new account's storage | `default_storage_quota_mb` sets the default for new accounts. Edit an existing account's own limit in **Benutzer und Rollen**. |
| Diagnose a rejected upload | Check the application file limit, the applicable video limit and any reverse-proxy limit. Changing a quota does not increase the request limit. |
| Tune transcription | Check the timeout and retry budget of the approved local connector before increasing either. Use **System → Modellwahl** for the assigned GPU/CPU ASR and text model. Save, reload and test with synthetic speech. |
| Control automatic summaries | Automatic generation after successful recognition requires system automatic summarization enabled and the author's automatic-summary preference. A manual action uses the latest saved text and the approved local text model. |
| Change retention | **Automatische Archivierung** and **Archivieren nach Tagen** govern the recording retention job. Archiving preserves data. Clinic dictation workflow archive and administrative audit retention have separate rules. |
| Adjust sign-in and notices | Review login lock thresholds, idle timeout, recording/upload disclaimers, time zone and the default appearance before rolling out site-wide changes. |

## Video input and policy limits

`VIDEO_PASSTHROUGH_ASR` defaults to false. In the existing video upload path, enabling it sends the original video to the transcription backend without ordinary audio extraction. Standard audio-only endpoints reject video. Enable it only for a tested, approved local connector that accepts that container. Audio-only dictation uploads are unaffected. This option is independent of `VIDEO_RETENTION`.

Security switches cannot override a clinic feature lock. Enabling `api_query_tokens_enabled` or `sso_api_tokens_enabled` above the clinic ceiling returns HTTP 403. The inherited transcription discovery controls can likewise return 403. Use the clinic model selection page for available task assignments. Group sharing, webhooks and upstream deletion settings do not enable their blocked routes.

[Original Speakr guide](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/system-settings.md).
