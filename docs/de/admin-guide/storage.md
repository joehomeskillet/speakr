# Dateispeicher und Wiederherstellung

[English](../../admin-guide/storage.md)

Ein Klinikdiktat besitzt ein unveränderliches Original samt Quittung, Arbeitsmedien sowie versionierte Text- und Audiobearbeitungen. Dies sind getrennte Speicher. Nur das gewöhnliche Uploadverzeichnis oder einen S3-Bucket zu sichern reicht nicht, wenn der Originalspeicher anderswo liegt.

## Speicherorte

| Daten | Speicherort |
|---|---|
| Übernommene Klinikoriginale | Lokale Dateien unter `CLINIC_ORIGINALS_ROOT/clinic/originals/v1/`; Quittung, Grösse und Inhaltshash stehen in `clinic_original_version`. |
| In eine Bearbeitung aufgenommenes Audio | `CLINIC_ORIGINALS_ROOT/audio-segments/<dictation id>/` |
| Uploadaufnahmen, ältere Aufnahmemedien und ASR-Arbeitskopien von Importen | Das übernommene Aufnahme-Speicherbackend, ausgewählt durch `FILE_STORAGE_BACKEND` |
| Transkripte, Revisionen, Schnittentscheidungen, Kontext, Freigaben, Zusammenfassungen, Import- und Ausgabequittungen, Auditnachweise | Datenbanktabellen |
| Serverseitige Aufnahmesitzungsblöcke | `UPLOAD_FOLDER/_sessions/` |
| Zwischenablage für Upload und Konvertierung | `FILE_STORAGE_STAGING_DIR`, normalerweise `UPLOAD_FOLDER/_staging` |
| Wellenformcache gewöhnlicher Aufnahmen | `UPLOAD_FOLDER/_peaks` |

Der Originalspeicher liegt immer lokal im Dateisystem der Anwendung, auch wenn gewöhnliche Aufnahmemedien S3 verwenden. Ein Browserupload kann deshalb als Aufnahmedatei und als Original vorliegen; eine importierte Datei als Original und als ASR-Arbeitskopie. Planen Sie Kapazität für beides.

Das Klinikoriginal ist exakt der Bytestrom, den der Klinik-Importschritt übernimmt. Ein Browserupload kann davor bereits die Upstream-Konvertierung durchlaufen haben, abhängig von Uploadpfad und Workerkonfiguration. Leiten Sie ohne Prüfung der Quittung nicht ab, dass jedes gespeicherte Original der ursprünglich im Browser gewählten Datei entspricht.

## Deployment-Konfiguration

Der Serverbetreiber konfiguriert den Speicher über Umgebungsvariablen. Die Webadministration verschiebt keine Speicherwurzeln und wechselt keine Backends. Kontospeicherlimits sind davon getrennt; siehe [Benutzerverwaltung](user-management.md).

| Variable | Standard | Zweck |
|---|---|---|
| `UPLOAD_FOLDER` | `/data/uploads` | Lokale Uploadwurzel |
| `CLINIC_ORIGINALS_ROOT` | `UPLOAD_FOLDER` | Dauerhafte Klinikoriginale und aufgenommene Audiosegmente |
| `FILE_STORAGE_BACKEND` | `local` | Backend gewöhnlicher Aufnahmemedien: `local` oder `s3` |
| `FILE_STORAGE_KEY_PREFIX` | `recordings` | Präfix gewöhnlicher Aufnahmeobjekte; Import-Arbeitskopien verwenden eigene Schlüssel |
| `FILE_STORAGE_STAGING_DIR` | `<UPLOAD_FOLDER>/_staging` | Lokale Zwischenablage, auch mit S3 erforderlich |

Halten Sie die Originalwurzel stabil: Klinikquittungen enthalten relative Schlüssel, während auf Zuordnung wartende Importoriginale zusätzlich ihre Speicherwurzel behalten. Das Dateisystem muss Hardlinks und Verzeichnis-`fsync` unterstützen. Prüfen Sie Mounts, Eigentum und Schreibrechte vor der Datenübernahme.

Originalspeicher und gewöhnlicher Aufnahmepräfix-Baum dürfen sich nicht überlappen. Die übernommene Bereinigung nicht referenzierter Dateien kennt Aufnahmereferenzen, aber nicht jedes Klinikoriginal, jede Livephrase oder jedes Audiosegment. Ein Aufnahmepräfix `clinic`, `audio-segments` oder ein übergeordneter Pfad des Originalspeichers kann Klinikdaten dieser Bereinigung aussetzen.

