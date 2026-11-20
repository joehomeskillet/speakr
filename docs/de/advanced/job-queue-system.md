# Verarbeitungsaufträge und Wiederholungen

[English](../../advanced/job-queue-system.md)

Die datenbankgestützten `ProcessingJob` und `FairJobQueue` verarbeiten Transkriptionen, Zusammenfassungen, Indexierung und Konvertierung. Der Audioplayer benötigt dafür keinen abgeschlossenen Auftrag. Arbeitsstand eines Diktats, Status eines Verarbeitungsauftrags und Zustellquittung eines Fremdsystems sind getrennte Sachverhalte.

1. Öffnen Sie **Verarbeitung** am berechtigten Diktat oder die zugelassene Auftragsübersicht. Notieren Sie Auftragsart und Status. Ein wartender Erkennungsauftrag hat noch kein Transkript erzeugt. Ein fertig erkannter Text kann weiterhin auf menschliche Korrektur warten.
2. Bei Verzögerungen prüfen Sie die Betriebsbereitschaft des lokalen Modells und ob die vorgesehene App-Instanz die Hintergrundworker betreibt. Eine reine Webreplik bedient Anfragen. Ein für Wartung mit `SPEAKR_SKIP_BACKGROUND_TASKS=true` gestarteter Prozess arbeitet keine Queue ab.
3. Bei Fehlern prüfen Sie Erreichbarkeit, Modellkennung und aktuelle Eingaberevision. Nutzen Sie, sofern angeboten, die zulässige Wiederholung am bestehenden Auftrag. Aktualisieren Sie die Übersicht und prüfen Sie den Endstatus an demselben Diktat.
4. Bei unklarer Fremdzustellung gleichen Sie die Quittung zuerst mit dem Zielsystem ab. Ein unbekannter Ausgang bestätigt keinen Zustellfehler. Bewahren Sie bei Textänderungen die menschliche Korrekturfassung und prüfen Sie die folgende Erkennungsfassung getrennt.

Für Transkription, Zusammenfassung, Indexierung und Konvertierung gelten getrennte Workergrenzen. Messen Sie Warte- und Laufzeit nach Auftragsart, bevor Sie die Parallelität erhöhen. Zusätzliche Worker auf derselben GPU oder Platte können die Verarbeitung verlängern. Die [Queue-Implementierung](https://github.com/joehomeskillet/speakr/blob/main/src/services/job_queue.py) enthält Zuordnung und unterstützte Umgebungsvariablen.

## Workergrenzen

Beim Start liest der Prozess `JOB_QUEUE_WORKERS` (Transkription, Standard 2), `SUMMARY_QUEUE_WORKERS` (2), `INDEX_QUEUE_WORKERS` (1) und `CONVERT_QUEUE_WORKERS` (2). Die Warteschlange verteilt Bearbeitungszüge über die Nutzer. Das Zusammensetzen einer Aufnahme belegt bereits vor der Erkennung die Transkriptionskategorie. Reichen Sie für einen Lasttest zwei zulässige synthetische Aufnahmen aus getrennten Konten ein, beobachten Sie Reihenfolge und Dauer und wiederholen Sie den Test bei gleicher Last nach Änderung eines einzelnen Grenzwerts.

Originalreferenz zu Upstream-Speakr: [archivierte Upstream-Seite](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/advanced/job-queue-system.md).
