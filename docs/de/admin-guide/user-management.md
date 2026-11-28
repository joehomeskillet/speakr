# Benutzerverwaltung

[English](../../admin-guide/user-management.md)

Bei aktiviertem `CLINIC_MODE` öffnen Sie **Verwaltung → Konten und Pools → Benutzer und Rollen** (`/admin#/benutzer`). Diese Seite ersetzt die ursprüngliche Benutzerverwaltung. Sie verbindet **Konten**, die schreibgeschützten **Poolbezüge** und **Fachrechte**. Die folgenden Bezeichnungen entsprechen der Klinikoberfläche.

## Rollen und Zugriff

| Aufgabe | Erforderlicher Zugriff |
| --- | --- |
| Verwaltung öffnen und Kontenliste lesen | **Administrationsrolle** |
| Konten anlegen oder ändern, Einmalcodes ausstellen, Anmeldesperren aufheben oder Bestandskonten übernehmen | **Administrationsrolle** und **Identitätsadministration**, aktives Klinikkonto, kein ausstehender Passwortwechsel, gültige lokale Sitzung und Anmeldung innerhalb der letzten 300 Sekunden |
| Fachrechte ändern oder Pools verwalten | Aktive Administration; Identitätsadministration allein verleiht diese Befugnis nicht |
| Zuweisungen innerhalb eines Schreibkräfte-Pools koordinieren | **Leitung**; diese Rolle öffnet `/admin` nicht |

Eine aktive Administration kann Klinikdiktate ansehen, abspielen und deren Korrekturen lesen. Dieser Zugriff erlaubt weder Bearbeitung noch Dokumentexport, endgültige Freigabe, Archivierung oder Originalaudioexport fremder Diktate. **Identitätsadministration** ergänzt keinen Diktatzugriff. [Pools und Berechtigungen](group-management.md) erläutert Geltungsbereiche und die Ausnahme der administrativen Lesezugriffe von Verweigerungseinträgen.

Bei einer verweigerten sensiblen Kontoänderung melden Sie sich ab, erneut an und wiederholen die Änderung. Fehlende Rollen ergeben `403 unauthorized`; ein abgelaufenes Zeitfenster für die frische Anmeldung ergibt `403 reauth_required`. Nach einem Versionskonflikt laden und prüfen Sie den aktuellen Kontostand vor dem erneuten Speichern.

## Erste Administration anlegen

Führen Sie das Bootstrap-Skript einmal in der Anwendungsumgebung mit derselben Datenbank und Bereitstellungskonfiguration wie die Anwendung aus:

```bash
python scripts/clinic_bootstrap_admin.py --username <username> --email <email-address>
```

Das Passwort stammt aus `--password-stdin`, der mit `--password-env` bezeichneten Umgebungsvariable (Standard `CLINIC_BOOTSTRAP_PASSWORD`) oder einer verdeckten Terminaleingabe. Geben Sie kein Passwort als Kommandozeilenargument an. Das Skript erstellt ein aktives, ausschliesslich lokales Konto mit beiden Administrationskennzeichen. Es erteilt keine zusätzlichen Fachrechte. Ein Bootstrap-Marker oder eine vorhandene Identitätsadministration bewirkt, dass ein erneuter Aufruf die Konten unverändert lässt.

Verwenden Sie die ursprünglichen Variablen `ADMIN_USERNAME`, `ADMIN_EMAIL` und `ADMIN_PASSWORD` nicht als Klinik-Bootstrap. Das ursprüngliche Docker-Skript erstellt ein Konto ohne Klinik-Kontorichtlinie; dieses kann sich erst nach ausdrücklicher Übernahme anmelden. Benutzername und E-Mail-Adresse sind bereits belegt; verwenden Sie andere Werte für die erste Klinik-Identitätsadministration.

Der Code definiert ausserdem Konsolenbefehle unter `flask clinic-identity`, registriert diese beim Anwendungsstart aber nicht. Gehen Sie nicht davon aus, dass diese Befehle in der bereitgestellten Anwendung verfügbar sind.

## Konten und Einmalcodes

1. Wählen Sie **Konto anlegen**. Geben Sie eindeutigen Benutzernamen, E-Mail-Adresse und optional Anzeigenamen ein. Das Konto beginnt mit **Aktivierung ausstehend**, **Nur lokal**, ohne Passwort und ohne Administrationsrolle.
2. Wählen Sie **Einmalcode ausstellen**. Ein ausstehendes Konto erhält einen Aktivierungscode; ein aktives lokales Konto erhält einen Code zum Zurücksetzen des Passworts.
3. Übermitteln Sie den Code über den freigegebenen vertraulichen Kanal des Standorts. Er wird einmal angezeigt, läuft nach 30 Minuten ab und ist einmal verwendbar. Der Server speichert seinen Hash. Ein neuer Code macht ältere offene Codes ungültig.
4. Die kontoinhabende Person löst den Code ein, setzt und bestätigt ein Passwort und meldet sich danach normal an. Prüfen Sie, dass das Konto nun **Aktiv** anzeigt.

