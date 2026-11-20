# Operate local speech recognition

[Deutsch](../de/admin-guide/whisperx-setup.md)

For a clinic installation select a local speech-recognition service. The primary OpenAI-compatible connector can point to local Speaches through **TRANSCRIPTION_BASE_URL**. An installation that uses the separate **asr_endpoint** connector must configure its **ASR_BASE_URL** for a local endpoint as well. Do not enter a cloud endpoint. The synthetic clinic stub is a test fixture.

The bundled OpenVINO GPU service is optional. A configured eligible GPU failure can fall back once to the local CPU connector. Keep the CPU path functional: upload a short neutral spoken sample, confirm the transcription job finishes, then compare the words with playback. If you expect word highlighting, enable supported word timestamps and verify several words against the same audio; segment start times alone cannot supply word-level alignment.

See [model selection](model-configuration.md) for GPU/CPU overrides. Recognition hints require explicit support and configuration; clinic hints are capped at 55 words. Speaker identification and the upstream cloud-assisted diarisation setup are outside the default clinic workflow.

[Original Speakr handbook at the archived revision](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/whisperx-setup.md)
