# Dateispeicher
<small>[English](../../admin-guide/storage.md)</small>

Die Klinikversion legt Audio an zwei getrennten Orten ab:

- Der **Klinik-Originalspeicher** enthält das unveränderliche Original jedes klinischen Diktats, das Audio abgeschlossener Phrasen eines Live-Diktats und die Audiosegmente, die im Audioeditor aufgenommen werden. Er ist immer ein Verzeichnis im lokalen Dateisystem der Anwendung und wird mit `CLINIC_ORIGINALS_ROOT` festgelegt.
- Die **Medienablage** aus dem Upstream-Speakr (`local` oder `s3`, festgelegt mit `FILE_STORAGE_BACKEND`) enthält die Medien der Basisaufnahmen: die Upload-Aufnahme eines Uploads oder einer Aufnahme im Browser, die Arbeitskopie eines Imports, die an die Spracherkennung geht, und die Aufnahmen, die schon vor der Umstellung auf die Klinikversion bestanden. Eine Basisaufnahme ist eine Zeile des Aufnahmemodells von Upstream-Speakr (`recording`), im Unterschied zu einem klinischen Diktat. «Basisaufnahme» und «Basisanwendung» bezeichnen auf dieser Seite immer dieses Upstream-Modell und seine Routen.

Die Klinikregeln dieser Seite gelten mit eingeschaltetem `CLINIC_MODE`. Ohne Klinikmodus legt ein Upload im Web kein Diktat an, der Startdurchlauf über den Originalspeicher läuft nicht, und die Medienablage verhält sich wie im Upstream-Speakr.

Upstream-Speakr-Dokumentation (fixiertes Original; beschreibt die beiden Ablagen ohne den Klinik-Originalspeicher): [File Storage](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/storage.md).

## Statusübersicht

