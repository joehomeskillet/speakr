# P10-Spike: Speaches v0.8.3 ASR-Fähigkeiten

Stand 17.11.2026. Nur Quellenreview am Pin **v0.8.3 / c78b77d**. Kein Test bestanden, keine GPU-Läufe, kein deutsches Klinikaudio.

`clinic-spec/CLAUDE.md`, `docs/14_Wortschatz_Profile_ASR.md` und `supplements/grok-review/docs/11_ASR_Qualitaetsplan.md` fehlen in diesem Worktree. Grundlage: Backlog P10, PAR-ASR-001..007, V4-008, BT-ASR-001..007, X4-013/014, `09_asr.json`, `asr-benchmark-plan.json`, `src/services/transcription/`.

## Engine (belegt)

Speaches ist ein OpenAI-kompatibler Server: `POST /v1/audio/transcriptions` (Sprache, `prompt`→`initial_prompt`, `hotwords`, `vad_filter`, Wortzeitstempel bei `verbose_json`, SSE-Stream pro Segment). Modelle: `GET/POST/DELETE /v1/models`. VAD: Silero in STT plus `POST /v1/audio/speech/timestamps`. TTS: Kokoro/Piper. Realtime: WebSocket `/v1/realtime` — Default **englisches** `faster-distil-whisper-small.en` plus Kokoro-TTS. STT läuft über **faster-whisper / CTranslate2** (Inferenz). `POST /asr` gibt es nicht; der Speakr-`asr_endpoint`-Connector passt nicht.

Hotwords sind Decoder-Prompt-Tokens (`get_prompt`, faster-whisper ≥1.1.1), kein Wortschatz- oder Akustiktraining. CT2 trainiert nicht; Feinabstimmung ginge nur offline in Transformers plus `ct2-transformers-converter`, nicht als Speaches-Job.

## Klassifikation

| ID | Klausel (kurz) | Klasse | Produktwortlaut |
|---|---|---|---|
| PAR-ASR-001 | Hintergrund-STT → Text + Zeitbezug; Korrektur menschlich | `supported_with_adapter_work` | Lokale Batch-STT. Erkennung ≠ Abnahme. |
| PAR-ASR-002 | Getrennte Live-/Batch-Profile je Quelle | `supported_with_adapter_work` | Config (Modell/Sprache/Hinweise). Keine Sprecherakustik. |
| PAR-ASR-003 | Timeout → manuelle Abschrift, keine späte Überschreibung | `supported_with_adapter_work` | Frist ist Produktlogik, nicht Speaches. |
| PAR-ASR-004 | Korrektur trainiert Wortschatz/Akustik | `not_supported_gap` | **Hotword/Prompt ≠ akustisches Training.** `train_capability=false`. |
| PAR-ASR-005 | Optimierungsfenster + Mindestmaterial | `not_supported_gap` | Kein Optimierungsjob, also kein Fenster. |
| PAR-ASR-006 | never/ask/always speichern, zweiter Arbeitsplatz | `supported_with_adapter_work` | Zentrale Config, kein Dragon-Profil. |
| PAR-ASR-007 | Quellenwechsel, echter Versandcodec | `supported_with_adapter_work` | Aufnahme/Export in Speakr; Speaches transkribiert Dateien. |
| V4-008 | Echte Capabilities, kein Prompt als Training | `supported_with_adapter_work` | Diese Tabelle ist der Vertrag; Training bleibt Lücke. |

## Offene Lücken

- Akustik-/Sprachmodell aus Korrektur (PAR-ASR-004/005); X4-013/WB-018 müssen `CAPABILITY_UNAVAILABLE` bleiben.
- Negativlexikon (PAR-VOC-003 / X4-014) und Aussprachetraining (PAR-VOC-007): keine Engine-API.
- Protokollbruch Speakr `/asr` vs Speaches `/v1/audio/transcriptions`.
- Realtime-Default ist englisch+TTS, nicht deutsches Diktat.
- Codec-Matrix, dss-codec-Pin, WER: **unverified**.

## Regressionsbenchmark (Vorschlag)

Anschluss an `asr-benchmark-plan.json`. Nichts davon ist gelaufen.

Pin: Speaches v0.8.3, CT2/faster-whisper-Versionen, Modell+Revision (Kandidat `Systran/faster-whisper-large-v3`, `language=de`). Erst mit Freigabe: ~20 Aufnahmen je Philips 9600/9620 und Browser-Mic, Standarddeutsch und Schweizer Aussprache, **keine** ungenehmigten Patientendaten.

Vier Bedingungen: Baseline ohne Hinweise; dieselbe Datei mit Hotword/Prompt (**Biasing, nicht Training**); defektes Audio isoliert; blockierte Engine plus manuelle Korrektur ohne späte Überschreibung. Metriken: WER, kritische Fehler (Negation, Dosis, Namen, Seite), Korrektursekunden je Audiominute, Latenz P50/P95, Hotword-Lift nur auf Zielformen.

BT-ASR-001..007, X4-013, X4-014, WB-018 bleiben `not_run`.
