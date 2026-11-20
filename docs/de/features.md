# Funktionen im Klinikablauf

[English](../features.md)

| Aufgabe | Bedienweg | Woran Sie den Erfolg erkennen |
|---|---|---|
| Aufnehmen oder hochladen | Unter **Neue Aufnahme** Mikrofon oder Audiodatei wählen und den Entwurf sichern. | Der Server bestätigt das gespeicherte Audio. Erst das Einreichen startet den Schreibablauf. |
| Sprache lokal erkennen | Das konfigurierte lokale ASR-Profil verwenden und den Verarbeitungsstand beobachten. | Einen bekannten Satz anhören und mit dem gespeicherten Text vergleichen. Wortgenaues Anspringen setzt Zeitangaben der Engine voraus. |
| Zuweisen und korrigieren | In der **Arbeitsliste** nach Pool oder Status filtern, Eintrag öffnen und Bearbeitung übernehmen. | Eine weitere berechtigte Person kann während der Bearbeitung zuhören. Nach dem Speichern den korrigierten Text erneut öffnen. |
| Audio bearbeiten | Bei vorhandener Berechtigung am Schreibplatz die Audiobearbeitung öffnen. Eine neue Fassung speichern und die bearbeitete Stelle anhören. | Das Original bleibt erhalten. Vor einer erneuten Erkennung aktuelle Audio- und Textfassung prüfen. |
| Prüfen und freigeben | Der Abnahmerichtlinie folgen. Eine benannte Prüfperson muss gegebenenfalls erst die Prüfung beginnen und abnehmen. | Die Freigabe bindet konkrete Text- und Audiofassungen. Der Download des Originalaudios benötigt eine eigene Berechtigung. |
| Dokumente vorbereiten | Bei freigegebener Revision bietet die Ausgabe Aufträge für TXT und DOCX. | Ein Auftrag kann Dateinamen und Prüfsumme quittieren. Erst den tatsächlichen Dateiempfang im vorgesehenen Ausgabekanal als Zustellung verbuchen. |

## Unterschiede zum Desktop-Diktat

Die Stapelerkennung nach der Aufnahme ist verfügbar. Für durchgängiges Live-Diktat mit vorläufigem Text aus dem Browsermikrofon, verlässliches Wiederöffnen eines Offline-Audioentwurfs und die Anwendung zentraler Word-Vorlagen mit Textmarken auf freigegebene Dokumente fehlen noch Teile des durchgängigen Ablaufs. Der optionale Windows-Agent importiert zugelassene Aufnahmen. Die Zuordnung des Importautors in Speakr programmiert keine Autorenliste auf einem Philips-Rekorder.

Weitere Grenzen, auch zur realen KIS-Zustellung, stehen im [Funktionsvergleich](roadmap.md). Die [Galerie](screenshots.md) zeigt die Bedienoberfläche.

**Videodateien im gemeinsamen Importpfad.** Die übernommene Einstellung `VIDEO_PASSTHROUGH_ASR` ist standardmäßig ausgeschaltet. Aktiviert überträgt der gemeinsame Videoimport den ursprünglichen Videocontainer an den gewählten ASR-Dienst. Reine Audio-APIs können ihn abweisen. Die Klinikauswahl für Diktate nimmt Audiodateien an. Prüfen Sie den privaten Dienst und sein Eingabeformat vor einer Aktivierung.

## Verwandte Upstream-Funktionen

Die Klinikzusammenarbeit verwendet begrenzte Pools, Zuweisungen und versionierte Abnahme. Für Kontorollen bietet die [Benutzerverwaltung](admin-guide/user-management.md) den Einstieg, für Gruppierung die [Gruppenverwaltung](admin-guide/group-management.md). Prüfen Sie die Sicht am vorgesehenen Konto. Upstream-Ordner, Gruppenfreigaben, öffentliche Links, API-Tokens, Ambrose und aufnahmeübergreifende Suche haben eigene Richtlinien und Datenflüsse. Die jeweiligen Kapitel des [Benutzerhandbuchs](user-guide/index.md) ordnen sie für die Klinik ein. Prüfen Sie vor einer generativen Funktion die [Modellkonfiguration](admin-guide/model-configuration.md), vor einer Integration die [API-Referenz](user-guide/api-reference.md).

Originalreferenz zu Upstream-Speakr: [archivierte Upstream-Seite](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/features.md).
