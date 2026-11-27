# Bestehende Speakr-Installation migrieren

[English](../../admin-guide/migration-guide.md)

Proben Sie die Migration vor einem Upgrade auf einer wiederhergestellten Kopie. Erfassen Sie Datenbanktyp, Anwendungsversion, Volume-Zuordnungen, Konto-IDs, Aufnahmeanzahlen und Eigentümerschaft. Verwenden Sie synthetisches Material für Funktionsprüfungen. Dieses Verfahren erteilt keine Produktions- oder klinische Freigabe.

Der Klinik-Fork veröffentlicht Releases auf `main`; `dev` ist der Entwicklungs- und Integrationszweig. Wählen Sie die beabsichtigte Klinikrevision ausdrücklich. Ursprüngliche Speakr-Konfiguration und ursprüngliches Verhalten reichen zum Betrieb des Klinik-Forks nicht aus.

## 1. Sichern und Wiederherstellung prüfen

Sichern Sie eine konsistente Datenbank, Uploads, das getrennte `CLINIC_ORIGINALS_ROOT`, das Instanzverzeichnis, eine gegebenenfalls separat konfigurierte Signaturschlüsseldatei und die geschützte Bereitstellungskonfiguration. Schliessen Sie das gewöhnliche Aufnahme-Backend ein, falls es ein Objektspeicher ist. Datenbankzeilen und Klinikoriginale müssen gemeinsam wiederhergestellt werden. Siehe [Dateispeicher](storage.md).

Prüfen Sie die Wiederherstellung vor Änderungen. Behalten Sie den Sicherungssatz von vor dem Upgrade bis zur Abnahme. Ein Wechsel des Anwendungsimages allein kann Schema- oder angenommene Datenänderungen nicht zurücknehmen.

## 2. Klinikanwendung konfigurieren

Bauen Sie die gewählte Klinikrevision wie unter [Installation](../getting-started/installation.md) beschrieben. Erhalten Sie Datenbank-, Speicher- und Signaturschlüsselzuordnungen. Setzen Sie `CLINIC_MODE=1` und konfigurieren Sie freigegebene lokale Transkriptionsdienste. Falls ein Textmodell konfiguriert ist, verwenden Sie einen freigegebenen lokalen Endpunkt; setzen Sie nicht nur einen Textmodell-API-Schlüssel im Vertrauen auf eine Standardadresse. Siehe [Modellwahl](model-configuration.md).

Belassen Sie Entra, LDAP und Windows-Anmeldung als vorbereitete, deaktivierte Integrationen. Ursprüngliche `ENABLE_SSO`-Einstellungen aktivieren die Klinik-Identitätsadapter nicht. Belassen Sie `ENABLE_INQUIRE_MODE=false` für den Klinikarbeitsplatz. Siehe [Anmeldung und Identitätsanbieter](sso-setup.md) und [Vektorspeicher](vector-store.md).

## 3. Schema initialisieren

Starten Sie einen normalen Anwendungsprozess gegen die wiederhergestellte Datenbank. Der normale Start initialisiert Kliniktabellen und Migrationen. Ein Prozess mit `CLINIC_WEB_ONLY=1` setzt vorhandenes Schema und vorhandenen Speicher voraus; er führt diese Initialisierung nicht aus. Prüfen Sie Startfehler, bevor Sie fortfahren oder weitere reine Webworker ergänzen.

Die folgenden Konto- und Aufnahmeübernahmen erfolgen ausdrücklich; das Aktivieren des Klinikmodus übernimmt weder automatisch bestehende Benutzer noch verwandelt es jede Aufnahme in ein Klinikdiktat.

## 4. Identitätsadministration herstellen

Erstellen Sie die erste Klinikadministration mit Anwendungsumgebung und Datenbank:

```bash
python scripts/clinic_bootstrap_admin.py --username <username> --email <email-address>
```

Das Skript liest sein Passwort sicher aus einer verdeckten Eingabe, der Standardeingabe oder der konfigurierten Passwort-Umgebungsvariable. Es erstellt **Administrationsrolle** und **Identitätsadministration**. Es ergänzt keine Fachrechte. Verwenden Sie die ursprünglichen Docker-Variablen `ADMIN_*` nicht als Ersatz; deren Konto fehlt die Klinikrichtlinie.

Ein lokales Bestandskonto ohne Richtlinie kann eine frisch angemeldete Identitätsadministration über `PUT /admin/users/<id>` mit CSRF-Schutz und genau diesem Inhalt übernehmen:

```json
{"adopt_existing_local":true,"expected_version":0}
```

