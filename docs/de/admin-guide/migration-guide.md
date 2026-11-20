# Eine bestehende Speakr-Installation migrieren

[English](../../admin-guide/migration-guide.md)

Proben Sie das Upgrade zunächst an einer wiederhergestellten Kopie mit synthetischen Daten. Halten Sie Datenbanktyp, Anwendungsversion, eingebundene Volumes und Aufnahmemengen je Eigentümer fest. Sichern Sie Datenbank (konsistente SQLite-Kopie oder PostgreSQL-Dump), Uploads, getrenntes `CLINIC_ORIGINALS_ROOT`, Instanzverzeichnis, gegebenenfalls die Signaturschlüsseldatei sowie die geschützte Betriebskonfiguration. Prüfen Sie die Wiederherstellung vor der Umstellung.

1. Bauen Sie den Klinikfork vom öffentlichen Branch `main` nach der [Installationsanleitung](../getting-started/installation.md). Setzen Sie `CLINIC_MODE=1`, konfigurieren Sie freigegebene lokale Dienste für Transkription und optionale Textgenerierung und erhalten Sie Speicher- und Schlüsselzuordnungen.
2. Starten Sie den regulären Anwendungsdienst an der wiederhergestellten Datenbank. Er legt die Kliniktabellen an. Eine Instanz mit `CLINIC_WEB_ONLY=1` setzt eingerichteten Speicher und vorhandenes Schema voraus.
3. Bei der Ersteinrichtung erstellt der Betreiber die erste Identitätsadministration mit `scripts/clinic_bootstrap_admin.py`. Ein vorhandenes Konto ohne Klinikrichtlinie wird getrennt von der Identitätsadministration übernommen. Siehe [Benutzerverwaltung](user-management.md). Kontrollieren Sie interne ID und bisherige Autorbindung.
4. Richten Sie Autorenpool und ein gültiges Routenziel ein. Starten Sie im App-Container `python scripts/clinic_backfill_dictations.py --dry-run`. Prüfen Sie Anzahl und Gründe für übersprungene Aufnahmen, insbesondere unfertige Aufträge, fehlende Dateien und Eigentümer ohne routbaren Pool.
5. Nach Freigabe des Plans führen Sie `python scripts/clinic_backfill_dictations.py --apply` aus. Der nächste Probelauf soll keine doppelten Diktatanker vorschlagen. Für fehlende Audiofassungen prüfen Sie erst `python scripts/clinic_backfill_dictations.py --init-audio --dry-run` und danach `--init-audio --apply`. Die Reparatur benötigt tatsächliche Audiosamples. Dokumentieren Sie leere und fehlende Quellen.
6. Prüfen Sie mit einem berechtigten synthetischen Konto Listenansicht, Wiedergabe mit Positionssprung, Korrektur mit Speichern und erneutem Öffnen sowie erlaubte Dokumentausgabe. Gleichen Sie die Zählwerte je Eigentümer und Geltungsbereich ab. Bewahren Sie die Sicherung bis zur Abnahme auf.

## Connectoren und Speicher

Verwenden Sie einen unterstützten lokalen Transkriptionsconnector wie `openai_whisper` an einem lokalen kompatiblen Endpunkt oder den selbst betriebenen `asr_endpoint`. Prüfen Sie Modell, Zeitmarken und unterstützte Sprechertrennung mit neutralem Testmaterial. Prüfen Sie bei einem inkompatiblen Dienst den konfigurierten lokalen Endpunkt. Öffentliche Links, interne Freigaben und Cloudconnectoren werden nicht in Klinikabläufe übertragen. Historische Freigabezeilen schalten keine Funktion ein.

Die Upstream-Werkzeuge für alte Dateipfade, das Verlagern gewöhnlicher Aufnahmen nach S3 und das Ergänzen von Audiodauern betreffen eine andere Speicherschicht. Klinikoriginale bleiben lokal. Ein Rückweg benötigt zueinander passende Datenbank, Dateien, Schlüssel und Anwendungsversion. Allein der Tausch des Images macht zwischenzeitliche Schreibvorgänge nicht rückgängig. Siehe [Dateispeicher](storage.md).

[Originalanleitung von Speakr](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/migration-guide.md).
