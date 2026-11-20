# Speakr V8: geprüfte Änderungen im Klinik-Webclient

## Staging und Teststand am 20. November 2026

| Bereich | Stand und Prüfumfang |
| --- | --- |
| Aufnahmen und Arbeitsliste | c5890b54 enthält den Zeilenaufruf, die Tastaturkorrektur nach dem Zurücknavigieren und die bei 390 Pixeln vollständig sichtbare erste Karte. Browserläufe haben das Wiederöffnen 24-mal geprüft. |
| Schreibplatz und Verwaltung | Der Integrationsstand enthält die gemeinsame Diktatnavigation, das integrierte Verwaltungsmenü, namentliche Zuweisungen und getrennte persönliche und administrative Wortschatzansichten. Die Prüfung verwendete synthetische Inhalte. |
| Freigabe und Korrektur | Der Korrekturclient sendet If-Match. Die lokale Browserprüfung bestätigte die vollständige Freigabekette mit aktueller Audiofassung, Abnahmeregel und Sperre. |
| Verlässlichkeit | Zwei Prozessabbrüche prüften die Wiederaufnahme der Benachrichtigungen. Die Exportprüfung las eine Datei über einen zweiten Anwendungsprozess. |
| Vollständiger Projektlauf | Auf 86ddee26 bestanden 11.657 Python-Tests, 5.809 serielle Kliniktests, 1.942 Paketprüfungen und 2.337 JavaScript-Tests. Die Suite für die nachfolgenden Änderungen läuft separat. |

Auf b7c776ee erreicht eine regulär angemeldete QA-Autorin den Schreibplatz über die Aufnahmenkarte. Ein neues synthetisches Diktat wurde über HTTPS angenommen und im Browser auf dem öffentlichen Staging abgespielt. Die lokale Browserprüfung bestätigt den vollständigen Freigabeweg samt aktualisierter Fallbindung. Im Liveversuch scheiterte die Spracherkennung an einer vom lokalen Dienst zurückgewiesenen Modellkennung. Der erste manuelle Speichervorgang ohne vorhandene Textrevision lieferte HTTP 503. Für beide Fälle läuft eine gesonderte Prüfung.

Die Staging-Reparatur ergab zwölf abspielbare Audiodateien aus dreizehn übernommenen Diktaten. Das verbliebene DS2-Original enthält fünf leere Frames und ergibt beim Dekodieren null Audiosamples. Hardware-, externe KIS- und Produktionsabnahme sind offen.

## REST27 x20 gap4: explizite ASR-Wiederholung (Worktree, 20. November 2026)

Ein ausdrücklich gestarteter, weiterhin aktueller Repeat darf einen geänderten Maschinenstand als unveränderlichen ASR-Nachfolger speichern. Die Bindung an Ausgangsrevision, Audiofassung und Quellversuch sowie eine Ergebnisquittung liegen im bestehenden ProcessingJob. Recording-Quelle und Klinikanker zeigen denselben aktuellen Text. Duplikate, identische Ergebnisse und veraltete Versuche ersetzen ihn nicht; menschliche Korrekturen bleiben aktiv. Segmentlinks und Wortbindungen gehören zum neuen Textstand.

Fokussierter Lauf: **113 passed in 21.26s**, einschließlich echtem HTTP-Upload-/Repeat-/Queue-Pfad mit synthetischem lokalem Connector, veralteter Text-/Audiofassung, geändertem Quellversuch, neuerem Job, überholtem Worker mit geändertem Retry-Zähler oder Startzeit, menschlicher Korrektur während ASR und Ingest-Rollback. Befehle und Ausgaben: `.rest27-evidence/REPORT.md` und `.rest27-evidence/final-focused-tests.log` im Worktree `rest27-asr-successor`. Keine Aussage über ASR-Qualität oder PostgreSQL-Parallelität. Vollständige Projekt-Gates und Staging wurden nicht ausgeführt; Commit und Integration liegen beim Orchestrator.

## Staging mit regulären QA-Konten am 20. November

Auf afa9f9b3 hat der QA-Autor die erste manuelle Korrektur einer Aufnahme ohne ASR-Fassung im Schreibplatz gespeichert. Der Aufruf lieferte HTTP 200. Nach einem erneuten Öffnen in einer neuen Seite war derselbe Satz sichtbar. Die frühere 503 entstand, weil die erste menschliche Revision keine Referenz auf das vorhandene Original hatte.

Der zweite synthetische Upload durchlief die lokale GPU-Erkennung und wurde über die Oberfläche eingereicht. Die Klinik legte eine Textfassung mit 126 Zeichen und einer Segmentverknüpfung an. Die GPU lieferte im Produktpfad zunächst keine nutzbaren Wortzeiten. Die automatische Zusammenfassung ist für diesen Ablauf deaktiviert.

Ein über HTTPS angemeldeter Identitätsadministrator übernahm Staging-Konto 1 mit der bestehenden ID und 13 unverändert zugeordneten Diktaten. Das Konto besitzt jetzt die aktive lokale Klinikpolicy und einen expliziten Grant für Dokumentexport und Freigabe im eigenen Bestand. Die bestehende Site-Admin-Rolle hat die unter O4 vereinbarten Leserechte. Die Browserabnahme verwendet neue QA-Konten.

PR #2 wurde nach sechs grünen Projektprüfungen in clinic/main gemergt. Der Lauf auf 3475a94e bestand mit 11.664 Python-Tests, 5.816 seriellen Kliniktests, 1.942 Paketprüfungen und 2.352 JavaScript-Tests.
