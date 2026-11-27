# Aufbewahrung und Archivierung

[English](../../admin-guide/retention.md)

Der Klinik-Fork bietet keine automatische endgültige Löschung von Diktaten oder Aufnahmemedien. Die Aufbewahrungsfunktion archiviert geeignete Aufnahmen und erhält deren Dateien und Datenbankzeilen. Die übernommenen Löschmodusnamen schalten keine Löschung ein. Auch manuelles endgültiges Löschen ist im Klinikmodus gesperrt; Konten werden deaktiviert statt gelöscht.

Zwei unterschiedliche Mechanismen bleiben: der tägliche Aufbewahrungslauf für gewöhnliche Aufnahmezeilen und die ausdrückliche Aktion **Archivieren** für ein Klinikdiktat. Archivierung gibt keinen Speicher frei. Berücksichtigen Sie archiviertes Audio, Revisionen und Auditnachweise bei Kapazitäts- und Sicherungsplanung.

## Einstellungen der automatischen Archivierung

Öffnen Sie **Verwaltung → System → Systemeinstellungen** als Standortadministration. Die alten Einstellungsnamen bleiben zur Kompatibilität erhalten.

| Einstellung | Umgebungsvariable | Standard und Wirkung |
|---|---|---|
| `enable_auto_deletion` | `ENABLE_AUTO_DELETION` | `false`; aktiviert automatische Archivierung einschliesslich manuell ausgelöster Läufe. |
| `global_retention_days` | `GLOBAL_RETENTION_DAYS` | `0`; ein positiver Wert archiviert Aufnahmen nach dieser Anzahl Tage seit Erstellung. Bei `0` zählen nur anwendbare positive Tagfristen. |
| `deletion_mode` | `DELETION_MODE` | Die Oberfläche bietet `archive` an. Historische Werte `audio_only` und `full_recording` archivieren ebenfalls und erhalten alle Daten. |

Gespeicherte Systemeinstellungen haben Vorrang vor Umgebungsvariablen. Der Lauf liest die Einstellungen zur Laufzeit; Änderungen benötigen keinen Neustart. Der interne Umgebungsstandard verwendet weiterhin `full_recording`, ältere Beispiele beschreiben Löschung. Beides ändert nichts am heutigen reinen Archivierungsverhalten. Das Flag `features.auto_deletion` des Entwicklungsvertrags steuert diesen Lauf nicht.

Ein gewählter Hintergrundprozess führt die tägliche Wartung um 02:00 Uhr Server-Ortszeit aus. Er erledigt aktivierte Archivierung, Bereinigung eigentümerloser Aufnahmen und Auditbereinigung. Ein Prozess mit `CLINIC_WEB_ONLY=1` startet keine Hintergrundarbeit. Im Klinikmodus bleiben bei der Auditbereinigung alle Zeilen erhalten; siehe [Auditprotokoll](audit-log.md).

## Welche Aufnahmen berücksichtigt werden

Der Lauf betrachtet Aufnahmezeilen mit `COMPLETED` oder `FAILED` und prüft jede unter einer Sperre erneut. Bereits durch Aufbewahrung archivierte Aufnahmen werden übersprungen, ebenso solche ohne positive Frist oder ausreichend altes Erstellungsdatum.

| Bestehendes Feld oder Regel | Wirkung |
|---|---|
| `deletion_exempt` | Nimmt die Aufnahme von automatischer Archivierung aus. |
| Anwendbarer Tag mit `protect_from_deletion` oder `retention_days=-1` | Nimmt die Aufnahme aus. |
| Anwendbare positive Tagfristen | Die kürzeste positive Frist ersetzt die globale Frist. |
| Keine anwendbare positive Tagfrist | Die positive globale Frist gilt, sofern konfiguriert. |

Persönliche Tags des Eigentümers und bestehende Gruppentags zählen. Der persönliche Tag eines anderen Benutzers ändert die Aufbewahrungsfrist des Eigentümers nicht. Der Klinik-Fork bietet keine Ordner; übernommene Ordner-Aufbewahrungsfelder werden nicht ausgewertet.

