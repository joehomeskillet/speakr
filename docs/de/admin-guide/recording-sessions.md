# Aufnahmesitzungen und unterbrochene Uploads

[English](../../admin-guide/recording-sessions.md)

Der Klinikrekorder hält Audio im Browserspeicher und sendet die fertige Aufnahme in einem Upload. Serverseitige Uploadsitzungen, Live-Diktat und Importagent sind getrennte Pfade. Deren Einstellungen machen aus dem Klinikrekorder keinen Dienst zur Wiederherstellung nach Abstürzen.

## Klinikrekorder: Neue Aufnahme

Öffnen Sie **Neue Aufnahme** unter `/#/neu`. **Diktiert von** zeigt die angemeldete Person; sie kann hier nicht geändert werden. Wählen Sie unter **Quelle** zwischen **Aufnehmen** und **Datei hochladen**.

Geben Sie vor einer Mikrofonaufnahme einen Titel ein. Die Bedienung erlaubt Starten, Pausieren, Fortsetzen, Beenden und Abhören. **Entwurf sichern** sendet die fertige Aufnahme an den Server. Eine ausgewählte Audiodatei nutzt denselben Uploadpfad. Die Seite akzeptiert eine nicht leere Audiodatei, einschliesslich unterstützter Diktatdateien mit den Endungen `.ds2` oder `.dss`.

Die Seite sendet einen Multipart-`POST /upload`; sie streamt keine Aufnahmeblöcke und teilt die Datei nicht in Uploadteile. `ENABLE_SERVER_RECORDING_CHUNKS` ändert daran nichts. Jeder Autor benötigt einen zuordenbaren Autorenpool. Ohne einen solchen Pool lehnt der Server den Upload als Klinikdiktat mit `clinic_no_author_pool` ab. Siehe [Pools](group-management.md).

Warten Sie vor dem Schliessen auf **Gespeichert. Noch nicht an den Schreibdienst gesendet.** und die Diktat-ID. Entwurf sichern und Einreichen sind getrennte Aktionen. **Einreichen** benötigt das Bearbeitungsrecht des Autors, einen Entwurf mit gültiger Audiofassung und die Zuordnung zu einem aktiven Schreibkräfte-Pool. Prüfen Sie das Ergebnis danach über **Zur Arbeitsliste** oder **Öffnen**.

## Fehlende Quittungen und Wiederherstellung

- Lassen Sie bei einem Speicherfehler den Tab offen. **Erneut sichern** oder **Erneut hochladen** wiederholt den Versuch mit der Aufnahme im Arbeitsspeicher oder der ausgewählten Datei.
- Prüfen Sie vor einem erneuten Versuch nach unklarer Antwort die Liste des Autors. Ein Upload kann den Server erreicht haben, obwohl der Browser keine Quittung erhalten hat.
- Bewahren Sie bei einer ausgewählten Datei die Quelldatei auf und wiederholen Sie den Upload nach Wiederherstellung der Verbindung von dort.
- Das Verlassen der Aufnahmeseite verwirft eine ungesendete Aufnahme. Neuladen, Schliessen des Tabs oder ein Browserabsturz verlieren die Kopie im Arbeitsspeicher. Ein bereits laufender Upload wird beim Verlassen der Seite nicht abgebrochen.

Der Klinikrekorder speichert Audio und Titel nicht absichtlich dauerhaft in IndexedDB, `localStorage` oder `sessionStorage`. Auch die Upstream-Speicher für fehlgeschlagene Uploads und Absturzwiederherstellung werden durch die Klinik-Persistenzsperre abgewiesen. Das garantiert keine Rückstandsfreiheit des Browsers oder Geräts; siehe [Datenschutz](privacy.md).

## Grenzen und Proxy-Einstellungen

| Einstellung | Standard | Gilt für |
|---|---|---|
| `RECORDING_MAX_HOURS` | `8` | Maximale Aufnahmedauer |
| `RECORDING_CHUNK_SECONDS` | `5`, begrenzt auf 1–60 | Aufnahmetakt des Browsers; Klinikblöcke bleiben im Arbeitsspeicher |
| `max_file_size_mb` | `250` beim ersten Start | Audiodateigrenze, soweit Chunking die Dateiprüfung nicht umgeht |
| `max_audio_only_video_size_mb` | 4 × `max_file_size_mb` beim ersten Start | Videoeingabegrenze; die Anwendungsgrenze pro Anfrage nutzt den grösseren Wert |

Der Browserrekorder hat zusätzlich eine Grössengrenze von 200 MiB und warnt bei etwa 80% davon. Speicherkontingente der Anwendung, Anfragegrenze und Reverse-Proxy-Grenzen sind getrennte Prüfungen. Ein höheres Speicherkontingent erhöht keine Uploadgrenze.

Da der Klinikrekorder die ganze Aufnahme in einer Anfrage sendet, muss die Proxy-Body-Grenze für die ganze erwartete Datei ausreichen. Planen Sie genügend Zeit für die Übernahme ein. Eine 16-MiB-Blockgrenze eines anderen Uploadpfads schützt diese Anfrage nicht vor einer kleineren Proxy-Grenze. [Systemeinstellungen](system-settings.md) beschreibt Upload- und Videoregeln.

## Serverseitige Uploadsitzungen

