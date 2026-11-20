# Häufige Fragen

[English](../faq.md)

**Wer sieht mein Diktat?** Der übliche Autorenbereich umfasst die eigenen Aufnahmen. Poolmitgliedschaft, zusätzliche Rechte, aktive administrative Leserechte und Ausschlüsse bestimmen die Sicht. Bearbeitung, Freigabe, Dokumentausgabe und Originalaudio-Download werden getrennt geprüft. Kontrollieren Sie den Sichtbereich mit einem zweiten zugelassenen Testkonto und derselben synthetischen Kennung.

**Kann ich ein Telefon oder eine Rekorderdatei verwenden?** Öffnen Sie die mobile Weboberfläche über HTTPS. Erlauben Sie für eine Aufnahme das Mikrofon oder wählen Sie eine vorhandene Datei. Das Aufnahmeformat hängt vom Browser ab. Für den Ersttest eignet sich eine WAV-Datei mit bekanntem Satz. DSS/DS2-Wiedergabe setzt eine lesbare Quelle und den mitgelieferten Decoder voraus. Ein Autorencode in der Datei bestätigt keine Sprecheridentität.

**Braucht die Erkennung Internet?** Das Audio geht an den konfigurierten lokalen Erkennungsdienst. Der Browser braucht weiterhin eine Verbindung zur Anwendung. Die Offline-Speicherung mit späterem Wiederherstellen ist offen. Prüfen Sie vor einem Einsatz ohne Netz den [Funktionsvergleich](roadmap.md).

**Wie lang darf eine Aufnahme sein? Welche Sprachen und Sprecher werden erkannt?** Berücksichtigen Sie Modell, Uploadgrenzen, Proxy-Zeitlimit und Speicherplatz. Erproben Sie Länge und Sprache mit einem repräsentativen Test. Sprecherkennungen und Wortzeiten hängen vom Konnektor und seiner Antwort ab. Unbekannte Sprecher müssen fachlich zugeordnet werden. Für Mobil- oder ARM-Hosts sind Containerbuild, Decoder und Modell einzeln zu prüfen. Das Baubeispiel dieser Anleitung gilt für Linux x86-64.

**Kann ich aufnahmeübergreifend Fragen stellen oder eine Cloud-ASR verwenden?** Übergreifende Suche, Freigabelinks und Upstream-Chatfunktionen haben eigene Richtlinien. Eine in der Upstream-Dokumentation sichtbare Funktion begründet kein Recht in der Klinikfassung. Prüfen Sie Freigaben und zugelassene lokale Modelle. Tencent Cloud ASR ist im Klinikmodus ausgeschlossen.

**Wovon hängen Titel und Zusammenfassungen ab?** Textgenerierung setzt ein gesondert konfiguriertes lokales Textmodell und die jeweiligen Freigaben voraus. Das fertige Transkript kann unabhängig davon von einer Person korrigiert werden. Vergleichen Sie generierte Formulierungen mit der gespeicherten Textfassung und dem Originalton.

**Kann ich eine Word-Datei erzeugen?** Die Ausgabe einer Freigabe nimmt TXT- und DOCX-Aufträge an und quittiert das Ergebnis zu einer konkreten Revision. Allein die Quittung belegt weder Dateidownload noch die Anwendung einer Word-Vorlage oder Zustellung ans KIS. Siehe [Grenzen der Dokumentausgabe](roadmap.md).

**Wie sichere ich Daten und stelle sie wieder her?** Sichern Sie Datenbank, Audiospeicher und stabilen Anwendungsschlüssel über den vorgesehenen Betriebsweg. Stellen Sie die Bestandteile zusammen in einer isolierten Instanz wieder her. Prüfen Sie Wiedergabe, Korrektur und Berechtigungen mit einem Testdiktat. Klären Sie Einwilligung und Aufbewahrungsregeln vor der Aufnahme. Siehe [Verwaltung](admin-guide/index.md).

**Was gehört in eine Supportanfrage?** Verwenden Sie einen synthetischen Reproduktionsfall, App-Commit, Browserversion, Modellkennung, Bedienhandlung und angezeigten Status. Entfernen Sie interne Adressen, Konto- und Patientenangaben, Transkripttext, Cookies und Zugangsdaten. Beginnen Sie bei der [Fehlerdiagnose](troubleshooting.md).

Originalreferenz zu Upstream-Speakr: [archivierte Upstream-Seite](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/faq.md).
