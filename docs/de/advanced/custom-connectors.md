# Lokalen Erkennungsdienst anbinden

[English](../../advanced/custom-connectors.md)

Die [Konnektorschnittstelle](https://github.com/joehomeskillet/speakr/blob/main/src/services/transcription/base.py) überträgt Audio und liefert Text, Segmente und gegebenenfalls Zeitangaben. Wählen Sie den Konnektor passend zur API des Modelldienstes. Der Name `openai_whisper` bezeichnet hier ein Protokoll. Im Klinikbetrieb muss das Ziel lokal oder privat betrieben werden.

| Server-API | Konfiguration in Speakr | Erstprüfung |
|---|---|---|
| Multipart `POST /asr` | `TRANSCRIPTION_CONNECTOR=asr_endpoint`, `ASR_BASE_URL` auf die private Dienstbasis setzen | Kurze WAV hochladen. Transkriptantwort und abgeschlossene Erkennung in der Arbeitsliste prüfen. |
| Lokale OpenAI-kompatible Transkription | `TRANSCRIPTION_CONNECTOR=openai_whisper`, `TRANSCRIPTION_BASE_URL` auf die lokale API-Basis setzen (häufig mit `/v1`), `TRANSCRIPTION_MODEL` auf das angebotene Modell | Modellkennung prüfen, dieselbe WAV einreichen und Wörter sowie Dauer vergleichen. Erforderliche Dienstauthentisierung privat konfigurieren. |
| Optionale lokale GPU mit lokaler CPU als Rückfall | OpenAI-kompatiblen CPU-Konnektor konfigurieren, dann GPU-Adresse und exakten `CLINIC_GPU_ASR_MODEL` für die Klinikumschaltung setzen. | Verwendete Engine des Auftrags kontrollieren. Bei Transport-, Server- oder Gesundheitsfehlern kann die CPU übernehmen. Ungültige Anfrage oder Modellkennung korrigieren. |

## Weiteren lokalen Konnektor implementieren

Erweitern Sie `BaseTranscriptionConnector` im Konnektorpaket. Übersetzen Sie Anfrage und Antwort, liefern Sie ein `TranscriptionResponse` und melden Sie ausschließlich tatsächlich gelieferte Eigenschaften. Registrieren Sie den Konnektor mit Konfigurationsprüfung in der [Registry](https://github.com/joehomeskillet/speakr/blob/main/src/services/transcription/registry.py). Prüfen Sie die Dienstadresse über die vorhandene Lokalitätsprüfung vor der Audioübertragung. Unterscheiden Sie wiederholbare Transportfehler, nicht unterstützte Funktionen und dauerhaft fehlerhafte Konfiguration.

Prüfen Sie mit synthetischem Ton Upload, erneute Erkennung und Wiederholung. Kontrollieren Sie fehlende Wörter, Zeiteinheiten, Sprache und Sprecherfelder anhand einer tatsächlichen Engineantwort. Führen Sie in der Testumgebung `python -m pytest tests/test_connector_architecture.py tests/clinic/test_gpu_connector_everywhere.py` aus. Zugangsdaten, Audiodaten und Transkripttexte gehören nicht ins Diagnoseprotokoll.

Für den optionalen GPU-Pfad bezeichnet `CLINIC_GPU_ASR_BASE_URL` den privaten GPU-Endpunkt und `CLINIC_GPU_ASR_MODEL` das dort angebotene Modell. Prüfen Sie im privaten Netz den Dienstpfad `/healthz` am Ursprung. Ein betriebsbereiter Dienst kann eine unbekannte Modellkennung zurückweisen. Im eigenen Konnektor lehnt `_validate_config()` ungültige Konfigurationen ab, `transcribe(request)` liefert die Antwort. Geben Sie `duration` in Sekunden und tatsächlich vorhandene Segment- oder Wortzeiten an. Lassen Sie fehlende Sprecherfelder frei. Der Dateiname ist kein Nachweis einer Sprecheridentität.

Originalreferenz zu Upstream-Speakr: [archivierte Upstream-Seite](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/advanced/custom-connectors.md).
