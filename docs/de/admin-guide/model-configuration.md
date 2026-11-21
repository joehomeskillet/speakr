# Modellkonfiguration
<small>[English](../../admin-guide/model-configuration.md)</small>

Diese Seite beschreibt, wie die Klinikversion ihre Modelle auswählt und erreicht: das lokale Spracherkennungsmodell (ASR) für die Transkription und das lokale Textmodell für Titel und Zusammenfassungen. Alles Folgende gilt mit eingeschaltetem `CLINIC_MODE` (`1`, `true`, `yes` oder `on`). Ist der Klinikmodus aus, gilt das Verhalten von Upstream-Speakr.

In der freigegebenen Klinikversion werden Modelle ausschliesslich über Umgebungsvariablen der Installation konfiguriert. Eine Verwaltungsseite für die Modellwahl je Aufgabe und automatische, an eine Textfassung gebundene Diktatzusammenfassungen gibt es als Funktion, die sich **in Prüfung** befindet. Sie sind am Ende dieser Seite beschrieben und nicht Teil der freigegebenen Version.

## Statusübersicht

| Bereich | Status |
|---------|--------|
| Lokale ASR über einen OpenAI-kompatiblen Dienst wie Speaches (`TRANSCRIPTION_BASE_URL`) | Verfügbar |
| WhisperX oder whisper-asr-webservice (`ASR_BASE_URL`) | Verfügbar; Lokalitätsprüfung: Offene Lücke (siehe unten) |
| Zuerst GPU, Ausweichlösung CPU (`CLINIC_GPU_ASR_BASE_URL`) | Standardmässig aus |
| Lokales Textmodell für Titel und Zusammenfassungen (`TEXT_MODEL_*`) | Standardmässig aus (kein LLM-Client, solange weder `TEXT_MODEL_BASE_URL` noch `TEXT_MODEL_API_KEY` gesetzt ist); Lokalitätsprüfung des Auftrags für automatischen Titel und automatische Zusammenfassung: Offene Lücke |
| Modellerkennung und Speichern im Bereich **Transkriptionsmodelle** | Nur Upstream (Bereich sichtbar; **Erkennen** und **Auswahl speichern** antworten mit 403 `clinic_feature_disabled`) |
| Modell aus einem serverseitigen ASR-Profil je Autorin oder Autor | Vorbereitet, deaktiviert |
| Seite **Modellwahl**, automatische Diktatzusammenfassungen, Bereich **KI-Zusammenfassung** | In Prüfung |
| Cloud-ASR-Anbindungen, gehostete Textmodell-Anbieter, GPT-5-Einstellungen, separate Chat- und Übersetzungsmodelle, Embedding-Modelle | Nur Upstream |

Die Spracherkennung läuft ausschliesslich lokal. Die Klinikversion enthält keine Cloud-ASR-Anbindung und keine Cloud-Ausweichlösung. Die Lokalität wird für `TRANSCRIPTION_BASE_URL` und `CLINIC_GPU_ASR_BASE_URL` durchgesetzt; `ASR_BASE_URL` wird nicht geprüft (Offene Lücke, siehe unten).

## Wer was konfiguriert

- **Betrieb der Installation (keine Anwendungsrolle).** Alle Modell-Endpunkte, Modellnamen und Schlüssel sind Umgebungsvariablen des Serverprozesses. Starten Sie die Anwendung nach einer Änderung neu. Keine Seite erlaubt es, die URL eines Modelldienstes oder einen API-Schlüssel für ein Modell einzugeben.
- **Globale Administration** (**Administrationsrolle**, Upstream-Flag `User.is_admin`). Öffnet **Verwaltung** › **System** › **Systemeinstellungen** mit den Einstellungen, die Modelle betreffen: `disable_auto_summarization`, `transcript_length_limit` und `asr_timeout_seconds` (siehe [Systemeinstellungen](system-settings.md)). Jede Änderung wird als `admin.setting.changed` protokolliert.
- **Aktive globale Administration** (**Administrationsrolle** und ein Klinikkonto mit dem Status **Aktiv**). Nur diese Rolle darf die Seite **Modellwahl** verwenden, die sich in Prüfung befindet.
- **Lokale Identitätsadministration** (**Identitätsadministration**) und **Pool-Leitungen** (**Leitung**). Keine Modellkonfiguration.
- **Fachrechte.** In der Zusammenfassungsfunktion, die sich in Prüfung befindet, braucht das Lesen einer Zusammenfassung **Ansehen** und das Anfordern **Bearbeiten** am Diktat. Die separaten Rechte **Freigeben** und **Originalaudio exportieren** spielen für die Modellkonfiguration keine Rolle. Siehe [Benutzerverwaltung](user-management.md).

## Spracherkennungsmodelle (freigegebene Version)

### Anbindungen

Es sind nur drei Transkriptionsanbindungen registriert:

| Anbindung | Ausgewählt durch | Verwendung |
|-----------|------------------|------------|
| `openai_whisper` | `TRANSCRIPTION_BASE_URL` (Vorgabe, wenn `ASR_BASE_URL` nicht gesetzt ist) | Lokale OpenAI-kompatible ASR wie Speaches |
| `asr_endpoint` | `ASR_BASE_URL` (oder das veraltete `USE_ASR_ENDPOINT=true`) | Selbst betriebenes WhisperX oder whisper-asr-webservice |
| `clinic_stub` | nur `TRANSCRIPTION_CONNECTOR=clinic_stub` | Synthetischer Testersatz mit festen Sätzen; keine Spracherkennung, verweigert den Betrieb mit `CLINIC_PROFILE=production` |

Reihenfolge der Auswahl: `TRANSCRIPTION_CONNECTOR`, dann `ASR_BASE_URL`, dann `openai_whisper`. Bei jedem anderen Namen wird keine Anbindung eingerichtet: Das Startprotokoll zeigt `Failed to initialize transcription connector: ... Unknown connector: <name>. Available: [...]`. Die Einrichtung der Dienste selbst beschreibt [Lokale Spracherkennung einrichten](whisperx-setup.md).

