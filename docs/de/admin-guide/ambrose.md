# Ambrose-Einstellungen
<small>[English](../../admin-guide/ambrose.md)</small>

Im Upstream-Speakr ist Ambrose der KI-Assistent: der Aufnahme-Chat, die Seite «Ambrose fragen» und ein optionaler Agent, der die eigene Bibliothek durchsucht. In der Klinikversion hat Ambrose den Status **Nur Upstream**. Der Code ist weiterhin Teil der Klinikversion. Bei eingeschaltetem `CLINIC_MODE` zeigt keine Seite Ambrose an, und seine Routen sind gesperrt, solange nicht jeder gesetzte Textmodell-Endpunkt lokal ist (siehe [So wirkt die Sperre](#so-wirkt-die-sperre)). Keine Seite der **Verwaltung** und keine Systemeinstellung schaltet Ambrose ein. Das gilt für jede Rolle: Weder die globale Administration (**Administrationsrolle**) noch die **Identitätsadministration** noch Poolleitungen (**Leitung**) haben darauf Einfluss.

## Status in der Klinikversion

| Upstream-Bestandteil | Status | Verhalten bei eingeschaltetem `CLINIC_MODE` |
|---|---|---|
| Schaltfläche **Ambrose fragen** in der Kopfzeile | Nur Upstream | Wird nicht angezeigt. Die Klinik-Kopfzeile zeigt stattdessen das Menü **Verwaltung & Seiten** und die Schaltfläche **Neue Aufnahme**. |
| Aufnahme-Chat | Nur Upstream | Das Chat-Feld gehört zur Upstream-Detailansicht, die der Klinikmodus nicht darstellt. Die Chat-Routen sind unter der Funktionssperre `chat` gesperrt, solange ein Modell-Endpunkt nicht lokal ist. |
| Seite «Ambrose fragen» (`/inquire`, `/ask`) und Agent | Nur Upstream | Kein Link in der Klinikoberfläche. Die Routen sind unter der Funktionssperre `inquire` gesperrt, solange ein Modell-Endpunkt nicht lokal ist. |
| Verwaltungsreiter **Vektorspeicher** und Inquire-Verwaltungsrouten | Nur Upstream | Der Reiter wird nicht angezeigt. `admin.admin_inquire_status` und `admin.admin_process_recordings_for_inquire` sind unter `inquire` gesperrt. Siehe [Vektorspeicher](vector-store.md). |
| Kontoreiter **Prompt-Optionen** mit **Was Ambrose lesen darf** | Nur Upstream | Wird nicht angezeigt. |
| Kontofeld **Name des Assistenten** (Reiter **Einstellungen**, Abschnitt **Assistent**) | Nur Upstream (sichtbar, ohne Wirkung) | Wird auf `/account` weiterhin angezeigt. Nur der Chat und die Seite «Ambrose fragen» verwenden es, und die Klinikoberfläche zeigt keines von beiden; in der Klinikoberfläche ändert es daher nichts. |
| Inkognito-Aufnahmen und ihr Chat | Nur Upstream | Aus dem Fork entfernt. `ENABLE_INCOGNITO_MODE` wird nicht gelesen. |

## Warum Ambrose nicht Teil der Klinikversion ist

- **Patientendaten.** Klinische Transkripte enthalten Patientendaten. Der Upstream-Aufnahme-Chat übergibt bei jeder Frage das Transkript an das Sprachmodell, bis zur Systemeinstellung `transcript_length_limit` (Standard 30 000 Zeichen). Die Seite «Ambrose fragen» und die semantische Suche arbeiten mit einem Index aus Transkriptabschnitten und deren Vektoreinbettungen.
- **Keine generative klinische Umschreibung.** Der verbindliche Auftrag (`clinic-spec/CLAUDE.md`) schliesst Cloud-ASR und generative klinische Umschreibung aus. Das Entscheidungsregister `clinic-spec/config/decisions.json`, das als Entwicklungsvorgabe und nicht als klinische Freigabe gekennzeichnet ist, führt D21: «No LLM rewrite/chat/summaries/voiceprints/RAG/public share/personal webhooks». Der Code weicht davon für ein lokales Textmodell ab: `chat`, `inquire` und `generative_summary` sind entsperrt, wenn jeder Textmodell-Endpunkt lokal ist (siehe [So wirkt die Sperre](#so-wirkt-die-sperre)). Die KI-Zusammenfassung in Prüfung baut darauf auf.
- **Persönliche Upstream-Funktion.** Ambrose ist ein Assistent pro Benutzerkonto, mit eigenem Namen und eigenen Leseschaltern pro Konto, und beantwortet Fragen über alle Aufnahmen, die das Konto öffnen darf. Die klinische Arbeit läuft über Diktate in der **Arbeitsliste** und im **Schreibplatz**. Über den Zugriff entscheiden pro Diktat Pools und **Fachrechte**, siehe [Benutzerverwaltung](user-management.md).

## So wirkt die Sperre

Im Klinikmodus ordnet ein Routenwächter jeden Endpunkt einer Klasse zu (Quelle: [Routenrichtlinie, Klinik-Fork](https://github.com/joehomeskillet/speakr/blob/main/src/clinic/route_policy.py)). Die Ambrose-Routen liegen in zwei gesperrten Gruppen:

| Funktionssperre | Endpunkte |
|---|---|
| `chat` | `recordings.chat_with_transcription` (`POST /chat`), `recordings.download_chat_word`, `api_v1.chat_with_recording` |
| `inquire` | jeder Endpunkt `inquire.*` (Seite «Ambrose fragen», Sitzungen, Suche, Chat, Download, Filter), `api_v1.inquire_v1`, `admin.admin_inquire_status`, `admin.admin_process_recordings_for_inquire` |

Anders als andere Klinikfunktionen richten sich `chat` und `inquire` nicht nach ihrem Schalter in `dev.defaults.json` unter `clinic-spec/config/` (oder im Verzeichnis aus `CLINIC_CONFIG_DIR`). Zusammen mit `generative_summary` bleiben sie gesperrt, solange nicht jeder Textmodell-Endpunkt lokal ist:

- `TEXT_MODEL_BASE_URL` ist gesetzt und lokal.
- `CHAT_MODEL_BASE_URL`, `TRANSLATION_MODEL_BASE_URL` und `EMBEDDING_BASE_URL` sind lokal, sofern sie gesetzt sind. Eine einzige nicht lokale URL sperrt alle drei Funktionen.
- Lokal heisst: eine Loopback- oder private Adresse (`127.0.0.0/8`, `::1`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `fc00::/7`) oder ein Hostname aus `CLINIC_LOCAL_MODEL_HOSTS` (Standard `speaches,host.docker.internal,localhost`). Jeder andere Hostname gilt nicht als lokal, auch ein einteiliger Name ohne Domain. Ein Wert in `CLINIC_LOCAL_MODEL_HOSTS` ersetzt die Standardliste und ergänzt sie nicht. Ein leerer Wert lässt nur Loopback- und private Adressen als lokal gelten. Eine URL mit Benutzerangaben, umgekehrtem Schrägstrich, Leerraum oder Steuerzeichen oder mit einem anderen Schema als `http` oder `https` gilt nie als lokal; eine URL ohne Schema ist zulässig. Text nach `#` wird ignoriert.

Solange eine Funktion gesperrt ist, antworten ihre Routen mit `403` und dem Code `clinic_feature_disabled`. Der Routenwächter schreibt für diese Ablehnungen weder eine Logzeile noch einen Auditeintrag.

Sind alle gesetzten Modell-Endpunkte lokal, fällt die Sperre weg, aber die Klinikoberfläche hat weiterhin kein Chat-Feld, keine Seite «Ambrose fragen» und keine Schaltfläche in der Kopfzeile. Eine direkte Anfrage wird dann so behandelt:

- Eine Chat-Anfrage entscheidet die zentrale Richtlinie pro Aufnahme, wie eine Bearbeitung dieser Aufnahme. Ohne Bearbeitungsrecht folgt `403 forbidden` oder `404 not_found`. Bei einem Klinikdiktat verlangt der Routenwächter zusätzlich eine gültige Bearbeitungssperre (`lease_generation`), sonst folgt `400 invalid_lease_generation` oder `409`.
- Eine Anfrage, die keine Aufnahme nennt, etwa die Seite «Ambrose fragen» selbst (`GET /inquire` oder `/ask`), wird mit `403 clinic_feature_unsupported` abgelehnt.
- Die Inquire-Handler lehnen zusätzlich ab, solange nicht `ENABLE_INQUIRE_MODE=true` gesetzt ist: die JSON-Routen unter `/api/inquire/` mit `403 inquire_disabled`, `api_v1.inquire_v1` mit `403 feature_disabled`.
- Bei `POST /chat` wird der Tokenverbrauch unter dem Vorgangstyp `chat` erfasst und auf das **Tokenlimit pro Monat** des Kontos angerechnet. `api_v1.chat_with_recording` übergibt keinen Vorgangstyp; dieser Weg prüft das Limit daher nicht und erfasst seinen Tokenverbrauch nicht.

## Upstream-Einstellungen ohne Klinikfunktion

Der Code liest diese Variablen weiterhin, ausser `ENABLE_INCOGNITO_MODE`. Es sind reine Upstream-Einstellungen und keine Anleitung für die Klinikversion: Richten Sie damit in der Klinikversion keinen Assistenten ein.

| Variable | Zweck im Upstream | Klinikversion |
|---|---|---|
| `ENABLE_INQUIRE_MODE` | Seite «Ambrose fragen», Schaltfläche in der Kopfzeile und Suchindex | Auf `false` lassen (Standard im Code, auch in `clinic-spec/config/upstream.env.example` gesetzt). Siehe Warnung unten. |
| `ENABLE_INQUIRE_AGENT`, `INQUIRE_AGENT_TOOL_MODE`, `INQUIRE_AGENT_MAX_STEPS`, `INQUIRE_AGENT_TOOL_RESULT_TOKENS`, `INQUIRE_AGENT_TIMEOUT_SECONDS`, `INQUIRE_CONTEXT_BUDGET_TOKENS` | Agent und seine Grenzen | Keine Klinikoberfläche. |
| `INQUIRE_DEFAULT_ALLOW_SUMMARIES`, `INQUIRE_DEFAULT_ALLOW_NOTES` | Vorgaben für **Was Ambrose lesen darf** | Der Kontoreiter wird nicht angezeigt. |
| `CHAT_MODEL_*`, `CHAT_MAX_TOKENS` | Eigenes Chat-Modell und Antwortlänge | Keine Klinikoberfläche. Eine nicht lokale `CHAT_MODEL_BASE_URL` sperrt Chat, Inquire und die generative Zusammenfassung. |
| `CHUNK_TARGET_CHARS`, `CHUNKING_STRATEGY`, `EMBEDDING_MODEL`, `EMBEDDING_BASE_URL` | Suchindex | Siehe [Vektorspeicher](vector-store.md). Eine nicht lokale `EMBEDDING_BASE_URL` sperrt Chat, Inquire und die generative Zusammenfassung. |
| `ENABLE_INCOGNITO_MODE` | Inkognito-Aufnahmen | Aus dem Fork entfernt, wird nicht gelesen. |

!!! warning "`ENABLE_INQUIRE_MODE=true` indexiert Transkripte ohne Klinikprüfung"
    Mit `ENABLE_INQUIRE_MODE=true` erhält jede Aufnahme, deren Verarbeitung abgeschlossen ist, einen Auftrag `index_chunks`, der das Transkript in Abschnitte zerlegt und diese einbettet. Eine Änderung des Transkripts oder der Sprechernamen über Upstream-Routen reiht den Neuaufbau erneut ein. Beim Start zerlegt die Anwendung zudem bis zu 10 abgeschlossene Aufnahmen, die noch keine Abschnitte haben. Keiner dieser Wege prüft den Klinikmodus, die Sperre `inquire` oder ob `EMBEDDING_BASE_URL` lokal ist. Die Einbettungen berechnet die Anwendung selbst mit sentence-transformers, ausser `EMBEDDING_BASE_URL` ist gesetzt; dann gehen die Abschnitte an diesen Endpunkt. Lassen Sie `ENABLE_INQUIRE_MODE=false`.

## Was die Klinikversion stattdessen bietet

Die Klinikversion hat keinen Assistenten, der Fragen zu Diktaten beantwortet.

- **Diktate finden.** Die **Arbeitsliste** bietet eine **Metadatensuche** und **Gespeicherte Filter** auf Feldern wie Status, Priorität, diktierende Person, Pool, Titel, Erstellungsdatum und Frist.
- **Mit dem Text arbeiten.** Der Text wird im **Schreibplatz** von Hand korrigiert.

### Titel- und Zusammenfassungsschritt in der freigegebenen Version

Die freigegebene Version führt nach jeder Transkription weiterhin den Upstream-Schritt für Titel und Zusammenfassung aus. Im Klinikmodus ist dieser Schritt ein Folgeauftrag in der Warteschlange für Zusammenfassungen. Ohne konfiguriertes Textmodell (weder `TEXT_MODEL_BASE_URL` noch `TEXT_MODEL_API_KEY` gesetzt) entfallen beide Teile. Sonst kann der Titelteil das Textmodell nach einem Titel fragen, und der Zusammenfassungsteil sendet das Transkript an das Textmodell, ausser die Systemeinstellung `disable_auto_summarization` ist `true` oder die Kontoeinstellung der diktierenden Person für automatische Zusammenfassungen ist ausgeschaltet. Ist nur `TEXT_MODEL_API_KEY` gesetzt, verwendet der Client den Standardwert im Code, `https://openrouter.ai/api/v1`, einen gehosteten Dienst. Dieser Schritt wendet die Regel für lokale URLs aus [So wirkt die Sperre](#so-wirkt-die-sperre) nicht an. Die Zusammenfassung wird bei der Aufnahme gespeichert, aber die Klinikoberfläche zeigt sie nicht an. Die globale Administration (**Administrationsrolle**) setzt `disable_auto_summarization` auf der Seite **Systemeinstellungen** in der **Verwaltung**. Die Kontoeinstellung für automatische Zusammenfassungen ist standardmässig eingeschaltet; die Klinikoberfläche hat dafür keinen Schalter, weil der Kontoreiter **Prompt-Optionen** nicht angezeigt wird. Siehe [Modellkonfiguration](model-configuration.md).

### In Prüfung: KI-Zusammenfassung

!!! note "In Prüfung"
    Diese Funktion ist in Prüfung. Sie ist nicht Teil der aktuell freigegebenen Version; Beschriftungen und Verfügbarkeit können sich ändern.

In der aktuell freigegebenen Version zeigt die Klinikoberfläche keine KI-Zusammenfassung. Die Funktion in Prüfung ergänzt ein schreibgeschütztes Feld **KI-Zusammenfassung**. Es ist kein Assistent: Es nimmt keine Fragen entgegen und ändert den Diktattext nie.

- Das Feld erscheint im **Schreibplatz** eingeklappt und lädt seinen Inhalt beim Öffnen. In den **Diktatdetails** ist es ausgeklappt, sobald die Details geladen sind.
- Eine Zusammenfassung ist an die Textrevision gebunden, aus der sie entstand. Nach einer späteren Korrektur gilt sie als veraltet, und das Feld zeigt «Der Bezug zur aktuellen Textfassung ist nicht bestätigt.» Eine Korrektur löst keine neue Zusammenfassung aus.
- Automatische Zusammenfassung: Wird der erste Erkennungstext eines neuen Diktats gespeichert, wird ein Zusammenfassungsauftrag eingereiht, wenn alle folgenden Bedingungen gelten: Die Systemeinstellung `disable_auto_summarization` ist nicht `true`, die Kontoeinstellung der diktierenden Person für automatische Zusammenfassungen ist nicht ausgeschaltet (siehe oben), sie hat am Diktat die Rechte **Ansehen** und **Bearbeiten**, der Text hat mindestens 10 Zeichen, und ein lokales Textmodell ist bereit. Bestehende Diktate erhalten nicht automatisch eine neue Zusammenfassung. Eine Zusammenfassung, welche die freigegebene Version bei der Aufnahme gespeichert hat, wird als veraltet angezeigt.
- Für eine Aufnahme, die mit einem Diktat verknüpft ist, führt die Funktion nach der Transkription den Upstream-Zusammenfassungsschritt nicht mehr aus; der Upstream-Titelschritt läuft weiterhin.
- Manuelle Anforderung: **Zusammenfassung erstellen** oder **Zusammenfassung neu erstellen**. Die Schaltfläche erscheint nur mit dem Recht **Bearbeiten** am Diktat, wenn der aktuelle Text mindestens 10 Zeichen hat und ein lokales Textmodell bereit ist.
- Lesen erfordert das Recht **Ansehen** am Diktat. Jeder Lesezugriff wird als `recording.content.read` mit `part=summary` auditiert, jede manuelle Anforderung als `recording.reprocessed` mit `kind=summary`.
- «Lokales Textmodell bereit» heisst: Der Klinikmodus ist eingeschaltet, die Sperre `generative_summary` ist aufgehoben (die Regel für lokale URLs oben), und der Textmodell-Client zeigt auf eine lokale URL. Sonst antwortet eine manuelle Anforderung mit `503`, und das Feld zeigt «Der lokale KI-Dienst ist derzeit nicht verfügbar.» Eine gespeicherte Zusammenfassung lässt sich weiterhin lesen; die Schaltfläche wird nicht angezeigt. Einen gehosteten Ersatz gibt es nicht. Dasselbe lokale Textmodell hebt auch die Sperren `chat` und `inquire` auf: Direkte Chat-Anfragen funktionieren dann für Konten mit Bearbeitungsrecht, obwohl die Klinikoberfläche keinen Chat zeigt (siehe [So wirkt die Sperre](#so-wirkt-die-sperre)).
- Die Zusammenfassung verwendet die Auflösung des Zusammenfassungs-Prompts, die unter [Prompts](prompts.md) beschrieben ist. Ihr Modell lässt sich auf der Systemseite **Modellwahl** im Feld **Textmodell** festlegen; auch diese Seite ist in Prüfung (siehe [Modellkonfiguration](model-configuration.md)).

## Upstream-Referenz

Die Upstream-Seite behandelt das Chat-Modell, die Werkzeugmodi des Agenten, seine Grenzen, die Verbrauchserfassung und den Suchindex: [Ambrose-Einstellungen (Upstream-Speakr-Dokumentation, Englisch)](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/ambrose.md). Sie beschreibt das Upstream-Speakr, nicht die Klinikversion.
