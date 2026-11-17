# Startauftrag – Speakr-Fork V4 / Web-only
Lies `CLAUDE.md` und bearbeite den vollständigen Auftrag nach dessen Vorrangregeln. Die zusätzlichen 101 DNS-Comfort-Funktionen sind Pflicht. Verwende Speakr als Fork; kein Neubau und kein nativer Diktatclient. Nur der Import-Agent darf lokal Dateien von Diktiergeräten/erlaubten Ordnern abholen.

Erster Betriebsmodus ist lokal, ohne Entra/Keycloak/SMTP. Entra/LDAP später vorbereiten, nicht wegen fehlender Zugangsdaten stoppen. Alle Aufnahmen, Text-/Audiobearbeitungen, Korrekturen und Verwaltungsaktionen im Web. Webkonflikte aus `planning/web-parity-gaps.json` beibehalten; keine Originaltests abschwächen oder native Hintertür im Import-Agent bauen.

Bei neuem Repository: gepinnten Speakr-Stand verifizieren, S00/P00/P03 starten. Bei bestehendem Fork: zunächst Arbeitsstand/Gitstatus/Tests sichern und den Spezifikationsdelta einarbeiten; nicht neu initialisieren oder bestehende Implementierung überschreiben. Vorhandene Module wiederverwenden. Aktuelle Specs unter `clinic-spec/` integrieren; Originalarchive nicht kopieren oder indexieren.

Liefere zunächst einen synthetisch getesteten lokalen Ende-zu-Ende-Ablauf und arbeite dann die PAR-Matrix nach Abhängigkeiten ab. Reversible Entscheidungen ohne Rückfrage treffen; Hersteller-/Hardware-/Browsernachweise als gezielte Tickets/Gates führen. Echte offene Teilkonflikte blockieren nur ihre Freigabe beziehungsweise volle Paritätsaussage, nicht unabhängige Entwicklung.

Am Ende jeder Arbeitseinheit: geänderte Dateien, ausgeführte Tests, tatsächliche Resultate, nächste Aufgabe und konkrete Blocker nennen. Kein Produktivbetrieb, keine Datenlöschung/externen Accountänderungen und keine vollständige Parität behaupten, solange dafür die Nachweise fehlen.