Ein Konfigurationsfehler der Anbindung bricht den Start nicht von selbst ab. Das Startprotokoll zeigt `Failed to initialize transcription connector: <grund>`, danach läuft eine Prüfung der alten Konfiguration. Die Anwendung beendet sich nur, wenn auch diese Prüfung scheitert: mit `ERROR: No transcription service configured!`, wenn `USE_ASR_ENDPOINT` nicht `true` ist und `TRANSCRIPTION_BASE_URL` oder `TRANSCRIPTION_API_KEY` leer ist, oder mit `ERROR: ASR enabled but ASR_BASE_URL not configured!`, wenn `USE_ASR_ENDPOINT=true` gesetzt und `ASR_BASE_URL` leer ist. Sonst startet die Anwendung, und jeder Transkriptionsauftrag scheitert mit demselben Konfigurationsfehler, bis er behoben ist.

### Lokalitätsregel für die ASR

Im Klinikmodus lehnt die Anbindung `openai_whisper` eine leere oder nicht lokale `TRANSCRIPTION_BASE_URL` mit `CLINIC_MODE is active: transcription base URL must point to a local or private service ...` ab. Für die ASR gilt als lokal: `localhost`, eine Loopback-, private (RFC 1918, IPv6-ULA) oder Link-Local-Adresse, jeder einteilige Hostname (zum Beispiel ein Compose-Dienstname wie `speaches`) sowie Namen mit der Endung `.local`, `.internal`, `.lan` oder `.localhost`. Das GPU-Ziel wird gleich geprüft.

!!! warning "Offene Lücke: `ASR_BASE_URL` wird nicht geprüft"
    Die Anbindung `asr_endpoint` prüft nur, ob `ASR_BASE_URL` gesetzt ist. Eine öffentliche Adresse wird im Klinikmodus nicht abgewiesen; höchstens erscheint beim Start eine Warnung für unverschlüsseltes `http` zu einem anderen Host. Lassen Sie `ASR_BASE_URL` nur auf einen lokalen Dienst zeigen.

### ASR-Variablen

| Variable | Vorgabe | Bedeutung |
|----------|---------|-----------|
| `TRANSCRIPTION_CONNECTOR` | automatische Erkennung | `openai_whisper`, `asr_endpoint` oder `clinic_stub` |
| `TRANSCRIPTION_BASE_URL` | nicht gesetzt | Lokale OpenAI-kompatible ASR, zum Beispiel `http://speaches:8000/v1` |
| `TRANSCRIPTION_API_KEY` | nicht gesetzt | Darf für `openai_whisper` nicht leer sein; ein lokaler Dienst akzeptiert einen Platzhalter wie `not-needed-for-local` |
| `TRANSCRIPTION_MODEL` | `whisper-1` | Modell von `openai_whisper` (alter Name `WHISPER_MODEL`); die Beispielkonfiguration verwendet `Systran/faster-distil-whisper-large-v3` |
| `TRANSCRIPTION_TIMEOUT` | `3600` s im Klinikmodus | Lese-Zeitlimit von `openai_whisper`; muss eine positive Zahl sein, sonst wird die Anbindung nicht eingerichtet, und der Fehler nennt die Variable (siehe Anbindungen oben) |
| `TRANSCRIPTION_MAX_RETRIES` | `0` im Klinikmodus | SDK-Wiederholungen von `openai_whisper`; ganze Zahl `>= 0`, sonst gleiche Fehlerbehandlung wie bei `TRANSCRIPTION_TIMEOUT` |
| `ASR_BASE_URL` | nicht gesetzt | WhisperX oder whisper-asr-webservice; ohne Lokalitätsprüfung (Offene Lücke) |
| `ASR_TIMEOUT` | `1800` s (über `asr_timeout_seconds`) | Zeitlimit von `asr_endpoint`; hat Vorrang vor der Systemeinstellung `asr_timeout_seconds`, die nur für diese Anbindung gilt |
| `TRANSCRIPTION_MODELS_AVAILABLE` / `TRANSCRIPTION_MODEL_LABELS` | nicht gesetzt | Kommagetrennte Modellkennungen und optionale Anzeigenamen. Erscheinen als Modellliste, solange in der Datenbank keine Liste gespeichert ist; bei der Prüfung des Modells eines Auftrags (Schritt 2 unten) gelten diese Kennungen zusätzlich zu einer gespeicherten Liste |

### Zuerst GPU, dann CPU (standardmässig aus)

Ist `CLINIC_GPU_ASR_BASE_URL` gesetzt, geht jeder Auftrag der Anbindung `openai_whisper` oder `asr_endpoint` (nie `clinic_stub`) zuerst an das GPU-Ziel. Die konfigurierte Anbindung bleibt das CPU-Ziel.

| Variable | Vorgabe | Bedeutung |
|----------|---------|-----------|
| `CLINIC_GPU_ASR_BASE_URL` | nicht gesetzt (Ausweichlösung aus) | OpenAI-kompatible GPU-ASR; muss lokal sein |
| `CLINIC_GPU_ASR_TIMEOUT` | `300` s | Zeitlimit der GPU-Anfrage; ein ungültiger oder nicht positiver Wert ergibt 300 |
| `CLINIC_GPU_ASR_API_KEY` | Schlüssel des CPU-Ziels, sonst `local` | Schlüssel für das GPU-Ziel |
| `CLINIC_GPU_ASR_MODEL` | nicht gesetzt | Fest vorgegebener GPU-Modellname |

Vor jeder Transkriptionsanfrage ruft die Anwendung `/healthz` an der Wurzel des GPU-Dienstes auf (Zeitlimit 5 s). Läuft dieser Aufruf in eine Zeitüberschreitung, ist der Dienst nicht erreichbar oder antwortet er nicht mit 2xx, oder scheitert die GPU-Anfrage mit einer Zeitüberschreitung, einem Verbindungsfehler oder HTTP 500 und höher, läuft derselbe Auftrag einmal auf dem CPU-Ziel. Das GPU-Ziel wird nicht wiederholt, und ein Fehler auf der CPU ist der Fehler des Auftrags. Jeder andere GPU-Fehler, zum Beispiel HTTP 404 `model_not_found`, lässt den Auftrag ohne CPU-Ausweichlösung scheitern. Protokollzeilen: `ASR GPU target <url> configured (timeout <n>s); CPU target stays the fallback` beim Start, `ASR: sending job to GPU target <url>` je Auftrag und `ASR fallback: GPU target <url> not used (<reason>); transcribing on CPU target <url>` beim Ausweichen.

Modellname, der an das GPU-Ziel geht:

1. `CLINIC_GPU_ASR_MODEL`, wenn gesetzt.
2. Sonst beim mitgelieferten OpenVINO-GPU-Dienst das Modell, das dessen `/healthz` meldet.
3. Sonst derselbe Modellname, den das CPU-Ziel erhalten würde.

