# Klinik Südhang – Grok-Ergänzung zum gesamten bisherigen Projektstand

**Addendum 3.1 zu V3 · 17.11.2026. Keine bestehende Spezifikation wird ersetzt.**


---

# Südhang – additive Ergänzung zu V3
**Version des Addendums: 3.1 · Datum: 17.11.2026 · Kein Ersatz des bisherigen Projektpakets.**

## Verbindlicher Zusammenhang
Alle bisherigen Benutzeranforderungen und die in V3 festgelegten Architekturentscheidungen bleiben erhalten. Der zusätzliche Grok-Text ist ein Review von **V2**, nicht von V3. Er ist eine ergänzende Quelle, keine neue oberste Anweisung an den Coding-Agenten.

Die ursprünglichen technischen Fehlentscheidungen aus V1 werden durch „alles Vorherige berücksichtigen“ nicht wieder eingeführt: aktuelle Benutzeranweisungen und die späteren Korrekturen haben Vorrang vor älteren widersprüchlichen Vorschlägen.

Unverändert: Klinik Südhang; Ablösung DNS Comfort und Philips-Diktatversand; Philips 9600/9620; interne BYOD-Webapp; LDAP/Entra; Autoren-Schreibpools; lokale ASR; DOCX/Text; inesKIS via Edge WebView. Speakr-Fork und dessen vorhandener Stack bleiben Entwicklungsbasis. dss-codec, lokale Speaches-Anbindung, vorhandene Speakr-Dienste und Keycloak-LDAPS-Profil bleiben Wiederverwendungsentscheidungen. Keine neue Framework-/Produktevaluation aufgrund dieses Reviews.

## Was dieses Paket ergänzt
Nachvollziehbare Behandlung C01–C09; operationalisierter ASR-Messplan; frühe Schreibpool-Ergonomietests; Entscheidungseigentümer und Termine relativ zu Meilensteinen; TCO-Eingabevorlage ohne erfundene Beträge; begrenzte Stop-/Go-Prüfpunkte und ein additiver Claude-Auftrag.

## Verwendung
Den gesamten Ordner als `clinic-spec/supplements/grok-review/` in das bestehende V3-Repository übernehmen. **Bestehende CLAUDE.md, OpenAPI, Anforderungen, Tests und Backlog nicht ersetzen.** `CLAUDE_ADDENDUM.md` ergänzend aus der vorhandenen CLAUDE.md referenzieren. Der Startauftrag steht in `START_CLAUDE_ERGAENZUNG.md`.

Neue IDs beginnen mit `GRK-`, damit bestehende R-/T-/S-IDs weder überschrieben noch umnummeriert werden. Claude integriert die Aufgaben und Tests additiv an den angegebenen Stellen. Technische Implementierung mit synthetischen Daten geht weiter. Reale Hersteller-/Geräte-/Rechtsnachweise bleiben Voraussetzungen für die jeweils betroffene reale Funktion, nicht für jede Codeänderung.

## Evidenz und Liefergrenze
Das Addendum beruht auf dem gesamten vorhandenen Gesprächskontext, den verfügbaren V2-Unterlagen und dem vollständigen hochgeladenen Grok-Review. Die V3-Entscheidungen werden aus dem vorherigen Gespräch übernommen; hier wurde kein unverändertes V3-ZIP neu gepackt oder dessen Quellcode erneut getestet. Es ist bewusst eine **zusätzliche Lieferung**, kein behaupteter vollständiger V3.1-Neubau.

Die neun neuen Abnahmetests sind `not_run`. Paketprüfungen betreffen nur JSON-Verknüpfungen, Dateien und Hashes. Keine neue Produkt-, Lizenz-, Rechts-, Hardware-, ASR- oder Herstellerprüfung wurde dadurch durchgeführt.


---

# Ergänzungsreview: Grok zu V2 im Kontext von V3

## Einordnung
Der hochgeladene Text bezeichnet sich ausdrücklich als Review von Version 2.0 und erklärt, weder Hardware noch inesKIS geprüft zu haben (Original, Zeilen 1 sowie 52–56). Er liefert zusätzliche Bewertung, aber keine neuen technischen Nachweise. Besonders C09 muss deshalb gegen die nachfolgende V3-Fassung gelesen werden: die inzwischen festgelegte OSS-Basis und deren eigene API-Verträge werden nicht zurückgezogen.

