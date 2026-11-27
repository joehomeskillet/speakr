# Auditprotokoll

[English](../../admin-guide/audit-log.md)

Öffnen Sie **Verwaltung → System → System-Auditprotokoll** (`/admin#/system/audit`) mit der Administrationsrolle. Poolleitung oder Identitätsadministration allein berechtigen nicht dazu. Bei einem bestehenden Konto in **Benutzer und Rollen** öffnet **Systemaktivität dieses Kontos** das Protokoll mit diesem Konto als Filter.

Das Protokoll erfasst Anmeldungen, Inhaltszugriffe, Änderungen und Administration in `audit_event`. Es ist standardmässig eingeschaltet. Es ist von der Revisions- und Workflowhistorie eines Diktats getrennt; nicht jeder Vorgang in der Klinik-Anwendung erhält einen Eintrag in dieser Tabelle.

## Ereignisse suchen und exportieren

Filtern Sie nach Zeitraum, Benutzer, Ereignisgruppe, Ereignis, Aufnahme-ID, Ergebnis oder Akteurtyp. Datumswerte in den Serverfiltern sind UTC. Der Benutzerfilter erfasst das handelnde oder betroffene Konto. Klinik-Auditeinträge verwenden die ganzzahlige Aufnahme-ID, meist die ID der Ankeraufnahme des Diktats, nicht die Diktat-UUID.

Der Endpunkt für Browsersitzungen `GET /admin/audit-log` liefert die neuesten Ereignisse zuerst, standardmässig 50 und höchstens 500 Zeilen pro Anfrage. Aktualisieren Sie vor dem Blättern zu älteren Ergebnissen. Der Leser schreibt zuvor die wartenden Ereignisse seines eigenen Prozesses; Ereignisse anderer Worker können später eintreffen.

Wählen Sie CSV für eine Tabellenkalkulation oder JSONL zur automatisierten Auswertung. `GET /admin/audit-log/export` verwendet dieselben Filter und exportiert die ältesten Zeilen zuerst, begrenzt durch die höchste passende Ereignis-ID beim Exportstart. Halten Sie Filter, Zeitzone und höchste exportierte ID bei einer Untersuchung fest. Lesen und Exportieren erzeugen `audit.viewed` und `audit.exported`; wiederholte Ansichten werden innerhalb von 30 Minuten zusammengefasst.

Der Klinik-Fork bietet keinen allgemeinen API-v1-Zugang und keine persönlichen API-Tokens zum Abrufen des Protokolls. Maschinenzugangsdaten sind dem Importagent-Kanal vorbehalten. Verwenden Sie für dieses Protokoll die Browsersitzung einer berechtigten Administration.

## Inhalt einer Zeile

| Feld | Bedeutung |
|---|---|
| `id`, `created_at` | Steigende Ereignis-ID und UTC-Zeit |
| `event`, `event_group` | Ereignisname und Gruppe: `auth`, `access`, `change`, `admin` oder `audit` |
| `actor_kind`, `actor_user_id`, `actor_token_id` | Akteurtyp und zugehörige Konto- oder Token-IDs; historische Tokenereignisse können erhalten bleiben |
| `recording_id`, `recording_owner_id`, `target_user_id`, `group_id` | Für das Ereignis relevante IDs; nicht jedes Ereignis belegt jedes Feld |
| `outcome` | `success`, `denied` oder `failed` |
| `ip`, `user_agent` | Clientadresse gemäss IP-Einstellung und höchstens 160 Zeichen User-Agent-Information; bei Systemakteuren leer |
| `details` | Ereignisspezifisch erlaubte Werte; zu grosse Details werden durch einen Kürzungshinweis ersetzt |

Die Positivliste schliesst Transkripte, Zusammenfassungen, Notizen, Titel, Dateinamen, Suchanfragen, Passwörter, Einmalcodes und Providerfehlertexte aus. Kontoerstellung und historische Kontolöschung können einen Benutzernamen erfassen. Behandeln Sie Adressen, User-Agent-Informationen und Exporte als schützenswert. Die Zeilen haben keine Fremdschlüssel zu den referenzierten Konten oder Aufnahmen; diese Referenzen können daher die ursprünglichen Objekte überdauern. IDs anderer Tabellen können unter SQLite erneut vergeben werden; interpretieren Sie sie zusammen mit Zeitpunkt und Löschhistorie.

## Klinikereignisse

| Ereignis oder Gruppe | Bedeutung |
|---|---|
| `auth.login.succeeded`, `auth.login.failed`, `auth.login.locked`, `auth.logout`, `auth.password.changed` | Lokale Anmeldung, Sperre, Abmeldung und Passwortänderungen |
| `recording.content.read` | Diktatinhalt oder inhaltstragende Metadaten wurden gelesen; `details.part` bezeichnet Details, Korrektur, Historie, Kontext, Audio oder Zusammenfassung |
| `recording.media.played`, `recording.media.downloaded` | Wiedergabe oder Download über die Medienrouten einer Aufnahme |
| `recording.edited` | Namen geänderter Felder oder Workflowschritte, nicht deren Inhalt |
| `recording.archived`, `recording.unarchived` | Archivübergang einer Aufnahme mit Regel und Geltungsbereich |
| `recording.reprocessed`, `recording.job.canceled` | Angeforderte Verarbeitung oder Abbruch |
| `admin.user.*`, `admin.setting.changed` | Konto- und Einstellungsverwaltung; das Register enthält auch historische Upstream-Vorgänge |
| `audit.viewed`, `audit.exported`, `audit.write_failed` | Protokollzugriff und Nachweise fehlgeschlagener Schreibvorgänge |