Die übernommenen `/upload/session`-Endpunkte bleiben für angemeldete Benutzer erreichbar und beschränken jede Sitzung auf ihren Eigentümer. Sie unterstützen Rekorderstreams und in Teile zerlegte grosse Datei-Uploads. Die normale Klinikseite **Neue Aufnahme** nutzt keinen dieser Transporte.

| Variable | Standard | Zweck |
|---|---|---|
| `ENABLE_SERVER_RECORDING_CHUNKS` | `false` | Blockstreaming des Upstream-Rekorders, nicht des Klinikrekorders |
| `RECORDING_SESSION_MAX_CHUNK_BYTES` | `16777216` (16 MiB) | Maximaler Block oder Dateiteil |
| `RECORDING_SESSION_MAX_BYTES_PER_USER` | `5368709120` (5 GiB) | Weiche Grenze offener Sitzungen je Benutzer |
| `RECORDING_SESSION_TTL_HOURS` | `24` | Inaktivitätsgrenze für die Bereinigung |
| `RECORDING_SESSION_CLEANUP_INTERVAL_SECONDS` | `3600` | Bereinigungsintervall; null oder weniger deaktiviert den Lauf |
| `RECORDING_SESSION_COMMIT_BATCH_SIZE` | `1` | Diesen Standard für die Blockbuchhaltung beibehalten |

Blöcke liegen unter `UPLOAD_FOLDER/_sessions/`. Der Abschluss eines geteilten Uploads setzt die Datei zusammen und übernimmt sie. Planen Sie Platz für die zusammengesetzte Kopie und einen ausreichend langen Proxy-Timeout ein. Der Abschluss einer Rekordersitzung erstellt eine Aufnahme und stellt das Zusammensetzen in die Warteschlange. Dieses Rekordersitzungsergebnis wird nicht automatisch zum Klinikdiktat; nutzen Sie gegebenenfalls das geprüfte [Verfahren zur Altbestandsübernahme](migration-guide.md).

Der Bereinigungslauf kann verlassene Rekordersitzungen mit Blöcken abschliessen, festhängendes Zusammensetzen erneut einreihen oder unvollständige Uploads ablaufen lassen. Abgelaufene temporäre Blöcke und verwaiste Sitzungsverzeichnisse können entfernt werden. Diese Bereinigung temporärer Uploads ist von der [Aufbewahrung von Aufnahmen](retention.md) getrennt, die Daten archiviert.

## Live-Diktat

**Live-Diktat** unter `/#/live` wählt ein bestehendes Diktat aus. Bedienelemente für Start, Pause, Fortsetzen, Beenden und Wiederverbinden sind vorhanden, ebenso Serverrouten zum Speichern endgültiger Sätze und zum Fortsetzen anhand von Quittungen. Live-Aktionen benötigen das Bearbeitungsrecht am Diktat. Die Sprechsperre läuft nach 60 Sekunden ohne erneuernde Aktion ab; beim Wiederverbinden wird geprüft, ob die Sitzung noch derselben Person gehört.

Die ausgelieferte Seite verbindet weder Audioaufnahme noch Erkennungsdatenstrom mit diesen Satzrouten. Der lokale Live-ASR-Adapter existiert, aber `CLINIC_LIVE_ASR_BASE_URL` allein bindet ihn nicht in die Seite ein. Stellen Sie diese Seite nicht als funktionierenden Live-Erkennungspfad dar. Der Wechsel zwischen Live-Entwürfen antwortet mit `CAPABILITY_UNAVAILABLE`.

Diktieren in fremde Windows-Felder, Sprechsperren an fremden Fenstern und native Fokusbindung bleiben die offenen Lücken WG-LIVE-002, WG-LIVE-006 und WG-LIVE-007.

## Offlinebetrieb und Importe

Der Server wählt das Clientprofil; vom Client gelieferte Header bestimmen es nicht. Ohne vertrauenswürdige Zuordnung nutzen Anfragen den konfigurierten Standard `managed_web_online`. Verwaltete Offlineablage ist nicht aktiviert: Die Capability-Route meldet fehlende Freigabe, weil keine vertrauenswürdige Browserprüfung und kein Grant-Provider eingebunden sind. **Offline und Wiederherstellung** bietet daher keine lokale Wiederherstellung.

**Dateien und Geräteimport** sowie die Verwaltungsseite **Import** betreffen freigegebene Geräte- und Dateisystemquellen. `apps/windows-import` ist der einzige lokale Agent; er importiert Dateien, nimmt kein Audio auf, spielt es nicht ab und automatisiert keine andere Anwendung. Servicecode für Registrierung und Quellenbereitstellung ist noch kein vollständiges Inbetriebnahmeverfahren. Simulatorausgaben belegen keine Philips-Gerätekompatibilität. Quellbereinigung unterliegt gesonderten Quittungs- und Richtlinienregeln; sie ist keine Wiederherstellung einer Browseraufnahme.

Nach abgeschlossenen Uploads zeigt **Verwaltung → Betrieb → Verarbeitung** die folgenden Verarbeitungsaufträge. Prüfen Sie bei einer Erfolgskontrolle das tatsächliche Diktat, die Wiedergabe und das Transkript; ein Warteschlangenstatus allein belegt keine Erkennungsqualität. Siehe [Lokale Spracherkennung](whisperx-setup.md).
