> V4: erhaltene ergänzende Bewertung. Aktuelle Architektur ist Web-only, lokale Anwender zuerst. Historische Task-/Gateformulierungen haben keinen Vorrang vor CLAUDE.md und V4-Katalogen.

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
