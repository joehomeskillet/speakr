# Lokale Spracherkennung betreiben

[English](../../admin-guide/whisperx-setup.md)

Der Klinik-Fork nutzt lokale Spracherkennungsdienste. Cloud-ASR-Konnektoren und ein Cloud-Fallback werden nicht angeboten. Betreiben Sie die Klinikanwendung mit `CLINIC_MODE=1`; die hier beschriebenen Prüfungen lokaler Adressen hängen vom Klinikmodus ab.

Registriert sind `openai_whisper` für einen lokalen OpenAI-kompatiblen Dienst wie Speaches, `asr_endpoint` für einen selbst betriebenen `/asr`-Dienst und `clinic_stub` für synthetische Tests. `TRANSCRIPTION_CONNECTOR` wählt einen ausdrücklich aus. Andernfalls wählt `ASR_BASE_URL` den `asr_endpoint`; ohne diese Variable gilt `openai_whisper`. Das ältere `USE_ASR_ENDPOINT=true` wählt ebenfalls `asr_endpoint`, mit einer Ablösewarnung.

## Lokaler CPU-Konnektor

Verwenden Sie einen lokal betriebenen OpenAI-kompatiblen Endpunkt. Beispiel:

```bash
CLINIC_MODE=1
TRANSCRIPTION_CONNECTOR=openai_whisper
TRANSCRIPTION_BASE_URL=http://speaches:8000/v1
TRANSCRIPTION_API_KEY=not-needed-for-local
TRANSCRIPTION_MODEL=your-local-model
```

Ersetzen Sie die Modellkennung durch eine tatsächlich vom Dienst angebotene Kennung. Der Konnektor benötigt einen nicht leeren Schlüssel; ein Platzhalter eignet sich nur für einen lokalen Dienst ohne Authentifizierungspflicht. Dieses Beispiel installiert oder startet Speaches nicht.

Im Klinikmodus wird eine leere oder nicht lokale `TRANSCRIPTION_BASE_URL` abgewiesen. Die Prüfung akzeptiert Loopback-, private und Link-Local-Adressen, einfache Dienstnamen sowie Namen mit `.local`, `.internal`, `.lan` oder `.localhost`. Nutzen Sie ein freigegebenes lokales Ziel und prüfen Sie dessen tatsächliches Routing. Die Lokalitätsregel für Textmodelle ist strenger; siehe [Modellkonfiguration](model-configuration.md).

Die vom Konnektor angegebene Dateigrenze beträgt 25 MiB. Bei aktiviertem Chunking zerlegt die Anwendung grössere Audiodateien. `ENABLE_CHUNKING` ist standardmässig `true`; `CHUNK_LIMIT` und `CHUNK_OVERLAP_SECONDS` steuern die Teile der Anwendung. Das sind Verarbeitungsteile, keine Browser-Uploadsitzungen.

## Optionales GPU-Ziel mit CPU-Fallback

`CLINIC_GPU_ASR_BASE_URL` erweitert im Klinikmodus den konfigurierten CPU-Konnektor um einen GPU-Versuch. Der synthetische Konnektor ist ausgenommen. Das GPU-Ziel muss ein lokaler OpenAI-kompatibler Dienst sein:

```bash
CLINIC_GPU_ASR_BASE_URL=http://gpu-asr:8000/v1
CLINIC_GPU_ASR_TIMEOUT=300
```

`CLINIC_GPU_ASR_API_KEY` fällt auf den CPU-Schlüssel, danach auf `local` zurück. Für jede Anfrage beziehungsweise jeden Audioteil gilt:

1. Speakr prüft `/healthz` an der Wurzel des GPU-Dienstes mit einem Timeout von 5 Sekunden.
2. Ein gesundes Ziel erhält einen GPU-Versuch ohne SDK-Wiederholung.
3. Ein fehlerhafter oder unerreichbarer Health-Endpunkt, ein GPU-Verbindungsfehler, Timeout oder HTTP 5xx löst einen CPU-Versuch aus.
4. Ein CPU-Fehler beendet diesen Versuch. Andere GPU-Fehler, einschliesslich HTTP 4xx wie `model_not_found`, lösen keinen CPU-Fallback aus.

Die Auftragswarteschlange kann einen vorübergehend fehlgeschlagenen Auftrag erneut ausführen; jeder neue Versuch beginnt wieder mit der GPU-Prüfung. Ein Fallback bedeutet nicht, dass der ganze Auftrag nur einmal laufen kann.

