# E-Mail, Kontowiederherstellung und interne Hinweise

[English](../../admin-guide/email-setup.md)

Der Klinik-Fork bietet keine Selbstregistrierung, E-Mail-Verifizierung oder Passwort-Reset-Links per E-Mail. SMTP ist für lokale Anmeldung und Kontoverwaltung nicht erforderlich. Wenden Sie sich zur Wiederherstellung an eine berechtigte lokale Identitätsadministration; [Benutzerverwaltung](user-management.md) beschreibt lokale Einmalcodes und die Wiederherstellung von Administratorkonten.

Optionale Verarbeitungshinweise per E-Mail bleiben verfügbar. Sie sind von der Kontowiederherstellung und den internen Workflow-Hinweisen der Klinik getrennt.

## Verarbeitungshinweise per E-Mail

E-Mail ist für jedes Konto standardmässig ausgeschaltet. Für den Versand müssen alle folgenden Bedingungen erfüllt sein:

1. `SMTP_HOST`, `SMTP_USERNAME` und `SMTP_PASSWORD` sind gesetzt.
2. Die Person hat unter **Konto → Einstellungen → Benachrichtigungen** die Option **E-Mail senden, wenn eine Transkription fertig ist** aktiviert.
3. Das Konto hat eine nutzbare E-Mail-Adresse mit `@`, keine Upstream-Platzhalteradresse mit `@placeholder.local`.
4. Ein entsprechender Auftrag wird abgeschlossen oder scheitert endgültig. Erfolgsmeldungen gelten für `transcribe` und `reprocess_transcription`; Fehlermeldungen zusätzlich für `convert` und `merge`.

Die Kontooption erscheint nur, wenn SMTP und Adresse den Versand erlauben. Wird SMTP entfernt, endet der Versand; die gespeicherte Präferenz bleibt erhalten. Zusammenfassungen erzeugen diese Meldungen nicht. Ein abgebrochener Auftrag sendet weder Erfolgs- noch Fehlermeldung.

Jede Nachricht betrifft eine Aufnahme. Betreff und Text können Aufnahmetitel und Benutzername enthalten. Erfolgsmeldungen können Audiolänge und Transkriptionsdauer nennen. Fehlermeldungen können die erste Fehlerzeile enthalten, gekürzt auf 200 Zeichen. Ein Link auf `/recordings/<id>` wird eingefügt, wenn `APP_BASE_URL` gesetzt ist. Dieser Verarbeitungshinweis enthält weder Transkript noch Audioanhang.

Diese Felder verlassen die Anwendung über Mailserver und Empfängerpostfach. Aufnahmetitel und Fehler können schützenswerte Angaben enthalten. Verwenden Sie nur ein freigegebenes institutionelles Mailsystem und eine entsprechende Inhaltsrichtlinie. Ein erfolgreicher synthetischer Mailtest belegt den Transport, nicht die klinische Eignung.

## Konfiguration

Der Betrieb konfiguriert SMTP in der Anwendungsumgebung:

| Variable | Standard | Zweck |
|---|---|---|
| `SMTP_HOST` | leer | Mailserver; erforderlich |
| `SMTP_PORT` | `587` | Numerischer Port |
| `SMTP_USERNAME` | leer | Anmeldename; erforderlich |
| `SMTP_PASSWORD` | leer | Passwort; erforderlich |
| `SMTP_USE_TLS` | `true` | STARTTLS |
| `SMTP_USE_SSL` | `false` | Implizites TLS; hat Vorrang vor STARTTLS |
| `SMTP_FROM_ADDRESS` | `noreply@yourdomain.com` | Absenderadresse |
| `SMTP_FROM_NAME` | `Speakr` | Angezeigter Absendername |
| `APP_BASE_URL` | nicht gesetzt | Externe Instanzadresse für Links |

Die beiden TLS-Schalter akzeptieren `true`, unabhängig von Gross-/Kleinschreibung. Verwenden Sie TLS-Modus und Port des institutionellen Servers. Der Versand authentifiziert sich immer mit Anmeldename und Passwort; ein Relay ohne Anmeldung wird nicht unterstützt. Lassen Sie `SMTP_PORT` für den Standardwert weg, statt die Variable leer zu setzen.

Testen Sie nach einer angewendeten Betriebsänderung mit einer synthetischen Aufnahme und einem Testkonto mit aktivierter Option. Prüfen Sie empfangenen Inhalt und Link. Bei Fehlern helfen Meldungen im Anwendungslog wie `SMTP authentication failed`, `SMTP error sending email` oder `Error sending email`. Erfolgreicher Versand protokolliert Benutzer- und Aufnahme-IDs; die Empfängeradresse wird auf Stufe `DEBUG` protokolliert. Der Mailversand erzeugt selbst kein Auditereignis.

## Interne Workflow-Hinweise

Workflow-Hinweise benötigen kein SMTP. Ihre festen Texte enthalten kein Diktattranskript. Die vier Ereignisse und vorgesehenen Empfänger sind:

| Hinweis | Empfänger |
|---|---|
| **Abnahme angefordert** | Konfigurierte Freigebende |
| **Abnahme abgelehnt** | Zugewiesene Schreibkraft, falls vorhanden |
| **Diktat abgenommen** | Autor und zugewiesene Schreibkraft |
| **Diktat fertiggestellt** | Autor |

Die handelnde Person wird ausgeschlossen. Die Zustellung prüft aktuelle Zuordnung und Sichtbarkeit erneut; Wiederholungen derselben Transition erzeugen keine doppelten Hinweise. Bei einem fehlenden Hinweis sind Workflow-Transition und Empfänger zu prüfen, nicht SMTP. **Benachrichtigungen** werden im Benutzermenü geöffnet.

Der endgültige Abschluss hängt vom Freigabeworkflow und seinem KIS-Kontext ab. Ein Hinweis belegt keine reale KIS-Zustellung. Prüfen Sie **Ausgabe** und die Quittung; ein simuliertes Ergebnis belegt nur den simulierten Pfad. Siehe [Pools und Rechte](group-management.md) und [Auditprotokoll](audit-log.md).

Der Klinik-Fork bietet keine [Webhooks](webhooks.md). Das Öffnen eines lokalen Mailprogramms mit angehängtem Originalaudio bleibt die offene Webparitätslücke WG-EXP-009; Verarbeitungshinweise per E-Mail schliessen sie nicht.