Der mitgelieferte GPU-Dienst akzeptiert nur seinen `ASR_MODEL_NAME` (Vorgabe `whisper-large-v3-turbo-int8-ov`) und `whisper-1`. Jeder andere Name erhält HTTP 404 `model_not_found`, und der Auftrag scheitert. Einzelheiten zum Dienst: [Lokale Spracherkennung einrichten](whisperx-setup.md).

### Welches ASR-Modell ein Auftrag verwendet

Für jeden Batch-Auftrag wird das Modell in dieser Reihenfolge bestimmt:

1. Ein mit der Anfrage übergebenes Modell (Hochladen, erneute Verarbeitung), sonst das Standard-Transkriptionsmodell des ersten Tags (in Tag-Reihenfolge), das eines festlegt, sonst die Vorgabe des Ordners.
2. Gibt es eine Positivliste (die im Bereich **Transkriptionsmodelle** gespeicherte Liste, `TRANSCRIPTION_MODELS_AVAILABLE` oder beide), wird ein Wert aus Schritt 1, der auf keiner dieser Listen steht, mit einer Warnung im Protokoll verworfen.
3. Ergab Schritt 1 keinen Wert, gilt die gespeicherte Administrationsvorgabe `transcription_default_model`, sofern gesetzt.
4. Sonst das eigene Modell der Anbindung (`TRANSCRIPTION_MODEL` bei `openai_whisper`). `asr_endpoint` hat kein konfiguriertes Modell: Diese Anbindung sendet den Abfrageparameter `model` nur, wenn die Schritte 1 bis 3 ein Modell ergeben; sonst verwendet der Dienst sein geladenes Modell.

Die Diktatansichten der Klinik bieten keine Modellauswahl. Ein in Schritt 2 verworfener Wert fällt auf das eigene Modell der Anbindung zurück, nicht auf die Administrationsvorgabe.

### Der Bereich Transkriptionsmodelle

**Systemeinstellungen** zeigt weiterhin den Upstream-Bereich **Transkriptionsmodelle**. Die globale Administration kann die gespeicherte Liste und die Vorgabe lesen. Im Klinikmodus rufen die Schaltflächen **Erkennen** und **Auswahl speichern** Routen unter der Funktionssperre `cloud_provider_selection` auf, die der Klinikvertrag nicht freigibt; beide antworten deshalb mit 403 `clinic_feature_disabled`. Eine früher gespeicherte Liste oder Vorgabe bleibt wirksam und lässt sich in der Oberfläche nicht ändern. Um die Positivliste zu erweitern, ergänzen Sie `TRANSCRIPTION_MODELS_AVAILABLE`; Einträge einer gespeicherten Liste lassen sich im Klinikmodus nicht entfernen.

### ASR-Profile je Autorin oder Autor (vorbereitet, deaktiviert)

Das Serverprofil einer Autorin oder eines Autors kann eine Sprache und ein Modell nennen. Es gilt für einen Auftrag nur, wenn das Fähigkeitsregister einen Laufzeitnachweis für genau dieses Ziel enthält, und die freigegebene Version füllt dieses Register nie. Jeder Auftrag läuft deshalb mit den Klinikvorgaben (Sprache `de`, sofern weder Anfrage, Tag, Ordner noch Besitzer eine Sprache setzen) und hält den Grund in seinen Auftragsparametern fest. Hotwords und Profileinstellungen beeinflussen den Decoder; sie sind kein akustisches Training. Im Klinikmodus erreichen Initial-Prompt und Hotwords die Spracherkennung nur, wenn `CLINIC_ASR_PROMPTS_ENABLED` eingeschaltet ist (`1`, `true`, `yes` oder `on`; standardmässig aus); dann teilen sie sich eine Obergrenze von 55 Wörtern. Siehe [Standard-Prompts](prompts.md) und [Lokale Spracherkennung einrichten](whisperx-setup.md).

## Textmodell für Titel und Zusammenfassungen (freigegebene Version)

### Variablen

| Variable | Vorgabe | Bedeutung |
|----------|---------|-----------|
| `TEXT_MODEL_BASE_URL` | Code-Vorgabe `https://openrouter.ai/api/v1`, verwendet nur bei ausdrücklichem Setzen oder vorhandenem Schlüssel | OpenAI-kompatibler Endpunkt des Textmodells |
| `TEXT_MODEL_API_KEY` | nicht gesetzt | Für einen lokalen Server optional; ohne Schlüssel wird der Platzhalter `not-needed` gesendet |
| `TEXT_MODEL_NAME` | `openai/gpt-3.5-turbo` | Modellkennung; setzen Sie eine Kennung, die Ihr lokaler Server bereitstellt |
| `CLINIC_LOCAL_MODEL_HOSTS` | `speaches,host.docker.internal,localhost` | Kommagetrennte Hostnamen, die als lokal gelten; ein gesetzter Wert ersetzt die Vorgabeliste |
| `CLINIC_LLM_TIMEOUT_SECONDS` | `120` | Zeitlimit je Aufruf für Titel und Zusammenfassungen im Klinikmodus; ersetzt `LLM_REQUEST_TIMEOUT` (600) |
| `CLINIC_LLM_MAX_RETRIES` | `2` | Wiederholungen je Aufruf im Klinikmodus; ersetzt `LLM_MAX_RETRIES`. Ungültige Werte beider Klinikvariablen ergeben ihre Vorgaben |
| `LLM_CONNECT_TIMEOUT`, `LLM_WRITE_TIMEOUT` | je `30` | Zeitlimits in Sekunden für Verbindungsaufbau und Senden des Textmodell-Clients (Lesen: `LLM_REQUEST_TIMEOUT`). Sie gelten für Chat- und Übersetzungsaufrufe; im Klinikmodus ersetzen Titel- und Zusammenfassungsaufrufe sie durch das einheitliche Zeitlimit je Aufruf `CLINIC_LLM_TIMEOUT_SECONDS` |
| `SUMMARY_MAX_TOKENS` | `3000` | Höchstzahl der Ausgabetokens einer Zusammenfassung |
| `TITLE_MAX_TOKENS` | `5000` | Höchstzahl der Ausgabetokens eines Titelaufrufs |
| `TEXT_MODEL_EXTRA_BODY` | nicht gesetzt | JSON-Objekt, das jeder Anfrage an das Textmodell hinzugefügt wird (siehe unten) |
| `PREFIX_CACHE_OPTIMIZED_PROMPTS` | `false` | Baut Titel- und Zusammenfassungsprompts mit gemeinsamem Präfix auf (siehe unten) |

