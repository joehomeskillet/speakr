# Recorder-Rückrollplan

**RD-00:** Nur Dokumentation, keine Laufzeitänderung, Migration oder Auslieferung. Falls nötig, den RD-00-Dokumentationscommit im üblichen Integrationsverfahren revertieren. Kein Datenrücklauf.

## Für RD-01 bis RD-03 vor der Freigabe

1. Bekannten guten Build, Konfiguration, Schema-Version und kompatiblen Rückfallpfad im Staging protokollieren. View und Speicherkontrakt additiv und getrennt auslieferbar halten. Falls ein Schalter ergänzt wird, Klinikmodus und RT-03 respektieren; alter View bleibt Rückfalloption, solange er mit neuem Speichervertrag kompatibel ist. Keine Datenmigration allein für den View.
2. Mit synthetischen Daten Rückfall pro Zustand proben: aktive Aufnahme, pausierte Aufnahme, laufender Transfer, Commit ohne ACK, bestätigter Entwurf, explizit eingereicht. UI-Rückfall darf keine ungesicherten Tabs neu laden, keinen Upload erneut auslösen und keinen bereits quittierten Auftrag ein zweites Mal einreichen. In-flight-Vorgänge nach Vorgangs-ID und Besitzer abgleichen; Unklares zur Klärung offen lassen.
3. Stoppsignale für den Pilot: Verlust/Duplikat, falscher Speicher-/Empfangsstatus, fremde Sichtbarkeit, unzulässiger Datenweg oder unbedienbare Hauptaktion. IT stoppt die Ausweitung, deaktiviert den betroffenen neuen Einstieg bzw. schaltet auf den geprüften kompatiblen Build zurück und informiert Schreibdienst/Qualitätsmanagement. Bestehende Entwürfe, Originale, Revisionen und Audit-/Empfangsbelege bleiben erhalten.
4. Danach mit synthetischem Konto Aufnahme → belegter Entwurf → Öffnen und getrenntes Einreichen prüfen, Problemfälle auf doppelte Diktate/Aufträge und Rechte prüfen. Ist die alte App nicht kompatibel, den betroffenen Pfad gesperrt lassen und gezielt vorwärts korrigieren. DB-/Datei-/Schlüssel-Restore nur als separat freigegebene Betriebswiederherstellung mit geprüftem konsistentem Stand (`docs/admin-guide/migration-guide.md:9`, `:114`), nie als Standard-Rollback und nie durch automatisches Löschen von Diktaten.

Rückrollprobe und Freigabenachweis gehören zu D7/G6. Nicht ausgeführt in RD-00; keine Produktivmassnahme autorisiert.