Das aktuelle Panel **KI-Zusammenfassung** erfasst jedes Lesen seines Zustands als `recording.content.read` mit `part=summary` und jede manuelle Erzeugungsanfrage als `recording.reprocessed` mit `kind=summary`. Änderungen in **Modellwahl** werden als `admin.setting.changed` für `clinic_task_models` erfasst; der Modellkonfigurationswert wird nicht in die Auditdetails übernommen.

Das Modul zur Bereinigung alter Stimmdaten registriert bei seiner Verwendung `admin.upstream_voice.purge`. Phase und Zähler unterscheiden einen abgeschlossenen Datenbankschritt von verbleibenden Dateiarbeiten. Ein Ereignisname im Register belegt weder eine entsprechende Klinikoberfläche noch einen unterstützten Vorgang.

## Pflichtnachweise und Lücken

Eine aktive Standortadministration kann fremde Transkripte oder Audiodateien über die eigenen Admin-Inhaltsrouten ohne Begründungsabfrage öffnen. Vor jeder erfolgreichen Öffnung muss die Auditzeile gespeichert sein; andernfalls antwortet der Server mit `503 audit_unavailable` und liefert keinen Inhalt. Dieser Lese- oder Wiedergabezugriff erteilt keine Bearbeitungs-, Freigabe- oder Exportrechte.

Lassen Sie `audit_log_enabled` und `audit_log_record_views` eingeschaltet: Das Ausschalten der ersten Einstellung verhindert diese Admin-Inhaltszugriffe; das Ausschalten der zweiten verhindert zusätzlich die Admin-Audiozugriffe. Die reine Metadatenübersicht der Administration schreibt selbst kein Inhaltsleseereignis. Jede fremde Diktatzeile, die die Arbeitsliste einer Standortadministration liefert, erhält ein eigenes Leseereignis mit `part=worklist`.

Archivübergänge einer Aufnahme und ihr verpflichtender Auditnachweis werden gemeinsam gespeichert. Auch Workflow-Senden, Abnahmeschritte und endgültige Freigabe benötigen transaktionale Nachweise. Der eigene Eintrag des Archivworkflows ist von einem Aufbewahrungsübergang einer Aufnahme getrennt; siehe [Aufbewahrung](retention.md).

Manche gewöhnlichen Lesezugriffe werden dedupliziert. Ablehnungen durch den Klinik-Routenwächter erzeugen nicht automatisch eine Auditzeile. Das Kopieren von Text oder Speichern einer bereits gelieferten Datei im Browser ist für den Server nicht sichtbar. Pool- und Rechtehistorie, Einwilligungshistorie, Ausgabequittungen und Diktatrevisionen besitzen zudem eigene Tabellen; dieses Protokoll ersetzt sie nicht.

## Konfiguration

Setzen Sie diese Werte in **Systemeinstellungen**. Gespeicherte Einstellungen haben Vorrang vor den entsprechenden Umgebungsvariablen.

| Einstellung | Umgebung | Standard und Wirkung |
|---|---|---|
| `audit_log_enabled` | `AUDIT_LOG_ENABLED` | `true`; schaltet gewöhnliche Ereignisse ein oder aus. Auditvorgänge, Änderungen der Auditeinstellungen und ausdrücklich verpflichtende Ereignisse bleiben erfasst. |
| `audit_log_retention_days` | `AUDIT_LOG_RETENTION_DAYS` | `365`; `0` hebt die Altersgrenze auf. Die Klinikbereinigung behält auch Zeilen jenseits der Grenze. |
| `audit_log_store_ip` | `AUDIT_LOG_STORE_IP` | `full`; `truncated` behält das IPv4-/24- oder IPv6-/48-Präfix, `off` lässt die Adresse weg. |
| `audit_log_record_views` | `AUDIT_LOG_RECORD_VIEWS` | `true`; ausgeschaltet entfallen gewöhnliche Aufnahmeansichts- und Wiedergabeereignisse, nicht Downloads oder Inhaltsleseereignisse. |

## Aufbewahrung, Schutz und Fehler

Die tägliche Klinikbereinigung löscht keine Auditzeilen. Sie protokolliert `Audit pruning withheld (separate_privileged_retention)` und die Anzahl jenseits der Altersgrenze. Der Helfer für privilegierte Aufbewahrung prüft nur eine Richtlinie und zählt geeignete Zeilen; er ist kein Löschdienst. Archivierungs- und Wiederherstellungsereignisse sind auch ausserhalb des Klinikmodus vor gewöhnlicher Bereinigung geschützt.

ORM-Listener verweigern Änderungen und Löschung von Auditzeilen. Ein Datenbanktrigger-Installer liegt in `src/clinic/audit_db_guards.py`, wird beim Start aber nicht aufgerufen. Seine Existenz belegt daher keinen Schutz gegen direktes SQL. Beschränken Sie Datenbankrollen und schützen Sie unabhängige Exporte; Datenbankeigentümer oder Superuser bleiben ein betriebliches Risiko. Lücken bei Ereignis-IDs allein beweisen keine Löschung, da auch Rollbacks und Deduplizierung Lücken hinterlassen können.

Fehlt ein erwartetes Ereignis auch nach Aktualisierung, prüfen Sie geschützte Dienstprotokolle auf `AUDIT-FALLBACK` und nachfolgende `audit.write_failed`-Zeilen. Bewahren Sie die Fallback-Nachweise bei der Untersuchung auf. Diese Einträge melden fehlgeschlagene Schreibvorgänge; sie rekonstruieren die fehlenden Datenbankereignisse nicht automatisch.

Siehe [Datenschutz](privacy.md) zur Datenhandhabung und [Dateispeicher](storage.md) zu Sicherungen.
