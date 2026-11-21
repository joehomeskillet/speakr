# Auditprotokoll
<small>[English](../../admin-guide/audit-log.md)</small>

Das Auditprotokoll erfasst Anmeldungen, Zugriffe auf Aufnahmen und klinische Diktate, Änderungen und administrative Aktionen. Es besteht aus einer Datenbanktabelle, `audit_event`, die nur über `src/services/audit.py` geschrieben wird. Die globale Administration liest es auf der Seite **System-Auditprotokoll**. Das Protokoll ist standardmässig eingeschaltet, auch nach einer Aktualisierung von einer Version ohne Protokoll.

Den Inhalt eines Diktats enthält das Protokoll nie. Was eine Zeile speichert und was nie, steht auf dieser Seite. Wie sich das Protokoll zu den übrigen gespeicherten Daten verhält, beschreibt [Datenschutz und regulierte Daten](privacy.md). Alles, was die Klinikversion betrifft, gilt bei eingeschaltetem `CLINIC_MODE`. Bei ausgeschaltetem Klinikmodus gilt das Upstream-Verhalten.

| Thema | Status in der Klinikversion |
|---|---|
| Erfassung der Ereignisse, die Seite **System-Auditprotokoll**, Export als CSV und JSONL | Verfügbar |
| Protokolliertes Öffnen von Transkripten und Audio anderer Benutzer durch die globale Administration | Verfügbar |
| Tägliches Entfernen von Zeilen, die älter als `audit_log_retention_days` sind | Nur Upstream: Im Klinikmodus löscht das Entfernen nichts; das Repository enthält keinen getrennt berechtigten Löschprozess. |
| Datenbank-Trigger gegen Änderungen an `audit_event` | Vorbereitet, deaktiviert: Die Installationsfunktion ist vorhanden, der Start führt sie nicht aus |
| Abruf durch ein externes System mit einem API-Token | Nur Upstream: API-Token werden im Klinikmodus abgelehnt |
| Diktatübergreifende Auditliste in **Aktivität und Audit** | Offene Lücke: Es gibt keine Route; die Seite hat keine solche Liste und keinen CSV-Download |
| Auditeinträge der Modellwahl je Aufgabe und der Diktatzusammenfassung | In Prüfung (siehe [Funktionen in Prüfung](#funktionen-in-prufung)) |

## Wo das Protokoll steht und wer es lesen darf

**Pfad.** **Verwaltung** › **System** › **System-Auditprotokoll**, `/admin#/system/audit`. Die Navigationsbezeichnungen gibt es nur auf Deutsch. Die Seite selbst ist der Upstream-Reiter des Auditprotokolls und folgt der Oberflächensprache: Die Überschrift lautet auf Deutsch «Audit-Protokoll», auf Englisch «Audit log».

**Von einem Konto aus.** In **Verwaltung** › **Konten und Pools** › **Benutzer und Rollen**, Bereich **Konten**, erscheint beim Bearbeiten eines bestehenden Kontos die Schaltfläche **Systemaktivität dieses Kontos**. Sie öffnet `/admin#/system/audit?konto=<id>` mit dem Filter **Benutzer** auf diesem Konto. Die Upstream-Schaltfläche «Aktivität anzeigen» gehört zur Upstream-Benutzerliste, die im Klinikmodus nicht angezeigt wird.

**Wer das Protokoll lesen und exportieren darf.**

- **Globale Administration** (**Administrationsrolle**, das Merkmal `User.is_admin`): darf lesen und exportieren. Die Routen `GET /admin/audit-log` und `GET /admin/audit-log/export` prüfen dieses Merkmal. Der klinische Routenschutz lehnt jedes andere Konto mit 403 ab, bevor die Funktion ausgeführt wird; diese Ablehnung wird nicht protokolliert.
- **Lokale Identitätsverwaltung** (**Identitätsadministration**): allein nicht ausreichend. Ohne **Administrationsrolle** kann ein Konto `/admin` nicht öffnen.
- **Poolleitungen** (**Leitung**) und Upstream-Gruppenadministrationen: kein Zugang. Ein Konto, das nur eine Gruppe administriert, erhält im Klinikmodus auf `/admin` die Antwort 403.
- Die getrennten Rechte **Freigeben** und **Originalaudio exportieren** geben keinen Zugang zum Protokoll und werden dafür nicht verlangt.

**Aktivität und Audit** in der Gruppe **Betrieb** ist eine andere Seite. Sie zeigt den Verlauf eines einzelnen Diktats, gebildet aus Revisionen, Freigaben, Übergaben des Arbeitsstands und Archiveinträgen, sowie die Klinikstatistik. `audit_event` liest sie nicht. Die Klinikversion bietet keinen Weg, eine Statistikberechtigung zu erteilen; die Seite wird deshalb für jedes Konto zurückgehalten, siehe [Systemstatistik](statistics.md).

## Inhalt einer Zeile

| Spalte | Inhalt |
|---|---|
| `id` | Eine Nummer, die nie erneut vergeben wird (AUTOINCREMENT unter SQLite, eine Sequenz unter PostgreSQL). Eine entfernte Zeile hinterlässt eine Lücke. Unter PostgreSQL entstehen Lücken auch durch zurückgerollte Aktionen und durch verworfene Wiederholungen; eine Lücke allein beweist also kein Entfernen. Vergleichen Sie mit Ihren externen JSONL-Kopien (siehe [Schutz vor Änderungen](#schutz-vor-anderungen)). |
| `created_at` | Zeitpunkt des Ereignisses, in UTC |
| `event`, `event_group` | Name des Ereignisses (siehe [Ereignisse](#ereignisse)) und seine Gruppe: `auth`, `access`, `change`, `admin` oder `audit` |
| `actor_kind` | `session` (Browser), `api_token`, `sso_token`, `system` (ein geplanter Auftrag oder ein serverseitiges Skript) oder `anonymous` (nicht angemeldet, zum Beispiel eine abgelehnte Anmeldung). `public_link` ist kein gültiger Wert und kein gültiger Filter mehr; die Oberfläche enthält noch eine Beschriftung dafür. |
| `actor_user_id`, `actor_token_id` | Konto und API-Token der Anfrage, als ID |
| `ip`, `user_agent` | Client-Adresse gemäss `audit_log_store_ip` und die ersten 160 Zeichen des User-Agents. Beim Akteur `system` sind beide leer. |
| `recording_id`, `recording_owner_id` | Aufnahme und, bei Upstream-Routen, ihr Eigentümer zum Zeitpunkt des Ereignisses. Bei einem klinischen Diktat ist `recording_id` die ganzzahlige ID der Ankeraufnahme des Diktats, nicht die UUID des Diktats. Die Diktatübersicht der Administration `GET /api/admin/clinic/dictations` liefert beide IDs. Bei den klinischen Inhaltszugriffen (`part` `detail`, `review`, `history`, `context` und die `audio_*`-Teile) und bei Zeilen der Bearbeitungssperre ist `recording_owner_id` leer; bei Zeilen des klinischen Ablaufs, der Freigabe und der Abnahme enthält es den Autor des Diktats. Zum Filter **Aufnahme-ID** siehe [Die Seite System-Auditprotokoll](#die-seite-system-auditprotokoll). |
| `target_user_id`, `group_id` | Konto oder Gruppe, die das Ereignis betrifft |
| `outcome` | `success`, `denied` oder `failed` |
| `details` | Einige Werte des Ereignisses, aus einer festen Liste von Schlüsseln je Ereignis. Ein Schlüssel, der nicht auf der Liste steht, wird mit einer Warnung verworfen. Details mit mehr als 512 Zeichen als JSON werden durch `{"truncated": true}` ersetzt. |

Die Tabelle hat zusätzlich die interne Spalte `dedup_key` für wiederholte Lesezugriffe. Die Routen und die Exporte geben sie nicht aus.

Nie in einer Zeile gespeichert werden: Titel, Dateinamen, Transkript-, Zusammenfassungs- und Notiztext, Suchanfragen, Chatnachrichten, der Freitext einer Ablehnungsbegründung, Namen von API-Token und Gruppen, E-Mail-Adressen, Passwörter, Einmalcodes und Fehlertexte. Konten, Aufnahmen, Token und Gruppen werden über ihre ID genannt. Ein Benutzername wird nur in zwei Ereignissen gespeichert, `admin.user.created` und `admin.user.deleted`, damit sich die Zeilen eines Kontos auch nach dessen Löschung zuordnen lassen. Die Seite zeigt Benutzernamen an, indem sie beim Lesen die IDs der angezeigten Zeilen nachschlägt; ein nicht mehr vorhandenes Konto erscheint als «Gelöschter Benutzer #<id>».

Eine Zeile hat keinen Fremdschlüssel auf die Aufnahme, das Konto oder die Gruppe, die sie nennt. Die Zeilen bleiben deshalb nach einer Löschung erhalten.

## Ereignisse

Die Spalte **Klinikmodus** verwendet die Statusbegriffe dieses Handbuchs. Die Gruppen entsprechen dem Filter **Ereignisse** der Seite.

### Anmeldung und Sitzung (Gruppe `auth`)

| Ereignis | Erfasst, wenn | Details | Klinikmodus |
|---|---|---|---|
| `auth.login.succeeded` | Eine Anmeldung gelang | `method`, `remember`. Die klinische Anmeldeseite schreibt `method` `password` und `remember` `false`. | Verfügbar. Die Werte `entra` und `iwa` von `method` gehören zu den Identitätsanbietern: Vorbereitet, deaktiviert. |
| `auth.login.failed` | Eine Anmeldung oder eine Passwortprüfung wurde abgelehnt. Eine Zeile pro Minute je Adresse und Konto. | `method`, `reason`. Werte im Klinikmodus: `bad_password` (falsches Passwort oder unbekanntes Konto; bei einem unbekannten Konto ist `target_user_id` leer), `locked`, `bad_recovery_code` mit `method` `recovery` (ein Einmalcode liess sich nicht einlösen), `bad_current_password` mit `method` `password_change` (eigene Passwortänderung, bei jeder Ablehnung geschrieben). Die eingegebene Adresse wird nie gespeichert. | Verfügbar. Die Gründe `unknown_account`, `no_local_password`, `password_login_disabled` und `sso_error` stammen aus den Upstream-Routen für Anmeldung und SSO: Nur Upstream. |
| `auth.login.locked` | Die Grenze falscher Passwörter wurde erreicht, je Client-Adresse (`login_max_failed_attempts`) oder für das ganze Konto (das Vierfache dieser Grenze) | `minutes` | Verfügbar |
| `auth.logout` | Ein angemeldeter Benutzer meldete sich ab | | Verfügbar |
| `auth.password.changed` | Ein Benutzer änderte das eigene Passwort oder setzte ein Passwort durch Einlösen eines Aktivierungs- oder Rücksetzcodes | | Verfügbar |
| `auth.sso.linked`, `auth.sso.unlinked` | Ein Konto wurde mit einer externen Identität verknüpft oder davon gelöst | | Vorbereitet, deaktiviert (Entra-Verknüpfung; die Upstream-OIDC-Routen lehnen im Klinikmodus ab). `auth.sso.unlinked`: Die klinische Route zum Lösen funktioniert auch bei deaktiviertem Anbieter, für früher angelegte Verknüpfungen. |
| `auth.sso.link_requested` | Eine Identitätsadministration genehmigte eine einmalige Anfrage zur Verknüpfung eines Kontos | | Vorbereitet, deaktiviert |
| `token.created`, `token.changed`, `token.revoked` | Ein API-Token wurde angelegt, umbenannt bzw. eingeschränkt oder widerrufen | `token_id`, `scopes`, `expires`, `sso_client_id` (`token.changed`: `token_id`, `scopes`; `token.revoked`: `token_id`) | Nur Upstream: Die Token-Routen sind gesperrt (`personal_api_tokens`) |
| `token.first_used` | Ein API-Token wurde zum ersten Mal verwendet | `token_id` | Nur Upstream: Eine Anfrage, die nur mit einem API-Token angemeldet ist, wird mit 403 `clinic_feature_disabled` abgelehnt |

### Zugriff auf eine Aufnahme (Gruppe `access`)

| Ereignis | Erfasst, wenn | Details | Klinikmodus |
|---|---|---|---|
| `recording.viewed` | Eine Upstream-Ansicht einer Aufnahme wurde gelesen: `GET /api/recordings/<id>` oder `GET /api/v1/recordings/<id>`. Eine Zeile pro 30 Minuten je Konto oder Token und Aufnahme. | | Verfügbar auf den Upstream-Routen. Die klinischen Diktatansichten schreiben stattdessen `recording.content.read`. |
| `recording.content.read` | Inhalt oder inhaltstragende Metadaten eines Diktats wurden ausgeliefert | `part` (siehe Tabelle unten) | Verfügbar |
| `recording.media.played` | Audio wurde über die Upstream-Route `/audio/<id>` zum Abspielen angefordert (eine Zeile pro 30 Minuten), oder die globale Administration öffnete das Audio eines Diktats (eine Zeile je Öffnung) | | Verfügbar |
| `recording.media.downloaded` | Audio wurde als Download angefordert (`/audio/<id>?download=true`). Eine Zeile pro 5 Minuten. | | Verfügbar mit dem Fachrecht **Originalaudio exportieren** (Aktion `audio_export`); ohne dieses Recht lehnt der klinische Routenschutz die Anfrage ab |
| `recording.downloaded` | Eine Transkript-, Zusammenfassungs-, Notiz- oder Chatdatei wurde über eine Upstream-Route heruntergeladen | `part`, `format` | Transkript-, Zusammenfassungs- und Notizdownloads: Nur Upstream (`stock_unversioned_export` gesperrt). Chat-Download (`part` `chat`, `format` `docx`): nur mit einem lokalen Textmodell erreichbar (Funktion `chat`), sonst gesperrt. |
| `recording.exported` | Bestehende Aufnahmen wurden in den Exportordner geschrieben | `count` | Nur Upstream: Die Route ist gesperrt (`stock_unversioned_export`) |

Werte von `part` in `recording.content.read`:

| `part` | Geschrieben von | Wiederholungen |
|---|---|---|
| `detail` | `GET /api/clinic/v1/dictations/<id>/detail` (Diktatdetails) | Jede Anfrage |
| `review` | `GET /api/clinic/v1/dictations/<id>/review` | Jede Anfrage |
| `history` | `GET /api/clinic/v1/dictations/<id>/history` | Jede Anfrage |
| `context` | `GET /api/clinic/v1/dictations/<id>/context` (Fallkontext) | Jede Anfrage |
| `audio_revision`, `audio_derived`, `audio_peaks` | Die klinischen Audiorouten: aktuelle Audiorevision, abgeleitetes WAV zum Abspielen und Bearbeiten, Wellenform | Jede Anfrage |
| `worklist` | `GET /api/clinic/v1/worklist/rows` für eine aktive globale Administration: eine Zeile je ausgeliefertes Diktat, dessen Autor und Eigentümer ein anderes Konto ist. Die Arbeitsliste jedes anderen Kontos wird nicht erfasst. | Jede Anfrage |
| `transcript` | Öffnen eines Transkripts durch die globale Administration (`GET /api/admin/clinic/dictations/<recording id>/transcript`) und die Upstream-Route `GET /api/v1/recordings/<id>/transcript` | Öffnung durch die Administration: jede Anfrage. API v1: eine Zeile pro 30 Minuten. |
| `summary`, `notes`, `speakers` | Die Upstream-Routen der API v1 | Eine Zeile pro 30 Minuten |

Eine Anfrage nach einer bestehenden Upstream-Aufnahme, auf die das Konto keinen Zugriff hat, wird als dasselbe Ereignis mit dem Ergebnis `denied` erfasst, einmal pro Minute, wenn die Funktion selbst ablehnt. Eine Anfrage, die der klinische Routenschutz ablehnt, erreicht die Funktion nie und wird nicht in `audit_event` geschrieben; siehe [Nicht in diesem Protokoll erfasst](#nicht-in-diesem-protokoll-erfasst).

**Öffnen von Inhalten durch die globale Administration.** Eine aktive globale Administration (`User.is_admin` und ein Klinikkonto mit dem Status `active`) kann Transkript und Audio jedes Diktats über `GET /api/admin/clinic/dictations/<recording id>/transcript` und `…/audio` öffnen. Es gibt keine Begründungsabfrage und keine Bestätigung. Jede Öffnung wird ohne Wiederholungsfenster erfasst, als `recording.content.read` mit `part` `transcript` oder als `recording.media.played`. Der Inhalt wird erst ausgeliefert, wenn die Zeile gespeichert ist. Andernfalls lautet die Antwort 503 `audit_unavailable`. Öffnungen werden deshalb abgelehnt, solange `audit_log_enabled` ausgeschaltet ist, Audioöffnungen zusätzlich, solange `audit_log_record_views` ausgeschaltet ist. Das Auflisten der Übersicht selbst (`GET /api/admin/clinic/dictations`, nur Metadaten) wird nicht erfasst. Mit S3-Speicher wird das Audio mit einer Weiterleitung zum Speicher beantwortet; die Weiterleitung gilt als Öffnung.

Text, der nach der Auslieferung im Browser kopiert oder gespeichert wird, ist für den Server nicht sichtbar und wird nicht erfasst.

### Änderungen (Gruppe `change`)

| Ereignis | Erfasst, wenn | Details | Klinikmodus |
|---|---|---|---|
| `recording.edited` | Eine Aufnahme oder ein klinisches Diktat wurde geändert, oder ein Schritt des klinischen Ablaufs wurde wirksam | `fields`: Namen von Feldern oder Schritten, nie Feldinhalte. Einträge der Bearbeitungssperre enthalten zusätzlich die Diktat-ID, die Generation und die Konto-ID der Person, welche die Sperre zuvor hielt; eine Importzuweisung (nicht erreichbar) würde die IDs von Lieferung und Importabsicht speichern. Siehe Tabelle unten. | Verfügbar |
| `recording.deleted` | Eine Aufnahme wurde gelöscht | `reason`: `user`, `retention`, `account_deleted`, `ownerless`, `upload_canceled` oder `merged` | Das manuelle Löschen ist gesperrt (`clinical_user_hard_delete`), und die Aufbewahrung löscht nie. Zeilen können trotzdem entstehen durch die tägliche Bereinigung von Aufnahmen ohne Eigentümer (`ownerless`, unverändertes Upstream-Verhalten) und durch die Upstream-Route, die einen laufenden Upload abbricht (`upload_canceled`). Ältere Zeilen können `retention` enthalten. |
| `recording.audio.deleted` | Das Audio einer Aufnahme wurde gelöscht und der Text behalten | `reason`: `user` oder `retention` | Nur Upstream: Die Routen zum Löschen von Audio sind gesperrt, und die Aufbewahrung löscht nie |
| `recording.archived` | Eine Aufnahme kam durch die Aufbewahrung oder eine Benutzeraktion ins Archiv | `rule`, `scope`, optional `retention_days` und `tag_id` | Verfügbar. Wird auch bei ausgeschaltetem Protokoll erfasst. |
| `recording.unarchived` | Ein Benutzer stellte eine archivierte Aufnahme wieder her | `rule`, `scope`, optional `retention_days` und `tag_id` | Verfügbar. Wird auch bei ausgeschaltetem Protokoll erfasst. |
| `recording.reprocessed` | Eine neue Transkription, Zusammenfassung, Titelerzeugung oder Übersetzung wurde gestartet | `kind`: `transcription`, `summary`, `title` oder `translation` | Verfügbar für `transcription` (Bearbeitungsrecht). `summary`, `title` und `translation` stammen aus Upstream-Routen, die nur mit einem lokalen Textmodell erreichbar sind (Funktion `generative_summary`), sonst gesperrt. |
| `recording.job.canceled` | Ein wartender oder laufender Auftrag einer Aufnahme wurde abgebrochen | `kind`: der Auftragstyp | Verfügbar |

Werte von `fields` in `recording.edited`:

| `fields` | Erfasst, wenn | Immer erfasst |
|---|---|---|
| `title`, `participants`, `notes`, `summary`, `transcript`, `translation`, `speakers`, `meeting_date`, `deletion_exempt` | Eine Upstream-Bearbeitungsroute änderte das Feld | Nein |
| `workflow_status`, `workflow_send` | Ein Diktat wurde gesendet | Ja |
| `workflow_status` und `workflow_submit`, `workflow_accept`, `workflow_reject` oder `workflow_reopen` | Ein Diktat wurde zur Abnahme gegeben, abgenommen, abgelehnt oder wieder geöffnet. Bei einer Ablehnung ist `target_user_id` das Konto, an das das Diktat zurückgeht; der Begründungstext wird nicht gespeichert. | Ja |
| `workflow_status`, `workflow_finalize` | **Endgültig freigeben** wurde wirksam | Ja |
| `workflow_status`, `workflow_acceptance_resubmitted` | Eine gespeicherte Korrektur veränderte den Text eines abgenommenen Diktats, das deshalb wieder zur Abnahme ging | Ja |
| `assignee` | Die globale Administration oder eine Poolleitung wies das Diktat zu; `target_user_id` ist die neue zugewiesene Person | Ja |
| `acceptance_policy` | Die Abnahmerichtlinie (**Abnahme**) eines Diktats wurde veröffentlicht. Ein abgelehnter Versuch wird mit dem Ergebnis `denied` erfasst. | Die Ablehnung: ja. Die Veröffentlichung: nein. |
| `title`, `priority`, `deadline_date`, `deadline_time`, `author_user_id`, `assignee_user_id`, `pool_id` | Metadaten eines Diktats wurden geändert (Umbenennen, Priorität und Termin, Neuzuordnung) | Nein |
| `lease.acquire`, `lease.renew`, `lease.release`, `lease.takeover`, `lease.expire`, mit `dictation:<id>`, `generation:<n>` und bei einer Übernahme durch eine andere Person oder einer Aufhebung wegen Neuzuweisung `from:<user id>` (dann auch in `target_user_id`) | Eine Bearbeitungssperre wurde gesetzt, verlängert, aufgehoben, von einer anderen Person übernommen oder als abgelaufen erkannt. Ein Ablauf wird erst erfasst, wenn ein späteres Setzen oder Übernehmen ihn vorfindet. Seine Zeile hat `actor_kind` `system`, als `actor_user_id` die Person, welche die Sperre hielt, und als `created_at` den Ablaufzeitpunkt. | Nein |
| `clinic_backfill` | Das Skript `scripts/clinic_backfill_dictations.py --apply` legte ein Diktat aus einer Altaufnahme an (Akteur `system`) | Nein |
| `import_delivery_id`, `import_intent_id` | Zuweisung eines nicht zugeordneten Imports (**Klärliste**). Keine Web-Route ruft die Zuweisung auf; in der freigegebenen Version wird diese Zeile deshalb nicht geschrieben. | Ja |

«Immer erfasst» bedeutet, dass die Zeile auch bei ausgeschaltetem `audit_log_enabled` geschrieben wird. Eine solche Aktion wird zusammen mit ihrer Zeile gespeichert. Lässt sich die Zeile nicht anfügen, schlägt die Aktion fehl (`audit_unavailable`); der Schritt wird also nie ohne seinen Nachweis wirksam.

Archivübergänge speichern den Zustand der Aufnahme, eine Zeile in `audit_event` und eine Zeile im nur erweiterbaren Archivbuch `clinic_archive_event` in einer Transaktion. Die geplante Archivierung hat den Akteur `system`, ein manuell gestarteter Lauf das Konto der globalen Administration, das ihn gestartet hat. Die Regeln sind `retention.global`, `retention.tag`, `user.archive` und `user.restore`. Einzelheiten stehen unter [Automatische Archivierung und Aufbewahrungsregeln](retention.md).

### Administration (Gruppe `admin`)

| Ereignis | Erfasst, wenn | Details | Klinikmodus |
|---|---|---|---|
| `admin.user.created` | Eine Identitätsadministration legte ein Konto an (**Konto anlegen**) | `username`, `is_admin` (im Klinikmodus immer `false`) | Verfügbar. Die erste Administration, angelegt mit `scripts/clinic_bootstrap_admin.py`, erhält keine Zeile; das Skript schreibt nur eine Zeile ins Anwendungsprotokoll. |
| `admin.user.changed` | Ein Konto wurde geändert | `fields`: nur Namen. Werte im Klinikmodus: `username`, `email`, `name`, `storage_quota_mb`, `monthly_token_budget`, `monthly_transcription_budget`, `status`, `login_policy`, `is_admin`, `is_identity_admin`, `create_groups_granted`, `activate_local` oder `reset_local` (ein Einmalcode wurde ausgestellt), `recovery_refused` (ein Code für eine andere Administration wurde abgelehnt), `local_account_policy_adopted` | Verfügbar |
| `admin.user.password_set` | Eine Upstream-Administration setzte das Passwort eines Kontos | `self` | Nur Upstream: Im Klinikmodus stellt eine Identitätsadministration stattdessen Einmalcodes aus |
| `admin.user.unlocked` | Eine Identitätsadministration hob die Anmeldesperre eines Kontos auf | | Verfügbar |
| `admin.user.role_changed` | Die **Administrationsrolle** wurde erteilt oder entzogen | `old`, `new` | Verfügbar. Eine Änderung der **Identitätsadministration** erscheint als `is_identity_admin` in `admin.user.changed`. |
| `admin.user.deleted` | Eine Upstream-Administration löschte ein Konto | `username`, `recordings_deleted` | Nur Upstream: Konten werden deaktiviert, nie gelöscht; die Löschroute ist gesperrt (403 `clinic_feature_disabled`, Funktion `clinical_user_hard_delete`) |
| `admin.setting.changed` | Eine Systemeinstellung wurde gespeichert oder geleert | `key`. Alter und neuer Wert (`old`, `new`) nur für Schalter-, Zahl- und Auswahleinstellungen einer festen Liste, zum Beispiel die vier `audit_log_*`-Einstellungen, die Aufbewahrungseinstellungen, die Anmeldesperre und `transcript_length_limit`. Bei anderen Einstellungen nur der Schlüssel. Ein Schlüssel, den die Anwendung nicht definiert, wird als `other` gespeichert. | Verfügbar. Schreibende Klinikseiten: Eine gespeicherte Metadatenspalte auf **Spalten** wird mit dem Schlüssel `other` erfasst; eine gespeicherte Zuordnung auf **Importzuordnung** mit dem Schlüssel `clinic_import_routing:<config id>`, und zwar immer. Freigegebene Importquellen lassen sich nicht über die Weboberfläche anlegen (keine Route und kein Befehl legt eine an); diese Zeile entsteht daher nur für Quellen, die bereits in der Datenbank stehen. |
| `admin.retention.run` | Die Archivierung wurde auf der Verwaltungsseite gestartet (`POST /admin/auto-deletion/run`) | `checked`, `archived`, `already_archived`, `deleted_audio_only`, `deleted_full`, `errors`. Die beiden Zähler `deleted_*` sind immer `0`, weil jeder Modus archiviert. | Verfügbar |
| `admin.inquire.processed` | Eine Upstream-Administration startete die Verarbeitung von Aufnahmen für Ask Ambrose | `processed`, `failed`, `total`, `force` | Nur Upstream: `/api/admin/migrate_recordings` ist gesperrt (`generic_admin_content_access`). `/admin/inquire/process-recordings` ist nur mit einem lokalen Textmodell entsperrt (Funktion `inquire`); der klinische Routenschutz lehnt die Anfrage dann mit 403 `clinic_feature_unsupported` ab, weil sie keine Aufnahme nennt. |
| `group.created`, `group.changed`, `group.deleted` | Eine Gruppe wurde angelegt, geändert oder gelöscht | `group.changed`: `fields` | Nur Upstream: Die Verwaltung von Gruppen und Teams ist gesperrt (`internal_sharing`) |
| `group.member.added`, `group.member.role_changed`, `group.member.removed` | Ein Mitglied wurde einer Gruppe hinzugefügt, in seiner Rolle geändert oder entfernt | `role`, `old_role` | Nur Upstream. Änderungen an Pools, Mitgliedschaften und Poolleitungen gehen stattdessen in den Poolverlauf, siehe unten. |
| `admin.upstream_voice.purge` | Entfernen von Upstream-Sprecherprofilen und Stimmdaten. Das Ereignis wird erst registriert, wenn die Bereinigungsfunktion läuft. | `operation`, `phase`, `counts` | Vorbereitet, deaktiviert: Keine Route und kein Befehl ruft die Funktion auf |

### Das Protokoll selbst (Gruppe `audit`)

| Ereignis | Erfasst, wenn | Details | Klinikmodus |
|---|---|---|---|
| `audit.viewed` | Das Protokoll wurde gelesen. Eine Zeile pro 30 Minuten je Konto der globalen Administration. | `filters`: die Namen der verwendeten Filter | Verfügbar |
| `audit.exported` | Ein Export wurde vollständig übertragen | `filters`, `count`, `format` | Verfügbar |
| `audit.pruned` | Zeilen nach Ablauf der Aufbewahrungsfrist wurden entfernt | `count`, `cutoff` | Im Klinikmodus nie geschrieben, weil das Entfernen zurückgehalten wird (siehe [Aufbewahrung der Auditzeilen](#aufbewahrung-der-auditzeilen)) |
| `audit.write_failed` | Ereignisse liessen sich nicht speichern (siehe [Störungen](#storungen)) | `count` | Verfügbar |

### Upstream-Ereignisse, die nicht zu dieser Version gehören

Die Upstream-Ereignisse zu öffentlichen Links zum Teilen und zum internen Teilen (`share.internal.*`, `share.public.*`), zu Inkognito-Uploads, zur Selbstregistrierung und zum Zurücksetzen des Passworts per E-Mail gehören nicht zu dieser Version und werden nicht geschrieben. Bereits gespeicherte Zeilen behalten ihre Namen.

## Nicht in diesem Protokoll erfasst

- Aufnahmelisten, Suchen und die Arbeitsliste eines Kontos, das keine aktive globale Administration ist
- Fragen an den Chat einer Aufnahme (nur mit einem lokalen Textmodell erreichbar)
- Das Anlegen einer Aufnahme durch Upload oder Import
- Änderungen an Tags und Ordnern sowie am persönlichen Posteingangs- und Markierungsstatus
- Eine Anfrage mit einem ungültigen API-Token
- Die Statusabfragen, die eine offene Seite in festen Abständen sendet, und die Wellenform einer Upstream-Aufnahme (die klinische Wellenformroute erfasst `audio_peaks`)
- Die Statistikseiten der globalen Administration
- Text, der im Browser kopiert oder gespeichert wird
- **Ablehnungen durch den klinischen Routenschutz.** Protokolliert werden nur Ablehnungen durch die Zugriffsregeln und nicht klassifizierte Routen, im Anwendungsprotokoll auf der Stufe WARNING im Logger `src.clinic.guard`: eine Ablehnung durch die Zugriffsregeln als `clinic policy denied <action> <endpoint> on recording <id> for user <id>: <reason>`, eine nicht klassifizierte Route als `clinic policy: unclassified route … refused`. Alle anderen Ablehnungen des Routenschutzes werden nicht protokolliert, darunter gesperrte Funktionen (403 `clinic_feature_disabled`), Einstellungsobergrenzen (403 `clinic_policy_ceiling`), 403 `clinic_scope_forbidden`, `clinic_no_target` und `clinic_feature_unsupported`, 400 `invalid_recording_id`, 404 für eine nicht vorhandene Aufnahme sowie die Antwort 403, die ein Konto ohne **Administrationsrolle** auf `/admin` oder `/admin/audit-log` erhält. Der Versuch eines solchen Kontos, das Auditprotokoll zu öffnen, hinterlässt daher keine Spur.
- **Pools und Fachrechte.** Änderungen an Pools, Mitgliedschaften, Poolleitungen und Fachrechten werden mit altem und neuem Wert in den Poolverlauf `ClinicPoolHistory` geschrieben. Er ist nur in der Anwendung vor Änderungen geschützt (ORM-Listener, kein Datenbank-Trigger). Keine Seite zeigt diesen Verlauf an. Siehe [Gruppenverwaltung: Autorenpools und Schreibkräfte-Pools](group-management.md).
- **Klinische Ausgabe.** Exportaufträge auf **Ausgabe**, der Dateidownload eines Diktatpakets und die Ausgabe in die Zwischenablage schreiben keine Zeile in `audit_event`. Meldet der Browser ein erfolgreiches Kopieren, wird eine nur erweiterbare Ausgabequittung (`clinic_output_receipt`) angefügt; eine Verweigerung oder ein Abbruch ändert nur den Ausgabeversuch.
- **Aufträge und Statistik.** Ein erneuter Versuch auf **Verarbeitung** speichert nur eine Idempotenzquittung. Die Exportfunktion der Statistik schreibt nur ins Anwendungsprotokoll.
- **Einwilligung für die eigene Stimme.** Erteilte und widerrufene Einwilligungen stehen in der nur erweiterbaren Tabelle `clinic_voice_consent`, Erfassungen und Löschungen von Stimmprofilen in `clinic_voice_consent_audit`.

## Einstellungen

Die Einstellungen werden auf **Systemeinstellungen** (`/admin#/system/einstellungen`) unter ihrem Schlüssel geändert oder in `.env` gesetzt. Ein Wert aus `.env` gilt, bis die globale Administration einen Wert speichert; eine solche Zeile zeigt «In .env festgelegt». In `.env` schaltet nur `true` (in beliebiger Gross- und Kleinschreibung) eine Ja/Nein-Einstellung ein; `AUDIT_LOG_ENABLED=1` schaltet das Protokoll also aus.

| Einstellung | Variable in `.env` | Standard | Wirkung |
|---|---|---|---|
| `audit_log_enabled` | `AUDIT_LOG_ENABLED` | `true` | `false`: Es wird kein Ereignis erfasst, mit den Ausnahmen unten. |
| `audit_log_retention_days` | `AUDIT_LOG_RETENTION_DAYS` | `365` | Ohne Klinikmodus: Tage, nach denen eine gewöhnliche Zeile entfernt wird; `0` behält jede Zeile. Im Klinikmodus wird nichts entfernt; der Wert bestimmt nur den Stichtag der zurückgehaltenen Zählung und die Mindestfrist einer berechtigten Aufbewahrungsrichtlinie. Ein ungültiger Wert gilt als `365`. |
| `audit_log_store_ip` | `AUDIT_LOG_STORE_IP` | `full` | `full`: Die Client-Adresse wird gespeichert. `truncated`: das `/24`-Netz einer IPv4-Adresse oder das `/48`-Netz einer IPv6-Adresse. `off`: keine Adresse. Ein unbekannter Wert gilt als `full`. |
| `audit_log_record_views` | `AUDIT_LOG_RECORD_VIEWS` | `true` | `false`: `recording.viewed` und `recording.media.played` werden nicht erfasst, und das Öffnen von Audio durch die globale Administration wird mit 503 abgelehnt. `recording.content.read`, Downloads und Exporte werden in jedem Fall erfasst. |

Jede Änderung dieser Einstellungen wird als `admin.setting.changed` mit altem und neuem Wert erfasst, auch bei ausgeschaltetem Protokoll.

Bei ausgeschaltetem Protokoll werden weiterhin geschrieben: die Ereignisse der Gruppe `audit`, Änderungen der `audit_log_*`-Einstellungen, Archiv- und Wiederherstellungsübergänge und die oben mit «Immer erfasst» bezeichneten Klinikschritte. Das Öffnen von Transkripten und Audio durch die globale Administration wird abgelehnt. Alle anderen Ereignisse, auch die klinischen Inhaltszugriffe, werden bei ausgeschaltetem Protokoll nicht erfasst; die Anfragen selbst werden weiterhin beantwortet.

Eine Änderung von `audit_log_store_ip` gilt für neue Zeilen. Adressen in bestehenden Zeilen werden nicht geändert.

Die Client-Adresse ist die Adresse, welche die Anwendung nach ihrer Proxy-Verarbeitung sieht. `TRUSTED_PROXY_HOPS` (Standard `1`) ist die Zahl der Reverse-Proxys, deren Kopfzeile `X-Forwarded-For` vertraut wird. Ohne `TRUSTED_PROXIES` wird der Kopfzeile von jeder Gegenstelle vertraut, und beim Start erscheint eine Warnung dazu. Setzen Sie hinter einem Reverse-Proxy `TRUSTED_PROXIES` auf dessen Adresse oder Netz. Ohne vorgeschalteten Proxy setzen Sie `TRUSTED_PROXY_HOPS=0`; sonst kann ein Client die gespeicherte Adresse selbst wählen.

## Aufbewahrung der Auditzeilen

Der tägliche Wartungslauf beginnt um 2:00 Uhr Serverzeit: Archivierung, Bereinigung der Aufnahmen ohne Eigentümer, dann das Entfernen alter Auditzeilen.

- **Ohne Klinikmodus** (Upstream-Verhalten): Zeilen, die älter als `audit_log_retention_days` sind, werden stapelweise entfernt, und eine Zeile `audit.pruned` hält den Lauf fest.
- **Im Klinikmodus:** Das Entfernen löscht nichts. Es schreibt auf der Stufe INFO im Logger `speakr.audit` die Zeile `Audit pruning withheld (separate_privileged_retention): <n> row(s) past cutoff <cutoff> kept`. Es entsteht keine Zeile `audit.pruned`, und die Tabelle wächst ohne Grenze. Planen Sie Datenbankspeicher und Sicherungen entsprechend.
- Zeilen `recording.archived` und `recording.unarchived` werden in keinem Modus entfernt.

Das Entfernen von Auditzeilen im Klinikmodus ist für einen getrennt berechtigten Aufbewahrungsprozess vorgesehen, mit einer genehmigten Aufbewahrungsrichtlinie und einer eigenen Datenbankrolle. Der Code enthält nur den Planungsschritt `plan_privileged_retention`. Er prüft eine genehmigte Richtlinie (Kennung, genehmigende Stelle, Genehmigungszeitpunkt, geprüfte Version, nicht kürzer als `audit_log_retention_days`) und zählt die betroffenen Zeilen; er löscht nie (`performed_deletion` ist `false`). Steht `audit_log_retention_days` auf `0`, wird jede Richtlinie abgelehnt. Das Repository enthält keinen Prozess, der Auditzeilen löscht, und keine genehmigte Aufbewahrungsfrist für das Auditprotokoll: `retention.audit_days` in `clinic-spec/config/production.template.json` ist `null`. Dieser Prozess ist nicht umgesetzt. Die Spezifikation setzt keine rechtliche Aufbewahrungsfrist; sie ist vor einem Produktivbetrieb als Datenschutz- und Fachentscheidung festzulegen.

## Schutz vor Änderungen

- **Anwendung.** ORM-Listener verweigern jede Änderung und jede Löschung eines `AuditEvent` («Audit events are append-only …»). Die einzige Löschanweisung der Anwendung ist das Entfernen alter Zeilen, das im Klinikmodus nichts löscht.
- **Datenbank.** Rohes SQL wird in einem laufenden Betrieb nicht blockiert. Die Installationsfunktion `install_audit_db_guards` (Trigger gegen UPDATE und DELETE unter SQLite und PostgreSQL, unter PostgreSQL zusätzlich gegen TRUNCATE) ist vorhanden, wird beim Start aber nicht aufgerufen, nur in Tests. Status: Vorbereitet, deaktiviert. Wer Schreibzugriff auf die Datenbank hat, kann Zeilen daher ändern oder löschen.
- **Datenbankrolle.** Eine PostgreSQL-Anwendungsrolle ohne DELETE-Recht auf `audit_event` ist Sache des Betriebs. Die Anwendung legt sie nicht an. Der zugehörige Nachweistest läuft nur mit `CLINIC_AUDIT_DB_PROOF=1` gegen eine nur dafür angelegte PostgreSQL-Testdatenbank. Datenbankeigentümer und Superuser behalten die volle Berechtigung.
- **Weitere klinische Nachweistabellen.** Beim Start werden unter SQLite und PostgreSQL Datenbank-Trigger installiert für das Archivbuch `clinic_archive_event` («clinic archive evidence is append-only»), für `clinic_output_receipt`, `clinic_marker_event`, `clinic_asr_profile_version`, `clinic_asr_training_run`, `clinic_voice_consent` und `clinic_voice_consent_audit` sowie für die unveränderlichen Originale und Transkriptrevisionen. Andere Datenbanksysteme erhalten keine Trigger.

Eine Kopie ausserhalb der Reichweite der Serveradministration schützt vor einer späteren Änderung von `audit_event`. Exportieren Sie das Protokoll regelmässig mit **JSONL exportieren** und legen Sie die Dateien auf einem solchen System ab.

## Anfragekennungen und Zuordnung

- Eine Zeile hat keine Anfragekennung. Zeilen einer Anfrage werden über `created_at`, `actor_user_id` und `recording_id` zugeordnet.
- Viele Fehlerantworten der klinischen Diktat-API (`application/problem+json`), zum Beispiel der Routen für Ablauf, Verlauf, Audio und Kontext, enthalten eine `correlation_id`, eine zufällige UUID, die für jede Antwort neu erzeugt wird. Die Routen für Statistik, Import und Paritätsbefehle antworten ohne sie. Fehlermeldungen klinischer Aufträge tragen eine eigene Kennung `<operation key>:<job id>`. Keine der beiden wird in `audit_event` gespeichert.
- Der `Idempotency-Key` klinischer Befehle und der interne `dedup_key` sind nicht Teil einer exportierten Zeile.
- `id` wird in Schreibreihenfolge vergeben und nie erneut vergeben. Weil Lesezugriffe und Anmeldungen bis zu einer Sekunde nach dem Ereignis geschrieben werden, können zwei Zeilen derselben Sekunde in beliebiger Reihenfolge erscheinen; der Zeitpunkt jeder Zeile ist genau.

## Die Seite System-Auditprotokoll

- **Tabelle.** Die neuesten Ereignisse zuerst, in Schreibreihenfolge. Ein Klick auf eine Zeile zeigt die Details, das betroffene Konto, den Eigentümer der Aufnahme und den User-Agent.
- **Aktualisieren.** **Aktualisieren** liest die Seite mit denselben Filtern neu. Vorher wartet der antwortende Serverprozess höchstens 0,25 Sekunden, bis seine wartenden Ereignisse geschrieben sind. Ist sein Schreiber länger beschäftigt, zeigt die Liste die bis dahin geschriebenen Zeilen.
- **Filter.** **Von**, **Bis**, **Benutzer** (Benutzername oder ID; findet Ereignisse dieses Kontos und Ereignisse, die es betreffen; ein unbekannter Name findet nichts), **Ereignisse** (eine Gruppe), **Aufnahme-ID**, **Ergebnis** und **Zugriff über**. Datumsangaben sind Tage in der Zeitzone des Browsers. **Filter zurücksetzen** leert die Filter.
- **Aufnahme-ID im Klinikmodus.** Der klinische Routenschutz prüft den Filter **Aufnahme-ID** (Parameter `recording_id`) wie einen Lesezugriff auf diese Aufnahme, in der Liste und beim Export. Der Filter wird zugelassen für die Ankeraufnahme eines klinischen Diktats, weil eine aktive globale Administration jedes Diktat lesen darf, und für Aufnahmen, die Sie als Eigentümer oder dank eines Fachrechts lesen dürfen. Abgelehnt wird er für eine gelöschte Aufnahme, eine Aufnahme ohne Eigentümer und eine Upstream-Aufnahme eines anderen Kontos ohne Fachrecht; die globale Administration hat dort keinen Sonderzugriff. Die Antwort lautet 404 `not_found` (403 `forbidden` bei einer abgelehnten Aufnahme, von der Sie wissen dürfen). Die Seite zeigt dann «Das Audit-Protokoll konnte nicht geladen werden.», und der Exportlink liefert die Fehlerantwort statt einer Datei. Eine Ablehnung durch die Zugriffsregeln wird als `clinic policy denied view <endpoint> on recording <id> …` protokolliert, eine nicht vorhandene Aufnahme nicht. Exportieren Sie in diesem Fall ohne **Aufnahme-ID**, grenzen Sie mit **Von**, **Bis**, **Benutzer** oder **Ereignisse** ein und filtern Sie die Datei nach `recording_id`.
- **Blättern.** **Älter** und **Neuer**, je 50 Zeilen.
- **Export.** **CSV exportieren** und **JSONL exportieren** laden die Zeilen des aktuellen Filters herunter, die ältesten zuerst, mit unveränderten Spalten und `Cache-Control: no-store`. In CSV erhält eine Textzelle, die mit `=`, `+`, `-`, `@`, einem Tabulator oder einem Wagenrücklauf beginnt, einen vorangestellten Apostroph. Ein Export ist eine Momentaufnahme: Der antwortende Prozess wartet zuerst höchstens 1 Sekunde, bis seine wartenden Ereignisse geschrieben sind, und die Datei endet bei der höchsten passenden ID zu Beginn des Exports.
- **Hinweis.** Bis die globale Administration ihn ausblendet, zeigt ein Hinweis, dass Ereignisse erfasst werden, ob Adressen gespeichert werden und wie lange Zeilen aufbewahrt werden. Er erscheint in jeder Installation, neu oder aktualisiert. Das Ausblenden speichert `audit_log_notice_dismissed`; das wird als `admin.setting.changed` mit altem und neuem Wert erfasst, auch bei ausgeschaltetem Protokoll. Der Hinweistext nennt noch «Freigaben» im Sinne von Links zum Teilen, die nicht zu dieser Version gehören. Die genannte Aufbewahrungsfrist wird im Klinikmodus nicht angewendet (siehe oben).

### Wiederverwendete IDs

Unter SQLite kann die ID einer gelöschten Aufnahme oder eines gelöschten Kontos neu vergeben werden. Im Klinikmodus ist das manuelle Löschen von Aufnahmen und Konten gesperrt, aber die Bereinigung von Aufnahmen ohne Eigentümer und der Abbruch eines Uploads löschen weiterhin Aufnahmen. Lesen Sie die Zeilen einer Aufnahme zusammen mit ihren Zeilen `recording.deleted`: Ihr Verlauf besteht aus den Zeilen nach der letzten Zeile `recording.deleted` mit dieser ID. Im Klinikmodus wird der Filter **Aufnahme-ID** für eine gelöschte ID abgelehnt (siehe oben). Finden Sie solche Zeilen mit dem Filter **Ereignisse** auf **Änderungen**, oder exportieren Sie ohne Aufnahmefilter und durchsuchen Sie die Datei nach der `recording_id`. Unter PostgreSQL werden IDs nicht erneut vergeben.

## Zugriff für ein externes System

Im Klinikmodus lässt sich das Protokoll nur in der Browsersitzung einer globalen Administration lesen und exportieren. Eine Anfrage, die mit einem API-Token angemeldet ist, wird mit 403 `clinic_feature_disabled` abgelehnt, und API-Token lassen sich nicht anlegen. Der im Upstream beschriebene geplante Abruf durch ein Überwachungssystem mit einem API-Token mit vollem Zugriff hat den Status Nur Upstream.

Die Parameter beider Routen sind `from` und `to` (ein Datum oder ein Datum mit Uhrzeit nach ISO 8601; `to` ist ausschliessend; ein Datum ohne Uhrzeit gilt als Mitternacht UTC), `user`, `group`, `event`, `recording_id`, `outcome`, `actor_kind`, `before_id` und `after_id`, für die Seitenroute zusätzlich `limit` (1 bis 500, Standard 50). Die Exportroute nimmt `format` (`csv` oder `jsonl`). Ein ungültiges Datum oder eine unbekannte Gruppe, ein unbekanntes Ereignis, Ergebnis oder eine unbekannte Akteursart wird mit 400 abgelehnt. Im Klinikmodus prüft der klinische Routenschutz `recording_id` wie unter [Die Seite System-Auditprotokoll](#die-seite-system-auditprotokoll) beschrieben.

## Störungen

Ereignisse von Lesezugriffen und Anmeldungen kommen in eine Warteschlange und werden von einem Hintergrund-Thread je Prozess etwa einmal pro Sekunde geschrieben, damit eine Anfrage nie auf das Protokoll wartet. Ereignisse von Änderungen und administrativen Aktionen werden in derselben Datenbanktransaktion wie die Aktion geschrieben: Eine solche Zeile besteht genau dann, wenn die Aktion wirksam wurde.

Jeder Arbeitsprozess hat eine eigene Warteschlange (bis zu 10 000 Ereignisse). Vor dem Lesen der Liste oder eines Exports wird nur die Warteschlange des antwortenden Prozesses geschrieben. Ein Ereignis in der Warteschlange eines anderen Prozesses kann deshalb bis etwa eine Sekunde später erscheinen.

Ein Stapel, der sich nicht schreiben lässt, wird nach 1, 5, 15 und 60 Sekunden erneut versucht. Danach wird jedes Ereignis des Stapels als eine Zeile `AUDIT-FALLBACK {json}` auf der Stufe ERROR im Logger `speakr.audit` ins Anwendungsprotokoll geschrieben, und der nächste geschriebene Stapel fügt eine Zeile `audit.write_failed` mit der Anzahl an. Ein Ereignis, das die Warteschlange voll vorfindet, wird gleich behandelt. Die Zeile enthält dieselben Werte wie die Tabellenzeile, auch die Client-Adresse, wenn Adressen gespeichert werden. Bewahren Sie diese Zeilen im Sammler des Containerprotokolls auf.

Wird der Serverprozess hart beendet, kann bis zu einer Sekunde an Lese- und Anmeldeereignissen fehlen. Ereignisse von Änderungen sind nicht betroffen.

## Aktualisierung

- **Das Protokoll ist nach der Aktualisierung eingeschaltet.** Ab dem ersten Start werden Ereignisse mit der Client-Adresse erfasst. Die Einstellungen oben ändern das.
- **Links zum Teilen.** Öffentliche Links zum Teilen und internes Teilen gehören nicht zu dieser Version. Die Tabelle `share_audit_log` früherer Versionen wird weder in `audit_event` kopiert noch abgebildet; eine vorhandene Tabelle bleibt unverändert bestehen. Zeilen, die eine frühere Version kopiert hat (mit `imported` in den Details), bleiben in `audit_event`.
- **Wachstum.** Im Klinikmodus werden keine Zeilen entfernt, und die klinischen Inhaltszugriffe werden bei jeder Anfrage erfasst. Die Tabelle wächst schneller als bei einer Upstream-Installation mit Entfernen alter Zeilen.

## Funktionen in Prüfung

Das Folgende ist nicht Teil der aktuell freigegebenen Version. Beschriftungen und Verfügbarkeit können sich ändern.

- Das Speichern der Modellwahl je Aufgabe (**Modellwahl**) schreibt `admin.setting.changed` mit dem Schlüssel `clinic_task_models`, ohne alte oder neue Modellkennungen.
- Das Lesen einer Diktatzusammenfassung schreibt `recording.content.read` mit `part` `summary`. Eine manuell angeforderte Zusammenfassung schreibt `recording.reprocessed` mit `kind` `summary`.

## Upstream-Dokumentation

Upstream-Speakr-Dokumentation zu diesem Thema (fixiertes Original): [Audit log (Upstream-Speakr-Dokumentation, Englisch)](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/audit-log.md). Ihre Abschnitte zu Links zum Teilen, Inkognito-Uploads, Registrierung, Zurücksetzen des Passworts und Abruf mit einem API-Token gelten für die Klinikversion nicht.
