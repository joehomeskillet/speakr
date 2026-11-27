# Systemeinstellungen

[English](../../admin-guide/system-settings.md)

Öffnen Sie als Standortadministration **Verwaltung → System → Systemeinstellungen** unter `/admin#/system/einstellungen`. Identitätsadministration und Poolleitung gewähren für sich allein keinen Zugang zu dieser Seite. Diktatrechte wie Freigeben und Originalaudioexport bleiben getrennt; siehe [Benutzerverwaltung](user-management.md).

## Einstellung ändern und prüfen

1. Halten Sie vor der Änderung aktuellen Wert und Geltungsbereich fest.
2. Öffnen Sie den Bearbeitungsdialog und speichern Sie eine Änderung.
3. Laden Sie die Liste erneut und prüfen Sie den gespeicherten Wert.
4. Testen Sie den betroffenen Vorgang mit synthetischen Daten. Prüfen Sie zur Untersuchung einer Änderung `admin.setting.changed` im [Auditprotokoll](audit-log.md).

Bei unterstützten Einstellungen überschreibt ein Datenbankwert den Rückfallwert aus der Umgebung. **Use .env value** entfernt diese Vorgabe; die Schaltfläche gibt es nur für umgebungsgebundene Einstellungen. Reine Umgebungskonfiguration wird über das Neustartverfahren des Deployments angewendet. Systemeinstellungen ersetzen keine Betriebskonfiguration.

Änderungen protokollieren den Einstellungsschlüssel; nur eine Positivliste von Einstellungen protokolliert alte und neue Werte. Änderungen an Auditeinstellungen werden auch bei ausgeschaltetem allgemeinem Auditschalter erfasst. Erwarten Sie im Auditprotokoll keine Geheimnisse oder vollständigen Einstellungsinhalte.

## Uploads, Speicher und Hinweise

| Einstellung | Standard | Wirkung |
|---|---|---|
| `max_file_size_mb` | `250` beim ersten Start | Grenze für Audiouploads |
| `max_audio_only_video_size_mb` | 4 × `max_file_size_mb` beim ersten Start | Grenze für Videouploads |
| `default_storage_quota_mb` | `0`, unbegrenzt | Standard für neu angelegte Konten; bestehende Konten behalten ihr eigenes Kontingent |
| `max_concurrent_uploads` | `3` | Parallele Uploads im übernommenen Uploaddialog; der Klinikrekorder sendet eine Datei pro Upload |
| `recording_disclaimer` | leer | Vor Aufnahmestart zu bestätigender Hinweis |
| `upload_disclaimer` | leer | Hinweis im übernommenen Uploaddialog; die Klinikaufnahmeseite nutzt einen eigenen Datei-Uploadablauf |
| `custom_banner` | leer | Schliessbares Banner auf der Hauptseite |
| `help_center_url`, `help_center_label` | leer | Optionaler Hilfelink im Kontomenü |

Die Anwendungsgrenze pro Anfrage ist der grössere Wert aus Audio- und Videogrenze. Das Speichern einer der Grenzen berechnet sie neu. Bei aktiviertem Chunking können die Übernahmeregeln die Dateigrenze umgehen; die Anfragegrenze gilt weiterhin. Ohne Chunking gilt die jeweilige Dateigrenze; extrahiertes Videoaudio kann zusätzlich als `extracted_audio_too_large` abgewiesen werden. Proxy-Grenzen und Speicherkontingente der Konten sind getrennt.

Der Klinikrekorder sendet eine ganze Aufnahme über `/upload`. Dimensionieren Sie den Proxy für diese Anfrage, nicht für Blockgrössen von Serversitzungen. Siehe [Aufnahmesitzungen](recording-sessions.md).

## Anmeldung, Sitzungen und Kontostandards

| Einstellung | Standard | Wirkung |
|---|---|---|
| `login_max_failed_attempts` | `5` | Fehlpasswortgrenze je Konto und Clientadresse; `0` deaktiviert die Sperre |
| `login_lockout_minutes` | `15` | Sperrdauer |
| `session_idle_timeout_minutes` | `0` | Inaktivitätsgrenze der Sitzung; `0` bedeutet keinen Inaktivitätstimeout |
| `default_timezone` | `UTC` oder der ältere `TIMEZONE`-Wert beim ersten Start | Rückfallzeitzone |
| `default_ui_theme` | `cards` | Oberflächenthema für neue Konten |
| `default_color_scheme` | `emerald` | Farbschema für neue Konten |
| `default_audio_player_position` | `top` | Playerposition für neue Konten |