**Aktuelle Oberflächenlücke:** Die Anmeldeseite hat kein Formular zum Einlösen von Codes. Der abgemeldete, CSRF-geschützte Endpunkt `POST /login` akzeptiert `purpose` (`activate_local` oder `reset_local`), `code`, `new_password` und `confirmation`. Bei Erfolg antwortet er mit `204`; der Browser bleibt abgemeldet. Verwenden Sie das freigegebene Aktivierungsverfahren des Standorts; das Anlegen allein macht das Konto nicht nutzbar.

Das Ausstellen eines Rücksetzcodes beendet bestehende Sitzungen und verhindert bis zum Einlösen die Anmeldung mit dem alten Passwort. Stellen Sie vor dem Ausstellen sicher, dass die kontoinhabende Person das Aktivierungsverfahren abschliessen kann. Deaktivierte Konten und Konten ohne lokale Anmelderichtlinie können keinen lokalen Code erhalten.

Die Weboberfläche verweigert Wiederherstellungscodes für Administratorkonten; der Server verhindert ausdrücklich die Wiederherstellung eines anderen Kontos mit einem der beiden Administrationskennzeichen. Der definierte Konsolenbefehl zur Wiederherstellung wird beim Anwendungsstart nicht registriert. Behandeln Sie die Passwortwiederherstellung für Administratorkonten als Betreiberverfahren mit einem nachgewiesenen Wiederherstellungsweg; die Weboberfläche stellt keinen bereit. Eine Identitätsadministration kann weiterhin die Anmeldesperre eines Administratorkontos aufheben. Halten Sie eine weitere nutzbare lokale Identitätsadministration bereit.

## Passwörter und Anmeldesperren

Passwörter müssen 15–128 Unicode-Codepunkte lang sein und dürfen nicht auf der Sperrliste stehen. Es gibt keine Zusammensetzungsregel; Passwörter werden nicht beschnitten. `CLINIC_PASSWORD_BLOCKLIST_FILE` kann standortspezifische Einträge ergänzen. Neue Hashes verwenden `scrypt:131072:8:1`; ein gültiger älterer bcrypt-Hash wird nach erfolgreicher Anmeldung aktualisiert.

Administratoren setzen keine Passwörter anderer Personen. Benutzer können ihr eigenes Passwort auf der Kontoseite mit dem aktuellen Passwort ändern. Die serverseitige Mindestlänge von 15 Zeichen gilt auch dort, wo ein ursprünglicher Hinweis noch acht nennt. Ein Passwortwechsel macht offene Codes und andere Sitzungen ungültig.

`login_max_failed_attempts` hat den Standard 5, `login_lockout_minutes` den Standard 15. Ein Konto wird pro Clientadresse beim konfigurierten Grenzwert und kontoweit beim Vierfachen dieses Werts gesperrt. Der Versuchsgrenzwert 0 schaltet die Sperre aus. Fehlgeschlagene Anmeldungen verwenden dieselbe allgemeine Meldung. Im Kontoeditor hebt **Anmeldesperre aufheben** nach Identitätsprüfung eine bestehende Sperre auf.

## Bearbeiten, deaktivieren und Limits

Suchen oder filtern Sie die Kontenliste und wählen Sie **Bearbeiten**. Der Editor zeigt Profilfelder, Limits, **Zuletzt aktiv**, Verbrauch, Anmelderichtlinie und Administrationskennzeichen. **Übernehmen** speichert und lässt den Editor offen; **Speichern und schließen** speichert und schliesst ihn. **Systemaktivität dieses Kontos** öffnet das auf dieses Konto gefilterte System-Auditprotokoll.

Das Entfernen der **Administrationsrolle** entfernt auch **Identitätsadministration**. Änderungen an einem der Kennzeichen beenden die Sitzungen des Zielkontos. Die letzte aktive, lokal nutzbare Identitätsadministration ist vor Deaktivierung, Rollenentzug und Wechsel zu ausschliesslicher Entra-Anmeldung geschützt.

Verwenden Sie **Deaktivieren**, um Zugriff zu entziehen. Es beendet Sitzungen und macht offene Codes ungültig; Diktate, Verlauf und Auditzeilen bleiben erhalten. **Reaktivieren** stellt **Aktiv** wieder her oder **Aktivierung ausstehend**, wenn kein Passwort besteht. Der Klinik-Fork bietet keine Kontolöschung an.

