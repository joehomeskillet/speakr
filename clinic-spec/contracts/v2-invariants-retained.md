> V4-Vorrang: Web-only und Import-Agent-Grenze gelten verbindlich. Local-first aus V3.2 bleibt. Neue PAR-Rechte/Policies, WG-Teilkonflikte und Quellenpflege in den V4-Katalogen ergänzen frühere engere Defaults; historische Quellen sind keine Produktfreigabe.

# Datenvertrag: zwingende Invarianten
**Übernommene Sicherheitsinvarianten. V3-Datenvertrag konkretisiert sie; kein ausführbares SQL.**

| ID | Invariante | Datenbank-/Anwendungsnachweis |
|---|---|---|
| INV-01 | Externe Anmeldeidentität ist `issuer + subject`, gegebenenfalls zusätzlich Tenant-Scope; fachliche Identität bleibt die vorhandene interne `user.id`. | Eindeutiger Schlüssel; kein E-Mail-basiertes Zusammenführen. |
| INV-02 | Gerätezuordnung besitzt Historie und Gültigkeit. | Reassignment verändert die Autorenschaft bereits importierter Aufträge nicht. |
| INV-03 | Eine Originalversion gehört genau zu einem logischen Diktat. | Unveränderliche Bytes/Hash; Änderungen als neue Version mit Herkunft. |
| INV-04 | Eine Transkriptversion gehört zum angegebenen Diktat. | Zusammengesetzter Unique-/Foreign-Key-Vertrag oder gleichwertige Constraints. |
| INV-05 | Eine Freigabe referenziert genau die geprüfte Text-, Audio- und Kontextversion desselben Diktats. | Fremde Versionen werden schon beim Persistieren abgelehnt. |
| INV-06 | Ein Export referenziert genau eine zulässige Freigabe oder einen explizit erlaubten KIS-Entwurfsvertrag. | Inhalt-/Kontexthash, Dokumenttyp, target, mode und Herkunft sind eingefroren. |
| INV-07 | Ein freigegebener Text wird nicht bearbeitet, sondern durch eine neue Version ersetzt. | Neue Version ohne alte Freigabe; Nachtrag nach bestehendem KIS-Export separat. |
| INV-08 | Case-/Patientreferenzen sind mandanten-/quellsystemgebunden. | Eine zufällig gleiche Fallnummer in einem anderen Scope kann nicht matchen. |
| INV-09 | Autorpool und Leserolle sind verschieden. | Autor-Mitgliedschaft gibt keinen Vollzugriff auf den Pool. |
| INV-10 | Bearbeitungsübernahme besitzt Besitzer, Ablauf und Fencing-Token. | Veralteter Client kann trotz alter Lease nicht speichern/freigeben. |
| INV-11 | Retry-Identität ist von logischer Aufnahmeidentität verschieden. | Gleicher Retry-Key mit anderem Payload wird abgewiesen. |
| INV-12 | Gleicher Audiohash allein führt nicht automatisch zur Zusammenführung fachlich verschiedener Diktate. | Quellnamespace und Aufnahmeidentität werden berücksichtigt; unsichere Matches in Klärung. |
| INV-13 | Jobs und interne Übergabeabsichten sind dauerhaft und transaktional mit ihrer Fachänderung verknüpft. | DB-Crash zwischen Statuswechsel und Jobpublikation verliert keinen Auftrag. |
| INV-14 | KIS-UNKNOWN ist kein automatischer FAILED-Retryfall. | Nur bestätigtes Nichtvorhandensein oder dokumentierter Hersteller-Idempotenzvertrag erlaubt Neuanlage. |
| INV-15 | Audit-Ereignisse sind für Anwendungsrollen append-only. | UPDATE/DELETE verweigert; DB-Admin-Restprivilegien separat überwacht. |
| INV-16 | Löschauftrag berücksichtigt Legal Hold, Exportstatus und Wiederherstellung aus Backup. | Tombstone/Löschledger verhindert unkontrollierte Wiederbelebung nach Restore. |

## Konkreter Fehler der alten Schema-Skizze
Die reine Beziehung `approved_version_id -> transcript_version.id` beweist nur, dass die Version existiert. Sie beweist nicht ihre Zugehörigkeit zum Diktat. Das neue Schema muss beispielsweise eine Relation über `(dictation_id, transcript_version_id)` gegen einen entsprechenden eindeutigen Schlüssel erzwingen. Analog gilt dies für Export, Freigabe und Kontextrevision. Die konkrete Migration wird gemeinsam mit fachlichen Statusguards implementiert und negativ getestet.

## Aufnahmeidentität
Ein Dateiname ist keine globale Aufnahme-ID. Wenn das Gerät eine verlässliche native Aufnahme-ID liefert, wird sie mit Quellnamespace und Gerätezuordnung verwendet. Ohne solche ID führt der Agent ein dauerhaftes Manifest; serverseitige Heuristiken dürfen nicht still fachliche Aufträge zusammenlegen. Mehrfachimport über verschiedene PCs, Gerätewechsel, Kartenwechsel, Umbenennung, Headeränderung und Audioänderung sind separate Testfälle. Vollständige Dublettenfreiheit ohne unterscheidbare Quellen ist keine ehrliche Zusage; unklare Fälle brauchen einen dokumentierten Klärprozess.

## Freigabepayload
Mindestens Diktat-ID, Original-/Audio-Version, Transkriptversion, Text-Hash, Kontextrevision, Dokumenttyp, Freigabetyp, freigebende Identität, Berechtigungsentscheidung und Zeitpunkt. „Signiert“ bedeutet in dieser Spezifikation nicht automatisch eine qualifizierte elektronische Signatur. Eine KIS-Signatur folgt dem jeweiligen Hersteller-/Klinikprozess.
