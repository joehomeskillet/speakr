# Webhooks
<small>[English](../../admin-guide/webhooks.md)</small>

In der Klinikversion (`CLINIC_MODE` eingeschaltet) sind Benutzer-Webhooks gesperrt. Die Anwendung sendet
keine Webhook-Ereignisse, und weder Benutzer noch Administration können Webhooks anlegen oder verwalten.
Diese Seite beschreibt, was gesperrt ist, welche Webhook-Einstellungen noch angezeigt werden und was sie
bewirken, sowie die Mechanismen, die die Klinikversion stattdessen umsetzt: klinische Ablaufhinweise,
die Ansicht **Ausgabe** und die KIS-Schnittstelle, die nur simuliert ist.

## Statusübersicht

| Funktion | Status in der Klinikversion |
|---|---|
| Benutzer-Webhooks (`/api/v1/webhooks`, Kontoreiter «Webhooks») | Nur Upstream (im Klinikmodus gesperrt) |
| Abfrage der API v1 mit einem persönlichen API-Token | Nur Upstream (im Klinikmodus gesperrt) |
| Web-Push-Benachrichtigungen | Nur Upstream (im Klinikmodus gesperrt) |
| Klinische Ablaufhinweise (Benutzermenü, **Benachrichtigungen**) | Verfügbar |
| Ansicht **Ausgabe**: TXT, DOCX, **Zwischenablage** | Als Ansicht verfügbar. Jedes Ziel erfordert eine aktive endgültige Freigabe; diese ist derzeit nur mit `CLINIC_PROFILE=staging` möglich (Simuliert). TXT und DOCX werden auf dem Server erzeugt, aber keine Route liefert die Datei aus. |
| KIS-Ziel in **Ausgabe**, KIS-Kontext für die endgültige Freigabe | Simuliert (nur mit `CLINIC_PROFILE=staging`, synthetischer MockKIS, kein KIS-Nachweis); sonst ist der Adapter deaktiviert, und die endgültige Freigabe wird abgelehnt |
| Abgleich einer KIS-Zustellung mit unklarem Ergebnis | Vorbereitet, deaktiviert (keine Route, keine Oberfläche) |
| Serverseitige Exportordner, ODT-Ausgabe, DOCX-Vorlagen mit Textmarken | Vorbereitet, deaktiviert (Bibliothekscode, keine Route) |
| Einfügen in ein geöffnetes Word- oder Writer-Dokument, Fenster- oder Tastenautomation, lokales Mailprogramm mit Audioanhang, Word-Add-in | Offene Lücke (WG-EXP-004, WG-EXP-005, WG-EXP-007, WG-EXP-009, WG-EXP-010) |

## Webhooks im Klinikmodus

Im Klinikmodus ist jeder Funktionsschalter gesperrt, solange die Vertragsdatei
`clinic-spec/config/dev.defaults.json` (oder dieselbe Datei im Verzeichnis, das `CLINIC_CONFIG_DIR`
angibt) ihn unter `features` nicht auf `true` setzt. Ausgenommen sind `chat`, `inquire` und
`generative_summary`: Sie hängen von einem lokalen Textmodell ab, nicht von diesem Schalter. Die mitgelieferte
Datei setzt `"user_webhooks": false`. Solange diese Sperre gilt:

- **Routen werden abgewiesen.** Die Routenrichtlinie führt alle zehn Webhook-Endpunkte (`webhooks.*`
  unter `/api/v1/webhooks`: auflisten, anlegen, lesen, ändern, löschen, Geheimnis erneuern, testen,
  Zustellungen auflisten, Zustellung lesen, erneut senden) als deaktiviert unter `user_webhooks`. Die
  Zugriffsprüfung (Guard) antwortet mit `403`, `"code": "clinic_feature_disabled"` und `"feature": "user_webhooks"`,
  noch bevor sie die Anmeldung prüft. Eine Anfrage, die sich mit einem API-Token statt mit einer
  Browsersitzung anmeldet, wird schon einen Schritt früher mit `"feature": "personal_api_tokens"`
  abgewiesen.
