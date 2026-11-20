# Dateispeicher und Wiederherstellung

[English](../../admin-guide/storage.md)

Ein Klinikdiktat hat einen Eingangsbeleg, ein unveränderliches Original und Arbeitsfassungen. Das Original liegt bytegetreu unter `CLINIC_ORIGINALS_ROOT`, standardmäßig innerhalb von `UPLOAD_FOLDER`. Das relative Verzeichnis lautet `clinic/originals/v1/`. Auch bei einem anderen Speicherbackend für die ASR-Arbeitskopie bleibt das Original dort. Eine Bearbeitung erzeugt eine neue Fassung. Wiedergabe, Dokumentexport und Download des Originalaudios haben getrennte Berechtigungen. Auch der Autor benötigt für den Originaldownload ausdrücklich `audio_export`.

## Sicherung planen

1. Sichern Sie Datenbank, Uploads, einen getrennten Originalspeicher und das Instanzverzeichnis zusammen. Sichern Sie Betriebskonfiguration und Signaturschlüssel gesondert mit beschränktem Zugriff.
2. Stellen Sie den Satz in einer isolierten Installation wieder her. Prüfen Sie die Autorzuordnung eines synthetischen Diktats und öffnen Sie eine gespeicherte Korrektur erneut.
3. Spielen Sie die Arbeitsfassung ab, springen Sie im Audio und öffnen Sie das Diktat nochmals. Vergleichen Sie bei einem synthetischen Fall mit gesondert berechtigtem Originaldownload Größe und SHA-256 des Originals mit dem Eingangsbeleg. Das Abspielen einer abgeleiteten Datei allein belegt die Wiederherstellung des Originals nicht.
4. Halten Sie Sicherungsdatum und Wiederherstellungsergebnis ohne klinische Inhalte fest. Stimmen Sie Zugriff und Aufbewahrung mit der für Sicherungen verantwortlichen Stelle ab.

## Fehlendes Audio untersuchen

Prüfen Sie eingebundene Volumes, freien Speicher, Leserechte und den vom laufenden Image verwendeten Originalspeicher. Beim Start meldet die Prüfung des Originalspeichers die Zähler `scanned`, `removed_leftovers`, `incomplete`, `awaiting_row` und `errors`. Unvollständige Belege und fehlende Quellen müssen geklärt werden. Aus intaktem Audio lässt sich die Wellenform neu ableiten. Fehlende Audiosamples lassen sich aus einer Wellenform nicht zurückgewinnen.

Die Klinikspeicherplanung sieht lokalen persistenten Speicher vor. Das übernommene `FILE_STORAGE_BACKEND` unterstützt für gewöhnliche Aufnahmeobjekte lokalen oder S3-kompatiblen Speicher. `FILE_STORAGE_KEY_PREFIX` und `FILE_STORAGE_STAGING_DIR` legen Schlüsselpräfix und Arbeitsverzeichnis fest. Die S3-Migrationswerkzeuge verlagern keine Klinikoriginale. Eine reine Bucket-Sicherung ist unvollständig. Automatische Aufbewahrung archiviert Aufnahmen. Prüfen und genehmigen Sie Dateibereinigungen getrennt.

[Originalanleitung von Speakr](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/storage.md).