Der Inaktivitätstimeout zählt Benutzeraktivität, nicht die ausgenommenen Hintergrundabfragen der Anwendung. Das Aufheben von Kontosperren und Änderungen bestehender Konten gehören zu **Benutzer und Rollen** mit dessen gesonderten Prüfungen der Identitätsadministration. Darstellungsstandards überschreiben keine bestehenden Konten. Siehe [Benutzerverwaltung](user-management.md) und [Anmeldung](sso-setup.md).

## Spracherkennung und Zusammenfassungen

Unter **System → Modellwahl** setzen Sie GPU- und CPU-ASR-Modellkennungen sowie das Textmodell für Diktatzusammenfassungen. Diese Seite gehört zu diesem Stand. Dienst-URLs und Zugangsdaten bleiben in der Betriebskonfiguration. Die interne Einstellung `clinic_task_models` ist aus der allgemeinen Liste ausgeblendet; Schreibzugriffe über den allgemeinen Einstellungsendpunkt werden mit `use_task_models_api` abgewiesen.

Der übernommene Bereich **Modellkatalog für die Aufnahme** erscheint weiterhin auf der Einstellungsseite. Seine Erkennungs- und Speicherrouten sind im Klinikmodus durch `cloud_provider_selection` gesperrt und können `403 clinic_feature_disabled` liefern. Eine im Browser ergänzte Zeile ist damit nicht automatisch gespeichert. Nutzen Sie **Modellwahl** für die Aufgabenzuordnung; siehe [Modellkonfiguration](model-configuration.md).

| Einstellung | Standard | Wirkung |
|---|---|---|
| `asr_timeout_seconds` | `1800` Sekunden | Nur Timeout von `asr_endpoint`; `ASR_TIMEOUT` und die ältere Umgebungsvariable `asr_timeout_seconds` haben Vorrang |
| `asr_async_jobs` | `true` | WhisperX-Jobs-API verwenden, wenn der Dienst sie meldet |
| `transcript_length_limit` | `30000` Zeichen | Transkriptgrenze für Textmodellaufrufe; `-1` hebt sie auf |
| `disable_auto_summarization` | `false` | Bei `true` automatische Zusammenfassungen deaktivieren |

Der OpenAI-kompatible CPU-Konnektor nutzt stattdessen `TRANSCRIPTION_TIMEOUT` und `TRANSCRIPTION_MAX_RETRIES`, das GPU-Ziel nutzt `CLINIC_GPU_ASR_TIMEOUT`. Siehe [Lokale Spracherkennung](whisperx-setup.md).

Automatische Klinikzusammenfassungen sind an eine Textrevision gebunden und werden nur für den ersten erkannten Text eines neuen Diktats eingereiht, wenn lokales Modell, Autorenrechte und Zusammenfassungspräferenzen dies erlauben. Der Systemschalter und die gespeicherte automatische Zusammenfassungspräferenz des Autors gelten gemeinsam. Manuelles Erstellen nutzt den zuletzt gespeicherten Text. Bestehende Diktate erhalten nicht allein durch das Vorhandensein der Funktion automatische Zusammenfassungen. [Modellkonfiguration](model-configuration.md) und [Prompts](prompts.md) erläutern den getrennten Titelpfad und Grenzen der Modelllokalität.

## Archivierung und Audit

Der Klinik-Fork bietet keine harte automatische Löschung. Die übernommenen Aufbewahrungsschlüssel steuern jetzt Archivierung und bewahren Aufnahmedateien und Daten:

| Einstellung | Standard | Klinikbedeutung |
|---|---|---|
| `enable_auto_deletion` | `false` | **Automatische Archivierung**: täglichen Archivierungslauf aktivieren |
| `global_retention_days` | `0` | **Archivieren nach Tagen**: Alter seit Erstellung; `0` nutzt nur Tagfristen |
| `deletion_mode` | `archive` | Dateien und Daten bewahren; auch die älteren Werte `audio_only` und `full_recording` archivieren |
| `audit_log_enabled` | `true` | Allgemeiner Auditschalter; verpflichtende Klinikereignisse haben zusätzliche Regeln |
| `audit_log_retention_days` | `365` | Aufbewahrungswert; Auditbereinigung bleibt im Klinikmodus ausgesetzt |
| `audit_log_store_ip` | `full` | `full`, `truncated` oder `off` |
| `audit_log_record_views` | `true` | Protokollierung von Ansichts- und Wiedergabeereignissen |

