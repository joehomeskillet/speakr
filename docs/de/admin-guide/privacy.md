# Datenschutz und regulierte Daten
<small>[English](../../admin-guide/privacy.md)</small>

Diese Seite richtet sich an die Administration des reinen Web-Klinik-Diktatforks von Speakr v0.11.1 (fixierte Upstream-Basis `48d3018`). Sie zeigt, was die Klinikversion speichert, an andere Dienste sendet und im Browser behält, welche Upstream-Funktionen, die Daten nach aussen senden oder offenlegen würden, entfernt oder gesperrt sind, und wo die Nachweise enden. Hosting, Umsysteme und die Personen mit Zugriff liegen ausserhalb dieser Seite. Nichts auf dieser Seite ist eine Rechtsberatung.

Alles Folgende gilt nur, wenn `CLINIC_MODE` den Wert `1`, `true`, `yes` oder `on` hat. Der Schalter wird bei jedem Aufruf neu gelesen. Ohne Klinikmodus sind die Routenprüfung, die Funktionssperren und die Prüfungen des Klinikmodus wirkungslos. Aus dem Code entfernte Funktionen bleiben entfernt, und die Integritätstrigger bleiben installiert. Dieses Handbuch beschreibt den Betrieb ohne Klinikmodus nicht.

## Status und Grenzen der Nachweise

- Für diese Version wird keine Konformität mit HIPAA, der DSGVO, dem Schweizer Datenschutzgesetz oder einem anderen Gesetz oder Regelwerk beansprucht, und sie ist nach keinem davon zertifiziert. Die Klinikversion hat keine klinische Freigabe, gilt nicht als produktionsreif und erreicht keine 1:1-Parität mit dem bisherigen Diktatprodukt.
- Die Vertragsdatei, die der Klinikmodus lädt, `clinic-spec/config/dev.defaults.json`, ist das Entwicklungsprofil `local-web-parity-lab` mit `synthetic_only: true`. Ihr Block `privacy` setzt `production_approved`, `allow_real_data` und `retention_policy_approved` auf `false`.
- `clinic-spec/config/production.template.json` wird zur Laufzeit nicht gelesen. Die Datei hat `enabled: false`, eine leere Liste `approved_gates` und `null` für jeden Aufbewahrungswert (`policy_reference`, `audio_days`, `transcript_days`, `audit_days`, `backup_days`) und jeden Wiederherstellungswert (RPO, RTO, Wiederherstellungsnachweis).
- Tests mit Mocks, Fixtures oder synthetischen Daten sind kein Nachweis für Philips-Geräte, Microsoft Entra, ein KIS (Krankenhausinformationssystem) oder Fusspedale.
- Die Anwendung verschlüsselt keine gespeicherten Daten. Datenbankinhalte, Originaldateien, Arbeitskopien und exportierte Dateien liegen unverschlüsselt vor.
- Die Web-Paritätslücken in `clinic-spec/planning/web-parity-gaps.json` bleiben offen. Die für den Datenschutz wichtigen Lücken sind an der jeweiligen Stelle genannt.