- **Es wird nichts zugestellt.** Jeder Webhook-Einstieg prüft zuerst die Sperre: der Ereignisaufruf
  (`emit_webhook_event`), die Änderungsverfolgung, die `recording.updated` einreiht, und jeder Durchlauf
  des Zustelldienstes (Dispatcher). Es entsteht keine Zustellungszeile, und keine HTTP-Anfrage verlässt den Server.
  `WEBHOOK_GLOBAL_ENABLED=true` hebt die Sperre nicht auf.
- **Keine Oberfläche.** Auf `/account` werden der Reiter «Webhooks» und sein Skript
  `account-webhooks.js` nicht ausgegeben. Ein Kliniktest in `tests/clinic/` prüft
  dies.
- **Protokollzeile.** Im Prozess, der die Hintergrundarbeit ausführt (nicht in einem reinen Webprozess mit
  `CLINIC_WEB_ONLY=1`), startet der Zustelldienst weiterhin und protokolliert
  `Webhook dispatcher started (interval=<n>s)`. Solange die Sperre gilt, überspringt er jeden Durchlauf.
- **Keine Audit-Ereignisse.** Der Audit-Katalog enthält keine Webhook-Ereignisse. Siehe
  [Auditprotokoll](audit-log.md).

Der verbindliche Entscheid D21 in `clinic-spec/config/decisions.json` schliesst persönliche Webhooks aus.
Das Bearbeiten der Vertragsdatei ist kein Betriebsverfahren für diese Version, und dieses Handbuch enthält
keine Schritte, um die Funktion freizuschalten.

