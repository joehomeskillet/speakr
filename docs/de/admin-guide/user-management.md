# Benutzerverwaltung
<small>[English](../../admin-guide/user-management.md)</small>

Diese Seite beschreibt die Kontoverwaltung in der Klinikversion, also mit `CLINIC_MODE` auf `1`, `true`, `yes` oder `on`. Ohne Klinikmodus gibt es keine der hier beschriebenen Klinikprüfungen, Routen und Seiten, und es gilt das Verhalten von Upstream-Speakr.

Im Klinikmodus wird der Upstream-Reiter **User Management** von `/admin` nicht angezeigt. Konten werden auf der Klinikseite **Benutzer und Rollen** verwaltet. Lokale Konten sind der einzige Anmeldeweg, der funktioniert. Niemand in der Verwaltung setzt oder sieht Passwörter: Eine Identitätsadministration (mit **Administrationsrolle**) legt ein Konto an und stellt einen Einmalcode aus.

Dokumentation dieses Themas im Original-Speakr (Upstream, fixiertes Original, gilt nicht für die Klinikversion): [User Management (Upstream)](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/user-management.md).

Die Bezeichnungen der Klinikverwaltung gibt es nur auf Deutsch. Sie werden hier wörtlich und fett zitiert.

## Statusübersicht

| Funktion | Status |
|---|---|
| Lokale Konten: anlegen, bearbeiten, deaktivieren, reaktivieren, Einmalcodes | Verfügbar |
| Erste Administration (**Administrationsrolle** und **Identitätsadministration**) über `scripts/clinic_bootstrap_admin.py` | Verfügbar |
| Fachrechte, einschliesslich Freigabe und Export des Originalaudios | Verfügbar |
| Formular auf der Anmeldeseite zum Einlösen eines Einmalcodes | Offene Lücke (nur Serverendpunkt, kein Formular) |
| Einmalcode oder Wiederherstellung über die Konsole für ein Konto mit **Administrationsrolle** oder **Identitätsadministration** | Offene Lücke (siehe unten) |
| Übernahme eines bestehenden Kontos ohne Klinik-Kontorichtlinie | Verfügbar (nur über die API, kein Bedienelement) |
| Entra-Anmeldung und -Verknüpfung (**Lokal und Entra**, **Nur Entra**) | Vorbereitet, deaktiviert (nur synthetische lokale Tests, kein echter Mandant, keine klinische Freigabe) |
| Verzeichnisabgleich (**Verzeichnis**), LDAP, Windows-Einmalanmeldung | Vorbereitet, deaktiviert |
| Auslesen der lokalen Windows-Konten eines Arbeitsplatzes (WG-ADM-009) | Offene Lücke |
| Von der Administration gesetzte Passwörter, Löschen von Konten, Berechtigung für öffentliches Teilen, **Can create groups**, OIDC-Einmalanmeldung | Nur Upstream (nur im Original-Speakr) |

## Wer was darf

Die Klinikversion trennt vier Arten von Rechten. Keine davon schliesst eine andere ein.

**Administrationsrolle** ist die globale Administration. Sie ist das Administrationskennzeichen des Speakr-Kontos. Sie öffnet `/admin` und die Systemseiten (siehe [Systemeinstellungen](system-settings.md), [Systemstatistik](statistics.md) und [Auditprotokoll](audit-log.md)). Eine *aktive globale Administration* ist ein Konto mit dieser Rolle und dem Status **Aktiv**.

**Identitätsadministration** ist die lokale Identitätsverwaltung. Sie ist ein Kennzeichen der Klinik-Kontorichtlinie. Sie wirkt nur zusammen mit der **Administrationsrolle**: Ohne diese Rolle kann das Konto `/admin` gar nicht öffnen.

**Leitung** ist die Poolleitung eines **Schreibkräfte-Pools**. Die zusätzlichen Befugnisse einer Leitung gelten nur für den geleiteten Pool, und die Leitungsrolle öffnet `/admin` nicht. Leitungen setzt eine aktive globale Administration auf den Poolseiten, nicht hier; siehe [Gruppenverwaltung](group-management.md).