Der mitgelieferte optionale Dienst liegt unter `deployment/gpu-asr-service`. Sein Compose-Ausschnitt stellt Port 8000 bereit, reicht `/dev/dri` durch und wählt `GPU.0`. Er ist nicht das Standard-ASR-Deployment. Sein `/healthz` nennt das geladene Modell; der Fallback kann diese Kennung verwenden, wenn kein ausdrückliches GPU-Modell gesetzt ist. Der mitgelieferte Dienst akzeptiert seine bereitgestellte Modellkennung und `whisper-1`.

Unter **Verwaltung → System → Modellwahl** setzen Sie **GPU-Modell** und **CPU-Modell**. Diese Bedienelemente sind in diesem Stand vorhanden. Eine gültige gespeicherte GPU-Vorgabe hat Vorrang vor `CLINIC_GPU_ASR_MODEL` und der vom Dienst gemeldeten Kennung. Die Auswahl einer Kennung lädt kein anderes Modell im Dienst. [Modellkonfiguration](model-configuration.md) beschreibt Katalogprüfungen, inaktive Dienste und gespeicherte Vorgaben.

## Timeouts und Wiederholungen

| Pfad | Einstellung | Klinikstandard |
|---|---|---|
| OpenAI-kompatibler CPU-Konnektor | `TRANSCRIPTION_TIMEOUT` | `3600` Sekunden; muss positiv sein |
| OpenAI-kompatibler CPU-Konnektor | `TRANSCRIPTION_MAX_RETRIES` | `0`; nicht negative Zahl von SDK-Wiederholungen |
| GPU-Ziel | `CLINIC_GPU_ASR_TIMEOUT` | `300` Sekunden; keine SDK-Wiederholungen |
| GPU-Health-Prüfung | Fest | `5` Sekunden |
| `asr_endpoint` | `ASR_TIMEOUT`, ältere Variable `asr_timeout_seconds`, danach Systemeinstellung `asr_timeout_seconds` | `1800` Sekunden |
| Verarbeitungswarteschlange | `JOB_MAX_RETRIES` | Insgesamt `3` Versuche bei vorübergehenden Fehlern |

Die Systemeinstellung `asr_timeout_seconds` steuert weder Speaches über `openai_whisper` noch das GPU-Ziel. `TRANSCRIPTION_MAX_RETRIES=0` verhindert SDK-Wiederholungen, keine Wiederholungen durch die Verarbeitungswarteschlange. Berücksichtigen Sie das gesamte Wiederholungsbudget bei der Kapazitätsplanung.

Betriebsadressen, Schlüssel und Konnektorkonfiguration werden beim Erstellen des Konnektors eingelesen. Wenden Sie solche Änderungen über das Neustartverfahren des Deployments an. Der `asr_endpoint`-Timeout wird für Anfragen erneut gelesen. Modellkennungen lassen sich über **Modellwahl** ändern, ohne Dienstadressen zu ändern.

## Lokales WhisperX oder whisper-asr-webservice

`asr_endpoint` nutzt `ASR_BASE_URL` und optional `ASR_API_KEY`. **Der Konnektor erzwingt keine Lokalität von `ASR_BASE_URL`.** Verwenden Sie ausschliesslich einen freigegebenen lokalen Dienst; die fehlende Prüfung erlaubt kein Cloud-ASR.

- Mit `ASR_ASYNC_JOBS=true` (Standard) oder der entsprechenden Systemeinstellung `asr_async_jobs` kann ein Dienst, der die Jobs-API meldet, einen asynchronen Auftrag erhalten, dessen Status Speakr abfragt. Sonst nutzt der Konnektor synchrones `/asr`.
- `ASR_ENABLE_CHUNKING` ist standardmässig `false`; `ASR_MAX_DURATION_SECONDS` beträgt standardmässig `7200` für das anwendungsseitige Chunking dieses Konnektors.
- `ASR_DIARIZE` ist standardmässig `true`; setzen Sie es auf `false`, wenn keine Diarisierung gewünscht ist. Das Feld `asr.diarization` im Klinikvertrag steuert diesen Konnektor nicht.
- Im Klinikmodus werden Stimm-Embeddings von diarisierten Sprechern nicht als Upstream-Sprecherprofile aufbewahrt. Sprecheridentifikation gehört nicht zum Standard-Klinikworkflow.

## Erkennungshinweise, Zeitmarken und Trainingsgrenzen