Quellcode (Klinik-Fork): [src/clinic/route_policy.py](https://github.com/joehomeskillet/speakr/blob/main/src/clinic/route_policy.py),
[src/services/webhook_dispatch.py](https://github.com/joehomeskillet/speakr/blob/main/src/services/webhook_dispatch.py).

### Verbleibende Webhook-Einstellungen und -Variablen

In der globalen Administration führt **Systemeinstellungen** (`/admin#/system/einstellungen`) weiterhin zwei
Webhook-Einstellungen auf. Nur ein Konto mit der **Administrationsrolle** kann sie ändern. Solange das
Auditprotokoll eingeschaltet ist, wird jede Änderung als `admin.setting.changed` mit altem und neuem Wert
protokolliert.

| Einstellung | Umgebungsvariable | Standard | Wirkung im Klinikmodus |
|---|---|---|---|
| `webhook_omit_titles` | `WEBHOOK_OMIT_TITLES` | `false` | Keine. Es wird keine Nutzlast erzeugt. |
| `webhook_delivery_retention_days` | `WEBHOOK_DELIVERY_RETENTION_DAYS` | `30` | Entfernt nur bereits vorhandene Zustellungszeilen, zum Beispiel aus dem Betrieb vor dem Einschalten des Klinikmodus. `0` behält sie. |

Das Entfernen übernimmt die Bereinigung im Hintergrund, alle `RECORDING_SESSION_CLEANUP_INTERVAL_SECONDS`
(Standard `3600`), und protokolliert `Pruned <n> webhook delivery record(s)`. Ist
`RECORDING_SESSION_CLEANUP_INTERVAL_SECONDS` auf `0` oder weniger gesetzt, läuft der Bereinigungsthread nicht und
damit auch dieses Entfernen nicht.

Die mitgelieferten Umgebungsbeispiele (`config/env.*.example`) führen weitere, auskommentierte
Webhook-Variablen auf. Sie gelten nur für Upstream und haben im Klinikmodus keine Wirkung:
`WEBHOOK_GLOBAL_ENABLED`, `WEBHOOK_MAX_PER_USER`, `WEBHOOK_DELIVERY_TIMEOUT_SECONDS`,
`WEBHOOK_MAX_ATTEMPTS`, `WEBHOOK_AUTOPAUSE_FAILURES`, `WEBHOOK_TRIAL_INTERVAL_SECONDS`,
`WEBHOOK_INTRANET_HOST_ALLOWLIST` und `WEBHOOK_SECRET_GRACE_HOURS`. `WEBHOOK_DISPATCHER_INTERVAL_SECONDS`
(Standard `5`, mindestens `1`) legt nur fest, wie oft der untätige Zustelldienst aufwacht; der Wert erscheint
in der Protokollzeile als `interval=<n>s`.

### Webhook-Daten aus einer früheren Upstream-Instanz

Wird eine bestehende Speakr-Instanz auf den Klinikmodus umgestellt, bleiben ihre Zeilen in den Tabellen
`webhook` und `webhook_delivery` in der Datenbank. Es wird nichts gesendet, und keine Route und keine
Seite zeigt oder verwaltet sie. Zustellungszeilen, auch eingereihte, werden wie oben beschrieben nach
Alter entfernt. Webhook-Zeilen werden nicht entfernt.

- Eine Zustellungszeile enthält die gesendete Nutzlast (Aufnahme-ID, Titel und ursprünglicher Dateiname, sofern
  `webhook_omit_titles` nicht eingeschaltet war, Fehlertext), den HTTP-Status des Empfängers und die ersten
  2000 Zeichen seiner Antwort.
- Eine Webhook-Zeile behält Name, Ziel-URL, abonnierte Ereignisse und das HMAC-Signaturgeheimnis (bei einer
  Erneuerung zusätzlich das vorherige).

### Upstream-Verhalten bei ausgeschaltetem Klinikmodus

Mit ausgeschaltetem `CLINIC_MODE` gilt keine dieser Sperren, und die Webhook-Funktion verhält sich wie im
Upstream-Speakr, mit einem Unterschied: Der Fork sendet keine Ereignisse zu öffentlichen Links zum Teilen,
Kalenderterminen oder geteilten Aufnahmen. Verfügbar sind die Ereignistypen `recording.created`,
`recording.transcription.started`, `recording.transcription.completed`,
`recording.transcription.failed`, `recording.summary.completed`, `recording.summary.failed`,
`recording.updated`, `recording.deleted` und `webhook.test`.

Upstream-Speakr-Dokumentation (englisch, gilt nicht für die Klinikversion):
[Webhooks, Upstream-Speakr](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/webhooks.md).

## Alternative: Klinische Ablaufhinweise { #alternative-in-app-hinweise-zum-ablauf }

**Status: Verfügbar.** Die Klinikversion informiert über Abnahme und Fertigstellung mit Hinweisen in der
Anwendung.
Nichts verlässt den Server, es ist kein SMTP-Server nötig, und es ist nichts zu konfigurieren.

**Ort.** In der Kopfzeile zeigt das Benutzermenü (der Avatar mit den Initialen) einen Zähler für
ungelesene Hinweise und den Eintrag **Benachrichtigungen**. Jeder Hinweis hat **Ansehen** und eine
Schaltfläche zum Ausblenden (**Ausblenden**). Die Kopfzeile fragt die Zahl ungelesener Hinweise alle
60 Sekunden ab.

| Ereignis | Gespeicherter Titel (fester deutscher Text) | Empfänger | Ausgelöst durch |
|---|---|---|---|
| `acceptance_requested` | **Abnahme angefordert** | Freigebende der veröffentlichten Abnahmerichtlinie des Diktats: aufgeführte Freigebende und Mitglieder der freigebenden Autorenpools, abzüglich der Sperrliste | **Zur Abnahme einreichen**; zudem, wenn sich der Text nach einer Abnahme ändert und eine neue Abnahme nötig ist |
| `acceptance_rejected` | **Abnahme abgelehnt** | Die aktuell zuständige Person, also die Person, an die das Diktat zurückgeht | **Zur Korrektur zurückweisen** |
| `accepted` | **Diktat abgenommen** | Diktierende Person und zuständige Person | **Zusätzlich abnehmen** |
| `completed` | **Diktat fertiggestellt** | Diktierende Person | **Endgültig freigeben** |

Regeln, wie umgesetzt:

- Die handelnde Person erhält nie einen Hinweis.
- Jede empfangende Person wird bei der Zustellung erneut geprüft. Aktive Konten mit der
  **Administrationsrolle** bestehen die Prüfung immer. Alle anderen benötigen die Aktion `view` auf dem Diktat gemäss
  Poolrichtlinie; wer die Prüfung nicht besteht, wird ausgelassen.
- Ein Hinweis wird als Auftrag vom Typ `clinic_notify` über die bestehende Auftragswarteschlange
  zugestellt. Die Auftragsübersicht **Verarbeitung** führt ihn als **Benachrichtigung**.
- Jede empfangende Person erhält pro Übergang höchstens einen Hinweis (Schlüssel
  `clinic.wf:<event>:<dictation>:<transition>`). Ein wiederholter Auftrag stellt nur an Personen zu,
  deren Hinweis nicht gespeichert werden konnte. Ein ausgeblendeter Hinweis erscheint nicht erneut.
- Der gespeicherte Text besteht aus einem festen deutschen Titel und Text, zum Beispiel «Das Diktat wartet
  auf Ihre Abnahme.». Er enthält kein Transkript und keine Patientendaten. Zulässig sind nur die
  Kontextschlüssel `count`, `priority_label` und `deadline_date`; die heutigen Auslöser übergeben
  keinen davon.

Einschränkungen:

- **Diktat fertiggestellt** setzt eine endgültige Freigabe voraus. Ausserhalb des Staging-Profils ist die
  endgültige Freigabe nicht möglich (siehe [KIS-Schnittstelle](#kis-schnittstelle-simuliert)), dort tritt
  dieser Hinweis also nicht auf.
- Die Liste stellt einen Hinweis über seinen Nachrichtenschlüssel (`clinic.workflow.<event>`) mit den
  Übersetzungen der Oberfläche dar. Die mitgelieferten Sprachdateien enthalten für diese Schlüssel keinen
  Eintrag; gemäss Code zeigt die Liste deshalb den Schlüssel statt des gespeicherten deutschen Titels und
  Texts.
- Gemäss Code verweist der Link **Ansehen** auf `/clinic/dictations/<id>`, einen Pfad, den die Anwendung
  nicht bedient; er öffnet deshalb eine Fehlerseite. Öffnen Sie das Diktat stattdessen über die **Arbeitsliste**.
- Klinische Ablaufhinweise werden nie per E-Mail oder Webhook versendet.

Die Upstream-E-Mail bei abgeschlossener oder fehlgeschlagener Transkription ist davon getrennt. Sie hat
keine Kliniksperre und wird unter [E-Mail-Einrichtung](email-setup.md) beschrieben.

## Alternative: Ansicht «Ausgabe»

**Status: Verfügbar** (als Ansicht), mit den folgenden Einschränkungen. In einem geöffneten Diktat steht die
Ansicht **Ausgabe** (`#/diktat/ausgabe`) neben **Details**, **Prüfen und Freigeben**, **Verarbeitung** und
**Verlauf**. Die Tabelle **Ausgabeziele** führt **Textdatei (TXT)**, **Word-Dokument (DOCX)** und **KIS**, jedes
mit **Ausgabe starten** und eigenem Status. Der Bereich **Zwischenablage** mit **Als Text kopieren** und
**Mit Formatierung kopieren** erscheint nur, wenn eine freigegebene Version besteht. Ohne aktive Freigabe zeigt
die Ansicht **Keine freigegebene Version. Eine Ausgabe ist erst nach der Freigabe möglich.** Die endgültige
Freigabe verlangt einen vertrauenswürdigen KIS-Kontext; ausserhalb von `CLINIC_PROFILE=staging` ist deshalb kein
Ausgabeziel nutzbar (siehe [KIS-Schnittstelle](#kis-schnittstelle-simuliert)).

- **Rechte.** Die Ausgabe erfordert das Fachrecht **Exportieren** (Aktion `export`), auch für die
  **Zwischenablage**. Konten mit der **Administrationsrolle** (globale Administration) haben es standardmässig
  nicht. Die endgültige Freigabe (**Endgültig freigeben**) erfordert das separate Recht **Freigeben**, der Export
  des Originalaudios das separate Recht **Originalaudio exportieren**. Siehe
  [Benutzerverwaltung](user-management.md) und [Gruppenverwaltung](group-management.md).
- **TXT, DOCX und KIS** geben nur die Transkriptrevision der aktiven Freigabe aus. Jede andere Revision
  wird mit `409` `NOT_RELEASED` abgewiesen («Nur die freigegebene Version kann ausgegeben werden»).
  Jede Anfrage benötigt ein CSRF-Token und einen UUID-`Idempotency-Key`. Bei TXT und DOCX liefert eine
  wiederholte Anfrage mit demselben Schlüssel das gespeicherte Ergebnis erneut. Beim KIS liefert jede wiederholte
  Anfrage das festgehaltene Ergebnis dieser Freigabe, unabhängig vom gesendeten Schlüssel.
- **Ein Dateiauftrag mit Status `ready` ist keine gespeicherte Datei.** Der Status `ready` bedeutet, dass die Datei
  auf dem Server erzeugt wurde. Die Ansicht zeigt **Datei erzeugt (noch nicht gespeichert)**. Die Klinikversion
  hat keine Route, die die erzeugte TXT- oder DOCX-Datei ausliefert. Der Auftrag behält nur Dateiname, MIME-Typ
  und SHA-256; aus **Ausgabe** lässt sich deshalb nichts herunterladen.
- **Zwischenablage** kopiert die freigegebene Textrevision über die Zwischenablage-API des Browsers (Clipboard
  API), als bewusste Benutzeraktion («Explizite Browseraktion. Es wird nichts automatisch übertragen.»). Der
  Server liefert den Text nur, solange die freigegebene Revision noch der aktuelle Text ist; sonst antwortet er mit
  `409`, und der Bereich zeigt **Die Version ist veraltet. Bitte neu laden.** Der Server bereitet die Formate vor
  und hält das vom Browser gemeldete Ergebnis fest (kopiert, verweigert oder abgebrochen). Nur das Ergebnis
  `copied` (kopiert) fügt eine Ausgabequittung an. Es gibt keine Automation von Fenstern, Fokus oder Tasten des
  Betriebssystems.
- **Nur über die API, ohne Klinikansicht:** `GET /api/clinic/v1/bundles/<Diktat-ID>/export` mit `format=txt`,
  `rtf` oder `sdict` liefert einer angemeldeten Browsersitzung mit **Exportieren** im Standard-Clientprofil
  `managed_web_online` den aktuellen Text (ohne Freigabe). Ein `.sdict`-Bündel enthält eine leere Audiodatei,
  kein Diktataudio.
- **Vorbereitet, deaktiviert:** Freigegebene serverseitige Exportordner (Server-Alias,
  Kollisionsregel `fail`, `version` oder `unique_name`), ODT-Ausgabe aus einer Vorlage und
  versionierte DOCX-Vorlagen mit Textmarken bestehen nur als Bibliothekscode. Keine Route verwendet sie.
- **Offene Lücken:** Das Einfügen in ein geöffnetes Word- oder Writer-Dokument (WG-EXP-004, WG-EXP-005),
  Fenster- und Tastenautomation (WG-EXP-007), das Öffnen eines lokalen Mailprogramms mit Audioanhang
  (WG-EXP-009) und ein Word-Add-in (WG-EXP-010) bleiben offen. Die Klinikversion bietet dafür keinen
  Ersatz.

## KIS-Schnittstelle (Simuliert)

Das KIS (Klinikinformationssystem) ist kein Webhook-Ziel. Eine KIS-Zustellung läuft ausschliesslich über
den konfigurierten KIS-Adapter, und dieser hängt nur von `CLINIC_PROFILE` ab:

| `CLINIC_PROFILE` | Adapter | Wirkung |
|---|---|---|
| nicht gesetzt oder ein anderer Wert | `DisabledAdapter` | Weist alles ab. Es wird nichts gesendet. |
| `staging` | `MockKisAdapter` | Synthetische, steuerbare Testattrappe, die Dokumente nur im Arbeitsspeicher hält. |
| `production` | `DisabledAdapter` | `MockKisAdapter` verweigert unter diesem Profil den Start. |

Keine Umgebungsvariable, keine Systemeinstellung und keine Konfigurationsdatei wählt einen echten
KIS-Adapter. Ein Herstelleradapter ist im Code vorbereitet: Er bleibt deaktiviert, bis alle zehn
Vertragsfelder des Herstellers und ein Transport vorliegen, und nichts in der Anwendung erzeugt ihn. Die
Abschnitte `kis` in `clinic-spec/config/dev.defaults.json` und `production.template.json` (zum Beispiel
`automatic_retry_unknown: false`) sind deklarativ und werden vom Code nicht gelesen. Das
Produktionsgate G-KIS ist offen: Der Herstellervertrag (WebView, Kontext und Rechte, Schreiben, Quittung,
Abfrage, Nachtrag) fehlt.

Mit `DisabledAdapter`:

- **Endgültig freigeben** wird mit `422` `UNTRUSTED_CONTEXT` abgewiesen.
- Ohne aktive Freigabe (der Regelfall, weil **Endgültig freigeben** scheitert) zeigt **Ausgabe** für TXT, DOCX
  und KIS **Keine freigegebene Version.**, und eine Ausgabeanfrage wird mit `409` `NOT_RELEASED` abgewiesen.
  Nur wenn bereits eine aktive Freigabe besteht (zum Beispiel aus einem früheren Staging-Betrieb mit derselben
  Datenbank), zeigt das KIS-Ziel **nicht verfügbar**, und eine KIS-Anfrage antwortet mit `503`
  `KIS_UNAVAILABLE` («Keine KIS-Anbindung konfiguriert»). In keinem Fall wird eine Zeile in das
  Zustellungsjournal `clinic_delivery` geschrieben.

Mit `CLINIC_PROFILE=staging` lässt sich über `/api/clinic/v1/dictations/<id>/context` eine synthetische
Fall-ID im Format `SYN-0001` (`SYN-` und 4 bis 8 Ziffern) verknüpfen. Nur in diesem Profil zeigt die
Diktatansicht **Details** den Bereich **Fall-ID verknüpfen** mit dem Feld **Synthetische Fall-ID** und der
Schaltfläche **Fall-ID verknüpfen**. Das Verknüpfen erfordert Lese- und Bearbeitungsrechte am Diktat sowie eine
aktuelle Bearbeitungssperre. Ohne Sperre antwortet die Route mit `423` («Eine aktive Bearbeitungssperre ist
erforderlich»), und die Ansicht zeigt **Bearbeitung zuerst anfordern.** Fehlt das Staging-Profil oder ein
aktiver `MockKisAdapter`, antwortet die Route mit `503` («Fall-ID-Simulation nicht verfügbar»). Sie
erfordert zudem das Clientprofil `managed_web_online` oder `managed_web_offline` (sonst `403`).
Ergebnisse mit `MockKisAdapter` sind kein Nachweis einer KIS-Anbindung.

### KIS-Kontext

- Ein KIS-Kontext (Quellsystem, Mandant, Fallreferenz, Patientenreferenz) entsteht nur über einen
  Berechtigungsnachweis (Grant), den der Server prüft: Er ist an die handelnde Person und das Diktat gebunden,
  läuft innerhalb von 60 Sekunden ab, und seine Nonce ist nur einmal verwendbar. Kontextrevisionen werden nur
  angefügt, nie geändert. Einen Resolver für einen echten KIS-Nachweis gibt es in der Klinikversion nicht. Der
  einzige Aufrufer ist die Staging-Simulation: Die Route `/api/clinic/v1/dictations/<id>/context` erzeugt den
  Nachweis selbst aus der eingegebenen `SYN-`-Fall-ID (Mandant `staging`, zufällige Patientenreferenz). Es
  findet keine externe Prüfung statt, und das ist kein Nachweis einer KIS-Anbindung.
- Der verbindliche Auftrag verbietet, den KIS-Kontext aus einem Fensternamen, dem DOM der Seite oder dem
  Transkript abzuleiten. Die Kontextfelder stammen ausschliesslich aus dem geprüften Nachweis.
- Die endgültige Freigabe akzeptiert nur die aktuelle Kontextrevision, die an einen verbrauchten Nachweis
  gebunden ist. Ein deaktivierter Adapter oder ein Mock-Adapter unter `CLINIC_PROFILE=production` gilt
  nie als vertrauenswürdig.
- Eine KIS-Zustellung sendet nur die an die Freigabe gebundene Kontextrevision, nur wenn deren vier
  Kontextfelder vorhanden sind und nur wenn der Adapter den Kontext annimmt.

### Unklares Ergebnis (UNKNOWN): Abgleich vor jedem neuen Versuch

- Die Zeile im Zustellungsjournal wird als `PENDING` gespeichert, bevor der Adapter aufgerufen wird. Der
  Journalschlüssel ist `export:<Freigabe-ID>`; jede Freigabe wird also höchstens einmal an ein Ziel
  gesendet.
- Eine Zeitüberschreitung oder eine unlesbare Antwort setzt die Zeile auf `UNKNOWN`. Ein unerwarteter Fehler lässt
  die Zeile auf `PENDING`, was ebenso sperrt. Eine wiederholte Anfrage sendet dann nichts und antwortet
  mit `UNKNOWN` («outcome unclear: reconcile first»). **Ausgabe** zeigt
  **Unbekannt: Bitte Status abgleichen, nicht erneut senden.**; die Schaltfläche **Status prüfen** liest nur den
  gespeicherten Zustand und sendet nie erneut.
- Nur ein Abgleich über die Abfragefunktion (Lookup) des Adapters klärt den Zustand. «Bestätigt» markiert die
  Zustellung als zugestellt. «Nicht vorhanden» erlaubt genau einen neuen Versuch. «Unbekannt» lässt sie
  gesperrt. Jeder Abgleich und jeder erlaubte neue Versuch wird als Verlaufszeile angefügt
  (`<key>#hNNNN:<KIND>`); Verlaufszeilen werden nie geändert.
- Die Anwendung reiht derzeit keinen Ausgabeauftrag (`clinic_output`) ein; KIS-Ausgaben aus **Ausgabe** laufen
  direkt über das Zustellungsjournal. Besteht ein solcher Auftrag und ist er fehlgeschlagen, nachdem die
  Zustellung begonnen hat, führt **Verarbeitung** ihn mit dem Zustand `UNKNOWN`. Statt **Erneut versuchen**
  erscheint **Abgleich prüfen**. Die Schaltfläche sendet nichts und führt keinen Abgleich aus; sie zeigt nur den
  Hinweis «Das Ergebnis ist unbekannt. Bitte im Abgleich prüfen.» Ein Wiederholungsauftrag über die API wird mit
  `422` `RECONCILE_REQUIRED` abgewiesen.
- Einen anderen fehlgeschlagenen Auftrag in **Verarbeitung** erneut zu versuchen, wird nur aktiven Konten mit der
  **Administrationsrolle** angeboten und nur für sie angenommen. Alle anderen sehen die Aufträge nur lesend.
- Keine Route und keine Seite führt den Abgleich aus. Er besteht nur als Servicefunktion, und es gibt
  keinen echten KIS-Adapter, gegen den er laufen könnte. Eine Zustellung mit `UNKNOWN` bleibt deshalb
  gesperrt, und der Code wiederholt sie nie automatisch.

Quellcode (Klinik-Fork): [src/clinic/kis.py](https://github.com/joehomeskillet/speakr/blob/main/src/clinic/kis.py).

## Was keine Alternative ist

- **Abfrage der API mit einem Token.** Im Klinikmodus wird eine Anfrage, die sich mit einem API-Token
  anmeldet und keine Browsersitzung hat, mit `403` `clinic_feature_disabled`
  (`personal_api_tokens`) abgewiesen. Auch die Token-Routen sind deaktiviert.
- **Web-Push.** Die Push-Routen (`push.*`) sind unter `push_notifications` deaktiviert.
- **E-Mail als Ersatz für KIS oder Webhooks.** Siehe [E-Mail-Einrichtung](email-setup.md).

## Verwandte Seiten

- [Systemeinstellungen](system-settings.md)
- [Benutzerverwaltung](user-management.md)
- [Gruppenverwaltung](group-management.md)
- [Auditprotokoll](audit-log.md)
- [Datenschutz und regulierte Daten](privacy.md)
- [E-Mail-Einrichtung](email-setup.md)