Statusbegriffe: siehe [Statusbegriffe](index.md#statusbegriffe).

## Standardmässige Sperre im Klinikmodus

- Jeder Endpunkt einer Anfrage wird in der Routentabelle des Klinikmodus nachgeschlagen. Ein Endpunkt, den die Tabelle nicht enthält, wird mit 403 `clinic_unclassified_route` und einer Warnung im Protokoll abgewiesen.
- Eine Funktion ist nur nutzbar, wenn die Zuordnung `features` in `dev.defaults.json` (oder im Verzeichnis aus `CLINIC_CONFIG_DIR`) sie mit dem Wert `true` nennt. Ein Name, den die Datei nicht kennt, ist gesperrt. Eine Route einer gesperrten Funktion antwortet mit 403 `clinic_feature_disabled`.
- Die Schalter liegen in der Vertragsdatei, nicht in der Administrationsoberfläche. Jede Upstream-Funktion, die Daten nach aussen sendet oder offenlegt, steht dort auf `false`: `public_sharing`, `personal_api_tokens`, `token_query_parameters`, `user_webhooks`, `auto_deletion`, `email_exports`, `speaker_identification` und weitere. `internal_sharing`, `push_notifications` und `cloud_provider_selection` sind nicht genannt und damit gesperrt. Dieses Handbuch gibt keine Anweisung, diese Werte zu ändern.
- Chat, Inquire und die generative Zusammenfassung folgen nicht ihrem Schalter. Sie hängen nur von einem lokalen Textmodell ab (siehe [Optionale lokale KI](#optionale-lokale-ki)).

## Ausschliesslich lokale Verarbeitung

### Spracherkennung

Status: Verfügbar. Die Spracherkennung läuft auf Diensten, die die Institution selbst betreibt. Es gibt keine Cloud-Spracherkennung und keine Ausweichlösung in eine Cloud.

- Es sind nur drei Transkriptionsanbindungen registriert: `openai_whisper` (ein OpenAI-kompatibler lokaler Dienst wie Speaches, Adresse in `TRANSCRIPTION_BASE_URL`), `asr_endpoint` (WhisperX oder whisper-asr-webservice, Adresse in `ASR_BASE_URL`) und `clinic_stub`. Alle anderen Upstream-Anbindungen sind entfernt (Nur Upstream). Jeder andere Wert von `TRANSCRIPTION_CONNECTOR` scheitert mit `Unknown connector: <name>`.
- Der Vertrag deklariert `asr.cloud_fallback`, `asr.realtime_cloud_fallback`, `asr.diarization` und `asr.llm_postprocessing` als `false`. Der Code liest diese Werte nicht; das auf dieser Seite beschriebene Verhalten ergibt sich aus dem Code. Die einzige Ausweichlösung im Code führt von einem lokalen GPU-Dienst zum lokalen CPU-Dienst.
- Im Klinikmodus verweigert die Anbindung `openai_whisper` den Betrieb, wenn ihre Adresse nicht lokal ist. Eine leere oder nicht lokale `TRANSCRIPTION_BASE_URL` stoppt die Anbindung mit `CLINIC_MODE is active: transcription base URL must point to a local or private service ...`. Das gilt auch für die Standardadresse von OpenAI. Lokal ist: eine Loopback-Adresse (`localhost`, `127.0.0.0/8`, `::1`), eine private Adresse (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `fc00::/7`), eine Link-Local-Adresse (`169.254.0.0/16`, `fe80::/10`), ein Hostname ohne Punkt oder ein Name, der auf `.local`, `.internal`, `.lan` oder `.localhost` endet.
- Offene Lücke: Die Anbindung `asr_endpoint` prüft nur, ob `ASR_BASE_URL` gesetzt ist. Seine Adresse wird im Klinikmodus nicht auf Lokalität geprüft. Setzen Sie `ASR_BASE_URL` nur auf einen Host der Institution.
- Standardmässig aus: zuerst GPU, dann CPU. Ist `CLINIC_GPU_ASR_BASE_URL` gesetzt, geht ein Auftrag zuerst an den GPU-Dienst. Bei einer Zeitüberschreitung, einem Verbindungsfehler, einer HTTP-5xx-Antwort oder einem nicht gesunden `/healthz` läuft derselbe Auftrag einmal auf dem CPU-Dienst. Das GPU-Ziel verwendet dieselbe Anbindungsklasse, daher gilt für seine Adresse dieselbe Lokalitätsprüfung.
- Standardmässig aus: Transkriptionshinweise. Im Klinikmodus werden Initial-Prompt und Hotwords aus allen Quellen (Administrationsvorgaben, Benutzer, Tag, Ordner) verworfen, ausser `CLINIC_ASR_PROMPTS_ENABLED` ist eingeschaltet. Dann teilen sich Prompt und Hotwords eine Obergrenze von 55 Wörtern.
- Hotwords und Profileinstellungen beeinflussen nur die Dekodierung, und Einträge im **Wortschatz** werden nicht mit der Batch-Erkennungsanfrage gesendet. Nichts davon ist akustisches Training. Läufe, die mit **Optimierung starten** gestartet werden, werden mit `CAPABILITY_UNAVAILABLE` abgewiesen; es wird kein Modell trainiert oder angepasst.
- `clinic_stub` liefert feste synthetische Sätze. Er ist ein synthetischer Testkonnektor und verweigert den Betrieb mit `CLINIC_PROFILE=production`.
- Beim Start wird pro Dienst eine Warnung protokolliert, wenn `ASR_BASE_URL`, `TRANSCRIPTION_BASE_URL`, eine Adresse eines Text-, Chat- oder Übersetzungsmodells oder `EMBEDDING_BASE_URL` unverschlüsseltes `http://` zu einem anderen Rechner verwendet. Auch private Adressbereiche werden gemeldet, weil die Inhalte trotzdem ein Netzwerk durchqueren. Die Warnung verhindert den Start nicht.
- Die Erkennungsqualität für deutschsprachige klinische Diktate ist nicht gemessen.

Einrichtung: [Lokale Spracherkennung](whisperx-setup.md) und [Modellkonfiguration](model-configuration.md).

### Optionale lokale KI

- Ein Sprachmodell-Client entsteht nur, wenn `TEXT_MODEL_BASE_URL` oder `TEXT_MODEL_API_KEY` gesetzt ist. Ohne `TEXT_MODEL_BASE_URL` verwendet der Client die Standardadresse im Code, `https://openrouter.ai/api/v1`, einen gehosteten Dienst.
- Die Routen für Chat, Inquire und die generative Zusammenfassung sind gesperrt, ausser `TEXT_MODEL_BASE_URL` ist lokal und jede gesetzte Adresse in `CHAT_MODEL_BASE_URL`, `TRANSLATION_MODEL_BASE_URL` und `EMBEDDING_BASE_URL` ist ebenfalls lokal. Eine einzige öffentliche Adresse sperrt alle drei Funktionen. Für Textmodelle gilt als lokal: eine Loopback- oder private Adresse oder ein Hostname aus `CLINIC_LOCAL_MODEL_HOSTS` (Standard `speaches,host.docker.internal,localhost`). Jeder andere Hostname ohne Punkt gilt nicht als lokal, und eine Adresse mit Benutzerangaben, Backslash oder Leerraum wird abgewiesen.
- Die Klinikoberfläche hat weder einen Chat noch eine Oberfläche für Ask Ambrose (siehe [Ambrose-Einstellungen](ambrose.md)).
- Offene Lücke: automatischer Titel und automatische Zusammenfassung. Im Klinikmodus stellt eine abgeschlossene Transkription einen Auftrag `summarize` für den Titel ein und, wenn automatische Zusammenfassungen gelten, für die Zusammenfassung des Standard-Aufnahmedatensatzes. Dieser Weg hat keine eigene Lokalitätsprüfung. Ist nur `TEXT_MODEL_API_KEY` gesetzt, verwendet der Client die gehostete Standardadresse `https://openrouter.ai/api/v1`, und automatischer Titel und automatische Zusammenfassung senden das Transkript dorthin. Dasselbe gilt für eine gehostete `TEXT_MODEL_BASE_URL`. Konfigurieren Sie ein lokales Textmodell oder keines. Die Systemeinstellung `disable_auto_summarization` (Standard `false`) und der persönliche Schalter für automatische Zusammenfassungen stoppen die Zusammenfassung, nicht den Titel.
- Offene Lücke: Mit `ENABLE_INQUIRE_MODE=true` (Standard `false`) stellt jede abgeschlossene Aufnahme einen Indexauftrag für Transkriptpassagen und Embeddings ein, ohne Klinikprüfung. Lassen Sie `ENABLE_INQUIRE_MODE` ausgeschaltet.
- Kein Sprachmodell schreibt den Diktattext um. Eine Transkriptrevision ist entweder `ASR` oder `HUMAN` (`TRANSCRIPT_KINDS` in `src/clinic/models.py`).
- In Prüfung: eine KI-Zusammenfassung pro Diktat, schreibgeschützt angezeigt im Bereich **KI-Zusammenfassung**. Sie läuft nur, wenn der Klinikmodus eingeschaltet ist, die generative Zusammenfassung entsperrt ist, ein Sprachmodell-Client besteht und seine Adresse lokal ist. Es gibt keine Ausweichlösung auf einen gehosteten Dienst. Die Anfrage ignoriert Proxy-Umgebungsvariablen, folgt keinen Weiterleitungen und ersetzt einen Anbieterfehler durch eine allgemeine Meldung, damit der Fehlertext das Transkript nicht zitieren kann. Eingabe ist die aktuell gespeicherte Textrevision, gekürzt auf `transcript_length_limit` (Standard 30000 Zeichen). Die Zusammenfassung ändert den Diktattext nie. Automatisch entsteht sie nur für den ersten erkannten Text eines neuen Diktats, und nur wenn `disable_auto_summarization` ausgeschaltet ist, der persönliche Schalter der diktierenden Person für automatische Zusammenfassungen eingeschaltet ist und diese Person die Rechte zum Ansehen und Bearbeiten hat. Wer das Diktat bearbeiten darf, kann sie für den aktuell gespeicherten Text (mindestens 10 Zeichen) mit **Zusammenfassung erstellen** bzw. **Zusammenfassung neu erstellen** anfordern. Jedes Lesen des Bereichsstands, auch jede Abfrage während der Erstellung, wird als `recording.content.read` mit `part=summary` protokolliert, jede Anforderung als `recording.reprocessed` mit `kind=summary`. Mit dieser Funktion erhält die Arbeitskopie eines verknüpften Diktats nur einen Titel, keine Standardzusammenfassung. Die Administrationsseite **Modellwahl**, ebenfalls in Prüfung, speichert nur Modellnamen; Dienstadressen und Schlüssel bleiben in Umgebungsvariablen. Diese Funktionen sind nicht Teil der aktuell veröffentlichten Version; Bezeichnungen und Verfügbarkeit können sich ändern.

### Sprecherdienst

Status: Standardmässig aus. `CLINIC_SPEAKER_SERVICE_URL` nennt einen separaten lokalen Prozess, der Stimmvektoren (Embeddings) berechnet. Jeder Aufruf wird einmal versucht, mit einer Zeitgrenze von 120 s (5 s für die Zustandsprüfung). Offene Lücke: Die Adresse wird nicht auf Lokalität geprüft. Setzen Sie sie nur auf einen Host der Institution. Der Dienst wird nur für Stimmprofile mit Einwilligung verwendet (siehe [Stimmprofile](#stimmprofile-nur-mit-einwilligung)).

## Gespeicherte Daten der Klinikversion

Die Upstream-Tabellen bleiben in der Datenbank (standardmässig SQLite, wahlweise PostgreSQL). Im Klinikmodus enthält der Standard-Datensatz `recording` der Transkriptions-Arbeitskopie das erkannte Transkript und, wenn ein Textmodell konfiguriert ist, den Titel und die Zusammenfassung. Der Ankerdatensatz des Diktats übernimmt den Titel, sofern er noch keinen hat. Die Klinikversion ergänzt folgende Ablagen und Dateien.

| Daten | Ort | Schutz |
|---|---|---|
| Diktatoriginale, bitgenau geschrieben und mit fsync gesichert | Dateien unter `CLINIC_ORIGINALS_ROOT` (Standard `UPLOAD_FOLDER`) unter `clinic/originals/v1/<SHA-256 des Idempotenzschlüssels>`; Quittung mit Hash und Grösse in `clinic_original_version` | Immer im lokalen Dateisystem, unabhängig von `FILE_STORAGE_BACKEND`. Datenbanktrigger verweigern UPDATE und DELETE der Quittung. |
| Arbeitskopie für die Transkription | Speicher-Backend, Schlüssel `clinic/imports/asr/<id>/Diktat.<ext>`, und ein Standard-Datensatz `recording` mit dem Dateinamen `Diktat.<ext>` | Gelangt mit `FILE_STORAGE_BACKEND=s3` in den S3-Bucket. |
| Audio und Teilstücke während Konvertierung und Transkription | Temporärer Ordner des Containers | Wird am Ende des Auftrags gelöscht; kann nach einem Absturz bleiben, bis der Container neu erstellt wird. |
| Teilstücke einer Standardaufnahme im Browser und Teile eines grossen Standard-Uploads in Arbeit | `UPLOAD_FOLDER/_sessions/<Sitzungs-ID>/` | Wird beim Abschluss entfernt; verlassene Sitzungen behandelt die stündliche Bereinigung (siehe [Löschung, Archivierung und Aufbewahrung](#loschung-archivierung-und-aufbewahrung)). |
| Dateien in Konvertierung | `UPLOAD_FOLDER/_staging/` oder `FILE_STORAGE_STAGING_DIR` | Lokales Dateisystem. |
| Wellenformdaten | `UPLOAD_FOLDER/_peaks/` | Wird mit dem Audio entfernt. |
| Abspielbare MP3-Datei eines DS2/DSS-Diktats | `<Stamm>.playback.mp3` neben der Audiodatei der Aufnahme | Abgeleitete Kopie für die Wiedergabe im Browser; das Original bleibt unverändert. |
| Jede erkannte und jede menschliche Textrevision | `clinic_transcript_revision` (vollständiger Text) | Datenbanktrigger verweigern UPDATE und DELETE. |
| Audiobearbeitungen | `clinic_audio_revision` und die Tabellen der Audiobearbeitung | Nichtdestruktive Bearbeitungsliste über dem Original. Das ORM verweigert UPDATE und DELETE in den Bearbeitungstabellen; deren Datenbanktrigger sind vorbereitet, werden beim Start aber nicht installiert. |
| Fallkontext: Quellsystem, Mandantenbereich, Fall- und Patientenreferenz | `clinic_context_revision` | Eine Zeile pro Revision. |
| Freigaben mit den exakten Audio-, Text- und Kontextrevisionen und dem Text-Hash; KIS-Zustellprotokoll mit Hash der Nutzdaten und externer Dokument-ID | `clinic_release`, `clinic_delivery` | Verlaufszeilen der Zustellung werden angefügt, nie geändert. |
| Verlauf der Stimmeinwilligung (Status, Zweck, SHA-256 der Erklärung), Stimmvektoren (nur Vektoren, kein Audio), Auditzeilen der Einwilligung | `clinic_voice_consent`, `clinic_voice_embedding`, `clinic_voice_consent_audit` | Einwilligung und deren Audit sind durch Datenbanktrigger nur anfügbar. |
| Importagent: Inventar (relativer Pfad, Grösse, Änderungszeit, SHA-256), Aufträge, Übertragungssitzungen, Upload-Teile mit Audiobytes, gemeldete Ergebnisse | `clinic_agent_inventory`, `clinic_agent_intent`, `clinic_agent_ingest`, `clinic_agent_chunk`, `clinic_agent_outcome` | Upload-Teile werden gelöscht, sobald die Übertragung gespeichert oder abgebrochen ist. Ergebnisse ändert der Kanal nie. |
| Aktivierungs- und Rücksetzcodes | `clinic_local_recovery` | Gespeichert wird nur der SHA-256 des Codes. |
| Ausgabequittungen | `clinic_output_receipt` | Durch Datenbanktrigger nur anfügbar. |
| Änderungen an Pools, Mitgliedschaften, Leitungen und Fachrechten mit alten und neuen Werten | `clinic_pool_history` | Das ORM verweigert UPDATE und DELETE; es gibt keinen Datenbanktrigger. Keine Seite listet die Zeilen auf. |
| Auditprotokoll | `audit_event` | Siehe [Auditprotokoll](audit-log.md). |

Mit `PRESERVE_TEMP_AUDIO=true` bleibt von jeder konvertierten Datei eine Debug-Kopie im temporären Ordner; lassen Sie die Variable ungesetzt.

Tabellen öffentlicher Links und interner Teilungen aus einer früheren Version (`share`, `internal_share`, `shared_recording_state`, `share_audit_log`) werden von dieser Version weder gelesen noch beschrieben noch entfernt.

Die Geheimnisse des Importagenten (`CLINIC_AGENT_PROXY_SECRET`, `CLINIC_AGENT_MASTER_SECRET`) werden aus Umgebungsvariablen gelesen. Wo Originale, Arbeitskopien und Sicherungen liegen, beschreibt [Dateispeicher](storage.md).

## Originale und Revisionen sind unveränderlich

- Datenbanktrigger verweigern UPDATE und DELETE von Originalen und Transkriptrevisionen. SQLite antwortet mit `clinic original is immutable` oder `clinic original cannot be deleted` und mit `clinic transcript revision is immutable` oder `clinic transcript revision cannot be deleted`. PostgreSQL antwortet für beide Operationen mit `clinic original is immutable` oder `clinic transcript revision is immutable`. Andere Datenbanksysteme erhalten keinen Trigger.
- Der Ankerdatensatz eines Diktats behält einen leeren `audio_path`, damit ein Standard-Konvertierungsauftrag das Original nicht überschreiben kann.
- Beim Start im Klinikmodus wird der Originalspeicher geprüft, mit einer Protokollzeile: `Clinic original sweep: scanned=… removed_leftovers=… incomplete=… awaiting_row=… errors=…`. Entfernt werden nur Reste unterbrochener Schreibvorgänge (`.partial-`-Hardlinks); unvollständige Dateien und Dateien ohne Datenbankzeile werden gemeldet, nie gelöscht.

## Löschung, Archivierung und Aufbewahrung

- Status: Nur Upstream. Das manuelle endgültige Löschen ist gesperrt: Die Standard-Löschrouten für Aufnahmen und deren Audio, das Sammel- und API-v1-Löschen und das Löschen von Benutzern durch die Administration antworten mit 403 `clinic_feature_disabled`. Konten werden deaktiviert statt gelöscht (siehe [Benutzerverwaltung](user-management.md)).
- Die automatische Aufbewahrung archiviert nur. Jeder Wert von `DELETION_MODE` archiviert und behält Dateien und Zeilen. Ankerdatensätze von Diktaten werden von der Aufbewahrung ausgenommen angelegt; der Lauf überspringt sie daher, solange die Ausnahme nicht aufgehoben wird (die Klinikoberfläche hat dafür kein Bedienelement; siehe [Aufbewahrung](retention.md)). Eine genehmigte Aufbewahrungsrichtlinie der Klinik gibt es nicht. Siehe [Aufbewahrung](retention.md).
- Im Klinikmodus löscht die tägliche Bereinigung des Auditprotokolls nichts und protokolliert `Audit pruning withheld (separate_privileged_retention): N row(s) past cutoff ... kept`. Im Klinikmodus löscht kein Prozess Auditzeilen; der separat berechtigte Aufbewahrungsprozess ist nicht Teil des Repositorys.
- Was im Klinikmodus weiterhin gelöscht wird:
    - alle Stimmvektoren einer Person, wenn diese ihre Stimmeinwilligung widerruft;
    - Upload-Teile des Importagenten, sobald die Übertragung gespeichert oder abgebrochen ist;
    - Aufnahmen ohne Eigentümer, durch die tägliche Bereinigung um 02:00 Serverzeit nach einer Markierung im vorherigen Lauf (unverändert Upstream);
    - Löschmarker (Tombstones), Zustellzeilen von Webhooks und verlassene Aufnahmesitzungen (eine Browseraufnahme mit Teilstücken wird stattdessen als wiederhergestellte Aufnahme abgeschlossen), durch die stündliche Upstream-Bereinigung (unverändert Upstream); `RECORDING_SESSION_CLEANUP_INTERVAL_SECONDS=0` schaltet diese Bereinigung aus.
- Sicherungen liegen ausserhalb der Anwendung. Eine Sicherung muss `CLINIC_ORIGINALS_ROOT` umfassen, das die Originale auch mit `FILE_STORAGE_BACKEND=s3` enthält, und denselben Schutz erhalten wie das laufende System.

## Browserspeicher, eigene Geräte (BYOD) und Offline-Betrieb

### Clientprofile

Das Clientprofil ist eine Entscheidung des Servers. Kein Header, kein User-Agent und kein Hostname des Browsers wählt es aus. Bis eine vertrauenswürdige Zuordnung am Eingang existiert, erhält jede Anfrage das Standardprofil `managed_web_online`. Die Profile in `clinic-spec/config/client-capabilities.json`:

| Profil | Bezeichnung in der Oberfläche | Audio dauerhaft im Browser gespeichert | Dokumentexport |
|---|---|---|---|
| `managed_web_online` (Standard) | **Verwaltetes Web, nur online** | Nein | Über Rechte gesteuert |
| `byod_internal` | **Eigenes Gerät, nur online** | Nein | Nein |
| `kis_webview` | **KIS-Ansicht, nur online** | Nein | Nach Herstellervertrag und Richtlinie |
| `managed_web_offline` | **Verwaltetes Web, Offline freigegeben** | Verschlüsselt, nach ausdrücklicher Zustimmung und erst nach dem Gate `G-MANAGED-WEB-OFFLINE` | Über Rechte gesteuert |

Weil heute keine Anfrage dem Profil `byod_internal` zugeordnet wird, greifen die davon abhängigen Regeln (zum Beispiel die Verweigerung von Bündelexporten für nicht verwaltete Clients) noch nicht. Die folgenden Regeln zum Browserspeicher gelten für jeden Browser.

### Was der Browser behält

Status: Verfügbar.

- Die Klinikaufnahme (**Neue Aufnahme**) hält Audio und Titel nur im Arbeitsspeicher und schreibt nie in IndexedDB, `localStorage` oder `sessionStorage`. Nach einem fehlgeschlagenen Upload erscheint: **Der Upload ist fehlgeschlagen. Die Aufnahme wird nicht lokal gespeichert. Bitte erneut aufnehmen.**
- Die IndexedDB-Speicher der Standardaufnahme und des Standard-Uploads, `SpeakrRecordings` (Wiederherstellung nach Absturz) und `SpeakrFailedUploads` (erneuter Versuch), sind im Klinikmodus gesperrt. Sie werden nur verwendet, wenn die Seite `data-clinic-persistent-audio="True"` trägt, und keine Vorlage gibt dieses Attribut aus.
- Die Ansicht der Stimmeinwilligung und die Offline-Module schreiben nichts in den Browserspeicher und lesen oder schreiben kein Anmelde- oder Sitzungstoken.
- Klinikwerte in `localStorage`: die Fusspedal-Einstellungen des Arbeitsplatzes (Tastencodes, Sprungsekunden, automatisches Zurückspulen) und pro Person die ID des zuletzt gewählten Arbeitslistenfilters. Die Filter selbst liegen auf dem Server.
- Nicht durch den Klinikmodus gesperrt (unverändert Upstream): Darstellungseinstellungen (Farbschema, Spaltenbreiten, Lautstärke, Wiedergabegeschwindigkeit), die ID der zuletzt gewählten Aufnahme, die Vorgaben des Upload-Dialogs und, für einen Standard-Upload über 16 MiB, der in Teilen gesendet wird, Dateiname, Grösse und Änderungszeit mit der Sitzungs-ID für bis zu 24 Stunden. `sessionStorage` enthält die Zeitzone und einen ausgeblendeten Mikrofonhinweis.
- Der Service Worker wird auf den Seiten nach der Anmeldung registriert, auch auf den Klinikseiten, wenn der Browser einen sicheren Kontext hat (`https` oder `http` auf `localhost` oder `127.0.0.1`). Sein Cache Storage (`speakr-static-<Version>`) enthält nur Anwendungsdateien unter `/static/`. Keine Seite und keine Antwort unter `/api/`, `/audio/`, `/recording/` oder `/admin` wird gespeichert.
- Beim Abmelden setzt der Server das Cookie `speakr_signed_out` (ohne Daten), und die folgende Anmeldeseite leert den Cache Storage, den `sessionStorage` des Tabs, die IndexedDB-Speicher `SpeakrRecordings` und `SpeakrFailedUploads` und den `localStorage`-Eintrag `speakr.slicedUploads`. Eine Abmeldung durch die Leerlaufgrenze ist kein Abmelden und leert nichts. Darstellungseinstellungen, die Fusspedal-Einstellungen, die ID des Arbeitslistenfilters, die Sitzungs-ID der Standardaufnahme (`speakr.serverRecordingSession`, nur mit `ENABLE_SERVER_RECORDING_CHUNKS=true` geschrieben, Standard `false`) und der `sessionStorage` anderer offener Tabs bleiben erhalten.
- Die Anwendung kann nicht garantieren, dass ein Browser keine Daten behält: Downloads, der Browsercache und Kopien des Betriebssystems liegen ausserhalb ihrer Kontrolle.

### Verwaltetes Offline-Profil

Status: Vorbereitet, deaktiviert.

- `GET /api/clinic/v1/offline-web/capability` (Klinikmodus, Browsersitzung, `Cache-Control: no-store`) antwortet immer mit `available: false` und der Meldung `Offline-Speicherung ist nicht freigegeben.`. Es sind weder eine vertrauenswürdige Browserprüfung noch eine Quelle für Freigaben angebunden.
- Der Vertrag setzt `web_client.managed_offline_enabled` und `web_client.byod_persistence_enabled` auf `false`; der Code liest nur den ersten Wert. Das Profil `managed_web_offline` hat `enabled_by_default: false`.
- Auch nach einer Anbindung erlaubt der Server die Offline-Speicherung nur für das Profil `managed_web_offline`, einen geprüften Browser, eine aktuelle Serverfreigabe mit Version, Kontingent und Schlüsselrichtlinie `encrypted_user_unlock` und `managed_offline_enabled: true`.
- Die Browsermodule versiegeln jeden Datensatz mit AES-GCM, gebunden an Person, Profil, Diktat und Sequenz. Der Datenschlüssel wird mit einem aus der Passphrase der Person abgeleiteten Schlüssel umhüllt (PBKDF2-SHA-256, 600000 Iterationen) und nur im Arbeitsspeicher gehalten. Es gibt keinen voreingestellten Speicheradapter und keinen Ersatzspeicher. Eine widerrufene Freigabe und das Abmelden löschen die gespeicherten Datensätze. Ohne Freigabe wird nichts gespeichert.
- Die Ansicht **Offline und Wiederherstellung** im Menü **Verwaltung & Seiten** zeigt: **Offline-Speicherung ist nicht freigegeben. Es wird nichts lokal gespeichert und es gibt keine lokale Wiederherstellung. Bereits auf dem Server bestätigte Daten bleiben über die Arbeitsliste erreichbar.**

## Token und Geheimnisse

- Kein Token und kein Geheimnis liegt im Browserspeicher. Das Sitzungscookie ist `HttpOnly` und für Skripte nicht lesbar, das CSRF-Token kommt mit jeder Seite, und kein Klinikmodul schreibt ein Token in `localStorage`, `sessionStorage` oder IndexedDB.
- Kein Token in der URL. Die Einstellung `api_query_tokens_enabled` ist im Klinikmodus fest auf `false` gesetzt. Ein Token im Abfrageparameter `?token=` authentisiert bei keiner Methode. Ein Schreibzugriff auf die Einstellungen, der den Wert anheben will, wird mit 403 `clinic_policy_ceiling` abgewiesen, und ein abweichender gespeicherter Wert wird beim Start als `clinic policy overrides stored setting <key>=<value> (effective: False)` protokolliert.
- Persönliche API-Token sind gesperrt (Nur Upstream). Eine Anfrage, die sich statt mit einer Browsersitzung mit einem Token ausweist, wird auf jeder nicht öffentlichen Route mit 403 `clinic_feature_disabled` abgewiesen. Die Tokenverwaltung und der SSO-Tokentausch sind gesperrt, und `sso_api_tokens_enabled` ist fest auf `false` gesetzt.
- Die Anmeldung ist nur lokal; die OIDC-Routen von Upstream verweigern im Klinikmodus, und Microsoft Entra ist vorbereitet, deaktiviert (siehe [Anmeldung und Identitätsanbieter](sso-setup.md)). Lokale Passwörter haben 15 bis 128 Zeichen, eine eingebaute Sperrliste, die `CLINIC_PASSWORD_BLOCKLIST_FILE` erweitert, und scrypt-Hashes. Die Administration setzt nie Passwörter. Ein Einmalcode (**Einmalcode ausstellen**) hat 256 Bit, gilt 30 Minuten und einmal, wird einmal mit `Cache-Control: no-store` angezeigt und nur als SHA-256 gespeichert. Siehe [Benutzerverwaltung](user-management.md).
- Der Importagent weist sich mit einem Maschinenausweis an einem vertrauenswürdigen mTLS-Eingang aus, nie mit einer Sitzung. `CLINIC_AGENT_PROXY_SECRET` und `CLINIC_AGENT_MASTER_SECRET` brauchen je mindestens 32 Byte. Fehlt einer der Werte, wird pro Prozess ein Zufallswert verwendet, jede Agentenanfrage antwortet mit 401, und das Protokoll meldet `Clinic import agent: <NAME> is not set or shorter than 32 bytes; agent requests are refused until it is configured`.
- Sitzungscookies sind `HttpOnly` und `SameSite=Lax`. Das Flag `Secure` wird nur mit `SESSION_COOKIE_SECURE=true` gesetzt (Standard `false`). Das Cookie für **Angemeldet bleiben** gilt `REMEMBER_COOKIE_DAYS` Tage (Standard 30) und wird nicht angenommen, solange eine Leerlaufgrenze gesetzt ist. Die Leerlaufgrenze ist die Systemeinstellung `session_idle_timeout_minutes` (Standard 0, keine Grenze).
- Offene Lücke: Die Vertragswerte `identity.session_idle_minutes` (15) und `identity.session_absolute_hours` (8) werden vom Code nicht gelesen.

## Stimmprofile nur mit Einwilligung

- Status: Nur Upstream. Die Sprechererkennung von Upstream ist gesperrt (`speaker_identification`): Ihre Routen sind abgeschaltet, Stimmvektoren von Sprechern aus der Sprechertrennung (Diarisierung), Patientinnen und Patienten eingeschlossen, werden nicht an der Aufnahme gespeichert, und die kontextbezogene Benennung von Sprechern durch ein Sprachmodell liefert im Klinikmodus nichts.
- Sprechertrennung: Mit `asr_endpoint` fordert ein Auftrag beim Dienst die Sprechertrennung an, ausser `ASR_DIARIZE=false` ist gesetzt (Standard `true`). Sprecherkennungen wie `SPEAKER_00` werden dann in der Segmentliste des Transkripts der Arbeitskopie gespeichert; die Textrevision der Klinik enthält den Segmenttext ohne Kennungen. Stimmvektoren, die für getrennte Sprecher zurückkommen, werden im Klinikmodus verworfen; lassen Sie `ASR_RETURN_SPEAKER_EMBEDDINGS` ungesetzt (Standard `false`).
- Status: Verfügbar. Eine Person kann nur für ihre eigene Stimme in ein Stimmprofil einwilligen, zum Zweck `author_recognition`. Die Routen wirken nur auf die angemeldete Person: `GET /api/clinic/v1/voice-consent`, `POST /api/clinic/v1/voice-consent/grant` und `POST /api/clinic/v1/voice-consent/revoke`, dazu die Erfassungs-API unter `/api/clinic/v1/voice/`. Schreibzugriffe auf `/api/clinic/v1/voice-consent` brauchen die Browsersitzung, einen Header `X-CSRF-Token` und einen `Idempotency-Key` mit 8 bis 255 Zeichen; ein Anfragekörper, der eine andere Person nennt (zum Beispiel `user_id`, `actor`, `patient_id`, `role`, `is_admin`), wird mit 403 `forbidden_field` abgewiesen. Schreibzugriffe auf die Erfassungs-API brauchen die Browsersitzung, einen CSRF-Header, einen `Idempotency-Key` mit 8 bis 128 Zeichen und `If-Match`; eine Probe ist ein WAV-Körper (16 kHz, mono, 16 Bit, 3 bis 30 Sekunden). Weder die Administration noch ein Dienst kann für jemand anderen einwilligen oder eine Stimme erfassen.
- Gespeichert werden: jede Erteilung und jeder Widerruf als neue Zeile mit dem SHA-256 der angezeigten Erklärung, nicht deren Text; erfasste Stimmvektoren nur als Vektoren, nie als Audio. Die temporäre WAV-Datei einer Erfassung wird nach der Vektorberechnung gelöscht. Eine Erfassung ohne aktive Einwilligung wird abgewiesen.
- Ein Widerruf löscht in derselben Transaktion alle erfassten Stimmvektoren der Person in allen Vektorräumen und schreibt eine Auditzeile mit der Anzahl gelöschter Vektoren. Der Einwilligungsverlauf bleibt. Sprecherprofile anderer Personen im Adressbuch werden nicht verändert.
- Wo Benutzende es finden: **Verwaltung & Seiten** > **Einstellungen**. Konten mit der **Administrationsrolle** finden zusätzlich den Bereich **Eigenes Stimmprofil (freiwillig)** auf der Administrationsseite **Sprachprofile** (Menüeintrag **ASR und Sprachprofile**). Auch dieser Bereich betrifft nur die eigene Stimme. Bezeichnungen: **Einwilligen**, **Einwilligung widerrufen**, **Widerruf bestätigen**; Zustände **Keine Einwilligung erteilt**, **Einwilligung erteilt**, **Einwilligung widerrufen**; nach einem Widerruf **Einwilligung widerrufen. Stimmprofil und Vorschläge sind entfernt.**
- Verfügbar (nur API): die Erfassung. Keine Seite nimmt eine Erfassungsprobe auf, und die Vektorberechnung braucht den Sprecherdienst (Standardmässig aus). Vorbereitet, deaktiviert: Der Autorenvorschlag aus Stimmprofilen mit Einwilligung ist an keine Route angebunden. Ein Vorschlag wäre immer nur ein Vorschlag, den ein Mensch bestätigt, und Patientinnen und Patienten erhalten nie ein Profil.
- Vorbereitet, deaktiviert: Eine Bereinigung der Upstream-Sprecherdaten (Sprecherzeilen, Proben, Zitate und Sprechernamen in Transkripten) existiert im Code, hat aber weder einen Befehl noch eine Route.

## Zugriff durch Administration und andere Rollen

Die Klinikversion trennt die globale Administration (**Administrationsrolle**), die lokale Identitätsverwaltung (**Identitätsadministration**), Poolleitungen (**Leitung**, auf die eigenen Pools beschränkt) und die Rechte zur Freigabe (**Freigeben**) und zum Export des Originalaudios (**Originalaudio exportieren**). Einzelheiten: [Benutzerverwaltung](user-management.md) und [Gruppenverwaltung](group-management.md).

- Eine aktive globale Administration (Administrationsrolle und aktives Klinikkonto) darf jedes Klinikdiktat ansehen und abspielen und seine Korrekturen lesen. Bearbeiten, Exportieren, Freigeben, Originalaudio exportieren, Archivieren und Löschen brauchen wie bei allen anderen ausdrücklich erteilte **Fachrechte**.
- Die Diktatübersicht der Administration liefert nur Metadaten und schreibt keine Auditzeile. Transkript und Audio jedes Diktats lassen sich ohne Bestätigung und ohne Begründung öffnen. Jede Öffnung wird einzeln protokolliert, als `recording.content.read` mit `part=transcript` oder als `recording.media.played`. Die Auditzeile wird zuerst gespeichert; kann sie nicht gespeichert werden, lautet die Antwort 503 `audit_unavailable`. Mit ausgeschaltetem `audit_log_enabled` werden solche Öffnungen daher verweigert, mit ausgeschaltetem `audit_log_record_views` auch Audioöffnungen.
- Jede fremde Zeile, die die Arbeitsliste einer globalen Administration liefert, wird als `recording.content.read` mit `part=worklist` protokolliert.
- Eine Identitätsrolle gibt keinen Zugriff auf Diktate. Die Oberfläche sagt: **Identitätsrollen geben keinen Zugriff auf Diktate. Fachrechte werden separat erteilt.**
- Ein Standard-Audiodownload (`download=true`) braucht das Recht `audio_export`. Es gehört nicht zu den eigenen Standardrechten, und die globale Administration erhält es nicht durch ihre Rolle.
- **Systemstatistik** zeigt Benutzernamen und die fünf neuesten fehlgeschlagenen Aufnahmen mit bis zu 300 Zeichen ihres gespeicherten Fehlertexts (unverändert Upstream; siehe [Protokolle](#protokolle)). Siehe [Systemstatistik](statistics.md).
- Wer Zugriff auf den Server oder die Datenbank hat, kann alles lesen. Über die Anwendung lassen sich Auditzeilen nicht ändern (das ORM verweigert es). Die Datenbanktrigger für `audit_event` existieren im Code, werden beim Start aber nicht installiert, und eine Datenbankrolle ohne DELETE auf `audit_event` ist Sache des Betriebs.

## Ausgehende Verbindungen

| Ziel | Daten | Status und Steuerung |
|---|---|---|
| Lokale Spracherkennung | Audio, Sprachkennung und Hinweise nur mit `CLINIC_ASR_PROMPTS_ENABLED` | Verfügbar. `TRANSCRIPTION_BASE_URL` und `CLINIC_GPU_ASR_BASE_URL` müssen lokal sein. `ASR_BASE_URL` wird nicht geprüft (Offene Lücke). |
| Transkriptionsdienst, beim Start | Mitgelieferter Testclip, keine Benutzerdaten | Nur mit `asr_endpoint` und `ASR_RETURN_SPEAKER_EMBEDDINGS=true`, wenn keine Referenz besteht oder sich die Konfiguration geändert hat. Aus mit `DISABLE_VOICE_EMBEDDING_CHECK=true`. |
| Textmodell (Titel, Zusammenfassung; Chat und Inquire nur bei lokalem Modell) | Transkript und Prompts | Standardmässig aus (kein LLM-Client, solange weder `TEXT_MODEL_BASE_URL` noch `TEXT_MODEL_API_KEY` gesetzt ist). Lokalitätsprüfung des Auftrags für automatischen Titel und automatische Zusammenfassung: Offene Lücke. |
| Embedding-Dienst | Transkriptpassagen | Nur mit `ENABLE_INQUIRE_MODE=true`; lassen Sie es ausgeschaltet. |
| Sprecherdienst | Audio einer Erfassungsprobe mit Einwilligung | Standardmässig aus. `CLINIC_SPEAKER_SERVICE_URL`; die Lokalität wird nicht geprüft (Offene Lücke). |
| KIS | Freigegebene Dokumente | Simuliert (nur mit `CLINIC_PROFILE=staging`, synthetischer MockKIS im Arbeitsspeicher, kein KIS-Nachweis); sonst ist der Adapter deaktiviert, sendet nichts, und die endgültige Freigabe wird abgelehnt. |
| Importagent | Nur eingehend: Der Agent verbindet sich über den mTLS-Eingang mit dem Server | Vorbereitet, deaktiviert. Die Serverrouten unter `/api/clinic/v1/agent/` sind vorhanden, aber keine unterstützte Route, kein Befehl und kein Skript registriert einen Agenten, legt eine freigegebene Quelle an oder erstellt einen Importauftrag. Die Philips-Geräteseite ist Simuliert (Simulatormodus und Testdaten, kein Gerätenachweis). Der Server baut keine Verbindung zum Agenten auf. Vertrag für den Eingang: [Aufnahmesitzungen](recording-sessions.md#kanal-des-importagenten-und-eingang). |
| Webhooks | Keine | Nur Upstream. Der Versand ist aus, solange `user_webhooks` gesperrt ist, unabhängig von `WEBHOOK_GLOBAL_ENABLED`. Siehe [Webhooks](webhooks.md). |
| Web-Push | Keine | Nur Upstream. Die Push-Routen sind gesperrt. |
| SMTP-Server | Fertigstellungs- und Fehlermail von Upstream, mit dem Aufnahmetitel im Betreff | Nicht durch den Klinikmodus gesperrt. Versand nur, wenn `SMTP_HOST`, `SMTP_USERNAME` und `SMTP_PASSWORD` gesetzt sind und die Person zugestimmt hat. Für den klinischen Einsatz nicht geprüft; lassen Sie SMTP ungesetzt. Siehe [E-Mail-Einrichtung](email-setup.md). |
| OIDC-Identitätsanbieter | Keine | Nur Upstream. Die SSO-Routen verweigern im Klinikmodus. |
| S3-Speicher | Transkriptions-Arbeitskopien | Nur mit `FILE_STORAGE_BACKEND=s3`. Originale bleiben lokal. |

Anfragen verwenden das Schema der konfigurierten Adresse. Mit einer `http://`-Adresse durchqueren Audio und Text das Netzwerk unverschlüsselt. Die Startwarnung zu unverschlüsseltem `http://` umfasst `CLINIC_GPU_ASR_BASE_URL` und `CLINIC_SPEAKER_SERVICE_URL` nicht; prüfen Sie deren Schema selbst.

Die voreingestellte Content-Security-Policy beschränkt Skripte, Stile, Schriften und Verbindungen auf den Ursprung der Instanz (Inline-Skripte und -Stile sind erlaubt). `CONTENT_SECURITY_POLICY` ersetzt sie, und `SECURITY_HEADERS_ENABLED=false` schaltet die Sicherheitsheader aus; erweitern oder deaktivieren Sie sie nicht.

Die Hinweise zum Arbeitsablauf in der Anwendung (zum Beispiel **Abnahme angefordert**) bleiben in der Anwendung. Sie verwenden feste deutsche Texte und enthalten keine Patientendaten.

## Protokolle

Die Protokollierung ist gegenüber Upstream unverändert. Alle Ausgaben gehen auf die Standardausgabe des Prozesses. Die Anwendung legt keine Protokolldatei an, und für den Anwendungsserver ist kein HTTP-Zugriffsprotokoll eingerichtet. Die Aufbewahrung der Ausgaben hängt vom Protokolltreiber des Containers und von einem allfälligen Protokollsammler des Hosts ab.

| `LOG_LEVEL` | Inhalt des Protokolls |
|---|---|
| `ERROR`, `WARNING` | Fehler, mit IDs. Ein Fehler eines Transkriptionsdienstes, eines Sprachmodell-Anbieters oder der Embedding-API wird mit Klasse, Anbieter, HTTP-Status, Fehlercode und Länge protokolliert, ohne seinen Text. |
| `INFO` (Standard) | Zusätzlich: IDs, Längen, Anzahlen, Dauern, Modellnamen und Dienstadressen. Kein Transkript, keine Zusammenfassung, kein Titel, kein Prompt, kein Hotword und keine Suchanfrage. Benutzer, Gruppen, Tags, Ordner, Sprecher, Benennungsvorlagen und Aufnahmen werden über ihre ID genannt. Eine Datei wird mit einer Kennung ohne ihren ursprünglichen Dateinamen bezeichnet. |
| `DEBUG` | Zusätzlich: Titel, Zusammenfassungen, Prompts, Modellantworten, Suchanfragen, Hotwords, Benutzernamen, Gruppen-, Tag- und Sprechernamen, ursprüngliche Dateinamen, Empfängeradressen gesendeter E-Mails sowie der vollständige Text und Traceback von Anbieterfehlern. |

`DEBUG` eignet sich nicht für regulierte Daten, ausser für eine kurze Diagnose auf einer Testinstanz.

Fehler des Betriebssystems, der Datenbank und von FFmpeg werden auf `WARNING` und `ERROR` mit ihrem Text protokolliert. Im Code für Upload, Konvertierung, Prüfung, Aufteilung, Überwachungsordner, Export, Löschen im Speicher und FFmpeg wird eine in diesem Text genannte Datei durch ihre Kennung ersetzt; im Traceback eines oberhalb von `DEBUG` protokollierten Fehlers gilt dieselbe Ersetzung für den Text der Ausnahme. In anderem Code kann ein Dateipfad in der Meldung einer Fehlerzeile erscheinen.

Eine Auditzeile, die nicht geschrieben werden kann, wird auf `ERROR` als eine Zeile protokolliert, die mit `AUDIT-FALLBACK` beginnt. Die Zeile enthält IDs und keine Inhalte; siehe [Auditprotokoll](audit-log.md).

### Fehlertext für Benutzende

Der Fehlertext eines fehlgeschlagenen Auftrags wird am Auftrag und an der Aufnahme gespeichert, anstelle des Transkripts oder der Zusammenfassung angezeigt, in der **Systemstatistik** aufgeführt und mit der Upstream-Fehlermail versendet, wenn SMTP eingerichtet ist. Solange `user_webhooks` gesperrt ist, wird kein Fehler-Webhook gesendet. Der Text entsteht so:

- Der fehlgeschlagene Schritt und der HTTP-Status bleiben erhalten.
- Aus einem strukturierten Fehlerkörper bleibt nur das Meldungsfeld des Anbieters (`error`, `error.message`, `message`, `detail`, `msg`, `error_description`, `description` oder `reason`) erhalten, gekürzt auf 300 Zeichen, mit Fehlercode und Typ.
- Ein unstrukturierter Körper, etwa ein Traceback, bleibt in begrenzter Form erhalten, in der Zugangsdaten und Verzeichnisse ersetzt sind.
- Jede Passage mit 20 oder mehr Zeichen und jede Zeichenkette in Anführungszeichen mit 4 oder mehr Zeichen, die auch im Transkript, in der Zusammenfassung, den Notizen, dem Titel, den Teilnehmenden, dem ursprünglichen Dateinamen, dem Initial-Prompt oder den Hotwords der Aufnahme vorkommt, wird durch `[content removed]` ersetzt.
- Das Ergebnis ist auf 500 Zeichen begrenzt; Anfang und Ende bleiben erhalten.

Eine Wiederholung mit weniger als 20 Zeichen ausserhalb von Anführungszeichen wird nicht erkannt. Ebenso wenig wird Text aus dem Audio selbst erkannt, etwa eine transkribierte Passage im Fehler eines Transkriptionsdienstes.

## Entfernte oder gesperrte Upstream-Funktionen

| Upstream-Funktion | Status in der Klinikversion |
|---|---|
| Weitere Transkriptionskonnektoren: gehostet (OpenAI Transcribe, Azure OpenAI, Mistral, AssemblyAI, Alibaba FunASR, Tencent, Mossland) und selbst betrieben (VibeVoice, OpenASR) | Nur Upstream: aus dem Code entfernt. |
| Öffentliche Links zum Teilen | Nur Upstream: aus dem Code entfernt. |
| Internes Teilen, Gruppen, Gruppen-Tags und -Ordner | Nur Upstream: gesperrt (`internal_sharing`). |
| Inkognito-Modus | Nur Upstream: aus dem Code entfernt. |
| Ereigniserkennung und Kalenderexport | Nur Upstream: aus dem Code entfernt. |
| Selbstregistrierung, E-Mail-Bestätigung, Passwortrücksetzung per E-Mail | Nur Upstream: entfernt. Die Kontowiederherstellung erfolgt über den lokalen Einmalcode. |
| Benutzer-Webhooks | Nur Upstream: gesperrt (`user_webhooks`). |
| Persönliche API-Token und `?token=` | Nur Upstream: gesperrt (`personal_api_tokens`, `token_query_parameters`). |
| Web-Push | Nur Upstream: gesperrt (`push_notifications`). |
| OIDC-Einmalanmeldung | Nur Upstream: Die Routen verweigern im Klinikmodus. |
| Chat, Ask Ambrose, Inquire | Nur Upstream: keine Klinikoberfläche; Routen gesperrt, ausser jede Textmodell-Adresse ist lokal (siehe [Ambrose-Einstellungen](ambrose.md)). |
| Sprechererkennung und Sprecherprofile aus Aufnahmen | Nur Upstream: gesperrt; ersetzt durch das Stimmprofil mit Einwilligung. |
| Upload aus externen Diktier-Apps | Nur Upstream: gesperrt (`external_ingest`). |
| Unversionierte Standardexporte (Word-Downloads, Download mit Vorlage) | Nur Upstream: gesperrt (`stock_unversioned_export`). |
| Manuelles Löschen von Aufnahmen und Konten | Nur Upstream: gesperrt (`clinical_user_hard_delete`). |
| Automatischer Export (`ENABLE_AUTO_EXPORT`) und Überwachungsordner (`ENABLE_AUTO_PROCESSING`) | Nur Upstream (ohne Klinikprüfung): Der Code läuft weiterhin, sobald eine Variable gesetzt ist; gesperrt sind nur die Administrationsrouten zum Starten, Stoppen, Auslösen und Konfigurieren des Überwachungsordners (`auto_process`). Lassen Sie beide Variablen ungesetzt. Siehe [Systemeinstellungen](system-settings.md#upstream-funktionen-ohne-klinikprufung). |

## Checkliste für eine Klinikinstallation

1. Setzen Sie `CLINIC_MODE=1` und legen Sie die Vertragsdateien in `clinic-spec/config` (oder in `CLINIC_CONFIG_DIR`) ab. Fehlt eine Vertragsdatei oder ist sie nicht lesbar, scheitern Anfragen mit einem Fehler; die Klinikversion weicht nie auf Upstream-Vorgaben aus.
2. Setzen Sie `TRANSCRIPTION_BASE_URL` (und gegebenenfalls `CLINIC_GPU_ASR_BASE_URL`) auf lokale Dienste. Wird `ASR_BASE_URL` verwendet, setzen Sie sie nur auf einen Host der Institution: Sie wird nicht geprüft. Setzen Sie mit `asr_endpoint` `ASR_DIARIZE=false`, ausser die Institution will Sprecherkennungen, und lassen Sie `ASR_RETURN_SPEAKER_EMBEDDINGS` ungesetzt.
3. Lassen Sie `TEXT_MODEL_BASE_URL` und `TEXT_MODEL_API_KEY` ungesetzt oder zeigen Sie mit dem Textmodell auf einen lokalen Dienst. Lassen Sie `CHAT_MODEL_BASE_URL`, `TRANSLATION_MODEL_BASE_URL` und `EMBEDDING_BASE_URL` ungesetzt oder lokal.
4. Lassen Sie `ENABLE_INQUIRE_MODE` ausgeschaltet und `ENABLE_AUTO_EXPORT`, `ENABLE_AUTO_PROCESSING` und `PRESERVE_TEMP_AUDIO` ungesetzt.
5. Lassen Sie `SMTP_HOST` ungesetzt.
6. Setzen Sie `CLINIC_SPEAKER_SERVICE_URL` nur auf einen lokalen Dienst oder lassen Sie sie ungesetzt.
7. Beenden Sie TLS an einem Reverse-Proxy, setzen Sie `SESSION_COOKIE_SECURE=true`, setzen Sie `TRUSTED_PROXY_HOPS` auf die Anzahl der Proxys, und schützen oder deaktivieren Sie das Zugriffsprotokoll des Proxys. Setzen Sie `session_idle_timeout_minutes` auf die Leerlaufzeit, die die Institution zulässt.
8. Legen Sie `UPLOAD_FOLDER`, `CLINIC_ORIGINALS_ROOT`, die Datenbank und die Sicherungen auf verschlüsselte Datenträger. Sichern Sie `CLINIC_ORIGINALS_ROOT` zusammen mit der Datenbank.
9. Behalten Sie `FILE_STORAGE_BACKEND=local`, oder verwenden Sie für die Arbeitskopien einen S3-Dienst der Institution.
10. Halten Sie `LOG_LEVEL` auf `INFO` oder höher, und setzen Sie eine Grössengrenze und eine Aufbewahrungsdauer für die Containerprotokolle.
11. Lassen Sie `audit_log_enabled` und `audit_log_record_views` eingeschaltet, entscheiden Sie über `audit_log_store_ip`, und exportieren Sie das Auditprotokoll regelmässig aus einer Browsersitzung der Administration (siehe [Auditprotokoll](audit-log.md)).
12. Konfigurieren Sie `CLINIC_AGENT_PROXY_SECRET` und `CLINIC_AGENT_MASTER_SECRET` (je mindestens 32 Byte) nur zusammen mit einem vertrauenswürdigen mTLS-Eingang. Der Kanal des Importagenten ist Vorbereitet, deaktiviert; siehe [Aufnahmesitzungen](recording-sessions.md#kanal-des-importagenten-und-eingang).
13. Verwenden Sie `CLINIC_PROFILE=staging` nicht mit echten Daten: Es nutzt den synthetischen KIS-Mock.
14. Führen Sie eine Liste der Personen mit Zugriff auf den Host, die Datenbank und die Sicherungen. Jede dieser Personen kann alle Diktate lesen.
15. Bitten Sie Benutzende an gemeinsam genutzten Rechnern, sich abzumelden und die anderen Tabs zu schliessen oder je Person ein eigenes Browserprofil zu verwenden; siehe [Was der Browser behält](#was-der-browser-behalt).

## Offene Lücken

- `ASR_BASE_URL` und `CLINIC_SPEAKER_SERVICE_URL` werden nicht auf Lokalität geprüft.
- Automatischer Titel und automatische Zusammenfassung der aktuellen Version sind nicht an Lokalität gebunden, und die Inquire-Indexierung mit `ENABLE_INQUIRE_MODE=true` hat keine Klinikprüfung.
- Es gibt keine genehmigte Aufbewahrungsrichtlinie, und der separat berechtigte Prozess zum Löschen von Auditzeilen ist nicht Teil des Repositorys.
- Die Sitzungsgrenzen des Vertrags (15 Minuten Leerlauf, 8 Stunden absolut) werden nicht durchgesetzt.
- Kein Client wird dem BYOD-Profil zugeordnet, daher greift dessen Exportsperre nicht.
- Das Auditprotokoll ist nicht gegen Personen mit Datenbankzugriff geschützt, und die Anwendung verschlüsselt gespeicherte Daten nicht.
- Nicht durch den Klinikmodus gesperrte Standard-Browsereinträge können den Dateinamen eines grossen Uploads bis zu 24 Stunden enthalten.
- Für Erkennungsqualität, Entra, die KIS-Anbindung, Philips-Geräte und Fusspedale gibt es keine Nachweise aus dem realen Betrieb.
- Web-Parität: Ein lokaler Mailentwurf mit dem Originalaudio (WG-EXP-009) und das Auslesen lokaler Windows-Konten (WG-ADM-009) bleiben offen.

## Upstream-Dokumentation

Upstream-Speakr-Dokumentation (fixiertes Original; beschreibt Upstream-Speakr, nicht diese Version): [Privacy and regulated data](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/privacy.md).
