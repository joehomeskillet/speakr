# E-Mail-Einrichtung
<small>[English](../../admin-guide/email-setup.md)</small>

SMTP ist in der Klinikversion keine Startvoraussetzung. Anmeldung, Kontoaktivierung, Passwortrücksetzung und die klinischen Ablaufhinweise benötigen keinen Mailserver. Aktivierung und Rücksetzung laufen über einen lokalen Einmalcode, der ausserhalb der Anwendung übergeben wird. Ein Formular zum Einlösen des Codes fehlt auf der Anmeldeseite noch, und für ein ausgesperrtes Konto der Administration (**Administrationsrolle** oder **Identitätsadministration**) gibt es keinen Wiederherstellungsweg (beides Offene Lücke, siehe [Benutzerverwaltung](user-management.md)). Die einzige E-Mail, die der Code versenden kann, ist die aus Upstream-Speakr übernommene Verarbeitungsbenachrichtigung. Sie ist aus, solange Sie SMTP nicht konfigurieren und ein Benutzer sie nicht selbst einschaltet, und sie wurde nicht für den klinischen Einsatz geprüft.

## Status der E-Mail-Funktionen

| Funktion | Status | In der Klinikversion |
|----------|--------|-----------------|
| Verarbeitungsbenachrichtigung («Transkription fertig» / «Transkription fehlgeschlagen») | Standardmässig aus | Erfordert SMTP und das Einschalten durch jeden Benutzer selbst. Nicht durch den Klinikmodus gesperrt. Nicht für den klinischen Einsatz geprüft. Siehe [Verarbeitungsbenachrichtigung per E-Mail](#verarbeitungsbenachrichtigung-per-e-mail). |
| Klinische Ablaufhinweise (**Abnahme angefordert**, **Abnahme abgelehnt**, **Diktat abgenommen**, **Diktat fertiggestellt**) | Verfügbar | Nur in der Anwendung, nie per E-Mail, kein SMTP nötig. Siehe [Klinische Ablaufhinweise](#klinische-ablaufhinweise). |
| E-Mail-Bestätigung neuer Registrierungen | Nur Upstream | Zusammen mit der Selbstregistrierung entfernt. |
| Passwort zurücksetzen per E-Mail («Passwort vergessen?») | Nur Upstream | Entfernt. Eine Person mit **Administrationsrolle** und **Identitätsadministration** stellt stattdessen einen lokalen Einmalcode aus; ein Webformular zum Einlösen fehlt noch (Offene Lücke). Siehe [Benutzerverwaltung](user-management.md). |
| Selbstregistrierung | Nur Upstream | Entfernt. Konten legt eine Person mit **Administrationsrolle** und **Identitätsadministration** an. |
| Originalaudio als Anhang versenden oder lokalen Mailentwurf öffnen | Offene Lücke | WG-EXP-009 bleibt offen. Kein Codepfad versendet Diktataudio oder Dokumente per E-Mail. Der zulässige Webweg ist ein berechtigter Originalaudio-Download; er erfordert das separate Recht **Originalaudio exportieren**, und derzeit bietet keine Klinikansicht diesen Download an. Das Modul für eine nicht versendete `.eml`-Datei hat keinen Aufrufer. Siehe [Gruppenverwaltung](group-management.md#originalaudio-exportieren). |
| Webhooks und Web-Push | Nur Upstream | Im Klinikmodus gesperrt (Funktionssperren `user_webhooks` und `push_notifications`); kein Betriebsverfahren hebt die Sperre auf. Siehe [Webhooks](webhooks.md). |

## Anmeldung und Wiederherstellung ohne E-Mail

- Der Fork hat keine Routen für Registrierung, E-Mail-Bestätigung oder Passwortrücksetzung. Von den Standard-Anmeldeendpunkten sind im Klinikmodus nur die Anmeldeseite sowie Upstream-SSO-Anmeldung und -Rückruf öffentlich, und die SSO-Routen verweigern dort den Dienst (siehe [Anmeldung und Identitätsanbieter](sso-setup.md)).
- Die Upstream-Variablen `ENABLE_EMAIL_VERIFICATION` und `REQUIRE_EMAIL_VERIFICATION` werden im Fork nirgends gelesen. Sie zu setzen, hat keine Wirkung.
- Konten legt eine Person an, die sowohl die **Administrationsrolle** als auch die **Identitätsadministration** besitzt. Aktivierung und Passwortrücksetzung laufen über einen lokalen Einmalcode aus **Einmalcode ausstellen**. Die Seite zeigt den Code der ausstellenden Person einmal unter **Einmalcode (wird nur jetzt angezeigt)** an; die Anwendung versendet ihn nicht per E-Mail. Ein Rücksetzungscode beendet alle Sitzungen des Kontos und sperrt die Anmeldung mit Passwort, bis er eingelöst ist. Ein Formular zum Einlösen des Codes fehlt auf der Anmeldeseite noch (Offene Lücke). Für das Konto einer anderen Administration (**Administrationsrolle** oder **Identitätsadministration**) lehnt die Weboberfläche den Einmalcode ab (`Administratorkonten werden nur über die Serverkonsole wiederhergestellt. Ein Einmalcode kann hier nicht ausgestellt werden.`), und die Konsolenbefehle zur Wiederherstellung sind nicht registriert; ein ausgesperrtes Konto der Administration lässt sich daher nicht wiederherstellen (Offene Lücke). Die erste Administration wird an der Serverkonsole mit `scripts/clinic_bootstrap_admin.py` eingerichtet. Ablauf und aktuelle Einschränkungen beschreibt die [Benutzerverwaltung](user-management.md).
- Jedes Konto hat weiterhin eine E-Mail-Adresse. Sie dient als Anmeldekennung (das Anmeldefeld heisst **E-Mail**, nimmt aber auch den Benutzernamen an) und als Empfängeradresse der Verarbeitungsbenachrichtigung, falls diese genutzt wird.
- Die Klinik-Vertragsdatei `clinic-spec/config/dev.defaults.json` hält `identity.smtp_required: false`, `identity.self_registration: false`, `identity.email_auto_link: false` und `features.email_exports: false` fest. Die Anwendung liest diese Datei zur Laufzeit für andere Werte (zum Beispiel die Funktionssperren unter `features`), wertet diese vier Schlüssel aber nirgends aus. Das auf dieser Seite beschriebene Verhalten ergibt sich aus dem Code selbst.

## Klinische Ablaufhinweise

Status: Verfügbar. Vier feste deutsche In-App-Hinweise ersetzen für den Diktatablauf die E-Mail: **Abnahme angefordert**, **Abnahme abgelehnt**, **Diktat abgenommen** und **Diktat fertiggestellt**. Sie werden nie per E-Mail versendet, und es ist kein SMTP-Server nötig. Sie werden als Aufträge `clinic_notify` über die bestehende Verarbeitungswarteschlange zugestellt. Benutzer finden sie im Benutzermenü unter **Benachrichtigungen**.

Abnahmehinweise entstehen nur für Diktate, für die auf der Seite **Abnahme** (Feld **Diktat-ID**, Schaltfläche **Richtlinie laden**) eine Abnahmerichtlinie mit Freigebenden (**Berechtigte Freigebende**) gespeichert wurde. **Diktat fertiggestellt** setzt eine endgültige Freigabe voraus, und diese verlangt einen vertrauenswürdigen KIS-Kontext. Das KIS hat den Status Simuliert: Nur mit `CLINIC_PROFILE=staging` wird der synthetische MockKIS-Adapter verwendet, und er ist kein Nachweis einer KIS-Anbindung; sonst wird die endgültige Freigabe abgelehnt (`untrusted_context`), und **Diktat fertiggestellt** tritt nicht auf. Siehe [Gruppenverwaltung](group-management.md#freigabe-freigeben-und-abnahme).

Empfänger, Auslöser, Zustellregeln und die aktuelle Anzeigeeinschränkung: [Webhooks › In-App-Hinweise zum Ablauf](webhooks.md#alternative-in-app-hinweise-zum-ablauf).

## Verarbeitungsbenachrichtigung per E-Mail

Status: Standardmässig aus. Es handelt sich um die unveränderte Upstream-Speakr-Benachrichtigung. Für sie gilt keine Prüfung des Klinikmodus, und sie wurde nicht für den klinischen Einsatz geprüft.

### Wann eine E-Mail versendet wird

Alle folgenden Bedingungen müssen erfüllt sein:

1. `SMTP_HOST`, `SMTP_USERNAME` und `SMTP_PASSWORD` sind alle gesetzt.
2. Die Eigentümerin oder der Eigentümer der Aufnahme hat die Benachrichtigung eingeschaltet. Sie ist für jedes Konto standardmässig aus.
3. Die E-Mail-Adresse des Kontos enthält `@` und endet nicht auf `@placeholder.local` (die Upstream-Platzhalteradresse für SSO-Konten ohne Adresse).
4. Der Auftrag ist ein Transkriptionsauftrag:
   - E-Mail «fertig»: Ein Auftrag vom Typ `transcribe` oder `reprocess_transcription` wurde abgeschlossen, und die Aufnahme hat nicht den Status `FAILED`.
   - E-Mail «fehlgeschlagen»: Ein Auftrag vom Typ `transcribe`, `reprocess_transcription`, `convert` oder `merge` ist endgültig fehlgeschlagen, oder eine Transkription endete ohne Text oder wurde durch das monatliche Transkriptionslimit gestoppt.

Für Zusammenfassungen, Hintergrundaufträge und vom Benutzer abgebrochene Aufträge wird keine E-Mail versendet. Jede Aufnahme löst eine eigene E-Mail aus; ein Import von fünfzig Dateien kann also fünfzig E-Mails erzeugen. Auch Diktate, die über den Klinikimport eingehen, erzeugen Standardaufträge `transcribe` für eine Arbeitsaufnahme der diktierenden Person; die Benachrichtigung gilt also auch für sie.

### Inhalt der E-Mail

- Betreff `Transcription ready: <Aufnahmetitel>` oder `Transcription failed: <Aufnahmetitel>`. Ohne Titel steht im Betreff «Untitled recording».
- Ein fester englischer Text mit Benutzername und Aufnahmetitel; die E-Mail «fertig» nennt zusätzlich Audiodauer und Transkriptionsdauer, sofern bekannt.
- In der E-Mail «fehlgeschlagen»: die erste Zeile des Fehlertexts, gekürzt auf 200 Zeichen, sofern vorhanden.
- Ein Link auf `<APP_BASE_URL>/recordings/<id>`, nur wenn `APP_BASE_URL` gesetzt ist.

Diese Inhalte verlassen das System zum SMTP-Server und zum Postfach der empfangenden Person. Aufnahmetitel können eine Patientin oder einen Patienten identifizieren. Lassen Sie die SMTP-Variablen leer, solange Ihre Organisation diese Inhalte nicht für den E-Mail-Versand freigegeben hat; falls Sie sie nutzen, verwenden Sie den eigenen Mailserver der Institution. Siehe [Datenschutz](privacy.md).

Der Versand schreibt kein Audit-Ereignis. Das Anwendungsprotokoll enthält «Email sent successfully» und «Sent completion email for recording <id> to user <id>» (beziehungsweise «Sent failure email …»). Die Empfängeradresse wird nur auf der Protokollstufe `DEBUG` protokolliert.

### Wo Benutzer die Benachrichtigung einschalten

Benutzer schalten die E-Mail selbst ein: Benutzermenü **Konto** (`/account`), Register **Einstellungen**, Abschnitt **Benachrichtigungen**, Option **E-Mail senden, wenn eine Transkription fertig ist**.

Der Abschnitt erscheint nur, wenn die Bedingungen 1 und 3 oben erfüllt sind. Wird SMTP später entfernt, bleibt eine gespeicherte Einstellung erhalten, es wird aber keine E-Mail versendet.

### Konfiguration

Setzen Sie die Variablen in der Umgebung der Anwendung (Vorlage: [`config/env.email.example`](https://github.com/joehomeskillet/speakr/blob/main/config/env.email.example), Klinik-Fork) und starten Sie die Anwendung neu.

| Variable | Standardwert | Bedeutung |
|----------|--------------|-----------|
| `SMTP_HOST` | (leer) | Hostname des SMTP-Servers. Erforderlich. |
| `SMTP_PORT` | `587` | SMTP-Port. |
| `SMTP_USERNAME` | (leer) | SMTP-Anmeldename. Erforderlich. |
| `SMTP_PASSWORD` | (leer) | SMTP-Passwort. Erforderlich. |
| `SMTP_USE_TLS` | `true` | STARTTLS nach dem Verbindungsaufbau (üblicherweise Port 587). |
| `SMTP_USE_SSL` | `false` | Direktes TLS (üblicherweise Port 465). Hat Vorrang vor `SMTP_USE_TLS`. |
| `SMTP_FROM_ADDRESS` | `noreply@yourdomain.com` | Absenderadresse. |
| `SMTP_FROM_NAME` | `Speakr` | Anzeigename des Absenders. |
| `APP_BASE_URL` | (nicht gesetzt) | Externe Adresse der Instanz, ohne nachfolgenden Pfad. Wird für den Link in der E-Mail verwendet. |

```bash
SMTP_HOST=<smtp-host>
SMTP_PORT=587
SMTP_USE_TLS=true
SMTP_USE_SSL=false
SMTP_USERNAME=<smtp-user>
SMTP_PASSWORD=<secret>
SMTP_FROM_ADDRESS=<sender-address>
SMTP_FROM_NAME=Speakr
APP_BASE_URL=https://<your-host>
```

Hinweise:

- `SMTP_USE_TLS` und `SMTP_USE_SSL` sind nur beim Wert `true` (Gross-/Kleinschreibung beliebig) eingeschaltet. Sind beide aus, ist die Verbindung unverschlüsselt. Betreiben Sie sie nicht so.
- Die E-Mail wird von einem Hintergrundprozess der Warteschlange ohne Webanfrage versendet; den Link gibt es deshalb nur mit `APP_BASE_URL`. Er verwendet den Standardpfad `/recordings/<id>` mit der ID der Standard-Aufnahmezeile.
- Ist `SMTP_PORT` gesetzt, muss der Wert eine Zahl sein. Ein leerer oder nicht numerischer Wert verhindert den Start der Anwendung, auch wenn SMTP nicht genutzt wird; lassen Sie die Variable dann weg.
- Die Anwendung meldet sich beim SMTP-Server immer mit `SMTP_USERNAME` und `SMTP_PASSWORD` an; ein Relay ohne Anmeldung wird nicht unterstützt.
- Die Vorlage enthält aktive Beispielwerte für Gmail und auskommentierte Beispiele öffentlicher Anbieter. Übernehmen Sie nur die benötigten Zeilen, mit den Werten Ihres eigenen Mailservers.
- `APP_BASE_URL` wird auch ausserhalb der E-Mail gelesen, zum Beispiel für absolute Links in heruntergeladenen Dokumenten und für die Startwarnung bei einer https-Adresse mit ausgeschaltetem `SESSION_COOKIE_SECURE`.

### Fehlerbehebung

- Die Option erscheint nicht auf der Kontoseite: Eine der Variablen `SMTP_HOST`, `SMTP_USERNAME`, `SMTP_PASSWORD` fehlt, oder die Kontoadresse ist nicht zustellbar (siehe Bedingung 3).
- E-Mails kommen nicht an: Suchen Sie im Anwendungsprotokoll nach «SMTP authentication failed: …», «SMTP error sending email: …» oder «Error sending email: …» sowie nach den Warnungen «Completion email for job <id> failed: …» oder «Failure email for job <id> failed: …». Prüfen Sie, ob der SMTP-Port vom Anwendungshost aus erreichbar ist.
- Die E-Mail enthält keinen Link: `APP_BASE_URL` ist nicht gesetzt.
- E-Mails landen im Spam: Verwenden Sie eine Absenderadresse Ihrer eigenen Domain mit SPF und DKIM auf Ihrem Mailserver.

## Upstream-Referenz

Upstream-Speakr-Dokumentation (fixiertes Original, für die Klinikversion nicht gültig): [Email Verification & Password Reset](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/email-setup.md). Sie beschreibt E-Mail-Bestätigung, Passwortrücksetzung, Registrierungseinstellungen und öffentliche Mailanbieter; nichts davon gilt hier.

---

Weiter: [Anmeldung und Identitätsanbieter](sso-setup.md) →