**Fachrechte** legen fest, was ein Konto mit Diktaten tun darf, zum Beispiel **Freigeben** und **Originalaudio exportieren**. Sie werden im Bereich **Fachrechte** der Seite **Benutzer und Rollen** gesetzt. Zwei Fachrecht-Aktionen besitzt standardmässig niemand, auch nicht die globale Administration: **Freigeben** und **Originalaudio exportieren**; siehe [Freigaberecht](index.md#freigaberecht) und [Recht zum Export des Originalaudios](index.md#recht-zum-export-des-originalaudios) im Administrationshandbuch.

| Aufgabe | Erforderlich |
|---|---|
| **Benutzer und Rollen** öffnen und die Kontenliste lesen | **Administrationsrolle** |
| Konten anlegen, bearbeiten, deaktivieren und reaktivieren, Rollen ändern, Einmalcodes ausstellen, Anmeldesperre aufheben, Konto übernehmen | **Administrationsrolle** und **Identitätsadministration**, Status **Aktiv**, kein ausstehender Passwortwechsel, eine lokale Sitzung mit aktueller Sicherheitsversion und eine Anmeldung innerhalb der letzten 300 Sekunden |
| Fachrechte ändern | Aktive globale Administration. **Identitätsadministration** ist weder nötig noch ausreichend. |
| Fachrechteliste eines Kontos lesen (API) | Aktive globale Administration, aktive Identitätsadministration oder das Konto selbst |
| Poolleitung setzen oder entfernen | Aktive globale Administration |
| Verzeichnisabgleich (**Verzeichnis**) | **Administrationsrolle** und **Identitätsadministration** (vorbereitet, deaktiviert) |
| Jedes Klinikdiktat ansehen und abspielen, seine Korrekturen lesen, die Diktatübersicht der Administration nutzen | Aktive globale Administration. Das Öffnen eines Transkripts oder Audios über die Administrationsrouten und jede fremde Zeile in der Arbeitsliste werden protokolliert, die Metadatenübersicht nicht. |
| Diktat eines anderen Kontos bearbeiten, exportieren, freigeben, Originalaudio exportieren, archivieren oder löschen | Ein passendes Fachrecht. Die **Administrationsrolle** gewährt diese Aktionen nicht. |

Der Server lehnt eine Kontoänderung ohne **Identitätsadministration** mit `403 unauthorized` ab (`Identitätsadministration erforderlich.`); die Seite zeigt `Keine Berechtigung für diese Aktion.`. Eine Änderung mehr als 300 Sekunden nach der letzten lokalen Anmeldung wird mit `403 reauth_required` abgelehnt; die Seite zeigt dann `Bitte erneut anmelden und die Änderung nochmals ausführen.`. Melden Sie sich ab, erneut an und wiederholen Sie die Änderung.

Der Bereich **Fachrechte** zeigt den Hinweis `Der Zugriff auf ein konkretes Diktat bleibt zusätzlich an Benutzer, Pools und Objektberechtigungen gebunden. Eine Identitätsrolle gibt keinen Diktatzugriff.`

## Erste Administration einrichten

Eine neue Klinikinstallation hat kein anmeldefähiges Konto. Richten Sie die erste Administration einmalig an der Serverkonsole mit `scripts/clinic_bootstrap_admin.py` ein ([Quelle, Klinik-Fork](https://github.com/joehomeskillet/speakr/blob/main/scripts/clinic_bootstrap_admin.py)). Führen Sie das Skript in der Umgebung der Anwendung aus, zum Beispiel im laufenden Anwendungscontainer, mit derselben Konfiguration wie die Anwendung, einschliesslich `CLINIC_MODE`. Das Skript selbst prüft `CLINIC_MODE` nicht.

```bash
python scripts/clinic_bootstrap_admin.py --username <benutzername> --email <e-mail-adresse> [--name "<anzeigename>"]
```

Das Passwort ist nie ein Befehlszeilenargument. Das Skript liest es aus der ersten zutreffenden Quelle:

1. `--password-stdin`: die erste Zeile der Standardeingabe.
2. Die mit `--password-env` benannte Umgebungsvariable, standardmässig `CLINIC_BOOTSTRAP_PASSWORD`, sofern sie gesetzt und nicht leer ist.
3. Im Terminal: eine verdeckte Eingabe (`Initial administrator password:`, danach `Repeat password:`).

Das Passwort wird nie ausgegeben oder protokolliert. Es muss der [Passwortrichtlinie](#passworter) entsprechen.

Das Skript legt fehlende Identitätstabellen an. Danach legt es ein Konto mit **Administrationsrolle** und **Identitätsadministration** an, mit dem Status **Aktiv**, der Anmelderichtlinie **Nur lokal**, ohne klinische Rolle und ohne Fachrecht. Ein Markierungseintrag sorgt dafür, dass der Schritt nur einmal wirkt: Sobald eine Markierung oder irgendeine Identitätsadministration existiert, ändert ein weiterer Lauf nichts.

| Ausgabe | Exitcode |
|---|---|
| `First identity administrator '<username>' created.` | 0 |
| `Already initialised; nothing changed.` | 0 |
| `Error: no password supplied (environment variable, --password-stdin or terminal).` (auch wenn die beiden Eingaben verschieden sind) | 2 |
| `Error: password rejected (<code>).` mit `too_short`, `too_long`, `blocklisted` oder `invalid_type` | 2 |
| `Error: account data rejected (<code>).` mit `invalid_username`, `invalid_email` oder `duplicate` | 2 |
| `Error: bootstrap failed.` | 1 |

Der Benutzername ist 2 bis 20 Zeichen lang, die E-Mail-Adresse höchstens 120 Zeichen. Die Einrichtung schreibt die Logzeile `clinic identity: first identity administrator <id> created` und keine Zeile ins Auditprotokoll.

**Verwenden Sie für die Klinikversion nicht die Upstream-Variablen `ADMIN_USERNAME`, `ADMIN_EMAIL` und `ADMIN_PASSWORD`.** Der Docker-Einstiegspunkt führt dann `scripts/docker_create_admin.py` aus. Dieses Skript legt ein Konto mit Administrationskennzeichen, aber ohne Klinik-Kontorichtlinie an. Im Klinikmodus kann sich ein solches Konto erst anmelden, nachdem eine Identitätsadministration es übernommen hat (siehe [Bestehende Konten ohne Kontorichtlinie](#bestehende-konten-ohne-kontorichtlinie)). Benutzername und E-Mail-Adresse sind dann vergeben, die Einrichtung braucht also andere.

Die Konsolenbefehle `flask clinic-identity bootstrap` und `flask clinic-identity recovery` sind im Code definiert, die Anwendung registriert sie beim Start aber nicht; das tun nur Tests. In einer betriebenen Instanz stehen sie nicht zur Verfügung.

## Die Seite Benutzer und Rollen

Im Klinikmodus zeigt `/admin` die Navigation **Verwaltung**. **Benutzer und Rollen** steht in der Gruppe **Konten und Pools**, neben **Schreibkräfte-Pools**, **Diktierende und Autorenpools** und **Verzeichnis**. Sie ist die Startseite von `/admin`; der Direktlink lautet `/admin#/benutzer`. Beim Laden von `/admin` wird jede Seite geprüft. Eine Seite, deren API das Konto ablehnt, wird ausgeblendet und im Hinweis `Für dieses Konto nicht freigegeben: …` aufgeführt.

Die Seite hat drei Bereiche:

- **Konten**: die Kontenliste mit dem Bearbeitungsformular.
- **Poolbezüge**: die Autorenpools und Schreibkräfte-Pools eines gewählten Kontos, nur lesend.
- **Fachrechte**: die Fachrechte eines gewählten Kontos.

Die Schaltfläche **Konto anlegen** steht im Seitenkopf. Unter der Liste zeigen Kacheln **Benutzerkonten**, **Aktive Konten**, **Deaktivierte Konten** und **Administration** (Konten mit Administrationsrolle).

### Kontenliste

Die Tabelle **Lokale Konten** hat die Spalten **Benutzername**, **E-Mail-Adresse**, **Rollen** (**Administration**, **Identitätsadministration** oder **Keine**), **Status** und **Anmelderichtlinie**. Die Status sind **Aktiv**, **Deaktiviert** und **Aktivierung ausstehend**. Jede Zeile hat die Aktionen **Bearbeiten**, **Einmalcode ausstellen** und, je nach Status, **Deaktivieren** (bei **Aktiv**) oder **Reaktivieren** (bei **Deaktiviert**). Ein Konto mit **Aktivierung ausstehend** zeigt keine dieser beiden Schaltflächen und lässt sich deshalb in der Liste nicht deaktivieren.

Über der Tabelle stehen **Suche** (in Benutzername und E-Mail-Adresse), **Rolle**, **Status**, **Anmelderichtlinie** und **Sortierung**. Zu den Sortierungen gehören **Zuletzt aktiv** und **Längste Inaktivität**; damit finden Sie ungenutzte Konten. Die Liste zeigt 15 Konten pro Seite. **Filter zurücksetzen** hebt die Filter auf.

### Bearbeitungsformular

**Bearbeiten** öffnet das Formular mit **Benutzername** (2 bis 20 Zeichen), **E-Mail-Adresse**, **Anzeigename** (höchstens 100 Zeichen) und den Grenzen **Speicherlimit (MB)**, **Tokenlimit pro Monat** und **Transkriptionslimit pro Monat (Sekunden)**. Für ein bestehendes Konto zeigt es zusätzlich:

- **Zuletzt aktiv** (**Noch nie**, wenn keine Nutzung erfasst ist), die Zahl der Diktate und den belegten Speicher. Der Zeitpunkt der letzten Aktivität wird pro Konto höchstens alle fünf Minuten geschrieben.
- Den Token- und Transkriptionsverbrauch des laufenden Monats.
- Das Kontrollkästchen **Anmeldesperre aufheben**, nur solange das Konto gesperrt ist (siehe [Anmeldesperre](#anmeldesperre)).
- **Anmelderichtlinie** mit dem Hinweis `Entra ist vorbereitet, aber noch nicht aktiviert.`
- Unter **Rollen**: **Administrationsrolle** und **Identitätsadministration**.

**Übernehmen** speichert und lässt das Formular offen; **Speichern und schließen** speichert und schliesst es; **Abbrechen** verwirft die Änderungen. Für ein Konto in Bearbeitung öffnet **Systemaktivität dieses Kontos** das **System-Auditprotokoll**, gefiltert auf dieses Konto (`/admin#/system/audit?konto=<id>`).

Jede Änderung trägt die geladene Version des Kontos mit. Hat jemand anders das Konto inzwischen geändert, wird die Änderung abgelehnt, der aktuelle Stand neu geladen, und die Seite zeigt `Das Konto wurde inzwischen geändert oder die Änderung ist nicht möglich. Der aktuelle Stand wurde geladen; Ihre Eingaben wurden verworfen.`. Kontozeilen, Entwürfe und Einmalcodes bleiben nur im Arbeitsspeicher; nichts wird im Browserspeicher abgelegt.

## Konto anlegen und aktivieren

**Konto anlegen** fragt **Benutzername**, **E-Mail-Adresse**, optional **Anzeigename** und die Grenzen ab. Ein leeres **Speicherlimit (MB)** zeigt **Systemstandard**: Das Konto erhält dann den Wert der Systemeinstellung `default_storage_quota_mb`, wobei 0 keine Grenze bedeutet. Die übrigen leeren Grenzen zeigen **Unbegrenzt**. Nach **Übernehmen** bleibt das Formular für das neue Konto offen; **Speichern und schließen** schliesst es. Neue Konten erhalten zudem die [Vorgaben für neue Konten](system-settings.md#vorgaben-fur-neue-konten) aus den Systemeinstellungen (Oberflächendesign, Farbschema, Position des Audioplayers).

Das neue Konto hat den Status **Aktivierung ausstehend**, die Anmelderichtlinie **Nur lokal**, kein Passwort, keine Rolle und kein Fachrecht. Beim Anlegen lassen sich weder Rollen noch Status, Anmelderichtlinie, die Aufhebung einer Anmeldesperre oder die Option zum Erstellen von Gruppen mitsenden: Eine solche Anfrage wird mit `409` und `Konto zuerst anlegen und aktivieren.` abgelehnt. Eine Anfrage mit Passwort wird mit `400 use_recovery_code` abgelehnt (`Bitte einen lokalen Einmalcode ausstellen.`). Eine Anfrage mit Rollen, Pools oder klinischen Rechten wird mit `403 explicit_grant_required` abgelehnt (`Fachrechte müssen separat erteilt werden.`). Benutzername und E-Mail-Adresse dürfen keinem anderen Konto gehören (`Benutzername oder E-Mail-Adresse ist bereits vergeben.`).

### Einmalcodes

**Einmalcode ausstellen** erzeugt einen Einmalcode:

- Für ein Konto mit **Aktivierung ausstehend** aktiviert der Code das Konto (Zweck `activate_local`).
- Für ein aktives Konto mit lokaler Anmelderichtlinie setzt der Code das Passwort zurück (Zweck `reset_local`). Das Zurücksetzen beendet sofort alle Sitzungen des Kontos und vermerkt einen ausstehenden Passwortwechsel. Bis zur Einlösung des Codes kann sich das Konto nicht mehr mit dem alten Passwort anmelden. Da kein Formular Codes einlöst (siehe unten), sperrt ein Rücksetzcode die Anmeldung des Kontos, bis der Code über den Serverendpunkt eingelöst ist.

Der Code hat 256 Bit, gilt 30 Minuten und ist einmal verwendbar. Der Server speichert nur seinen SHA-256-Hash und sendet die Antwort mit `Cache-Control: no-store`. Die Seite zeigt ihn einmal unter **Einmalcode (wird nur jetzt angezeigt)**; **Code ausblenden** blendet ihn aus. Ein verlorener Code lässt sich nicht erneut anzeigen; stellen Sie einen neuen aus. Ein neuer Code macht alle älteren offenen Codes des Kontos ungültig, ebenso eine Deaktivierung und ein Passwortwechsel.

Ein Code wird abgelehnt:

- für das Konto einer anderen Administration (**Administrationsrolle** oder **Identitätsadministration**): `Administratorkonten werden nur über die Serverkonsole wiederhergestellt. Ein Einmalcode kann hier nicht ausgestellt werden.`. Die Seite lehnt dies für jedes Konto mit **Administrationsrolle** bereits vor dem Senden ab. Eine Ablehnung durch den Server wird im Auditprotokoll vermerkt.
- für ein deaktiviertes Konto oder ein Konto mit nicht lokaler Anmelderichtlinie: `Für dieses Konto ist kein lokaler Einmalcode möglich.`; für ein Konto ohne Kontorichtlinie: `Für dieses Konto fehlt eine Anmelderichtlinie.`.

**Nicht verfügbar: Kein Formular löst den Code ein.** Der Server nimmt die Einlösung als `POST` auf `/login` entgegen (JSON oder Formulardaten) mit den Feldern `purpose` (`activate_local` oder `reset_local`), `code`, `new_password` und `confirmation`. Anstelle von `confirmation` wird auch `confirm_password` angenommen; stimmen die Passwörter nicht überein, folgt `400 password_mismatch`. Die Anfrage braucht das Sitzungscookie und ein CSRF-Token derselben Sitzung (Formularfeld `csrf_token` oder Header `X-CSRFToken`; `GET /api/csrf-token` liefert eines). Die Anmeldeseite der Klinik bietet nur Benutzername oder E-Mail-Adresse und Passwort an; ein Formular für diese Anfrage gibt es nicht. Eine erfolgreiche Einlösung setzt das Passwort, aktiviert ein ausstehendes Konto, hebt die Anmeldesperre des Kontos auf und meldet den Browser ab (`204`); danach meldet sich die Person normal an. Ein ungültiger, abgelaufener oder verwendeter Code ergibt `Der Einmalcode ist ungültig, abgelaufen oder wurde bereits verwendet.` und zählt als fehlgeschlagene Anmeldung des Kontos, zu dem er gehört. Ein von der Passwortrichtlinie abgelehntes Passwort lässt den Code unverbraucht. Ein angemeldeter Browser erhält `409 sign_out_first` (`Bitte zuerst abmelden.`).

**Konten der Administration: kein Einmalcode über das Web.** Das Web lehnt Einmalcodes für andere Konten ab, die aktuell **Administrationsrolle** oder **Identitätsadministration** haben, und die Konsolenbefehle sind nicht registriert. Eine andere Identitätsadministration kann die Anmeldesperre eines solchen Kontos mit **Anmeldesperre aufheben** aufheben (siehe [Anmeldesperre](#anmeldesperre)). Um einem solchen Konto ein neues Passwort zu geben, muss eine andere Identitätsadministration zuerst die Rollen entfernen (protokolliert als `admin.user.role_changed` und `admin.user.changed`), einen Rücksetzcode ausstellen und die Rollen nach dem Einlösen des Codes wieder setzen. Halten Sie mindestens zwei aktive lokale Identitätsadministrationen bereit.

## Passwörter

- Länge 15 bis 128 Unicode-Zeichen (Codepunkte). Es gibt keine Zusammensetzungsregeln. Leerzeichen am Anfang und am Ende werden nicht entfernt, und das Passwort wird nicht gekürzt.
- Es gilt eine eingebaute Sperrliste häufiger Passwörter. Verglichen wird nach Unicode-Normalisierung ohne Unterscheidung der Gross- und Kleinschreibung, zusätzlich auch nach Entfernen aller Leerraumzeichen. `CLINIC_PASSWORD_BLOCKLIST_FILE` (standardmässig nicht gesetzt) benennt eine zusätzliche, standortspezifische Sperrliste: ein Eintrag pro Zeile, Zeilen mit `#` am Anfang sind Kommentare.
- Hashes verwenden `scrypt:131072:8:1`. Ein bcrypt-Hash aus Upstream wird akzeptiert und nach der nächsten erfolgreichen Anmeldung durch das aktuelle Verfahren ersetzt.
- Im Klinikmodus setzt keine Rolle der Verwaltung Passwörter. Passwörter entstehen nur durch Einlösen eines Einmalcodes, durch das Einrichtungsskript oder durch die Person selbst.
- Eine angemeldete Person ändert ihr eigenes Passwort auf der Kontoseite; das aktuelle Passwort ist erforderlich, alle anderen Sitzungen enden und offene Codes werden ungültig. Der Dialog dort zeigt noch den Upstream-Hinweis auf mindestens 8 Zeichen; der Server lehnt Passwörter unter 15 Zeichen ab.

## Rollen ändern

**Administrationsrolle** und **Identitätsadministration** werden im Formular unter **Rollen** gesetzt.

- Wer die **Administrationsrolle** entfernt, entfernt auch die **Identitätsadministration**.
- Eine Änderung einer der beiden Rollen beendet alle Sitzungen des geänderten Kontos.
- Die letzte aktive, lokal nutzbare Identitätsadministration kann weder deaktiviert werden noch eine Rolle verlieren noch auf **Nur Entra** umgestellt werden: `Zuerst eine weitere lokale Identitätsadministration aktivieren.`
- `POST /admin/users/<id>/toggle-admin` mit dem Inhalt `{"expected_version": <version>}` schaltet die **Administrationsrolle** mit denselben Prüfungen um. Für das eigene Konto wird es abgelehnt (`400 invalid_role_target`).

## Deaktivieren statt löschen

Im Klinikmodus werden Konten nie gelöscht. **Deaktivieren** setzt den Status **Deaktiviert**, beendet alle Sitzungen des Kontos und macht seine offenen Einmalcodes ungültig. Ein deaktiviertes Konto kann sich nicht anmelden. Seine Diktate, sein Verlauf und seine Auditeinträge bleiben erhalten.

**Reaktivieren** setzt den Status zurück auf **Aktiv**, oder auf **Aktivierung ausstehend**, wenn das Konto kein Passwort hat.

`DELETE /admin/users/<id>` ist durch die Funktionssperre `clinical_user_hard_delete` gesperrt und antwortet mit `403 clinic_feature_disabled`; die Route selbst lehnt zusätzlich mit `disable_instead` ab (`Bitte das Konto deaktivieren.`).

## Grenzen

Die drei Grenzfelder übernehmen die Upstream-Budgets pro Konto. Ein leeres Feld bedeutet keine Grenze.

- **Speicherlimit (MB)**: Speicherkontingent in Megabyte. Ein Upload, der es überschreiten würde, wird abgelehnt (`507`, Code `storage_quota_exceeded`); Aufnahmen, deren Audio entfernt wurde, zählen nicht.
- **Tokenlimit pro Monat**: Tokenbudget des laufenden Monats für Anfragen an das Textmodell, zum Beispiel Titel, Zusammenfassungen, Chat und Inquire. Chat, Inquire und Zusammenfassungen sind nur mit einem lokalen Textmodell verfügbar (siehe [Modellkonfiguration](model-configuration.md)). Ab 80 % schreibt der Server eine Warnung in sein Log; bei 100 % wird die Anfrage mit der Budgetmeldung abgelehnt.
- **Transkriptionslimit pro Monat (Sekunden)**: Transkriptionsbudget in Sekunden. Das Budget des Kontos, dem die Aufnahme gehört, wird beim Start eines Transkriptionsauftrags geprüft; bei 100 % schlägt die Aufnahme mit der Budgetmeldung fehl, ohne Wiederholung.

Die Seite akzeptiert ganze Zahlen von 1 bis 2147483647. Die Upstream-Regeln für das Tokenbudget (Schritte von 10 000, mindestens 100 000) und das Upstream-Transkriptionsbudget in Minuten gelten hier nicht. Upstream-Kostenschätzungen pro Cloud-Anbindung entfallen: Die Klinikversion verwendet keine Cloud-Spracherkennung. Im Klinikmodus nimmt die Anbindung `openai_whisper` nur eine lokale oder private Adresse an; `ASR_BASE_URL` wird nicht geprüft und muss auf einen lokalen Dienst zeigen (siehe [Modellkonfiguration](model-configuration.md)).

## Anmeldesperre

Es gelten die Systemeinstellungen `login_max_failed_attempts` (Standard 5, 0 schaltet die Sperre aus) und `login_lockout_minutes` (Standard 15); siehe [Systemeinstellungen](system-settings.md). Nach so vielen fehlgeschlagenen Anmeldungen eines Kontos von einer Clientadresse werden Anmeldungen des Kontos von dieser Adresse für so viele Minuten abgelehnt. Ein zweiter, kontoweiter Zähler sperrt das Konto für alle Adressen beim Vierfachen des Schwellenwerts pro Adresse. Eine gesperrte Anmeldung erhält dieselbe Meldung wie ein falsches Passwort: `Anmeldung fehlgeschlagen.`

Eine Identitätsadministration hebt die Sperre im Formular auf, auch bei einem Konto mit **Administrationsrolle**: Öffnen Sie **Bearbeiten**; ein gesperrtes Konto zeigt das Kontrollkästchen **Anmeldesperre aufheben**. Aktivieren Sie es, und speichern Sie mit **Übernehmen** oder **Speichern und schließen**. Die Kontenliste zeigt die Sperre nicht an. Auch eine erfolgreiche Einlösung eines Einmalcodes hebt die Sperre auf.

## Anmelderichtlinie und Identitätsbindung

Jedes Konto hat eine **Anmelderichtlinie**:

| Bezeichnung | Wert | Bedeutung |
|---|---|---|
| **Nur lokal** | `local_only` | Lokales Passwort. Standard für jedes neue Konto. |
| **Lokal und Entra** | `local_and_entra` | Lokales Passwort und Entra. Vorbereitet, deaktiviert. |
| **Nur Entra** | `entra_only` | Nur Entra. Vorbereitet, deaktiviert. |
| **Nur Gerät** | `device_only` | Keine Anmeldung mit lokalem Passwort. |
| **Keine Anmelderichtlinie** | keiner | Konto ohne Klinik-Kontorichtlinie; keine lokale Anmeldung. |

Eine lokale Anmeldung setzt den Status **Aktiv**, ein gespeichertes Passwort und die Richtlinie **Nur lokal** oder **Lokal und Entra** voraus. Im Formular ist nur **Nur lokal** wählbar; für ein Konto ohne Richtlinie ist das Feld gesperrt. Der Server lehnt jede Änderung der Richtlinie ab, ausser das Ziel ist **Nur Entra** und das Konto hat eine aktive Entra-Verknüpfung; eine solche Verknüpfung lässt sich bei deaktiviertem Entra nicht anlegen. Die Seite zeigt dann `Diese Anmelderichtlinie ist für das Konto nicht verfügbar.`. **Lokal und Entra** lässt sich über das Web überhaupt nicht setzen.

Die Identitätsbindung folgt der Klinikspezifikation: Alle Anmeldewege verwenden dieselbe interne Benutzer-ID, und Konten werden nie über die E-Mail-Adresse verknüpft. Im vorbereiteten Entra-Profil ist die Verknüpfung ausdrücklich: Eine Identitätsadministration genehmigt sie, und die betroffene Person startet sie nach einer frischen lokalen Anmeldung. Es gibt keinen Abgleich über die E-Mail-Adresse, und kein Webweg verknüpft eine andere Administration. Dieser Ablauf hat API-Routen, aber keine Seite, und die Kontenliste zeigt keinen Verknüpfungsstatus. Die Entra-Anbieter sind deaktiviert; ihre Routen antworten mit `422` und dem Titel `Anbieter nicht aktiviert`. Die Anmeldeseite zeigt **Lokale Anmeldung: Aktiv** und **Einmalanmeldung: Nicht eingerichtet**, und die Upstream-Routen für OIDC-Einmalanmeldung lehnen im Klinikmodus ab. Siehe [Anmeldung und Identitätsanbieter](sso-setup.md).

## Bestehende Konten ohne Kontorichtlinie

Konten aus der Zeit vor dem Klinikmodus oder aus den Upstream-Variablen `ADMIN_*` haben keine Klinik-Kontorichtlinie. Sie erscheinen mit **Keine Anmelderichtlinie** und können sich nicht anmelden. Eine Identitätsadministration übernimmt ein solches Konto mit:

```http
PUT /admin/users/<id>
Content-Type: application/json

{"adopt_existing_local": true, "expected_version": 0}
```

Der Inhalt muss genau diese zwei Schlüssel enthalten. Es gelten die üblichen Regeln für Kontoänderungen: Identitätsadministration, frische Anmeldung, CSRF-Token. Das Konto braucht einen gespeicherten bcrypt- oder scrypt-Passworthash und keine externe Identität (keine Upstream-SSO-Verknüpfung, keine Entra-Verknüpfung und keine Verknüpfungsanfrage). Das übernommene Konto erhält den Status **Aktiv** und die Richtlinie **Nur lokal**; die **Identitätsadministration** bleibt aus, und sein Administrationskennzeichen bleibt unverändert. Das gespeicherte Passwort bleibt erhalten; die Passwortrichtlinie der Klinik wird dafür nicht geprüft. Bitten Sie die Person, nach der ersten Anmeldung auf der Kontoseite ein neues Passwort zu setzen. Die Seite hat dafür kein Bedienelement; es ist ein API-Aufruf. Siehe Schritt 6 der [Migrationsanleitung](migration-guide.md#6-bestehende-konten-ubernehmen).

## Verzeichnisabgleich (Verzeichnis)

Die Seite **Verzeichnis** (`/admin#/verzeichnis`) zeigt die Überschrift **Verzeichnisabgleich** mit `Der Verzeichnisabgleich ist nicht aktiviert.` und die Schaltfläche **Lokale Konten verwalten**, die **Benutzer und Rollen** öffnet. Ohne Identitätsrechte zeigt sie `Der Verzeichnisabgleich erfordert Rechte zur Identitätsverwaltung.`.

Status: Vorbereitet, deaktiviert. In der Anwendung ist kein Verzeichnisanbieter registriert. Die Abfrage der Verfügbarkeit meldet deshalb immer «nicht verfügbar», und Vorschau und Anwenden antworten mit `503` und `Verzeichnis nicht verfügbar`. Der vorbereitete Abgleich arbeitet mit Verzeichnis-Objekt-IDs, die die Identitätsadministration bestätigt. Die Vorschau listet neue Identitäten, geänderte Anzeigenamen, zu deaktivierende Konten und Änderungen der Poolmitgliedschaften in verwalteten Pools (**Poolzuordnungen**). Das Anwenden aktualisiert Anzeigenamen, deaktiviert Konten und ändert Mitgliedschaften. Es lehnt das Anlegen von Konten ab (`422 explicit_link_required`), löscht nie ein Konto, deaktiviert nie das eigene Konto der handelnden Identitätsadministration und ändert nie dessen Poolmitgliedschaften.

Offene Lücke WG-ADM-009: Für das Auslesen der lokalen Windows-Konten eines Arbeitsplatzes gibt es keinen zulässigen Webweg. Zulässige Alternativen sind ein serverseitiger Verzeichnisabgleich und lokale Anwendungskonten.

## Poolbezüge

Der Bereich **Poolbezüge** zeigt für das unter **Benutzer** gewählte Konto seine **Autorenpools** und **Schreibkräfte-Pools** oder `Keine Poolzugehörigkeit`. Er ist nur lesend: `Mitgliedschaften werden unter «Pools verwalten» geändert.` Ein Mitglied eines Schreibkräfte-Pools, der mit einem Autorenpool verbunden ist, erhält die Aktion **Ansehen** für die Diktate dieses Autorenpools; jede andere Aktion braucht ein Fachrecht. Pools, Mitgliedschaften und Poolleitungen beschreibt die [Gruppenverwaltung](group-management.md).

## Fachrechte

Nur eine aktive globale Administration ändert Fachrechte; die **Identitätsadministration** ist dafür weder nötig noch ausreichend. Aktive Identitätsadministrationen und das Konto selbst dürfen die Liste eines Kontos lesen; sonst niemand.

So legen Sie einen Eintrag an:

1. Öffnen Sie auf **Benutzer und Rollen** den Bereich **Fachrechte**, und wählen Sie unter **Benutzer** das Konto. Der Bereich zeigt den Abschnitt **Berechtigungen**: `Fachrechte eines Benutzers: erlauben oder verweigern, je Geltungsbereich und Aktion. Eine Verweigerung gewinnt immer.`
2. Wählen Sie den **Geltungsbereich**: **Eigene Diktate**, **Pool** mit einem **Autorenpool** oder **Alle Diktate**.
3. Wählen Sie die **Wirkung**: **Erlauben** oder **Verweigern**.
4. Wählen Sie mindestens eine der **Aktionen**, zum Beispiel **Freigeben** oder **Originalaudio exportieren**.
5. Klicken Sie auf **Speichern**.

**Entziehen** entfernt einen Eintrag aus **Bestehende Einträge**. Hat sich der **Stand** inzwischen geändert, wird die Änderung abgelehnt (`409`), und die Seite zeigt `Der Stand ist veraltet. Bitte den aktuellen Stand laden und die Änderung wiederholen.` mit **Aktuellen Stand laden**. Eine Änderung gilt ab der nächsten Anfrage des Kontos. Pool- und Rechteverwaltung sowie Statistikrechte lassen sich über Fachrechte nicht delegieren (siehe [Systemstatistik](statistics.md)). In einem Punkt reichen Fachrechte aber über einzelne Diktate hinaus: Eine Erlaubnis mit Geltungsbereich **Pool** oder **Alle Diktate**, die **Bearbeiten** oder **Freigeben** nennt, gibt auch Befugnis über den gemeinsamen Wortschatz des Autorenpools; siehe [Wirkung auf gemeinsame Poolressourcen](group-management.md#wirkung-auf-gemeinsame-poolressourcen).

Die sicheren Standardrechte, den Vorrang von Verweigerungen, die Vorschau **Wirksames Ergebnis nach dem Speichern** und die weiteren Aktionen, die nur die API annimmt, beschreibt die [Gruppenverwaltung](group-management.md#fachrechte).

### Freigeben

Standardmässig hat niemand **Freigeben**, auch Konten mit **Administrationsrolle** nicht. Das Recht ist nötig für die endgültige Freigabe (**Endgültig freigeben**), für Annehmen oder Ablehnen in der **Abnahme** und für das Wiederöffnen; **Zur Abnahme einreichen** und Neu zuordnen brauchen **Bearbeiten** und **Freigeben**. Die Bedingungen der Freigabe, den KIS-Kontext und die Abnahmerichtlinie beschreibt die [Gruppenverwaltung](group-management.md#freigabe-freigeben-und-abnahme).

### Originalaudio exportieren

**Originalaudio exportieren** ist von **Exportieren** (Textexport) getrennt. Es gehört nicht zu den Standardrechten an eigenen Diktaten, und das Ansehen und Abspielen durch die globale Administration schliesst es nicht ein. Die Routen, die dieses Recht prüfen, und den nicht angebundenen Mailentwurf (offene Lücke WG-EXP-009) beschreibt die [Gruppenverwaltung](group-management.md#originalaudio-exportieren).

## Was protokolliert wird

Die Kontoverwaltung schreibt diese Ereignisse ins Auditprotokoll (vollständiger Katalog im [Auditprotokoll](audit-log.md)):

| Ereignis | Wann | Details |
|---|---|---|
| `admin.user.created` | Konto angelegt | `username`, `is_admin` (hier immer `false`) |
| `admin.user.changed` | Profil, Grenzen, Status, Rollen oder Anmelderichtlinie geändert; Einmalcode ausgestellt; Code für eine Administration abgelehnt; Konto übernommen | `fields`: nur die Namen der geänderten Felder, nie Werte; bei Codes `activate_local` oder `reset_local`, bei einer Ablehnung `recovery_refused`, bei einer Übernahme `local_account_policy_adopted` |
| `admin.user.role_changed` | **Administrationsrolle** geändert | `old`, `new` |
| `admin.user.unlocked` | Anmeldesperre aufgehoben | keine |
| `auth.password.changed` | Code eingelöst oder eigenes Passwort geändert | keine |
| `auth.login.succeeded` | Lokale Anmeldung | `method` (`password`), `remember` (`false`) |
| `auth.login.failed` | Falsches Passwort, gesperrtes Konto, ungültiger Code, falsches aktuelles Passwort | `reason` (`bad_password`, `locked`, `bad_recovery_code`, `bad_current_password`), `method` (`password`, `recovery`, `password_change`) |
| `auth.login.locked` | Anmeldesperre gesetzt | `minutes` |
| `recording.content.read` | Eine aktive globale Administration öffnet ein Transkript über die Administrationsroute oder erhält eine fremde Zeile in der Arbeitsliste | `part`: `transcript` oder `worklist` |
| `recording.media.played` | Eine aktive globale Administration spielt Audio über die Administrationsroute ab | keine |

Eine Änderung nur der **Identitätsadministration** erscheint ausschliesslich als `admin.user.changed` mit dem Feld `is_identity_admin`.

Zeilen werden geschrieben, solange `audit_log_enabled` eingeschaltet ist (Standard: ein); `recording.media.played` nur, solange `audit_log_record_views` eingeschaltet ist; `auth.login.failed` wird höchstens einmal pro Minute je Adresse und Konto geschrieben (siehe [Auditprotokoll](audit-log.md)).

Nicht im Auditprotokoll:

- Änderungen an Fachrechten, Pools, Mitgliedschaften und Poolleitungen. Sie werden mit ihren Details in den separaten Poolverlauf geschrieben, den die Anwendung nur ergänzt und den keine Seite und keine Route anzeigt. Das Anwendungslog enthält pro Änderung eine Zeile (`clinic pool: <change> <entity> <id> by <actor>`).
- Die Einrichtung der ersten Administration (nur Logzeile).

Die Ereignisse `admin.user.password_set` und `admin.user.deleted` entstehen im Klinikmodus nicht, weil keine Rolle der Verwaltung Passwörter setzt und Konten nicht gelöscht werden.

## Upstream-Funktionen, die in der Klinikversion fehlen

Diese Teile der Upstream-Seite gelten im Klinikmodus nicht (Nur Upstream):

- **Add User** mit Passwort sowie von der Administration gesetzte Passwörter im Bearbeitungsdialog.
- Löschen eines Kontos mit allen Aufnahmen.
- Die Berechtigung für öffentliches Teilen (Globus-Symbol); öffentliches Teilen ist aus dem Fork entfernt.
- **Can create groups** (Upstream; im Klinikformular das Kontrollkästchen **Gruppen erstellen** unter **Rollen**): Die Option erscheint nur, solange die Funktion `internal_sharing` freigeschaltet ist, was die Klinikkonfiguration nicht tut. Upstream-Gruppen und -Teams sind gesperrt; siehe [Gruppenverwaltung](group-management.md).
- Die Benutzertabelle mit sortierbaren Spalten, der Spalte **Last active** und **View activity**; ersetzt durch die Kontenliste, **Zuletzt aktiv** im Formular und **Systemaktivität dieses Kontos**.
- Die eingeschränkte Benutzerliste für Gruppenadministrationen: Im Klinikmodus können Konten ohne **Administrationsrolle** `/admin` nicht öffnen.
- Verknüpfen und Lösen der OIDC-Einmalanmeldung auf der Kontoseite.

---

Weiter: [Gruppenverwaltung](group-management.md) →