Der Klinikimport erstellt die Ankeraufnahme eines Diktats mit `deletion_exempt=True`; der allgemeine Lauf überspringt sie deshalb normalerweise. Eine separate Uploadaufnahme, Import-Arbeitskopie oder ältere Quellaufnahme fällt nicht unter diese Ausnahme und kann bei erfüllten Bedingungen archiviert werden. Ihr Klinikoriginal bleibt erhalten.

Die administrativen Endpunkte für Browsersitzungen behalten ihre alten Namen:

- `GET /admin/auto-deletion/stats` meldet Aktivierung, Frist, Ausnahmen sowie geeignete oder archivierte Einträge.
- `POST /admin/auto-deletion/run` führt den aktivierten Lauf aus und benötigt Administrationssitzung und CSRF-Token. Keine Seite der Klinikadministration ruft diesen Endpunkt automatisch auf.

Ein Lauf meldet `checked`, `archived`, `already_archived`, `exempted`, `skipped_no_retention` und `errors`. Die Kompatibilitätszähler `deleted_audio_only` und `deleted_full` bleiben null. Jede Aufnahme wird separat gespeichert: Ein späterer Fehler macht frühere erfolgreiche Archivierungen nicht rückgängig. Prüfen Sie Fehler vor einer Wiederholung. Bei ausgeschalteter Archivierung meldet der Lauf `auto_deletion_disabled` und ändert nichts.

## Ein Klinikdiktat archivieren

Verwenden Sie **Archivieren** in den Workflowaktionen oder der Korrekturansicht des Diktats. Die Bestätigung lautet **Das Diktat wird archiviert. Es wird nichts gelöscht.** Die Person benötigt eine ausdrückliche Berechtigung **Archivieren** (`archive`) für dieses Diktat. Autoreneigentum, Standortadministration, Identitätsadministration, Poolleitung oder Freigaberecht allein reichen nicht aus.

Die Aktion ist für abgeschlossene Arbeit vorgesehen, der Server akzeptiert aber einen Übergang aus jedem Workflowzustand ausser **Zu archivieren**. Die Bestätigung verschiebt das Diktat nach **Zu archivieren**. Der erste Archiveintrag benötigt eine aktuelle Textrevision, eine Audiorevision und eine eindeutige aktuelle Kontextrevision; fehlen diese, ist die Aktion nicht verfügbar. Ein bestehender Eintrag behält seine erfassten Verknüpfungen.

Das Archivbuch bewahrt Verweise auf Original, Audio, Text, Kontext und aktive Freigabe sowie eine Version und ein idempotentes Ergebnis auf. Diese Objekte werden nicht entfernt. Der Workflowübergang unterscheidet sich von der Aufnahmeaufbewahrung: Eine ausgenommene Ankeraufnahme erhält normalerweise keinen Aufbewahrungszeitstempel, und der Workflow-Archiveintrag selbst ist kein Auditereignis `recording.archived`.

Öffnen Sie **Verwaltung & Seiten → Diktat und Ausgabe → Archiv** (`/#/archiv`), um das Ergebnis zu prüfen. Diese schreibgeschützte Ansicht listet sichtbare Diktate in **Zu archivieren**. Sie bietet Filter nach Datum, Autor und Schreibkräfte-Pool, aber keine Wiederherstellungs-, Wiederöffnungs- oder Löschfunktion. Wird kein Aufbewahrungsstatus geliefert, bedeutet **Aufbewahrung nicht angegeben**, dass diese Information fehlt; es bedeutet nicht, dass das Diktat gelöscht wird.

## Archivnachweise und Wiederherstellung

Ein Aufbewahrungsübergang einer Aufnahme setzt `retention_archived_at`. Das persönliche Archiv des Eigentümers verwendet `is_archived`; jeder der beiden Werte kennzeichnet die Aufnahme als archiviert. Nur der Eigentümer darf eine Aufbewahrungsarchivierung über den übernommenen Wiederherstellungspfad einer Aufnahme aufheben, unter Beachtung des Klinik-Bearbeitungsrechts. Dies ist keine Wiederherstellungsaktion in der Klinikansicht **Archiv**. Die Wiederherstellung ändert das Erstellungsdatum nicht; der nächste Lauf kann die Aufnahme bei unveränderter Frist und Ausnahme erneut archivieren.