## Behandlung der neun Befunde
| Befund | Einordnung gegenüber V3 | Umsetzung |
|---|---|---|
| C01: KIS-Verträge | beibehalten und praezisieren | Realer Ines-Adapter nur gegen Herstellervertrag; interne Adaptergrenzen, DisabledAdapter und stateful Mocks dürfen davor implementiert werden. Keine angenommenen ines-Endpunkte. |
| C02: Philips-Geräteverhalten | bereits enthalten nachweis konkretisieren | Hardware-/Firmware-/Dockmatrix einschliesslich EOL zurücknehmen, Audio ergänzen, Verschlüsselung und geteilter Autor. Simulator bleibt als solcher gekennzeichnet. |
| C03: ASR-Qualität | ergaenzen | Korpusmanifest, getrennte Roh-/Endtextfehler, kritische Fehlerkategorien, Korrekturzeitvergleich und vorab festgelegte fachliche Schwellen ergänzen. |
| C04: Product Owner und Entscheidungen | ergaenzen | Rollenbasierter Entscheidungsplan mit namentlich noch offenen Eigentümern und Fälligkeit vor konkreten Gates. Kein fehlender Name blockiert sämtliche synthetische Entwicklung. |
| C05: TCO/Make-or-buy | ergaenzen ohne architekturreset | Gleicher Betrachtungszeitraum und Leistungsumfang für Bestand und gewählten Speakr-Fork; Beträge/Volumen unbekannt bleiben null. Keine Neuauflage des OSS-Auswahlprozesses ohne neue wesentliche Evidenz. |
| C06: Begrenzter Pilot/Stop-Go | ergaenzen | An drei definierten Meilensteinen Scope und verbleibende Realisierbarkeit bewerten. Teilablösung nur bewusste Entscheidung. Fehlende KIS-Nachweise blockieren echte KIS-Ausgabe, nicht jedes unabhängige Arbeitspaket. |
| C07: Schreibpoolergonomie | ergaenzen vorziehen | Früher Prototyp auf vorhandener Speakr-Oberfläche, Tastatur/Fussschalter/Audio/Text/Parallelität testen. Kein Nachbau des Editors und keine erfundene Fussschalterunterstützung. |
| C08: Datenschutz/Zweckbestimmung | arbeitsvorbereitung ergaenzen | Zweck, Datenflüsse, Speicherorte, Empfänger, Aufbewahrung, Freigaberollen und Restrisiken als konkrete Entscheidungsunterlage vorbereiten; keine automatische Rechts-/Medizinprodukte-Einstufung. |
| C09: Verträge und Migrationen | auf v3 beziehen nicht zuruecksetzen | V3 hat eigene API-Verträge und einen konkreten OSS-Unterbau. Diese im Checkout prüfen und pro Slice Migrationen/Contracttests erstellen. Kein globaler Freeze aller Entwicklung bis sämtliche externen Gates abgeschlossen sind. |

## Nicht wörtlich übernommen
„Implementierungsfreigabe noch nicht erteilen“ wird nicht als generelles Verbot verstanden, den synthetischen Workflow zu bauen. Ebenso verhindert „kein Bauauftrag für den Adapter ohne Nachweis“ keine eigene Schnittstellendefinition, Mockimplementierung oder Sicherheitstests. Es verhindert **erfundene reale Herstellerprotokolle und deren produktive Nutzung**.

Der Wunsch nach einem Vertrags-Freeze führt zu einem geprüften Vertrag pro Umsetzungsschritt, nicht zu einem monatelangen pauschalen Stopp. V3 bleibt ein Entwicklungsauftrag innerhalb seiner Sicherheitsgrenzen; es war und ist keine klinische Betriebsfreigabe.

## Hierarchie
1. Ausdrückliche aktuelle Benutzeranforderungen und unverändertes Projektziel.
2. Aktuelle V3-Entscheidungen und bereits korrigierte Sicherheitsinvarianten.
3. Ältere Projektinformationen, soweit nicht durch spätere Entscheidungen ersetzt.
4. Grok-Review als ergänzende Prüfung mit den hier dokumentierten Dispositionen.

Keine zusätzliche Quelle darf still die Fallbindung, die Originalaufbewahrung, die Pflicht zur lokalen ASR, den internen BYOD-Umfang oder die abschliessende KIS-Anforderung entfernen. Keine Empfehlung aus V2 darf eine später verworfene Neubauarchitektur reaktivieren.

