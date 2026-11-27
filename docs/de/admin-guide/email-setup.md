# E-Mail und Benachrichtigungen

[English](../../admin-guide/email-setup.md)

Der Klinik-Fork bietet keinerlei E-Mail-Funktion. Es gibt keine SMTP-Konfiguration, keine Verarbeitungs- oder Benachrichtigungsmails, keinen EML-Download und keinen Mailadapter. Konfigurieren Sie für den Klinik-Build keine `SMTP_*`-Variablen; sie haben hier keine unterstützte Funktion.

Benachrichtigungen erscheinen ausschliesslich in der Anwendung. **Benachrichtigungen** werden im Benutzermenü geöffnet. Der Fork bietet ausserdem keine Selbstregistrierung, E-Mail-Verifizierung oder Passwort-Reset-Links per E-Mail. Wenden Sie sich zur Kontowiederherstellung an eine berechtigte lokale Identitätsadministration; [Benutzerverwaltung](user-management.md) beschreibt lokale Einmalcodes und die Wiederherstellung von Administratorkonten.

## Interne Workflow-Hinweise

Workflow-Hinweise bleiben innerhalb der Anwendung. Ihre festen Texte enthalten kein Diktattranskript. Die vier Ereignisse und vorgesehenen Empfänger sind:

| Hinweis | Empfänger |
|---|---|
| **Abnahme angefordert** | Konfigurierte Freigebende |
| **Abnahme abgelehnt** | Zugewiesene Schreibkraft, falls vorhanden |
| **Diktat abgenommen** | Autor und zugewiesene Schreibkraft |
| **Diktat fertiggestellt** | Autor |

Die handelnde Person wird ausgeschlossen. Die Zustellung prüft aktuelle Zuordnung und Sichtbarkeit erneut; Wiederholungen derselben Transition erzeugen keine doppelten Hinweise. Bei einem fehlenden Hinweis sind Workflow-Transition und Empfänger zu prüfen. Siehe [Pools und Rechte](group-management.md) und [Auditprotokoll](audit-log.md).

Der Klinik-Fork bietet auch keine [Webhooks](webhooks.md). Hinweise werden an kein externes System gesendet.
