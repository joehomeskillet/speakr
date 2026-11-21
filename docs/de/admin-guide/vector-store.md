# Vektorspeicher
<small>[English](../../admin-guide/vector-store.md)</small>

Status in der Klinikversion: **Nur Upstream**.

Im Upstream-Speakr verwaltet der Administrationsreiter **Vektorspeicher** den Inquire-Modus. Inquire zerlegt jedes Transkript in Textabschnitte (Chunks), berechnet dafür Einbettungen (Embeddings) und speichert sie für die semantische Suche auf der Seite «Ambrose fragen». Die Klinikversion bietet diese Funktion nicht an. Bei eingeschaltetem `CLINIC_MODE` wird der Reiter nicht angezeigt, die Routenrichtlinie der Klinik sperrt die Inquire-Routen ausser der Indexierungsroute pro Aufnahme, und keine Klinikseite verwendet eine semantische Suche.

Transkripte werden nicht automatisch in Abschnitte zerlegt oder eingebettet, solange `ENABLE_INQUIRE_MODE` ausgeschaltet bleibt. Die im Abschnitt «Inquire-Indexierung ausgeschaltet lassen» genannten manuellen Upstream-Werkzeuge und -Routen prüfen diesen Schalter nicht; sie dürfen deshalb nicht verwendet werden. Das beruht auf der Konfiguration, nicht auf einer Prüfung im Code. Für das Auffinden von Diktaten verwendet die Klinikversion strukturierte Listen mit Metadatenfiltern (Abschnitt «Diktate in der Klinikversion finden»).

## Statusübersicht

| Bestandteil | Status | Verhalten bei eingeschaltetem `CLINIC_MODE` |
|---|---|---|
| Administrationsreiter **Vektorspeicher** (englische Oberfläche: **Vector store**) | Nur Upstream | Wird nicht angezeigt. Die Klinikoberfläche **Verwaltung** unter `/admin` hat dafür keine Seite. |
| Seite «Ambrose fragen» (`/inquire`, Alias `/ask`) und die Kopfzeilenschaltfläche **Ambrose fragen** | Nur Upstream | Die Kopfzeile der Klinik hat keinen Einstieg. Die Route wird abgewiesen, auch mit einem lokalen Textmodell. |
| Inquire-API: `/api/inquire/sessions`, `/api/inquire/search`, `/api/inquire/chat`, `/api/inquire/download`, `/api/inquire/available_filters`, `/api/v1/inquire` | Nur Upstream | Durch die Routenrichtlinie der Klinik gesperrt (Funktion `inquire`). |
| Administrationsrouten `/admin/inquire/status` und `/admin/inquire/process-recordings` (Upstream-Schaltfläche **Alle Aufnahmen neu einbetten**) | Nur Upstream | Durch die Routenrichtlinie der Klinik gesperrt (Funktion `inquire`). Siehe die Warnung unten. |
| Administrationsroute `/api/admin/migrate_recordings` | Nur Upstream | Gesperrt (Funktion `generic_admin_content_access`, die die Klinikvorgaben nicht freigeben). |
| Indexierungsroute pro Aufnahme `POST /api/recording/<recording_id>/process_chunks` | Nur Upstream | Nicht unter `inquire` gesperrt. Die Routenrichtlinie behandelt den Aufruf als Bearbeitung der genannten Aufnahme, und die Route nimmt nur Anfragen der Eigentümerin oder des Eigentümers der Aufnahme an (bei einem Klinikdiktat nur mit gültiger Bearbeitungssperre). Sie zerlegt und bettet diese Aufnahme ein, ohne `ENABLE_INQUIRE_MODE`, den Klinikmodus oder die Lokalität von `EMBEDDING_BASE_URL` zu prüfen. Der Aufruf wird nicht protokolliert. Keine Klinikseite ruft sie auf. |
| Automatisches Zerlegen und Einbetten von Transkripten (Auftragstyp `index_chunks`, Nachindexierung beim Start) | Nur Upstream | Durch `ENABLE_INQUIRE_MODE=false` (Standard) ausgeschaltet. Mit `true` läuft es ohne Klinikprüfung. Ausgeschaltet lassen. Die manuellen Wege `scripts/migrate_existing_recordings.py`, `/admin/inquire/process-recordings` und `POST /api/recording/<recording_id>/process_chunks` zerlegen Transkripte unabhängig von diesem Schalter. |
| Suche im Transkripttext in Listen | Nur Upstream | Keine Klinikseite durchsucht den Transkripttext. Die Upstream-Endpunkte `GET /api/recordings?q=` und `GET /api/v1/search` bleiben für angemeldete Konten erreichbar. Sie durchsuchen den Transkripttext der Aufnahmen, auf die die aufrufende Person Zugriff hat (`/api/recordings`), beziehungsweise ihrer eigenen Aufnahmen (`/api/v1/search`). Keine Klinikseite ruft sie auf. |

