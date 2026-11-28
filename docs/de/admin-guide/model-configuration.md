# Lokale Modelle für Transkription und Zusammenfassung wählen

[English](../../admin-guide/model-configuration.md)

Bei eingeschaltetem `CLINIC_MODE` bietet der aktuelle Klinik-Build **Verwaltung → System → Modellwahl** für GPU-Transkription, CPU-Transkription und das Textmodell der Diktatzusammenfassung. Diese Bedienung und die revisionsgebundene **KI-Zusammenfassung** sind integriert, keine separate Vorschau. Der Betrieb konfiguriert Dienstadressen und Zugangsdaten; aktive Standortadministratoren wählen Modellkennungen in der Anwendung.

Cloud-ASR-Konnektoren und Cloud-Fallback bietet der Klinik-Fork nicht an. Der OpenAI-kompatible Konnektor bleibt für lokale Dienste wie Speaches erhalten, nicht als Anleitung zur Nutzung eines Cloud-Dienstes.

## Lokale Dienste konfigurieren

Registriert sind nur `openai_whisper`, `asr_endpoint` und der synthetische Konnektor `clinic_stub`. Eine explizite `TRANSCRIPTION_CONNECTOR`-Angabe hat Vorrang; andernfalls wählt `ASR_BASE_URL` den `asr_endpoint` (ebenso das veraltete `USE_ASR_ENDPOINT=true`), und der Standard ist `openai_whisper`. Der Stub ist keine Spracherkennung und verweigert das Produktionsprofil.

| Variable | Zweck |
| --- | --- |
| `TRANSCRIPTION_BASE_URL` | Lokaler OpenAI-kompatibler CPU-ASR-Endpunkt, beispielsweise `http://speaches:8000/v1` |
| `TRANSCRIPTION_API_KEY` | Vom Konnektor verlangter, nicht leerer Zugangswert; einen Platzhalter nur verwenden, wenn der lokale Dienst keine Authentisierung verlangt |
| `TRANSCRIPTION_MODEL` | Installationsmodell für `openai_whisper`; Standard `whisper-1`, mit `WHISPER_MODEL` als verbliebenem Altnamen |
| `ASR_BASE_URL` | Lokaler WhisperX- oder whisper-asr-webservice-Endpunkt für `asr_endpoint` |
| `CLINIC_GPU_ASR_BASE_URL` | Optionales lokales GPU-Ziel; ungesetzt bedeutet kein GPU-zuerst-Fallback |
| `CLINIC_GPU_ASR_MODEL` | Optionale GPU-Modellvorgabe der Installation; andernfalls liefert das erkannte GPU-Dienstmodell oder das CPU-Konnektormodell den Standard |
| `TEXT_MODEL_BASE_URL` | Expliziter lokaler OpenAI-kompatibler Textendpunkt |
| `TEXT_MODEL_NAME` | Von diesem lokalen Endpunkt bereitgestelltes Modell; explizit setzen statt den Upstream-Modellnamen zu übernehmen |
| `TEXT_MODEL_API_KEY` | Zugangsdaten des lokalen Dienstes, falls erforderlich; ohne Schlüssel sendet der Textclient `not-needed` |
| `CLINIC_LOCAL_MODEL_HOSTS` | Hostnamen-Freigabeliste für Textmodelle; Standard `speaches,host.docker.internal,localhost` |

Starten Sie die Anwendung nach Änderungen der Installationsumgebung neu. Die Modellwahlseite nimmt weder Endpunkt-URLs noch API-Schlüssel entgegen.

### Lokalitätsprüfungen

Der Konnektor `openai_whisper` und das GPU-Ziel weisen im Klinikmodus leere oder nicht lokale Basis-URLs ab. Die ASR-Regel akzeptiert Loopback-, private und Link-Local-Adressen, einteilige Dienstnamen und Namen mit den Endungen `.local`, `.internal`, `.lan` oder `.localhost`. Der Konnektor `asr_endpoint` erzwingt diese Lokalitätsprüfung **nicht**: Der Betrieb muss `ASR_BASE_URL` lokal halten. [Lokale ASR einrichten](whisperx-setup.md) beschreibt Fallback, Zeitlimits und Dienstprüfungen.