Die Archivierung im Klinikdiktat-Workflow ist vom übernommenen Aufbewahrungslauf für Aufnahmen getrennt. Ein archivierter Eintrag ist kein Backup; eine geänderte Auditaufbewahrung löscht keine Klinikauditzeilen. Siehe [Aufbewahrung](retention.md), [Auditprotokoll](audit-log.md) und [Speicher](storage.md).

## Videoeingaben

`video_passthrough_asr` / `VIDEO_PASSTHROUGH_ASR` ist standardmässig `false`. Bei Aktivierung sendet der Videouploadpfad das Originalvideo ohne gewöhnliche Extraktion, Konvertierung oder Chunking an das konfigurierte ASR-Backend. Reine Audiouploads sind nicht betroffen. Aktivieren Sie dies nur für einen freigegebenen lokalen Konnektor, der mit diesem Container getestet wurde; gewöhnliche reine Audio-Transkriptionsendpunkte weisen Video ab.

Davon getrennt ist `video_retention` / `VIDEO_RETENTION`, ebenfalls standardmässig aus. Es steuert das Bewahren des Videostreams für die Wiedergabe. Die Klinikaufnahmeseite bietet Audiodateien an, eine `.webm`-Datei kann dennoch Video enthalten.

## Klinikgrenzen und Betriebseinstellungen

Solange die zugehörigen Klinikfunktionen gesperrt sind, wirken `api_query_tokens_enabled` und `sso_api_tokens_enabled` immer als `false`. Aktivierungsversuche liefern `403 clinic_policy_ceiling`. Ein angezeigter gespeicherter Wert belegt keine Nutzbarkeit einer Funktion.

Der Klinik-Fork bietet keine öffentlichen oder internen Freigaben, Webhooks, allgemeine API v1, keinen Inkognitomodus, Cloud-ASR, Organisationsordner, Selbstregistrierung, E-Mail-Verifizierung, Passwort-Reset per Mail, Kalenderextraktion oder What's-New-Popup. API-Tokens sind dem Importagenten-Zweck vorbehalten. Übernommene Einstellungen stellen diese entfernten Funktionen nicht wieder her.

Der Betrieb verwaltet diese Werte ausserhalb der Einstellungsseite:

| Konfiguration | Zweck |
|---|---|
| `CLINIC_MODE`, `CLINIC_CONFIG_DIR` | Klinikrichtlinie und Vertragsverzeichnis |
| `TRANSCRIPTION_*`, `ASR_*`, `CLINIC_GPU_ASR_*` | Lokale Erkennungsendpunkte und Konnektoreinstellungen |
| `TEXT_MODEL_BASE_URL`, `TEXT_MODEL_NAME`, `TEXT_MODEL_API_KEY` | Lokaler Textdienst; nie nur einen Schlüssel ohne freigegebene lokale URL setzen |
| `CLINIC_ORIGINALS_ROOT` | Dauerhafter Originalspeicher; standardmässig `UPLOAD_FOLDER` |
| `CLINIC_DSS_CODEC_BIN` | Decoder für `.ds2` und `.dss` |
| `JOB_QUEUE_WORKERS`, `SUMMARY_QUEUE_WORKERS`, `CONVERT_QUEUE_WORKERS` | Workerzahlen, jeweils standardmässig `2` |
| `JOB_MAX_RETRIES` | Verarbeitungsversuche, standardmässig `3` |
| `SESSION_COOKIE_SECURE`, Trusted-Proxy-Konfiguration | HTTPS-Cookies und vertrauenswürdige Weiterleitungsheader |

Lassen Sie automatische Upstream-Watchfolder-Übernahme, automatische Exporte und Inquire-Indexierung ausgeschaltet. Ihr Vorhandensein im übernommenen Code ist kein freigegebener Klinikworkflow. Lokaler Dateiimport gehört zum Importagenten. Beachten Sie [Migration](migration-guide.md), bevor Sie Umgebungswerte einer Upstream-Installation übernehmen.