## Spezifische Präzisierung zur Freigabe
Die fachliche Freigabe einer Anwendungsversion ist nicht identisch mit dem lokalen Ausführen von Unit-/Integrationstests. Ein benannter klinischer Prozesseigner wird vor echten Pilotentscheidungen benötigt. Bis dahin kann Claude einen Prototyp mit synthetischen Fällen und klar markierten Testadaptern erstellen, ohne sich Namen, Budgets oder Genehmigungen auszudenken.


---

# ASR-Qualität: messbarer Ergänzungsplan
**Präzisierung von R-014/R-015/R-016/R-037; keine neue klinische Leistungszusage.**

## 1. Zwei Nachweise getrennt
Entwicklung: synthetische Inhalte und reproduzierbare Fixtures prüfen technische Kette, Datenintegrität, Versionsbindung und definierte Fehlerfälle. Fachliche Evaluation: ausdrücklich freigegebener, repräsentativer Testsatz wird ausschliesslich lokal verarbeitet und durch berechtigte Fachpersonen bewertet. Eine künstliche Text-/TTS-Fixture allein belegt weder Mikrofon-/Gerätequalität noch Dialekteignung.

Reale Sprachaufnahmen können auch ohne Patientennamen personenbezogen sein. Der Coding-Agent bekommt keine Originalaufnahmen und keine vertraulichen Referenztranskripte in externe Prompts/Logs. Er implementiert den Runner gegen synthetische Fixtures; autorisierte Betreiber führen die reale Evaluation im vorgesehenen Verfahren aus.

## 2. Arbeitsplan ohne erfundene Testergebnisse
Entwicklungs-Abdeckungsziel: **60 geplante Diktate**, verteilt auf 20 je Quelle 9600, 9620 und mobile Aufnahme. Das ist ein Projektvorschlag für Testabdeckung, keine gemessene Fallzahl und kein statistischer Zuverlässigkeitsnachweis. Für den Kernpilot ist der Gerätekorpus getrennt auswertbar; mobile Realtests folgen der mobilen Freigabe.

Pro Quelle Standarddeutsch, schweizerische Aussprache, kurze/lange Aufnahmen, Pausen, leise Sprache, Störgeräusche, Zahlen, Medikamente, Einheiten, Negationen und Korrekturanweisungen abdecken. Schweizerdeutsch wird nur dann als verbindliches Abnahmesegment geführt, wenn es im bestätigten Ist-Prozess tatsächlich benötigt wird; ansonsten ist es ein separat ausgewiesener Belastungstest. Keine gute Gesamtzahl darf eine schlechte benötigte Teilgruppe verdecken.

Cases erhalten neutrale IDs, Hardware-/Firmware-/Dock-/Browserprofil, Dateihash, Dauer, Sprache/Sprechstil, Referenzherkunft, Prüfberechtigung und Versionsnachweise. Corpusmanifest beginnt leer. Weder „60 Dateien erstellt“ noch „60 Fälle bestanden“ behaupten, bevor sie existieren bzw. ausgeführt wurden.

## 3. Referenz und Fehlerklassen
Referenztranskript vom Audio ableiten, nicht aus der Modellantwort. Kritische Fälle durch zwei fachkundige Personen beurteilen; Uneinigkeit explizit klären. Kritische Kategorien: falsche Substanz, Dosis, Einheit, Verabreichung, Negation/Bejahung, Zahlen-/Zeitbezug, relevante Auslassung und hinzugefügter nicht gesprochener Inhalt. Eindeutige Fallzuordnung wird zusätzlich im Fachworkflow geprüft; ein korrekt erkannter Name ist kein zulässiger automatischer Patientenschlüssel.

Drei Ergebnisebenen: unveränderte ASR-Ausgabe; durch Schreibpool korrigierter Text; fachlich freigegebener Endtext. Fehler auf allen drei Ebenen erfassen. Der Fehler im Rohtranskript ist nicht gleich dem Fehler im endgültigen Dokument. Umgekehrt beweist ein Klick auf Freigeben keine tatsächliche Fehlerfreiheit.

