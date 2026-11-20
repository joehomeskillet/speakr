# Aufnahmesitzungen und unterbrochene Uploads

[English](../../admin-guide/recording-sessions.md)

Der normale Klinikrekorder nimmt im Browser auf und überträgt die fertige Aufnahme in einem Multipart-Upload. Lassen Sie den Tab bis zum Empfang der Bestätigung und der neuen Diktatkennung geöffnet. Eine geschlossene Browsersitzung bietet keine wiederherstellbare Audiokopie. Der Klinikbrowser legt die Aufnahme nicht in IndexedDB ab.

**Fehlt die Bestätigung**, prüfen Sie zuerst die Aufnahmenliste des Autors. War die Quelle eine vorhandene Datei, behalten Sie das Original und wiederholen Sie den Upload nach Wiederherstellung der Verbindung. Ist die Aufnahme noch im offenen Tab, beachten Sie dort die konkrete Fehlermeldung. Ein Upstream-Schalter für Serverblöcke macht den Klinikrekorder nicht absturzsicher.

Beim separaten Upload großer Dateien können serverseitige Uploadsitzungen greifen. Für diesen Weg gelten standardmäßig 16 MiB pro Block, 5 GiB je Benutzer und 24 Stunden bis zum Ablauf einer inaktiven Sitzung. Gleichen Sie bei Abbrüchen die Proxy-Limits mit dem jeweiligen Übertragungsweg ab. Live-Diktat und Geräteimport sind eigene Abläufe. Siehe [Audio aufnehmen](../user-guide/recording.md).

[Originalhandbuch von Speakr in der archivierten Fassung](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/recording-sessions.md)