Ein Sprachmodell-Client entsteht nur, wenn `TEXT_MODEL_BASE_URL` ausdrücklich gesetzt ist oder `TEXT_MODEL_API_KEY` vorhanden ist. Ohne Client gibt es weder Titel noch Zusammenfassungen, und es wird nichts gesendet.

### Die Regel für lokale Modelle

Im Klinikmodus antworten die Upstream-Routen für Zusammenfassung, Titel und Übersetzung (`generate_summary_endpoint`, `reprocess_summary`, `regenerate_title`, `translate_transcription`, `api_regenerate_title`, `start_summarization`) mit 403 `clinic_feature_disabled`, solange das Textmodell nicht lokal ist. Dieselbe Regel sperrt Chat und Inquire. Die Regel gilt nur als erfüllt, wenn:

- `TEXT_MODEL_BASE_URL` gesetzt und lokal ist und
- jede gesetzte Variable unter `CHAT_MODEL_BASE_URL`, `TRANSLATION_MODEL_BASE_URL` und `EMBEDDING_BASE_URL` ebenfalls lokal ist. Eine einzige öffentliche URL sperrt alle diese Funktionen.

Für Textmodelle gilt als lokal: eine Loopback- oder private Adresse (`127.0.0.0/8`, `::1`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `fc00::/7`) oder ein Host auf `CLINIC_LOCAL_MODEL_HOSTS`. Das ist strenger als die ASR-Regel: Ein einteiliger Name, der nicht auf der Liste steht, ist **nicht** lokal, und eine URL mit Benutzerangabe (`@`), Backslash oder Leerraum wird abgewiesen. Das Flag `features.generative_summary` in der Klinik-Vorgabedatei entscheidet darüber nicht; massgebend ist nur die Lokalität der URLs.