| Funktion | Status | Hinweise |
|---|---|---|
| Klinik-Originalspeicher im lokalen Dateisystem | Verfügbar | Unabhängig von `FILE_STORAGE_BACKEND`. |
| Datenbanktrigger für Originale und Transkriptrevisionen | Verfügbar | Nur SQLite und PostgreSQL. |
| Startdurchlauf über den Originalspeicher | Verfügbar | Nur im Klinikmodus; meldet Befunde und entfernt nur übrig gebliebene harte Links. |
| Nichtdestruktive Audiobearbeitung, Berechnung bei jeder Anfrage | Verfügbar | Eine Bearbeitung schreibt nie in das Original. |
| Datenbanktrigger für die Tabellen der Audiobearbeitung | Vorbereitet, deaktiviert | Die Installationsfunktion ist vorhanden, wird beim Start aber nicht aufgerufen. Die Anwendung verweigert Änderungen nur über ORM-Listener. |
| Lokale Medienablage für Basismedien | Verfügbar | Standard; unverändert aus dem Upstream. |
| S3-kompatible Medienablage für Basismedien | Standardmässig aus | Unverändert aus dem Upstream. Nicht Teil des klinischen Speicherentscheids (siehe [S3-Ablage in der Klinikversion](#s3-ablage-in-der-klinikversion)). |
| Hashgeprüfte Ausgabe des Originalaudios mit einer nicht gesendeten `.eml`-Datei | Vorbereitet, deaktiviert | Das Modul hat keinen Aufrufer. Ein lokaler E-Mail-Entwurf mit dem Audio bleibt eine offene Lücke (WG-EXP-009). |
| Entfernen gespeicherter Dateien oder Bucket-Objekte durch die Aufbewahrung | Nur Upstream | Der Aufbewahrungslauf archiviert und löscht nichts; die übrigen Wege, die Daten löschen, nennt [Aufbewahrung](retention.md#pfade-die-weiterhin-daten-loschen). |
| Freigegebene Werte für Datensicherung und Wiederherstellung | Offene Lücke | Die Wiederherstellungswerte in der Produktionsvorlage sind `null`. Siehe [Datensicherung](#datensicherung). |

## Wer den Speicher einrichtet

- Alle Einstellungen, die festlegen, wo Daten liegen, sind Umgebungsvariablen der Installation (`.env` oder die Umgebung des Containers). Keine davon ist eine Systemeinstellung: **Systemeinstellungen** führt sie nicht auf, und keine Seite der Webverwaltung ändert sie. Sie werden vom Betrieb des Servers gesetzt. Die **Administrationsrolle** hat in der Weboberfläche keine Speichereinstellung.
- Änderungen wirken, sobald die Anwendungsprozesse neu gestartet werden.
- Die Grenze pro Konto, **Speicherlimit (MB)**, wird in **Benutzer und Rollen** gesetzt. Ändern darf sie nur eine lokale Identitätsadministration: ein Konto mit **Administrationsrolle** und **Identitätsadministration**, Status **Aktiv** und einer Anmeldung innerhalb der letzten 300 Sekunden. Die **Administrationsrolle** allein genügt nicht. Bleibt das Feld beim Anlegen eines Kontos leer, erhält das Konto den Wert der Systemeinstellung `default_storage_quota_mb` (Standardwert `0`, keine Grenze; siehe [Systemeinstellungen](system-settings.md)). Beim Bearbeiten eines Kontos entfernt ein leeres Feld die Grenze. Die vollständigen Voraussetzungen beschreibt [Benutzerverwaltung](user-management.md).

## Wo welche Daten liegen

| Daten | Ort | Ablage |
|---|---|---|
| Original eines Diktats (Upload im Browser, Rekorder im Browser, Importagent, Übernahme bestehender Aufnahmen) | `<CLINIC_ORIGINALS_ROOT>/clinic/originals/v1/<sha256>`. Der Dateiname ist der SHA-256 des Idempotenzschlüssels, nicht des Inhalts, und hat keine Endung. | Klinik-Originalspeicher, immer lokal |
| Audio einer abgeschlossenen Phrase eines Live-Diktats | Derselbe Ordner; der Dateiname ist der SHA-256 von `live:<Sitzungs-ID>:<Sequenz>`. Die Zeile steht in `clinic_live_phrase`. | Klinik-Originalspeicher, immer lokal |
| Im Audioeditor aufgenommenes Audiosegment für ein Einfügen oder Überschreiben | `<CLINIC_ORIGINALS_ROOT>/audio-segments/<Diktat-ID>/<uuid>.wav` | Klinik-Originalspeicher, immer lokal |
| Upload-Aufnahme eines Uploads oder einer Aufnahme im Browser | `<FILE_STORAGE_KEY_PREFIX>/<JJJJ>/<MM>/<Aufnahme-ID>/<Zeitstempel>_<Dateiname>` | Medienablage |
| Arbeitskopie eines Imports für die Spracherkennung | `clinic/imports/asr/<sha256>/Diktat.<Endung>`. `<sha256>` ist der Dateiname des Originals im Originalspeicher; `<Endung>` ist das erkannte Containerformat, `bin`, wenn es unbekannt ist. Das Schlüsselpräfix wird nicht angewendet. | Medienablage |
| Wiedergabekopie einer DS2/DSS-Basisaufnahme für den Browser (Klinikmodus) | `<Stamm>.playback.mp3` neben der Datei der Basisaufnahme (MP3, 64 kbit/s, 16 kHz, mono). Sie entsteht bei der Transkription oder bei der ersten Wiedergabe- oder Wellenformanfrage. | Nur lokale Medienablage. Mit `s3` schreibt die Transkription die Kopie neben ihren temporären Download im temporären Verzeichnis des Systems; dort wird sie von keinem Prozess entfernt. |
| Aufnahmen aus der Zeit vor der Umstellung, auch wenn die Übernahme bestehender Aufnahmen sie übernimmt | Ihr bisheriger Speicherort, unverändert | Medienablage oder ein älterer lokaler Pfad |
| Upload-Teilstücke des Importagenten | Datenbanktabelle `clinic_agent_chunk`. Die Teilstücke werden gelöscht, sobald die Übertragung gespeichert oder abgebrochen ist. Teilstücke von Übertragungen, die mit `INVALID` enden, ohne Abbruch ablaufen oder in `UNKNOWN` bleiben, werden nicht entfernt; es gibt keinen Aufräumauftrag dafür. | Datenbank |
| Teilstücke serverseitiger Aufnahmesitzungen | `UPLOAD_FOLDER/_sessions/<Sitzungs-ID>/` | Immer lokal; siehe [Aufnahmesitzungen](recording-sessions.md) |
| Transkriptrevisionen, Schnittliste der Audiobearbeitung, Freigaben, Export- und Ausgabequittungen | Datenbanktabellen | Datenbank |
| Abgeleitete Audiofassung des aktuellen Bearbeitungsstands, Wellenform einer Audiorevision, TXT-, DOCX- und Bündelexporte | Nicht gespeichert. Sie werden für jede Anfrage berechnet. | Keine |
| Wellenform-Zwischenspeicher der Basisaufnahmen | `UPLOAD_FOLDER/_peaks` | Immer lokal |
| Bereitstellungsordner für Uploads und Konvertierungen | `FILE_STORAGE_STAGING_DIR` | Immer lokal |

Ein Upload im Browser liegt deshalb mindestens zweimal vor: als Upload-Aufnahme in der Medienablage und als Original im Originalspeicher. Ein Import liegt als Original und als Arbeitskopie vor, eine übernommene Aufnahme als Quellaufnahme und als Original.

## Konfiguration

### Klinik-Originalspeicher

| Variable | Standardwert | Beschreibung |
|----------|--------------|--------------|
| `CLINIC_ORIGINALS_ROOT` | Wert von `UPLOAD_FOLDER` | Wurzel des Originalspeichers. Originale und Audiosegmente werden immer hier im lokalen Dateisystem abgelegt, unabhängig von `FILE_STORAGE_BACKEND`. |
| `UPLOAD_FOLDER` | `/data/uploads` | Lokales Upload-Verzeichnis der Anwendung und Standardwurzel des Originalspeichers. |

- Ein Prozess mit `CLINIC_WEB_ONLY=1` verlangt das bestehende Upload-Verzeichnis, führt keine Migration und keinen Startdurchlauf aus und schreibt kein Schema.
- Halten Sie den Wurzelpfad stabil. `clinic_original_version` speichert jeden Schlüssel relativ zur Wurzel. Anders bei Importen, die noch auf der **Klärliste** stehen: Ihre Zeilen in `clinic_import_original` speichern zusätzlich den absoluten Wurzelpfad, der beim Import galt, und eine spätere Zuweisung öffnet die Datei dort.
- `CLINIC_ORIGINALS_ROOT` muss auf einem Dateisystem liegen, das harte Links und `fsync` auf Verzeichnissen unterstützt. Originale und Audiosegmente werden mit `os.link` veröffentlicht. Schlägt der Link fehl, schlägt die Lieferung fehl: Ein Upload im Browser wird mit 500 `clinic_ingest_failed` abgelehnt, ein aufgenommenes Segment mit 503.

!!! warning "Schlüsselpräfix und Originalspeicher dürfen sich nicht überschneiden"
    Setzen Sie `FILE_STORAGE_KEY_PREFIX` nie auf `clinic`, `audio-segments` oder einen Pfad darunter, und legen Sie `CLINIC_ORIGINALS_ROOT` nie innerhalb von `UPLOAD_FOLDER/<FILE_STORAGE_KEY_PREFIX>` an. `scripts/clean_unreferenced_files.py` durchläuft diesen Baum und zählt nur die Pfade als referenziert, die in den Zeilen der Basisaufnahmen und der Upload-Teile einer Zusammenführung stehen. Es würde Originale, Audio von Live-Phrasen und Audiosegmente als nicht referenziert auflisten, und `--delete` oder `--move-to` würde sie entfernen.

### Medienablage (unverändert aus dem Upstream)

Alle Einstellungen werden aus der Umgebung gelesen. Für `local` ist nur `FILE_STORAGE_BACKEND` nötig; die `S3_*`-Werte sind nötig, wenn die Ablage `s3` ist. In der Klinikversion steuern diese Einstellungen die Medien der Basisaufnahmen und die Arbeitskopien der Importe. Sie verschieben nie Klinikoriginale oder Audiosegmente.

| Variable | Standardwert | Beschreibung |
|----------|--------------|--------------|
| `FILE_STORAGE_BACKEND` | `local` | Ablage: `local` oder `s3`. |
| `FILE_STORAGE_KEY_PREFIX` | `recordings` | Schlüsselpräfix (Pfad) für gespeicherte Aufnahmen in der Ablage. Arbeitskopien der Importe verwenden es nicht. |
| `FILE_STORAGE_STAGING_DIR` | `<UPLOAD_FOLDER>/_staging` | Lokaler Bereitstellungsordner für Uploads und Konvertierungen vor der endgültigen Ablage. |
| `S3_BUCKET_NAME` | (keiner) | Ziel-Bucket. Nötig, wenn die Ablage `s3` ist. Ohne Bucket wird keine S3-Ablage aufgebaut. |
| `S3_REGION` | (keiner) | Region des Buckets. Von den meisten Anbietern verlangt. |
| `S3_ENDPOINT_URL` | (keiner) | Dienstendpunkt. Für AWS S3 leer lassen; für MinIO und andere S3-kompatible Anbieter setzen. Ein Wert ohne Schema erhält `https://`. |
| `S3_ACCESS_KEY_ID` | (keiner) | Zugriffsschlüssel. |
| `S3_SECRET_ACCESS_KEY` | (keiner) | Geheimer Schlüssel. |
| `S3_SESSION_TOKEN` | (keiner) | Optionales Sitzungstoken für befristete Zugangsdaten. |
| `S3_USE_PATH_STYLE` | `false` | Pfadbasierte Adressierung. Für MinIO nötig; für Anbieter mit Adressierung über den Hostnamen auf `false` lassen. |
| `S3_VERIFY_SSL` | `true` | TLS-Zertifikate prüfen. Nur für ein lokales MinIO ohne TLS auf `false` setzen. |
| `S3_PRESIGN_TTL_SECONDS` | `900` | Gültigkeit vorsignierter URLs für Wiedergabe und Download (15 Minuten). |
| `S3_PRESIGN_PUBLIC_TTL_SECONDS` | `300` | Kürzere Gültigkeit, die beim Aufbau der Medienablage gelesen wird (5 Minuten). Die Audiorouten dieser Version übergeben `is_public=False` und verwenden `S3_PRESIGN_TTL_SECONDS`. Öffentliche Links zum Teilen gibt es in dieser Version nicht. |

- `S3_INTRANET_ENDPOINT_URL` wird beim Start gelesen, aber von keinem Code verwendet. Die Variable hat keine Wirkung.
- Die S3-Unterstützung braucht das Paket `boto3`. Die Lock-Dateien fixieren `boto3==1.43.118`, und ein aus dem Fork gebautes Image installiert es daraus.

## Klinikoriginale

### Wie ein Original geschrieben wird

1. Die Bytes werden in eine temporäre `.partial-`-Datei im Zielverzeichnis geschrieben und mit `fsync` auf den Datenträger gebracht.
2. Ihr SHA-256 muss dem angegebenen Hash entsprechen. Sonst wird die Datei verworfen und die Lieferung abgelehnt.
3. Die Datei wird mit einem harten Link unter ihrem endgültigen Namen veröffentlicht. Eine bestehende Datei dieses Namens wird nie ersetzt: Sie muss dieselben Bytes enthalten, sonst wird die Lieferung als Konflikt abgelehnt.
4. Auch das Verzeichnis wird mit `fsync` auf den Datenträger gebracht, und die veröffentlichte Datei wird erneut gehasht.
5. Erst danach wird die Zeile in `clinic_original_version` geschrieben: Hash, Grösse, der Schlüssel relativ zur Wurzel und eine JSON-Quittung. Das Diktat und seine Ankeraufnahme entstehen in derselben Transaktion. Die Ankeraufnahme hat keinen `audio_path`, damit ein Konvertierungsauftrag der Basisanwendung das Original nicht umschreiben kann, und sie trägt `deletion_exempt`.

Folgen:

- Schlägt das Festschreiben in der Datenbank fehl, bleibt die Datei ohne Zeile. Dieselbe Lieferung verwendet die Datei beim nächsten Mal wieder und ergänzt die Zeile. Das Protokoll zeigt `clinic ingest <key>: receipt transaction failed after the file was stored`.
- Derselbe Idempotenzschlüssel mit denselben Bytes liefert die bestehende Quittung. Es entsteht weder eine zweite Datei noch eine zweite Zeile.
- Ein Original ist höchstens 2 147 483 647 Bytes gross.
- Lässt sich das Original beim Eingang nicht dekodieren, bleibt die Quittung erhalten, und die erste Audiorevision wird zurückgestellt (Protokollzeile `clinic ingest: initial audio revision deferred (<code>)`). `python scripts/clinic_backfill_dictations.py --init-audio` listet die Diktate ohne Audiorevision auf; mit `--apply` legt es die fehlenden Revisionen an.

Welche Bytes zum Original werden:

- **Upload und Rekorder im Browser.** Die Datei, welche die Upload-Verarbeitung in diesem Moment für die Upload-Aufnahme gespeichert hat. Mit Konvertierungsprozessen (`CONVERT_QUEUE_WORKERS`, Standardwert `2`) wird eine lesbare Audiodatei so gespeichert, wie sie hochgeladen wurde, und erst später und nur in der Upload-Aufnahme konvertiert. Mit `CONVERT_QUEUE_WORKERS=0` konvertiert die Upload-Verarbeitung jede Datei noch während der Anfrage; der Originalspeicher erhält dann die konvertierte Datei. Die Upload-Wege beschreibt [Aufnahmesitzungen](recording-sessions.md).
- **Diktatdateien DS2 und DSS.** Das Original und die Datei der Basisaufnahme werden nie konvertiert. Die Transkription dekodiert sie. Im Klinikmodus liefern die Wiedergaberoute der Basisanwendung und das Öffnen des Audios eines Diktats durch eine aktive globale Administration eine separate MP3-Kopie aus, die neben der Datei der Basisaufnahme liegt (siehe [Wo welche Daten liegen](#wo-welche-daten-liegen)). Der Download der Basisanwendung liefert die DS2/DSS-Datei selbst. Mit `s3` leiten beide Routen auf das DS2/DSS-Objekt weiter, das Browser nicht abspielen können.
- **Importagent.** Die Bytes, die beim Abschluss aus den Upload-Teilstücken zusammengesetzt werden.
- **Übernahme bestehender Aufnahmen** (`scripts/clinic_backfill_dictations.py`). Eine Kopie des Audios der Basisaufnahme, gelesen über die Medienablage. Die Quellaufnahme und ihre Datei werden nur gelesen. Siehe [Migrationsanleitung](migration-guide.md).

### Unveränderlichkeit und Prüfung

- Datenbanktrigger auf SQLite und PostgreSQL weisen `UPDATE` und `DELETE` auf `clinic_original_version` und `clinic_transcript_revision` zurück. Auf SQLite lauten die Meldungen für Originale «clinic original is immutable» und «clinic original cannot be deleted», für Transkriptrevisionen «clinic transcript revision is immutable» und «clinic transcript revision cannot be deleted». Auf PostgreSQL weist je Tabelle ein Trigger `UPDATE` und `DELETE` mit «clinic original is immutable» beziehungsweise «clinic transcript revision is immutable» zurück. Der Migrationsabschnitt «clinic integrity» installiert sie bei jedem Start, der die Datenbank initialisiert. Andere Datenbanksysteme erhalten keine Trigger.
- Kein Pfad der Anwendung schreibt eine Originaldatei um oder löscht sie, sobald sie veröffentlicht ist und ihr zweiter Hash stimmt, auch wenn ihre Zeile nie geschrieben wird. Vor diesem Punkt entfernt die Anwendung nur ihre eigenen temporären Namen: Jede Lieferung entfernt ihren `.partial-`-Namen, auch wenn der angegebene Hash nicht stimmt, und eine eben veröffentlichte Datei, deren zweiter Hash nicht stimmt, wird wieder entfernt, bevor eine Zeile entsteht. Der [Startdurchlauf](#startdurchlauf) entfernt übrig gebliebene harte Links `.partial-`.
- Das Dateisystem schützt die Dateien nicht. Originale werden mit dem Modus `0600` angelegt und nicht schreibgeschützt; aufgenommene Audiosegmente erhalten `0400`. Wer als Benutzer der Anwendung oder als root Zugriff auf das Dateisystem hat, kann sie ändern oder löschen.
- Beim Lesen wird der Hash geprüft. Jede Berechnung prüft jede Quelle gegen den SHA-256 ihrer Zeile («stored audio source does not match its hash»). Wiedergabe- und Wellenformanfragen antworten dann mit 503, und eine wiederholte Erkennung lehnt mit `reprocess_audio_unavailable` ab. Eine wiederholte Erkennung eines Diktats ohne Audiorevision liest das Original direkt, prüft SHA-256 und Grösse und lehnt bei einer Abweichung mit `reprocess_audio_integrity_failed` ab.

### Startdurchlauf

Im Klinikmodus durchläuft jeder Start, der die Datenbank initialisiert, den Ordner `<CLINIC_ORIGINALS_ROOT>/clinic/originals/v1`. Ein Prozess mit `CLINIC_WEB_ONLY=1` führt ihn nicht aus.

| Fall | Bedeutung | Aktion |
|---|---|---|
| `removed_leftovers` | Ein `.partial-`-Name, der ein harter Link einer endgültigen Datei ist (Absturz zwischen Veröffentlichung und Aufräumen) | Der zusätzliche Name wird entfernt. Die Bytes bleiben unter dem endgültigen Namen erhalten. |
| `incomplete` | Eine `.partial-`-Datei ohne endgültige Datei, älter als eine Stunde | Nur gemeldet. Sie kann die einzige Kopie eines Diktats enthalten. |
| `awaiting_row` | Eine endgültige Datei ohne Zeile in `clinic_original_version` | Nur gemeldet. Eine wiederholte Lieferung ergänzt die Zeile. Audiodateien von Live-Diktatphrasen und von Importen, die noch auf der **Klärliste** stehen, haben ebenfalls keine solche Zeile und werden hier mitgezählt. |

Protokollzeilen:

- `Clinic original sweep: scanned=<n> removed_leftovers=<n> incomplete=<n> awaiting_row=<n> errors=<n>` (INFO).
- `clinic ingest sweep: {…}` mit denselben Zählern, als WARNING, wenn `incomplete` oder `errors` nicht null ist.
- `Clinic original sweep failed: <error>` (ERROR). Ein fehlgeschlagener Durchlauf hält den Start nie auf.

Prüfen Sie `incomplete` und `awaiting_row` nach einem Absturz oder einer Wiederherstellung, bevor Sie eine Datei von Hand entfernen. Das Protokoll zeigt nur Zähler, keine Dateinamen, und keine Seite der Administration listet die Dateien auf. Audio von Live-Diktatphrasen und Importe, die auf der **Klärliste** warten, halten `awaiting_row` im normalen Betrieb über null; vergleichen Sie die Zähler deshalb mit denen eines früheren Starts.

## Audiobearbeitung, abgeleitete Audiofassungen und Exporte

**Audiobearbeitung.** Bearbeitungen sind nichtdestruktiv: eine Schnittliste über dem unveränderlichen Original, mit mindestens 16 Rückgängig-Schritten. Die Tabellen `clinic_audio_edit_operation`, `clinic_audio_revision`, `clinic_audio_revision_snapshot`, `clinic_audio_segment` und `clinic_audio_action_log` lassen über ORM-Listener nur Ergänzungen zu. Ihre Datenbanktrigger sind vorbereitet, werden beim Start aber nicht installiert; direktes SQL wird daher nicht blockiert. `clinic_audio_session_state` hält die aktuellen Stapel für Rückgängig und Wiederherstellen und wird aktualisiert.

**Aufgenommene Segmente.** `POST /api/clinic/v1/dictations/<Diktat-ID>/audio-segments` nimmt WAV, WebM, Ogg oder MP4 bis 32 MiB an. Das Audio wird in ein WAV mit 16 kHz, mono, 16 Bit von höchstens 64 MiB dekodiert und mit einem harten Link unter `audio-segments/` veröffentlicht. Eine bestehende Datei wird nie ersetzt. Wird die Anfrage nicht festgeschrieben, wird die veröffentlichte Datei wieder entfernt. Sobald ihre Zeile festgeschrieben ist, entfernt die Anwendung sie nie.

**Abgeleitete Audiofassung.** `GET /api/clinic/v1/dictations/<Diktat-ID>/audio/derived` berechnet den aktuellen Bearbeitungsstand bei jeder Anfrage im Arbeitsspeicher, als WAV mit 16 Bit PCM, mono. Nichts wird gespeichert, und die Antwort trägt `Cache-Control: no-store`. Jede Auslieferung wird als `recording.content.read` mit `part=audio_derived` protokolliert. Die Wellenform einer Audiorevision für die Korrekturansicht wird auf Anfrage aus einer solchen Berechnung erstellt. Sie wird nicht in `_peaks` zwischengespeichert, und jede Berechnung wird mit `part=audio_peaks` protokolliert. Eine wiederholte Erkennung berechnet den aktuellen Bearbeitungsstand in ein temporäres Verzeichnis.

**Textexporte.** In der Ansicht **Ausgabe** werden TXT und DOCX im Arbeitsspeicher aus der Revision erzeugt, welche die aktive Freigabe bindet. Der Auftragseintrag hält Freigabe, Revision, Dateinamen und SHA-256 in einer Datenbankquittung fest. Keine Route liefert die Datei aus; der Status lautet **Datei erzeugt (noch nicht gespeichert)**. Der Bündel-Download `GET /api/clinic/v1/bundles/<Diktat-ID>/export?format=txt|rtf|sdict` erstellt die Datei für jede Anfrage aus der aktuellen Revision und speichert nichts. Nicht verwaltete Geräte (BYOD) erhalten 403.

**Originalaudio.** Das Recht **Originalaudio exportieren** (Aktion `audio_export`) steuert den Download der Basisanwendung `/audio/<Aufnahme-ID>?download=true`. Diese Route liefert die Datei der Basisaufnahme aus der Medienablage aus (mit `s3` als Weiterleitung). Sie liest nicht aus dem Originalspeicher und prüft die Datei nicht gegen den Hash des Originals. Bei einem Upload im Browser kann diese Datei die konvertierte Kopie sein (siehe [Welche Bytes zum Original werden](#wie-ein-original-geschrieben-wird)); der Download entspricht deshalb nicht zwingend dem Original. Das Modul für die hashgeprüfte Ausgabe von Originalen hat keinen Aufrufer. Das Recht beschreibt [Benutzerverwaltung](user-management.md).

## S3-Ablage in der Klinikversion

Status: Standardmässig aus. Der S3-Code ist unverändert aus dem Upstream und funktioniert für Basismedien. Entscheid D06 in `clinic-spec/config/decisions.json` lautet «Existing local persistent storage adapter; no new S3 cluster»; Objektspeicher ist damit nicht der klinische Speicherplan.

Mit `FILE_STORAGE_BACKEND=s3`:

- Upload-Aufnahmen und Arbeitskopien der Importe von Diktaten gelangen in den Bucket. Der Bucket enthält dann Diktataudio und braucht denselben Schutz wie der Anwendungsserver. Siehe [Datenschutz](privacy.md).
- Klinikoriginale, Audiosegmente, der Wellenform-Zwischenspeicher, der Bereitstellungsordner und der Sitzungsordner `_sessions` bleiben im lokalen Dateisystem. Der Ordner `uploads` enthält deshalb weiterhin Aufnahmeaudio und muss in der Datensicherung bleiben.
- Bei einem Upload im Browser lädt die Anwendung die Upload-Aufnahme einmal aus dem Bucket, um sie zu hashen und das Original zu schreiben.

### Wie Audio ausgeliefert wird

Im S3-Modus antworten die Audiorouten mit einer Weiterleitung `302` auf eine kurzlebige vorsignierte URL, statt die Datei durch die Anwendung zu leiten. Das gilt für die Wiedergabe- und Downloadroute der Basisanwendung und für das Öffnen des Audios eines Diktats durch eine aktive globale Administration (`/api/admin/clinic/dictations/<id>/audio`), die ihren Auditeintrag vor der Weiterleitung schreibt. Die URLs verfallen nach `S3_PRESIGN_TTL_SECONDS`. Bis dahin funktioniert die URL für alle, die sie besitzen. Die abgeleitete Audiofassung des Klinikeditors kommt nie aus dem Bucket, weil sie aus dem lokalen Originalspeicher berechnet wird.

!!! warning "Die vorsignierte URL muss für den Browser erreichbar sein"
    Weil der Browser zum Objektspeicher weitergeleitet wird, muss der Wert von `S3_ENDPOINT_URL` (oder der AWS-Endpunkt Ihrer Region) vom Client aus auflösbar und erreichbar sein, nicht nur aus dem Container der Anwendung. Steht die Anwendung hinter einem Reverse-Proxy, muss der Endpunkt des Objektspeichers ebenfalls für die Clients erreichbar sein.

    Unterscheiden sich der Ursprung der Anwendung und der Ursprung des Objektspeichers, muss der Bucket ursprungsübergreifende Anfragen (CORS) von Ihrem Webursprung zulassen. Der S3-Teil der [Migrationsanleitung](migration-guide.md) enthält Beispielregeln für AWS S3 und MinIO.

Anbieterbeispiele (AWS S3, MinIO, Backblaze B2, Cloudflare R2, Wasabi) stehen in der oben verlinkten Upstream-Dokumentation. Sie sind eine Upstream-Referenz, keine Anleitung für eine Klinikinstallation.

### Betriebshinweise (unverändert aus dem Upstream)

- **Gemischte Ablage.** Speakr liest aus beiden Ablagen zugleich. Nach dem Wechsel auf `s3` werden bestehende lokale Aufnahmen weiterhin aus der lokalen Ablage ausgeliefert, und nur neue Uploads gelangen in den Bucket.
- **Neuverarbeitung und Transkription.** Verarbeitungsprozesse holen ein Objekt für die Transkription in eine temporäre lokale Datei und entfernen sie danach. Grosse Dateien werden deshalb bei jedem Verarbeitungslauf heruntergeladen.
- **Bestehende Aufnahmen migrieren.** Ein Wechsel der Ablage betrifft nur neue Uploads. Die S3-Migrationsskripte der [Migrationsanleitung](migration-guide.md) arbeiten nur mit `recording.audio_path`. Sie lesen, verschieben oder löschen nie Klinikoriginale oder Audiosegmente.

## Wellenform-Zwischenspeicher

Dieser Zwischenspeicher bedient die Wellenformroute der Basisanwendung `/audio/<Aufnahme-ID>/peaks` für Basisaufnahmen. Die klinische Korrekturansicht verwendet ihn nicht (siehe [Audiobearbeitung, abgeleitete Audiofassungen und Exporte](#audiobearbeitung-abgeleitete-audiofassungen-und-exporte)).

Die Wellenform wird bei der ersten Anfrage aus dem Audio berechnet und als Peaks-Datei in `UPLOAD_FOLDER/_peaks` zwischengespeichert. Im Klinikmodus werden die Peaks einer DS2/DSS-Basisaufnahme aus ihrer Wiedergabekopie berechnet; fehlt die Kopie, legt diese Anfrage sie an.

- **Grösse.** Etwa 1 MB pro Stunde Audio und höchstens etwa 4 MB für eine Aufnahme.
- **Ort.** Der Ordner liegt immer im lokalen Dateisystem der Anwendung, auch mit `FILE_STORAGE_BACKEND=s3`. Im S3-Modus wird das Audio für die Berechnung einmal in eine temporäre Datei geladen.
- **Datensicherung.** Der Ordner kann von der Datensicherung ausgenommen werden. Eine fehlende Peaks-Datei wird bei der nächsten Anfrage neu berechnet.
- **Aufräumen.** Eine Peaks-Datei wird mit ihrer Aufnahme oder ihrem Audio gelöscht und ersetzt, wenn sich das Audio ändert. `scripts/clean_unreferenced_files.py` überspringt den Ordner.
- **Schreibgeschützter Speicher.** Lässt sich `UPLOAD_FOLDER` nicht beschreiben, werden keine Peaks berechnet, und pro Anwendungsprozess wird eine Warnung protokolliert. Bereits vorhandene Peaks-Dateien werden weiterhin ausgeliefert.

## Aufbewahrung, Löschen und Aufräumen

- **Die Aufbewahrung archiviert nur.** Kein Aufbewahrungsmodus entfernt eine Datei oder ein Bucket-Objekt. Die Ankeraufnahmen klinischer Diktate sind vom Archivierungslauf ausgenommen. Die Archivierung gibt keinen Speicher frei. Siehe [Aufbewahrung](retention.md).
- **Manuelles Löschen ist gesperrt.** Die Routen zum Löschen von Aufnahmen und Audio antworten im Klinikmodus mit 403 `clinic_feature_disabled` (Funktionssperre `clinical_user_hard_delete`).
- **Bereinigung von Aufnahmen ohne Eigentümer.** Unverändert aus dem Upstream. Sie löscht weiterhin Aufnahmen ohne Eigentümer samt ihren Medien. Siehe [Aufbewahrung](retention.md#pfade-die-weiterhin-daten-loschen).
- **Nicht referenzierte Dateien.** `scripts/clean_unreferenced_files.py` betrachtet nur einfache Dateien direkt in `UPLOAD_FOLDER` und den Baum unter `<FILE_STORAGE_KEY_PREFIX>` (bei S3 die Objekte unter diesem Präfix). Den Originalspeicher kennt es nicht. Bei der Standardwurzel und dem Standardpräfix `recordings` liegen die Ordner `clinic/` und `audio-segments/` ausserhalb des geprüften Bereichs, ebenso die Arbeitskopien der Importe, deren Schlüssel mit `clinic/` beginnen (siehe die Warnung unter [Klinik-Originalspeicher](#klinik-originalspeicher)). Keine Zeile verweist auf die Wiedergabekopien von DS2/DSS-Basisaufnahmen; das Skript listet deshalb diejenigen unter dem Schlüsselpräfix als nicht referenziert auf. Eine fehlende Kopie wird bei der nächsten Wiedergabe neu angelegt, und die Kopie wird mit ihrer Aufnahme gelöscht. Optionen und Beispiele stehen in [Aufbewahrung](retention.md#pfade-die-weiterhin-daten-loschen).
- **Kapazität.** Sobald ihre Zeile festgeschrieben ist, werden Originale, Audio von Live-Phrasen und Audiosegmente von der Anwendung nie entfernt, und Uploads, Importe und übernommene Aufnahmen liegen mindestens zweimal vor (siehe [Wo welche Daten liegen](#wo-welche-daten-liegen)). Planen Sie den Speicherplatz für den Originalspeicher, die Basismedien mit den Wiedergabekopien und das Wachstum der Datenbank gemeinsam, einschliesslich der Tabelle `clinic_agent_chunk` des Importagenten.

## Datensicherung

Diese Hinweise folgen aus der Art, wie der Code Daten schreibt. Kein Verfahren für Datensicherung oder Wiederherstellung wurde getestet.

Eine vollständige Datensicherung umfasst:

- die Datenbank: die SQLite-Datei (Standard `/data/instance/transcriptions.db`, festgelegt mit `SQLALCHEMY_DATABASE_URI`; die Anwendung betreibt SQLite im WAL-Modus, kopieren Sie die Datei deshalb zusammen mit ihren Dateien `-wal` und `-shm` bei gestoppter Anwendung, oder verwenden Sie die Online-Sicherung von SQLite) oder einen PostgreSQL-Dump;
- `UPLOAD_FOLDER`: Basismedien und, bei der Standardwurzel, den Originalspeicher mit seinen Audiosegmenten. `_peaks` und die Kopien `.playback.mp3` können ausgenommen werden; beide werden auf Anfrage neu angelegt;
- `CLINIC_ORIGINALS_ROOT`, wenn es ausserhalb von `UPLOAD_FOLDER` liegt. Es enthält die Originale auch mit `s3`;
- den Bucket, wenn `FILE_STORAGE_BACKEND=s3` gilt; seine Dauerhaftigkeit hängt vom Anbieter ab;
- `.env` und den Signaturschlüssel der Sitzungen: `SECRET_KEY` oder die erzeugte Schlüsseldatei (`SECRET_KEY_FILE`, sonst `secret_key` neben der SQLite-Datenbank, sonst in `/data/instance`). Ein Prozess mit `CLINIC_WEB_ONLY=1` erzeugt nie einen Schlüssel und startet ohne ihn nicht.

Reihenfolge und Wiederherstellung:

- Sichern Sie die Datenbank vor den Dateiablagen. Die Anwendung schreibt ein Original vor seiner Zeile und löscht es nie; eine so erstellte Datenbankkopie verweist daher auf kein Original, das in der späteren Dateikopie fehlt. Die umgekehrte Reihenfolge kann Zeilen ohne Datei hinterlassen; die Wiedergabe dieser Diktate schlägt dann mit 503 fehl. Diese Reihenfolge schützt nur den Originalspeicher. Basismedien können zwischen den beiden Kopien durch eine Konvertierung ersetzt oder gelöscht werden; die Datenbankkopie kann dann auf eine Datei einer Basisaufnahme verweisen, die in der späteren Dateikopie fehlt. Für eine übereinstimmende Kopie von beidem stoppen Sie zuerst die Anwendungsprozesse und die Verarbeitungsprozesse.
- Nach einer Wiederherstellung meldet der Startdurchlauf Dateien ohne Zeile als `awaiting_row`. Behalten Sie diese Dateien; sie können die einzige Kopie sein.
- Stellen Sie die Dateien mit dem Eigentümer des Anwendungsprozesses wieder her. Die Originale werden mit dem Modus `0600` angelegt.

Nicht festgelegt:

- Es gibt keine freigegebenen Wiederherstellungswerte. In `clinic-spec/config/production.template.json` sind `recovery.rpo_by_failure_domain`, `recovery.rto_by_data_class`, `recovery.restore_evidence` und `retention.backup_days` `null`. Eine Wiederherstellung wurde nicht getestet.
- Der Windows-Importagent hält auf seinem Arbeitsplatz einen verschlüsselten Pufferspeicher (`SpoolMaxBytes`, Standardwert 268435456 Bytes). Er gehört nicht zur Datensicherung des Servers, und die Installation des Agenten auf echten Arbeitsplätzen ist nicht getestet.