| Feld | Einheit und Aufheben des Limits |
| --- | --- |
| **Speicherlimit (MB)** | Megabyte; leer beim Anlegen verwendet `default_storage_quota_mb`, leer beim Bearbeiten entfernt das individuelle Limit |
| **Tokenlimit pro Monat** | Monatliche Textmodell-Tokens; leer entfernt das Limit |
| **Transkriptionslimit pro Monat (Sekunden)** | Monatliche Transkriptionssekunden, nicht Minuten; leer entfernt das Limit |

Das Formular akzeptiert positive ganze Zahlen bis 2147483647. Null ist keine gültige Formulareingabe; die Konto-API akzeptiert auch Null zum Aufheben eines individuellen Limits. Prüfen Sie den tatsächlichen Verbrauch unter [Systemstatistik](statistics.md). Eine Budgetverweigerung beweist keine fehlende Verarbeitungskapazität.

## Anmelderichtlinie und Bestandskonten

**Nur lokal** ist die unterstützte Richtlinie für neue Konten. **Lokal und Entra** und **Nur Entra** bezeichnen vorbereitete Identitätsrichtlinien; Entra ist deaktiviert. **Nur Gerät** erlaubt keine lokale Passwortanmeldung. **Keine Anmelderichtlinie** bedeutet, dass keine Klinik-Kontorichtlinie besteht, nicht dass ein Verzeichnisanbieter erkannt wurde. Es gibt keine Verknüpfung anhand der E-Mail-Adresse. Siehe [Anmeldung und Identitätsanbieter](sso-setup.md).

Für ein lokales Bestandskonto ohne Klinikrichtlinie kann eine frisch angemeldete Identitätsadministration diese CSRF-geschützte Anfrage senden:

```http
PUT /admin/users/<id>
Content-Type: application/json

{"adopt_existing_local":true,"expected_version":0}
```

Der Rumpf muss genau diese beiden Schlüssel enthalten. Die Übernahme erfordert einen unterstützten lokalen Passwort-Hash und keine externe Identität oder Verknüpfungsanfrage. Sie erhält interne ID, Passwort, Eigentümerschaft und vorhandene Administrationsrolle, erstellt eine aktive lokale Richtlinie und ergänzt keine Identitätsadministration. Prüfen Sie bestehende Administrationskennzeichen zuerst: Das Aktivieren einer Administration ermöglicht deren diktatübergreifende Lesezugriffe. Bitten Sie die kontoinhabende Person, das alte Passwort nach der Anmeldung zu ändern. Es gibt keine Schaltfläche zur Übernahme.

## Poolbezüge und Fachrechte

**Poolbezüge** zeigt die Autoren- und Schreibkräfte-Pools des gewählten Kontos; Mitgliedschaften werden auf den Poolseiten bearbeitet. Unter **Fachrechte** wählen Sie Benutzer, **Geltungsbereich** (**Eigene Diktate**, einen Autoren-**Pool** oder **Alle Diktate**), **Wirkung** (**Erlauben** oder **Verweigern**) und ausdrückliche Aktionen, dann speichern Sie. **Entziehen** widerruft einen Eintrag. Bei veraltetem Stand laden Sie neu.

Autoren können ihre eigenen Diktate ansehen, abspielen und bearbeiten, sofern keine passende Verweigerung gilt. Andere Aktionen benötigen ausdrückliche Rechte, darunter Dokumentexport, endgültige Freigabe, Archivierung und **Originalaudio exportieren** (`audio_export`). Originalaudioexport ist vom Textexport getrennt und nicht im administrativen Abspielzugriff enthalten. Poolmitgliedschaft oder Zuweisung allein erteilt keine Bearbeitung. Lesen Sie [Pools und Berechtigungen](group-management.md), bevor Sie zusätzliche Geltungsbereiche freigeben.

## Audit und entfernte Funktionen

Für Kontoanlage, Änderungen, Rollenänderungen, Entsperrungen, Einmalcode-Ausstellung und Passwortwechsel bestehen Audit-Ereignistypen. Kontoänderungen zeichnen Feldnamen auf, keine Passwort- oder Codewerte. Pool- und Fachrechteänderungen verwenden den separaten, nur ergänzbaren Poolverlauf. Der Bootstrap schreibt eine Anwendungslogzeile. Konfiguration und Abdeckungsgrenzen stehen unter [Auditprotokoll](audit-log.md).

Der Klinik-Fork bietet keine Selbstregistrierung, E-Mail-Verifizierung, Passwortwiederherstellung per E-Mail, öffentlichen Freigabelinks oder internen Freigaben. Die allgemeine API v1 wurde entfernt; Tokens bleiben dem Importagenten vorbehalten. Diese ursprünglichen Funktionen lassen sich nicht durch Kontoberechtigungen aktivieren.
