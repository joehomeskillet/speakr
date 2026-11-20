# Wie arbeite ich in der Klinikedition ohne Ambrose?

[English](../../admin-guide/ambrose.md)

Im Upstream-Projekt bietet Ambrose einen Aufnahmenchat und eine eigene Frageansicht. Im Klinikarbeitsplatz gibt es dafür weder eine Schaltfläche noch einen Chatbereich oder eine aufnahmeübergreifende Fragesuche. Auch das Umbenennen des Assistenten oder ein Upstream-Schalter für die Suche blendet diese Funktionen nicht ein.

Öffnen Sie das betreffende Diktat aus **Aufnahmen** oder der Arbeitsliste. Hören Sie die fragliche Stelle an und vergleichen Sie den Wortlaut mit dem Transkript. Für einen Überblick öffnen Sie **KI-Zusammenfassung**. Diese bezieht sich auf den gespeicherten Text des Diktats. Falls das lokale Textmodell fehlt oder geändert werden muss, wenden Sie sich an die Site-Administration.

Einzelne API-Funktionen je Aufnahme setzen ein ausdrücklich lokales Textmodell voraus. Daraus entsteht keine Chatansicht im Klinikarbeitsplatz. Siehe [Modellwahl](model-configuration.md) und [Vektorsuche](vector-store.md).

[Originalhandbuch von Speakr in der archivierten Fassung](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/ambrose.md)
