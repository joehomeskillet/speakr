# Systemstatistik

[English](../../admin-guide/statistics.md)

Öffnen Sie als Site-Administrator **System → Systemstatistik** und wählen Sie 7, 30 oder 90 Tage. Die Übersicht vergleicht Kalendertage bis heute mit dem gleich langen Vorzeitraum in UTC. Prüfen Sie Aufnahmezahl, transkribierte Zeit, Modellanfragen und aktive Konten. Der Speicherwert bezieht sich auf aufbewahrte Medien. Eine heute erstellte und morgen transkribierte Aufnahme erscheint bei diesen Vorgängen an unterschiedlichen Tagen.

Das Aktivitätsdiagramm unterscheidet Uploads, Browseraufnahmen und zusammengeführte Aufnahmen. Vergleichen Sie die Fehlerrate mit der Zahl der Aufträge, bevor Sie einen Ausschlag bewerten. Die jüngsten Fehler können Fehlertexte und Benutzernamen enthalten. Schützen Sie entsprechende Bildschirmaufnahmen. **Verarbeitung** zeigt den Zustand eines einzelnen Auftrags und die dafür verfügbare Wiederholungsaktion.

## Kosten und Kontingente

Die Modellübersicht trennt Textanfragen, Embeddings und Transkription. Tages- und Modellwerte für Transkriptionszeit werden auf Zehntelminuten gerundet. Hauptkennzahl und monatliche Benutzerwerte verwenden ganze Minuten. Die Anzeige „Lokal“ hängt von `MODELS_ARE_LOCAL` ab. Eine Schätzung von null erfasst weder Strom noch Geräte oder Personal.

Die Benutzertabelle zeigt auch bei einer Diagrammansicht über 90 Tage den Verbrauch des laufenden Kalendermonats neben Token- und Transkriptionslimit. Prüfen Sie bei einer Ablehnung das [Benutzerkonto](user-management.md).

**Aktivität und Audit → Statistik** ist eine gesonderte Fachauswertung für berechtigte Autoren oder Autorenpools. Sie verwendet die Zeitzone Europe/Zurich und ein exklusives Intervallende. Allein durch die Site-Administration erhält ein Konto keine fachlichen Statistikrechte. Dafür gibt es derzeit kein reguläres Verwaltungsformular. „Nicht erhoben“ kennzeichnet einen nicht verfügbaren Messwert.

[Originalanleitung von Speakr](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/statistics.md).
