# Auditprotokoll

[English](../../admin-guide/audit-log.md)

Öffnen Sie als Site-Administrator **System → System-Auditprotokoll**. Filtern Sie für einen gemeldeten Zugriff nach Aufnahme-ID, Zeitraum und Ergebnis. Für ein einzelnes Konto führt der Aktivitätslink im Konteneditor direkt zur gefilterten Ansicht. Aktualisieren Sie die Liste, bevor Sie zu älteren Treffern blättern. Eine Poolleitung allein hat keinen Zugriff auf dieses Protokoll.

Zeilen erfassen UTC-Zeit, Art des Zugriffs, Ereignis, Ergebnis, betroffene IDs und festgelegte Ereignisdetails. Transkripte, Zusammenfassungen, Dateinamen, Passwörter und Suchtexte sind ausgenommen. Beim Anlegen und Löschen eines Kontos kann der Benutzername enthalten sein. Schützen Sie IP-Adressen, Browserangaben und exportierte Protokolle entsprechend.

## Export und Fehlersuche

CSV eignet sich für Tabellenkalkulationen, JSONL für eine maschinelle Auswertung. Der Export übernimmt die aktuellen Filter. Halten Sie für einen späteren Abgleich Filter, Zeitzone und höchste Ereignis-ID fest. Unter `/admin/audit-log` kann eine Site-Admin-Browsersitzung bis zu 500 Zeilen pro Seite abrufen. Persönliche API-Token sind im Klinikmodus gesperrt. Lesen und Exportieren des Protokolls werden selbst protokolliert.

Leseereignisse eines anderen Workers können verzögert eintreffen. Fehlt ein erwarteter Eintrag auch nach dem Aktualisieren, prüfen Sie die geschützten Dienstprotokolle auf `AUDIT-FALLBACK` und `audit.write_failed`. Aktive Site-Administratoren dürfen fremde Diktate über einen auditierten Leseweg öffnen. Kann der Pflichtnachweis nicht gespeichert werden, antwortet der Dienst mit HTTP 503. Auch Einreichen und Schlussfreigabe hinterlassen Pflichtnachweise.

## Einstellungen

| Einstellung | Umgebungsvariable | Wirkung |
|---|---|---|
| `audit_log_enabled` | `AUDIT_LOG_ENABLED` | Standardmäßig aktiv. Bestimmte Verwaltungs- und Workflowereignisse bleiben auch nach dem Ausschalten erfasst. |
| `audit_log_retention_days` | `AUDIT_LOG_RETENTION_DAYS` | Standardwert 365. Null hebt die Zeitgrenze auf. Der tägliche Kliniklauf bewahrt auch ältere Zeilen. |
| `audit_log_store_ip` | `AUDIT_LOG_STORE_IP` | Standardwert full. truncated speichert das IPv4-/24- oder IPv6-/48-Netz, off unterdrückt die Adresse. |
| `audit_log_record_views` | `AUDIT_LOG_RECORD_VIEWS` | Standardmäßig aktiv. Beim Ausschalten entfallen gewöhnliche Ansichten und Wiedergaben. Downloads und Exporte bleiben erfasst. |

Prüfen Sie Datenbankrechte und getrennt geschützte Exporte im Betrieb. Der Ereigniskatalog enthält auch historische oder gesperrte Funktionen.

## Ereigniskatalog

### Anmeldung und Token

`auth.login.succeeded`, `auth.login.failed`, `auth.login.locked`, `auth.logout`, `auth.password.changed`, `auth.sso.linked`, `auth.sso.unlinked`, `auth.sso.link_requested`, `token.created`, `token.changed`, `token.revoked`, `token.first_used`.

### Zugriff

`recording.viewed`, `recording.content.read`, `recording.media.played`, `recording.media.downloaded`, `recording.downloaded`, `recording.exported`.

### Änderungen

`recording.edited`, `recording.deleted`, `recording.audio.deleted`, `recording.archived`, `recording.unarchived`, `recording.reprocessed`, `recording.job.canceled`.

### Verwaltung

`admin.user.created`, `admin.user.changed`, `admin.user.password_set`, `admin.user.unlocked`, `admin.user.role_changed`, `admin.user.deleted`, `admin.setting.changed`, `admin.retention.run`, `admin.inquire.processed`, `group.created`, `group.changed`, `group.deleted`, `group.member.added`, `group.member.role_changed`, `group.member.removed`.

### Protokollbetrieb

Das Ereignis `admin.upstream_voice.purge` protokolliert das Bereinigen alter Stimmprofildaten. An Phase und Anzahl ist erkennbar, ob die Bereinigung abgeschlossen ist oder noch Dateien zu bearbeiten sind.

`audit.viewed`, `audit.exported`, `audit.pruned`, `audit.write_failed`.

[Originalanleitung von Speakr](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/audit-log.md).
