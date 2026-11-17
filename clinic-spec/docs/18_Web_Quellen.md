# 18 – Neu geprüfte Primärquellen
Stand 17.11.2026. Bibliotheksdokumentation ist kein lokaler Kompatibilitätstest. Historische Quellen aus V3.2 bleiben in evidence/sources-v3_2.json erhalten.

## WEB01 – Speakr release and commit
https://github.com/murtaza-nasir/speakr/releases/tag/v0.11.1

Release v0.11.1 refers to commit 48d3018; pinned baseline retained, no build performed.

## WEB02 – Speakr recording sessions
https://murtaza-nasir.github.io/speakr/admin-guide/recording-sessions/

Existing server recording sessions/chunk transport; reuse, not proof of clinic privacy/completeness.

## WEB03 – Speakr mobile recording
https://murtaza-nasir.github.io/speakr/user-guide/recording/

Mobile foreground restrictions documented; not a native-background recording guarantee.

## WEB04 – Speaches realtime
https://speaches.ai/usage/realtime-api/

WebSocket transcription-only mode documented; specific interruption events are limited; local adapter must test its pinned version.

## WEB05 – Speaches TTS
https://speaches.ai/usage/text-to-speech/

Existing local TTS API; model, voice/language, license and local performance must be verified separately.

## WEB06 – Keyboard Lock
https://developer.chrome.com/docs/capabilities/web-apis/keyboard-lock

Keyboard Lock applies under browser/fullscreen/OS restrictions; not general native global hotkeys.

## WEB07 – WebHID
https://developer.mozilla.org/en-US/docs/Web/API/WebHID_API

Limited availability and explicit device access; not universal foot pedal/microphone control.

## WEB08 – File saving
https://developer.mozilla.org/en-US/docs/Web/API/Window/showSaveFilePicker

Picker requires secure context/user activation and is not universally available; no arbitrary path write.

## WEB09 – MediaStream Recording
https://www.w3.org/TR/mediastream-recording/

Recording events/containers need finalization; separate chunks are not necessarily independently playable.

## WEB10 – dss-codec
https://github.com/hirparak/dss-codec

MIT decoder DSS/DS2 to WAV; password-protected input supported according to project, not Philips metadata/firmware proof.

## WEB11 – wavesurfer.js
https://wavesurfer.xyz/

Existing OSS waveform/regions plugins, BSD-3-Clause per project site; candidate only if existing Speakr UI lacks necessary functionality.

## WEB12 – Speakr privacy
https://murtaza-nasir.github.io/speakr/admin-guide/privacy/

Existing browser persistence paths need a clinic-specific audit; latest web documentation may differ from pin.
