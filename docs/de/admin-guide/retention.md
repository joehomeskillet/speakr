---
layout: default
title: Aufbewahrung und automatische Archivierung
parent: Administrationshandbuch
nav_order: 7
---

# Automatische Archivierung und Aufbewahrungsregeln
<small>[English](../../admin-guide/retention.md)</small>

In der Klinikversion löscht die Aufbewahrung nie. Zwei getrennte Mechanismen versetzen Daten in einen Archivzustand:

- **Aufbewahrungsarchivierung** (der tägliche Lauf) markiert alte Basisaufnahmen (Upstream-Zeilen `recording`) für alle Betrachtenden als archiviert.
- **Diktatarchivierung** verschiebt ein Klinikdiktat in den Workflow-Status **Zu archivieren**. Sie ist eine Benutzeraktion mit einem eigenen Recht.

Keiner der beiden Mechanismen entfernt eine Datei oder eine Datenbankzeile. Diese Seite nennt ausserdem die verbleibenden Wartungspfade, die Daten löschen, den Stand der Aufbewahrungsrichtlinie und den Sicherungsplan.

Upstream-Speakr-Dokumentation (fixiertes Original, beschreibt löschende Modi, die in der Klinikversion nicht gelten): [Retention & Auto-Deletion](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/retention.md).

## Statusübersicht

