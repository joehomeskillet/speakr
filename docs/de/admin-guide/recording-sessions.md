# Aufnahmesitzungen
<small>[English](../../admin-guide/recording-sessions.md)</small>

Diese Seite erklärt, wie Audio in der Klinikversion vom Browser auf den Server gelangt, was der Browser behält und was die globale Administration konfigurieren oder einsehen kann. Sie behandelt die Klinikaufnahme **Neue Aufnahme**, die Seite **Live-Diktat**, die Clientprofile für eigene Geräte (BYOD) und das verwaltete Offlineprofil, die Verwaltungsseite **Import** für den Geräte- und Ordnerimport mit dem Kanal des Importagenten sowie die serverseitigen Aufnahmesitzungen aus Upstream, die im Code verblieben sind.

Alles Klinikspezifische auf dieser Seite gilt nur, wenn `CLINIC_MODE` auf `1`, `true`, `yes` oder `on` steht. Ohne Klinikmodus gilt das Upstream-Verhalten unverändert.

Upstream-Speakr-Dokumentation (fixiertes Original): [Server-side recording sessions](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/recording-sessions.md)

## Statusübersicht

| Funktion | Status | Kurzbeschreibung |
|---|---|---|
| Klinikaufnahme **Neue Aufnahme** | Verfügbar | Nimmt im Arbeitsspeicher des Browsers auf und sendet die fertige Aufnahme in einem einzigen Upload. Der Server legt einen Diktatentwurf an. |
| Datei hochladen auf **Neue Aufnahme** | Verfügbar | Eine Anfrage pro Datei, derselbe Weg wie bei einer Aufnahme. |
| Absturzwiederherstellung und Wiederholungsspeicher im Browser (IndexedDB) | Nur Upstream | Im Klinikmodus gesperrt. Der Browser speichert kein Audio. |
| Serverseitige Aufnahmesitzungen für den Recorder (`ENABLE_SERVER_RECORDING_CHUNKS`) | Nur Upstream | Streaming des Standard-Recorders. **Neue Aufnahme** verwendet es nie, unabhängig vom Schalter. Der Code-Standardwert ist `false`, die Vorlage `clinic-spec/config/upstream.env.example` setzt `true`. |
| Sitzungsendpunkte `/upload/session…` | Verfügbar | Für angemeldete Benutzer weiterhin erreichbar. Die Klinikseiten verwenden sie nicht. |
| **Live-Diktat**: Sitzung starten, pausieren, fortsetzen und beenden, Sprechsperre, Wiederaufnahme | Verfügbar | Serverseite und Bedienelemente der Seite sind vorhanden. |
| **Live-Diktat**: dauerhafte Speicherung von Sätzen | Vorbereitet, deaktiviert | Die Serverroute ist vorhanden, die ausgelieferte Seite sendet aber keine Sätze. |
| **Live-Diktat**: Audioerfassung und Live-Erkennung, die Sätze liefern | Vorbereitet, deaktiviert | Die ausgelieferte Seite hat weder Erfassung noch Erkennung angebunden. Es kommen keine Sätze an. |
| Wechsel zwischen mehreren Live-Entwürfen | Vorbereitet, deaktiviert | Die Route antwortet mit `CAPABILITY_UNAVAILABLE`. |
| Separates BYOD-Clientprofil | Vorbereitet, deaktiviert | Jede Anfrage erhält derzeit das Profil `managed_web_online`. |
| Verwaltetes Offlineprofil (verschlüsselte lokale Entwürfe) | Vorbereitet, deaktiviert | Nur synthetische Tests. Gate G-MANAGED-WEB-OFFLINE ist offen. |
| Live-Diktat in fremde Windows-Felder, Fensterbindung, Always-on-top | Offene Lücke | WG-LIVE-002, WG-LIVE-006, WG-LIVE-007 |
| Verwaltungsseite **Import** (Importaufträge, Geräteinventar, Importzuordnung, Klärliste) | Als Ansicht verfügbar | Die Listen bleiben in der freigegebenen Version leer; Zuweisen und erneutes Versuchen haben keine Serverroute. Siehe [Geräte- und Ordnerimport](#gerate-und-ordnerimport-verwaltungsseite-import). |
| Kanal des Importagenten (`/api/clinic/v1/agent/`) | Vorbereitet, deaktiviert | Kein unterstützter Weg registriert einen Agenten. Die Philips-Geräteseite ist Simuliert. |

## Klinikaufnahme: Neue Aufnahme

### Wo Benutzer sie finden

- Schaltfläche **Neue Aufnahme** in der Kopfzeile oder Menü **Verwaltung & Seiten**, Abschnitt **Diktat und Ausgabe**, Eintrag **Neue Aufnahme**.
- Direkter Link: `/#/neu`.

### Ablauf

1. **Diktiert von** zeigt die angemeldete Person. Sie kann auf dieser Seite nicht geändert werden, und es gibt keine Auswahl für Autor oder Pool.
2. **Quelle** bietet **Aufnehmen** oder **Datei hochladen**.
3. Für eine Aufnahme ist ein **Titel** erforderlich, bevor **Aufnahme starten** möglich ist. Die Bedienelemente sind **Pausieren**, **Fortsetzen**, **Aufnahme beenden**, **Zurückspulen**, **Abspielen** und **Abbrechen**. Die Seite nimmt vom Mikrofon auf.
4. **Entwurf sichern** sendet die Aufnahme an den Server. Der Hinweis auf der Seite lautet: «„Entwurf sichern“ speichert die Aufnahme auf dem Server. Mit „Einreichen“ geben Sie sie an den Schreibdienst weiter.»
5. Nach einer Serverquittung zeigt die Seite «Gespeichert. Noch nicht an den Schreibdienst gesendet.» und nur die Diktat-ID, ohne Titel und ohne Patientendaten. Die Aktionen sind **Einreichen**, **Zur Arbeitsliste**, **Öffnen** und **Weitere Aufnahme**.

Eine gewählte Datei darf nicht leer sein und muss einen Audio-MIME-Typ oder eine der Endungen `wav`, `mp3`, `m4a`, `aac`, `ogg`, `oga`, `opus`, `flac`, `webm`, `wma`, `ds2` oder `dss` haben.

### Übertragung: ein Upload, kein Streaming in Teilstücken

Die Seite sendet eine Aufnahme und eine gewählte Datei auf dieselbe Weise: als eine einzige Multipart-Anfrage `POST /upload`, nachdem die Erfassung beendet ist. Eine Aufnahme übermittelt zusätzlich `title` und `from_recorder=true`. Die Seite öffnet keine serverseitige Aufnahmesitzung und verwendet nicht den Transport in Teilstücken (Sliced Upload), auch nicht mit `ENABLE_SERVER_RECORDING_CHUNKS=true`.

Der Upload durchläuft die normale Verarbeitungskette. Im Klinikmodus legt der Server danach das Diktat an:

- Autor ist die angemeldete Person, die hochlädt. Pool ist der Autorenpool dieser Person. Inaktive Pools und Pools ausserhalb ihres Gültigkeitszeitraums werden übersprungen.
- Eine Person ohne zuweisbaren Autorenpool wird mit HTTP 403 `clinic_no_author_pool` abgewiesen («Ihr Benutzerkonto ist keinem Autorenpool zugeordnet. Die Aufnahme wurde nicht als Diktat angelegt.»). Die Seite selbst zeigt nur ihren allgemeinen Fehlertext. Richten Sie zuerst die Autorenpools ein, siehe [Gruppenverwaltung](group-management.md).
- Der Idempotenzschlüssel lautet `web:<Benutzer>:<Hash des Inhalts>`, weil diese Seite keine Client-Upload-ID mitsendet. Lädt dieselbe Person dieselben Bytes erneut hoch, erhält sie das bestehende Diktat zurück.
- Die Datei wird so in den Klinik-Originalspeicher unter `CLINIC_ORIGINALS_ROOT` (Standardwert `UPLOAD_FOLDER`) kopiert, wie die Verarbeitungskette sie abgelegt hat. Mit dem Standardwert `CONVERT_QUEUE_WORKERS=2` kommt eine lesbare Audiodatei so an, wie sie hochgeladen wurde, und wird danach nur in der Upload-Aufnahme umgewandelt. Mit `CONVERT_QUEUE_WORKERS=0` wird sie zuerst innerhalb der Anfrage umgewandelt. `.ds2`- und `.dss`-Dateien werden nie umgewandelt. Siehe [Dateispeicher](storage.md).
- Das neue Diktat beginnt im Workflowstatus Entwurf. Bei einem Fehler wird die halb angelegte Upload-Aufnahme entfernt, und die Antwort lautet «Die Aufnahme konnte nicht als Diktat angelegt werden. Bitte den Vorgang wiederholen.»

### Einreichen

**Einreichen** ruft den Workflowbefehl `send` für das Diktat auf. Er wird abgewiesen, ausser wenn:

- die aufrufende Person der Autor ist und das Recht `edit` auf dem Diktat hat,
- das Diktat noch ein Entwurf ist (sonst «Das Diktat ist kein neuer Entwurf mehr. Bitte den aktuellen Stand öffnen.»),
- eine Audiorevision vorhanden ist («Für diesen Entwurf liegt keine gültige Audiofassung vor.») und
- der Autorenpool an einen aktiven Schreibkräfte-Pool weitergeleitet wird («Kein Schreibkräfte-Pool zugeordnet.»).

Ein erfolgreiches Einreichen schreibt das Auditereignis `recording.edited` mit den Feldern `workflow_status` und `workflow_send`, auch wenn der allgemeine Auditschalter aus ist. Kann diese Zeile nicht geschrieben werden, schlägt das Einreichen mit `audit_unavailable` fehl. Siehe [Auditprotokoll](audit-log.md).

### Grenzen einer Aufnahme

| Grenze | Wert | Quelle |
|---|---|---|
| Grösse einer Aufnahme im Browser | Automatischer Stopp bei 200 MB, Warnung bei 80 % | Standard-Erfassungscode, fester Wert |
| Dauer einer Aufnahme | `RECORDING_MAX_HOURS`, Standardwert `8`, Warnung bei 80 % | Umgebungsvariable, wird in die Seite übernommen |
| Zeitscheibe des MediaRecorders | `RECORDING_CHUNK_SECONDS`, Standardwert `5`, begrenzt auf 1–60 | Umgebungsvariable. Auf dieser Seite bleiben die Teilstücke im Arbeitsspeicher des Browsers. |
| Upload-Grösse | Die Obergrenze pro Anfrage gilt immer. Sie ist der grössere Wert von `max_file_size_mb` (Startwert 250) und `max_audio_only_video_size_mb` (Startwert 4 × `max_file_size_mb`). Sie wird beim Start gesetzt und bei jeder Änderung einer dieser beiden Einstellungen neu berechnet. Die Grenze pro Datei `max_file_size_mb` gilt nur, solange das Aufteilen grosser Dateien (Chunking) ausgeschaltet ist. | [Systemeinstellungen](system-settings.md) |
| Speicherkontingent | Wird einmal beim Start einer Aufnahme geprüft: Ist das Kontingent voll, startet keine neue Aufnahme. Lässt sich die Prüfung nicht ausführen, blockiert sie nicht. Für eine Aufnahme aus einem angemeldeten Browser (`from_recorder=true`) wird es am Ende nicht nochmals geprüft. Gewählte Dateien werden geprüft. | Standard-Erfassungscode, Verarbeitungskette |
| Reverse-Proxy | Grenze der Anfragegrösse und Lese-Zeitlimit Ihres Proxys | Ihre Proxykonfiguration |

Weil die ganze Aufnahme in einer einzigen Anfrage übertragen wird, muss der Reverse-Proxy einen Anfragekörper so gross wie die grösste erwartete Aufnahme oder Datei annehmen, und sein Lese-Zeitlimit muss die Verarbeitung innerhalb der Anfrage abdecken. Eine Aufnahme über der Proxygrenze lässt sich nicht sichern. Sie existiert dann nur im Arbeitsspeicher des Browsers, bis die Seite verlassen wird (siehe nächster Abschnitt).

Beispiel für nginx. `client_max_body_size` ist die Obergrenze pro Anfrage, hier für die Startwerte (`max_file_size_mb` 250, `max_audio_only_video_size_mb` 1000):

```nginx
location = /upload {
    proxy_pass http://speakr_upstream;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    client_max_body_size 1000m;
    proxy_read_timeout 600s;
}
```

Jeder Block `location` braucht die gezeigten Zeilen `proxy_set_header`. Die Anwendung liest die weitergeleiteten Header über ihre Behandlung vertrauenswürdiger Proxys (`TRUSTED_PROXY_HOPS`, `TRUSTED_PROXIES`, siehe [Systemeinstellungen](system-settings.md)).

Der Block `recording` in `clinic-spec/config/dev.defaults.json` (`max_minutes` 30, `max_original_bytes`, `recorder_timeslice_ms`, `max_browser_ram_bytes` und weitere) ist ein Vertragswert. Kein Code liest ihn, eine Änderung bewirkt also nichts.

## Was der Browser behält und was nach einem Fehler geschieht

- Audio und Titel bleiben nur im Arbeitsspeicher des Browsers. Die Aufnahmeseite schreibt nichts in IndexedDB, `localStorage` oder `sessionStorage`.
- Der Standardspeicher für Absturzwiederherstellung und der Speicher für fehlgeschlagene Uploads (IndexedDB) sind im Klinikmodus gesperrt. Sie sind nur erlaubt, wenn das Seitenelement `#app` das Attribut `data-clinic-persistent-audio="True"` trägt. Keine Vorlage setzt dieses Attribut, die Speicherung ist also immer gesperrt.
- Schlägt **Entwurf sichern** fehl, zeigt die Seite «Senden fehlgeschlagen. Der Entwurf wurde nicht gesendet. Die Aufnahme wird nicht lokal gespeichert.» Die Aufnahme bleibt im Arbeitsspeicher, solange die Seite offen ist, und **Erneut sichern** versucht es nochmals. Für eine Datei lauten die Texte «Hochladen fehlgeschlagen. …» und **Erneut hochladen**.
- Ein fehlgeschlagener Upload einer Browseraufnahme über die Standard-Uploadwarteschlange zeigt «Der Upload ist fehlgeschlagen. Die Aufnahme wird nicht lokal gespeichert. Bitte erneut aufnehmen.»
- Bei einem Neuladen, einem geschlossenen Browser-Tab, einem Browserabsturz oder beim Verlassen der Seite vor einer bestätigten Sicherung geht die Aufnahme verloren. Auf dem Server liegt noch nichts, auf dem Gerät ebenfalls nicht. Beim Verlassen der Seite wird eine nicht gesendete Aufnahme verworfen. Ein bereits laufender Upload wird nicht abgebrochen.

## Live-Diktat

### Wo Benutzer es finden

Menü **Verwaltung & Seiten** > **Diktat und Ausgabe** > **Live-Diktat** oder `/#/live`. Live-Diktat arbeitet auf einem bestehenden Diktat. Die Person wählt im Feld **Diktat suchen** ein eigenes offenes Diktat («Eigenes offenes Diktat nach Name, Fall-ID oder Datum auswählen. Eine bekannte Diktat-ID ist optional möglich.») und klickt **Live-Diktat öffnen**, oder sie öffnet `?dictation=<id>`.

Die Bedienelemente der Seite sind **Starten**, **Pausieren**, **Fortsetzen** und **Beenden**. **Verbindung wiederherstellen** erscheint nach einer unterbrochenen Verbindung oder einer verlorenen Sprecherrolle, **Erneut speichern** bei nicht gespeicherten Sätzen.

### Verhalten auf dem Server

- Eine Live-Sitzung gehört zu einem Diktat und einer haltenden Person, nämlich der Person, die diktiert. Ihre Zustände sind `RECORDING`, `PAUSED` und `ENDED`. Die Datenbank erlaubt pro Diktat nur eine offene Sitzung.
- Jede Live-Aktion braucht eine angemeldete Browsersitzung, den Header `X-CSRF-Token` und das Recht `edit` auf dem Diktat. Pausieren, Fortsetzen, Speichern von Sätzen und Beenden sind nur der haltenden Person erlaubt.
- **Sprechsperre:** Solange eine Sitzung offen und ihre Sperre gültig ist, können andere Personen den Diktattext nicht ändern. Jede Aktion der haltenden Person verlängert die Sperre auf 60 Sekunden ab jetzt. Dieser Wert ist eine Konstante im Code, keine Einstellung. Nach 60 Sekunden ohne Aktivität läuft die Sperre ab: Dieselbe Person kann wieder aufnehmen, und eine andere Person mit `edit` kann eine neue Sitzung starten, welche die alte mit dem Grund `expired` beendet. Eine Route für ein Lebenszeichen (Heartbeat) gibt es nicht. Die ausgelieferte Seite verlängert die Sperre nur, wenn die haltende Person startet, wieder aufnimmt, pausiert oder fortsetzt. Nach 60 Sekunden ohne eine solche Aktion wird das nächste Pausieren oder Fortsetzen abgewiesen, und die Seite zeigt «Sie sind nicht mehr der aktive Sprecher. Neue Sätze werden nicht gespeichert.» **Verbindung wiederherstellen** nimmt die eigene Sitzung wieder auf, solange keine andere Person das Diktat inzwischen übernommen hat.
- **Dauerhafte Sätze:** Jeder endgültige Satz wird mit seinem Audio gespeichert. Das Audio wird im Klinik-Originalspeicher dauerhaft auf den Datenträger geschrieben (fsync), bevor die Satzzeile und die Sitzungsquittung festgeschrieben werden. Ein Satz gilt erst als **Gespeichert**, wenn die Serverquittung zu dieser Sitzung, Generation und Sequenz passt. Jeder Satz ist an die aktuelle Audio- und Textrevision des Diktats gebunden.
- **Wiederverbindung:** Der Aufruf zur Wiederaufnahme liefert die gespeicherte Generation, Sequenz und Audioposition sowie höchstens 50 gespeicherte Sätze nach der letzten Sequenz, die der Browser kennt. Der Server kann einen älteren Browser-Tab über eine Client-ID ausschliessen, die ausgelieferte Seite sendet aber keine.
- **Beenden:** Die Seite speichert vorher alle noch nicht gespeicherten Sätze. Gelingt das nicht, zeigt sie «Beenden nicht möglich: Es gibt nicht gespeicherte Sätze.» und sendet nichts. Der Server weist `stop_live_session` mit `REVISION_CONFLICT` ab, wenn die gesendete letzte Sequenz und Generation nicht zur gespeicherten Position passen.
- Die Seite hält Sätze, vorläufigen Text und Audioverweise nur im Arbeitsspeicher.

| Route | Zweck |
|---|---|
| `POST /api/clinic/v1/parity/commands` mit `start_live_session` | Sitzung öffnen |
| `POST /api/clinic/v1/live/sessions/<id>/pause` und `…/continue` | Pausieren und fortsetzen |
| `POST /api/clinic/v1/live/sessions/<id>/phrases` | Einen endgültigen Satz mit Audio speichern |
| `POST /api/clinic/v1/live/dictations/<id>/resume` | Ab der gespeicherten Position wieder aufnehmen |
| `POST /api/clinic/v1/parity/commands` mit `stop_live_session` | Sitzung beenden |
| `POST /api/clinic/v1/live/drafts/<id>/switch` | Entwurfswechsel. Nicht angebunden: antwortet mit `CAPABILITY_UNAVAILABLE`. |

Die Antworten der Routen unter `/api/clinic/v1/live/` tragen `Cache-Control: private, no-store`. Ohne Klinikmodus antworten die Live-Routen mit 404.

### Was nicht angebunden ist

- Die ausgelieferte Seite **Live-Diktat** hat weder Audioerfassung noch Erkennung angebunden. Sie kann eine Sitzung starten, pausieren, fortsetzen, beenden und wieder aufnehmen, aber es kommen keine Sätze an, und die Liste bleibt bei «Noch kein Text. Mit der Aufnahmetaste beginnen.»
- Der lokale Live-ASR-Adapter (`CLINIC_LIVE_ASR_BASE_URL`, `CLINIC_LIVE_ASR_TIMEOUT`, `CLINIC_LIVE_ASR_API_KEY`, `CLINIC_LIVE_ASR_MODEL`) ist im Code vorhanden und akzeptiert nur einen lokalen Endpunkt. Keine Route und kein Auftrag verwendet ihn, das Setzen dieser Variablen bewirkt also nichts.
- Der Code der Live-Sitzung schreibt kein Auditereignis für Starten, Pausieren, Fortsetzen, Speichern von Sätzen, Wiederaufnahme oder Beenden.
- Live-Diktat in fremde Windows-Textfelder, eine an ein fremdes Fenster gebundene Sprechsperre und die native Fokusbindung bleiben offene Lücken (WG-LIVE-002, WG-LIVE-006, WG-LIVE-007). Der Webweg ist der Live-Editor im Browser plus ein bewusster Export über die Zwischenablage oder als Datei.

Die Spracherkennung bleibt ausschliesslich lokal. Es gibt keinen Spracherkennungsdienst des Browsers und keine Cloud-Ausweichlösung. Zur Batch-Erkennung, die nach einem Upload läuft, siehe [Lokale Spracherkennung](whisperx-setup.md).

## Eigene Geräte (BYOD) und verwaltetes Offlineprofil

### Clientprofile

Das Clientprofil ist ein Serverentscheid. Weder ein Header noch ein User-Agent, ein Hostname oder ein Abfrageparameter wählt es. Eine vertrauenswürdige Zuordnung über den Eingangspunkt (Ingress) gibt es noch nicht. Deshalb erhält jede Anfrage, von einem verwalteten Arbeitsplatz wie von einem privaten Gerät, das Standardprofil `managed_web_online` aus `web_client.default_profile` in `dev.defaults.json`.

| Profil | Audioerfassung | Dauerhaftes Audio im Browser | Dokumentexport | Bezeichnung in der Oberfläche |
|---|---|---|---|---|
| `managed_web_online` | Browser | Nein | Berechtigungsgesteuert | Verwaltetes Web, nur online |
| `byod_internal` | Browser, im Vordergrund | Nein | Nein | Eigenes Gerät, nur online |
| `kis_webview` | Browser, falls der Host es zulässt | Nein | Herstellervertrag und Richtlinie | KIS-Ansicht, nur online |
| `managed_web_offline` | Browser | Verschlüsselt, nur nach ausdrücklicher Aktivierung und erst nach seinem Gate | Berechtigungsgesteuert | Verwaltetes Web, Offline freigegeben |

Die Profile stammen aus `clinic-spec/config/client-capabilities.json`. In allen Onlineprofilen behält der Browser kein Audio. Gate G-BYOD (freigegebene Gerätematrix, internes TLS/DNS, Restrisiko- und Unterbrechungsabnahme) ist offen. BYOD mit Echtdaten ist also nicht freigegeben.

### Verwaltetes Offlineprofil

- `GET /api/clinic/v1/offline-web/capability` (Klinikmodus, angemeldete Sitzung, `Cache-Control: no-store`) antwortet immer mit `available: false` und «Offline-Speicherung ist nicht freigegeben.» Weder eine vertrauenswürdige Browserprüfung noch eine Freigabequelle ist angebunden.
- `web_client.managed_offline_enabled` und `web_client.byod_persistence_enabled` stehen in `dev.defaults.json` auf `false`. `managed_offline_enabled` auf `true` zu setzen, schaltet allein nichts frei, weil der Route weiterhin ein geprüfter Browser und eine Freigabe fehlen.
- Die Seite **Offline und Wiederherstellung** (Menüabschnitt **Betrieb und Zugriff**, `/#/offline`) zeigt «Offline-Speicherung ist nicht freigegeben. Es wird nichts lokal gespeichert und es gibt keine lokale Wiederherstellung. Bereits auf dem Server bestätigte Daten bleiben über die Arbeitsliste erreichbar.»
- Die Module für Verschlüsselung und Speicherung (AES-GCM, PBKDF2-SHA-256, Löschen beim Abmelden) sind im Code vorhanden. Es wird aber kein Speicheradapter bereitgestellt, sie speichern also nichts.
- Gate G-MANAGED-WEB-OFFLINE (Tests zu Browserpersistenz, Schlüssel, Widerruf, Kontingent, Verdrängung, Absturz und Benutzerwechsel sowie Restrisikofreigabe) ist offen. Die Tests der Offlinerichtlinie sind synthetisch.

## Geräte- und Ordnerimport (Verwaltungsseite Import)

Die Verwaltungsseite **Import** (`/admin#/import`, Gruppe **Betrieb**) ist die Webansicht des Imports von Diktatdateien aus freigegebenen Geräten und Ordnern. Ihr Titel lautet **Dateien und Geräteimport**. Der gleichnamige Eintrag im Kopfzeilenmenü öffnet die Benutzeransicht `/#/import` mit denselben Bereichen.

Status: Die Seite ist als Ansicht Verfügbar. In der freigegebenen Version bleiben ihre Listen leer, weil nichts Importaufträge, freigegebene Quellen oder Lieferungen eines Agenten anlegt (siehe [Kanal des Importagenten und Eingang](#kanal-des-importagenten-und-eingang)). Die Philips-Geräteseite ist Simuliert.

**Zugriff**

- `/admin` prüft die Seite mit `GET /api/clinic/v1/imports/intents`. Die Prüfung gelingt nur für ein Konto mit der **Administrationsrolle** und einem Klinikkonto mit dem Status **Aktiv**. Die Importrouten lehnen jedes Konto ohne aktives Klinikkonto mit `403` ab.
- Der Servercode nimmt zusätzlich die Aktionen `import` und `manage_import_sources` an, sie sind aber keine erteilbaren Aktionen: Der Editor der **Fachrechte** und die Rechte-API weisen sie ab. Praktisch verwenden nur aktive globale Administrationen die Seite.

**Bereiche**

| Bereich | Inhalt | Status |
|---|---|---|
| **Importaufträge** | Vom angemeldeten Konto angelegte Importaufträge mit **Quelle**, **Dateien**, **Status** und **Erstellt**. Fehlgeschlagene oder abgelaufene Aufträge zeigen **Erneut versuchen**. Leere Liste: **Keine Importaufträge vorhanden.** | Als Ansicht verfügbar; die Liste bleibt leer, weil keine Route, kein Befehl und kein Skript einen Importauftrag anlegt. Die Katalogbefehle `queue_import`, `configure_routing` und `request_source_cleanup` haben keine Verarbeitung. **Erneut versuchen**: Offene Lücke, es gibt keine Serverroute. |
| **Geräteinventar** | Von registrierten Importagenten gemeldete Dateien mit **Quelle**, **Datei**, **Grösse**, **Aufnahmedatum** und **Stand**. Der Bereich hält fest: **Nur Ansicht. Konfiguration und Firmware-Schreiben sind nicht verfügbar.** Ohne Meldung: **Es liegt keine Meldung eines Importagenten vor. Es wird keine Geräteerkennung angezeigt.** | Nur lesend. Gerätedaten: Simuliert. Das Schreiben einer Gerätekonfiguration (WG-IMP-009) und das Schreiben von Firmware (WG-IMP-010) bleiben Offene Lücke. |
| **Importzuordnung** | Freigegebene Quellen (**Gerät** oder **Ordner**), deren Stammverzeichnis in `CLINIC_IMPORT_ROOT_ALLOWLIST` liegt, je mit **Importautor** und optionalem **Autorenpool**. Ohne Quelle: **Keine lokal freigegebene Quelle zur Verwaltung vorhanden.** | Verfügbar für vorhandene Quellen. Keine Quelle lässt sich im Web oder mit einem mitgelieferten Befehl anlegen, die Liste bleibt also leer. |
| **Klärliste** | Gelieferte Dateien ohne eindeutige Zuordnung, mit **Zuweisen** und **Zuweisung bestätigen**. Leere Liste: **Keine Dateien in Klärung.** | Sichtbar für aktive globale Administrationen und in `/#/import` für Leitungen der Pools, an die eine Quelle weiterleitet. **Zuweisung bestätigen**: Offene Lücke, es gibt keine Serverroute; die Aktion schlägt fehl und schreibt keine Auditzeile (siehe [Auditprotokoll](audit-log.md)). |

**Importzuordnung ändern**

1. Wählen Sie unter **Importzuordnung** bei der Quelle **Gerät … zuordnen** oder **Ordner … zuordnen**.
2. Wählen Sie den **Importautor**: ein beliebiges Konto mit aktivem Klinikkonto.
3. Wählen Sie unter **Autorenpool** optional einen Autorenpool dieser Person, oder lassen Sie **Ohne Poolvorgabe** stehen.
4. Klicken Sie auf **Speichern**. Die Seite zeigt **Importzuordnung gespeichert.**

Speichern kann nur eine aktive globale Administration. Jedes Speichern trägt die Version der Quelle mit; hat sich die Quelle inzwischen geändert, zeigt die Seite **Die Zuordnung wurde inzwischen geändert. Bitte erneut auswählen.** Jedes Speichern wird als `admin.setting.changed` mit dem Schlüssel `clinic_import_routing:<Quellen-ID>` protokolliert, auch bei ausgeschaltetem `audit_log_enabled`; lässt sich diese Zeile nicht schreiben, schlägt das Speichern fehl.

### Kanal des Importagenten und Eingang

Status: Vorbereitet, deaktiviert.

- Der Windows-Importagent (`apps/windows-import`) ist der einzige lokale Agent. Er ruft die Serverrouten unter `/api/clinic/v1/agent/` auf; der Server baut nie eine Verbindung zum Agenten auf.
- Die freigegebene Version hat keinen unterstützten Weg, einen Agenten in Betrieb zu nehmen. Einen Agenten mit dem Fingerabdruck seines Ausweises registrieren, eine freigegebene Quelle anlegen und einen Importauftrag anlegen gibt es als Serverfunktionen, aber keine Route, kein Befehl und kein Skript ruft sie auf; nur Tests tun es.
- Die Geräteseite ist Simuliert: Der Simulatormodus des Agenten und die Testdaten sind kein Nachweis für Philips-Geräte.

Der Kanal verwendet einen Maschinenvertrag mit mTLS. TLS mit Prüfung des Clientzertifikats endet an einem vertrauenswürdigen Reverse-Proxy. Dieser muss bei jeder Anfrage an `/api/clinic/v1/agent/` Folgendes tun:

1. Das Clientzertifikat des Agenten prüfen.
2. `X-Clinic-Client-Cert-Sha256` auf den SHA-256-Fingerabdruck des geprüften Zertifikats setzen, hexadezimal (Doppelpunkte, Leerzeichen sowie Gross- und Kleinschreibung werden ignoriert).
3. `X-Clinic-Proxy-Secret` auf den Wert von `CLINIC_AGENT_PROXY_SECRET` setzen (mindestens 32 Byte, zum Beispiel `<secret>`).
4. Beide Header bei jeder Anfrage überschreiben oder entfernen, damit ein Client nie eigene Werte durchreichen kann.

Eine Anfrage ohne das richtige Geheimnis, ohne Fingerabdruck, mit einem fehlerhaften oder unbekannten Fingerabdruck oder von einem deaktivierten Agenten erhält `401`. Diese Routen lesen das Sitzungscookie nie und sind von der CSRF-Prüfung ausgenommen. `CLINIC_AGENT_PROXY_SECRET` und `CLINIC_AGENT_MASTER_SECRET` brauchen je mindestens 32 Byte; ohne sie wird pro Prozess ein Zufallswert verwendet, und jede Anfrage des Agenten erhält `401` (siehe [Systemeinstellungen](system-settings.md#dateien-diktatformate-und-import)).

## Serverseitige Aufnahmesitzungen (Upstream-Mechanismus)

Upstream-Speakr kann die Teilstücke einer Aufnahme bereits während der Aufnahme an den Server streamen. Der Code ist in der Klinikversion weiterhin vorhanden. Sein Status hier:

- Die Klinikaufnahme verwendet ihn nie. Der Standard-Erfassungscode schaltet das Streaming ab, sobald der Klinikmodus aktiv und die Klinik-Aufnahmeseite geöffnet ist.
- `ENABLE_SERVER_RECORDING_CHUNKS` hat den Standardwert `false`. Die Klinikvorlage `clinic-spec/config/upstream.env.example` setzt ihn auf `true`. Keiner der beiden Werte wirkt sich auf **Neue Aufnahme** aus. Behandeln Sie ihn nicht als Schalter der Klinikaufnahme.
- Die Endpunkte sind im Klinikmodus als `AUTH` eingestuft, jede angemeldete Person kann sie also aufrufen. Ein synthetischer Test bestätigt, dass `POST /upload/session` im Klinikmodus 201 liefert.
- Eine Recorder-Sitzung, die von der Person oder von der Bereinigung abgeschlossen wird, wird zu einer Aufnahme mit der Quelle `recording_session`. Im Klinikmodus erhält diese Aufnahme beim Abschluss kein Klinikdiktat. Ein späterer Lauf von `scripts/clinic_backfill_dictations.py --apply` kann eine solche Aufnahme in einem Endstatus (`COMPLETED` oder `FAILED`) als Diktat übernehmen, siehe [Migrationsanleitung](migration-guide.md).
- Ein Upload in Teilstücken, der über `finalize-upload` abgeschlossen wird, wird wie `POST /upload` verarbeitet. Im Klinikmodus legt er deshalb ein Diktat an, mit derselben Autorenpool-Regel wie oben.
- Der Standard-Uploadcode sendet Dateien über 16 MiB in Teilstücken. Dieser Transport hat keine klinikspezifische Sperre. Die Klinikseiten verwenden ihn nicht.
- Der Standard-Uploaddialog mit seinen eigenen Aufnahmeschaltflächen ist weiterhin Teil des Codes der Klinikseite und hat keine Sperre für den Klinikmodus. Auch der Direktlink `/?upload=1`, der ihn öffnet, prüft den Klinikmodus nicht. Ob Benutzer den Dialog in der Klinikoberfläche erreichen, wurde nicht geprüft. Damit seine Aufnahme beim einzelnen Upload bleibt, lassen Sie `ENABLE_SERVER_RECORDING_CHUNKS` auf dem Code-Standardwert `false` statt auf dem Vorlagenwert `true`.

### Endpunkte

| Methode | Pfad | Zweck |
|---|---|---|
| POST | `/upload/session` | Sitzung anlegen. `{mime_type}` für eine Recorder-Sitzung, `{filename, total_bytes}` für einen Upload in Teilstücken. |
| POST | `/upload/session/<id>/chunks/<N>` | Teilstück N anhängen. Der Inhalt sind rohe Bytes. |
| GET | `/upload/session/<id>` | Status einer Sitzung |
| POST | `/upload/session/<id>/finalize` | Recorder-Sitzung: Aufnahme im Status `STITCHING` anlegen und einen Auftrag `stitch` einreihen |
| POST | `/upload/session/<id>/finalize-upload` | Upload in Teilstücken: Teilstücke zusammenfügen und innerhalb der Anfrage verarbeiten |
| DELETE | `/upload/session/<id>` | Abbrechen und gespeicherte Teilstücke entfernen |

Alle Endpunkte verlangen eine Anmeldung, und eine Sitzung ist nur für ihre Eigentümerin oder ihren Eigentümer sichtbar (andere erhalten 404). Die Pfade `/upload/session` sind von der Anfragebegrenzung pro IP-Adresse ausgenommen.

### Konfiguration

| Variable | Standardwert | Zweck |
|---|---|---|
| `ENABLE_SERVER_RECORDING_CHUNKS` | `false` | Streaming nur für den Standard-Recorder. Keine Wirkung auf die Klinikaufnahme. Nur die Zeichenfolge `true` (Gross- und Kleinschreibung beliebig) schaltet es ein, `1`, `yes` und `on` nicht. |
| `RECORDING_SESSION_TTL_HOURS` | `24` | Sitzungen mit älterem `last_seen_at` behandelt die Bereinigung. |
| `RECORDING_SESSION_MAX_BYTES_PER_USER` | `5368709120` (5 GiB) | Weiche Obergrenze pro Person für nicht abgeschlossene Sitzungen. Darüber antwortet die API mit 507. |
| `RECORDING_SESSION_MAX_CHUNK_BYTES` | `16777216` (16 MiB) | Obergrenze pro Teilstück. Grössere Teilstücke erhalten 413. |
| `RECORDING_SESSION_ALLOWED_MIME_TYPES` | `audio/webm,audio/ogg,audio/mp4,audio/mpeg,audio/wav,audio/x-m4a,video/webm,video/mp4` | Erlaubte Typen für Recorder-Sitzungen. Bei Uploads in Teilstücken nicht geprüft. |
| `RECORDING_SESSION_CLEANUP_INTERVAL_SECONDS` | `3600` | Intervall der Bereinigung. `0` oder kleiner schaltet sie ab. |
| `RECORDING_SESSION_INGEST_HEARTBEAT_SECONDS` | `30` (mindestens 5) | Lebenszeichen einer Verarbeitung innerhalb der Anfrage. Nach drei fehlenden Lebenszeichen darf ein anderer Abschlussaufruf die Sitzung übernehmen. |
| `RECORDING_SESSION_COMMIT_BATCH_SIZE` | `1` | Auf 1 lassen. Über 1 übergibt der Teilstück-Endpunkt seine Zählerstände nur an die Datenbanksitzung (flush), statt sie festzuschreiben. Laut Upstream-Dokumentation bleibt dadurch die Teilstückfolge stehen. |
| `RECORDING_MAX_HOURS` | `8` | Höchstdauer einer Aufnahme, mindestens 1 Minute. Gilt auch für die Klinikaufnahme. |
| `RECORDING_CHUNK_SECONDS` | `5` | Zeitscheibe des MediaRecorders, begrenzt auf 1–60 Sekunden |
| `RECORDING_VIDEO_KBPS` | `2500` | Videobitrate für die Standard-Erfassung von Tab oder Bildschirm, begrenzt auf 100–50000. Die Klinikaufnahme nimmt nur das Mikrofon auf. |

### Bereinigung und Ablage

- Teilstücke liegen unter `UPLOAD_FOLDER/_sessions/<id>/` als `chunk-000001.bin` und folgende, mit einer Kopie der Datenbankzeile in `session.json`. Ein Upload in Teilstücken schreibt beim Abschluss zusätzlich `joined.bin` und braucht dadurch kurzzeitig etwa die doppelte Dateigrösse.
- Die Bereinigung schliesst eine verlassene Recorder-Sitzung mit Teilstücken automatisch ab. Eine verlassene Recorder-Sitzung ohne Teilstücke und jeden verlassenen Upload in Teilstücken setzt sie auf `expired` und entfernt das zugehörige Verzeichnis. Für eine Sitzung, die im Status `finalizing` hängen geblieben ist, reiht sie den Auftrag `stitch` erneut ein. Sie entfernt auch verwaiste Sitzungsverzeichnisse, die älter als `RECORDING_SESSION_TTL_HOURS` sind. Sie protokolliert eine Zeile wie «Recording-session cleanup: … auto-finalized, … re-enqueued, … expired, … orphaned dir(s) removed».
- `DELETE` entfernt die Teilstücke sofort. Abgewiesen (409) wird es für eine abgeschlossene Sitzung (`session_already_finalized`) und für einen Upload in Teilstücken, der gerade abgeschlossen wird (`session_finalizing`).
- `finalize` antwortet sofort mit 202. Der Auftrag `stitch` läuft danach und nutzt `ffmpeg`. Fehlt es, wird eine Sitzung mit einem Segment ohne Remux als roher, aneinandergefügter Datenstrom übernommen (Warnung «ffmpeg not found; using raw byte-joined stream without remux»). Eine wiederaufgenommene Sitzung mit mehreren Segmenten scheitert dagegen im Auftrag `stitch` mit «ffmpeg binary not found on server PATH», und die Aufnahme erhält den Status `FAILED`.

### Reverse-Proxy für diese Pfade

Anfragen mit Teilstücken umfassen höchstens `RECORDING_SESSION_MAX_CHUNK_BYTES` (16 MiB). `finalize-upload` verarbeitet innerhalb der Anfrage, wie `POST /upload`, und braucht ein langes Lese-Zeitlimit. Beispiel für nginx:

```nginx
location /upload/session/ {
    proxy_pass http://speakr_upstream;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    client_max_body_size 32m;
    proxy_read_timeout 30s;
}

location ~* ^/upload/session/.+/finalize(-upload)?$ {
    proxy_pass http://speakr_upstream;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 600s;
    proxy_send_timeout 600s;
}
```

Für die Klinikaufnahme bemessen Sie stattdessen die Grenzen von `/upload` (siehe [Grenzen einer Aufnahme](#grenzen-einer-aufnahme)). Jeder Block braucht die üblichen Zeilen `proxy_set_header`.

## Was die Administration konfigurieren und sehen kann

- **Keine Verwaltungsseite** in **Verwaltung** zeigt laufende Aufnahmen, Live-Sitzungen oder serverseitige Aufnahmesitzungen, und keine konfiguriert die Aufnahme. Die Einstellungen sind Umgebungsvariablen und Systemeinstellungen.
- **Konfigurieren:** `RECORDING_MAX_HOURS`, `RECORDING_CHUNK_SECONDS`, Grenze der Anfragegrösse und Lese-Zeitlimit des Reverse-Proxys, `max_file_size_mb` in den [Systemeinstellungen](system-settings.md), `CLINIC_ORIGINALS_ROOT` (siehe [Dateispeicher](storage.md)) und die Variablen `RECORDING_SESSION_*`, falls die Sitzungsendpunkte verwendet werden.
- **Voraussetzungen für Benutzer:** Jeder Autor braucht einen zuweisbaren Autorenpool, sonst werden Uploads abgewiesen. **Einreichen** braucht einen Autorenpool, der an einen aktiven Schreibkräfte-Pool weitergeleitet wird. Siehe [Gruppenverwaltung](group-management.md) und [Benutzerverwaltung](user-management.md).
- **Rechte:** Live-Diktat braucht `edit` auf dem Diktat. Eine aktive globale Administration (Rolle **Administration**, Kontrollkästchen **Administrationsrolle**, dazu ein Klinikkonto mit Status **Aktiv**, siehe [Administrationshandbuch](index.md)) darf jedes Klinikdiktat ansehen und abspielen und seine Korrekturen lesen, erhält aber kein `edit`. `edit` entsteht nur aus ausdrücklich erteilten Rechten (**Fachrechte**, siehe [Gruppenverwaltung](group-management.md)). Die **Identitätsadministration** gibt keinen Zugriff auf Diktate. Nur Konten mit der **Administrationsrolle** ändern `max_file_size_mb`.
- **Einsehen:** Die Verwaltungsseite **Verarbeitung** (in `/admin` in der Gruppe **Betrieb**, `/admin#/verarbeitung`; im Kopfzeilenmenü im Abschnitt **Sprache und Verarbeitung**) zeigt die Transkriptionsaufträge nach einem Upload. Eine aktive globale Administration sieht alle Aufträge und darf einen fehlgeschlagenen Auftrag erneut ausführen. Alle anderen Konten sehen nur Aufträge von Diktaten, die sie ansehen dürfen. Das Auditprotokoll zeigt `recording.edited` für jedes Einreichen. Den Speicherbedarf von `UPLOAD_FOLDER/_sessions/` und des Originalspeichers sehen Sie nur auf dem Server.
- **Nicht konfigurierbar:** Zeitlimit der Sprechsperre (60 Sekunden), Fenster von 50 Sätzen bei der Wiederaufnahme, Speicherung im Browser und Zuordnung der Clientprofile.