Die Übernahme erhält interne ID, Passwort, Aufnahmeeigentümerschaft und vorhandene Administrationsrolle. Sie benötigt einen unterstützten lokalen Passwort-Hash und keine externe Identität oder Verknüpfungsanfrage. Prüfen Sie vorhandene Administrationskennzeichen vor der Aktivierung. Führen Sie Konten nicht anhand gleicher E-Mail-Adressen zusammen. Die Oberfläche hat keine Übernahmefunktion; [Benutzerverwaltung](user-management.md) beschreibt Einschränkungen, Aktivierungscodes und Wiederherstellungslücken.

## 5. Autorenpools und Weiterleitung einrichten

Erstellen Sie Autorenpools, ergänzen Sie die vorgesehenen Autoren und verknüpfen Sie diese mit besetzten, aktuell gültigen Schreibkräfte-Pools. Ein Eigentümer ohne zuordnungsfähigen Autorenpool wird bei der Übernahme übersprungen. Ursprüngliche Gruppen werden nicht automatisch zu Klinikpools. Halten Sie Autorenressourcen und Schreibteam-Zuständigkeit getrennt. Siehe [Pools und Berechtigungen](group-management.md), einschliesslich der nur per API verfügbaren Autorenpool- und Verknüpfungsoperationen.

## 6. Aufnahmen prüfen und übernehmen

Führen Sie in der Anwendungsumgebung aus:

```bash
python scripts/clinic_backfill_dictations.py --dry-run
```

Prüfen Sie jeden Kandidaten und Überspringungsgrund. Der Planer betrachtet Aufnahmen mit gespeichertem Audiolocator und prüft abgeschlossenen Verarbeitungszustand, vorhandene Mediendatei, Eigentümerschaft, Autorenpool und vorhandene Übernahmequittungen. Fehlende Medien, unfertige Aufträge oder fehlende Autorenpools benötigen Untersuchung, keine erzwungene Annahme.

Der normale Backfill-Befehl importiert die Anwendung. Obwohl der Übernahme-Probelauf keine Diktate erstellt, gilt weiterhin die normale Startinitialisierung; verwenden Sie zuerst die wiederhergestellte Probeinstanz.

Nach Freigabe der Vorschau:

```bash
python scripts/clinic_backfill_dictations.py --apply
```

Das Skript speichert ein Klinikoriginal, erstellt einen Anker und einen Workfloweintrag im Entwurfszustand und übernimmt ein abgeschlossenes Transkript als erste ASR-Revision. Quellaufnahme und Datei bleiben erhalten. Der Anker erhält ursprüngliches Aufnahmedatum, Verarbeitungsstatus und zwischengespeicherte Audiodauer. Die Übernahme kopiert weder alten Titel noch Notizen oder Tags in das neue Diktat. Sie verwendet den ursprünglichen Eigentümer und einen stabilen Übernahmeschlüssel; wiederholte Läufe erstellen keine doppelten Anker.

Eine Quellzusammenfassung ist keine aktuelle, revisionsgebundene Klinikzusammenfassung. Wo sie als Altbestand angezeigt wird, bleibt sie veraltet. Bestehende Diktate erhalten nicht allein durch ein Anwendungsupgrade eine neue automatische Zusammenfassung.

## 7. Fehlende initiale Audiorevisionen reparieren

Manche bestehende Diktate benötigen eine initiale Audiorevision für die Wiedergabe. Prüfen Sie dies getrennt:

```bash
python scripts/clinic_backfill_dictations.py --init-audio --dry-run
```

Wenden Sie anschliessend die geprüfte Reparatur an:

```bash
python scripts/clinic_backfill_dictations.py --init-audio --apply
```

Dieser Weg prüft Originaldatei und Hash, ermittelt die echte Audiodauer und erstellt eine Revision nur dort, wo sie fehlt. Er benötigt die tatsächlich gespeicherten Medien und Decoderunterstützung. Ein fehlendes, leeres oder unlesbares Original ist keine erfolgreiche Reparatur. Behalten Sie das Ergebnis pro Diktat einschliesslich Überspringungs- und Fehlergründen.

## 8. Betrieb und Anzahlen prüfen