## Wie der Klinikmodus Inquire sperrt

Der Klinikmodus ist eingeschaltet, wenn `CLINIC_MODE` den Wert `1`, `true`, `yes` oder `on` hat. Dann gelten drei voneinander unabhängige Ebenen:

1. **Administrationsseite.** `/admin` zeigt anstelle der Upstream-Reiterleiste die Klinikoberfläche **Verwaltung**. Der Reiter Vektorspeicher und sein Bereich werden nicht ausgegeben, und der Inquire-Zustand der Seite ist fest ausgeschaltet. Ein Kliniktest in `tests/clinic/` prüft, dass das Markup des Reiters im Klinikmodus fehlt und ohne Klinikmodus vorhanden ist.
2. **Routenprüfung.** Die Routenrichtlinie der Klinik sperrt die Seite «Ambrose fragen», die Inquire-API und die beiden Inquire-Administrationsrouten unter der Funktion `inquire` (`403 clinic_feature_disabled`). Eine Ausnahme ist die Indexierungsroute pro Aufnahme, siehe «Inquire-Indexierung ausgeschaltet lassen». Die Sperre fällt nur weg, wenn jeder Textmodell-Endpunkt lokal ist, und auch dann erhält eine Anfrage, die keine Aufnahme nennt, etwa die Seite «Ambrose fragen» oder die Administrationsroute für den Status, `403 clinic_feature_unsupported`. Eine Anfrage, die Aufnahmen nennt, passiert die Prüfung nur, wenn die aufrufende Person jede davon nach der zentralen Richtlinie bearbeiten darf. Die Sperrregeln beschreibt [Ambrose-Einstellungen](ambrose.md), was als lokal gilt, [Modellkonfiguration](model-configuration.md).
3. **Upstream-Schalter.** Solange nicht `ENABLE_INQUIRE_MODE=true` gesetzt ist (Standard `false`), leitet die Seite «Ambrose fragen» mit einem Warnhinweis auf die Aufnahmeliste um, und die Inquire-API-Endpunkte antworten mit `403` (`inquire_disabled`, bei `/api/v1/inquire` `feature_disabled`). Die Administrationsrouten `/admin/inquire/status` und `/admin/inquire/process-recordings` prüfen diesen Schalter nicht.

## Inquire-Indexierung ausgeschaltet lassen