Die Textmodellregel ist strenger. `TEXT_MODEL_BASE_URL` muss lokal sein; jede gesetzte `CHAT_MODEL_BASE_URL`, `TRANSLATION_MODEL_BASE_URL` und `EMBEDDING_BASE_URL` muss ebenfalls lokal sein. Ein nicht lokaler optionaler Endpunkt sperrt Chat, Inquire und generative Zusammenfassungen gemeinsam.

Für Textmodelle bedeutet lokal: Loopback (`127.0.0.0/8`, `::1`), private Netze (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `fc00::/7`) oder ein Hostname in `CLINIC_LOCAL_MODEL_HOSTS`. Eine eigene Liste ersetzt die Standards. Ein einteiliger Hostname ausserhalb dieser Liste genügt nicht. URLs mit Benutzerinformationen, Backslashes oder Leerraum werden abgewiesen. Verwenden Sie eine explizite `http`- oder `https`-URL.

Beispielwerte der Installation; ersetzen Sie Port- und Modellplatzhalter durch die Werte der lokalen Dienste:

```ini
CLINIC_MODE=1
TRANSCRIPTION_BASE_URL=http://speaches:8000/v1
TRANSCRIPTION_API_KEY=not-needed-for-local
TRANSCRIPTION_MODEL=<local-asr-model>
CLINIC_LOCAL_MODEL_HOSTS=speaches,host.docker.internal,localhost,llm
TEXT_MODEL_BASE_URL=http://llm:<port>/v1
TEXT_MODEL_NAME=<local-text-model>
ENABLE_INQUIRE_MODE=false
```

Setzen Sie `TEXT_MODEL_API_KEY` nicht allein. Ohne explizite Basis-URL verwendet der ältere Client einen gehosteten Standard. Sind sowohl Schlüssel als auch explizite Basis-URL abwesend, wird kein Textclient erzeugt. Einige Upstream-Beispieldateien nennen weiterhin gehostete Textdienste; sie sind keine einsatzfertigen Klinikkonfigurationen.

## Modellwahl bedienen

Melden Sie sich mit einem aktiven Standortadministratorkonto an: **Administrationsrolle** und ein Klinikkonto mit Status **Aktiv**. Öffnen Sie `/admin#/system/modelle`. Identitätsadministration oder Poolleitung allein gewährt keinen Zugriff.

1. Lesen Sie für **GPU-Modell**, **CPU-Modell** und **Textmodell** die gespeicherte Auswahl, wirksame Kennung und deren Herkunft.
2. Wählen Sie eine Kennung aus dem Katalog des lokalen Dienstes. Ist kein vollständiger Katalog verfügbar, geben Sie eine von der Installation tatsächlich unterstützte Kennung ein. Diese Freitexteingabe prüft die Syntax der Kennung, nicht, ob ein Modell geladen ist.
3. Wählen Sie **Modellwahl speichern** und prüfen Sie danach die wirksamen Werte. Leeren Sie ein Feld oder wählen Sie **Vorgabe übernehmen**, um seinen Standard zu erben.
4. Hat eine andere administrierende Person zuerst gespeichert, verwenden Sie **Neu laden** und gleichen Sie die Werte vor dem erneuten Speichern ab. Eine veraltete Revision ergibt `409 stale_revision`.

Katalogabfragen lesen die Modellliste des lokalen CPU-/Textdienstes und `/healthz` des GPU-Dienstes. Der GPU-Katalog erkennt das geladene Modell des mitgelieferten OpenVINO-Dienstes und `whisper-1`. Ein bestätigter Katalog ergibt ein Auswahlfeld; eine darin fehlende gespeicherte Kennung erscheint deaktiviert. Eine andere Kennung ausserhalb dieses Katalogs wird abgewiesen. Ohne Katalog stammen CPU-Vorschläge aus der vorhandenen Aufnahmemodellliste und `TRANSCRIPTION_MODELS_AVAILABLE`.

### Wirksame Werte verstehen

| Anzeige | Bedeutung |
| --- | --- |
| **Wirksam: …** | Für den konfigurierten lokalen Pfad ist eine wirksame Kennung bekannt. Dies beweist nicht, dass eine echte Inferenzanfrage erfolgreich sein wird. |
| **Wirksam: Keine Modellkennung gemeldet** | Keine Kennung ist bestätigt; dazu zählt eine gespeicherte oder vorgegebene Kennung, die im bestätigten Katalog fehlt. Anfragen können diese Kennung dennoch senden und vom Dienst abgewiesen werden. |
| **Derzeit inaktiv: Kein Modell aktiv** | Der Pfad ist nicht lokal konfiguriert oder der Zusammenfassungsclient erfüllt die lokalen Modellprüfungen nicht. Eine gespeicherte Auswahl kann trotzdem bestehen. |

