# Diktate ohne Vektorindex finden

[English](../../admin-guide/vector-store.md)

Der Vektorspeicher im Originalprojekt dient der semantischen Suche über mehrere Aufnahmen. Im Klinikmodus sind sein Verwaltungsreiter ausgeblendet und die Verwaltungsrouten für Inquire gesperrt. Im Klinikbetrieb gibt es keinen Auftrag zur Neuindizierung des Archivs. Lassen Sie die Upstream-Indizierung für Inquire in der Klinikinstallation ausgeschaltet.

Öffnen Sie die **Arbeitsliste** und grenzen Sie die Einträge über die verfügbaren Pool-, Zuständigkeits- oder Statusfilter ein. Autoren können in **Aufnahmen** auch nach den für ihr Konto sichtbaren Kennungen suchen. Prüfen Sie bei einem fehlenden Diktat zuerst Filter, Poolzuordnung und Leserecht.

Die Zusammenfassung eines berechtigten Diktats verwendet dessen eigene Textfassung und das lokale [Textmodell](model-configuration.md). Sie benötigt keinen aufnahmeübergreifenden Vektorindex. Weitere Datengrenzen beschreibt der [Datenschutzleitfaden](privacy.md).

[Originalhandbuch von Speakr in der archivierten Fassung](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/vector-store.md)