## 4. Kennzahlen
| Kennzahl | Definition / Aussage |
|---|---|
| WER | (Ersetzungen + Löschungen + Einfügungen) / Wörter im Referenztext; Normalisierungsversion dokumentieren. Kein alleiniger medizinischer Freigabeindikator. |
| Kritische Rohfehler | Anzahl und Kategorie je Diktat sowie Anteil betroffener Diktate. |
| Kritische Restfehler | Fachlich festgestellte kritische Fehler im freigegebenen Test-Endtext. |
| Korrekturaufwand | Aktive Sekunden bis geprüfter Arbeitsstand / Audiominute; Unterbrechungen getrennt erfassen. |
| Vergleich Bestand | Gleicher oder kontrolliert vergleichbarer Inhalt, vergleichbare Rollen und Bedingungen; Reihenfolge-/Lerneffekte dokumentieren. |
| Durchlaufzeit | Wartezeit, ASR-Rechenzeit und Bearbeitungszeit getrennt. Median, P95 und Fehlerfälle statt nur Mittelwert. |
| Vollständigkeit | Abgeschnittene Abschnitte, fehlende Chunks, verdeckte Lücken und verlorene Aufträge separat. |

Interpunktion/Grossschreibung dürfen für einen WER-Auswertungsweg normalisiert werden. Kritische Zahlen, Einheiten, Negationen und Substanzen dürfen dabei nicht aus der sicherheitsbezogenen Bewertung verschwinden. Rohreferenz und Rohantwort bleiben unverändert erhalten.

## 5. Vorab festgelegte Barriere
Für die Entwicklung gelten: technische Invarianten bestanden; absichtlich eingespielte Fehler führen zur erwarteten Sperre/Klärung; keine ungeprüfte/falsch zugeordnete Version wird freigegeben oder exportiert. Diese Eigenschaften prüft Claude automatisiert.

Für den realen Pilot müssen Testsatz, benötigte Sprachgruppen, Referenzprozess und Nutzen-/Qualitätsgrenzen **vor dessen auswertender Durchführung** fachlich beschlossen werden. Unbekannte Werte bleiben als offene Freigabepunkte markiert; Claude erfindet keine akzeptable medizinische Wortfehlerrate. Der vorbereitete Runner arbeitet unabhängig davon weiter mit synthetischen Daten.

Vorgeschlagene harte Sicherheitsbarriere im vereinbarten Abnahmekorpus: kein bekannter unkorrigierter kritischer Fehler in einem fachlich freigegebenen Testdokument. Ein solcher Fehler stoppt die Freigabe des betroffenen Prozess-/Modellstands bis zur Ursachenklärung. „Null beobachtete kritische Fehler“ bleibt eine Aussage zu diesem Korpus, keine Garantie für spätere beliebige Diktate.

Korrekturzeit und Kosten werden gegen den Ist-Prozess ausgewertet. Keine unbelegte feste Einsparquote oder GPU-Leistungszusage. Fachbereich entscheidet anhand des vorab definierten Nutzenschwellenwerts. Modell-/Quantisierungs-/Decoder-/VAD-Änderungen verlangen Regression.

## 6. Abgabe durch Claude
Ein lokaler Runner, Corpus-/Resultatschema, synthetischer Beispieldatensatz ohne echte Stimmen, reproduzierbarer Bericht und getrennte Import-/ASR-/Reviewfehler. Vorhandene Pipeline-/Testbibliotheken verwenden. Freigegebene reale Resultate werden später durch autorisierte Betreiber eingelesen; ein fehlender Realnachweis wird nicht automatisch PASS.


---

# Schreibpool-Ergonomie und begrenzte Freigabepunkte
## Früh prüfen, nicht spät neu bauen
Schon im vorhandenen Speakr-Player/-Editor einen schmalen klinischen Arbeitsablauf zeigen. Mindestens zwei unterschiedliche Schreibpoolperspektiven als vorgeschlagenes Entwicklungsziel einbeziehen; keine statistische Repräsentativität behaupten. Fehlen reale Teilnehmende, automatisierte Bedienungsabläufe implementieren und die Fachabnahme offen lassen.

