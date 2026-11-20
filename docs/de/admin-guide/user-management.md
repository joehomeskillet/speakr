# Benutzerverwaltung

[English](../../admin-guide/user-management.md)

Öffnen Sie in der Verwaltung **Benutzer und Rollen → Konten**. Suchen Sie in der Kontenliste oder filtern Sie nach Status und Anmelderichtlinie. „Keine Anmelderichtlinie“ bezeichnet ein bestehendes Konto ohne Klinikrichtlinie. Daraus lässt sich kein bestimmter Verzeichnisanbieter ableiten. Für neue Konten steht hier **Nur lokal** zur Verfügung. **Poolbezüge** zeigt Mitgliedschaften, **Fachrechte** zeigt fachliche Berechtigungen und ihren Geltungsbereich.

## Lokales Konto anlegen oder wiederherstellen

1. Melden Sie sich als aktive Site-Administration mit Identitätsverwaltungsrecht an. Sensible Kontoänderungen erfordern eine höchstens fünf Minuten zurückliegende Anmeldung. Legen Sie das Konto mit eindeutigem Benutzernamen, E-Mail-Adresse und Anzeigenamen an. Zunächst steht die Aktivierung aus.
2. Wählen Sie **Einmalcode ausstellen**. Der Code wird einmal angezeigt, ist 30 Minuten gültig und nur einmal verwendbar. Übermitteln Sie ihn über den freigegebenen vertraulichen Kanal an den Kontoinhaber. Halten Sie ihn aus Tickets und Bildschirmfotos heraus.
3. Der Klinik-Anmeldeendpunkt unterstützt Aktivierung und Passwortwiederherstellung im abgemeldeten Zustand mit CSRF-Schutz. Die ausgelieferte Anmeldeseite enthält derzeit kein Formular zum Einlösen. Nutzen Sie das freigegebene Aktivierungsverfahren des Standorts. Der Kontoinhaber setzt und bestätigt sein eigenes Passwort und meldet sich anschließend regulär an.
4. Öffnen Sie das Konto erneut und prüfen Sie den aktiven Status. Eine Anmeldesperre können Sie nach Identitätsprüfung im Konto aufheben. Deaktivieren Sie das Konto, wenn Sie den Zugang entziehen möchten. Die letzte aktive lokale Identitätsadministration ist geschützt.

Für ein älteres lokales Konto kann die frisch angemeldete Identitätsadministration über `PUT /admin/users/<id>` mit CSRF exakt `{"adopt_existing_local":true,"expected_version":0}` senden. Dieser Übernahmeweg über die API erhält interne ID, Passwort und Eigentumsbezüge. Prüfen Sie zuvor vorhandene Administrationsrollen. Mit der Aktivierung einer bestehenden Site-Administration werden auditierte Lesezugriffe auf fremde Diktate wirksam. Laden Sie bei einem Versionskonflikt den aktuellen Stand und prüfen Sie ihn vor einer erneuten Änderung.

## Limits, Mitgliedschaften und Fachrechte

Das Speicherlimit wird in MB, das Tokenlimit pro Monat und das monatliche Transkriptionslimit **in Sekunden** angegeben. Null hebt das jeweilige individuelle Limit auf. Den Verbrauch finden Sie in der [Systemstatistik](statistics.md). Poolmitgliedschaft und Poolleitung steuern Routing und Zuweisungen. Erteilen Sie zusätzliche Fachrechte im engsten benötigten Autoren- oder Poolbereich. Eine Verweigerung hat Vorrang. Prüfen Sie die erlaubte Aktion und die Grenze des Bereichs mit synthetischem Material.

Autoren können ihre eigenen Diktate gewöhnlich öffnen, abspielen und bearbeiten. Dokumentexport, Schlussfreigabe, Archivierung und Originaldownload sind eigene Aktionen. Auch für Autoren erfordert der Download des unveränderten Originals das Fachrecht `audio_export`. Technische Administration erlaubt auditierte Lesezugriffe im definierten Umfang. Sie erteilt kein allgemeines Bearbeitungs- oder Freigaberecht für fremde Diktate.

[Originalanleitung von Speakr](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/user-management.md).