!!! warning "Die Inquire-Indexierung hat keine Klinikprüfung"
    Lassen Sie `ENABLE_INQUIRE_MODE` ungesetzt oder auf `false`. Die Klinikvorlage [`clinic-spec/config/upstream.env.example`](https://github.com/joehomeskillet/speakr/blob/main/clinic-spec/config/upstream.env.example) (Klinik-Fork) setzt `ENABLE_INQUIRE_MODE=false`. Mit `true` läuft die Upstream-Indexierung auch im Klinikmodus:

    - Wenn die Verarbeitung einer Aufnahme abgeschlossen ist, wird für sie ein Auftrag vom Typ `index_chunks` eingereiht. Das betrifft auch die Aufnahmen hinter Klinikdiktaten, die ebenfalls den Transkripttext tragen.
    - Bei jedem Start zerlegt und bettet der Migrationsschritt «inquire mode chunk backfill» bis zu 10 abgeschlossene Aufnahmen ein, die noch keine Abschnitte haben.
    - Nach einer Transkriptänderung oder einer Sprecherzuordnung über die Upstream-Aufnahmerouten wird ebenfalls ein Auftrag `index_chunks` eingereiht. Das Umbenennen oder Zusammenführen von Sprecherprofilen tut dasselbe, deren Routen sind im Klinikmodus aber unter der Funktion `speaker_identification` gesperrt.

    Keiner dieser Wege prüft den Klinikmodus, die Funktionssperre `inquire` oder ob der Einbettungsendpunkt lokal ist. Die Abschnitte werden als Text in der Tabelle `transcript_chunk` gespeichert. Vektoren entstehen nur, sofern ein Einbettungsdienst verfügbar ist. Ist `EMBEDDING_BASE_URL` gesetzt, geht der Abschnittstext ohne Lokalitätsprüfung an diesen Endpunkt. Andernfalls wird ein lokales sentence-transformers-Modell (`EMBEDDING_MODEL`, Standard `all-MiniLM-L6-v2`) verwendet, sofern das Paket installiert ist: Das Standard-Image (`LIGHTWEIGHT=0`) installiert es aus `requirements-embeddings.lock`, `requirements.lock` (`LIGHTWEIGHT=1`) enthält es nicht, und die Abschnitte werden dann ohne Vektoren gespeichert. Ein nicht lokaler Wert in `EMBEDDING_BASE_URL` sperrt im Klinikmodus Chat, Inquire und generative Zusammenfassungen, hält diese Indexierung aber nicht auf.

Prüfpunkte für die Administration:

- Beim Start gibt die Anwendung `Inquire Mode: Disabled (set ENABLE_INQUIRE_MODE=true to enable)` aus. Jede andere Zeile «Inquire Mode» bedeutet, dass der Schalter eingeschaltet ist.
- Führen Sie weder `scripts/migrate_existing_recordings.py` noch `scripts/migrate_docker.sh` aus, das dieses Skript aufruft. Das Skript zerlegt und bettet abgeschlossene Aufnahmen ein und prüft weder `ENABLE_INQUIRE_MODE` noch den Klinikmodus.
- Senden Sie keine Anfragen an `/admin/inquire/process-recordings`. Mit einem vollständig lokalen Textmodell kann die Anfrage eines Kontos mit **Administrationsrolle** (globale Administration) die Prüfung passieren, wenn sie Aufnahmen nennt, die dieses Konto bearbeiten darf. Die Route ignoriert dann die genannten IDs, wählt abgeschlossene Aufnahmen nach eigenen Regeln aus und zerlegt sie, unabhängig davon, ob `ENABLE_INQUIRE_MODE` eingeschaltet ist.
- Senden Sie keine Anfragen an `POST /api/recording/<recording_id>/process_chunks`. Jedes Konto kann die Route für seine eigenen Aufnahmen aufrufen, auch mit `ENABLE_INQUIRE_MODE=false`; bei einem Klinikdiktat verlangt die Prüfung eine gültige Bearbeitungssperre. Der Aufruf wird nicht protokolliert. Ist `EMBEDDING_BASE_URL` gesetzt, geht der Abschnittstext an diesen Endpunkt, ob er lokal ist oder nicht.
- Nehmen Sie `EMBEDDING_MODEL` und `EMBEDDING_BASE_URL` nicht in die Klinikkonfiguration auf. Sie steuern nur die Upstream-Einbettungen für Inquire. Ausserhalb davon lesen nur die Lokalitätsprüfung der Klinik und die Startwarnung zu Diensten, die über unverschlüsseltes HTTP erreicht werden, `EMBEDDING_BASE_URL`. Ohne diesen Wert geht kein Abschnittstext an einen Einbettungsendpunkt. Das Standard-Image (`LIGHTWEIGHT=0`) enthält trotzdem sentence-transformers, ein dennoch ausgelöster Indexierungsweg speichert also lokale Vektoren.

Wenn `/admin/inquire/process-recordings` oder `/api/admin/migrate_recordings` mindestens eine zu verarbeitende Aufnahme findet, schreibt die Route am Ende des Laufs `admin.inquire.processed` (Details `processed`, `failed`, `total`, `force`). Ein Aufruf ohne zu verarbeitende Aufnahmen oder einer, der vor dem Ende des Laufs mit einem Fehler abbricht, schreibt kein Ereignis. `POST /api/recording/<recording_id>/process_chunks` wird nicht protokolliert. Die Klinikoberfläche bietet keine Möglichkeit, diese Routen auszulösen. Siehe [Auditprotokoll](audit-log.md).

## Diktate in der Klinikversion finden

Die Klinikversion findet Diktate über Listen mit strukturierten Filtern auf Metadaten. Jede Liste zeigt nur die Diktate, die das angemeldete Konto sehen darf. Die Sichtbarkeitsregeln beschreiben [Benutzerverwaltung](user-management.md) und [Gruppenverwaltung](group-management.md).

| Ansicht | Ort | Filter | Suche |
|---|---|---|---|
| **Arbeitsliste** | `/#/arbeitsliste` | **Gespeicherte Filter**: höchstens 20 pro Benutzer, je höchstens 20 Bedingungen, auf Status, Priorität, Autor, Pool, Titel, Erstellungsdatum, Frist, zugewiesene Person und Audiodauer. Reguläre Ausdrücke sind nicht zulässig. Die Filter werden pro Benutzer in der Datenbank gespeichert. | **Metadatensuche**, höchstens 200 Zeichen, auf dem Server ausgewertet. Jedes Wort muss im Titel, in den Notizen oder beim Empfänger eines Diktats vorkommen. |
| **Aufnahmen** | `/#/aufnahmen` | Auf dem Server ausgewertet: **Diktiert von**, **Schreibkräfte-Pool**, **Status der Aufnahme** und **Zeitraum der Aufnahme**. | **Aufnahmen durchsuchen**, Platzhalter «Person, Pool oder Fall-ID suchen …». Die Suche läuft im Browser über die bereits geladenen Zeilen und findet Autor, Pool, Fall-ID oder Diktat-ID. |
| **Archiv** | `/#/archiv` | Nur Diktate im Workflowstatus **Zu archivieren**, gefiltert nach **Von**, **Bis**, **Diktiert von** und **Schreibkräfte-Pool**. | Keine. Die Ansicht ist schreibgeschützt; siehe [Aufbewahrung](retention.md). |

Keine dieser Suchen liest den Transkripttext. In einem geöffneten Diktat bietet der Korrektureditor **Suchen und Ersetzen** nur innerhalb des aktuellen Texts an.

## Upstream-Referenz

Upstream-Speakr-Dokumentation (fixiertes Original, nicht auf die Klinikversion anwendbar): [Vector Store Management](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/vector-store.md). Sie beschreibt die Statuskarten, das Einbettungsmodell, **Re-embed all** und die Erfassung der Einbettungstoken. Nichts davon gilt bei eingeschaltetem `CLINIC_MODE`.

## Verwandte Seiten

- [Ambrose-Einstellungen](ambrose.md): Status des Upstream-Assistenten und des Chats in der Klinikversion.
- [Modellkonfiguration](model-configuration.md): lokale Textmodell-Endpunkte und `CLINIC_LOCAL_MODEL_HOSTS`.
- [Auditprotokoll](audit-log.md): Ereigniskatalog, einschliesslich `admin.inquire.processed`.
- [Aufbewahrung](retention.md): Archivierung und die Ansicht **Archiv**.

---

Zurück zum [Administrationshandbuch](index.md)
