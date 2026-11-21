---
layout: default
title: Lokale Spracherkennung einrichten
parent: Administrationshandbuch
nav_order: 8
---

# Lokale Spracherkennung (ASR) einrichten
<small>[English](../../admin-guide/whisperx-setup.md)</small>

In der Klinikversion läuft die Spracherkennung ausschliesslich über lokale Dienste. Diese Seite ersetzt die WhisperX-Anleitung von Upstream-Speakr. Sie beschreibt den lokalen CPU-Dienst, den optionalen GPU-Dienst mit CPU-Ausweichlösung, Zeitlimits, Statusansichten, den optionalen Sprecherdienst sowie die Wirkung und die Grenzen von Hotwords.

Die Regeln dieser Seite (Lokalitätsprüfung der Adressen, GPU-Ausweichung, Zeitlimits und Wiederholungen des Klinikmodus, Hinweisschalter, deutsche Sprachvorgabe) gelten nur, wenn `CLINIC_MODE` auf `1`, `true`, `yes` oder `on` steht. Unabhängig davon kennt die Klinikversion nur die Anbindungen `openai_whisper`, `asr_endpoint` und `clinic_stub`; die Cloud-Anbindungen von Upstream fehlen auch ohne Klinikmodus. Ohne Klinikmodus wird `TRANSCRIPTION_BASE_URL` jedoch nicht geprüft, und ein leerer Wert sendet das Audio an den Standardendpunkt des OpenAI-SDK. `CLINIC_MODE` ist standardmässig aus: Betreiben Sie die Klinikversion mit eingeschaltetem Klinikmodus.

Dienstadressen, Schlüssel, Zeitlimits und Schalter der Spracherkennung sind Umgebungsvariablen der Installation. Nur `asr_timeout_seconds` und `asr_async_jobs` (beide nur für `asr_endpoint`) stehen unter **Systemeinstellungen**; die Modellvorgabe ist in der [Modellkonfiguration](model-configuration.md) beschrieben. Die Anbindung einschliesslich GPU-Hülle wird einmal pro Anwendungsprozess aufgebaut; starten Sie die Anwendung nach der Änderung einer Umgebungsvariablen neu. Es gibt keine Verwaltungsseite, auf der ASR-Dienstadressen oder Schlüssel gesetzt werden.

## Statusübersicht

| Funktion | Status | Kurzhinweis |
|---|---|---|
| Lokaler OpenAI-kompatibler ASR-Dienst (zum Beispiel Speaches) über die Anbindung `openai_whisper` | Verfügbar | Die Dienstadresse muss lokal oder privat sein, sonst startet die Anbindung nicht. |
| Zuerst GPU, dann CPU (mitgelieferter OpenVINO-GPU-Dienst) | Standardmässig aus | Nur aktiv, wenn `CLINIC_GPU_ASR_BASE_URL` gesetzt ist. Eine Ausweichung auf das CPU-Ziel, keine Wiederholung auf der GPU. |
| Anbindung `asr_endpoint` (WhisperX, whisper-asr-webservice) | Verfügbar (unveränderte Upstream-Anbindung) | Ihre Adresse `ASR_BASE_URL` wird **nicht** auf Lokalität geprüft (offene Lücke; siehe den Abschnitt zu dieser Anbindung unten). |
| Cloud-ASR-Anbindungen (OpenAI GPT-4o Transcribe, Azure OpenAI, Mistral, Tencent, Alibaba FunASR, Mossland, AssemblyAI, VibeVoice, OpenASR) | Nur Upstream | In der Klinikversion entfernt. Kein Codepfad weicht auf eine Cloud-Erkennung aus. |
| Transkriptionshinweise (Initial-Prompt, Hotwords) | Standardmässig aus | Werden verworfen, solange `CLINIC_ASR_PROMPTS_ENABLED` nicht eingeschaltet ist. |
| Wortzeitstempel | Standardmässig aus | `TRANSCRIPTION_WORD_TIMESTAMPS`. |
| ASR-Serverprofile pro Autorin oder Autor (Sprache, Modell, Hotword-Sätze) | Vorbereitet, deaktiviert | In der aktuellen Version nie angewendet; Aufträge laufen mit den deutschen Vorgaben. |
| **Optimierung**, Aussprachetraining, Negativlexikon | Vorbereitet, deaktiviert | Jeder Trainingslauf wird abgelehnt. Es wird nichts trainiert oder angepasst. |
| Live-ASR-Adapter (`CLINIC_LIVE_ASR_BASE_URL`) | Vorbereitet, deaktiviert | Keine Route und kein Auftrag verwendet ihn. |
| Sprecherdienst (eigenes Stimmprofil) | Standardmässig aus | Separater lokaler Prozess, nur mit eigener Einwilligung der Benutzerin oder des Benutzers. |
| Stimmbasierter Autorenvorschlag | Vorbereitet, deaktiviert | Der Code ist vorhanden; keine Route und kein Auftrag ruft ihn auf. |
| Einrichtung des WhisperX-Dienstes (Hugging-Face-Token, pyannote), Stimmprofile aus Sprecher-Embeddings, Sprecheridentifikation | Nur Upstream | Die Sprecheridentifikation ist im Klinikmodus gesperrt; Embeddings diarisierter Sprecher werden nicht gespeichert. Die Diarisierung selbst fordert `asr_endpoint` standardmässig an (`ASR_DIARIZE`). |
| Anbindung `clinic_stub` | Simuliert | Synthetischer Testersatz; liefert feste Sätze. |
| Modellwahl pro Aufgabe (GPU- und CPU-Modell) | In Prüfung | Nicht Teil der aktuell freigegebenen Version. |
| Erkennungsqualität für deutsche klinische Diktate | Offene Lücke | Nicht gemessen: keine Wortfehlerrate an deutschem Klinikaudio, kein Audio von Philips-Diktiergeräten. |