Jeder tatsächliche Archivierungs- oder Wiederherstellungsübergang einer Aufnahme schreibt Zustand, eine `audit_event`-Zeile und eine nur erweiterbare `clinic_archive_event`-Zeile in derselben Transaktion. Fehlende Pflichtnachweise rollen den Übergang zurück. Diese Ereignisse bleiben bei ausgeschalteter gewöhnlicher Auditprotokollierung aktiv. SQLite- und PostgreSQL-Trigger schützen den Archivnachweis vor Änderungen und Löschung. Der Nachweis enthält Akteur, Aufnahme, Zeit, Archivrevision, Regel, Geltungsbereich sowie anwendbare Frist oder Tag.

## Wartung ist keine Aufbewahrungsrichtlinie

Reine Archivierung garantiert nicht, dass kein Wartungscode Daten löschen kann. Bestehende Upstream-Wartung behandelt weiterhin eigentümerlose Aufnahmen, verlassene Uploadsitzungen und temporäre Uploadteile. Die Bereinigung eigentümerloser Aufnahmen markiert eine Aufnahme zunächst und kann sie in einem späteren Lauf entfernen, sobald die Markierung mindestens 20 Stunden alt ist; aktive Jobs und Sitzungen verschieben die Entfernung. Prüfen Sie das tägliche Protokoll `Ownerless recording cleanup:` und untersuchen Sie unerwartete Kandidaten.

Das Abbrechen eines unfertigen Uploads kann dessen Uploadaufnahme entfernen. Ein bereits in den Originalspeicher übernommenes Klinikoriginal ist davon getrennt. Der Widerruf der eigenen Stimmeinwilligung löscht die aufgenommenen Stimmvektoren dieser Person, während die Einwilligungshistorie bleibt. Dies sind bestimmte Lebenszyklusvorgänge, keine freigegebene Richtlinie zum Löschen abgeschlossener Diktate.

Verwenden Sie übernommene Bereinigungs- oder Rücksetzskripte nicht als Klinik-Aufbewahrungsverfahren. Insbesondere kennt `scripts/clean_unreferenced_files.py` nicht alle Klinikoriginalreferenzen; `scripts/reset_db.py` ist destruktives Entwicklungswerkzeug. Halten Sie den Originalspeicher ausserhalb des Aufnahmepräfix-Baums, den die gewöhnliche Medienbereinigung durchsucht. Lassen Sie den Upstream-Watchfolder-Mechanismus (`ENABLE_AUTO_PROCESSING`) ausgeschaltet. Jede Dateibereinigung oder Ablaufregel für Importagent-Sicherungen benötigt einen eigenen geprüften Umfang und Wiederherstellungsplan.

## Richtlinie und Wiederherstellung

Das Repository liefert keine freigegebenen produktiven Aufbewahrungsfristen. `clinic-spec/config/production.template.json` lässt Richtlinienreferenz sowie Audio-, Transkript-, Audit- und Sicherungsfristen offen. Die Entwicklungskonfiguration enthält ausdrücklich `retention_policy_approved=false`. Der Helfer für Sicherungsablauf berechnet nur einen Plan; er ist kein laufender Aufbewahrungsdienst.

Vereinbaren Sie Fristen, Ausnahmen, Verantwortliche und Wiederherstellungsverantwortung lokal, bevor Sie sich auf einen Zeitplan verlassen. Sichern Sie Datenbank, Uploadspeicher und separaten Originalspeicher gemeinsam. Testen Sie die Wiederherstellung mit synthetischen Daten einschliesslich Eigentum, Revisionen, Audio und Auditnachweisen. Bereits gelöschte Medien lassen sich durch die Umstellung auf Archivierung nicht wiederherstellen.

Siehe [Dateispeicher](storage.md), [Auditprotokoll](audit-log.md) und [Datenschutz](privacy.md).