Als Herkunft erscheinen **Hier festgelegt**, **Installationsvorgabe**, **Vom lokalen Dienst**, **Bestehender Standard** und **Keine Vorgabe**. Die Seite fragt Kataloge ab, führt aber keinen Transkriptions- oder Zusammenfassungs-Abnahmetest aus.

| Aufgabe | Gespeicherte Auswahl | Geerbter Standard |
| --- | --- | --- |
| CPU-Transkription | Übersteuert das Anfragemodell, solange der CPU-Endpunkt lokal ist | Vorhandenes `transcription_default_model`, andernfalls Konnektorstandard; Anfrage-/Tag-Auswahl kann weiterhin Vorrang haben, wenn keine aufgabenspezifische Kennung gespeichert ist |
| GPU-Transkription | Übersteuert die GPU-Kennung der Installation und das gemeldete Dienstmodell | `CLINIC_GPU_ASR_MODEL`, andernfalls erkanntes Dienstmodell, andernfalls CPU-Konnektormodell |
| Diktatzusammenfassung | Gilt nur für revisionsgebundene Klinikzusammenfassungen | `TEXT_MODEL_NAME`; Titel und andere Textmodellvorgänge behalten ihre bisherige Modellkonfiguration |

Die Auswahl wird für neue Modellanfragen gelesen. Sie verarbeitet abgeschlossene Arbeit nicht erneut und lädt kein anderes Modell in den Dienst. Ein laufender ASR-Auftrag mit mehreren Abschnitten kann zwischen Abschnittsanfragen eine geänderte Auswahl lesen; ändern Sie sie nicht während eines Vergleichslaufs.

Jeder Speichervorgang liegt mit einer Revision in `clinic_task_models` und meldet `admin.setting.changed` über den Auditdienst, ohne alte/neue Modellkennungen. Der allgemeine Systemeinstellungs-Endpunkt weist Schreibzugriffe auf diesen Schlüssel ab; verwenden Sie **Modellwahl**.

Der separate Bereich **Modellkatalog für die Aufnahme** unter **Systemeinstellungen** beschreibt die vorhandene Aufnahmemodellliste. Seine Upstream-Routen zum Erkennen/Speichern bleiben im Klinikmodus gesperrt. Eine früher gespeicherte Liste beziehungsweise Vorgabe gilt weiter; dieser Bereich ist nicht der Editor für Aufgabenmodelle.

## Revisionsgebundene Diktatzusammenfassungen

**KI-Zusammenfassung** ist im **Schreibplatz** eingeklappt und in den **Diktatdetails** ausgeklappt. Das Öffnen liest den Status. Die Zusammenfassung ist vom Transkript getrennt und bearbeitet oder ersetzt den Diktattext nie.

Lesen erfordert **Ansehen**. Eine manuelle Anforderung benötigt zusätzlich **Bearbeiten**, eine gültige Sitzung samt CSRF-Token, einen verwendbaren gespeicherten Text mit mindestens 10 Zeichen und ein bereites lokales Textmodell. Der Inhaltszugriff der Standortadministration gewährt allein kein Bearbeitungsrecht. Die Schaltflächen heissen **Zusammenfassung erstellen** und **Zusammenfassung neu erstellen**.

Das Modell erhält die aktuelle gespeicherte Textrevision einschliesslich gespeicherter Korrekturen, begrenzt auf `transcript_length_limit` (Standard 30000 Zeichen; `-1` bedeutet unbegrenzt). Ungespeicherte Änderungen sind nicht enthalten. Die Zusammenfassung nutzt den [aufgelösten Prompt](prompts.md) und das gewählte **Textmodell**. Sie folgt keinen Weiterleitungen und ignoriert Proxy-Umgebungsvariablen. Vor dem Speichern prüft der Worker Rechte, Kontostatus, lokale Modellverfügbarkeit und Textrevision erneut.