## Auswahl der Anbindung

Die Klinikversion kennt nur drei Transkriptionsanbindungen: `openai_whisper`, `asr_endpoint` und `clinic_stub`. Die Anwendung wählt in dieser Reihenfolge:

1. `TRANSCRIPTION_CONNECTOR`, falls gesetzt (`openai_whisper`, `asr_endpoint` oder `clinic_stub`). Jeder andere Name scheitert mit `Unknown connector: <name>. Available: [...]`.
2. `asr_endpoint`, falls `ASR_BASE_URL` gesetzt ist (oder das veraltete `USE_ASR_ENDPOINT=true`, das eine Warnung protokolliert).
3. Sonst `openai_whisper` mit dem Modell aus `TRANSCRIPTION_MODEL`, dann aus dem älteren `WHISPER_MODEL`, dann `whisper-1`.

Verwenden Sie für eine Klinikinstallation `openai_whisper` mit einem lokalen Dienst. Das Startprotokoll nennt das Ergebnis, zum Beispiel `Initialized transcription connector: openai_whisper`.

## CPU-Pfad: lokaler OpenAI-kompatibler Dienst

Empfohlen ist ein OpenAI-kompatibler Dienst wie Speaches, angesprochen über die Anbindung `openai_whisper`. Das Repository liefert keine Speaches-Installation mit. Die Beispieldatei [config/env.transcription.example](https://github.com/joehomeskillet/speakr/blob/main/config/env.transcription.example) (Quellcode des Klinik-Forks) erwartet einen Dienst namens `speaches` im selben Docker-Netz:

```bash
CLINIC_MODE=1
TRANSCRIPTION_CONNECTOR=openai_whisper
TRANSCRIPTION_BASE_URL=http://speaches:8000/v1
TRANSCRIPTION_API_KEY=not-needed-for-local
TRANSCRIPTION_MODEL=Systran/faster-distil-whisper-large-v3
```

Regeln im Klinikmodus:

- **Die Adresse muss lokal sein (fail closed).** `TRANSCRIPTION_BASE_URL` muss auf Loopback (`localhost`, 127.0.0.0/8, ::1), eine private Adresse (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, fc00::/7), eine Link-local-Adresse, einen einteiligen Hostnamen wie einen Compose-Dienstnamen oder einen Namen mit der Endung `.local`, `.internal`, `.lan` oder `.localhost` zeigen. Ein leerer Wert wird ebenfalls abgelehnt; die OpenAI-Vorgabe gilt also nie. Die Anbindung scheitert dann mit `CLINIC_MODE is active: transcription base URL must point to a local or private service ...`.
- **Ein Schlüssel ist Pflicht.** `TRANSCRIPTION_API_KEY` darf nicht leer sein. Ein lokaler Dienst ohne Authentifizierung akzeptiert einen beliebigen Platzhalter.
- **Modell.** Der Modellwert in der Beispieldatei ist nur ein Ausgangspunkt. Kein Modell wurde an deutschem Klinikaudio gemessen. Das angefragte Modell kann auch aus dem Upload, einem Tag, einem Ordner oder der Administrationsvorgabe stammen; siehe [Modellkonfiguration](model-configuration.md).
- **Grosse Dateien.** Die Anbindung nimmt Dateien bis 25 MB an. Grössere Dateien teilt die Anwendung auf (`ENABLE_CHUNKING`, Vorgabe `true`; Grösse aus `CHUNK_LIMIT`, in der Beispieldatei `20MB`; Überlappung `CHUNK_OVERLAP_SECONDS`, Vorgabe `3`).

Die Anbindung `asr_endpoint` richtet sich an Dienste mit einem Endpunkt `/asr` (whisper-asr-webservice, WhisperX). Binden Sie einen OpenAI-kompatiblen Dienst wie Speaches über `openai_whisper` an.

## GPU-Pfad mit automatischer CPU-Ausweichlösung

**Status: Standardmässig aus.** Der GPU-Pfad wird nur verwendet, wenn der Klinikmodus eingeschaltet und `CLINIC_GPU_ASR_BASE_URL` gesetzt ist. Die Anwendung umhüllt dann die konfigurierte Anbindung (`openai_whisper` oder `asr_endpoint`, nie `clinic_stub`). Das gilt für Upload, erneute Verarbeitung, Wiederholungen und aufgeteilte Aufträge.

```bash
CLINIC_GPU_ASR_BASE_URL=http://gpu-asr:8000/v1
# CLINIC_GPU_ASR_TIMEOUT=300
# CLINIC_GPU_ASR_API_KEY=<secret>
# CLINIC_GPU_ASR_MODEL=
```

- Das GPU-Ziel ist ein OpenAI-kompatibler Client. Auch seine Adresse muss lokal sein; ein nicht lokaler Wert lässt den Start der Anbindung scheitern.
- Text nach `#` in der Adresse wird ignoriert.
- Ohne `CLINIC_GPU_ASR_API_KEY` gilt der CPU-Schlüssel, sonst `local`.

### Reihenfolge und Bedingungen der Ausweichung

Für jeden Auftrag (und jeden Teilabschnitt):

1. Die Anwendung ruft `/healthz` an der Wurzel der GPU-Adresse auf (Zeitlimit 5 s).
2. Antwortet die Prüfung mit 2xx, geht der Auftrag an das GPU-Ziel, mit einem einzigen Versuch ohne Wiederholung.
3. Derselbe Auftrag läuft **einmal** auf dem CPU-Ziel, wenn einer dieser Fälle eintritt:
   - `/healthz` überschreitet das Zeitlimit, ist nicht erreichbar oder antwortet mit einem Status ab 300;
   - die GPU-Anfrage überschreitet das Zeitlimit, hat einen Verbindungsfehler oder antwortet mit HTTP 500 oder höher.
4. Ein CPU-Fehler beendet diesen Versuch. Innerhalb eines Versuchs gibt es keine zweite Ausweichung und keine Cloud-Ausweichung. Ob der Auftrag erneut läuft, entscheidet die Auftragswarteschlange (siehe den Abschnitt «Zeitlimits und Wiederholungen» unten); jeder neue Versuch beginnt wieder mit `/healthz`.

Jeder andere GPU-Fehler beendet den Versuch **ohne** CPU-Ausweichung, zum Beispiel `404 model_not_found`, `400 invalid_audio` oder `413 payload_too_large`.

### Modellname auf der GPU

- Ist `CLINIC_GPU_ASR_MODEL` gesetzt, verwendet jede GPU-Anfrage diesen Namen.
- Ohne diese Variable und wenn `/healthz` den mitgelieferten OpenVINO-Dienst erkennen lässt (`model_loaded: true` und eine `openvino_genai`-Version), verwendet die Anfrage den dort gemeldeten Modellnamen.
- Sonst verwendet die Anfrage das CPU-Modell oder das Modell der einzelnen Anfrage.

Der mitgelieferte Dienst akzeptiert nur seinen eigenen Modellnamen und `whisper-1`. Jeder andere Name ergibt `404 model_not_found` und lässt den Auftrag ohne Ausweichung scheitern. Setzen Sie die feste Vorgabe nur auf einen dieser beiden Namen.

### Protokollzeilen

| Wann | Stufe | Text |
|---|---|---|
| Start der Anbindung | INFO | `ASR GPU target <url> configured (timeout <n>s); CPU target stays the fallback` |
| Jeder GPU-Auftrag | INFO | `ASR: sending job to GPU target <url>` |
| Ausweichung | WARNING | `ASR fallback: GPU target <url> not used (<reason>); transcribing on CPU target <url>` |

Ausweichungen werden nur protokolliert, nicht ins Auditprotokoll geschrieben. Jeder Klinikauftrag speichert die geplante Engine (`selected`, `fallback`) und nach dem Lauf die tatsächlich verwendete Engine (`actual`) in einem Datensatz `clinic_asr` seines Verarbeitungsauftrags. Keine Seite zeigt diesen Datensatz an.

### Mitgelieferter OpenVINO-GPU-Dienst

**Status: Standardmässig aus.** Der Dienst liegt in [deployment/gpu-asr-service](https://github.com/joehomeskillet/speakr/blob/main/deployment/gpu-asr-service/docker-compose.snippet.yml) (Quellcode des Klinik-Forks). Ein Kommentar im Compose-Ausschnitt hält fest, dass der Dienst noch nicht die Standard-Spracherkennung ist. Er ist nicht Teil einer Standardinstallation.

- **Schnittstelle.** `POST /v1/audio/transcriptions` (OpenAI-kompatibel, `json` oder `verbose_json`) und `GET /healthz`, auf Port 8000.
- **Engine.** OpenVINO GenAI `WhisperPipeline` auf dem Intel-GPU-Gerät `GPU.0`, Modell `whisper-large-v3-turbo-int8-ov`.
- **Build.** Der Image-Build lädt die Intel-GPU-Laufzeitpakete und die Modelldateien in einer festgelegten Modellrevision herunter. Der Build bricht ab, wenn eine SHA-256-Prüfsumme nicht passt (`model.sha256`). Das Modell liegt nicht im Repository.
- **Laufzeit.** Ein gunicorn-Worker und ein Engine-Thread: Anfragen laufen strikt nacheinander. Audio wird mit ffmpeg im Speicher dekodiert und nicht gespeichert.
- **Zustand.** `/healthz` liefert `status`, `model_loaded`, `model`, `device` und `versions`; solange das Modell nicht geladen ist, antwortet der Endpunkt mit 503. Der Docker-Healthcheck ruft alle 30 s `curl http://127.0.0.1:8000/healthz` auf.
- **Zeitlimit.** Eine Anfrage, die länger als `ASR_TIMEOUT_S` dauert, antwortet mit `504 engine_timeout`. In Speakr zählt das als HTTP 5xx und löst die CPU-Ausweichung aus.
- **Geräte.** Der Compose-Ausschnitt reicht `/dev/dri` in den Container und fügt die Render-Gruppe aus `RENDER_GID` hinzu (Vorgabe `109`, die Gruppe von `/dev/dri/renderD*`).

Umgebungsvariablen des GPU-Dienst-Containers (nicht von Speakr):

| Variable | Vorgabe | Bedeutung |
|---|---|---|
| `ASR_MODEL_DIR` | `/models/whisper-large-v3-turbo-int8-ov` | Modellverzeichnis im Container |
| `ASR_MODEL_NAME` | `whisper-large-v3-turbo-int8-ov` | Bereitgestellter Modellname; Anfragen dürfen diesen Namen oder `whisper-1` nennen |
| `ASR_DEVICE` | `GPU.0` | OpenVINO-Gerät |
| `ASR_TIMEOUT_S` | `300` | Höchstdauer pro Anfrage in Sekunden |
| `ASR_PROMPTS_ENABLED` | `0` | Prompt-Schalter des Dienstes (`1`, `true`, `yes` = ein) |
| `ASR_PROMPT_MAX_WORDS` | `55` | Wortbudget für den Prompt; kann nur gesenkt werden |
| `ASR_MAX_UPLOAD_BYTES` | `536870912` (512 MiB) | Upload-Grenze; darüber `413 payload_too_large` |

Prüfen Sie den Dienst aus einem Container im selben Netz:

```bash
curl http://gpu-asr:8000/healthz
```

## Zeitlimits und Wiederholungen

| Pfad | Einstellung | Vorgabe im Klinikmodus | Hinweis |
|---|---|---|---|
| CPU-Ziel (`openai_whisper`) | `TRANSCRIPTION_TIMEOUT` (Sekunden, > 0) | 3600 | Ohne Klinikmodus: SDK-Vorgabe 600 s |
| CPU-Ziel (`openai_whisper`) | `TRANSCRIPTION_MAX_RETRIES` (Ganzzahl >= 0) | 0 | Ohne Klinikmodus: SDK-Vorgabe 2. Ein ungültiger Wert einer der beiden Variablen ist ein Konfigurationsfehler. |
| GPU-Ziel | `CLINIC_GPU_ASR_TIMEOUT` | 300 | Ungültig oder <= 0 ergibt 300. Keine Wiederholungen des SDK; innerhalb eines Versuchs tritt die CPU-Ausweichung an die Stelle einer Wiederholung. |
| GPU-Zustandsprüfung | fest | 5 s | |
| `asr_endpoint` | `ASR_TIMEOUT`, dann die ältere Umgebungsvariable `asr_timeout_seconds`, dann die Systemeinstellung `asr_timeout_seconds`, dann 1800 | 1800 (angelegte Einstellung) | Wird bei jedem Aufruf gelesen. Gilt nur für `asr_endpoint`. |
| Auftragswarteschlange (alle Pfade) | `JOB_MAX_RETRIES` | 3 | Höchstzahl der Versuche pro Auftrag, nur bei vorübergehenden Fehlern; siehe [Systemeinstellungen](system-settings.md). |

Nach jedem fehlgeschlagenen Versuch entscheidet die Auftragswarteschlange, ob der Auftrag erneut läuft. Ein Fehler, der nicht als dauerhaft gilt, zum Beispiel eine Zeitüberschreitung, ein Verbindungsfehler oder HTTP 5xx, stellt den ganzen Auftrag erneut ein, bis `JOB_MAX_RETRIES` Versuche verbraucht sind (Vorgabe `3`, also höchstens drei Versuche insgesamt). Mit GPU-Ziel beginnt jeder neue Versuch wieder mit `/healthz`, dann GPU, dann CPU. Ein dauerhafter Fehler lässt den Auftrag sofort scheitern: HTTP 400, 401, 402, 403, 404 oder 413 oder eine Fehlermeldung mit einem Muster wie `model not found` oder `invalid file format`. Nach einer Zeitüberschreitung auf dem CPU-Ziel wird dasselbe Audio deshalb trotz `TRANSCRIPTION_MAX_RETRIES=0` erneut gesendet; rechnen Sie mit bis zu drei CPU-Anfragen von je bis zu 3600 s pro Auftrag.

Die Systemeinstellung `asr_timeout_seconds` (in **Systemeinstellungen**, Anzeige in Sekunden) wirkt weder auf den Speaches-Pfad noch auf das GPU-Ziel. Siehe [Systemeinstellungen](system-settings.md). Eine Änderung einer Systemeinstellung wird als `admin.setting.changed` auditiert; für `asr_timeout_seconds` wird nur der Schlüsselname gespeichert.

## Erneute Erkennung und späte Ergebnisse

- **Erneut transkribieren** im **Schreibplatz** setzt eine offene Bearbeitungssperre, abspielbares Audio, einen gespeicherten Text und keine ungespeicherten Änderungen voraus. Das neue Ergebnis wird gegen den aktuellen Text, die Audiorevision und den Ausgangsauftrag geprüft; ein veraltetes oder doppeltes Ergebnis wird nicht übernommen. Der Server verlangt zusätzlich das Recht **Bearbeiten** am Diktat und die aktuelle Bearbeitungssperre (`lease_generation`). Solange für das Diktat ein ASR-Auftrag wartet oder läuft, lehnt er die Anfrage mit `recording_already_processing` ab. Zum Recht **Bearbeiten** siehe [Benutzerverwaltung](user-management.md).
- Ist der aktuelle Text eine menschliche Korrektur, wird das ASR-Ergebnis als Nebenrevision abgelegt. Die menschliche Revision bleibt aktuell.
- Die ASR-Profilansicht meldet die Wartevorgabe: Die manuelle Erfassung (**Manuell schreiben**) wird nach 120 s angeboten oder nach dem Profilwert `skip_wait_minutes`, sofern dieser höchstens 14 Minuten beträgt; ein grösserer Wert wird ignoriert, und es gelten 120 s. Nach 900 s gilt die Erkennung als abgelaufen. Ein spätes ASR-Ergebnis wird als Nebenrevision abgelegt und ersetzt nie manuelle Arbeit.
- Fehlgeschlagene Aufträge lassen sich auf der Seite **Verarbeitung** erneut starten; siehe den Abschnitt «Status und Zustand» unten.

## Sprache und Transkriptionshinweise

- **Sprache.** Ein Klinikauftrag ohne Sprache aus Anfrage, Tag, Ordner oder Eigentümerkonto erhält `de` (Deutsch). Der Wert `auto` erzwingt die automatische Erkennung.
- **Hinweise standardmässig aus.** Im Klinikmodus verwerfen Batch-Aufträge den Initial-Prompt und die Hotwords aus allen Quellen (Administrationsvorgabe, Benutzer, Tag, Ordner, Upload), solange `CLINIC_ASR_PROMPTS_ENABLED` nicht auf `1`, `true`, `yes` oder `on` steht. Auch ein Auftrag ohne Klinikdatensatz läuft ohne Hinweise.
- **Wortgrenze.** Bei eingeschaltetem Schalter teilen sich Initial-Prompt und Hotwords ein Budget von 55 Wörtern; der Initial-Prompt wird zuerst gezählt. Die Anbindung `openai_whisper` sendet beide zusammen in einem Feld `prompt`.
- **GPU-Dienst.** Der mitgelieferte GPU-Dienst hat einen eigenen Schalter `ASR_PROMPTS_ENABLED` (Vorgabe aus). Ist er aus, verwirft der Dienst den Prompt und protokolliert `prompt/hotwords ignored: ASR_PROMPTS_ENABLED is off (<n> words)`. Hinweise erreichen die GPU-Engine nur, wenn beide Schalter eingeschaltet sind.
- Die administrativen Vorgaben für Hotwords und Initial-Prompt (`admin_default_hotwords`, `admin_default_initial_prompt`) wirken im Klinikmodus deshalb standardmässig nicht. Siehe [Prompts](prompts.md).

### Hotwords sind kein Training

Hotwords, Prompts, Wortschatz und Profileinstellungen beeinflussen den Decoder. Sie trainieren das akustische Modell oder das Sprachmodell nicht und passen es nicht an.

- Einträge im **Wortschatz** werden nicht mit der ASR-Anfrage gesendet.
- ASR-Serverprofile pro Autorin oder Autor gelten für einen Auftrag nur mit einem erfassten Laufzeitnachweis für genau dieses Ziel. Die aktuelle Version erfasst keine solchen Nachweise; jeder Auftrag läuft deshalb mit den deutschen Vorgaben (vorbereitet, deaktiviert).
- **Optimierung starten** auf der Seite **Sprachprofile** legt nur dann einen Auftrag vom Typ `asr_training` an, wenn der Lauf eingeplant wird (**Lauf eingeplant.**). Die anderen Ergebnisse legen keinen neuen Auftrag an: Für dasselbe Material läuft bereits ein Lauf, der Start liegt ausserhalb des Zeitfensters oder wird zurückgestellt, weil ein anderer Lauf aktiv ist, die Mindestdaten fehlen, oder die Einplanung scheitert. Die Warteschlange lehnt jeden solchen Auftrag mit `CAPABILITY_UNAVAILABLE` ab; die Auftragsübersicht zeigt **Training nicht verfügbar**. Es wird kein Modell trainiert.
- Aussprachetraining und Negativlexikon antworten ohne Nachweis mit `CAPABILITY_UNAVAILABLE`. Das Speichern einer Liste oder eines phonetischen Hinweises belegt keine Wirkung auf die Erkennung.
- Die Anpassung aufgrund von Korrekturen bleibt eine offene Anforderung unter dem offenen Produktionsgate `G-ASR-PARITY`.

Die Seite hält selbst fest: **«Eine Wortliste ersetzt kein Akustiktraining.»**

### Die Seite Wortschatz

**Wortschatz** (`/admin#/wortschatz`, im Kopfzeilenmenü im Bereich **Sprache und Verarbeitung**) hat die Reiter **Wörter**, **Vorschläge**, **Aussprache** und **Enginewirkung**.

- **Wörter:** Wählen Sie unter **Aktueller Bereich** entweder **Persönlich** (die **Eigene Liste**) oder **Autorenpool** mit **Autorenpool wählen**. Jeder Eintrag hat eine **Schreibweise**, eine Form **Gesprochen** und den Status **Aktiv** oder **Unterdrückt**. Die Aktionen sind **Wortliste importieren**, **Markierten Text hinzufügen**, **Unterdrücken**, **Reaktivieren**, **Wortliste exportieren** und **Wirksame Liste exportieren**.
- Jedes angemeldete Konto darf seine eigene persönliche Liste ändern. Die Poolliste öffnen darf nur ein Mitglied des Autorenpools, und für Änderungen braucht es eine weitere Befugnis; siehe [Wirkung auf gemeinsame Poolressourcen](group-management.md#wirkung-auf-gemeinsame-poolressourcen). Eine globale Administration, die nicht Mitglied des Autorenpools ist, kann dessen Liste nicht öffnen.
- **Vorschläge:** die Prüfliste für vorgeschlagene Änderungen am Wortschatz; eine ihrer Entscheidungen ist **Für den Pool übernehmen**. Sie bleibt in der freigegebenen Version leer, weil kein Code Vorschläge anlegt. Der Reiter zeigt dann **Keine Vorschläge zu prüfen.**
- Einträge der Wortliste werden nicht mit der ASR-Anfrage gesendet und sind kein akustisches Training (siehe oben).

## Status und Zustand

Keine Verwaltungsseite zeigt den aktuellen Zustand der ASR- oder Sprecherdienste. Verwenden Sie diese Ansichten:

| Wo | Wer | Was angezeigt wird |
|---|---|---|
| **Verwaltung** › **Betrieb** › **Sprachprofile**, `/admin#/sprachprofile`; Eintrag **ASR und Sprachprofile** im Kopfmenü | Aktive globale Administration; für andere Konten wird die Seite zurückgehalten | Bereich **Spracherkennung** für das angemeldete Konto. **Verwendete Erkennung** zeigt **Nicht ermittelt**, weil keine Fähigkeitsnachweise erfasst sind; die tatsächlich verwendete Engine wird nicht angezeigt. **Eingabehinweise (Prompts)** zeigt immer **Aus**, auch wenn `CLINIC_ASR_PROMPTS_ENABLED` eingeschaltet ist. **Fähigkeiten und Nachweise** zeigt keine Fähigkeit als **Nachgewiesen**. |
| **Verwaltung** › **Betrieb** › **Verarbeitung**, `/admin#/verarbeitung` | Aktive globale Administration sieht alle Aufträge; andere Konten sehen nur Aufträge von Diktaten, die sie ansehen dürfen | Auftragszahlen, hängende Aufträge (länger als 1800 s in Verarbeitung), die letzten Fehler mit **Fehlerdetails ausgeblendet.** Kein Transkript, keine Parameter, kein Audiopfad. **Erneut versuchen** ist nur für die aktive globale Administration erlaubt. |
| Kontoseite (`/account`), Register **Über**, Abschnitt **Spracherkennung** | Alle angemeldeten Konten sehen das Modell; nur die globale Administration sieht zusätzlich den **Endpunkt** | Modell und Adresse der CPU-Anbindung, nicht das GPU-Ziel. Ohne `TRANSCRIPTION_BASE_URL` zeigt der Endpunkt den Text `https://api.openai.com/v1` nur als Anzeigevorgabe; im Klinikmodus kann eine solche Anbindung nicht starten. |
| Anwendungsprotokoll | Wer Zugriff auf das Anwendungsprotokoll der Installation hat (kein Recht in der Weboberfläche) | Start- und Ausweichzeilen der Anbindung (siehe oben). Beim Start eine Warnung pro ASR-Adresse mit unverschlüsseltem `http` zu einem anderen Rechner (`ASR_BASE_URL`, `TRANSCRIPTION_BASE_URL`), auch für private Adressen; Loopback, einteilige Namen und Docker-Hostnamen sind ausgenommen. |

Aktive globale Administration bedeutet: **Administrationsrolle** und ein Klinikkonto mit dem Status **Aktiv**. Ein Lauf der **Optimierung** umfasst nur die eigenen freigegebenen Korrekturen des aufrufenden Kontos der globalen Administration. Siehe [Benutzerverwaltung](user-management.md).

## Sprecherdienst (optional)

**Status: Standardmässig aus.** Der Sprecherdienst ist ein separater lokaler Prozess für eingewilligte eigene Stimmprofile. Die Transkription braucht ihn nicht.

- Quelle: `deployment/speaker-service`. Nur CPU, Port 8090, Modelle beim Build per SHA-256 festgelegt, ein Worker, läuft unter einem Benutzer ohne Root-Rechte.
- Endpunkte: `GET /healthz`, `POST /embed`, `POST /diarize` (WAV-Daten als Inhalt der Anfrage). `/healthz` antwortet mit 503, solange die Modelle nicht geladen sind.
- Grenzen im Container: `SPEAKER_MAX_BYTES` (Vorgabe 268435456, 256 MiB), `SPEAKER_MIN_EMBED_SECONDS` (Vorgabe `3.0`, Sekunden), `SPEAKER_MIN_DIARIZE_SECONDS` (Vorgabe `1.0`, Sekunden).
- Speakr erreicht ihn nur über `CLINIC_SPEAKER_SERVICE_URL`, mit 120 s Zeitlimit (Zustandsprüfung 5 s) und ohne Wiederholungen. Ohne diese Variable lässt sich kein Stimmprofil anlegen. Offene Lücke: Speakr prüft diese Adresse nicht auf Lokalität. Verweisen Sie nur auf einen lokalen Dienst.
- Er dient nur dem eigenen Stimmprofil nach Einwilligung (**Eigenes Stimmprofil (freiwillig)**). Der stimmbasierte Autorenvorschlag ist vorbereitet, deaktiviert. Die Verarbeitung ruft `/diarize` nicht auf.
- Einwilligung und Widerruf sind unter [Datenschutz](privacy.md) beschrieben.

## Anbindung asr_endpoint (WhisperX, whisper-asr-webservice)

**Status: Verfügbar (unveränderte Upstream-Anbindung), nicht der empfohlene Klinikpfad.**

> **Offene Lücke: `ASR_BASE_URL` wird nicht auf Lokalität geprüft.** Im Klinikmodus werden nur `TRANSCRIPTION_BASE_URL` und `CLINIC_GPU_ASR_BASE_URL` geprüft. Eine öffentliche `ASR_BASE_URL` wird nicht abgelehnt; einziger Schutz ist die Startwarnung bei unverschlüsseltem `http`. Verweisen Sie mit `ASR_BASE_URL` nur auf einen lokalen oder privaten Host.

Wenn Sie ein selbst betriebenes WhisperX oder whisper-asr-webservice auf einem lokalen Host verwenden:

- `ASR_BASE_URL`, `ASR_API_KEY` (Vorgabe leer).
- Zeitlimit: siehe Tabelle oben (Vorgabe 1800 s aus der Systemeinstellung).
- `ASR_ASYNC_JOBS` / Systemeinstellung `asr_async_jobs` (Vorgabe `true`): Meldet der Dienst die Auftragsschnittstelle von WhisperX in `/health`, wird das Audio als Auftrag eingereicht und abgefragt, bis es fertig verarbeitet ist. Danach löscht die Anbindung den Auftrag samt Transkript auf dem Dienst; ebenso nach einem Fehlschlag auf dem Dienst und nach einem Abbruch. Ein erneuter Versuch des Speakr-Auftrags setzt einen gespeicherten Auftrag fort. Dienste ohne Auftragsschnittstelle oder `ASR_ASYNC_JOBS=false` erhalten die synchrone Anfrage an `/asr`. Siehe [Systemeinstellungen](system-settings.md). Eine Änderung wird als `admin.setting.changed` mit altem und neuem Wert auditiert.
- Ein Modellname wird nur gesendet, wenn die Anfrage einen nennt; sonst verwendet der Dienst sein eigenes Standardmodell. Bei eingeschaltetem `CLINIC_ASR_PROMPTS_ENABLED` sendet diese Anbindung Initial-Prompt und Hotwords als getrennte Parameter `initial_prompt` und `hotwords`.
- `ASR_ENABLE_CHUNKING` (Vorgabe `false`) und `ASR_MAX_DURATION_SECONDS` (Vorgabe `7200`) schalten das Aufteilen durch die Anwendung ein.
- **Diarisierung.** Der Vertragswert `asr.diarization: false` in `clinic-spec/config/dev.defaults.json` wird vom Code nicht gelesen. Mit dieser Anbindung folgt die Diarisierung `ASR_DIARIZE` (Vorgabe `true`) sowie `ASR_MIN_SPEAKERS` und `ASR_MAX_SPEAKERS`. Setzen Sie `ASR_DIARIZE=false`, wenn der Dienst nicht diarisieren soll.
- **Sprecher-Embeddings (nur Upstream).** Im Klinikmodus werden Stimm-Embeddings diarisierter Sprecher nicht an der Aufnahme gespeichert, und die Upstream-Routen der Sprecheridentifikation sind gesperrt. `ASR_RETURN_SPEAKER_EMBEDDINGS` ergibt deshalb keine Stimmprofile.
- Die GPU-Ausweichlösung umhüllt auch diese Anbindung: zuerst GPU, dann `asr_endpoint` als CPU-Ziel.

Die Upstream-Einrichtung des WhisperX-Dienstes (Hugging-Face-Token, Zustimmung zu den pyannote-Modellen, NVIDIA-GPU-Abstimmung, Stimmprofile) gilt nur für Upstream. Sie ist nicht Teil der Klinikversion und wurde nicht für den klinischen Einsatz geprüft.

## Synthetische Testanbindung

**Status: Simuliert.** `TRANSCRIPTION_CONNECTOR=clinic_stub` wählt einen synthetischen Offline-Testersatz. Er öffnet keine Netzwerkverbindung und erkennt keine Sprache. Er nimmt nur PCM-WAV an und liefert vier feste Sätze («Synthetischer Diktattext eins.» … «vier.»), gekennzeichnet mit `synthetic` und `not_asr_quality_proof`. Mit `CLINIC_PROFILE=production` verweigert er den Betrieb, und die GPU-Ausweichlösung umhüllt ihn nie. Verwenden Sie ihn nie für echte Diktate.

## Modellwahl pro Aufgabe (in Prüfung)

**Status: In Prüfung. Nicht Teil der aktuell freigegebenen Version; Bezeichnungen und Verfügbarkeit können sich ändern.**

Die Seite **Modellwahl** unter **Verwaltung** › **System** (in Prüfung) speichert Modellkennungen pro Aufgabe. Für die Spracherkennung hat ihr Bereich **Transkription** zwei Felder:

- **GPU-Modell** für das GPU-Ziel der Ausweichlösung und **CPU-Modell** für die konfigurierte (CPU-)Anbindung.
- Ein gespeichertes **CPU-Modell** ersetzt das Modell der Anfrage nur, solange die Adresse des CPU-Ziels lokal ist.
- Ein gespeichertes **GPU-Modell** geht `CLINIC_GPU_ASR_MODEL` und dem vom GPU-Dienst gemeldeten Modell vor, aber nur, solange die GPU-Adresse lokal ist. Es legt nur den Modellnamen der Anfrage fest; auf dem Dienst lädt oder wechselt es kein Modell.

Die Seite legt zudem das Textmodell der Zusammenfassung fest (**Zusammenfassung** › **Textmodell**). Die vollständige Beschreibung mit Katalogen, den Statuszeilen **Wirksam** und **Derzeit inaktiv**, Zugriff und Auditierung steht in der [Modellkonfiguration](model-configuration.md).

## Umgebungsvariablen von Speakr

| Variable | Vorgabe | Bedeutung |
|---|---|---|
| `CLINIC_MODE` | aus | Schaltet alle ASR-Regeln dieser Seite ein |
| `TRANSCRIPTION_CONNECTOR` | automatische Erkennung | `openai_whisper`, `asr_endpoint` oder `clinic_stub` |
| `TRANSCRIPTION_BASE_URL` | nicht gesetzt | Lokale OpenAI-kompatible Spracherkennung; im Klinikmodus Pflicht und lokal |
| `TRANSCRIPTION_API_KEY` | nicht gesetzt | Darf nicht leer sein; für einen lokalen Dienst ein beliebiger Platzhalter |
| `TRANSCRIPTION_MODEL` (älter: `WHISPER_MODEL`) | `whisper-1` | CPU-Modell |
| `TRANSCRIPTION_TIMEOUT` | 3600 im Klinikmodus | Lese-Zeitlimit in Sekunden |
| `TRANSCRIPTION_MAX_RETRIES` | 0 im Klinikmodus | Wiederholungen des SDK |
| `JOB_MAX_RETRIES` | `3` | Höchstzahl der Versuche pro Auftrag bei vorübergehenden Fehlern; siehe [Systemeinstellungen](system-settings.md) |
| `TRANSCRIPTION_WORD_TIMESTAMPS` | aus | `1`, `true`, `yes`: Wortzeitstempel anfordern (nur Klinikmodus; das GPU-Ziel übernimmt den Wert) |
| `TRANSCRIPTION_MODELS_AVAILABLE` | leer | Erlaubte Modelle; siehe [Modellkonfiguration](model-configuration.md) |
| `CLINIC_ASR_PROMPTS_ENABLED` | aus | Lässt Initial-Prompt und Hotwords durch, Grenze 55 Wörter |
| `CLINIC_GPU_ASR_BASE_URL` | nicht gesetzt | Schaltet «zuerst GPU, dann CPU» ein; muss lokal sein |
| `CLINIC_GPU_ASR_TIMEOUT` | 300 | Zeitlimit der GPU-Anfrage in Sekunden |
| `CLINIC_GPU_ASR_API_KEY` | CPU-Schlüssel, sonst `local` | Schlüssel für das GPU-Ziel |
| `CLINIC_GPU_ASR_MODEL` | nicht gesetzt | Fest vorgegebener GPU-Modellname |
| `ASR_BASE_URL` | nicht gesetzt | Adresse für `asr_endpoint`; nicht auf Lokalität geprüft |
| `ASR_API_KEY` | leer | Schlüssel für `asr_endpoint` |
| `ASR_TIMEOUT` | nicht gesetzt (Einstellung: 1800) | Zeitlimit von `asr_endpoint`; geht der Systemeinstellung vor |
| `ASR_ASYNC_JOBS` | `true` | Auftragsschnittstelle von WhisperX, wenn gemeldet |
| `ASR_DIARIZE` | `true` | Diarisierungsanfrage von `asr_endpoint` |
| `ASR_MIN_SPEAKERS` / `ASR_MAX_SPEAKERS` | nicht gesetzt | Hinweise zur Sprecherzahl für `asr_endpoint` |
| `ASR_RETURN_SPEAKER_EMBEDDINGS` | `false` | Nur Upstream; Embeddings werden im Klinikmodus nicht gespeichert |
| `ASR_ENABLE_CHUNKING` / `ASR_MAX_DURATION_SECONDS` | `false` / `7200` | Aufteilen durch die Anwendung für `asr_endpoint` |
| `USE_ASR_ENDPOINT` | `false` | Veraltet; stattdessen `ASR_BASE_URL` |
| `CLINIC_SPEAKER_SERVICE_URL` | nicht gesetzt | Lokaler Sprecherdienst |
| `CLINIC_PROFILE` | nicht gesetzt | `production` lässt `clinic_stub` den Betrieb verweigern |
| `CLINIC_LIVE_ASR_BASE_URL` | nicht gesetzt | Live-ASR-Adapter; nicht angebunden (vorbereitet, deaktiviert) |

## Fehlerbehebung

| Symptom | Ursache und Abhilfe |
|---|---|
| `CLINIC_MODE is active: transcription base URL must point to a local or private service ...` | `TRANSCRIPTION_BASE_URL` oder `CLINIC_GPU_ASR_BASE_URL` ist leer oder öffentlich. Verwenden Sie eine lokale oder private Adresse. |
| `Unknown connector: <name>. Available: [...]` | `TRANSCRIPTION_CONNECTOR` nennt eine Anbindung, die die Klinikversion entfernt hat. |
| `TRANSCRIPTION_TIMEOUT=... is invalid` | Verwenden Sie eine positive Zahl von Sekunden (`TRANSCRIPTION_MAX_RETRIES`: eine Ganzzahl >= 0). |
| Viele Warnungen `ASR fallback: GPU target ... not used` | Der GPU-Dienst ist ausgefallen oder nicht bereit. Prüfen Sie `/healthz` und das Protokoll des GPU-Containers. |
| Aufträge scheitern auf der GPU mit `model_not_found` | `CLINIC_GPU_ASR_MODEL` oder die Anfrage nennt ein Modell, das der GPU-Dienst nicht bereitstellt. Bei 4xx-Antworten gibt es keine Ausweichung. |
| Hotwords scheinen nicht zu wirken | Erwartet: Hinweise sind standardmässig aus (`CLINIC_ASR_PROMPTS_ENABLED` und `ASR_PROMPTS_ENABLED` auf dem GPU-Dienst). |
| **Verwendete Erkennung: Nicht ermittelt** | In der aktuellen Version erwartet; siehe den Abschnitt «Status und Zustand». |
| `.ds2`- / `.dss`-Aufnahmen enden als fehlgeschlagen | Ausserhalb von Docker `CLINIC_DSS_CODEC_BIN` auf das Programm `dss-decode` setzen; das Docker-Image setzt `/usr/local/bin/dss-decode`. |

## Upstream-Referenz

Die ursprüngliche WhisperX-Anleitung (Dienst-Repository, Hugging-Face-Token, pyannote-Zustimmungen, GPU-Abstimmung, Stimmprofile) ist keine Anleitung für die Klinikversion. Dokumentation von Upstream-Speakr: [WhisperX Setup (fixiertes Original)](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/whisperx-setup.md).