Transkriptionshinweise sind im Klinikmodus standardmässig aus. `CLINIC_ASR_PROMPTS_ENABLED` lässt Initialprompt und Hotwords mit einer gemeinsamen Grenze von 55 Wörtern durch. Der mitgelieferte GPU-Dienst hat seinen eigenen Schalter `ASR_PROMPTS_ENABLED`, ebenfalls standardmässig aus. Beide Schalter müssen Hinweise zulassen, damit sie die GPU-Engine erreichen. Siehe [Prompts](prompts.md).

Hotwords, Wortschatz und Profileinstellungen sind Decoderhinweise, kein Akustiktraining. Die Anwendung eines Autorenprofils hängt von passenden Laufzeitnachweisen der Fähigkeiten ab. Das Anlegen eines Optimierungslaufs belegt keine Anpassung: Die Verarbeitungswarteschlange weist `asr_training` derzeit mit `CAPABILITY_UNAVAILABLE` ab. Bezeichnen Sie weder gespeicherte Wortlisten noch synthetische Tests als verbesserte klinische Erkennung.

`TRANSCRIPTION_WORD_TIMESTAMPS` fordert bei Aktivierung im Klinikmodus Wortzeitmarken an. Prüfen Sie, ob der gewählte lokale Dienst echte Wortzeiten liefert, und vergleichen Sie mehrere hervorgehobene Wörter mit der Wiedergabe. Segmentstartzeiten allein reichen nicht für wortgenaue Zuordnung.

**Erneut transkribieren** im **Schreibplatz** benötigt das Bearbeitungsrecht und die aktuelle Bearbeitungslease. Das Ergebnis wird gegen seine Quellrevisionen geprüft; spätere menschliche Korrekturen dürfen nicht durch ein veraltetes ASR-Ergebnis ersetzt werden. Siehe [Benutzerablauf](../user-guide/recording.md).

Der separate lokale Sprecherdienst ist optional für das eigene Stimmprofil mit Einwilligung. Er wird über `CLINIC_SPEAKER_SERVICE_URL` konfiguriert; der Client prüft diese URL nicht auf Lokalität, daher muss der Betrieb sie lokal halten. Sein Diarisierungshelfer ist nicht an die Verarbeitung angebunden. [Datenschutz](privacy.md) beschreibt Einwilligung und Widerruf. Auch der Live-ASR-Adapter ist nicht mit der ausgelieferten Live-Seite verbunden; siehe [Aufnahmesitzungen](recording-sessions.md).

## Betrieb prüfen und Fehler eingrenzen

1. Laden Sie eine kurze neutrale Sprachprobe hoch und warten Sie auf das Verarbeitungsergebnis.
2. Hören Sie das Audio ab und vergleichen Sie das Transkript. Prüfen Sie Wortzeiten bei Bedarf gesondert.
3. Prüfen Sie bei konfiguriertem GPU-Fallback `/healthz` und Anwendungslogzeilen mit dem Anfang `ASR fallback:`. Halten Sie das CPU-Ziel funktionsfähig.
4. Prüfen Sie unter **Verwaltung → Betrieb → Verarbeitung** fehlgeschlagene oder festhängende Aufträge. Eine aktive Standortadministration kann fehlgeschlagene Aufträge wiederholen; ein grüner Auftragsstatus belegt keine Erkennungsqualität.

| Symptom | Prüfen |
|---|---|
| Konfigurationsfehler zur lokalen Adresse | `TRANSCRIPTION_BASE_URL` und `CLINIC_GPU_ASR_BASE_URL` |
| Unbekannter Konnektor | Nur die drei oben registrierten Konnektoren werden unterstützt |
| GPU meldet `model_not_found` | Gespeicherte GPU-Vorgabe, `CLINIC_GPU_ASR_MODEL` und bereitgestellte Modellkennung; dieser Fehler löst keinen Fallback aus |
| Häufige CPU-Fallbacks | GPU-Health, Verbindung, Timeout und Dienstlog |
| Hinweise zeigen keine Wirkung | Beide Hinweisschalter und tatsächliche Engine-Unterstützung; eine gespeicherte Wortliste ist kein Training |
| Dekodierfehler bei `.ds2` oder `.dss` | `CLINIC_DSS_CODEC_BIN`; das Docker-Image nutzt `/usr/local/bin/dss-decode` |

`clinic_stub` ist eine synthetische Offline-Testattrappe, keine Spracherkennung. Sie liefert festen Text und verweigert `CLINIC_PROFILE=production`. Weder Simulator noch Health-Antwort oder synthetischer Test belegen deutsche klinische Erkennungsqualität oder Philips-Gerätekompatibilität.