!!! warning "Mitgelieferte Beispiele sind nicht klinikfertig"
    Die Beispieldateien `config/env.asr.example`, `config/env.transcription.example` ([Quelle im Klinik-Fork](https://github.com/joehomeskillet/speakr/blob/main/config/env.transcription.example)), `config/env.whisper.example` und `config/env.whisperx.example` setzen alle `TEXT_MODEL_BASE_URL=https://openrouter.ai/api/v1`, einen gehosteten Dienst. Ersetzen Sie diesen Wert durch einen lokalen Endpunkt, bevor Sie den Klinikmodus einschalten.

Beispiel mit einem lokalen Textmodell-Dienst namens `llm` (Platzhalter in spitzen Klammern):

```bash
CLINIC_MODE=1

# Spracherkennung
TRANSCRIPTION_BASE_URL=http://speaches:8000/v1
TRANSCRIPTION_API_KEY=not-needed-for-local
TRANSCRIPTION_MODEL=Systran/faster-distil-whisper-large-v3

# Textmodell: der Dienstname muss auf der Liste lokaler Hosts stehen
CLINIC_LOCAL_MODEL_HOSTS=speaches,host.docker.internal,localhost,llm
TEXT_MODEL_BASE_URL=http://llm:<port>/v1
TEXT_MODEL_NAME=<modellkennung>
```

Der Textmodell-Client der freigegebenen Version und die Anbindung `openai_whisper` schalten die HTTP-Proxy-Umgebungsvariablen des Prozesses (zum Beispiel `HTTPS_PROXY`) nicht ab. Stellen Sie sicher, dass solche Einstellungen den Verkehr zu lokalen Modellen nicht aus dem Netz leiten.

### Anfrageparameter und Präfix-Caching

`TEXT_MODEL_EXTRA_BODY` enthält ein JSON-Objekt, das zuletzt in jede Anfrage an das Textmodell eingefügt wird und deshalb Felder wie `max_tokens` überschreibt. Verwenden Sie es für serverspezifische Optionen eines lokalen Servers, zum Beispiel um das «Thinking» eines Qwen3-Modells auf vLLM, SGLang oder llama.cpp abzuschalten:

```bash
TEXT_MODEL_EXTRA_BODY={"chat_template_kwargs": {"enable_thinking": false}}
```

Ein Wert, der kein JSON-Objekt ist, wird beim Start protokolliert, und Anfragen an das Textmodell scheitern, bis er korrigiert ist. Weist der Server eine Anfrage zurück, nennt das Protokoll die hinzugefügten Felder.

`PREFIX_CACHE_OPTIMIZED_PROMPTS=true` gibt den Titel- und Zusammenfassungsprompts eine byte-identische Systemnachricht und einen Benutzerblock, der mit dem Transkript beginnt. Ein lokaler Inferenzserver mit automatischem Präfix-Caching (zum Beispiel vLLM mit `--enable-prefix-caching`) kann den Transkript-Präfix dann zwischen beiden Aufrufen wiederverwenden. Ist das Flag eingeschaltet, verwenden beide Prompts eine gemeinsame Systemnachricht und einen umformulierten Aufgabenblock nach dem Transkript; die Ausgabe kann sich deshalb ändern. Standardmässig aus; prüfen Sie die Qualität der Zusammenfassungen, bevor Sie es einschalten. Prompttexte und ihre Rangfolge beschreibt [Standard-Prompts](prompts.md).

### Automatische Titel und Zusammenfassungen in der freigegebenen Version

Im Klinikmodus ruft ein Transkriptionsauftrag das Sprachmodell nie selbst auf. Er reiht einen Folgeauftrag `summarize` in die Zusammenfassungswarteschlange ein. Dieser Auftrag schreibt den Titel und, wenn die automatische Zusammenfassung gilt, die Upstream-Zusammenfassung der Aufnahme. Die automatische Zusammenfassung gilt, wenn die Systemeinstellung `disable_auto_summarization` nicht `true` ist, die persönliche Einstellung `auto_summarization` nicht ausgeschaltet ist und ein Sprachmodell-Client existiert. Im Klinikmodus liegt der Schalter für diese Einstellung (**Auto-Zusammenfassen**) im Kontobereich **Prompt-Optionen**, der nicht angezeigt wird. Die Einstellung behält deshalb ihre Vorgabe (ein), sofern sie nicht über die API geändert wird.

!!! warning "Offene Lücke: automatische Titel und Zusammenfassungen ohne Lokalitätsprüfung"
    Der automatische Folgeauftrag hat keine eigene Lokalitätsprüfung; die obige Regel für lokale Modelle schützt nur die Upstream-Routen für Zusammenfassung und Titel. Ob Titel und Zusammenfassungen von Transkripten lokal bleiben, hängt deshalb davon ab, dass eine lokale `TEXT_MODEL_BASE_URL` konfiguriert ist. Konfigurieren Sie im Klinikmodus kein gehostetes Textmodell. Setzen Sie `TEXT_MODEL_API_KEY` nie ohne lokale `TEXT_MODEL_BASE_URL`: Ein Schlüssel allein erzeugt einen Client mit der Code-Vorgabe `https://openrouter.ai/api/v1`. Lassen Sie beide leer, oder richten Sie sie auf einen lokalen Dienst.

Titel- und Zusammenfassungsaufrufe zählen gegen das **Tokenlimit pro Monat** des Kontos, siehe [Benutzerverwaltung](user-management.md). Die Nutzung der Sprachmodelle je Modell erscheint in der [Systemstatistik](statistics.md).

## Seite «Modellwahl» (in Prüfung)

**Status: In Prüfung.** Diese Seite ist nicht Teil der aktuell freigegebenen Klinikversion. Bezeichnungen und Verfügbarkeit können sich ändern. Die Beschreibung folgt dem Code der Funktion in ihrem aktuellen Stand.

### Ort und Zugriff

- **Verwaltung** › Gruppe **System** › **Modellwahl**, zwischen **Systemeinstellungen** und **System-Auditprotokoll**; Adresse `/admin#/system/modelle`. Wird nur bei eingeschaltetem Klinikmodus angezeigt.
- Brotkrumen `Speakr › Verwaltung › Modellwahl`, Überschrift **Modellwahl**, Unterzeile «Lokale Modelle für neue Verarbeitungsaufträge festlegen.»
- Seite und API (`GET`/`PUT /api/clinic/v1/admin/task-models`) stehen nur einer aktiven globalen Administration offen: `User.is_admin` und ein Klinikkonto mit dem Status **Aktiv**. Eine Identitätsadministration ohne **Administrationsrolle**, eine Pool-Leitung oder eine deaktivierte Administration erhält 403; eine Pool-Leitung sieht den Link nicht. Ohne Anmeldung antwortet die API mit 401, ein `PUT` ohne gültiges CSRF-Token mit 403 und bei ausgeschaltetem Klinikmodus mit 404.

### Felder

| Bereich | Feld | Aufgabe |
|---------|------|---------|
| **Transkription** – «Überträgt die Aufnahme in Text.» | **GPU-Modell** | GPU-Ziel der ASR-Ausweichlösung |
| | **CPU-Modell** | Konfigurierte (CPU-)ASR-Anbindung |
| **Zusammenfassung** – «Fasst das gespeicherte Transkript zusammen.» | **Textmodell** | Nur Diktatzusammenfassungen der Klinik |

Die Seite speichert ausschliesslich Modellkennungen. Dienst-URLs und Schlüssel bleiben in der Konfiguration der Installation. Eine Kennung hat höchstens 200 druckbare Zeichen, besteht aus Buchstaben, Ziffern und `_ . + : -` in Abschnitten, die durch `/` getrennt sind, und enthält kein `:/`. Werte wie `https://…`, `file:/…`, `../private` oder ein Zeilenumbruch werden mit 400 `invalid_model` abgewiesen.

### Geprüfte lokale Kataloge

Für jede Aufgabe fragt der Server den lokalen Dienst, welche Modelle er bereitstellt. Jede dieser Anfragen ignoriert Proxy-Umgebungsvariablen, folgt keiner Weiterleitung und hat ein kurzes Zeitlimit (2 s für die GPU-Zustandsprüfung, 1,5 s für Kataloge). Ein Katalog wird nur abgefragt, wenn die Dienst-URL lokal ist.

- **GPU-Modell:** `/healthz` an der Wurzel von `CLINIC_GPU_ASR_BASE_URL`. Es zählt nur der mitgelieferte OpenVINO-Dienst (`model_loaded` wahr und eine `openvino_genai`-Version). Zur Auswahl stehen das gemeldete Modell und `whisper-1`.
- **CPU-Modell:** `GET <Basis-URL>/models` der konfigurierten Anbindung (`/v1/models` bei `asr_endpoint`).
- **Textmodell:** `GET <Basis-URL des Textmodells>/models`.

Eine Antwort mit `has_more`, ohne `data`-Liste oder mit einer ungültigen Kennung gilt als kein Katalog.

Mit geprüftem Katalog ist das Feld eine Auswahlliste, deren erste Option **Vorgabe übernehmen** lautet. Eine gespeicherte Kennung, die nicht mehr im Katalog steht, erscheint als deaktivierte Option. Das Speichern einer Kennung ausserhalb des Katalogs wird mit 400 abgewiesen, ausser der Wert ist unverändert. Ohne geprüften Katalog ist das Feld ein Freitext. Für **CPU-Modell** schlägt es die Kennungen der im Bereich **Modellkatalog für die Aufnahme** gespeicherten Liste (siehe unten) und von `TRANSCRIPTION_MODELS_AVAILABLE` vor. Dann wird jede gültige Kennung angenommen; prüfen Sie deshalb, ob der Dienst sie tatsächlich bereitstellt.

### Inaktiver Dienst oder fehlende Auswahl

Unter jedem Feld zeigt die Seite eine von drei Zeilen:

| Zeile | Bedeutung |
|-------|-----------|
| **Wirksam: <Kennung>** | Der Dienst ist aktiv, und neue Anfragen senden diese Kennung. Bei einem geerbten CPU-Wert hat ein beim Hochladen gewähltes oder an einem Tag oder Ordner festgelegtes Modell weiterhin Vorrang |
| **Wirksam: Keine Modellkennung gemeldet** | Der Dienst ist aktiv, aber die Seite kann keine Kennung bestätigen: Es ist nichts gespeichert oder geerbt, oder die gespeicherte oder geerbte Kennung steht nicht im geprüften Katalog. Im zweiten Fall senden neue Anfragen diese Kennung trotzdem; der lokale Dienst kann sie ablehnen |
| **Derzeit inaktiv: Kein Modell aktiv** | Der lokale Dienst der Aufgabe ist nicht aktiv: Die URL der CPU-Anbindung ist nicht lokal, `CLINIC_GPU_ASR_BASE_URL` ist nicht gesetzt oder nicht lokal, oder es ist kein lokales Textmodell bereit |

Jede Zeile nennt zudem die Herkunft des Werts: **Hier festgelegt**, **Installationsvorgabe**, **Vom lokalen Dienst**, **Bestehender Standard** oder **Keine Vorgabe**. Fusszeile: «Ohne eigene Auswahl gilt die Vorgabe der Installation oder des lokalen Dienstes.»

Geerbte Werte, wenn nichts gespeichert ist:

- **CPU-Modell:** die gespeicherte Vorgabe `transcription_default_model` (**Bestehender Standard**), sonst das konfigurierte Modell der Anbindung (**Installationsvorgabe**). `asr_endpoint` hat kein konfiguriertes Modell; ohne gespeicherte Vorgabe zeigt die Zeile **Keine Vorgabe**.
- **GPU-Modell** (nur mit gesetzter `CLINIC_GPU_ASR_BASE_URL`): `CLINIC_GPU_ASR_MODEL` (**Installationsvorgabe**), sonst das vom GPU-Dienst gemeldete Modell (**Vom lokalen Dienst**), sonst das konfigurierte Modell der CPU-Anbindung (**Installationsvorgabe**).
- **Textmodell:** `TEXT_MODEL_NAME` (**Installationsvorgabe**).

### Wirkung einer gespeicherten Auswahl

- Der Server liest die Auswahl bei jeder Modellanfrage aus der Datenbank; ein gespeicherter Wert gilt also sofort für neue Anfragen. Abgeschlossene Aufträge werden nicht neu verarbeitet. Ein laufender Auftrag mit Abschnitten liest die Auswahl bei jeder Abschnittsanfrage und kann während des Auftrags das Modell wechseln; seine Textrevision hält dann `<anbindung>:mixed` fest. Ein leerer Wert (**Vorgabe übernehmen** oder ein geleertes Feld) kehrt zur geerbten Vorgabe zurück.
- Ein gespeichertes **CPU-Modell** ersetzt das Modell der Anfrage, auch eine Wahl beim Hochladen oder bei der erneuten Verarbeitung sowie Tag- und Ordnervorgaben, aber nur solange die URL des CPU-Ziels lokal ist.
- Ein gespeichertes **GPU-Modell** hat Vorrang vor `CLINIC_GPU_ASR_MODEL` und vor dem vom Dienst gemeldeten Modell, aber nur solange die GPU-URL lokal ist. Es legt nur den Modellnamen fest, der mit der Anfrage gesendet wird; es lädt oder wechselt kein Modell auf dem GPU-Dienst.
- **Textmodell** gilt nur für Diktatzusammenfassungen der Klinik. Titel und alle anderen Sprachmodell-Aufrufe verwenden weiterhin `TEXT_MODEL_NAME`.

### Speichern, Konflikte und Fehler

**Modellwahl speichern** bleibt deaktiviert, bis sich ein Wert ändert. Bei Erfolg erscheint «Modellwahl gespeichert.» Das Speichern verwendet eine Revision: Hat zwischenzeitlich jemand anderes gespeichert, antwortet der Server mit 409 `stale_revision`, die Seite zeigt «Die Modellwahl wurde zwischenzeitlich geändert. Bitte den aktuellen Stand neu laden.» mit der Schaltfläche **Neu laden**, und der Entwurf bleibt erhalten. Weitere Meldungen: «Für die Modellwahl fehlt die Administratorberechtigung.» (403), «Mindestens eine Modellkennung ist für den lokalen Dienst ungültig.» (400), «Die Modellwahl konnte nicht geladen werden.» und «Die Modellwahl konnte nicht gespeichert werden.»

### Speicherung, Auditprotokoll und Serverprotokoll

- Die Auswahl wird als Systemeinstellung `clinic_task_models` gespeichert (ein JSON-Objekt mit Revision und Modellen). **Systemeinstellungen** listet diesen Schlüssel nicht auf, und ein allgemeines `POST /admin/settings` dafür antwortet mit 400 `use_task_models_api`.
- Jedes Speichern schreibt `admin.setting.changed` mit dem Schlüssel `clinic_task_models`. Alte und neue Modellkennungen werden nicht erfasst. Siehe [Auditprotokoll](audit-log.md).
- Die Protokollzeile des Sprachmodell-Aufrufs und der Eintrag zum Tokenverbrauch nennen das tatsächlich verwendete Modell. Eine ASR-Textrevision hält das Modell der Antwort als `<anbindung>:<modell>` fest; gemischte Modelle über Abschnitte hinweg als `<anbindung>:mixed` und eine Antwort von `asr_endpoint` ohne übermittelten Modellnamen (auch bei einem fortgesetzten Auftrag) als `asr_endpoint:unspecified`.
- In **Systemeinstellungen** heisst der Bereich **Transkriptionsmodelle** dann **Modellkatalog für die Aufnahme**, mit dem Hinweis «Sichtbare Modelle und Anzeigenamen für die Aufnahme. Der Katalogstandard gilt für die CPU-Transkription, solange in der Modellwahl keine eigene CPU-Kennung festgelegt ist.»

## Automatische Diktatzusammenfassungen (in Prüfung)

**Status: In Prüfung.** Nicht Teil der aktuell freigegebenen Klinikversion. Bezeichnungen und Verfügbarkeit können sich ändern. Die Zusammenfassung ist ein separater, nur lesbarer Text: Sie bearbeitet oder ersetzt den Diktattext nie und wird nicht in ihn eingefügt. Die bisherige Prüfung verwendet ausschliesslich synthetische Daten; über die Qualität der Zusammenfassungen oder ihre klinische Eignung wird keine Aussage gemacht.

### Wo sie erscheint

Der Bereich **KI-Zusammenfassung** erscheint im **Schreibplatz** eingeklappt und wird erst beim Öffnen geladen; in den **Diktatdetails** ist er aufgeklappt, sobald die Details geladen sind. Der Text wird als eingeschränktes Markdown dargestellt (Überschriften, Listen, Absätze, fett, kursiv); Links und Bilder erscheinen als einfacher Text, rohes HTML als wörtlicher Text.

Statustexte: «Für dieses Diktat liegt noch keine KI-Zusammenfassung vor.», «Die Zusammenfassung wird nach der Transkription automatisch erstellt.», «Die KI-Zusammenfassung wartet auf die Verarbeitung.», «Die KI-Zusammenfassung wird erstellt …», «Die Erstellung ist fehlgeschlagen.» und «Der Bezug zur aktuellen Textfassung ist nicht bestätigt.» Solange der Bereich geöffnet ist und ein Auftrag aussteht, aktualisiert er sich alle 2,5 s. Die Schaltflächen **Zusammenfassung erstellen** und **Zusammenfassung neu erstellen** erscheinen nur für Personen mit Bearbeitungsrecht, wenn ein lokales Modell bereit ist und ein aktueller, gespeicherter Diktattext mit mindestens 10 verwendbaren Zeichen vorliegt, und nicht, solange ein Auftrag aussteht oder eine Fehlermeldung angezeigt wird; bei einer Fehlermeldung bietet der Bereich stattdessen **Stand erneut laden** an. Bei ungespeicherten Änderungen zeigt der Bereich «Die Zusammenfassung verwendet die zuletzt gespeicherte Textfassung.»

### An eine Textrevision gebunden

Jedes Diktat hat höchstens eine gespeicherte Zusammenfassung, gebunden an genau die Textrevision, die sie beschreibt. Ihr Status ist `ready` nur, solange diese Revision aktuell ist. Nach einer späteren Korrektur wird er `stale`; eine automatische Neuerstellung gibt es nicht. Ältere Upstream-Zusammenfassungen der Aufnahme erscheinen nur als `stale`, Fehlermarkierungen als `failed` ohne Text, und eine Zusammenfassung aus einer mehrdeutigen historischen Quellaufnahme wird nicht angezeigt.

### Berechtigungen und Protokollierung

| Aktion | Voraussetzung | Sonst |
|--------|---------------|-------|
| Lesen (`GET /api/clinic/v1/dictations/<id>/summary`) | **Ansehen** am Diktat, aktives Konto | 404 |
| Anfordern (`POST`, gleiche Adresse) | **Bearbeiten**, CSRF-Token | 403 |
| | Ein lokales Modell ist bereit | 503 `SUMMARY_UNAVAILABLE`, Oberfläche «Der lokale KI-Dienst ist derzeit nicht verfügbar.» |
| | Ein aktueller Text mit mindestens 10 Zeichen | 409, Oberfläche «Zum Erstellen wird ein aktueller Diktattext benötigt.» |

Eine aktive globale Administration kann Zusammenfassungen über das Recht **Ansehen** lesen, das die globale Administration erhält, bekommt aber ohne Bearbeitungsrecht beim Anfordern 403. Jeder Lesezugriff, auch jede automatische Aktualisierung, wird als `recording.content.read` mit `part=summary` protokolliert; jede manuelle Anforderung als `recording.reprocessed` mit `kind=summary`.

### Verarbeitung

- Die Zusammenfassung läuft als Auftragstyp `clinic_dictation_summary` in der bestehenden Zusammenfassungswarteschlange (`ProcessingJob`/`JobQueue`) im Hintergrund; die Aufnahme wird nicht auf «in Verarbeitung» gesetzt. Eine wiederholte Anforderung, solange ein Auftrag wartet, erzeugt keinen zweiten Auftrag.
- Eingabe ist die aktuelle gespeicherte Textrevision, die korrigiert sein kann, nicht der rohe ASR-Text. Sie wird auf `transcript_length_limit` gekürzt (Vorgabe 30000 Zeichen, `-1` = ohne Grenze). Der Prompt folgt der bestehenden Rangfolge, die [Standard-Prompts](prompts.md) beschreibt. Die Ausgabe ist durch `SUMMARY_MAX_TOKENS` (3000) begrenzt, und die Antwort muss mit `finish_reason` `stop` enden.
- Das Modell ist das gespeicherte **Textmodell**, sonst `TEXT_MODEL_NAME`. Die Anfrage ignoriert Proxy-Umgebungsvariablen und folgt keiner Weiterleitung. Fehler des Anbieters werden durch eine allgemeine Meldung ersetzt, damit der Fehlertext das Transkript nicht zitieren kann.
- Vor der Verwendung des Textes und erneut nach dem Modellaufruf prüft der Verarbeitungsprozess Ansehen- und Bearbeitungsrecht, das Konto, ein bereites lokales Modell, bei automatischen Aufträgen die Einstellung zur automatischen Zusammenfassung sowie eine unveränderte Textrevision. Hat sich etwas geändert, scheitert der Auftrag, ohne ein Ergebnis zu speichern.

### Automatische Zusammenfassung nach dem ersten Transkript

Die erste erfolgreiche ASR-Revision eines neuen Klinikdiktats (Auftragstyp `transcribe`) reiht in derselben Transaktion einen Zusammenfassungsauftrag ein. Der Auftrag läuft im Namen der Autorin oder des Autors. Alle folgenden Bedingungen müssen erfüllt sein:

- Die Systemeinstellung `disable_auto_summarization` ist nicht `true`.
- Die persönliche Einstellung `auto_summarization` der Autorin oder des Autors ist nicht ausgeschaltet.
- Ein lokales Modell ist bereit.
- Die Autorin oder der Autor hat Ansehen- und Bearbeitungsrecht am Diktat.
- Der erste ASR-Text ist verwendbar und mindestens 10 Zeichen lang.

Es gibt keine Nachverarbeitung bestehender Daten. Bestehende Diktate, eine erneute Erkennung, spätere ASR-Lieferungen und menschliche Korrekturen lösen keine automatische Zusammenfassung aus; eine Person mit Bearbeitungsrecht kann sie manuell anfordern. Wird die automatische Einstellung der Autorin oder des Autors nach dem Einreihen ausgeschaltet, scheitert der Auftrag ohne Modellaufruf.

Mit dieser Funktion schreibt der Upstream-Folgeauftrag (`summarize`) für Aufnahmen, die zu einem Klinikdiktat gehören, nur noch den Titel und keine Upstream-Zusammenfassung; die Zusammenfassung des Diktats ist an seine erste ASR-Revision gebunden.

### Ohne lokales Textmodell

Ein lokales Modell ist nur bereit, wenn der Klinikmodus eingeschaltet ist, die obige Regel für lokale Modelle erfüllt ist, ein Sprachmodell-Client existiert und dessen Basis-URL lokal ist. Andernfalls wird kein automatischer Auftrag eingereiht, keine Schaltfläche zum Erstellen angezeigt, eine Anforderung mit 503 beantwortet, und **Modellwahl** zeigt für **Textmodell** **Derzeit inaktiv: Kein Modell aktiv**. Ein Ausweichen auf ein gehostetes Modell gibt es nicht.

## Nur Upstream

Diese Teile der Upstream-Seite gelten für die Klinikversion nicht. Die Variablen können im Code noch vorhanden sein, sind aber keine Anleitung für den Klinikbetrieb:

- Gehostete Textmodell-Anbieter (OpenRouter, OpenAI, Azure AI Foundry und andere Gateways) sowie Kostenoptimierung. Im Klinikmodus sperrt eine gehostete URL die Upstream-Zusammenfassungsrouten, und der automatische Folgeauftrag darf nicht auf eine solche zeigen (siehe die offene Lücke oben).
- GPT-5-Einstellungen (`GPT5_REASONING_EFFORT`, `GPT5_VERBOSITY`, `GPT5_FORCE_MODE`, `CHAT_GPT5_*`, `TRANSLATION_GPT5_FORCE_MODE`) und Cache-Breakpoints für Anthropic-Modelle (`TEXT_MODEL_CACHE_BREAKPOINTS`).
- Separate Chat- und Übersetzungsmodelle (`CHAT_MODEL_*`, `TRANSLATION_MODEL_*`, `CHAT_MAX_TOKENS`, `ENABLE_STREAM_OPTIONS`): Chat und Übersetzung haben keine Klinikoberfläche. Sind ihre URLs gesetzt, müssen sie lokal sein, sonst werden die Funktionen für lokale Modelle gesperrt.
- Embedding-Modelle (`EMBEDDING_*`) für Inquire; siehe [Ambrose-Einstellungen](ambrose.md) und [Vektorspeicher](vector-store.md). Eine gesetzte, nicht lokale `EMBEDDING_BASE_URL` sperrt ebenfalls die Funktionen für lokale Modelle.
- Cloud-Transkriptionsanbindungen (OpenAI Transcribe, Azure OpenAI, Mistral Voxtral samt Chunking, VibeVoice und weitere) sind aus dem Fork entfernt.
- Die Modellpflege, wie Upstream sie beschreibt: Der Reiter **Standard-Prompts** wird im Klinikmodus nicht angezeigt, und der Bereich **Transkriptionsmodelle** in **Systemeinstellungen** kann weder erkennen noch speichern (siehe oben).

Upstream-Speakr-Dokumentation (fixiertes Original): [Model Configuration](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/model-configuration.md).

## Fehlerbehebung

- **Transkriptionsaufträge scheitern, und das Startprotokoll enthält `Failed to initialize transcription connector: ... CLINIC_MODE is active: transcription base URL must point to a local or private service`.** `TRANSCRIPTION_BASE_URL` oder `CLINIC_GPU_ASR_BASE_URL` ist nicht lokal. Die Aufträge scheitern, bis dies behoben ist.
- **Transkriptionsaufträge scheitern, und das Startprotokoll enthält `Failed to initialize transcription connector: ... Unknown connector: ...`.** `TRANSCRIPTION_CONNECTOR` nennt eine entfernte Anbindung; verwenden Sie `openai_whisper`, `asr_endpoint` oder `clinic_stub`.
- **Transkriptionsaufträge scheitern, und das Startprotokoll enthält `Failed to initialize transcription connector: ... TRANSCRIPTION_TIMEOUT=... is invalid`** (oder gleich für `TRANSCRIPTION_MAX_RETRIES`). Verwenden Sie eine positive Zahl von Sekunden beziehungsweise eine nicht negative ganze Zahl.
- **Die Anwendung beendet sich beim Start mit `ERROR: No transcription service configured!`** (oder `ERROR: ASR enabled but ASR_BASE_URL not configured!`). Die Anbindung ist gescheitert, und auch die Prüfung der alten Konfiguration scheitert: `TRANSCRIPTION_BASE_URL` oder `TRANSCRIPTION_API_KEY` ist leer (oder `USE_ASR_ENDPOINT=true` ist ohne `ASR_BASE_URL` gesetzt). Die vorangehende Zeile `Failed to initialize transcription connector: ...` nennt den Fehler der Anbindung.
- **Zusammenfassungs- oder Titelrouten antworten mit 403 `clinic_feature_disabled`.** Das Textmodell ist nicht lokal: Prüfen Sie `TEXT_MODEL_BASE_URL` und jede gesetzte `CHAT_MODEL_BASE_URL`, `TRANSLATION_MODEL_BASE_URL` und `EMBEDDING_BASE_URL`, und ergänzen Sie bei Bedarf einen Dienstnamen in `CLINIC_LOCAL_MODEL_HOSTS`.
- **Erkennen** oder **Auswahl speichern** **antwortet mit 403.** Im Klinikmodus erwartet (siehe Bereich **Transkriptionsmodelle** oben).
- **GPU-Aufträge scheitern mit `model_not_found`.** Der Modellname passt nicht zum GPU-Dienst. Entfernen oder korrigieren Sie `CLINIC_GPU_ASR_MODEL`; 4xx-Fehler weichen nicht auf die CPU aus.
- **Eine Änderung prüfen.** Starten Sie die Anwendung neu. Prüfen Sie im Startprotokoll `Transcription connector initialized: <name>`, mit GPU-Ausweichlösung zusätzlich `ASR GPU target <url> configured (timeout <n>s); CPU target stays the fallback`, sowie `LLM client initialized: <url> / <model>` (oder `Failed to initialize LLM client: ...`, wenn kein Textmodell konfiguriert ist). Führen Sie danach ein synthetisches Testdiktat aus.
- **Welche Werte gelten.** Jeder Aufruf des Textmodells protokolliert `LLM call: operation=..., model=..., max_tokens=...`.
- **Abgeschnittene oder leere Zusammenfassungen.** Das Protokoll meldet `Raise SUMMARY_MAX_TOKENS or limit reasoning with TEXT_MODEL_EXTRA_BODY.` Erhöhen Sie `SUMMARY_MAX_TOKENS` oder schalten Sie das «Thinking» mit `TEXT_MODEL_EXTRA_BODY` ab.
- **Langsames oder hängendes Sprachmodell.** Im Klinikmodus endet jeder Aufruf nach `CLINIC_LLM_TIMEOUT_SECONDS` (120 s) mit höchstens `CLINIC_LLM_MAX_RETRIES` (2) Wiederholungen; Transkriptionsprozesse werden nicht blockiert, weil Titel und Zusammenfassungen in der Zusammenfassungswarteschlange laufen.