| Funktion | Status | Hinweise |
|---|---|---|
| Lauf der Aufbewahrungsarchivierung | Standardmässig aus | `ENABLE_AUTO_DELETION=false`. Archiviert nur. |
| Diktataktion **Archivieren** | Verfügbar | Erfordert das Recht **Archivieren** in den Fachrechten. |
| Klinikansicht **Archiv** | Verfügbar | Nur Lesezugriff. |
| Löschmodi `audio_only` und `full_recording`, Filter **Audio deleted**, **Delete audio, keep transcript**, manuelles Löschen von Aufnahmen | Nur Upstream | Jeder Modus archiviert. Die Routen für endgültiges Löschen sind im Klinikmodus gesperrt. |
| Genehmigte Aufbewahrungsfristen der Klinik | Offene Lücke | Alle Aufbewahrungswerte in `clinic-spec/config/production.template.json` sind `null`. |
| Serverseitiger Ablaufplan für Sicherungskopien (`src/clinic/backup_retention_plan.py`) | Vorbereitet, deaktiviert | Nur Plan, ausserhalb der Tests kein Aufruf. |
| Aufbewahrung im Sicherungsordner des Windows-Importagenten | Standardmässig aus | Löscht nach dem Einschalten Dateien in seinem Sicherungsordner. |
| Bereinigung von Aufnahmen ohne Eigentümer | Verfügbar | Unverändert aus Upstream. Löscht Aufnahmen ohne Eigentümer. Läuft täglich. |
| Bereinigung des Auditprotokolls | Nur Upstream (im Klinikmodus zurückgehalten) | Löscht im Klinikmodus nichts; die Einstellung `audit_log_retention_days` wird gespeichert und angezeigt (Verfügbar). Siehe [Auditprotokoll](audit-log.md). |
| Upstream-Rücksetzskript `scripts/reset_db.py` | Verfügbar (Upstream-Skript); nie ausführen | Liegt im Image und löscht Daten ohne Rückfrage. Führen Sie es auf einer Klinikinstallation nie aus. Siehe [Upstream-Rücksetzskript](#upstream-rucksetzskript). |

## Was die Archivierung erhält

Die Aufbewahrungsarchivierung setzt `recording.retention_archived_at`, erhöht `recording.archive_revision` und schreibt den in [Pflichtnachweis im Audit](#pflichtnachweis-im-audit) beschriebenen Nachweis. Die Diktatarchivierung ändert den Workflow-Status und schreibt einen Eintrag ins Archivbuch. Sonst ändert keiner der beiden etwas:

- Gespeicherte Medien, Transkripte, Zusammenfassungen, Notizen, Tags und abhängige Zeilen der Aufnahme bleiben unverändert.
- Die Klinikoriginale bleiben unverändert. Datenbank-Trigger in SQLite und PostgreSQL weisen `UPDATE` und `DELETE` auf `clinic_original_version` und `clinic_transcript_revision` zurück.
- Freigaben behalten die exakt gebundenen Audio-, Transkript- und Kontextrevisionen. Eine spätere Änderung hängt eine Ablösungszeile an; die Freigabezeile selbst wird nicht geändert.
- Eine Mediendatei, die eine ältere Softwareversion bereits entfernt hatte, bleibt entfernt.

Die Archivierung gibt keinen Speicher frei. Die Kapazitätsplanung muss archivierte Medien und das wachsende Auditjournal berücksichtigen.

## Konfiguration

Die Einstellungen bearbeitet die globale Administration (**Administrationsrolle**, `User.is_admin`) unter **Verwaltung** › **System** › **Systemeinstellungen** (`/admin#/system/einstellungen`). Die Upstream-Schlüssel bleiben aus Kompatibilitätsgründen bestehen.

| Einstellungsschlüssel | Umgebungsvariable | Standard | Bezeichnung (DE / EN) | Wirkung |
|---|---|---|---|---|
| `enable_auto_deletion` | `ENABLE_AUTO_DELETION` | `false` | Automatische Archivierung / Automatic archival | Schaltet den Archivierungslauf ein, für geplante und manuell ausgelöste Läufe. In `.env` gilt nur `true` (Gross- und Kleinschreibung beliebig) als eingeschaltet. |
| `global_retention_days` | `GLOBAL_RETENTION_DAYS` | `0` | Archivieren nach Tagen / Archive after days | Tage seit der Erstellung bis zur Archivierung. Bei `0` gelten nur positive Fristen von Tags. |
| `deletion_mode` | `DELETION_MODE` | `archive` (so in **Systemeinstellungen** angezeigt; interner Rückfallwert `full_recording`, der ebenfalls archiviert) | Archivieren und alle Daten erhalten / Archive and preserve all data | Altschlüssel. `archive`, `audio_only` und `full_recording` archivieren alle. Der Bearbeitungsdialog bietet nur `archive` an. |

```dotenv
ENABLE_AUTO_DELETION=false
GLOBAL_RETENTION_DAYS=0
DELETION_MODE=archive
```

- Ein in **Systemeinstellungen** gespeicherter Wert hat Vorrang. Die Zeilen werden ohne Wert angelegt, deshalb gilt der Wert aus `.env`, bis die globale Administration einen Wert speichert.
- Der Lauf liest bei jeder Ausführung die aktuellen Werte. Ein Neustart ist nicht nötig.
- `config/env.asr.example`, `config/env.whisper.example` und `config/env.whisperx.example` enthalten noch `GLOBAL_RETENTION_DAYS=90` und `DELETION_MODE=audio_only`, mit Upstream-Kommentaren, die ein Löschen beschreiben. Das sind Beispielwerte, keine Standardwerte, und jeder Modus archiviert.
- Die Konsolenzeile beim Start behält den Upstream-Wortlaut: `Auto-deletion: Disabled (set ENABLE_AUTO_DELETION=true to enable)`, `Auto-deletion: Enabled (global retention: N days, mode: …)` oder `Auto-deletion: Enabled but no global retention period set (configure GLOBAL_RETENTION_DAYS)`. Sie liest nur `.env`, nicht die in **Systemeinstellungen** gespeicherten Werte. Ohne Wert in `.env` und in **Systemeinstellungen** zeigt **Systemeinstellungen** `archive` an; intern verwendet der Code dann den Upstream-Rückfallwert `full_recording`, der ebenfalls nur archiviert, und die Startzeile zeigt `mode: full_recording`.
- Der Eintrag `features.auto_deletion` in `clinic-spec/config/dev.defaults.json` wird vom Code nicht gelesen. Er schaltet die Archivierung weder ein noch aus.
- Jede Änderung wird als `admin.setting.changed` mit altem und neuem Wert protokolliert (alle drei Schlüssel gehören zu den Schlüsseln, deren Werte gespeichert werden). Siehe [Systemeinstellungen](system-settings.md).

## Zeitplan

Ein täglicher Wartungsthread startet um 02:00 Uhr Serverortszeit. Ihn führt nur ein gewählter Prozess aus; ein mit `CLINIC_WEB_ONLY=1` gestarteter Prozess startet keine Hintergrundarbeit. Der Lauf hat drei Schritte in dieser Reihenfolge:

1. Aufbewahrungsarchivierung, nur wenn `enable_auto_deletion` eingeschaltet ist. Sie läuft auch bei einer globalen Frist von `0`, damit reine Tag-Regeln greifen.
2. Die Bereinigung von Aufnahmen ohne Eigentümer, immer (siehe [Pfade, die weiterhin Daten löschen](#pfade-die-weiterhin-daten-loschen)).
3. Die Bereinigung des Auditprotokolls, immer. Im Klinikmodus löscht sie nichts (siehe [Auditprotokoll](audit-log.md)).

Protokollzeilen des Archivierungsschritts: `Running scheduled archival...`, `Scheduled archival skipped (disabled)`, `Archived recording <id> after <n> retention days (…)`, `Error archiving recording <id> (<error type>)`, `Automatic archival completed: {…}` und `Scheduled archival completed: {…}`.

## Archivierbarkeit von Basisaufnahmen

Der Lauf berücksichtigt Aufnahmen mit dem Status `COMPLETED` oder `FAILED`. Er sperrt jeden Kandidaten und prüft den Status erneut. Eine bereits durch Aufbewahrung archivierte Aufnahme wird als `already_archived` gezählt. Ihr Erstellungszeitpunkt muss strikt vor dem Laufzeitpunkt abzüglich der wirksamen Frist liegen.

| Regel | Wirkung |
|---|---|
| Aufnahmekennzeichen `deletion_exempt` | Die Aufnahme wird nicht archiviert (gezählt als `exempted`). |
| Geltender Tag mit `protect_from_deletion` oder `retention_days=-1` | Die Aufnahme wird nicht archiviert (gezählt als `exempted`). |
| Geltende positive Fristen von Tags | Die kürzeste positive Tag-Frist ersetzt die globale Frist. |
| Keine geltende positive Tag-Frist | Die positive globale Frist gilt, sofern gesetzt. |
| Keine positive Frist | Die Aufnahme bleibt unverändert (gezählt als `skipped_no_retention`). |

Welche Tags gelten:

- Ein Gruppen-Tag gilt immer. Die Verwaltung von Gruppen-Tags ist im Klinikmodus gesperrt (Funktion `internal_sharing`), bestehende Gruppen-Tags zählen aber weiterhin.
- Ein persönlicher Tag gilt nur, wenn er dem Eigentümer der Aufnahme gehört. Tags, die andere Betrachtende an die Aufnahme hängen, ändern ihre Frist nicht.
- Aufbewahrungs- und Schutzfelder von Ordnern werden aus Kompatibilitätsgründen gespeichert und nicht ausgewertet. Verwenden Sie für eine Ausnahme einen geschützten Tag.

Wo die Werte gesetzt werden:

- **Tag-Fristen und Schutz:** Avatarmenü **Konto** › Register **Tags & Ordner**, im Tag-Editor. Der Abschnitt **Aufbewahrung und automatische Archivierung** erscheint nur, solange die automatische Archivierung eingeschaltet ist. Er enthält das Feld **Aufbewahrungsdauer (Tage)** und ein Kontrollkästchen. Das Kontrollkästchen zeigt derzeit den Ordnertext **Ordnerschutz (nur gespeichert)**; bei einem Tag wird das Kennzeichen ausgewertet, wie der Hilfetext sagt: «Aufnahmen mit diesem Tag sind von der automatischen Archivierung ausgenommen.» Setzen Sie den Schutz bei eingeschalteter automatischer Archivierung: Bei ausgeschalteter Archivierung lässt eine Tag-Änderung, die den Schutz (`retention_days=-1`) sendet, ihn unverändert.
- **Ausnahme einer Aufnahme:** `POST /api/recordings/<id>/toggle_deletion_exempt`. Im Klinikmodus verlangt die Routenprüfung das Bearbeitungsrecht für die Aufnahme und bei einer Aufnahme, die mit einem Klinikdiktat verknüpft ist, zusätzlich eine gültige Bearbeitungssperre. Die Routenfunktion lässt danach nur den Eigentümer oder die globale Administration zu. Jede Umschaltung wird als `recording.edited` mit dem Feld `deletion_exempt` protokolliert (nur bei eingeschaltetem Auditprotokoll). Die Klinikoberfläche hat dafür kein Bedienelement, weil die Upstream-Aufnahmeansichten im Klinikmodus nicht dargestellt werden.

## Klinikdiktate und der Archivierungslauf

Jedes Klinikdiktat hat eine Ankeraufnahme. Die Eingangsverarbeitung legt sie mit `deletion_exempt=True` und gesetztem persönlichem Archivkennzeichen an; der Lauf überspringt sie, solange das Kennzeichen gesetzt ist. Die Klinikoberfläche bietet kein Bedienelement, das es entfernt.

Uploads, Importe und von der Altbestandsübernahme übernommene Aufnahmen haben zusätzlich eine eigene Basisaufnahme: die Upload-Aufnahme, die Importarbeitskopie für die Transkription oder die Quellaufnahme des Altbestands. Diese Zeilen werden nicht mit der Ausnahme angelegt. Sobald eine solche Zeile `COMPLETED` oder `FAILED` ist, kann eine positive Frist sie archivieren. Die Archivierung löscht nichts, und das Original des Diktats bleibt in jedem Fall im Originalspeicher (siehe [Speicher](storage.md)).

## Diktatarchivierung

**Status: Verfügbar.** Sie ist für abgeschlossene Diktate gedacht, der Server nimmt sie aber aus jedem Status ausser **Zu archivieren** an; eine bestätigte Anfrage aus diesem Status erhält die Antwort 422 `TRANSITION_FAILED`.

**Wo:** Die Workflow-Aktionen eines Diktats bieten **Archivieren** in der Gruppe **Archiv** an, die Prüfansicht bietet dieselbe Aktion. Die Bestätigung lautet: «Das Diktat wird archiviert. Es wird nichts gelöscht.»

**Wer:** Benutzerinnen und Benutzer mit der Aktion **Archivieren** in den **Fachrechten** für das Diktat. Archivieren gehört nicht zu den sicheren Standardrechten für eigene Diktate; auch Diktierende brauchen für ihre eigenen Diktate ein ausdrückliches Recht. Das Kennzeichen der globalen Administration (**Administrationsrolle**) enthält es nicht, ebenso wenig die **Identitätsadministration** oder die **Leitung** eines Pools. Fachrechte ändert nur eine aktive globale Administration, unter **Benutzer und Rollen** › **Fachrechte** (siehe [Benutzerverwaltung](user-management.md)).

**API:** `POST /api/clinic/v1/dictations/<Diktat-UUID>/archive`

- Anfragekörper genau `{"action": "archive" | "delete", "confirm": true | false}`.
- HTTP-Kopfzeilen: `Idempotency-Key` (eine UUID), `If-Match: "<Diktat-ID>:<Version>"` (ohne sie Antwort 428) und das CSRF-Token der Sitzung.
- `confirm: false` ist ein Probelauf. Er ändert weder Diktat noch Archivbucheintrag noch Aufnahme; gespeichert wird nur die Anfragequittung zum `Idempotency-Key`, damit eine Wiederholung mit demselben Schlüssel dieselbe Antwort erhält. Die Antwort enthält `dry_run` mit `eligible`, `reason`, `legal_hold` und `would_delete`, das immer `false` ist.
- `confirm: true` verschiebt das Diktat aus jedem anderen Status nach **Zu archivieren**.
- `"action": "delete"` erfordert die Aktionen Bearbeiten und Löschen. Im Klinikmodus führt sie zur selben Verschiebung nach **Zu archivieren**. Sie wird erst angenommen, nachdem eine erste Anfrage mit `archive` den Eintrag im Archivbuch angelegt hat; vorher lautet die Antwort 404. Die Fachrechte-Oberfläche bietet die Aktion Löschen nicht an.
- Ohne Archivbucheintrag braucht die erste Anfrage mit `archive` eine aktuelle Textrevision, eine Audiorevision und genau eine aktuelle Kontextrevision. Fehlt eine davon, bietet die Oberfläche **Archivieren** nicht an, und der Endpunkt antwortet wie bei fehlendem Recht mit 404 (`NOT_FOUND`, «Diktat nicht gefunden»). Ein bestehender Archivbucheintrag behält seine gespeicherten Verweise.

**Was geschrieben wird:**

- Der Archivbucheintrag `clinic_archive_entry` hält die Verweise auf das Original, die Audiorevision, die Transkriptrevision, die Kontextrevision und die aktive Freigabe sowie eine Version fest. Das gespeicherte Ergebnis `clinic_archive_result` beantwortet eine wiederholte Anfrage mit demselben Idempotenzschlüssel. Die Anwendung verweigert das Löschen in beiden Tabellen und das Ändern in der Ergebnistabelle (ORM-Listener, keine Datenbank-Trigger).
- Die Verschiebung selbst schreibt keine Zeile in `audit_event`.
- Die Ankeraufnahme ändert sich nur, wenn der Probelauf die Archivierung zulässt. Dann archiviert die Anfrage sie über denselben Pfad wie der Lauf, mit Nachweis `recording.archived`. Weil jede Ankeraufnahme `deletion_exempt` trägt, meldet der Probelauf in der Regel `legal_hold`, und `retention_changed` bleibt `false`.

## Die Ansicht Archiv

**Status: Verfügbar.** Menü **Verwaltung & Seiten** › **Diktat und Ausgabe** › **Archiv** (`/#/archiv`).

- Die Ansicht listet Diktate im Workflow-Status **Zu archivieren**. Sie liest `GET /api/clinic/v1/recordings?status=zu_archivieren` und zeigt nur Diktate, die die aufrufende Person in der Arbeitsliste sehen darf.
- Sie ist schreibgeschützt. Sie hat kein Bedienelement zum Wiederherstellen, Wiederöffnen oder Löschen. Hinweis auf der Seite: «Das Archiv wird nur gelesen. Bereits bestätigte Ausgaben bleiben unverändert.»
- Spalten: **Aufnahmezeit**, **Diktiert von**, **Schreibkräfte-Pool**, **Archiviert am**, **Aufbewahrung**. Filter: **Von**, **Bis**, **Diktiert von**, **Schreibkräfte-Pool**.
- **Archiviert am** zeigt den Archivierungszeitpunkt der Ankeraufnahme durch Aufbewahrung, sofern gesetzt, sonst den Zeitpunkt des Archivbucheintrags.
- Der Server liefert keinen Aufbewahrungszustand, deshalb zeigt **Aufbewahrung** immer **Aufbewahrung nicht angegeben**.

## Archivzustand durch Aufbewahrung und Wiederherstellung

Die Aufbewahrungsarchivierung setzt `retention_archived_at` und gilt für alle Betrachtenden. Das persönliche Archiv des Eigentümers verwendet das Kennzeichen `is_archived`. Eine Aufnahme gilt als archiviert, wenn einer der beiden Werte gesetzt ist.

Im Klinikmodus wird die Upstream-Seitenleiste der Aufnahmen mit ihrem Archivfilter nicht dargestellt. Die Aufnahmeliste der Basisanwendung `GET /api/recordings?archived=true` liefert weiterhin die eigenen archivierten Aufnahmen der aufrufenden Person.

Nur der Eigentümer kann ein Archiv durch Aufbewahrung aufheben. Andere erhalten von `POST /recording/<id>/toggle_archive` und von den Aktualisierungsrouten der Aufnahme mit `is_archived` HTTP 403 `archive_restore_owner_required` («Nur der Eigentümer kann eine durch Retention archivierte Aufnahme wiederherstellen.»). Die Sammelumschaltung (`POST /api/recordings/bulk-toggle`, `field: archive`) überspringt eine solche Aufnahme; sie fehlt in `affected_ids`. Im Klinikmodus brauchen diese Routen das Bearbeitungsrecht. Eine Wiederherstellung löscht den Aufbewahrungszeitstempel und behält das Erstellungsdatum, deshalb kann der nächste Lauf die Aufnahme erneut archivieren. Setzen Sie eine Ausnahme oder ändern Sie die Tag-Frist, wenn sie aktiv bleiben muss.

API v1 liefert weiterhin archivierte und aktive Aufnahmen, sofern nicht `archived=true` oder `archived=false` angegeben ist. Im Klinikmodus werden API-Token abgewiesen, deshalb ist API v1 nur mit einer Browsersitzung erreichbar. Der alte Endpunkt `/api/recordings/archived` listet Aufnahmen, deren Audio entfernt wurde (`audio_deleted_at`), nicht archivierte Aufnahmen.

## Pflichtnachweis im Audit

Jeder tatsächliche Archivierungs- oder Wiederherstellungsübergang einer Aufnahme schreibt drei Dinge in einer Transaktion: den Zustand der Aufnahme, eine Zeile in `audit_event` und eine nur anfügbare Zeile in `clinic_archive_event`. Kann die Auditzeile nicht angelegt werden, wird der Übergang zurückgerollt. Eine Anfrage für den bereits bestehenden Zustand erzeugt keinen Übergang.

| Nachweis | Erfasster Wert |
|---|---|
| Ereignis | `recording.archived` oder `recording.unarchived` |
| Akteur | Aus der Anfrage übernommen: das System beim geplanten Lauf, die angemeldete Administration bei einem Lauf über den Endpunkt, die Sitzungsbenutzerin oder der Sitzungsbenutzer bei einer Benutzeraktion |
| Objekt | Aufnahme-ID, Erstellungszeitpunkt der Aufnahme, Eigentümer-ID und gegebenenfalls Zielbenutzer |
| Zeit und Revision | UTC-Ereigniszeit und die steigende Archivrevision der Aufnahme |
| Regel (`details.rule`) | `retention.global`, `retention.tag`, `user.archive` oder `user.restore` |
| Geltungsbereich (`details.scope`) | `recording` für Aufbewahrung und deren Aufhebung, `personal` für eine persönliche Änderung |
| Regeldetail | `retention_days` bei Aufbewahrungsregeln, zusätzlich `tag_id`, wenn eine Tag-Frist galt |

- Archivübergänge werden auch geschrieben, wenn `audit_log_enabled` ausgeschaltet ist.
- Die Bereinigung des Auditprotokolls entfernt `recording.archived` und `recording.unarchived` nie, mit oder ohne Klinikmodus.
- Datenbank-Trigger in SQLite und PostgreSQL weisen `UPDATE` und `DELETE` auf `clinic_archive_event` zurück («clinic archive evidence is append-only»). Die Tabelle hat keine Fremdschlüssel, deshalb überdauern ihre Zeilen eine spätere Löschung der Aufnahme.
- In PostgreSQL warten gleichzeitige Schreibvorgänge auf die Zeilensperre der Aufnahme. Eine Eindeutigkeitsbedingung auf Aufnahme, Erstellungszeitpunkt und Revision weist zusätzlich widersprüchliche Nachweiszeilen zurück. Eine gescheiterte Transaktion hinterlässt den vorherigen Zustand und kann wiederholt werden.
- Der Lauf behandelt jede Aufnahme in einer eigenen Transaktion. Vor einem späteren Fehler archivierte Aufnahmen bleiben archiviert. Prüfen Sie den Zähler `errors` und wiederholen Sie den Lauf nach Behebung der Ursache.

## Endpunkte für die Administration

```http
POST /admin/auto-deletion/run
GET /admin/auto-deletion/stats
```

- Beide erfordern `User.is_admin`. Im Klinikmodus sind beide ADMIN-Routen: Die Routenprüfung weist Aufrufende ohne `User.is_admin` mit 403 `forbidden` ab, bevor die Routenfunktion läuft. Deren eigene Antwort 403 `admin_required` gilt nur ausserhalb des Klinikmodus.
- Keine Seite der Klinikverwaltung ruft sie auf. Im Klinikmodus werden API-Token abgewiesen, deshalb braucht ein Aufruf die Browsersitzung eines Kontos der globalen Administration, der `POST` zusätzlich dessen CSRF-Token.
- Bei ausgeschalteter Archivierung antwortet der Lauf mit HTTP 200 und `{"error": "Die automatische Archivierung ist nicht aktiviert", "code": "auto_deletion_disabled"}` (die Meldung in der Sprache der Oberfläche) und schreibt keine Zeile `admin.retention.run`.

Ein Lauf liefert Zähler in dieser Form:

```json
{
  "checked": 150,
  "archived": 45,
  "already_archived": 28,
  "deleted_audio_only": 0,
  "deleted_full": 0,
  "exempted": 12,
  "skipped_no_retention": 6,
  "errors": 0
}
```

Die beiden Löschzähler bleiben immer `0`. `checked` zählt jeden Kandidaten mit `COMPLETED` oder `FAILED`, auch später übersprungene. Ein manueller Lauf wird als `admin.retention.run` mit `checked`, `archived`, `already_archived`, `deleted_audio_only`, `deleted_full` und `errors` protokolliert. Diese Zeile ist nicht erzwungen und wird bei ausgeschaltetem Auditprotokoll nicht geschrieben.

Der Statistik-Endpunkt liefert `enabled`, `global_retention_days`, `deletion_mode` (immer `archive`), `eligible_count`, `exempted_count`, `no_retention_count` und `archived_count`. Die drei Zähler zur Archivierbarkeit werden nur bei eingeschalteter Archivierung berechnet. `archived_count` zählt nur Archivierungen durch Aufbewahrung.

Die Statuskarte in **Systemstatistik** zeigt **Archiviert** für Aufnahmen mit einem der beiden Archivkennzeichen. Im Klinikmodus enthält diese Zahl jede Ankeraufnahme eines Diktats und jede Importarbeitskopie, weil die Eingangsverarbeitung sie mit dem persönlichen Archivkennzeichen anlegt.

## Manuelles Löschen im Klinikmodus

Die Funktion `clinical_user_hard_delete` ist im Klinikmodus gesperrt, weil die Vorgaben sie nicht freischalten. Diese Routen antworten deshalb mit 403 `clinic_feature_disabled`: `recordings.delete_recording`, `recordings.delete_recording_audio`, `recordings.bulk_delete_recordings`, `api_v1.delete_recording`, `api_v1.delete_recording_audio`, `api_v1.batch_delete_recordings` und `admin.admin_delete_user`. Konten werden deaktiviert statt gelöscht (siehe [Benutzerverwaltung](user-management.md)).

Der Archivierungslauf ruft die Upstream-Bereinigung verwaister Sprecher nicht auf. Die Upstream-Sprechererkennung ist im Klinikmodus gesperrt.

## Pfade, die weiterhin Daten löschen

### Bereinigung von Aufnahmen ohne Eigentümer

Unverändert aus Upstream (Upstream-Speakr-Issue #572) und nicht an den Klinikmodus gebunden. Sie läuft bei jeder täglichen Wartung, unabhängig davon, ob die Archivierung eingeschaltet ist. Eine Klinikentscheidung zu dieser Löschung gibt es nicht. Das Löschen von Konten ist im Klinikmodus gesperrt, und jeder Pfad, der eine Aufnahme anlegt, setzt einen Eigentümer; Kandidaten sind deshalb Zeilen, die ältere Softwareversionen ohne Eigentümer hinterlassen haben.

- Zwei Läufe: Eine Aufnahme ohne Eigentümer wird beim ersten Lauf nur markiert. Ein späterer Lauf löscht sie samt Medien und abhängigen Zeilen, sobald die Markierung mindestens 20 Stunden alt ist.
- Aufnahmen mit Eigentümer sind nie Kandidaten. Eine Aufnahme mit wartendem oder laufendem Auftrag, aktiver Aufnahmesitzung oder nicht zusammengeführtem Upload-Teil wird übersprungen, und ihre Wartezeit beginnt neu. Eine Aufnahme, die wieder einen Eigentümer erhält, verliert ihre Markierung.
- Jede Löschung wird als `recording.deleted` mit `reason: ownerless` protokolliert, nur bei eingeschaltetem Auditprotokoll. Eine Aufnahme ohne Eigentümer hinterlässt keinen Löschvermerk im Änderungsfeed.
- Mediendateien ohne Zeile werden nur gezählt, nie gelöscht.
- Nach Löschungen führt sie die Upstream-Bereinigung verwaister Sprecher aus.
- Jeder Lauf protokolliert eine Zeile, die mit `Ownerless recording cleanup:` beginnt.

### Stündliche Wartungsschleife

Intervall `RECORDING_SESSION_CLEANUP_INTERVAL_SECONDS` (Standard `3600`; `0` oder weniger schaltet die Schleife aus). Der erste Durchlauf folgt ein Intervall nach dem Start. Jeder Durchlauf behandelt diese Datensätze:

| Datensätze | Einstellung / Variable | Standard |
|---|---|---|
| Aufnahmesitzungen, deren letzte Aktivität älter als die Frist ist. Eine Browseraufnahme mit Teilstücken wird zu einer Aufnahme abgeschlossen, und ein stehengebliebener Abschluss wird neu eingereiht; jede andere Sitzung läuft ab, und ihr Ordner wird entfernt. Sitzungsordner ohne Zeile werden nach derselben Frist entfernt. | `RECORDING_SESSION_TTL_HOURS` (nur Umgebung) | `24` Stunden |
| Upload-Teile für eine Zusammenführung. Medien und Zeile werden entfernt, ausser der Teil wartet noch auf seine Zusammenführung. | Fest | `24` Stunden |
| Löschvermerke (Tombstones) gelöschter Aufnahmen im Änderungsfeed | `recording_tombstone_days` / `RECORDING_TOMBSTONE_DAYS` | `90` Tage |
| Zustellungsdatensätze von Webhooks | `webhook_delivery_retention_days` / `WEBHOOK_DELIVERY_RETENTION_DAYS` | `30` Tage, `0` behält sie |

Benutzer-Webhooks sind im Klinikmodus gesperrt (siehe [Webhooks](webhooks.md)), deshalb entstehen keine neuen Zustellungsdatensätze.

### Abgebrochene Web-Uploads

`POST /upload/cancel` löscht die Upload-Aufnahme, die derselbe Upload weniger als 15 Minuten zuvor gespeichert hat, ausser sie ist `COMPLETED`. Die Löschung wird als `recording.deleted` mit `reason: upload_canceled` protokolliert (nur bei eingeschaltetem Auditprotokoll). Ein bereits daraus angelegtes Diktat behält seine Ankeraufnahme und sein Original. Ein Abbruch, der eintrifft, während der Upload noch gespeichert wird, verwirft die neue Upload-Aufnahme, bevor ein Diktat entsteht; dieser Pfad schreibt keine Auditzeile.

### Dateien ohne Aufnahme

`scripts/clean_unreferenced_files.py` (unverändert aus Upstream) listet Mediendateien, auf die keine Zeile verweist, und löscht oder verschiebt sie auf Anforderung. Es betrachtet nur einfache Dateien direkt in `UPLOAD_FOLDER` und den Baum unter `UPLOAD_FOLDER/<FILE_STORAGE_KEY_PREFIX>` (Standard `recordings`) beziehungsweise die Objekte unter diesem Präfix in S3. Den Originalspeicher der Klinik kennt es nicht; mit dem Standardwert von `CLINIC_ORIGINALS_ROOT` liegen die Originale unter `UPLOAD_FOLDER/clinic/originals/v1/`, ausserhalb der untersuchten Ordner. Der Befehl startet keine Auftragsverarbeitung und keine Zeitpläne, deshalb kann er laufen, während der Server läuft.

```bash
# Probelauf: Anzahl, Gesamtgrösse und die ersten 20 Dateien; es wird nichts geändert
docker compose exec app python scripts/clean_unreferenced_files.py
# Jede nicht referenzierte Datei und jede Zeile mit fehlender Datei
docker compose exec app python scripts/clean_unreferenced_files.py --list
# Dateien nach einer Bestätigungsabfrage löschen
docker compose exec app python scripts/clean_unreferenced_files.py --delete
# Dateien nach einer Bestätigungsabfrage in einen Quarantäneordner verschieben
docker compose exec app python scripts/clean_unreferenced_files.py --move-to <Quarantäneordner>
```

| Option | Wirkung |
|---|---|
| (keine) | Probelauf. Es wird nichts geändert. |
| `--list` | Jede nicht referenzierte Datei und jede Zeile mit fehlender Datei wird ausgegeben. |
| `--delete` | Die Dateien werden nach einer Bestätigungsabfrage gelöscht. |
| `--move-to DIR` | Die Dateien werden nach einer Bestätigungsabfrage mit ihren relativen Pfaden unter `DIR` verschoben. Nur lokaler Speicher. |
| `--yes` | Keine Bestätigungsabfrage. |
| `--min-age-hours N` | Dateien, die in den letzten `N` Stunden geändert wurden, werden übersprungen (Standard `48`). |

- `DIR` darf weder `UPLOAD_FOLDER` selbst sein noch in dessen Baum `<FILE_STORAGE_KEY_PREFIX>` liegen.
- S3-Objekte können nur gelöscht, nicht verschoben werden.
- Verweise und Dateialter werden vor jedem Entfernen erneut geprüft; eine seit der Untersuchung angelegte Zeile behält deshalb ihre Datei.

Sichern Sie vor dem ersten `--delete` den Upload-Ordner oder führen Sie zuerst `--move-to` aus.

### Sicherungsordner des Windows-Importagenten

Standardmässig aus. Mit `BackupRetentionEnabled=true` (30 Tage) oder `BackupRetentionDays` grösser als 0 und einem gesetzten `BackupFolder` löscht der Agent Dateien in diesem Ordner, deren letzte Änderung mindestens so viele Tage zurückliegt. Er löscht höchstens `BackupRetentionMaxBatch` Dateien pro Durchlauf (Standard 100, höchstens 10000) und überspringt Dateien mit der Endung `.part` sowie Namen, die `.import-hold-` enthalten. Jede Löschung wird als `retention deleted <relativer Pfad>` protokolliert.

- Die Schlüssel gehören in den Abschnitt `ImportAgent` der Datei `appsettings.json` des Agenten.
- `BackupFolder` muss ein absoluter Pfad sein, der sich mit keinem zugelassenen Stammordner überschneidet. Mit eingeschalteter Aufbewahrung und ohne `BackupFolder` startet der Agent nicht.
- Die Bereinigung läuft nach jedem Durchlauf des Agenten (`PollIntervalSeconds`, Standard `15`).

Der Server erlaubt derzeit nie den Nachbearbeitungsschritt nach dem Import, deshalb füllt der Agent diesen Ordner nicht. Installation, Aktualisierung und Rücknahme des Agenten sind nicht getestet, und seine Hardwaretests wurden nicht ausgeführt.

### Upstream-Überwachungsordner

Mit `ENABLE_AUTO_PROCESSING=true` (Standard `false`) übernimmt der Upstream-Überwachungsordner jede abgelegte Datei und löscht sie danach aus dem Überwachungsordner. Er hat keine Klinikprüfung. Lassen Sie die Variable ungesetzt; siehe [Systemeinstellungen](system-settings.md#upstream-funktionen-ohne-klinikprufung).

### Upstream-Rücksetzskript

`scripts/reset_db.py` ist ein Entwicklungsskript aus dem Upstream. Es gehört zum Repository und zum Docker-Image, das den ganzen Quellbaum kopiert. Es fragt nicht nach und prüft `CLINIC_MODE` nicht.

- Mit einer SQLite-Datenbank (`sqlite:///…`) löscht es die in der URI genannte Datenbankdatei und ihre Journaldatei, also auch alle Auditzeilen, Revisionen, das Archivbuch und die Kontorichtlinien, und legt danach ein leeres Schema an.
- Danach löscht es jede Datei und jeden Ordner unter `uploads` relativ zum aktuellen Arbeitsverzeichnis.
- Mit PostgreSQL entfernt es keine Daten der Datenbank (es legt nur fehlende Tabellen an), leert aber trotzdem `./uploads`.

Führen Sie es auf einer Klinikinstallation nie aus. Um zu einem früheren Stand zurückzukehren, stellen Sie stattdessen eine Sicherung wieder her (siehe [Dateispeicher](storage.md#datensicherung)).

## Aufbewahrungsrichtlinie und Sicherungsplan

- **Keine genehmigte Aufbewahrungsrichtlinie der Klinik.** In `clinic-spec/config/production.template.json` sind `retention.policy_reference`, `audio_days`, `transcript_days`, `audit_days` und `backup_days` `null`. In `clinic-spec/config/dev.defaults.json` ist `privacy.retention_policy_approved` `false`. Die Anwendung setzt keine klinische Aufbewahrungsfrist für Audio, Transkripte, Auditzeilen oder Sicherungen durch.
- **Serverseitiger Ablaufplan für Sicherungskopien: Vorbereitet, deaktiviert.** `src/clinic/backup_retention_plan.py` berechnet, welche Sicherungskopien importierter Gerätedateien abgelaufen sind (Standard 30 Tage, Haltekennzeichen, unsichere Pfade werden zurückgewiesen). Es greift nie auf das Dateisystem zu, und ausser den Tests ruft kein Code es auf.
- **Sicherungen.** Archivierte Daten bleiben in der Datenbank und im Speicher. Eine Sicherung muss die Datenbank, `UPLOAD_FOLDER` und `CLINIC_ORIGINALS_ROOT` umfassen, wenn dieses anderswo liegt. Siehe [Speicher](storage.md). Die Wiederherstellungswerte (RPO, RTO, Wiederherstellungsnachweis) in der Produktionsvorlage sind `null`.
- **Auditzeilen.** Im Klinikmodus löscht die tägliche Bereinigung keine Auditzeile. Ein separat berechtigter Aufbewahrungsprozess ist nur als Planungsfunktion vorbereitet und läuft nicht. Siehe [Auditprotokoll](audit-log.md).

## Verhalten beim Upgrade

Beim Start legt die Migration «clinic integrity» `clinic_archive_event` an und installiert deren Trigger. Additive Migrationen ergänzen bestehende Aufnahmetabellen um die Spalte `retention_archived_at` (nullbar) und die Spalte `archive_revision` (`INTEGER NOT NULL DEFAULT 0`). Bestehende persönliche Archivkennzeichen und Aufnahmeinhalte bleiben erhalten und gelten als Ausgangszustand; es werden keine historischen Übergangsereignisse erzeugt. Wiederholte Starts behalten Archivnachweise und Revisionen. Bestehende Werte von `deletion_mode` archivieren ab dem ersten Lauf nach dem Upgrade. Dateien, die ältere Softwareversionen entfernt hatten, bleiben entfernt.

## Betriebliche Prüfungen

- Prüfen Sie vor dem Einschalten der Archivierung mit `GET /admin/auto-deletion/stats`, welche Aufnahmen archivierbar und welche ausgenommen sind.
- Prüfen Sie `recording.archived` und `recording.unarchived` im **System-Auditprotokoll** (`/admin#/system/audit`) sowie den Zähler `errors` jedes Laufs.
- Wird eine Wiederherstellung verweigert, prüfen Sie, ob die aufrufende Person Eigentümer der Aufnahme ist.
- Wird eine wiederhergestellte Aufnahme erneut archiviert, prüfen Sie ihr Erstellungsdatum und ihre wirksame Tag- oder globale Frist.
- Prüfen Sie die tägliche Zeile `Ownerless recording cleanup:` auf gelöschte Aufnahmen.

Siehe [Auditprotokoll](audit-log.md) für Ereignisfelder und Audit-Einstellungen sowie [Datenschutz](privacy.md) für die Datenspeicher.
