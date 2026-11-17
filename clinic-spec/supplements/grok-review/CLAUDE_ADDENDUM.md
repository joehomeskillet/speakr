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
