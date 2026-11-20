# Fehlerdiagnose

[English](../troubleshooting.md)

Beginnen Sie mit einem synthetischen Diktat. Notieren Sie App-Commit, Browser, Zeitpunkt, Kennung und Bedienhandlung. Fehlende Audiospeicherung, abgebrochene Erkennung, Versionskonflikt und unklare Zustellung benötigen unterschiedliche Schritte.

| Symptom | Prüfen | Nächster Schritt |
|---|---|---|
| Nach dem Start erscheint die Upstream-Oberfläche | Image-Tag, Quellcommit und `CLINIC_MODE=true` im App-Prozess kontrollieren. | Den Fork bauen und das konfigurierte Klinik-Image starten. Änderungen an der Umgebungsdatei werden mit einem neu erzeugten Container wirksam. |
| Anmeldung scheitert nach der Installation | Prüfen, ob der einmalige Klinik-Bootstrap dieselbe Datenbank verwendet hat und das Konto eine aktive Anmelderichtlinie hat. | Mit dem gewählten Namen anmelden, Serverzeit und HTTPS prüfen. Für Wiederherstellung die Identitätsverwaltung verwenden. Erneuter Bootstrap setzt kein Passwort zurück. |
| Mikrofon fehlt oder Aufnahme ist stumm | Websitefreigabe, sicheren Ursprung, Eingabegerät und Mikrofonrecht im Betriebssystem prüfen. | Einen Satz aufnehmen und abhören. Der Upload einer bekannten WAV-Datei trennt Mikrofon- und Serverwiedergabepfad. |
| Wiedergabe ist stumm oder bricht ab | Zeitfortschritt, Ausgabegerät, Lautstärke und vorhandene Samples in der Quelle prüfen. | Mit einer bekannten Aufnahme vergleichen. Bei DSS/DS2 Decoder und Quelldauer kontrollieren und das Original erhalten. |
| Erkennung wartet oder schlägt fehl | **Verarbeitung** öffnen. Wartestand, zugelassene Dienstadresse, angebotene Modellkennung und Fehlerklasse prüfen. | Lokalen Dienst oder Modellbindung korrigieren, danach den vorgesehenen Wiederholschritt am betroffenen Testauftrag auslösen. Ein erneutes Einreichen des Diktats erzeugt unnötige Doppelvorgänge. |
| Eintrag fehlt im Pool oder ist schreibgeschützt | Filter zurücksetzen, Kennung vergleichen und Autor, Pool, Arbeitsstand und Bearbeitung prüfen. | Zuweisung und Sichtbereich durch eine berechtigte Person kontrollieren lassen. Bei einem Konflikt die aktuelle Fassung erneut öffnen. |
| Wortmarkierung fehlt | Prüfen, ob für diese Audiofassung Wortzeiten aus der Erkennung vorliegen. | Die normale Wiedergabe getrennt testen. Ein Transkript mit Satzzeiten liefert noch keine Wortausrichtung. |
| Dokument oder KIS-Quittung fehlt | Freigabe-ID, Dokumentrevision und Auftrags- oder Zustellstand vergleichen. | Tatsächliche Datei beziehungsweise Quittung des Zielsystems prüfen. Eine unklare KIS-Zustellung vor einem erneuten Versuch mit dem Ziel abgleichen. |

Vergleichen Sie bei langsamer Darstellung einen kurzen und einen langen synthetischen Text im selben Browser. Bei Uploadabbrüchen oder Zeitüberschreitungen prüfen Sie Proxygrenzen, freien Volumespeicher und Verarbeitungsstand. Notieren Sie Kennungen, Zeiten und bereinigte Fehlerklassen. Weitere Hinweise liefern [Installation](getting-started/installation.md) und [Auftragswarteschlange](advanced/job-queue-system.md).

Originalreferenz zu Upstream-Speakr: [archivierte Upstream-Seite](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/troubleshooting.md).
