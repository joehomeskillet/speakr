# Tencent Cloud ASR and clinic mode

[Deutsch](../de/advanced/tencent-asr.md)

Upstream Speakr documents Tencent’s cloud recording-file recognition as a provider. It uploads audio for a vendor task and later retrieves the result. Clinic mode requires local speech recognition. The clinic connector registry on this branch does not offer `tencent_asr`.

## Migrating a configuration

1. Inventory the upstream connector name and queue state without copying audio, API secrets or provider task payloads. A pending cloud task requires reconciliation under the original deployment’s procedures.
2. Provision a local engine implementing either `POST /asr` or the local OpenAI-compatible transcription API. Select the matching [connector and model](../advanced/custom-connectors.md) in the clinic app. Keep Tencent credentials and cloud staging buckets outside the clinic deployment.
3. Record a neutral sentence with a clinic test account. Check local processing, saved transcript, speaker or timing capabilities where available, and playback. A successful sign-in only checks the web service.

An unknown connector or a refused remote URL is a configuration error. Correct the selected local service and run a synthetic verification before processing clinical material.

Original reference for upstream Speakr: [archived upstream page](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/advanced/tencent-asr.md).