1. Führen Sie `python scripts/clinic_roles_report.py` für eine schreibgeschützte Momentaufnahme von IDs, Rollen, Richtlinien, Pools, Rechten, Diktatzustand und Aufträgen aus.
2. Melden Sie sich als berechtigter synthetischer Benutzer an. Prüfen Sie Arbeitslisten- und Aufnahmesichtbarkeit im vorgesehenen Geltungsbereich.
3. Öffnen Sie Audio, spielen Sie es ab und springen Sie darin; korrigieren Sie Text, speichern Sie und öffnen Sie erneut. Prüfen Sie berechtigte Dokumentausgabe und einen verweigerten Geltungsbereich.
4. Gleichen Sie Anzahlen nach Eigentümer und Übernahmequittung ab. System-Aufnahmesummen enthalten Klinikanker und sind keine Diktatanzahlen.
5. Wiederholen Sie den Übernahme-Probelauf und prüfen Sie, dass bereits übernommenes Material keine Duplikate erzeugt.
6. Prüfen Sie Startlogs und die relevanten System-Auditeinträge. Kontoübernahme verwendet `admin.user.changed`, Aufnahmeübernahme `recording.edited`.

Der Bericht und ein gesunder Endpunkt ersetzen keine tatsächliche Wiedergabe-, Korrektur- oder Ausgabeprüfung.

## Integrierte Modellwahl und Zusammenfassungen

Der aktuelle Code enthält **Verwaltung → System → Modellwahl** und revisionsgebundene **KI-Zusammenfassung**. Dies sind keine noch ausstehenden Handbuchfunktionen. Eine aktive Administration kann aufgabenspezifische Modellkennungen in `clinic_task_models` speichern; Dienstendpunkte und Zugangsdaten bleiben Bereitstellungskonfiguration.

Automatische Zusammenfassungen sind an die erste ASR-Revision geeigneter neuer Diktate gebunden und benötigen ein bereites lokales Textmodell, aktivierte automatische Zusammenfassungen sowie Ansichts-/Bearbeitungsrechte des Autors. Ein Upgrade ergänzt keine Zusammenfassungen bestehender Diktate. Berechtigte Benutzer können eine Zusammenfassung für verwendbaren aktuellen Text anfordern; gespeicherte Zusammenfassungen werden veraltet, wenn ihre Quellrevision nicht mehr übereinstimmt. Siehe [Modellwahl](model-configuration.md) und [Prompts](prompts.md).

## Entfernte ursprüngliche Funktionen

Der Klinik-Fork bietet keine Ereignis-/Kalenderextraktion, öffentlichen Freigabelinks, internen Freigaben, Webhooks, allgemeine API v1, Inkognitomodus, Cloud-ASR-Konnektoren, Ordner, Selbstregistrierung, E-Mail-Verifizierung, Passwortwiederherstellung per E-Mail, What's-New-Popup oder harte automatische Löschung. API-Tokens bleiben allein dem Importagenten vorbehalten. Historische Datenbankzeilen stellen diese Funktionen nicht wieder her. Aufbewahrung archiviert statt zu löschen; siehe [Aufbewahrung](retention.md).

## Konnektor- und Speicherkompatibilität

Unterstützte Transkriptionskonnektoren sind `openai_whisper` gegen einen lokalen kompatiblen Endpunkt, der selbst betriebene `asr_endpoint` und der synthetische `clinic_stub`. `TRANSCRIPTION_CONNECTOR` wählt ausdrücklich; andernfalls wählt `ASR_BASE_URL` den `asr_endpoint`, wobei der ältere Weg über `USE_ASR_ENDPOINT` weiterhin erkannt wird. Verwenden Sie `TRANSCRIPTION_MODEL` statt des älteren `WHISPER_MODEL`. Prüfen Sie das konfigurierte Modell und unterstützte Sprechertrennung mit neutraler Sprache. Verwenden Sie keine Cloud-URLs aus alten Beispielen.

Die älteren Werkzeuge `migrate_local_paths_to_local_locator.py`, `migrate_local_recordings_to_s3.py` und `backfill_audio_duration_seconds.py` arbeiten auf gewöhnlichen Aufnahmemedien, nicht auf dem Klinikoriginalspeicher. Neuer Klinikspeicher verwendet persistente lokale Originale. Eine Migration des gewöhnlichen Backends ist eine getrennt geprüfte Operation und schliesst keine Klinikoriginal-Migration ab.

Stellen Sie für ein Rollback zusammengehörige Datenbank, Dateien, Schlüssel und Anwendungsversion wieder her. Eine erfolgreiche Klinikanmeldung kann einen älteren bcrypt-Hash durch scrypt ersetzen; gehen Sie nicht davon aus, dass die ursprüngliche Anwendung die veränderte Datenbank authentifizieren kann. Behalten Sie den Sicherungssatz von vor dem Upgrade, bis die gesamte Abnahmefolge erfolgreich ist.
