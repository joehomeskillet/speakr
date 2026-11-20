# Darstellung, Themen und Bedienbarkeit

[English](../../advanced/ui-themes.md)

Der Klinikarbeitsplatz bietet helle und dunkle Flächen, Desktop-Arbeitslisten und eine kompakte Telefonansicht. Das Upstream-Darstellungssystem verwaltet Kontothema, Farbschema und Helligkeit getrennt. Die Klinikkomponenten haben eigene Layoutregeln. Prüfen Sie nach einer verfügbaren Darstellungsänderung die tatsächliche Aufnahme- und Verwaltungsansicht.

## Änderungen prüfen

1. Wählen Sie mit einem zugelassenen Testkonto eine verfügbare Darstellungsoption. Kehren Sie zur Aufnahmenliste zurück und öffnen Sie ein synthetisches Diktat. Prüfen Sie bei schmaler Breite, ob derselbe Titel dasselbe Diktat öffnet wie am Desktop.
2. Kontrollieren Sie Wiedergabe, sichtbaren Fokus, Beschriftungen, Statusmarken, Korrekturwerkzeuge und Formularfehler im hellen und dunklen Modus. Erreichen und bedienen Sie die Elemente per Tastatur. Prüfen Sie auch den eigentlichen Transkripttext.
3. Öffnen Sie die Seite erneut und prüfen Sie, was für Konto und Browser gespeichert wurde. Browser- oder Service-Worker-Cache können vorübergehend ältere Dateien ausliefern. Halten Sie den Versionsstand fest, bevor Sie Bilder vergleichen.

## Ein Thema pflegen oder hinzufügen

Die [Themenregistrierung](https://github.com/joehomeskillet/speakr/blob/main/src/config/ui_themes.py) verwaltet Kennungen, Verfügbarkeit und benannte Markup-Bereiche. Gemeinsame Komponenten liefern die Standarddarstellung. Ein Thema kann einzelne Bereiche ersetzen. Eine unbekannte oder zurückgezogene gespeicherte Kennung fällt auf das Standardthema zurück. Verwenden Sie die Projekttokens und erhalten Sie Beschriftungen, Tastaturbedienung und Kontrast. Führen Sie `python -m pytest tests/test_ui_themes.py tests/test_ui_theme_slots.py` aus und prüfen Sie die [Klinikansichten](../screenshots.md) auf Telefon und Desktop.

Originalreferenz zu Upstream-Speakr: [archivierte Upstream-Seite](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/advanced/ui-themes.md).