| Szenario | Nachweis |
|---|---|
| Auftrag übernehmen und zum nächsten wechseln | Reihenfolge/Priorität klar, keine unbeabsichtigte Pool-/Falländerung. |
| Wiedergabe, Zurückspringen, Geschwindigkeit | Häufige Aktionen per Tastatur ohne Fokusverlust erreichbar. |
| Bestehender Fussschalter | Modell/Firmware/USB-Pfad/OS/Browser dokumentiert; tatsächliche Play/Pause/Zurück-Funktion geprüft. |
| Text und Audio vergleichen | Lesbarkeit, Segmentanker und korrigierte ungenaue Wortausrichtung transparent. |
| Gleichzeitige Bearbeitung | Zweiter Benutzer erkennt belegten Auftrag, stale save überschreibt nichts. |
| Autosave, Timeout, Wiederanmeldung | Kein scheinbarer Speichersieg bei Fehler, keine stille Textverlustsituation. |
| Korrektur versus Freigabe | Unterschiedliche Aktion/Rolle sichtbar; Patient/Fall/Kontextversion vor Ausgabe prüfbar. |
| Export/KIS-UNKNOWN | Download ist kein KIS-Empfang; unklarer Ausgang führt zur Klärung, nicht blindem Zweitversand. |

Nötige Fussschalter sind kein optionaler Nachtrag, wenn sie im heutigen Arbeitsprozess erforderlich sind. Kein universelles Browser-HID-Versprechen. Ein lokaler Helfer wird nur bei nachgewiesenem Bedarf in den vorgesehenen Agentenumfang ergänzt, nicht als unkontrollierte zweite Desktopanwendung.

## Drei Stop-/Go-Momente
**Nach S05, dem synthetischen vertikalen Ablauf:** Architektur-/Reuse-Nachweis und Stand von Geräte-/KIS-Evidenz prüfen. Kein stiller Ausbau zum allgemeinen Meeting-/KI-Produkt. Die nächsten unabhängigen V3-Aufgaben dürfen weiterlaufen; zusätzliche Funktionen ausserhalb des Auftrags benötigen eine bewusste Scopeentscheidung.

**Vor dem echten Kernpilot:** Geräte, minimaler Ist-Funktionsumfang, echte Identität, Fachfreigabe, Datenschutz, Betrieb/Restore und die jeweils notwendige Kontextprüfung nachweisen. Scheitert ein Pflichtkriterium, Pilot begrenzen oder stoppen; nicht nachträglich den Test so ändern, dass er besteht.

**Vor Gesamtablösung:** Alle Pflichtkanäle einschliesslich BYOD, LDAP-Fähigkeit im vereinbarten Profil und echter KIS-Rückgabe abgenommen. Nur manuelle Exporte oder eine reine eingebettete Webseite erfüllen nicht die vollständige Ablösung.

## Funktionsbezogene Gates statt pauschaler Entwicklungssperre
| Fähigkeit | Erforderlicher Nachweis vor realer Nutzung |
|---|---|
| Synthetische lokale Entwicklung | Isolation, synthetische Daten und bestehende Sicherheitsregeln; keine klinische Produktivgenehmigung. |
| Reale Geräteaufnahme im Kernpilot | Gerätegate sowie einschlägige Identity-/Fach-/Betriebsfreigaben; verifizierte Fallzuordnung vor Ausgabe. |
| Reale mobile Aufnahme | Zusätzlich BYOD-Gate und genehmigte Browser-/Netz-/Restrisikogrenzen. |
| Reale automatische KIS-Ausgabe | Zusätzlich KIS-Vertrag, Kontextrichtigkeit, Schreibrecht, Quittung und Lookup-/Nachtragsnachweis. |
| Vollständige Abschaltung der Altsoftware | Sämtliche für die vollständige Ablösung notwendigen Gates und Migrations-/Rückfallfreigabe. |

Dies ist keine Freigabe für Patientendaten in ungenehmigten Tests. Fehlende Herstellerfähigkeit wird nicht durch einen Mock ersetzt. „Unabhängig weiterentwickeln“ und „unbegrenzten Pilot weiterfinanzieren“ sind unterschiedliche Entscheidungen. Budget und zulässige externe Testzugänge bleiben beim zuständigen Auftraggeber.


---

# Entscheidungsregister, Datenschutzvorbereitung und TCO
## Keine erfundenen Verantwortlichen
`planning/decision-register.json` enthält Rollen, benötigte Entscheidungen, Fälligkeit relativ zum Projektmeilenstein und erforderliche Evidenz. Namen sind noch nicht vorgegeben. Claude kann die technische Arbeit fortführen, darf aber weder Joël noch eine andere Person ungefragt als klinischen Product Owner oder rechtlich Freigebenden eintragen.

