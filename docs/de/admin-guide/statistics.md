# Systemstatistik
<small>[English](../../admin-guide/statistics.md)</small>

Die Klinikversion hat zwei Statistikansichten. Sie zählen Verschiedenes und haben unterschiedliche Zugriffsregeln. Alles auf dieser Seite gilt mit eingeschaltetem `CLINIC_MODE`. Ohne Klinikmodus zeigt `/admin` die Upstream-Registerleiste, und die klinischen Statistik- und Auftragsrouten antworten mit 404.

| Ansicht | Ort | Wer sie öffnen kann | Was gezählt wird | Status |
|---|---|---|---|---|
| **Systemstatistik** | **Verwaltung** › **System** › **Systemstatistik**, `/admin#/system/statistik` | Konten mit der **Administrationsrolle** (globale Administration) | Aufnahmezeilen der Basisanwendung, Transkriptions- und Sprachmodellnutzung, eine Zeile pro Konto | Verfügbar (Upstream-Register, unverändert übernommen) |
| **Statistik** in **Aktivität und Audit** | **Verwaltung** › **Betrieb** › **Aktivität und Audit**, `/admin#/aktivitaet` | Aktive Klinikkonten mit einer Aggregat-Statistikberechtigung | Klinische Diktate nach Status, Autor, Schreibkraft und Zeitraum | Vorbereitet, deaktiviert: Es lässt sich keine Berechtigung anlegen, daher ist die Seite für jedes Konto zurückgehalten |

