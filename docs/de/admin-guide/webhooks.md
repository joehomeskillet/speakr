# Workflow-Mitteilungen und externe Ausgabe

[English](../../admin-guide/webhooks.md)

Persönliche Webhooks sind im Klinikmodus gesperrt. Im Konto gibt es keinen Reiter für eine Rückrufadresse. Ein Upstream-Schalter für Webhooks schaltet den Versand hier nicht frei. Ein Webhook, persönliches API-Token oder eine Push-Mitteilung ersetzt keinen klinischen Zustellnachweis.

Öffnen Sie die internen Benachrichtigungen des jeweils beteiligten Kontos. Unterstützt werden **Abnahme angefordert**, **Abnahme abgelehnt**, **Diktat abgenommen** und **Diktat fertiggestellt**. Die Ereignisse verwenden feste Bezeichnungen und Empfängerregeln. Die Mitteilung enthält keinen diktierten Patiententext. Fehlt sie, prüfen Sie den tatsächlich vollzogenen Statuswechsel und den vorgesehenen Empfänger.

Für eine Ausgabe öffnen Sie **Ausgabe** am Diktat und prüfen das protokollierte Ergebnis. Ein KIS benötigt einen eigenen eingerichteten und abgenommenen Anschluss. Ein simulierter KIS-Lauf belegt den Anwendungsablauf. Importierte Dateien und ihre Belege finden Sie im Importzentrum. [Auditprotokoll](audit-log.md) · [E-Mail](email-setup.md)

[Originalhandbuch von Speakr in der archivierten Fassung](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/webhooks.md)