Technische Defaults sind bereits durch V3 gesetzt. Fachliche Entscheidungen werden zum passenden Meilenstein gebündelt vorgelegt, nicht als Einzelrückfrage bei jeder Implementierung. Geheimnisse/Zugangsdaten werden lokal über den vorgesehenen Konfigurationsweg bereitgestellt, nicht im Chat abgefragt.

## Datenschutz-/Zweckunterlage konkret vorbereiten
Vorlage mit Zweck, Nutzerrollen, Eingabe-/Ausgabedaten, Datenflüssen, Speicherorten, Entra-Anmeldemetadaten versus lokalem Audio/Text, Empfängern, Zugriffen, Fehlzuordnungsfolgen, BYOD-Restspuren, Retention/Löschung/Legal Hold, Backupwiederherstellung und verantwortlicher Entscheidung. Die vorgesehenen Schutzmassnahmen mit Tests verknüpfen. Keine definitive rechtliche oder medizinprodukterechtliche Einstufung durch Claude vorgeben. Dieses Addendum macht keine neue Rechtsaussage und ersetzt keine zuständige Bewertung.

## Wirtschaftlichkeit ohne erneute Stackdiskussion
Als gemeinsamer Planungsrahmen dienen **36 Monate** für Bestand und den in V3 gewählten Speakr-Fork. Das ist ein Vergleichsdefault, kein genehmigtes Budget. Gleicher Funktions-/Support-/Verfügbarkeitsumfang; Anfangsinvestitionen und laufende Kosten getrennt. Eine Teilablösung wird separat benannt, statt sie mit vollständiger Ablösung zu vergleichen.

Zu erfassen: Bestandsverträge; Einführung; interne Entwicklung/Teststunden und interner Stundensatz; Herstellerintegration; Geräteadministration inklusive möglicher Rest-SpeechExec-Installation; Hardware; Storage/Backup; laufender Betrieb; Security-/Modell-/Browser-/KIS-Upgrades; Anwenderschulung; Stellvertretung und Exit. Gemeinsame Infrastrukturkosten mit gleicher Zuordnungsregel auf beide Seiten verteilen. Kein Spekulationsaufschlag ohne Annahmenbegründung.

Formel: Einmalige Einführung + 36 × monatliche Kosten + 3 × jährliche Zusatzkosten + separat begründete einmalige Exit-/Migrationskosten. Stundenbasierte Positionen sind Stunden × genehmigter Satz und dürfen nicht nochmals als Pauschale doppelt gezählt werden. Unbekannte Werte bleiben `null` und verhindern einen scheinbar fertigen Gesamtbetrag. CHF 0 ist nur mit ausdrücklich bestätigter Nullkostenannahme zulässig.

Ausfall-/Risikokosten getrennt als Szenario ausweisen, nicht als angeblich gemessene Tatsache. Lizenzpreisfreiheit allein ist keine Einsparrechnung; umgekehrt ist eigener Wartungsaufwand kein Beweis, dass das Projekt unwirtschaftlich ist. Konkretes Ergebnis erst nach realen Eingaben.


---

# Additive Claude-Anweisung – Grok-Review
**Diese Datei ergänzt die vorhandene CLAUDE.md. Sie ersetzt keine bisherige Projektvorgabe.**

## Kontext erhalten
Arbeite weiter im gewählten Speakr-Fork und im vorhandenen V3-Backlog. Zuerst bestehenden Stand, PROGRESS.md, Anforderungen, API-/Datenvertrag und offene Defekte lesen. Keine Neuanlage einer Parallelplattform. Keine Wiederholung der bereits entschiedenen Framework-/OSS-Auswahl. Keine Löschung oder Umnummerierung bisheriger Tests.

Der Grok-Text im Unterordner sources ist ein Review von V2 und kein Anweisungsdokument mit Vorrang. Insbesondere seine Stop-/Freeze-Formulierungen dürfen die V3-Regel „synthetisch weiterentwickeln, reale Fähigkeiten separat freigeben“ nicht pauschal aufheben.