Der Zustand der Verarbeitungsaufträge (Warteschlange, hängende und fehlgeschlagene Aufträge) steht auf der Seite **Verarbeitung**, siehe [Verarbeitung (Auftragszustand)](#verarbeitung-auftragszustand).

## Wer Statistiken sehen darf

- **Globale Administration** (**Administrationsrolle**, `User.is_admin`): öffnet `/admin` und damit die **Systemstatistik**. Die Routen `GET /admin/stats/overview` und `GET /admin/stats` prüfen nur dieses Merkmal. Jedes andere Konto erhält vom klinischen Routenschutz 403, bevor die Funktion ausgeführt wird.
- **Lokale Identitätsverwaltung** (**Identitätsadministration**): weder nötig noch ausreichend. Ohne Administrationsrolle besteht kein Zugang zu `/admin`.
- **Poolleitungen** (**Leitung**): kein Zugang zur **Systemstatistik**, weil `/admin` die Administrationsrolle verlangt. Eine Leitungsrolle gibt auch keine Statistikberechtigung.
- **Statistikberechtigung**: Die klinische **Statistik** verlangt eine Aggregatberechtigung mit dem Geltungsbereich Autor oder Autorenpool. Die globale Administration (Administrationsrolle) enthält keine solche Berechtigung; ein Test stellt sicher, dass ein Konto mit **Administrationsrolle** ohne Berechtigung 403 erhält. Keine Oberfläche, keine Route und kein Befehl legt eine Berechtigung an (siehe [Klinikstatistik](#klinikstatistik)).
- **Freigabe** (**Freigeben**) und **Export des Originalaudios** (**Originalaudio exportieren**): ohne Bezug zur Statistik. Eine Statistikberechtigung gibt kein Recht zum Ansehen, Abspielen, Exportieren, Freigeben oder zum Export des Originalaudios. Diese Rechte bleiben bei den Fachrechten, siehe [Benutzerverwaltung](user-management.md).

## Systemstatistik

Status: Verfügbar. Es handelt sich um das Upstream-Register «System Statistics», unverändert in die klinische Verwaltung übernommen. Die Navigationsbezeichnung **Systemstatistik** gibt es nur auf Deutsch. Das Register selbst folgt der Oberflächensprache: Die Überschrift lautet auf Deutsch «Systemstatistiken», auf Englisch «System statistics». Das Öffnen des Registers schreibt kein Auditereignis.

### Zeitraum

Wählen Sie **7 Tage**, **30 Tage** oder **90 Tage**. Voreingestellt sind 30 Tage. Die Schnittstelle akzeptiert nur 7, 30 oder 90 und verwendet für jeden anderen Wert 30. Der Zeitraum endet heute, und jede Kennzahl wird mit dem gleich langen Zeitraum davor verglichen. Tage sind Kalendertage nach der lokalen Serverzeit. Nutzungszeilen tragen dieses lokale Datum, Aufnahmezeilen werden nach ihrer UTC-Erstellungszeit zugeordnet; beides stimmt nur auf einem Server in UTC überein. Dieses Register verwendet nicht Europe/Zurich. Die Schaltfläche zum Aktualisieren lädt die Zahlen neu. Das Register hat keine weiteren Filter und keinen Export.

### Was im Klinikmodus gezählt wird

Das Register liest die Basistabellen `Recording`, `TokenUsage` und `TranscriptionUsage`. Klinische Diktate liest es nicht. Im Klinikmodus hat das Folgen:

- Jedes klinische Diktat hat eine eigene Ankerzeile in der Aufnahmetabelle, zusätzlich zur Aufnahme, die das Audio für die Transkription trägt (die Aufnahme des Web-Uploads, die Arbeitskopie eines Imports oder die vom Übernahmeskript übernommene Altaufnahme). Die Aufnahmezahlen und die Zustandszahlen zählen deshalb Aufnahmezeilen, nicht Diktate.
- Auch die Ankerzeile trägt die Dateigrösse des Originals. Die Speichersumme rechnet sie deshalb zur Grösse der transkribierten Aufnahme hinzu.
- Ankerzeilen und die Arbeitskopien des Importkanals werden mit gesetztem Archivmerkmal angelegt. Die Zeile **Archiviert** der Karte **Zustand** enthält deshalb die Ankerzeile jedes klinischen Diktats und jede Import-Arbeitskopie, dazu die durch die Aufbewahrung archivierten Aufnahmen.
- In **Aufnahmen pro Tag** zählen Ankerzeilen als **Hochgeladen** (sie tragen den Spaltenstandard `upload`), die Arbeitskopien des Importkanals als **Sonstige**.

Für Diktatzahlen ist die klinische **Statistik** vorgesehen, sobald sie sich freischalten lässt; heute ist sie zurückgehalten (siehe unten).

### Übersichtskarten

| Karte (Deutsch / Englisch) | Inhalt |
|---|---|
| **Aufnahmen** / **Recordings** | Im Zeitraum angelegte Aufnahmezeilen. |
| **Transkribiertes Audio** / **Audio transcribed** | Audiolänge, die erfolgreiche Transkriptionen im Zeitraum erfasst haben (Rundung siehe unten). |
| **KI-Anfragen** / **AI requests** | Sprachmodellaufrufe im Zeitraum. Embedding-Anfragen zählen nicht. |
| **KI-Kosten** / **AI cost** | Sprachmodell-, Embedding- und geschätzte Transkriptionskosten zusammen (Kosten siehe unten). |
| **Aktive Benutzer** / **Active users** | Eigentümer mindestens einer im Zeitraum angelegten Aufnahmezeile, «von N Benutzern». |
| **Gesamter Speicher** / **Total storage** | Dateigrössen aller Aufnahmezeilen, deren Audio nicht entfernt wurde, über die gesamte Zeit. |

Unter **Aufnahmen**, **Transkribiertes Audio**, **KI-Anfragen** und **KI-Kosten** steht die Veränderung gegenüber dem vorherigen Zeitraum («… gegenüber dem vorherigen Zeitraum», «Wie im vorherigen Zeitraum» oder «Keine im vorherigen Zeitraum»). Zeigt **KI-Kosten** «Lokal», bleibt die Zeile leer. Unter **Aktive Benutzer** steht «von N Benutzern», unter **Gesamter Speicher** «N Aufnahmen insgesamt» (alle Aufnahmezeilen, einschliesslich Ankerzeilen und Import-Arbeitskopien).

**Aufnahmen pro Tag** stapelt die neuen Aufnahmezeilen jedes Tages nach Quelle: Hochgeladen, Aufgenommen, Zusammengeführt und Sonstige. Unter dem Diagramm stehen die Anzahl **Erstellt**, die **Mittlere Verarbeitungszeit** (Median) der im Zeitraum angelegten, abgeschlossenen Zeilen und **Fehlgeschlagen** (Anzahl, Gesamtzahl und Prozentsatz).

### Minuten und Rundung

Transkriptionsminuten stammen aus den Audiosekunden, die der Hintergrundprozess nach einer erfolgreichen Transkription erfasst. Die Rundung unterscheidet sich je nach Stelle:

| Stelle | Regel |
|---|---|
| Karte **Transkribiertes Audio** und ihr Vergleich | Summe der Sekunden im Zeitraum, durch 60 geteilt und auf ganze Minuten abgerundet. Angezeigt als «N Min.» oder «H Std. M Min.». |
| Diagramm **Transkribiertes Audio pro Tag (Minuten)** | Pro Tag: Sekunden durch 60, auf eine Dezimalstelle gerundet. |
| **Kosten nach Modell**, Umfang einer Transkriptionszeile | Je Gruppe aus Anbindung und Modell auf eine Dezimalstelle gerundet, dann je angezeigtem Namen addiert. Die Tabelle zeigt den Wert auf ganze Minuten gerundet. |
| Benutzertabelle, **Minuten diesen Monat** | Sekunden des laufenden Kalendermonats, auf ganze Minuten abgerundet. |

### Zustand

Die Karte **Zustand** listet die aktuellen Aufnahmezustände über die gesamte Zeit: Abgeschlossen, Wird transkribiert, Wird zusammengefasst, Ausstehend, Fehlgeschlagen, Audio gelöscht und Archiviert. Das sind Zustände der oben beschriebenen Aufnahmezeilen der Basisanwendung, nicht die klinischen Arbeitsablaufzustände (etwa **Offen**, **In Prüfung** oder **Freigegeben**).

Die Zeile «Warteschlange: N wartend, M laufend» zählt alle wartenden und laufenden Verarbeitungsaufträge; danach folgt, wenn es welche gibt, «N in den letzten 7 Tagen fehlgeschlagen». **Zuletzt fehlgeschlagene Aufnahmen** listet die fünf neuesten fehlgeschlagenen Aufnahmezeilen mit Kennung, Benutzername des Eigentümers, Datum und den ersten 300 Zeichen der gespeicherten Fehlermeldung. Dieser Fehlertext wird nicht geschwärzt. Die Seite **Verarbeitung** zeigt dagegen fehlgeschlagene Verarbeitungsaufträge (nicht Aufnahmezeilen), nur mit Auftragsart und Fehlerklasse und ohne Fehlertext, siehe [Verarbeitung (Auftragszustand)](#verarbeitung-auftragszustand).

Eine Warteschlange, die ständig wächst, weist auf einen angehaltenen oder überlasteten Hintergrundprozess hin. Wiederholte Fehler mit derselben Meldung bedeuten meist, dass der lokale Transkriptionsdienst nicht erreichbar oder ein Modellname falsch ist.

### Kompatibilität der Stimm-Embeddings

Die Karte gehört zum übernommenen Register, hat aber keine klinische Funktion: Im Klinikmodus sind die Upstream-Sprecherprofile gesperrt (Funktion `speaker_identification`), und Stimm-Embeddings diarisierter Sprecher werden nicht gespeichert. Die Karte erscheint nur, wenn die aktive Transkriptionsanbindung Sprecher-Embeddings liefert. Das ist nur bei der Anbindung `asr_endpoint` mit `ASR_RETURN_SPEAKER_EMBEDDINGS=true` der Fall. Hinter der klinischen GPU/CPU-Ausweichlösung entscheiden die Fähigkeiten der CPU-Anbindung.

### Nutzung und Kosten

Der Abschnitt zeigt **Sprachmodell-Tokens pro Tag, nach Aufgabe**, **Transkribiertes Audio pro Tag (Minuten)**, **Embedding-Tokens pro Tag**, **Kosten pro Monat** (die letzten zwölf Kalendermonate, gestapelt nach Sprachmodell, Embedding und Transkription) und **Kosten nach Modell** (Modell, Anfragen, Umfang, Kosten). Meldet der Textmodell-Server Cache-Treffer, nennt eine Zeile unter dem Token-Diagramm die im Zeitraum aus dem Prompt-Cache gelesenen und in ihn geschriebenen Tokens («Prompt-Cache: N Tokens aus dem Cache gelesen, M geschrieben, in diesem Zeitraum»). Die Zeile erscheint nur, wenn mehr als null Tokens aus dem Cache gelesen wurden. Das Token-Diagramm zeigt nur Vorgänge, die tatsächlich gelaufen sind. Vorgänge von Funktionen, die im Klinikmodus gesperrt oder entfernt sind (Inquire, Upstream-Sprechererkennung, Ereignisextraktion), kommen im Normalbetrieb nicht vor.

Die Kostenzahlen sind Schätzungen, keine Rechnungen:

- **Sprachmodell und Embeddings**: nur der Kostenwert, den der Modellserver in seiner Antwort meldet. Ein Server ohne solchen Wert wird mit $0 gezählt.
- **Transkription**: je Anbindung aus einer festen Tabelle geschätzt. `openai_whisper` wird für jedes Modell mit $0.006 pro Minute gezählt, auch wenn `TRANSCRIPTION_BASE_URL` auf einen lokalen Dienst wie Speaches zeigt. `asr_endpoint` und `clinic_stub` werden mit $0 gezählt.
- Mit der empfohlenen lokalen Speaches-Einrichtung (Anbindung `openai_whisper`) zeigen die **KI-Kosten** deshalb geschätzte Transkriptionskosten, obwohl das Audio beim lokalen Dienst bleibt.
- Der Wert «Lokal» ersetzt $0 nur, wenn die Kosten genau 0 betragen und `MODELS_ARE_LOCAL=true` gesetzt ist (Standard `false`). Für Embedding-Zeilen entscheidet die Embedding-Konfiguration.
- Die Nutzung wird unter dem Namen der Anbindung erfasst. Ein GPU-Ziel hinter der klinischen GPU/CPU-Ausweichlösung wird unter dem Namen der CPU-Anbindung erfasst. Die Statistik zeigt daher nicht, ob GPU oder CPU gelaufen ist.

### Benutzer

Die Tabelle **Benutzer** hat eine Zeile für jedes Konto und ist beim Öffnen absteigend nach **Speicher** sortiert (grösster zuerst). Spalten: **Benutzer**, **Aufnahmen**, **Speicher**, **Letzte Aufnahme**, **Tokens diesen Monat**, **Minuten diesen Monat** und **Kosten diesen Monat**. Aufnahmen und Speicher gelten für die gesamte Zeit; der Speicher zählt Aufnahmen mit entferntem Audio nicht mit. Tokens, Minuten und Kosten gelten für den laufenden Kalendermonat, unabhängig vom gewählten Zeitraum. Ein Klick auf eine Spaltenüberschrift sortiert nach dieser Spalte (**Benutzer** aufsteigend, alle anderen absteigend); ein weiterer Klick kehrt die Reihenfolge um.

Jedes Konto mit der **Administrationsrolle** sieht hier die Benutzernamen und die Nutzung aller Konten.

Hat ein Konto ein Limit, zeigt ein Balken die Ausschöpfung. Die Limits werden im Konteneditor unter **Benutzer und Rollen** als **Speicherlimit (MB)**, **Tokenlimit pro Monat** und **Transkriptionslimit pro Monat (Sekunden)** gesetzt, siehe [Benutzerverwaltung](user-management.md).

- Grün: unter 80 % des Limits.
- Gelb: 80 % bis unter 100 %.
- Rot: 100 % oder mehr. Die Budgetprüfungen für Tokens und Transkription verweigern dann jede weitere Nutzung bis zum nächsten Kalendermonat.

Erreicht eines der drei Limits 80 %, erscheint neben dem Benutzernamen ein Warnsymbol: gelb, ab 100 % rot, mit dem Tooltip «Bei oder über 80 % eines Monatsbudgets».

## Klinikstatistik

Status: Vorbereitet, deaktiviert. Ort: Abschnitt **Statistik** auf der Seite **Aktivität und Audit**.

### Warum die Seite zurückgehalten wird

- Aggregatberechtigungen liegen nur im Arbeitsspeicher eines einzelnen Serverprozesses. Sie gehen bei einem Neustart verloren und werden nicht zwischen Prozessen geteilt.
- Nur die Python-Funktion `grant_aggregate()` legt eine Berechtigung an, und nur Tests rufen sie auf. Keine Route, keine Verwaltungsseite und kein Befehl legt eine an. Der Fachrechte-Editor weist Statistikrechte zurück.
- Die Verwaltung fragt beim Laden `GET /api/clinic/v1/statistics/aggregates` ab. Ohne Berechtigung antwortet die Route mit 403, und die Seite **Aktivität und Audit** wird für jedes Konto zurückgehalten, auch für die globale Administration. Die Verwaltung zeigt in der Navigation **Für dieses Konto nicht freigegeben: … Aktivität und Audit.** und beim direkten Aufruf von `/admin#/aktivitaet` **Die Seite „Aktivität und Audit“ ist für dieses Konto nicht freigegeben.** Im Kopfmenü **Verwaltung & Seiten** ist der Eintrag **Aktivität und Audit** ausgeblendet.
- Der Abschnitt **Verlauf eines Diktats** auf derselben Seite ist damit ebenfalls ausgeblendet. Der Verlauf eines Diktats (**Verlauf**) bleibt in der Diktatansicht und in der Arbeitsliste verfügbar. Zum Prüfpfad siehe [Auditprotokoll](audit-log.md).

Der Rest dieses Abschnitts beschreibt das vorbereitete Verhalten so, wie der Code es umsetzt.

### Zugriffsregeln

- Verlangt wird eine angemeldete Browsersitzung (sonst 401) und ein aktives Klinikkonto (sonst 403).
- Das Konto braucht mindestens eine Aggregatberechtigung mit dem Geltungsbereich `author` (aufgeführte Autorinnen und Autoren) oder `author_pool` (aufgeführte Autorenpools). Der Geltungsbereich `all` wird abgelehnt. Mehrere Berechtigungen werden zusammengefasst. Diktate ausserhalb der Berechtigungen werden vor dem Zählen ausgeschlossen. Ein Konto ohne Berechtigung erhält 403.
- Die Berechtigung gibt keinen Zugriff auf Audio, Transkript, Export oder eine andere Diktataktion.
- Die Antwort enthält nur Kennungen, Anzahlen und Sekunden. Personen erscheinen als «Benutzer <Kennung>», nie mit Namen, und es wird kein Diktattext gezeigt.

### Was gezählt wird

- Jedes klinische Diktat zählt einmal.
- Status: **Freigegeben** (eine aktive, nicht abgelöste Freigabe, deren Audio-, Transkript- und Kontextrevision noch aktuell sind), **In Prüfung** (eine noch laufende **Bearbeitungssperre** aus **Prüfung beginnen**) oder **Offen** (alles andere).
- **Schreibkraft**: die Inhaberin oder der Inhaber einer laufenden Bearbeitungssperre, sonst die Empfängerin oder der Empfänger der letzten Weiterleitung des Arbeitsstands, sonst **nicht zugewiesen**.
- Audiolänge: die auf der Ankeraufnahme des Diktats gespeicherte Audiodauer. Eine fehlende, negative oder nicht endliche Länge wird nicht addiert; die betroffene Gruppe (Tabellenzeile) wird als **unvollständig** markiert. Eine Gruppe mit Diktaten, aber ohne jede verwendbare Länge zeigt **Nicht erhoben**, nie null. Leere, mit Nullen aufgefüllte Zeiträume zeigen die Gesamtdauer 00:00 und als Durchschnitt **Nicht erhoben**.
- Keine Minutenrundung: Die Schnittstelle liefert Sekunden, die Seite zeigt mm:ss oder h:mm:ss, Sekundenbruchteile werden abgeschnitten. Der Durchschnitt teilt die bekannte Summe durch die Anzahl bekannter Längen.

### Filter und Gruppierung

| Feld | Bedeutung |
|---|---|
| **Zeitbasis** | **Eingang des Diktats** (Standard) oder **Fertigstellung (Freigabe)**. Bei Fertigstellung werden Diktate ohne aktive Freigabe unter **Ohne Zeitstempel (nicht gezählt)** ausgewiesen. Bearbeitungs- und Prüfzeiten stehen als Zeitbasis nicht zur Verfügung. |
| **Von (einschliesslich)** / **Bis (ausschliesslich)** | Ein Datum bedeutet Mitternacht in Europe/Zurich. Das Fenster ist halboffen [von, bis). |
| **Zeitraster** | **Tag**, **Woche** (beginnt am Montag) oder **Monat**. Nötig bei Gruppierung nach Zeitraum. |
| **Gruppieren nach** | **Status**, **Diktiert von**, **Schreibkraft**, **Zeitraum**. Die Gruppierung nach Zeitraum verlangt beide Daten und ein Zeitraster. Leere Zeiträume werden nur mit Nullen aufgefüllt, wenn ausschliesslich nach Zeitraum gruppiert wird. Mehr als 4000 Zeitraumeinträge werden abgelehnt. |

Das Ergebnis zeigt **Herkunft der Zahlen** (**Zeitbasis**, **Zeitzone**, **Zeitfenster**, **Stand**, **Ohne Zeitstempel (nicht gezählt)**) und eine Tabelle mit den Gruppierungsspalten sowie **Anzahl**, **Gesamtdauer**, **Durchschnitt**, **Mit bekannter Dauer** und **Vollständigkeit**.

Die Schnittstelle ist `GET /api/clinic/v1/statistics/aggregates` mit den Parametern `time_basis` (`created`|`completed`), `from`, `to`, `grain` (`day`|`week`|`month`), `group_by` (durch Komma getrennt `status`, `author`, `typist`, `period`) und `expected_version`. Jeder andere Parameter ergibt 400. Alle Antworten der Route tragen `Cache-Control: no-store`, auch Ablehnungen; nur erfolgreiche Antworten tragen zusätzlich den Header `X-Grant-Version`. Wird dieser Wert als `expected_version` zurückgesendet, folgt 409, wenn sich die Berechtigungen geändert haben. Die Route schreibt nichts.

### Export

- Die Seite bietet keinen CSV-Download. Sie zeigt **CSV-Export: nur über den autorisierten Serverbefehl, nicht in dieser Ansicht.**
- Das Modul `src/clinic/statistics_export.py` enthält eine CSV-Ausgabe und eine Funktion `main()` mit den Argumenten `--actor-id`, `--role` und `--output` (Pflicht) sowie `--time-basis`, `--start`, `--end`, `--grain`, `--group-by` und `--expected-version`. Sie schreibt UTF-8 ohne BOM mit CRLF-Zeilenenden, atomar, und setzt formelähnlichen Zellen einen Apostroph voran. Exitcodes: 0 erledigt, 2 ungültig, 3 abgelehnt, 4 Abfrage oder Schreiben fehlgeschlagen.
- Dieser Befehl ist im ausgelieferten Stand nicht ausführbar. `clinic-statistics-export` ist nur der Programmname des Argumentparsers; es gibt keinen Einstiegspunkt. Ein neuer Prozess hat zudem keine Berechtigungen, ein Lauf würde also mit Exitcode 3 (`no_grant`) abgelehnt.
- Der Export würde in den Anwendungslogger `speakr.clinic.statistics_export` schreiben («statistics export done …» oder «statistics export refused … code=…»). Er schreibt keine Zeile ins Auditprotokoll.

### Nicht umgesetzte Paritätsanforderungen

Die Paritätsanforderungen zur Statistik, PAR-ADM-008 (eigene Administrations- und Statistikkonten), PAR-OPS-005 (separat aktivierbare, zeitlich begrenzte Erfassung), PAR-OPS-006 (Berichte) und PAR-OPS-007 (CSV-Export per Oberfläche oder Befehlszeile), sind in der Paritätsmatrix (`clinic-spec/planning/parity-matrix.json`) als nicht begonnen (`not_started`) und nicht ausgeführt (`not_run`) erfasst. Es fehlen insbesondere verpasste Fristen, Löschungen, Bearbeitungs-, Abnahme- und Fertigstellungszeiten sowie die Anwendungsnutzung. Die Klinikstatistik zählt nur Menge und Audiolänge nach Status, Autor, Schreibkraft und Zeitraum.

## Verarbeitung (Auftragszustand)

Status: Verfügbar. **Verwaltung** › **Betrieb** › **Verarbeitung**, `/admin#/verarbeitung`; ausserdem im Kopfmenü **Verwaltung & Seiten** › **Sprache und Verarbeitung** › **Verarbeitung**.

- Die Seite liest `GET /api/clinic/v1/jobs/health` über die neuesten 5000 Verarbeitungsaufträge.
- Eine aktive globale Administration (Administrationsrolle und aktives Klinikkonto) sieht alle Aufträge. Andere angemeldete Benutzer sehen nur Aufträge von Diktaten, die sie ansehen dürfen.
- Kennzahlen: **Abgeschlossen** (abgeschlossene von allen gelesenen Aufträgen, darunter «Aufträge insgesamt: N»), **Fehlgeschlagen** (Hinweis **Manueller Versuch möglich**), **Hängende Aufträge** (länger als 1800 Sekunden in Arbeit, Hinweis **Ohne Fortschritt**) und **Angezeigt** (Hinweis **Nach Filter**).
- Die Liste **Fehlgeschlagene Aufträge** enthält die zehn neuesten Fehler mit den Spalten **Auftrag**, **Art**, **Zustand** und **Aktion** sowie einen Filter **Zustand**. **Zustand** zeigt «Fehlgeschlagen:» und dahinter die Fehlerklasse, bei einer Zustellung mit unbekanntem Ergebnis «Unbekannt:» und die Fehlerklasse. Einen Fehlertext zeigt die Seite nicht. Die Schnittstelle liefert an seiner Stelle nur «Fehlerdetails ausgeblendet.»; bei einem abgelehnten Auftrag der Art **Sprachtraining** liefert sie «Training nicht verfügbar» mit der Fehlerklasse `CAPABILITY_UNAVAILABLE`. Bereits erneut eingereihte Fehler werden ausgelassen.
- Darunter stehen die **Wiederholungsregeln**: Bereits korrigierte Textrevisionen dürfen durch eine erneute Spracherkennung nicht still ersetzt werden, und ein unklarer Zustand verlangt zuerst einen Abgleich statt eines erneuten Versands.
- Sie zeigt kein Transkript, keine Auftragsparameter und keinen Audiopfad. Welche Erkennung tatsächlich lief (GPU oder CPU), ist daher auch hier nicht zu sehen.
- **Erneut versuchen** ist nur einer aktiven globalen Administration erlaubt und verlangt ein CSRF-Token, einen `Idempotency-Key` und `If-Match` mit der Version des Auftrags. Dabei wird genau ein neuer Auftrag eingereiht. Eine Zustellung mit unbekanntem Ergebnis zeigt stattdessen **Abgleich prüfen** und wird nicht erneut versucht; Freigabe- und Zustellaufträge mit eigenem Vorgangsschlüssel werden nicht wiederholt. Ein erneuter Versuch speichert einen Idempotenzbeleg und schreibt kein Auditereignis.
- Für hängende Aufträge oder eine alte Warteschlange gibt es keine Warnung und keinen Hinweisbalken.
- Der native Tray- und Autostart-Betrieb bleibt eine offene Lücke (WG-OPS-004, Status offen). Die Seite **Verarbeitung** und der Browser ersetzen ihn nicht.

## Unterschiede zu Upstream

- Die Upstream-Registerleiste ist durch die klinische Verwaltung ersetzt; die **Systemstatistik** steht in der Gruppe **System** neben **Systemeinstellungen** und **System-Auditprotokoll**.
- Die Ereignisextraktion ist aus dem Fork entfernt, es erscheinen daher keine Tokens dafür.
- Die Preistabelle für Transkription kennt nur `openai_whisper` und `asr_endpoint`; die Cloud-Anbindungen von Upstream sind entfernt.
- Die Karte zur Kompatibilität der Stimm-Embeddings hat keine klinische Funktion (siehe oben).

Upstream-Speakr-Dokumentation (fixiertes Original, nicht das klinische Verhalten): [System Statistics](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/statistics.md)

---

Weiter: [Systemeinstellungen](system-settings.md) →