Pro Diktat wird eine Zusammenfassung gespeichert, gebunden an ihre Quellrevision. Eine spätere Textrevision macht sie veraltet; der Bereich meldet **Der Bezug zur aktuellen Textfassung ist nicht bestätigt.** Historische Aufnahmezusammenfassungen ohne bestätigte Revision gelten ebenfalls als veraltet. Korrekturen fordern nicht automatisch eine neue Zusammenfassung an.

Die erste erfolgreiche ASR-Revision kann eine automatische Zusammenfassung einreihen, wenn:

- `disable_auto_summarization` nicht `true` ist;
- die Präferenz `auto_summarization` der diktierenden Person nicht ausgeschaltet ist;
- die diktierende Person das Diktat ansehen und bearbeiten darf;
- der erste gespeicherte ASR-Text verwendbar und mindestens 10 Zeichen lang ist;
- ein lokales Textmodell bereit ist.

Bestehende Diktate werden nicht automatisch nachverarbeitet. Wiederholte Erkennung und spätere Korrekturen lösen diesen Auftrag der ersten Revision nicht aus. Die Zusammenfassung läuft in der bestehenden Hintergrundwarteschlange für Zusammenfassungen; das Öffnen des Bereichs ist nicht ihr Auslöser. Jeder Lesezugriff wird als `recording.content.read` mit `part=summary` auditiert, jede manuelle Anforderung als `recording.reprocessed` mit `kind=summary`.

Ohne bereites lokales Modell wird keine automatische Zusammenfassung eingereiht und die Erstellen-Schaltfläche fehlt; eine direkte Anfrage liefert 503. Vorhandene gespeicherte Zusammenfassungen bleiben lesbar. Fehlender verwendbarer aktueller Text ergibt 409, fehlendes Bearbeitungsrecht 403. Diese Zustände unterscheiden sich von einer fehlgeschlagenen Hintergrundgenerierung, die als **Die Erstellung ist fehlgeschlagen.** erscheint.

## Titelverarbeitung und Grenzen

Für mit Klinikdiktaten verknüpfte Aufnahmen erzeugt der ältere Folgeauftrag nur den Titel; er überspringt die normale Aufnahmezusammenfassung. Die revisionsgebundene Zusammenfassung hat einen eigenen Auftrag. Unverknüpfte Altaufnahmen können weiterhin den normalen Zusammenfassungspfad verwenden.

Die Titel- und älteren Zusammenfassungspfade besitzen nicht die Lokalitätsprüfung des Workers für Revisionszusammenfassungen. Konfigurieren Sie für die gesamte Installation einen lokalen Textendpunkt oder lassen Sie die Textmodellkonfiguration ungesetzt. `disable_auto_summarization` stoppt automatische Zusammenfassungen, nicht die Titelerzeugung. Der ältere Client kann zudem Proxy-Umgebungsvariablen übernehmen; prüfen Sie, dass lokaler Verkehr lokal bleibt.

| Grenze | Standard |
| --- | --- |
| `CLINIC_LLM_TIMEOUT_SECONDS` | 120 Sekunden je Titel-/Zusammenfassungsaufruf |
| `CLINIC_LLM_MAX_RETRIES` | 2 |
| `SUMMARY_MAX_TOKENS` | 3000 Ausgabetokens |
| `TITLE_MAX_TOKENS` | 5000 Ausgabetokens |

Falls verwendet, muss `TEXT_MODEL_EXTRA_BODY` ein vom lokalen Server akzeptiertes JSON-Objekt sein; es wird zuletzt ergänzt und kann Anfrageparameter übersteuern. Ungültige JSON-/Objektwerte lassen Anfragen scheitern. Lassen Sie `PREFIX_CACHE_OPTIMIZED_PROMPTS` ausgeschaltet, solange das veränderte Promptlayout für die Installation nicht geprüft wurde. Modellwahl und flüssiger generierter Text belegen keine klinische Eignung.

Führen Sie nach Konfigurationsänderungen ein synthetisches Diktat über den vorgesehenen CPU-/GPU-Pfad aus und prüfen Sie die Zusammenfassung separat. Prüfen Sie bei `model_not_found` die tatsächlich bereitgestellte Kennung: GPU-4xx-Fehler lösen keinen CPU-Fallback aus. Prüfen Sie bei gesperrter Zusammenfassung alle konfigurierten Textmodell-URLs, nicht nur `TEXT_MODEL_BASE_URL`.

[Originalhandbuch von Speakr in der archivierten Fassung](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/model-configuration.md)
