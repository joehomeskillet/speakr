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