### S3-kompatibler Aufnahmespeicher

S3-Unterstützung bleibt im übernommenen Backend erhalten. Wählen Sie für den Klinikeinsatz einen durch die Institution kontrollierten Dienst und prüfen Sie Zugriffs- und Netzwerkrichtlinien. Klinikoriginale, Audiosegmente, Sitzungsblöcke und Zwischendateien werden dadurch nicht in den Bucket verschoben.

| Variable | Zweck |
|---|---|
| `S3_BUCKET_NAME` | Bucket; zum Aufbau des S3-Backends erforderlich |
| `S3_REGION` | Providerregion |
| `S3_ENDPOINT_URL` | Endpunkt eines S3-kompatiblen Dienstes |
| `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_SESSION_TOKEN` | Deployment-Zugangsdaten; Sitzungstoken optional |
| `S3_USE_PATH_STYLE` | Pfadbasierte Adressierung; Standard `false` |
| `S3_VERIFY_SSL` | Prüfung des TLS-Zertifikats; Standard `true` |
| `S3_PRESIGN_TTL_SECONDS` | Gültigkeit von Wiedergabe-/Download-URLs; Standard `900` Sekunden |

Lassen Sie die Zertifikatsprüfung eingeschaltet. Der Browser muss den Endpunkt einer vorsignierten URL erreichen können. Eine solche URL kann bis zu ihrem Ablauf verwendbar bleiben; schützen Sie sie deshalb wie Zugangsdaten. Der Klinik-Fork bietet keine öffentlichen Freigabelinks; die übernommene TTL öffentlicher Links aktiviert sie nicht.

Ein Backendwechsel migriert bestehende Dateien nicht. Die Upstream-S3-Migrationswerkzeuge behandeln Aufnahmemedien, nicht den Klinikoriginalspeicher. Ein Bucket allein ist daher keine vollständige Sicherung.

## Originalintegrität und Revisionen

Der Import schreibt eine temporäre Datei, synchronisiert sie, prüft deklarierte Grösse und SHA-256 und veröffentlicht sie per Hardlink, ohne ein bestehendes Original zu überschreiben. Vor dem Speichern der Quittung prüft er die veröffentlichte Datei erneut. Eine Wiederholung desselben Idempotenzschlüssels mit identischen Bytes verwendet die übernommene Lieferung erneut; abweichender Inhalt wird verweigert.

Schlägt die Datenbanktransaktion nach Veröffentlichung fehl, bleibt die Datei für einen erneuten Versuch erhalten. Sie darf nicht allein deshalb entfernt werden, weil noch keine Quittungszeile sichtbar ist. Originaldateinamen in `clinic/originals/v1/` werden aus dem Idempotenzschlüssel gebildet; der Inhaltshash der Datei ist ein eigener Quittungswert.

SQLite- und PostgreSQL-Trigger verweigern Änderungen und Löschung von `clinic_original_version` und `clinic_transcript_revision`. Audiobearbeitungen sind nichtdestruktive Revisionsdaten über dem Original. Ihre ORM-Listener verweigern Änderungen an nur erweiterbaren Bearbeitungsdatensätzen, der separate Installer für Audio-Datenbanktrigger wird beim Start aber nicht aufgerufen. Dateisystemzugriff als Anwendungsbenutzer oder root kann Dateien weiterhin verändern; Datenbank-Unveränderlichkeit ersetzt keinen Hostschutz.

Das Audiorendering prüft seine Quellen gegen die erfassten Hashes. Fehlende oder veränderte Quellen können Wiedergabe oder erneute Erkennung blockieren. Ein Wellenformcache kann fehlende Audiosamples nicht rekonstruieren.

### Prüfung beim Start

Die normale Klinik-Datenbankinitialisierung durchsucht den Originalspeicher. Ein Prozess mit `CLINIC_WEB_ONLY=1` überspringt Initialisierung und Prüfung. Das Protokoll fasst `scanned`, `removed_leftovers`, `incomplete`, `awaiting_row` und `errors` zusammen:

