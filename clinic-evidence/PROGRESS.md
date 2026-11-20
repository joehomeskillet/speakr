# Speakr V8: geprüfte Änderungen im Klinik-Webclient

## Staging und Teststand am 20. November 2026

| Bereich | Stand und Prüfumfang |
| --- | --- |
| Aufnahmen und Arbeitsliste | c5890b54 enthält den Zeilenaufruf, die Tastaturkorrektur nach dem Zurücknavigieren und die bei 390 Pixeln vollständig sichtbare erste Karte. Browserläufe haben das Wiederöffnen 24-mal geprüft. |
| Schreibplatz und Verwaltung | Der Integrationsstand enthält die gemeinsame Diktatnavigation, das integrierte Verwaltungsmenü, namentliche Zuweisungen und getrennte persönliche und administrative Wortschatzansichten. Die Prüfung verwendete synthetische Inhalte. |
| Freigabe und Korrektur | Der Korrekturclient sendet If-Match. Die Prüfung der vollständigen Freigabekette mit aktueller Audiofassung, Abnahmeregel und Sperre steht aus. |
| Verlässlichkeit | Zwei Prozessabbrüche prüften die Wiederaufnahme der Benachrichtigungen. Die Exportprüfung las eine Datei über einen zweiten Anwendungsprozess. |
| Vollständiger Projektlauf | Auf 86ddee26 bestanden 11.657 Python-Tests, 5.809 serielle Kliniktests, 1.942 Paketprüfungen und 2.337 JavaScript-Tests. Die Suite für die nachfolgenden Änderungen läuft separat. |

Die Staging-Reparatur ergab zwölf abspielbare Audiodateien aus dreizehn übernommenen Diktaten. Ein DS2-Original enthält null Audioframes. Der Durchlauf als bestehender User 1 benötigt dessen Klinik-Kontopolicy. Hardware-, externe KIS- und Produktionsabnahme sind offen.
