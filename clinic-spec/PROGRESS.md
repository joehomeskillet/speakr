# Fortschritt V4
## Tatsächlich erledigt in dieser Lieferung
Spezifikationen konsolidiert, 101 Funktionen und Original-BT übernommen, Web-only-Grenze und 13 offene Teilkonflikte dokumentiert; Originalartefakte inventarisiert. Paketprüfungen separat unter evidence/package-checks.json.
## Nicht erledigt
Keine Anwendung implementiert; kein Speakr-Build oder Windows-/Browser-/Geräte-/KIS-/ASR-Test ausgeführt. Alle 252 Produkttests stehen auf not_run. Keine Produktivfreigabe und keine Scopeausnahme.
## Nächste Codearbeit
S00 Baseline prüfen; P00 Quell-/Paritätsvorrang; P03 Browserfähigkeit/Agentgrenze; lokales Identity-/ACL-Slice. Unabhängig P10 tatsächliche ASR-/Lexikon-/Trainingscapabilities prüfen.
## Bei vorhandener Implementierung
Bestehenden Fortschritt nicht durch diese Anfangsnotiz löschen; versioniert zusammenführen und bereits belegte Tests mit realen Artefakten übernehmen.

## WP archive — 2026-11-18

- Owner-Auftrag `/home/codexweb/briefs/archive.md` vollständig gelesen.
- Retention archiviert aufnahmeweit mit unveränderten Dateien und Kindzeilen. Persönliche Archivzustände bleiben separat. Eigentümer dürfen den zentralen Status aufheben.
- Pflichtaudit und append-only Archivjournal liegen in derselben Transaktion. Eindeutige Revisionen sichern konkurrierende Archivänderungen ab. Additive Migration für bestehende Aufnahmen.
- Admin-Einstellungen und sieben Sprachdateien verwenden Archivierungsbegriffe. Bestehende Konfigurationsschlüssel, URLs und historische Moduswerte bleiben kompatibel. Tag-Regeln funktionieren auch bei globaler Frist 0.
- Ergänzende Ausführung mit Python 3.14.4 und SQLite belegt 1560 bestandene Kliniktests bei einem übersprungenen DS2-Codec-Test sowie 654 bestandene gezielte Abschlussprüfungen. Die Abschlussprüfung umfasst Archivierung, Erhaltung echter temporärer Dateien, Audit/Rollback, konkurrierende Änderungen, bestehende manuelle Löschung, Startlogik, Lokalisierung und zwei Altbestand-Migrationen.
- Der ergänzende parallele Gesamtlauf meldet 7949 bestanden, 57 fehlgeschlagen, 242 übersprungen und 16 Setup-Fehler. Enthalten sind Zugriffsbeschränkungen, Laufzeitabweichungen und drei noch ungeklärte Fehlschläge, die im gezielten Lauf nicht auftreten. Der bekannte Audit-Coverage-Test blieb unverändert und besteht in der gezielten Abschlussprüfung.
- Beide vorgeschriebenen Gates bleiben blockiert. Der vorgegebene Python-3.11-Interpreter ist unzugänglich. Die ergänzende Laufzeit besitzt einen expliziten lokalen Import-Bootstrap und erfüllt diese Gates nicht. PostgreSQL, Philips-Hardware, Entra, KIS und Pedale wurden dadurch nicht nachgewiesen.
- Aktueller geprüfter HEAD ist `946da7c6532999d0f433e9e899f6c1f8a0d5b616` auf `clinic/archive`. Die WP-Änderungen liegen uncommittet im Clone. Das dokumentierte Staging scheiterte am schreibgeschützten `.git/index.lock`.
- Unabhängiges Ownerless-Cleanup, Export-Lebenszyklus, Tombstone-/Webhook-Pruning, manuelle Bereinigung unreferenzierter Dateien und gespeicherte Ordner-Retention stehen im Bericht unter OFFENE FRAGEN. Die offenen Web-Paritätslücken behalten ihren bisherigen Status.
- Vollständiger Bericht mit Testdatei-Begründungen, Einschränkungen und wörtlichen Ausgaben unter [archive-report.md](../clinic-evidence/archive-report.md). Maschinenlesbare Zuordnung unter [upstream-map.json](../clinic-evidence/upstream-map.json). Der einmalige Schreibversuch nach `/home/codexweb/reports/archive-report.md` scheiterte mit Exit 1 und `Read-only file system`. Der vollständige Bericht bleibt in der verlinkten Ersatzablage im Clone.
- JSON-/Syntaxprüfung und `git diff --check` sind erfolgreich. Planungs-/Abnahmedaten sowie die unveränderten Audit-, Startup- und Ownerless-Tests zeigen im gezielten Git-Vergleich keine Änderungen.
- Status `BLOCKED` wegen der vorgeschriebenen Gates, des ausstehenden Commits und der schreibgeschützten Zielablage.