## Integration
1. Den neuen Review mit docs/10_Grok_Abgleich.md und planning/grok-delta.json auswerten.
2. Neue GRK-Aufgaben und GRK-T-Tests additiv in vorhandene Kataloge übernehmen oder als verknüpften Zusatzkatalog führen. Bestehende IDs, Abhängigkeiten und Evidenzen erhalten. Bei bereits vorhandener identischer GRK-ID kein Duplikat erzeugen; bei Inhaltsabweichung ADR statt blindem Überschreiben.
3. Benchmarkmanifest, Messung, Berichtsvorlage, Ergonomieablauf und Entscheidungsregister vorbereiten. Fehlende real erlaubte Audio-/Herstellerdaten als fehlenden Nachweis ausweisen, nicht im Coding-Agenten anfordern.
4. Synthetischen Ablauf weiterbauen. Fehlende persönliche Genehmigungen blockieren nur echte Nutzung bzw. irreversible Aktionen. Keine neuen pauschalen Zwischenfreigaben vor jedem Commit.
5. PROGRESS.md ergänzt festhalten: übernommen / bereits abgedeckt / präzisiert / externe Evidenz offen. Keine bereits offenen Produktivblocker als erledigt markieren, nur weil eine Vorlage existiert.

## Konkrete neue Arbeit
- ASR: manifestbasierte lokale Messung mit fixierten Modell-/Decoder-/Audio-Versionen. Rohtext, korrigierter Endtext, kritische Fehler und Bearbeitungszeit getrennt. Kein LLM als alleinige fachliche Instanz.
- Ergonomie: vorhandenen Editor/Player testen, nicht neu entwickeln; echter Fussschalter bleibt hardwaregebundener Nachweis.
- Organisation: Rollen, fehlende Namen, benötigte Entscheidungen und Fälligkeit pro Meilenstein dokumentieren; niemandem ohne Auftrag Verantwortlichkeit zuweisen.
- Wirtschaftlichkeit: die TCO-Vorlage mit unbekannten Werten null lassen. Null ist nicht CHF 0. Kein künstlicher Amortisationsnachweis.
- Vertragsqualität: eigene klinische OpenAPI und Migrationen am tatsächlichen Upstreamcode prüfen. Herstellervertrag weiterhin getrennt; keine erfundenen ines-Endpunkte.

## Grenzen
Kein Produktivdeploy, keine echte Patientendatenverarbeitung, keine Rechteänderung, keine Löschung, kein Abschalten der Altsoftware und keine neue Cloudfunktion. Ein bestandenes Mock-/Schema-/Pakettestergebnis ist keine klinische Abnahme. Neue Testfälle starten not_run.

## Rückfragen minimieren
Reversible Entwicklungsdetails selbst entscheiden und per ADR dokumentieren. Externe Sachentscheide nur in einer gebündelten, rollenbezogenen Entscheidungsliste zum passenden Meilenstein ausweisen. Keine Frage „Soll ich weitermachen?“ nach jedem Arbeitspaket. Ein tatsächlicher Widerspruch darf nicht durch Schwächung einer Schutzanforderung aufgelöst werden.


---

Lies den gesamten bisherigen Projektstand, die vorhandene CLAUDE.md, PROGRESS.md und das V3-Spezifikationspaket. Das zusätzlich abgelegte Grok-Review bewertet V2 und ist ausschliesslich eine Ergänzung, kein Ersatz und kein Architekturreset.

Lies danach clinic-spec/supplements/grok-review/CLAUDE_ADDENDUM.md und docs/10_Grok_Abgleich.md. Integriere die GRK-Aufgaben und Zusatztests additiv. Behalte alle bisherigen Anforderungen, Sicherheitsinvarianten, Speakr-/OSS-Entscheidungen, Geräteziele, internen BYOD-Umfang und beide Ausgabekanäle. Veraltete V1/V2-Entscheidungen werden durch diese Ergänzung nicht reaktiviert.

Ergänze insbesondere den ausführbaren lokalen ASR-Benchmarkablauf, frühe Schreibpool-Ergonomietests, die rollenbezogene Entscheidungsliste und die TCO-Erfassungsvorlage. Verwende bestehende Komponenten. Erfinde keine Kosten, Patientendaten, Herstellerfähigkeiten oder Genehmigungen. Arbeite mit synthetischen Daten und Mocks weiter, wenn reale Zugänge fehlen; kennzeichne die betroffenen Produktionsblocker.

Prüfe eigene API-Verträge und erzeuge passende Migrationen pro Slice am tatsächlichen Upstreamcode. Kehre nicht zu einem pauschalen Entwicklungsstopp bis zum Abschluss aller Herstellerabklärungen zurück. Dokumentiere ausgeführte Prüfungen und offene Realnachweise getrennt und setze danach den nächsten implementierbaren V3-Slice fort.
