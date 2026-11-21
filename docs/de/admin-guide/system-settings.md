# Systemeinstellungen
<small>[English](../../admin-guide/system-settings.md)</small>

Diese Seite beschreibt die globalen Einstellungen der Klinikversion: die Einstellungen auf der Seite **Systemeinstellungen** und die Umgebungsvariablen, die die ganze Instanz steuern. Sie behandelt nur, was bei eingeschaltetem Klinikmodus wirkt (`CLINIC_MODE` auf `1`, `true`, `yes` oder `on`). Einstellungen, die noch angezeigt werden, im Klinikmodus aber nichts bewirken, sind als **Nur Upstream** aufgeführt.

Statusbegriffe: siehe [Statusbegriffe](index.md#statusbegriffe).

## Wo die Seite liegt und wer sie verwenden darf

Öffnen Sie die Verwaltung über das Kontomenü (**Administration**) oder unter `/admin`. Wählen Sie in der Navigationsgruppe **System** den Eintrag **Systemeinstellungen**. Der direkte Link lautet `/admin#/system/einstellungen`. In derselben Gruppe liegen **Systemstatistik** (siehe [Systemstatistik](statistics.md)) und **System-Auditprotokoll** (siehe [Auditprotokoll](audit-log.md)).

Der Zugriff hängt von der Rolle ab:

- **Globale Administration**: Nur ein Konto mit der **Administrationsrolle** öffnet `/admin` und liest oder ändert Systemeinstellungen. Der Klinik-Routenschutz weist jedes andere Konto mit `403` ab, bevor die Seite geladen wird.
- **Lokale Identitätsadministration**: Das Merkmal **Identitätsadministration** bewirkt auf dieser Seite nichts. Es wird für Kontoänderungen benötigt, siehe [Benutzerverwaltung](user-management.md).
- **Poolleitungen**: Eine Poolleitung (**Leitung**) hat keinen Zugriff auf `/admin` und kann keine Systemeinstellungen ändern. Siehe [Gruppenverwaltung](group-management.md).
- **Freigabe und Export des Originalaudios**: Die Rechte **Freigeben** und **Originalaudio exportieren** sind Rechte an Diktaten. Sie sind von den Systemeinstellungen getrennt und werden je Konto erteilt, siehe [Benutzerverwaltung](user-management.md).

Im Klinikmodus werden die Upstream-Registerkarten **Benutzerverwaltung**, **Standard-Prompts**, **Gruppen** und **Vektorspeicher** nicht angezeigt. Klinikseiten wie **Vorgaben**, **Spalten** und **Kategorien** sind eigene Seiten der Verwaltung mit eigenen Regeln. Sie sind keine Systemeinstellungen, siehe [Weitere Verwaltungsseiten](index.md#weitere-verwaltungsseiten).

## Aufbau der Seite

Oben steht der Bereich **Transkriptionsmodelle**. Darunter wird jede gespeicherte Systemeinstellung als Karte angezeigt, sortiert nach ihrem Schlüssel. Der Kartentitel ist der Schlüssel selbst, zum Beispiel `transcript_length_limit`. Nur die drei Aufbewahrungseinstellungen tragen eine Bezeichnung: **Automatische Archivierung**, **Archivieren nach Tagen** und **Archivieren und alle Daten erhalten**. Jede Karte zeigt den Werttyp und entweder **In .env festgelegt** oder **Zuletzt aktualisiert** mit Datum. **Aktualisieren** lädt die Liste neu.

Das Bearbeitungssymbol öffnet den Dialog **Systemeinstellung bearbeiten**:

- **Änderungen speichern** speichert den neuen Wert.
- **.env-Wert verwenden** entfernt den gespeicherten Wert einer Einstellung mit `.env`-Rückfall, sodass wieder der Wert aus `.env` gilt. Die Schaltfläche erscheint nur bei diesen Einstellungen und nur, solange ein Wert gespeichert ist.
- Der Dialog prüft einige Werte vor dem Speichern: `transcript_length_limit` muss mindestens 1000 Zeichen betragen, oder **Keine Begrenzung (gesamtes Transkript verwenden)** speichert `-1`. `max_file_size_mb` muss zwischen 1 und 10000 liegen. `asr_timeout_seconds` wird in Minuten und Sekunden eingegeben und muss zwischen 60 und 36000 Sekunden liegen.
- Der Server prüft den Typ (`string`, `integer`, `boolean`, `float`), eine gültige IANA-Zeitzone für `default_timezone` sowie die zulässigen Werte von `default_ui_theme`, `default_color_scheme`, `default_audio_player_position`, `deletion_mode` und `permission_create_groups`.

Ein gespeicherter Wert wird bei der nächsten Verwendung aus der Datenbank gelesen, ohne Neustart. Umgebungsvariablen, zu denen es auf dieser Seite keine Einstellung gibt, wirken erst nach einem Neustart der Anwendung.

Diese Schlüssel erscheinen nicht in der Liste, weil andere Editoren sie verwalten oder weil Speakr sie selbst schreibt: `transcription_models_visible_json`, `transcription_default_model`, die Prompt-Schlüssel `admin_default_summary_prompt`, `admin_default_title_prompt`, `admin_default_initial_prompt`, `admin_default_hotwords` und `admin_default_contextual_speaker_prompt`, die Temperaturen `llm_temperature_*` sowie die Stimmschlüssel `voice_space_current`, `voice_space_legacy` und `voice_embedding_reference`. Im Klinikmodus wird die Registerkarte **Standard-Prompts** nicht angezeigt, diese Prompts haben in der Oberfläche also keinen Editor. Siehe [Standard-Prompts](prompts.md).

Einige Karten enthalten Werte, die Speakr selbst schreibt, zum Beispiel `embedding_identifier` und `changes_feed_since`. Sie sind keine Einstellungen für die Administration.

### Was protokolliert wird

Jede Änderung und jedes **.env-Wert verwenden** schreibt das Auditereignis `admin.setting.changed` in derselben Transaktion wie die Änderung. Das Ereignis speichert den Schlüssel, wenn die Anwendung ihn definiert, sonst `other`. Alter und neuer Wert werden nur für diese Schlüssel gespeichert: `audit_log_enabled`, `audit_log_retention_days`, `audit_log_store_ip`, `audit_log_record_views`, `audit_log_notice_dismissed`, `users_can_delete`, `enable_auto_deletion`, `global_retention_days`, `deletion_mode`, `recording_tombstone_days`, `webhook_delivery_retention_days`, `webhook_omit_titles`, `auto_export_delete_with_recording`, `video_retention`, `video_passthrough_asr`, `asr_async_jobs`, `max_concurrent_uploads`, `session_idle_timeout_minutes`, `api_query_tokens_enabled`, `sso_api_tokens_enabled`, `login_max_failed_attempts`, `login_lockout_minutes`, `max_file_size_mb`, `default_storage_quota_mb` und `transcript_length_limit`. Für alle anderen Schlüssel, zum Beispiel `asr_timeout_seconds` oder `custom_banner`, wird nur der Schlüssel gespeichert. Eine Änderung einer `audit_log_*`-Einstellung wird auch bei ausgeschaltetem Auditprotokoll erfasst. Ein Schreibversuch, den die unten beschriebene Klinikobergrenze abweist, wird nicht erfasst. Siehe [Auditprotokoll](audit-log.md).

## Klinikobergrenzen

Zwei Upstream-Einstellungen sind durch die Klinikrichtlinie begrenzt. Solange ihre Klinikfunktionen gesperrt sind, liest die Anwendung immer den Wert `false`:

| Einstellung | Sperrende Funktion | Wirksamer Wert |
|---|---|---|
| `api_query_tokens_enabled` | `token_query_parameters` | `false` |
| `sso_api_tokens_enabled` | `personal_api_tokens` | `false` |

Beide Funktionen stehen in der Klinik-Vertragsdatei `clinic-spec/config/dev.defaults.json` auf `false` ([Quelle im Klinik-Fork](https://github.com/joehomeskillet/speakr/blob/main/clinic-spec/config/dev.defaults.json)). Klinikfunktionen sind standardmässig verweigert und werden nicht in der Verwaltung geschaltet. Das Speichern eines anderen Werts als `false` oder eines leeren Werts wird mit `403` und dem Code `clinic_policy_ceiling` abgewiesen. Beim Start wird ein widersprechender gespeicherter Wert als Warnung `clinic policy overrides stored setting <key>=<value> (effective: False)` protokolliert.

Die Karte in der Liste zeigt den gespeicherten Wert oder den Wert aus `.env`, nicht den wirksamen Wert. `api_query_tokens_enabled` kann deshalb als aktiv angezeigt werden, obwohl die Einstellung wirksam `false` ist. Im Klinikmodus wird eine Anfrage, die sich mit einem API-Token statt mit einer Browsersitzung anmeldet, mit `403 clinic_feature_disabled` abgewiesen. Die Datenschutzaspekte beschreibt [Datenschutz und regulierte Daten](privacy.md).

## Wirksame Einstellungen nach Zweck

### Hochladen und Speicher

| Einstellung | Standard | Wirkung in der Klinikversion | Status |
|---|---|---|---|
| `max_file_size_mb` | `250` (beim ersten Start gesetzt) | Grössenlimit eines Audio-Uploads in MB. | Verfügbar |
| `max_audio_only_video_size_mb` | 4 × `max_file_size_mb` beim ersten Start | Grössenlimit eines Uploads mit Video-Dateiendung. | Verfügbar |
| `default_storage_quota_mb` | `0` (kein Limit) | **Speicherlimit (MB)** für Konten, die danach in **Benutzer und Rollen** angelegt werden. Bestehende Konten behalten ihr eigenes Limit. | Verfügbar |
| `max_concurrent_uploads` | `3` (`MAX_CONCURRENT_UPLOADS`) | Gleichzeitige Uploads des Standard-Upload-Dialogs. Die Klinikansicht **Neue Aufnahme** sendet eine Datei je Upload. | Verfügbar |

Die Klinikansicht **Neue Aufnahme** sendet Browseraufnahmen und ausgewählte Dateien an die Standard-Upload-Route `POST /upload`. Die Limits gelten deshalb auch für Diktat-Uploads. Es gelten diese Regeln:

- Die Anfrageobergrenze ist das höhere der beiden Limits. Sie wird neu berechnet, wenn einer der beiden Werte gespeichert wird, und gilt in jedem Fall.
- Bei eingeschalteter Aufteilung in Abschnitte (`ENABLE_CHUNKING`, Standard `true`, oder Aufteilung in der Anbindung) wird das typabhängige Limit nicht durchgesetzt; es gilt nur die Anfrageobergrenze.
- Ohne Aufteilung wird das typabhängige Limit durchgesetzt. Eine Datei mit Video-Dateiendung erhält zunächst das Videolimit. Findet die Prüfung danach keinen Videostream, gilt das Audiolimit.
- Ohne Aufteilung wird ein Video, von dem nur das Audio behalten wird, mit `413` (`extracted_audio_too_large`) abgewiesen, wenn sein extrahiertes Audio grösser als `max_file_size_mb` ist. Läuft die Umwandlung erst nach der Antwort auf den Upload, endet die Aufnahme stattdessen mit demselben Grund als fehlgeschlagen.
- Eine Browseraufnahme wird als `aufnahme.webm` gesendet. `.webm` gilt als Video-Dateiendung, bis die Prüfung die Datei gelesen hat.
- Diktatdateien `.ds2` und `.dss` werden so gespeichert, wie sie hochgeladen wurden, ohne Umwandlung und ohne Aufteilung. Der Transkriptionsauftrag dekodiert sie.
- Ein Upload über einem Limit wird mit `413` beantwortet.

### Hinweise für Benutzer

| Einstellung | Standard | Wirkung in der Klinikversion | Status |
|---|---|---|---|
| `recording_disclaimer` | leer (aus) | Markdown-Hinweis, der vor dem Start einer Aufnahme bestätigt werden muss. Die Klinikansicht **Neue Aufnahme** verwendet den Standard-Aufnahmestart, der Hinweis erscheint also auch dort. | Verfügbar |
| `upload_disclaimer` | leer (aus) | Markdown-Hinweis des Standard-Upload-Dialogs. Die Klinikansicht **Neue Aufnahme** lädt Dateien mit einem eigenen Upload hoch und zeigt ihn nicht an. | Verfügbar, in der Klinik-Aufnahmeansicht nicht angezeigt |
| `custom_banner` | leer (aus) | Markdown-Banner über dem Hauptinhalt der Klinikanwendung. Benutzer können es schliessen; nach dem Neuladen der Seite erscheint es wieder. | Verfügbar |
| `help_center_url`, `help_center_label` | leer | Link im Kontomenü, der in einem neuen Tab geöffnet wird. Ohne Bezeichnung wird **Hilfe-Center** angezeigt. | Verfügbar |

### Anmeldung und Sitzungen

| Einstellung | Standard | Wirkung in der Klinikversion | Status |
|---|---|---|---|
| `login_max_failed_attempts` | `5` | Falsche Passwörter für ein Konto von einer Clientadresse bis zur Anmeldesperre. `0` schaltet die Sperre ab. | Verfügbar |
| `login_lockout_minutes` | `15` | Dauer einer Sperre in Minuten. Ein Wert unter 1 wird als 15 gelesen. | Verfügbar |
| `session_idle_timeout_minutes` | `0` (`SESSION_IDLE_TIMEOUT_MINUTES`) | Minuten ohne Anfrage, nach denen eine Browsersitzung endet. `0`: kein Inaktivitätslimit. | Verfügbar |

Die Klinikanmeldung zählt falsche Passwörter auf zwei Arten:

- Je Konto und Clientadresse. Eine IPv6-Adresse zählt als ihr /64-Netz.
- Je Konto über alle Adressen zusammen, mit der vierfachen Schwelle.

Ein gesperrtes Konto erhält dieselbe Meldung wie bei einem falschen Passwort: **Anmeldung fehlgeschlagen.** Ein richtiges Passwort vor Erreichen des Limits setzt beide Zählungen zurück. Ein falscher Einmalcode bei Aktivierung oder Zurücksetzen zählt gegen das Konto, dem der Code gehört. Beim Beginn einer Sperre wird das Auditereignis `auth.login.locked` geschrieben.

Um eine Sperre zu beenden, öffnen Sie das Konto in **Benutzer und Rollen**, Abschnitt **Konten**, und aktivieren Sie das Kontrollkästchen **Anmeldesperre aufheben**; speichern Sie danach das Formular. Die Option erscheint nur, solange das Konto gesperrt ist. Wie jede Kontoänderung erfordert sie die **Administrationsrolle** und die **Identitätsadministration** und schreibt `admin.user.unlocked`. Zusätzlich nimmt `/login` (Anmeldung und Einlösen von Einmalcodes) von einer Clientadresse 10 Anfragen pro Minute an. Ohne `RATELIMIT_STORAGE_URI` (zum Beispiel eine `redis://`-Adresse) zählt jeder Arbeitsprozess für sich. `RATELIMIT_ENABLED=false` schaltet das Limit ab. Hinter einem Reverse Proxy stammt die Clientadresse aus den weitergeleiteten Headern. Setzen Sie `TRUSTED_PROXY_HOPS` und `TRUSTED_PROXIES` wie in [Reverse-proxy hops](../../getting-started/installation.md#reverse-proxy-hops) (English) beschrieben.

Das Inaktivitätslimit funktioniert so:

- Mit `session_idle_timeout_minutes` über `0` wird die Zeit der letzten Anfrage im signierten Sitzungscookie gespeichert. Die nächste Anfrage nach Ablauf des Limits gilt als abgemeldet.
- Anfragen, die eine geöffnete Seite selbst sendet (Benachrichtigungszähler, Status der Auftragswarteschlange, Erneuerung des CSRF-Tokens), zählen nicht als Aktivität.
- Die Schlüssel `identity.session_idle_minutes` (15) und `identity.session_absolute_hours` (8) in der Klinik-Vertragsdatei liest die Anwendung nicht. Nur `session_idle_timeout_minutes` setzt das Inaktivitätslimit.
- Die Klinikanmeldung setzt nie das Cookie für «Angemeldet bleiben». `REMEMBER_COOKIE_DAYS` wirkt deshalb nicht auf Klinikanmeldungen.

Für die Kontoverwaltung gelten eigene Regeln (Einmalcodes, erneute Anmeldung innerhalb von 5 Minuten vor Kontoänderungen). Sie sind in [Benutzerverwaltung](user-management.md) beschrieben.

### Spracherkennung

Die Spracherkennung läuft nur lokal. Die Anbindungen, die Reihenfolge GPU zuerst, CPU danach und die Modellvariablen beschreiben [Lokale Spracherkennung](whisperx-setup.md) und [Modellkonfiguration](model-configuration.md).

| Einstellung | Standard | Wirkung in der Klinikversion | Status |
|---|---|---|---|
| `asr_timeout_seconds` | `1800` | Zeitlimit nur der Anbindung `asr_endpoint` (`ASR_BASE_URL`, WhisperX oder whisper-asr-webservice). Reihenfolge: `ASR_TIMEOUT` in der Umgebung, dann eine Variable namens `asr_timeout_seconds`, dann diese Einstellung, dann 1800. Der Wert wird bei jeder Anfrage neu gelesen. | Verfügbar, nur `asr_endpoint` |
| `asr_async_jobs` | `true` (`ASR_ASYNC_JOBS`) | Die Anbindung `asr_endpoint` verwendet die asynchrone Auftrags-API des WhisperX-ASR-Dienstes (ab 0.5.0), wenn der Dienst sie in seiner `/health`-Antwort meldet. Aus: Jede Transkription verwendet die synchrone Anfrage `/asr`. | Verfügbar, nur `asr_endpoint` |

`asr_timeout_seconds` gilt nicht für den lokalen OpenAI-kompatiblen Weg (`TRANSCRIPTION_BASE_URL`, zum Beispiel Speaches). Dieser Weg verwendet `TRANSCRIPTION_TIMEOUT` (im Klinikmodus standardmässig 3600 s) und `TRANSCRIPTION_MAX_RETRIES` (im Klinikmodus standardmässig `0`). Ein GPU-Ziel verwendet `CLINIC_GPU_ASR_TIMEOUT` (Standard 300 s).

Der Bereich **Transkriptionsmodelle** lässt sich anzeigen, im Klinikmodus aber nicht verwenden. **Erkennen** und **Auswahl speichern** rufen Routen auf, die unter der Klinikfunktion `cloud_provider_selection` gesperrt sind. Sie antworten mit `403 clinic_feature_disabled`. **Modell-ID hinzufügen** fügt nur im Browser eine Zeile hinzu; das Speichern scheitert auf dieselbe Weise.

In Klinik-Stapelaufträgen wird jeder Initial-Prompt und jeder Hotword-Hinweis verworfen (Vorgaben aus Konto, Tag, Ordner und Administration), ausser `CLINIC_ASR_PROMPTS_ENABLED` ist eingeschaltet. Dann teilen sich Prompt und Hotwords eine Obergrenze von 55 Wörtern. Hotwords und Prompts beeinflussen den Decoder; sie sind kein akustisches Training. Siehe [Standard-Prompts](prompts.md).

### Textmodell und Zusammenfassungen

| Einstellung | Standard | Wirkung in der Klinikversion | Status |
|---|---|---|---|
| `transcript_length_limit` | `30000` | Höchstzahl an Transkriptzeichen, die für einen automatischen Titel oder eine Zusammenfassung an das Textmodell gehen. `-1`: kein Limit. | Verfügbar |
| `disable_auto_summarization` | `false` | `true`: keine automatische Zusammenfassung nach einer Transkription. | Verfügbar |
| `disable_auto_translation` | `true` | `false`: Benutzer können in ihren Einstellungen **Neue Aufnahmen automatisch übersetzen** einschalten. | Verfügbar |

In der freigegebenen Version ruft ein Klinik-Transkriptionsauftrag das Textmodell nicht selbst auf. Er stellt einen Folgeauftrag in die Zusammenfassungs-Warteschlange, für den Titel und, wenn automatische Zusammenfassungen gelten, für die Zusammenfassung. Automatische Zusammenfassungen gelten, wenn `disable_auto_summarization` nicht `true` ist, der eigene Schalter des Kontos nicht aus ist und ein Textmodell-Client besteht. Im Klinikmodus zeigt die Kontoseite den Schalter **Auto-Zusammenfassen** nicht an (die Registerkarte **Prompt-Optionen** ist ausgeblendet). Jedes Konto behält deshalb seinen gespeicherten Wert; der Standard ist eingeschaltet.

Offene Lücke: Der automatische Titel, die automatische Zusammenfassung und der automatische Übersetzungsauftrag haben keine eigene Lokalitätsprüfung. Ihre Routen für die manuelle Verwendung sind gesperrt, solange nicht alle Textmodell-URLs lokal sind. Der Folgeauftrag sendet das Transkript jedoch an das jeweils konfigurierte Textmodell. Der Titel wird erzeugt, sobald ein Textmodell-Client besteht und die Aufnahme keinen vom Benutzer gewählten Titel hat (eine in **Neue Aufnahme** ausgewählte Datei wird ohne Titel gesendet), auch wenn `disable_auto_summarization` auf `true` steht. Ein Textmodell-Client entsteht, sobald `TEXT_MODEL_BASE_URL` oder `TEXT_MODEL_API_KEY` gesetzt ist; ohne `TEXT_MODEL_BASE_URL` verwendet er eine gehostete Standardadresse. Halten Sie `TEXT_MODEL_BASE_URL` und jede weitere gesetzte Modell-URL auf lokalen Diensten, siehe [Modellkonfiguration](model-configuration.md). Ist das nicht möglich, lassen Sie `TEXT_MODEL_BASE_URL` und `TEXT_MODEL_API_KEY` beide leer, damit kein Textmodell-Client besteht und weder Titel noch Zusammenfassung erzeugt werden, und lassen Sie `disable_auto_translation` auf `true`.

### Aufbewahrung und Auditprotokoll

| Einstellung | Standard | Wirkung in der Klinikversion | Status |
|---|---|---|---|
| `enable_auto_deletion` (**Automatische Archivierung**) | `false` (`ENABLE_AUTO_DELETION`) | Schaltet den täglichen Archivierungslauf ein. Er archiviert und löscht nie. | Standardmässig aus |
| `global_retention_days` (**Archivieren nach Tagen**) | `0` (`GLOBAL_RETENTION_DAYS`) | Tage seit der Erstellung bis zur Archivierung. `0`: Es gelten nur Tag-Fristen. | Verfügbar |
| `deletion_mode` (**Archivieren und alle Daten erhalten**) | `archive` (`DELETION_MODE`) | Alter Schlüssel. `archive`, `audio_only` und `full_recording` archivieren alle und behalten jede Datei und jede Zeile. | Verfügbar |
| `recording_tombstone_days` | `90` (`RECORDING_TOMBSTONE_DAYS`) | Tage, während denen Löschmarken des Änderungsfeeds aufbewahrt werden. Unverändert gegenüber Upstream. | Verfügbar |
| `audit_log_enabled` | `true` (`AUDIT_LOG_ENABLED`) | Hauptschalter des Auditprotokolls. Erzwungene Klinikzeilen werden trotzdem geschrieben. Solange er aus ist, kann die Administration Transkript und Audio fremder Diktate nicht öffnen (`503 audit_unavailable`). | Verfügbar |
| `audit_log_retention_days` | `365` (`AUDIT_LOG_RETENTION_DAYS`) | Stichtag für das Bereinigen. Im Klinikmodus entfernt die tägliche Bereinigung nichts und protokolliert nur, wie viele Zeilen sie behalten hat. | Verfügbar; Bereinigung: Nur Upstream |
| `audit_log_store_ip` | `full` (`AUDIT_LOG_STORE_IP`) | `full`, `truncated` (IPv4 /24, IPv6 /48) oder `off`. | Verfügbar |
| `audit_log_record_views` | `true` (`AUDIT_LOG_RECORD_VIEWS`) | `false` verwirft Ansichts- und Abspielereignisse. Die Administration kann dann das Audio fremder Diktate nicht öffnen (`503 audit_unavailable`). | Verfügbar |
| `audit_log_notice_dismissed` | `false` | Ob der Hinweis auf der Seite des Auditprotokolls ausgeblendet wurde. Keine weitere Wirkung. | Verfügbar |

Die Aufnahme, an der ein Klinikdiktat hängt, wird als von der automatischen Archivierung ausgenommen angelegt. Der Aufbewahrungslauf archiviert deshalb nie ein Diktat. Einzelheiten stehen in [Aufbewahrung](retention.md), [Auditprotokoll](audit-log.md) und [Datenschutz und regulierte Daten](privacy.md).

### Vorgaben für neue Konten

| Einstellung | Standard | Wirkung | Status |
|---|---|---|---|
| `default_timezone` | `UTC` oder der Wert der alten Variable `TIMEZONE` beim ersten Start | Zeitzone für Datumsangaben in erzeugten Titeln für Benutzer, deren Browser keine Zeitzone gemeldet hat und die keine gewählt haben. | Verfügbar |
| `default_ui_theme` | `cards` (`DEFAULT_UI_THEME`) | Oberflächendesign, das bei danach angelegten Konten gespeichert wird. Zulässig: `default`, `classic`, `workspace`, `focus`, `desk`, `cards`. | Verfügbar |
| `default_color_scheme` | `emerald` (`DEFAULT_COLOR_SCHEME`) | Farbschema, das bei danach angelegten Konten gespeichert wird. Zulässig: `blue`, `emerald`, `purple`, `rose`, `amber`, `teal`. | Verfügbar |
| `default_audio_player_position` | `top` (`DEFAULT_AUDIO_PLAYER_POSITION`) | Position des Audioplayers, die bei danach angelegten Konten gespeichert wird: `top` oder `bottom`. | Verfügbar |

Die Klinik-Kontoanlage übernimmt diese drei Werte für jedes neue Konto. Bestehende Konten behalten ihre eigenen Werte.

## Video-Uploads und `VIDEO_PASSTHROUGH_ASR`

| Einstellung | Variable | Standard | Status |
|---|---|---|---|
| `video_retention` | `VIDEO_RETENTION` | `false` | Standardmässig aus |
| `video_passthrough_asr` | `VIDEO_PASSTHROUGH_ASR` | `false` | Standardmässig aus |

Die Klinikansicht **Neue Aufnahme** bietet nur Audiodateien an. Sie akzeptiert Audio-MIME-Typen und die Endungen `.wav`, `.mp3`, `.m4a`, `.aac`, `.ogg`, `.oga`, `.opus`, `.flac`, `.webm`, `.wma`, `.ds2` und `.dss`. Eine WebM-Datei kann trotzdem einen Videostream enthalten, und die Upload-Route hinter der Ansicht verarbeitet Video wie Upstream. Ein Video kann die Transkription deshalb nur als solche Datei oder über eine direkte Anfrage mit Browsersitzung an eine Standard-Upload-Route (`/upload`, `/share-target`, `/api/v1/recordings/upload`) erreichen.

Für eine solche Datei wirken die beiden Einstellungen wie in Upstream:

- Mit eingeschaltetem `VIDEO_RETENTION` wird der Videostream für die Wiedergabe behalten. Das Audio wird nur für die Transkription in eine temporäre Datei extrahiert.
- Mit eingeschaltetem `VIDEO_PASSTHROUGH_ASR` geht die originale Videodatei unverändert an den konfigurierten lokalen ASR-Endpunkt. Audioextraktion, Codec-Umwandlung und Aufteilung entfallen. Audio-Uploads sind nicht betroffen. Im Klinikmodus ist der Endpunkt die lokale Anbindung, mit der Reihenfolge GPU zuerst, CPU danach, wenn ein GPU-Ziel gesetzt ist.

Schalten Sie `VIDEO_PASSTHROUGH_ASR` nur ein, wenn Ihr lokaler ASR-Endpunkt Videodateien annimmt. Übliche ASR-Schnittstellen, etwa OpenAI-kompatible Transkriptionsendpunkte, weisen rohe Videodaten ab, und die Transkription dieser Datei scheitert dann mit dem Fehler des Endpunkts.

## Upstream-Einstellungen ohne Wirkung im Klinikmodus

Diese Karten erscheinen weiterhin in der Liste. Ihre Werte verändern die Klinikversion nicht.

| Einstellung | Grund | Status |
|---|---|---|
| `permission_create_groups` | Upstream-Gruppen sind gesperrt (Funktion `internal_sharing`). | Nur Upstream |
| `users_can_delete` (`USERS_CAN_DELETE`) | Das Löschen von Aufnahmen ist im Klinikmodus gesperrt (Funktion `clinical_user_hard_delete`). | Nur Upstream |
| `webhook_delivery_retention_days`, `webhook_omit_titles` | Benutzer-Webhooks sind gesperrt (Funktion `user_webhooks`), siehe [Webhooks](webhooks.md). | Nur Upstream |
| `sso_api_allowed_clients`, `sso_api_exchange_clients`, `sso_api_scopes`, `sso_api_client_scopes`, `sso_api_client_names` | Die Upstream-SSO-Anmeldung lehnt im Klinikmodus ab, und API-Token sind gesperrt, siehe [Anmeldung und Identitätsanbieter](sso-setup.md). | Nur Upstream |
| `auto_export_delete_with_recording` | Gehört zum automatischen Upstream-Export (`ENABLE_AUTO_EXPORT`), und Aufnahmen lassen sich im Klinikmodus nicht löschen. | Nur Upstream |

`enable_folders` (Standard `false`) gehört zu den Upstream-Ordnern, sein Wert wirkt aber weiterhin. Lassen Sie den Wert auf `false`. Mit `true` zeigt die Standard-Kontoseite **Konto** unter **Tags & Ordner** die Unterregisterkarte **Ordner**, und die Ordner-Routen nehmen Änderungen an. Benutzer können dann Ordner mit eigenen Prompts und ASR-Vorgaben anlegen. Die Klinikansichten bieten keine Ordner an. Status: Nur Upstream, ausgeschaltet lassen.

Die Upstream-Abschnitte zum Inkognito-Modus, zu den Neuigkeitshinweisen nach Updates, zu öffentlichen Seiten zum Teilen und zur Benutzersuche beim Teilen haben in dieser Version keine Entsprechung.

## Einstellungen mit Rückfall auf .env

Die folgenden Einstellungen lassen sich auf dieser Seite oder in `.env` setzen. Bis auf dieser Seite ein Wert gespeichert wird, gilt der Wert aus `.env`, und die Karte zeigt **In .env festgelegt**. Ein gespeicherter Wert wirkt ohne Neustart, und spätere Änderungen der Variable werden ignoriert. **.env-Wert verwenden** kehrt zur Variable zurück. In `.env` schaltet nur `true` (beliebige Gross- und Kleinschreibung) diese Wahrheitswerte ein.

| Einstellung | Variable | Standard | Status |
|---|---|---|---|
| `api_query_tokens_enabled` | `API_QUERY_TOKENS_ENABLED` | `true` | Durch die Klinikobergrenze fest auf `false` |
| `asr_async_jobs` | `ASR_ASYNC_JOBS` | `true` | Verfügbar, nur `asr_endpoint` |
| `audit_log_enabled` | `AUDIT_LOG_ENABLED` | `true` | Verfügbar |
| `audit_log_record_views` | `AUDIT_LOG_RECORD_VIEWS` | `true` | Verfügbar |
| `audit_log_retention_days` | `AUDIT_LOG_RETENTION_DAYS` | `365` | Verfügbar; Bereinigung: Nur Upstream |
| `audit_log_store_ip` | `AUDIT_LOG_STORE_IP` | `full` | Verfügbar |
| `auto_export_delete_with_recording` | `AUTO_EXPORT_DELETE_WITH_RECORDING` | `false` | Nur Upstream |
| `default_audio_player_position` | `DEFAULT_AUDIO_PLAYER_POSITION` | `top` | Verfügbar |
| `default_color_scheme` | `DEFAULT_COLOR_SCHEME` | `emerald` | Verfügbar |
| `default_ui_theme` | `DEFAULT_UI_THEME` | `cards` | Verfügbar |
| `deletion_mode` | `DELETION_MODE` | `archive` | Verfügbar, nur Archivierung |
| `enable_auto_deletion` | `ENABLE_AUTO_DELETION` | `false` | Standardmässig aus |
| `global_retention_days` | `GLOBAL_RETENTION_DAYS` | `0` | Verfügbar |
| `help_center_label` | `HELP_CENTER_LABEL` | leer | Verfügbar |
| `help_center_url` | `HELP_CENTER_URL` | leer | Verfügbar |
| `max_concurrent_uploads` | `MAX_CONCURRENT_UPLOADS` | `3` | Verfügbar |
| `recording_tombstone_days` | `RECORDING_TOMBSTONE_DAYS` | `90` | Verfügbar |
| `session_idle_timeout_minutes` | `SESSION_IDLE_TIMEOUT_MINUTES` | `0` | Verfügbar |
| `sso_api_allowed_clients`, `sso_api_client_names`, `sso_api_client_scopes`, `sso_api_exchange_clients`, `sso_api_scopes` | `SSO_API_ALLOWED_CLIENTS`, `SSO_API_CLIENT_NAMES`, `SSO_API_CLIENT_SCOPES`, `SSO_API_EXCHANGE_CLIENTS`, `SSO_API_SCOPES` | leer; `read` für die Bereiche | Nur Upstream |
| `sso_api_tokens_enabled` | `SSO_API_TOKENS_ENABLED` | `false` | Durch die Klinikobergrenze fest auf `false` |
| `users_can_delete` | `USERS_CAN_DELETE` | `true` | Nur Upstream |
| `video_passthrough_asr` | `VIDEO_PASSTHROUGH_ASR` | `false` | Standardmässig aus |
| `video_retention` | `VIDEO_RETENTION` | `false` | Standardmässig aus |
| `webhook_delivery_retention_days` | `WEBHOOK_DELIVERY_RETENTION_DAYS` | `30` | Nur Upstream |
| `webhook_omit_titles` | `WEBHOOK_OMIT_TITLES` | `false` | Nur Upstream |

## Umgebungsvariablen

Diese Variablen werden in der Umgebung der Anwendung gesetzt und erscheinen nicht auf der Seite. Starten Sie die Anwendung nach einer Änderung neu.

### Klinikmodus

| Variable | Standard | Wirkung | Status |
|---|---|---|---|
| `CLINIC_MODE` | nicht gesetzt (aus) | `1`, `true`, `yes` oder `on` schaltet den Klinikmodus ein: Routenschutz, Funktionssperren, Einstellungsobergrenzen und Klinikverwaltung. Wird bei jedem Aufruf gelesen. | Verfügbar |
| `CLINIC_CONFIG_DIR` | `clinic-spec/config` im Repository-Stammverzeichnis | Verzeichnis der Vertragsdateien `dev.defaults.json`, `client-capabilities.json` und `import-agent-boundary.json`; die Webanwendung liest nur die ersten beiden. Eine fehlende oder unlesbare Datei wird beim Lesen der Richtlinie als Fehler gemeldet; der Klinikmodus fällt nie auf eine offene oder unbekannte Richtlinie zurück. Eine Funktion ist nur verwendbar, wenn `dev.defaults.json` sie unter `features` auf `true` setzt. Ausgenommen sind `chat`, `inquire` und `generative_summary`: Sie werden stattdessen nur mit einem lokalen Textmodell entsperrt, siehe [Modellkonfiguration](model-configuration.md). | Verfügbar |
| `CLINIC_WEB_ONLY` | `0` | `1`: ein Prozess, der nur HTTP bedient, für eine bereits initialisierte Klinikinstallation. Er startet keine Warteschlange und keine Hintergrundarbeit und setzt den Klinikmodus voraus. Jeder andere Wert als `0` oder `1` wird abgewiesen. | Verfügbar |
| `CLINIC_PROFILE` | nicht gesetzt | `staging` verwendet den synthetischen KIS-Adapter, damit die endgültige Freigabe mit synthetischen Falldaten getestet werden kann. Jeder andere Wert lässt den KIS-Adapter deaktiviert, und die endgültige Freigabe bleibt gesperrt. `production`: Die synthetische Anbindung `clinic_stub` und der KIS-Mock verweigern den Betrieb. | Simuliert (`staging`) |

### Spracherkennung und Textmodell

Gesetzt und beschrieben in [Lokale Spracherkennung](whisperx-setup.md) und [Modellkonfiguration](model-configuration.md): `TRANSCRIPTION_CONNECTOR`, `TRANSCRIPTION_BASE_URL`, `TRANSCRIPTION_MODEL`, `TRANSCRIPTION_TIMEOUT` und `TRANSCRIPTION_MAX_RETRIES`, die Variablen `CLINIC_GPU_ASR_*`, `CLINIC_ASR_PROMPTS_ENABLED`, `ASR_BASE_URL` und `ASR_TIMEOUT`, `TEXT_MODEL_BASE_URL`, `TEXT_MODEL_NAME`, `CLINIC_LOCAL_MODEL_HOSTS`, `CLINIC_LLM_TIMEOUT_SECONDS` (Standard 120) und `CLINIC_LLM_MAX_RETRIES` (Standard 2).

### Dateien, Diktatformate und Import

| Variable | Standard | Wirkung | Status |
|---|---|---|---|
| `CLINIC_ORIGINALS_ROOT` | `UPLOAD_FOLDER` | Lokaler Stamm der unveränderten Diktat-Originale, auch mit `FILE_STORAGE_BACKEND=s3`. Siehe [Dateispeicher](storage.md). | Verfügbar |
| `CLINIC_DSS_CODEC_BIN` | `/usr/local/bin/dss-decode` im Docker-Image | Pfad der fest versionierten Binärdatei `dss-decode`. Ohne sie wird eine hochgeladene `.ds2`- oder `.dss`-Datei gespeichert, ihre Aufnahme endet aber als fehlgeschlagen. | Verfügbar |
| `CLINIC_IMPORT_ROOT_ALLOWLIST` | leer | Absolute Stammverzeichnisse, getrennt durch `;`, innerhalb derer eine freigegebene Importquelle liegen muss. Leer: Jedes Quellverzeichnis wird abgewiesen. Siehe [Geräte- und Ordnerimport](recording-sessions.md#gerate-und-ordnerimport-verwaltungsseite-import). | Verfügbar |
| `CLINIC_AGENT_PROXY_SECRET`, `CLINIC_AGENT_MASTER_SECRET` | nicht gesetzt | Maschinengeheimnisse des Windows-Importagenten, je mindestens 32 Bytes. Fehlt eines oder ist es kürzer als 32 Bytes, wird eine Warnung protokolliert, und jede Anfrage des Agenten wird mit `401` beantwortet. Der Kanal selbst ist Vorbereitet, deaktiviert: Keine unterstützte Route, kein Befehl und kein Skript registriert einen Agenten. Vertrag für den Eingang: [Aufnahmesitzungen](recording-sessions.md#kanal-des-importagenten-und-eingang). | Vorbereitet, deaktiviert |

### Konten

`CLINIC_PASSWORD_BLOCKLIST_FILE` (Passwort-Sperrliste des Standorts) und `CLINIC_BOOTSTRAP_PASSWORD` (Passwort der ersten Administration) sind in [Benutzerverwaltung](user-management.md) beschrieben.

### Hintergrundverarbeitung

| Variable | Standard | Wirkung |
|---|---|---|
| `JOB_QUEUE_WORKERS` | `2` | Threads für Transkriptionsaufträge. |
| `SUMMARY_QUEUE_WORKERS` | `2` | Threads der Zusammenfassungs-Warteschlange; sie führt auch die Klinik-Folgeaufträge für Titel und Zusammenfassung aus. |
| `CONVERT_QUEUE_WORKERS` | `2` | Threads, die Uploads umwandeln, nachdem der Upload beantwortet ist. `0` wandelt während der Upload-Anfrage um. |
| `INDEX_QUEUE_WORKERS` | `1` | Threads für die Inquire-Suchabschnitte (Upstream; nur mit `ENABLE_INQUIRE_MODE`). |
| `JOB_MAX_RETRIES` | `3` | Höchstzahl der Versuche je Auftrag bei vorübergehenden Fehlern (Standard 3, also höchstens drei Versuche). Ein dauerhafter Fehler lässt den Auftrag sofort fehlschlagen. |

Diese Threads laufen nur in dem einen Prozess, dem die Auftragswarteschlange gehört. Die anderen Prozesse beantworten Anfragen und stellen Aufträge ein. Im Klinikmodus versucht ein Prozess, der die Wahl verloren hat, es alle 15 Sekunden erneut. Ein anderer Prozess übernimmt die Warteschlange deshalb spätestens 15 Sekunden plus die Auftragsreservierung (Lease) von 120 Sekunden, nachdem der bisherige Besitzer beendet ist.

Mit `CONVERT_QUEUE_WORKERS=0` durchläuft ein Web-Upload den Umwandlungsschritt (Codec-Umwandlung und, mit `AUDIO_COMPRESS_UPLOADS`, Komprimierung verlustfreier Audiodateien) während der Anfrage, bevor sein Klinik-Original gespeichert wird. Das Klinik-Original enthält dann die Datei im Zustand nach diesem Schritt. Mit dem Standardwert gelangt eine lesbare Audiodatei so ins Klinik-Original, wie sie hochgeladen wurde, und die Umwandlung folgt danach. `.ds2`- und `.dss`-Dateien werden nie umgewandelt.

### Upload-Verarbeitung und Cookies

| Variable | Standard | Wirkung |
|---|---|---|
| `ENABLE_CHUNKING` | `true` | Serverseitige Aufteilung in Abschnitte für den ASR-Aufruf; hebt auch das typabhängige Upload-Limit auf. |
| `AUDIO_COMPRESS_UPLOADS` | `true` | Komprimiert verlustfreie Uploads (WAV, AIFF) im Umwandlungsschritt. |
| `AUDIO_CODEC` | `mp3` | Ziel-Codec: `mp3`, `flac` oder `opus`. |
| `AUDIO_BITRATE` | `128k` | Bitrate für verlustbehaftete Codecs. |
| `AUDIO_UNSUPPORTED_CODECS` | leer | Durch Kommas getrennte Codec-Namen (zum Beispiel `opus,vorbis`), die aus der Liste der Codecs entfernt werden, welche die Transkriptionsanbindung unverändert annimmt; Dateien in einem solchen Codec werden im Umwandlungsschritt umgewandelt. |
| `FFMPEG_TIMEOUT_SECONDS` | `3600` | Obergrenze in Sekunden für jeden FFmpeg-Unterprozess zum Umwandeln und Aufteilen; Werte unter `30` gelten als `30`. Erhöhen Sie den Wert für sehr lange Diktate auf langsamer Hardware. |
| `FFPROBE_TIMEOUT_SECONDS` | `60` | Obergrenze in Sekunden für jeden FFprobe-Aufruf, der Dauer und Codec liest; Werte unter `10` gelten als `10`. |
| `SESSION_COOKIE_SECURE` | `false` | `true`: Das Sitzungscookie wird nur über HTTPS gesendet. Setzen Sie den Wert für jede Installation hinter TLS. Mit `true` scheitert die Anmeldung über unverschlüsseltes HTTP, weil der Browser das Cookie verwirft. Wird Speakr über HTTPS erreicht, während der Wert `false` ist, wird eine Warnung protokolliert, ebenso beim Start, wenn `APP_BASE_URL` eine https-Adresse ist und der Wert `false` ist. |

### Upstream-Funktionen ohne Klinikprüfung

| Variable | Standard | Hinweis | Status |
|---|---|---|---|
| `ENABLE_AUTO_EXPORT` | `false` | Automatischer Upstream-Export jeder abgeschlossenen Aufnahme in einen Ordner. Dieser Weg hat keine Klinikprüfung. Die Klinikausgabe läuft über die freigabegebundene Ansicht **Ausgabe**. | Nur Upstream |
| `ENABLE_AUTO_PROCESSING` | `false` | Upstream-Überwachungsordner. Mit `true` übernimmt ein Hintergrunddienst jede Datei, die im Überwachungsordner abgelegt wird, über den gemeinsamen Upload-Weg, ohne Upload-Grenzen und ohne Klinikprüfung, und löscht die Datei danach aus dem Ordner. Das ist ein zweiter lokaler Importweg ausserhalb der Grenze des Importagenten. Gesperrt sind nur die Administrationsrouten zum Starten, Stoppen, Auslösen und Konfigurieren (Funktion `auto_process`); die Statusroute bleibt für die globale Administration lesbar. | Nur Upstream; nicht setzen |
| `AUTO_PROCESS_WATCH_DIR` | `/data/auto-process` | Überwachungsordner von `ENABLE_AUTO_PROCESSING`. | Nur Upstream; nicht setzen |
| `AUTO_PROCESS_MODE` | `admin_only` | `admin_only` (Dateien gehören dem ersten Konto mit der **Administrationsrolle**), `user_directories` oder `single_user`. | Nur Upstream; nicht setzen |
| `AUTO_PROCESS_DEFAULT_USERNAME` | nicht gesetzt | Eigentümerkonto für `single_user`. | Nur Upstream; nicht setzen |
| `AUTO_PROCESS_CHECK_INTERVAL` | `30` | Sekunden zwischen zwei Durchsuchungen des Überwachungsordners. | Nur Upstream; nicht setzen |
| `AUTO_PROCESS_STABILITY_TIME` | `5` | Sekunden, während derer die Dateigrösse unverändert bleiben muss, bevor die Datei übernommen wird. | Nur Upstream; nicht setzen |
| `ENABLE_INQUIRE_MODE` | `false` | Upstream-Inquire. Im Klinikmodus ohne Oberfläche, mit `true` wird aber jede abgeschlossene Aufnahme zur Indexierung eingestellt. Die Klinikvorlage `clinic-spec/config/upstream.env.example` setzt `false`. Siehe [Vektorspeicher](vector-store.md). | Nur Upstream |
| `DELETE_ORPHANED_SPEAKERS` | `false` | Gehört zu den Upstream-Sprecherprofilen, die im Klinikmodus gesperrt sind (Funktion `speaker_identification`). | Nur Upstream |
| `SKIP_EMAIL_DOMAIN_CHECK` | `false` | Wird vom Upstream-Administrationsskript `scripts/create_admin.py` gelesen. Die erste Administration wird mit `scripts/clinic_bootstrap_admin.py` angelegt, siehe [Benutzerverwaltung](user-management.md). | Nur Upstream |

### Beispiel

```bash
# Klinikmodus
CLINIC_MODE=1

# Installation mit HTTPS
SESSION_COOKIE_SECURE=true

# Hintergrundverarbeitung (Standardwerte)
JOB_QUEUE_WORKERS=2
SUMMARY_QUEUE_WORKERS=2
CONVERT_QUEUE_WORKERS=2
JOB_MAX_RETRIES=3

# Video-Durchleitung (nur für einen lokalen ASR-Endpunkt, der Video annimmt)
# VIDEO_PASSTHROUGH_ASR=false
```

## In Prüfung: Modellwahl und automatische Diktatzusammenfassungen

!!! note "In Prüfung"
    Das Folgende ist nicht Teil der aktuell freigegebenen Version. Bezeichnungen und Verfügbarkeit können sich ändern.

    - Eine eigene Seite **Modellwahl** in der Gruppe **System** unter `/admin#/system/modelle`, zwischen **Systemeinstellungen** und **System-Auditprotokoll**. Nur aktive Konten mit der **Administrationsrolle** können sie verwenden. Beschrieben ist sie in [Modellkonfiguration](model-configuration.md).
    - Im Klinikmodus heisst der Bereich **Transkriptionsmodelle** dort **Modellkatalog für die Aufnahme**. Seine Beschreibung sagt, dass der Katalogstandard für die CPU-Transkription gilt, solange in der **Modellwahl** keine eigene CPU-Kennung festgelegt ist.
    - Die Auswahl wird in der Einstellung `clinic_task_models` gespeichert. Dieser Schlüssel ist in der Liste ausgeblendet, und ein Schreibversuch über diese Seite wird mit `400 use_task_models_api` abgewiesen. Eine Änderung schreibt `admin.setting.changed` nur mit dem Schlüssel.
    - Eine automatische Diktatzusammenfassung wird nur für den ersten erkannten Text eines neuen Diktats eingestellt, nur mit einem lokalen Textmodell und nur, wenn `disable_auto_summarization` nicht `true` ist und der eigene Schalter der diktierenden Person nicht aus ist. Bestehende Diktate erhalten keine automatische Zusammenfassung.

## Fehlerbehebung

- **Upload mit 413 abgewiesen**: Die Datei liegt über der Anfrageobergrenze oder, ohne Aufteilung, über `max_file_size_mb` oder `max_audio_only_video_size_mb`, oder das extrahierte Audio eines Videos liegt über `max_file_size_mb` (`extracted_audio_too_large`). Die Upload-Prüfung protokolliert eine Zeile, die mit `Upload failed: File too large` beginnt; eine Anfrage über der Anfrageobergrenze kann schon vorher ohne diese Zeile abgewiesen werden.
- **Speichern mit 403 `clinic_policy_ceiling` abgewiesen**: Der Schlüssel ist durch die Klinikrichtlinie begrenzt, siehe [Klinikobergrenzen](#klinikobergrenzen).
- **Erkennen oder Auswahl speichern antwortet mit 403 `clinic_feature_disabled`**: Der Bereich Transkriptionsmodelle ist im Klinikmodus gesperrt.
- **Eine lange WhisperX-Transkription überschreitet das Zeitlimit**: Erhöhen Sie `asr_timeout_seconds`, oder prüfen Sie, ob `ASR_TIMEOUT` gesetzt ist, denn diese Variable hat Vorrang. Für Speaches oder einen anderen OpenAI-kompatiblen Dienst ändern Sie stattdessen `TRANSCRIPTION_TIMEOUT`.
- **Ein Wert in `.env` wirkt nicht**: Ein auf dieser Seite gespeicherter Wert hat Vorrang. Verwenden Sie **.env-Wert verwenden**.

---

Upstream-Speakr-Dokumentation (fixiertes Original, nicht die Klinikversion): [System Settings](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/system-settings.md).