- `removed_leftovers`: Ein zusätzlicher `.partial-`-Hardlinkname wurde entfernt; seine veröffentlichte Datei bleibt.
- `incomplete`: Eine alte Teildatei hat kein veröffentlichtes Gegenstück. Sie wird gemeldet, nicht gelöscht.
- `awaiting_row`: Eine veröffentlichte Datei hat keine `clinic_original_version`-Zeile. Sie wird gemeldet, nicht gelöscht; auch Livephrasen und auf Zuordnung wartende Importe können zu diesem Zähler beitragen.
- `errors`: Prüfung oder Bereinigung eines zusätzlichen Namens ist fehlgeschlagen.

Vergleichen Sie die Zähler mit einem früheren Start. Untersuchen Sie unerwartete Anstiege nach Absturz oder Wiederherstellung, bevor Sie etwas entfernen. Ein Prüfungsfehler wird protokolliert, stoppt aber den Start nicht; eine laufende Anwendung belegt deshalb keinen erfolgreichen Prüflauf.

## Wiedergabe und Originaldownloads

Wiedergabe, Textexport und Originalaudioexport haben getrennte Rechte. Ein Audio-Download einer Aufnahme benötigt ausdrücklich `audio_export`, auch für den Autor. Standortadministration allein erteilt dieses Recht nicht.

Der übernommene Aufnahmedownload liefert das Medium aus dem Speicherverweis der Aufnahme. Er liest nicht die Klinikoriginalquittung und prüft deren Hash nicht; je nach Uploadpfad kann dieses Medium eine konvertierte Arbeitskopie sein. Verwenden Sie erfolgreiche Wiedergabe oder diesen Download allein nicht als Nachweis der Wiederherstellbarkeit des Klinikoriginals.

Bei lokalen DS2-/DSS-Aufnahmemedien kann die Klinikwiedergabe eine separate MP3-Ableitung erzeugen und die DS2-/DSS-Datei erhalten. Bei S3 leiten die übernommenen Medienrouten zum gespeicherten Objekt weiter; ein Browser kann ein DS2-/DSS-Objekt möglicherweise nicht dekodieren. Prüfen Sie bei fehlendem Ton sowohl die verfügbare Quelle als auch den vorgesehenen Wiedergabepfad.

Das separate Modul für hashgeprüfte Originalausgabe ist mit keiner unterstützten Benutzerroute verbunden. Das Erstellen eines lokalen Mailentwurfs mit Originalaudio bleibt die offene Lücke WG-EXP-009.

## Sicherungen und Wiederherstellungsprüfungen

1. Sichern Sie Datenbank, gewöhnliche Medien, die vollständige Originalwurzel bei separatem Speicherort und das Anwendungs-Instanzverzeichnis als konsistenten Satz. Schützen Sie Deployment-Einstellungen und Signaturschlüsselsicherungen separat mit eingeschränktem Zugriff.
2. Beziehen Sie einen verwendeten S3-Bucket sowie die zum Wiederherstellungsplan passenden lokalen Sitzungs- und Zwischenablagen ein. Halten Sie fest, welche Dateien temporär und welche dauerhaft sind; ein Dateiname allein belegt diese Unterscheidung nicht.
3. Stellen Sie in einer isolierten Installation wieder her. Prüfen Sie mit einem synthetischen Diktat Eigentum, Poolsichtbarkeit, gespeicherte Korrekturen und Freigabe-/Revisionsverknüpfungen nach erneutem Laden.
4. Spielen Sie das Arbeitsaudio ab, springen Sie darin und öffnen Sie es erneut. Vergleichen Sie separat Grösse und SHA-256 des wiederhergestellten Originals mit seiner Quittung und der synthetischen Quelle. Eine Ableitung ersetzt diese Prüfung nicht.
5. Halten Sie Sicherungsdatum, Wiederherstellungsergebnis und fehlende Nachweise ohne klinische Inhalte fest. Beschränken Sie den Zugriff auf wiederhergestellte Installation und Sicherungen.

Wiederherstellungswerte und -nachweise in der Produktionsvorlage bleiben offen. Legen Sie sie mit dem verantwortlichen Betreiber fest. Automatische Aufnahmeaufbewahrung archiviert Daten; jede Datei- oder Objektbereinigung benötigt einen eigenen geprüften Umfang und Wiederherstellungsplan. Siehe [Aufbewahrung](retention.md) und [Datenschutz](privacy.md).
