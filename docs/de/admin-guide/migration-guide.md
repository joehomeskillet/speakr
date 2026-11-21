# Migrationsanleitung
<small>[English](../../admin-guide/migration-guide.md)</small>

Diese Seite beschreibt, wie Sie eine bestehende Upstream-Speakr-Installation und ihre Datenbank auf die Klinikversion umstellen, wie Sie von einer Klinikversion auf die nächste aktualisieren und welche Upstream-Daten und -Funktionen nicht übernommen werden. Sie enthält ausserdem die übernommene Referenz zu älteren Transkriptionseinstellungen und zu den Upstream-Skripten für die S3-Migration, angepasst an die Klinikversion.

Die Klinikversion ist ein reiner Web-Fork von Speakr v0.11.1 (fixierte Upstream-Basis `48d3018`). Die freigegebene Version ist der Branch `main` des Klinik-Forks. Die Entwicklung findet auf dem Branch `dev` statt; Beiträge gehen an `dev`.

Die Klinikregeln dieser Seite gelten nur, wenn `CLINIC_MODE` auf `1`, `true`, `yes` oder `on` steht. Ohne Klinikmodus verhält sich die Anwendung wie Upstream-Speakr. Die Vertragsdatei, die der Klinikmodus lädt (`clinic-spec/config/dev.defaults.json`, Profil `local-web-parity-lab`, `synthetic_only: true`), enthält Entwicklungsvorgaben und keine klinische Freigabe. Keine Anleitung auf dieser Seite macht eine Installation klinisch freigegeben oder produktionsreif.

Upstream-Speakr-Dokumentation (fixiertes Original; behandelt die Migration der Anbindungen von Speakr v0.8 und das Verschieben von Audiodateien nach S3): [Migration Guide](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/migration-guide.md).

## Statusübersicht

| Funktion | Status | Hinweise |
|---|---|---|
| Anlegen und Migrieren des Schemas bei jedem Start der Anwendung | Verfügbar | Es gibt keinen eigenen Migrationsbefehl. |
| Kliniktabellen, Integritätstrigger und die Gültigkeitsspalten der Pools | Verfügbar | Angelegt vom Migrationsabschnitt «clinic integrity». |
| Erste Administration (**Administrationsrolle** und **Identitätsadministration**) mit `scripts/clinic_bootstrap_admin.py` | Verfügbar | Läuft nur einmal. |
| Übernahme bestehender lokaler Konten | Verfügbar | Nur als API-Aufruf; die Seite hat dafür kein Bedienelement. |
| Übernahme bestehender Aufnahmen mit `scripts/clinic_backfill_dictations.py` | Verfügbar | Die Eigentümerin oder der Eigentümer braucht einen aktiven Autorenpool. |
| Schreibgeschützte Momentaufnahme von Konten, Pools und Diktaten mit `scripts/clinic_roles_report.py` | Verfügbar | Gibt IDs und Zustände aus, keine Namen und keinen Text. |
| Upstream-Administration aus `ADMIN_USERNAME`, `ADMIN_EMAIL` und `ADMIN_PASSWORD` | Verfügbar (Upstream-Entrypoint); nicht verwenden | Der Entrypoint führt `scripts/docker_create_admin.py` aus. Das Skript legt ein Konto mit **Administrationsrolle**, aber ohne Klinik-Kontorichtlinie an. Es kann sich erst nach der Übernahme anmelden (Schritt 6). `scripts/create_admin.py` verhält sich gleich. |
| Konsolenbefehle `flask clinic-identity bootstrap` und `flask clinic-identity recovery` | Vorbereitet, deaktiviert | Im Code definiert, beim Start aber nicht registriert. |
| Veröffentlichtes Container-Image der Klinikversion | Offene Lücke | Bauen Sie das Image aus dem Quellcode. |
| Werkzeug zum Übertragen der Daten von SQLite nach PostgreSQL | Offene Lücke | Nicht im Repository vorhanden. |
| Rückkehr auf eine frühere Klinikversion | Offene Lücke | Kein Befehl für eine Rückmigration. Stellen Sie stattdessen die Sicherung wieder her. |
| Upstream-Skripte für die S3-Migration von Basismedien | Standardmässig aus | Die S3-Ablage ist nicht der klinische Speicherentscheid (D06). |
| Seite **Modellwahl** und automatische Diktatzusammenfassungen | In Prüfung | Nicht Teil der aktuell freigegebenen Version. |
| Öffentliche Links zum Teilen und internes Teilen (Upstream), Selbstregistrierung, Inkognito-Modus, Ereignisextraktion, Cloud-Anbindungen für die Transkription | Nur Upstream | Aus dem Fork entfernt. |

## Vor dem Start

- **Es gibt kein veröffentlichtes Klinik-Image.** Die Veröffentlichungsautomatik des Repositorys (`.github/workflows/docker-publish.yml`) und `config/docker-compose.example.yml` nennen das Upstream-Image `learnedmachine/speakr` (Upstream-Speakr). `docker compose pull` mit dieser Datei lädt Upstream-Speakr, nicht die Klinikversion. Bauen Sie das Klinik-Image aus dem Branch `main` (Schritt 2).
- **Keine Umgebungsvorlage ist vollständig.** Keine Beispieldatei im Repository setzt `CLINIC_MODE`. `clinic-spec/config/upstream.env.example` beginnt mit der Zeile «Existing upstream flags; NOT a complete deployment or implementation.» `config/env.transcription.example` setzt noch das gehostete Textmodell `https://openrouter.ai/api/v1` und die Upstream-Variablen `ADMIN_USERNAME`, `ADMIN_EMAIL` und `ADMIN_PASSWORD`. Beides müssen Sie für die Klinikversion ändern (Schritt 3).
- **Die Installationsanleitung ist Upstream-Text.** Die [Installationsanleitung](../../getting-started/installation.md) (English) enthält nichts zur Klinikversion: kein `CLINIC_MODE`, keine erste Administration, keine Kontoübernahme, keine Übernahme von Aufnahmen. Ihr Aktualisierungsablauf mit `docker compose pull` lädt Upstream-Speakr.
- **Wer die Schritte ausführt.** Die Schritte brauchen Zugriff auf den Server und seine Umgebung. In der Webverwaltung braucht die Kontoübernahme ein Konto mit **Administrationsrolle** und **Identitätsadministration**, dem Status **Aktiv**, ohne ausstehenden Passwortwechsel und mit einer lokalen Anmeldung in den letzten 300 Sekunden. Die Einrichtung der Pools braucht eine aktive globale Administration. Die beiden Kennzeichen sind in der [Benutzerverwaltung](user-management.md) beschrieben, die Rollen für Pools in der [Gruppenverwaltung](group-management.md).

## Upstream-Installation auf die Klinikversion umstellen

Führen Sie die Schritte in dieser Reihenfolge aus.

### 1. Sichern

Sichern Sie vor dem Wechsel des Images:

- die Datenbank: die SQLite-Datei (Standard-URI `sqlite:////data/instance/transcriptions.db`) oder einen PostgreSQL-Dump;
- `UPLOAD_FOLDER` (Standard `/data/uploads`);
- den Bucket, wenn `FILE_STORAGE_BACKEND=s3` gesetzt ist;
- `.env` und den Signaturschlüssel der Sitzungen: `SECRET_KEY` oder die erzeugte Schlüsseldatei (`SECRET_KEY_FILE`, sonst `secret_key` neben der SQLite-Datenbank, sonst in `/data/instance`).

Sichern Sie die Datenbank vor den Dateiablagen. Die vollständige Liste für eine laufende Klinikinstallation, einschliesslich `CLINIC_ORIGINALS_ROOT`, steht unter [Dateispeicher](storage.md#datensicherung). Eine Wiederherstellung wurde nicht getestet, und die Produktionsvorlage enthält keine freigegebenen Wiederherstellungswerte.

### 2. Klinik-Image bauen

Verwenden Sie den Stand des Branches `main` des Klinik-Forks und bauen Sie das Image mit seinem Dockerfile ([Dockerfile, Klinik-Fork](https://github.com/joehomeskillet/speakr/blob/main/Dockerfile)):

```bash
docker build -t <image>:<tag> .
```

- `--build-arg LIGHTWEIGHT=1` installiert `requirements.lock` statt `requirements-embeddings.lock`.
- Der Build fixiert seine Eingaben: das Python-Basisimage über den Digest, die Python-Pakete mit `--require-hashes` sowie FFmpeg, das Programm `dss-decode` und Tailwind mit SHA-256-Prüfung.
- Das Image setzt `CLINIC_DSS_CODEC_BIN=/usr/local/bin/dss-decode` für DS2- und DSS-Diktatdateien, und gunicorn antwortet auf Port 8899. Ausserhalb von Docker setzen Sie `CLINIC_DSS_CODEC_BIN` selbst. Ohne diese Variable wird eine hochgeladene `.ds2`- oder `.dss`-Datei zwar gespeichert, ihre Aufnahme endet aber mit dem Status FAILED.
- Das Image enthält die Klinik-Vertragsdateien in `clinic-spec/config`.

Verwenden Sie dieses Image in Ihrer Installation anstelle von `learnedmachine/speakr`.

### 3. Umgebung anpassen

| Variable | Massnahme | Grund |
|---|---|---|
| `CLINIC_MODE` | Auf `1` setzen. | Schaltet den Klinikmodus ein. Der Wert wird bei jedem Aufruf gelesen. |
| `ADMIN_USERNAME`, `ADMIN_EMAIL`, `ADMIN_PASSWORD` | Entfernen. | Der Docker-Entrypoint würde eine Administration ohne Klinik-Kontorichtlinie anlegen, die sich nicht anmelden kann. Verwenden Sie stattdessen das Bootstrap-Skript (Schritt 5). |
| `TRANSCRIPTION_BASE_URL` | Auf einen lokalen OpenAI-kompatiblen ASR-Dienst richten, zum Beispiel `http://speaches:8000/v1`. | Im Klinikmodus wird eine leere oder nicht lokale Adresse abgelehnt; die Folgen für den Start beschreibt Schritt 4. Lokal ist eine Loopback-, private oder Link-Local-Adresse, ein einteiliger Hostname oder ein Name, der auf `.local`, `.internal`, `.lan` oder `.localhost` endet. |
| `ASR_BASE_URL` | Nur einen lokalen WhisperX- oder whisper-asr-webservice-Host eintragen. | Diese Adresse wird im Klinikmodus nicht auf Lokalität geprüft. |
| `TRANSCRIPTION_CONNECTOR` | `openai_whisper`, `asr_endpoint` oder leer. | Die Cloud-Anbindungen des Upstreams sind entfernt. Ihre Namen scheitern mit `Unknown connector: <name>. Available: [...]`. `clinic_stub` ist eine synthetische Testanbindung und erzeugt keine echten Transkripte. |
| `TEXT_MODEL_BASE_URL`, `TEXT_MODEL_API_KEY` | Beide Werte auf ein lokales Textmodell setzen oder beide entfernen. | Eine gehostete Adresse sperrt im Klinikmodus Chat, Inquire und die Routen der generativen Zusammenfassung. Ist keiner der beiden Werte gesetzt, wird kein LLM-Client angelegt. Ist nur `TEXT_MODEL_API_KEY` gesetzt, entsteht ebenfalls ein Client; er verwendet die gehostete Standardadresse `https://openrouter.ai/api/v1`. Siehe die Warnung unten. |
| `ENABLE_INQUIRE_MODE` | `false` | Mit `true` zerlegt und indexiert jeder Start weiterhin bis zu 10 bestehende Aufnahmen, und jede fertige Aufnahme wird zur Indexierung eingereiht, ohne Klinikprüfung. |
| `SQLALCHEMY_DATABASE_URI` | Beibehalten oder auf PostgreSQL richten. | Entscheid D04: PostgreSQL in Integration und Produktion. Kein Werkzeug im Repository überträgt Daten von SQLite nach PostgreSQL. |
| `CLINIC_ORIGINALS_ROOT` | Optional. | Wurzel des Klinik-Originalspeichers. Standard: `UPLOAD_FOLDER`. |
| `CLINIC_CONFIG_DIR` | Meist nicht gesetzt. | Standard: `clinic-spec/config` im Anwendungsverzeichnis. Eine fehlende Vertragsdatei ist ein Fehler. |
| `CLINIC_PROFILE` | Nicht setzen oder `production`; `staging` nie ausserhalb einer Testinstallation. | Nicht gesetzt: Der KIS-Adapter ist deaktiviert, die endgültige Freigabe bleibt geschlossen. `staging`: synthetischer Mock-KIS-Adapter (Simuliert; kein KIS-Nachweis). `production`: `clinic_stub` und der Mock-KIS-Adapter verweigern den Betrieb. Siehe [Gruppenverwaltung](group-management.md). |
| `CLINIC_AGENT_PROXY_SECRET`, `CLINIC_AGENT_MASTER_SECRET` | Nur mit dem Windows-Importagenten: je mindestens 32 Byte, zum Beispiel `<secret>`. | Ohne gültige Werte wird jede Anfrage des Agenten mit 401 abgelehnt. Der Kanal des Importagenten ist Vorbereitet, deaktiviert: Keine unterstützte Route, kein Befehl und kein Skript registriert einen Agenten. Siehe [Aufnahmesitzungen](recording-sessions.md#kanal-des-importagenten-und-eingang). |
| `CLINIC_PASSWORD_BLOCKLIST_FILE` | Optional. | Sperrliste für Passwörter der Installation, ein Eintrag pro Zeile. |

!!! warning "Automatische Titel und Zusammenfassungen ohne Lokalitätsprüfung"
    Im Klinikmodus reiht jede fertige Transkription einen Folgeauftrag ein. Wenn ein LLM-Client besteht, sendet dieser Auftrag das Transkript für den Titel an das Textmodell und, sofern automatische Zusammenfassungen nicht ausgeschaltet sind, auch für die Zusammenfassung. Dieser Weg hat keine eigene Lokalitätsprüfung (offene Lücke). Ein LLM-Client entsteht, sobald `TEXT_MODEL_BASE_URL` oder `TEXT_MODEL_API_KEY` gesetzt ist; ohne `TEXT_MODEL_BASE_URL` verwendet er die gehostete Standardadresse `https://openrouter.ai/api/v1`. Mit einer gehosteten Adresse gehen Transkripte an diesen Dienst. Setzen Sie nie nur `TEXT_MODEL_API_KEY`. Setzen Sie beide Werte auf ein lokales Textmodell, oder entfernen Sie beide. Die Regel für lokale Modelle steht in der [Modellkonfiguration](model-configuration.md#die-regel-fur-lokale-modelle).

Weitere Hinweise:

- **Aufbewahrung.** `ENABLE_AUTO_DELETION`, `GLOBAL_RETENTION_DAYS` und `DELETION_MODE` behalten ihre Namen, aber jeder Modus archiviert ab dem ersten Lauf nach dem Upgrade nur noch. Siehe [Aufbewahrung](retention.md#verhalten-beim-upgrade).
- **Upstream-Schalter ohne Wirkung.** `ENABLE_PUBLIC_SHARING` und `ENABLE_INTERNAL_SHARING` werden nicht gelesen. `ENABLE_SSO` und die `SSO_*`-Variablen schalten im Klinikmodus keine Anmeldung ein. `WEBHOOK_GLOBAL_ENABLED` wirkt nicht, solange Webhooks gesperrt sind. Siehe [Systemeinstellungen](system-settings.md#upstream-einstellungen-ohne-wirkung-im-klinikmodus) und [Anmeldung und Identitätsanbieter](sso-setup.md).
- **Spracherkennung.** Einrichtung von CPU und GPU, Zeitlimits und die einmalige CPU-Ausweichlösung stehen unter [Lokale Spracherkennung](whisperx-setup.md).

### 4. Anwendung starten

Der erste Start mit dem Klinik-Image aktualisiert das Schema. Es gibt keinen eigenen Migrationsbefehl: kein Alembic und kein `flask db upgrade`.

Bei jedem Start, der die Datenbank initialisiert, führt die Anwendung Folgendes aus:

1. Sie ruft `db.create_all()` auf und legt fehlende Tabellen an.
2. Sie nimmt die Migrationssperre: einen Advisory Lock auf PostgreSQL, eine Sperrdatei im temporären Verzeichnis auf SQLite. Nach 120 Sekunden ohne Sperre fährt sie fort, weil jede Migration idempotent ist. Jeder Prozess führt die Migrationen einmal aus (der Entrypoint, das Administrationsskript, jeder gunicorn-Worker).
3. Sie legt die Tabelle `schema_migrations` an, in der ausgeführte Migrationen verbucht werden, falls sie fehlt.
4. Sie führt die Migrationsabschnitte nacheinander aus. Der erste Abschnitt, «clinic integrity», legt alle fehlenden `clinic_*`-Tabellen an, führt die Anlageschritte der Klinikmodule aus und installiert die Datenbanktrigger. Ein Anlageschritt ergänzt in `clinic_author_pool` und `clinic_staff_pool` die fehlenden Spalten `active`, `valid_from` und `valid_to`; sie erlauben `NULL`, und `NULL` in `active` bedeutet aktiv. Der Abschnitt «core recording and user columns» erweitert `user.password` von VARCHAR(60) auf TEXT, weil die Passworthashes des Klinikverfahrens (scrypt) mehr Platz brauchen; bestehende Hashes werden unverändert übernommen (Protokoll: `Widened user.password from VARCHAR(60) to TEXT`).
5. Nur im Klinikmodus durchläuft sie den Klinik-Originalspeicher (siehe [Dateispeicher](storage.md#startdurchlauf)).

Die Trigger gibt es nur auf SQLite und PostgreSQL. Sie verweigern `UPDATE` und `DELETE` auf `clinic_original_version` und `clinic_transcript_revision` und lassen in `clinic_output_receipt`, `clinic_marker_event`, `clinic_asr_profile_version`, `clinic_asr_training_run`, `clinic_voice_consent` und `clinic_voice_consent_audit` nur das Anfügen zu. In `clinic_review_lease` verlangen sie eine steigende `generation`. Andere Datenbanksysteme erhalten keine Trigger.

Prüfen Sie das Startprotokoll:

| Protokollzeile | Bedeutung |
|---|---|
| `DATABASE MIGRATION: <n> section(s) failed: <names>` (ERROR) | Ein oder mehrere Abschnitte sind fehlgeschlagen. Die Anwendung startet trotzdem weiter, und die Weboberfläche zeigt keinen Hinweis. Jeder Abschnitt wird beim nächsten Start wiederholt. Die folgenden ERROR-Zeilen des Banners nennen ausserdem den Issue-Tracker von Upstream-Speakr; sie stammen unverändert aus dem Upstream. |
| `Clinic original sweep: scanned=<n> removed_leftovers=<n> incomplete=<n> awaiting_row=<n> errors=<n>` | Der Durchlauf über den Originalspeicher ist gelaufen. |
| `Transcription connector initialized: <name>` | Die Anbindung ist gestartet. |
| `Failed to initialize transcription connector: Failed to initialize connector '<name>': …` (ERROR), danach `Falling back to legacy transcription configuration validation` | Die Anbindung wurde abgelehnt (leere oder nicht lokale Adresse, leerer `TRANSCRIPTION_API_KEY`, unbekannter Name). Ist `TRANSCRIPTION_BASE_URL` oder `TRANSCRIPTION_API_KEY` leer und steht `USE_ASR_ENDPOINT` nicht auf `true`, endet der Start danach mit `ERROR: No transcription service configured!` (Exitcode 1): Der Entrypoint bricht ab, ebenso jeder gunicorn-Worker, der die Anwendung lädt. Sind beide gesetzt, zum Beispiel eine nicht lokale Adresse mit Schlüssel, startet die Anwendung und protokolliert `Using Whisper API: <url>`, aber keine Transkription funktioniert. |
| `clinic policy overrides stored setting <key>=<value> (effective: False)` (WARNING) | Eine Upstream-Token-Einstellung ist als eingeschaltet gespeichert, die Klinikobergrenze erzwingt aber «aus». Siehe [Systemeinstellungen](system-settings.md#klinikobergrenzen). |

Der Docker-Entrypoint prüft nur, ob die SQLite-Datei `/data/instance/transcriptions.db` vorhanden ist. Mit PostgreSQL gibt er deshalb bei jedem Start `Database doesn't exist. Creating new database...` aus. Die Meldung hat dort keine Bedeutung; die Arbeit am Schema läuft, wenn die Anwendung geladen wird.

**Reine Webprozesse.** `CLINIC_WEB_ONLY=1` macht einen Prozess zu einem vorübergehenden HTTP-Worker für eine bereits initialisierte Klinikinstallation. Ein solcher Prozess verlangt den Klinikmodus, führt kein DDL, keine Migration und keinen Durchlauf aus und prüft nur, ob die Tabelle `clinic_dictation` vorhanden ist. Er braucht das bestehende Upload-Verzeichnis und den bestehenden Signaturschlüssel der Sitzungen und erzeugt nie einen Schlüssel. Ein anderer Wert als `0` oder `1` beendet den Entrypoint mit dem Exitcode 64. Starten Sie nach jedem Upgrade zuerst einen normalen Prozess.

### 5. Erste Administration anlegen

Nach dem Upgrade kann sich kein bestehendes Konto anmelden (Schritt 6). Legen Sie die erste Administration einmalig an der Serverkonsole an, in der Umgebung der Anwendung:

```bash
python scripts/clinic_bootstrap_admin.py --username <benutzername> --email <e-mail-adresse> [--name "<anzeigename>"]
```

Das Passwort stammt aus `CLINIC_BOOTSTRAP_PASSWORD` (oder aus der mit `--password-env` genannten Variable), aus `--password-stdin` oder aus einer verdeckten Eingabe. Es wird nie ausgegeben. Es braucht 15 bis 128 Zeichen. Das Konto erhält **Administrationsrolle** und **Identitätsadministration**, die Anmelderichtlinie **Nur lokal** und keine klinische Rolle. Ein zweiter Lauf gibt `Already initialised; nothing changed.` aus.

Benutzernamen und E-Mail-Adressen bestehender Upstream-Konten sind vergeben. Wählen Sie neue, sonst endet das Skript mit `Error: account data rejected (duplicate).` und dem Exitcode 2. Optionen, Ausgaben und Exitcodes stehen in der [Benutzerverwaltung](user-management.md#erste-administration-einrichten).

### 6. Bestehende Konten übernehmen

Bestehende Upstream-Konten haben keine Klinik-Kontorichtlinie. Im Klinikmodus nimmt die lokale Anmeldung nur ein aktives Konto mit der Anmelderichtlinie **Nur lokal** oder **Lokal und Entra** an. Ein Konto ohne Richtlinie wird mit `Anmeldung fehlgeschlagen.` abgewiesen.

Sie finden die Konten ohne Richtlinie auf der Seite **Benutzer und Rollen** (`/admin#/benutzer`) mit dem Filter **Keine Anmelderichtlinie** oder in der Ausgabe von `scripts/clinic_roles_report.py` (Schritt 9): Unter `accounts` hat ein solches Konto `"status": null`.

Eine Identitätsadministration übernimmt jeweils ein Konto. Die Seite hat dafür kein Bedienelement:

```http
PUT /admin/users/<id>
Content-Type: application/json

{"adopt_existing_local": true, "expected_version": 0}
```

Der Anfragekörper muss genau diese beiden Schlüssel enthalten. Die Anfrage braucht die Sitzung eines Kontos mit **Administrationsrolle** und **Identitätsadministration**, dem Status **Aktiv**, ohne ausstehenden Passwortwechsel und mit einer lokalen Anmeldung, die höchstens 300 Sekunden zurückliegt, sowie das CSRF-Token dieser Sitzung. Einzelheiten stehen in der [Benutzerverwaltung](user-management.md#bestehende-konten-ohne-kontorichtlinie).

- **Ergebnis.** Das Konto erhält den Status **Aktiv** und die Anmelderichtlinie **Nur lokal**. **Identitätsadministration** bleibt aus. Das Auditprotokoll erfasst `admin.user.changed` mit dem Feld `local_account_policy_adopted`.
- **Ablehnungen.** HTTP 409 mit `Diese Änderung ist im aktuellen Kontozustand nicht möglich.` und dem Code `no_local_credential` (kein bcrypt- oder scrypt-Passworthash, zum Beispiel bei einem Konto, das nur die Einmalanmeldung verwendet hat), `external_identity_present` (eine Upstream-SSO-Verknüpfung, eine Entra-Verknüpfung oder eine Verknüpfungsanfrage) oder `existing_account_policy` (das Konto hat bereits eine Richtlinie, die nicht aktiv **Nur lokal** ist). Ein anderer Anfragekörper ergibt 400 `invalid_request`.
- **Konten mit Administrationsrolle.** Das Administrationskennzeichen aus dem Upstream (**Administrationsrolle**) bleibt erhalten. Ein übernommenes Upstream-Administrationskonto ist deshalb eine aktive globale Administration: Es sieht jedes Klinikdiktat und kann jedes Transkript und Audio öffnen; jedes Öffnen wird protokolliert. Entfernen Sie die **Administrationsrolle**, wo sie nicht gewollt ist (siehe [Benutzerverwaltung](user-management.md#rollen-andern)).
- **Passwörter.** Das bisherige Passwort bleibt gültig. Die Anmeldung wendet die Mindestlänge von 15 Zeichen darauf nicht an. Ein bcrypt-Hash wird bei der nächsten erfolgreichen Anmeldung durch das Klinikverfahren ersetzt.
- **Konten, die sich nicht übernehmen lassen.** Die Klinikversion bietet ihnen keine Anmeldung: Die Upstream-Routen für die Einmalanmeldung lehnen im Klinikmodus ab. Ein neues lokales Konto ist ein eigener Benutzer mit eigener ID.
- **Rechte.** Ein übernommenes Konto hat nur die sicheren Standardrechte für eigene Diktate (ansehen, abspielen, bearbeiten). **Exportieren**, **Freigeben**, **Originalaudio exportieren** und jede andere Aktion brauchen Fachrechte. Siehe [Benutzerverwaltung](user-management.md).

### 7. Autorenpools einrichten

Eine Aufnahme wird nur dann zum Diktat, wenn ihre Eigentümerin oder ihr Eigentümer zu einem aktiven Autorenpool gehört, der innerhalb seiner Gültigkeit liegt. Das gilt für die Übernahme bestehender Aufnahmen ebenso wie für neue Aufnahmen und Uploads im Browser.

- Autorenpools lassen sich nur über die API anlegen: `POST /api/clinic/v1/pool-admin/pools` mit `kind` gleich `author` und einem `name`, durch eine aktive globale Administration.
- Mitglieder fügen Sie auf der Seite **Diktierende und Autorenpools** (`/admin#/diktierende`) im Editor **Pools verwalten** hinzu.
- Schreibkräfte-Pools legen Sie auf der Seite **Schreibkräfte-Pools** (`/admin#/schreibkraefte-pools`) an. Schreibkräfte sehen Diktate über Schreibkräfte-Pools, die mit dem Autorenpool verknüpft sind. Die Verknüpfung eines Schreibkräfte-Pools mit einem Autorenpool ist nur über die API möglich: `POST /api/clinic/v1/pool-admin/pools/author/<pool_id>/links` mit `{"staff_pool_id": "<id>"}`.
- Halten Sie jede Autorin und jeden Autor in genau einem Autorenpool. Der Server lehnt eine zweite Mitgliedschaft nicht ab. Die Übernahme verwendet den ersten verwendbaren Pool in der Reihenfolge der IDs.

Alle Routen und Seiten sind in der [Gruppenverwaltung](group-management.md) beschrieben.

### 8. Bestehende Aufnahmen übernehmen

Aufnahmen aus der Upstream-Installation haben kein Klinikdiktat. Bis zu ihrer Übernahme fehlen sie unter **Aufnahmen** und in der **Arbeitsliste**.

Führen Sie das Skript im Anwendungscontainer aus, mit der Umgebung der Anwendung:

```bash
# Probelauf (Standard): eine Zeile pro Kandidat, nichts wird geändert
python scripts/clinic_backfill_dictations.py

# Diktate anlegen
python scripts/clinic_backfill_dictations.py --apply

# Diktate ohne erste Audiorevision auflisten; mit --apply die Revisionen anlegen
python scripts/clinic_backfill_dictations.py --init-audio
python scripts/clinic_backfill_dictations.py --init-audio --apply
```

Was die Übernahme tut:

- Sie berücksichtigt jede Aufnahme mit gespeichertem Audiopfad. Übernommen werden nur Aufnahmen mit dem Status COMPLETED oder FAILED.
- Sie legt pro Aufnahme ein Diktat an, über denselben Originalspeicher wie ein Upload im Browser. Autorin oder Autor ist die Eigentümerin oder der Eigentümer der Aufnahme, der Pool ist deren Autorenpool. Das Diktat beginnt im Ablaufstatus **Neu angelegt**.
- Der Zeitpunkt des Diktats ist das Besprechungsdatum der Aufnahme, wenn es gesetzt ist, sonst ihr Erstellungszeitpunkt. Die neue Ankeraufnahme trägt Besprechungsdatum, Status und Dauer der Quelle.
- Ein fertiges Transkript wird zur ersten ASR-Textrevision des Diktats. Scheitert das Speichern, wird das ganze Diktat zurückgerollt.
- Die Übernahme kopiert nur das Audio, den Transkripttext, das Besprechungsdatum, den Status und die Dauer. Titel, Zusammenfassung, Notizen, Tags und Ordner der Quellaufnahme werden nicht in das Diktat übernommen; sie bleiben unverändert bei der Quellaufnahme in der Datenbank.
- Die Quellaufnahme und ihre Datei werden nur gelesen, nie geändert oder gelöscht. Das Audio liegt danach zweimal vor: als Quelle und als Klinikoriginal. Planen Sie den Speicherplatz dafür ein (siehe [Dateispeicher](storage.md)).
- Jede Aufnahme ist eine eigene Transaktion; ein Fehler überspringt nur diese Aufnahme. Der Idempotenzschlüssel ist `legacy:<Aufnahme-ID>`, deshalb legt ein zweites `--apply` nichts an.
- Jedes angelegte Diktat wird als `recording.edited` mit `actor_kind` `system` und dem Feld `clinic_backfill` protokolliert.

Die Ausgabezeilen enthalten nur IDs, die Aktion und den Grund, nie Text: `recording=<id> owner=<id> action=<create|skip> reason=<reason>`, nach dem Anlegen ergänzt um ` dictation=<id>`. Die letzte Zeile lautet `dry run: <n> to create, <m> skipped; nothing changed` oder `created dictations: <ids>` (beziehungsweise `none`).

| Grund | Aktion | Bedeutung |
|---|---|---|
| `with_transcription` | create | Übernommen, mit dem Transkript als erster ASR-Revision. |
| `without_transcription` | create | Ohne Text übernommen, zum Beispiel eine Aufnahme mit FAILED. |
| `already_linked` | skip | Bereits übernommen oder bereits durch den Upload im Browser verknüpft. |
| `not_finished` | skip | Nicht COMPLETED oder FAILED. Ein späterer Lauf nimmt sie auf. |
| `no_audio_file` | skip | Die Audiodatei fehlt in der Ablage. |
| `no_author_pool` | skip | Die Eigentümerin oder der Eigentümer gehört zu keinem verwendbaren Autorenpool. Richten Sie einen ein (Schritt 7) und starten Sie das Skript erneut. |
| `asr_failed` | skip | Das Speichern des Transkripts ist gescheitert. Das Diktat wurde zurückgerollt; ein neuer Lauf versucht es erneut. |
| `ingest_<code>` | skip | Der Originalspeicher hat die Datei abgelehnt. |
| `ingest_failed` | skip | Ein anderer Fehler. Prüfen Sie die Protokollzeile `legacy backfill: recording <id> failed (<type>)`. |

**`--init-audio`** repariert Diktate ohne erste Audiorevision. Die Option lädt nicht die ganze Anwendung und führt deshalb keine Migration und keinen Durchlauf aus. Sie braucht eine bestehende Datenbank; auf SQLite öffnet der Probelauf die Datei nur lesend. Jede Zeile lautet `dictation=<id> action=<action> reason=<reason>`, mit den Gründen `missing_audio_revision` (Probelauf), `initialized`, `already_initialized`, `no_original`, `no_dictation`, `original_read_failed`, `invalid_storage_key`, `database_failed` (Aktion `error`) sowie den Codes der Dauermessung `missing_original_file`, `original_hash_mismatch`, `audio_too_short` und `audio_<status>` (zum Beispiel `audio_decoder_missing` ohne `CLINIC_DSS_CODEC_BIN`).

Exitcodes: `0` erledigt, `2` Klinikmodus ist aus (`Error: clinic mode is off (CLINIC_MODE).`), `1` anderer Fehler (bei `--init-audio` auch, wenn ein Schreibvorgang in der Datenbank gescheitert ist).

### 9. Ergebnis prüfen

- `python scripts/clinic_roles_report.py` gibt eine schreibgeschützte JSON-Momentaufnahme aus: Konten mit ihren Kennzeichen und dem Status der Richtlinie, Pools, Mitgliedschaften, Verknüpfungen, Fachrechte, Diktate mit Ablaufstatus und Revisionen, Anzahl Aufträge nach Typ und Status, die häufigsten Inhaltszugriffe und die letzten 10 Originale mit `source_recording_id` für übernommene Aufnahmen. Das Skript lädt die Webanwendung nicht und führt keine Migration aus. Es braucht `CLINIC_MODE` und `SQLALCHEMY_DATABASE_URI`; ein SQLite-Pfad muss absolut sein. Es gibt IDs und Zustände aus, keine Namen.
- Melden Sie sich mit einem übernommenen Konto an und öffnen Sie die **Arbeitsliste**.
- Angemeldet zeigt `GET /api/system/info` die aktive Transkriptionsanbindung.
- Prüfen Sie im **System-Auditprotokoll** (`/admin#/system/audit`) die Einträge `admin.user.changed` für die Kontoübernahmen und `recording.edited` für die Übernahme der Aufnahmen. Siehe [Auditprotokoll](audit-log.md).

## Was nicht übernommen wird

| Upstream-Daten oder -Funktion | In der Klinikversion |
|---|---|
| Öffentliche Links zum Teilen und internes Teilen (Upstream) | Nur Upstream. Der Code ist entfernt. Die Tabellen `share`, `internal_share`, `shared_recording_state` und `share_audit_log` bleiben ohne Modell in der Datenbank. Die Klinikversion führt den Upstream-Schritt nicht aus, der `share_audit_log` nach `audit_event` kopiert. Eine Installation, auf der Upstream-Speakr v0.11.1 lief, hat diese Zeilen beim Start bereits kopiert; sie bleiben in `audit_event`, mit Upstream-Ereigniscodes für das Teilen (`share.internal.*`, `share.public.*`), die der Ereigniskatalog der Klinikversion nicht aufführt. |
| Selbstregistrierung, Bestätigung der E-Mail-Adresse, Zurücksetzen des Passworts per E-Mail | Nur Upstream. Entfernt. Konten legt eine Identitätsadministration an; aktiviert werden sie mit einem Einmalcode. Siehe [Benutzerverwaltung](user-management.md) und [E-Mail-Einrichtung](email-setup.md). |
| Inkognito-Modus, Ereignisextraktion und Kalenderexport | Nur Upstream. Entfernt. |
| Cloud-Anbindungen für die Transkription | Nur Upstream. Entfernt. Es bleiben nur `openai_whisper`, `asr_endpoint` und `clinic_stub`. |
| Einmalanmeldung des Upstreams | Nur Upstream. Die Routen lehnen im Klinikmodus ab, und Konten mit SSO-Verknüpfung lassen sich nicht übernehmen. Siehe [Anmeldung und Identitätsanbieter](sso-setup.md). |
| Bestehende Upstream-Konten | Können sich erst nach der Übernahme anmelden (Schritt 6). |
| Bestehende Aufnahmen | Erscheinen erst nach der Übernahme unter **Aufnahmen** und in der **Arbeitsliste** (Schritt 8). |
| Titel, Zusammenfassung, Notizen, Tags und Ordner übernommener Aufnahmen | Werden nicht in das Diktat übernommen (Schritt 8). |
| Gruppen, Teamadministration, Gruppen-Tags und Gruppenordner | Nur Upstream. Gesperrt (Funktion `internal_sharing`). Bestehende Upstream-Gruppen, ihre Mitglieder und Teamadministrationsrollen werden nicht zu Schreibkräfte-Pools. Ein Schreibkräfte-Pool wird als neue Zeile in derselben Tabelle `group` gespeichert. Ein Poolname, den eine Upstream-Gruppe bereits verwendet, wird mit 409 `name_taken` abgelehnt. Persönliche Tags bleiben verfügbar. Siehe [Gruppenverwaltung](group-management.md). |
| Sprecherprofile und Stimmproben | Nur Upstream. Die Routen sind gesperrt. Eine Funktion zum Entfernen der gespeicherten Upstream-Stimmdaten ist vorhanden, wird aber nirgends aufgerufen. Siehe [Datenschutz](privacy.md). |
| Persönliche API-Token | Nur Upstream. Anfragen mit einem Token werden mit 403 `clinic_feature_disabled` abgelehnt. |
| Webhooks | Nur Upstream. Gesperrt; nichts wird versendet. Siehe [Webhooks](webhooks.md). |
| Inquire, Ambrose und der Vektorspeicher | Nur Upstream. Ausgeblendet oder gesperrt. Siehe [Ambrose-Einstellungen](ambrose.md) und [Vektorspeicher](vector-store.md). |
| Gespeicherte Standard-Prompts | Der Reiter der Administration ist ausgeblendet. Die deutschen Klinik-Prompts gelten, wenn der gespeicherte Administrations-Prompt leer ist oder dem Upstream-Standard entspricht; ein anderer gespeicherter Prompt hat weiterhin Vorrang. Siehe [Standard-Prompts](prompts.md). |
| Löschen durch die Aufbewahrung, manuelles Löschen von Aufnahmen | Die Aufbewahrung archiviert nur. Manuelles Löschen ist im Klinikmodus gesperrt. Siehe [Aufbewahrung](retention.md). |
| Gespeicherte Token-Einstellungen `api_query_tokens_enabled`, `sso_api_tokens_enabled` | Von der Klinikobergrenze auf `false` gezwungen. Siehe [Systemeinstellungen](system-settings.md#klinikobergrenzen). |

## Von einer Klinikversion auf die nächste aktualisieren

Die freigegebene Version ist der Branch `main` des Klinik-Forks.

1. Sichern Sie zuerst die Datenbank, dann die Dateiablagen (siehe [Dateispeicher](storage.md#datensicherung)).
2. Bauen Sie das Image aus dem neuen Stand von `main` (Schritt 2 oben).
3. Starten Sie zuerst einen normalen Prozess, keinen reinen Webprozess. Er führt die Migrationen der neuen Version aus. Prüfen Sie die Protokollzeilen aus Schritt 4.
4. Starten Sie danach die reinen Webprozesse, falls Sie solche verwenden.

Die Migrationen sind idempotent, und ein fehlgeschlagener Abschnitt wird beim nächsten Start wiederholt. Es gibt keinen Befehl für eine Rückmigration. Die Migration der Poolspalten hat zwar eine Funktion zum Zurücknehmen, aber kein Befehl ruft sie auf. Um auf eine frühere Version zurückzugehen, stellen Sie die Datenbank- und Dateisicherungen von vor dem Upgrade wieder her. Dasselbe gilt für eine Rückkehr zu Upstream-Speakr: Nach einer Anmeldung ersetzt die Klinikversion bcrypt-Hashes durch scrypt-Hashes, die Upstream-Speakr nicht prüfen kann. Nach einer Wiederherstellung meldet der Startdurchlauf Dateien ohne Zeile als `awaiting_row`; behalten Sie diese Dateien (siehe [Dateispeicher](storage.md#startdurchlauf)).

### Modellwahl und Diktatzusammenfassungen (In Prüfung)

Die Seite **Modellwahl** und die automatischen Diktatzusammenfassungen sind nicht Teil der aktuell freigegebenen Version. Bezeichnungen und Verfügbarkeit können sich noch ändern. Im Code, der sich in Prüfung befindet, verhält sich ein Upgrade auf diese Version so:

- Der Start legt die neue Tabelle `clinic_dictation_summary` an wie jede andere Kliniktabelle. Es werden keine Daten migriert.
- Nur eine aktive globale Administration kann **Modellwahl** öffnen (**Verwaltung › System**, `/admin#/system/modelle`). Die Auswahl wird in der Systemeinstellung `clinic_task_models` gespeichert. Ohne gespeicherte Auswahl übernimmt jede Aufgabe die Vorgabe der Installation oder des lokalen Dienstes. Eine gespeicherte Modellkennung gilt nur, solange der jeweilige Dienst lokal ist.
- Eine automatische Zusammenfassung entsteht nur für den ersten ASR-Text eines neuen Diktats. Sie braucht ein bereites lokales Textmodell, automatische Zusammenfassungen, die weder über die Systemeinstellung `disable_auto_summarization` noch in der eigenen Einstellung des Kontos ausgeschaltet sind, sowie die Rechte zum Ansehen und Bearbeiten der Autorin oder des Autors. Bestehende Diktate, auch übernommene, erhalten keine automatische Zusammenfassung.
- Ein Konto mit Bearbeitungsrecht kann eine Zusammenfassung anfordern, sobald der aktuelle Text mindestens 10 Zeichen hat und das lokale Textmodell bereit ist; ohne bereites lokales Modell wird die Anfrage mit 503 abgelehnt.
- Für Diktate, die mit einer Quellaufnahme verknüpft sind, erstellt der Folgeauftrag nur noch den Titel, auch wenn er vor dem Upgrade eingereiht wurde. Bei übernommenen Diktaten wird die Basiszusammenfassung der Quellaufnahme als veraltet angezeigt.

Siehe [Modellkonfiguration](model-configuration.md#automatische-diktatzusammenfassungen-in-prufung).

## Transkriptionseinstellungen aus älteren Speakr-Versionen

Speakr v0.8 hat Transkriptionsanbindungen eingeführt. Ältere Umgebungsvariablen funktionieren in der Klinikversion weiterhin.

### Veraltete Variablen

| Veraltete Variable | Status | Umstellung |
|---|---|---|
| `USE_ASR_ENDPOINT=true` | Funktioniert weiterhin, protokolliert eine Warnung | Setzen Sie stattdessen `ASR_BASE_URL`. |
| `WHISPER_MODEL` | Wird weiterhin gelesen, wenn `TRANSCRIPTION_MODEL` leer ist; keine Warnung | Verwenden Sie `TRANSCRIPTION_MODEL`. |

### Auswahl der Anbindung

| Variable | Beschreibung |
|---|---|
| `TRANSCRIPTION_CONNECTOR` | Ausdrücklich gewählte Anbindung: `openai_whisper`, `asr_endpoint` oder `clinic_stub`. Optional. |
| `TRANSCRIPTION_MODEL` | Modellname für `openai_whisper`. |

Die Anbindung wird in dieser Reihenfolge gewählt:

1. `TRANSCRIPTION_CONNECTOR`, wenn gesetzt;
2. `asr_endpoint`, wenn `ASR_BASE_URL` gesetzt ist (oder das veraltete `USE_ASR_ENDPOINT=true`);
3. `openai_whisper` mit `TRANSCRIPTION_MODEL`, sonst `WHISPER_MODEL`, sonst `whisper-1`.

Den Upstream-Schritt, der bei Modellnamen mit `gpt-4o` OpenAI Transcribe wählte, gibt es in der Klinikversion nicht.

### Beispiele

Von der älteren ASR-Konfiguration:

```bash
# Vorher
USE_ASR_ENDPOINT=true
ASR_BASE_URL=http://whisperx-asr:9000

# Nachher: ASR_BASE_URL allein wählt asr_endpoint
ASR_BASE_URL=http://whisperx-asr:9000
```

`ASR_BASE_URL` muss auf einen lokalen Host zeigen. Die Anwendung prüft das nicht. `ASR_DIARIZE` steht bei dieser Anbindung standardmässig auf `true`. Im Klinikmodus werden die Stimmmerkmale der bei der Sprechertrennung erkannten Personen nicht gespeichert, und die Sprechererkennung des Upstreams ist gesperrt; siehe [Lokale Spracherkennung](whisperx-setup.md).

Von einer älteren Whisper-Konfiguration auf einen lokalen OpenAI-kompatiblen Dienst (Werte aus `config/env.transcription.example`):

```bash
# Vorher
TRANSCRIPTION_BASE_URL=https://api.openai.com/v1
TRANSCRIPTION_API_KEY=<secret>
WHISPER_MODEL=whisper-1

# Nachher
TRANSCRIPTION_BASE_URL=http://speaches:8000/v1
TRANSCRIPTION_API_KEY=not-needed-for-local
TRANSCRIPTION_MODEL=Systran/faster-distil-whisper-large-v3
```

Im Klinikmodus muss `TRANSCRIPTION_BASE_URL` gesetzt und lokal sein. Ein leerer Wert wird abgelehnt, und die Anwendung startet nicht (`ERROR: No transcription service configured!`, Schritt 4); es gibt keinen Rückfall auf eine gehostete API. Auch ein nicht lokaler Wert wird abgelehnt; ist ein Schlüssel gesetzt, startet die Anwendung, aber keine Transkription funktioniert. Die Anbindung braucht einen nicht leeren `TRANSCRIPTION_API_KEY`, auch für einen lokalen Dienst.

### Aufteilen grosser Dateien

| Anbindung | Aufteilung |
|---|---|
| `asr_endpoint` | Übernimmt der ASR-Dienst. Mit `ASR_ENABLE_CHUNKING=true` (Standard `false`) teilt die Anwendung die Dateien selbst auf, anhand von `ASR_MAX_DURATION_SECONDS` (Standard `7200`). |
| `openai_whisper` | Die Anwendung teilt grosse Dateien auf: `ENABLE_CHUNKING` (Standard `true`), `CHUNK_LIMIT` (zum Beispiel `20MB`) und `CHUNK_OVERLAP_SECONDS` (Standard `3`). |
| `clinic_stub` | Übernimmt die Anbindung; die Anwendung teilt keine Dateien auf. |

### Änderungen der Upstream-Oberfläche

Die Upstream-Seite nennt Änderungen der Basisansicht einer Aufnahme (Schaltfläche für die Sprechererkennung, Sprecheranzahl bei der erneuten Verarbeitung, Sprechblasenansicht). Sie beschreiben Upstream-Speakr. Im Klinikmodus ist die Sprechererkennung des Upstreams gesperrt. Nur Upstream.

### Anbindung prüfen

- Suchen Sie im Anwendungsprotokoll nach Warnungen zu veralteten Variablen, zum Beispiel `USE_ASR_ENDPOINT=true is deprecated.`.
- Angemeldet zeigt `GET /api/system/info` die Anbindung und ihre Fähigkeiten. Nur ein Konto mit **Administrationsrolle** sieht zusätzlich die Dienstadressen. Das ältere Feld `whisper_endpoint` zeigt `https://api.openai.com/v1`, wenn `TRANSCRIPTION_BASE_URL` nicht gesetzt ist, zum Beispiel mit der Anbindung `asr_endpoint`. Das ist nur eine Vorgabebezeichnung; dorthin wird nichts gesendet. Mit `openai_whisper` und ohne `TRANSCRIPTION_BASE_URL` startet die Anwendung nicht (Schritt 4).
- Laden Sie eine synthetische Testdatei hoch und prüfen Sie, ob sie transkribiert wird.

## Upstream-Skripte für die S3-Migration

Status: Standardmässig aus. Die S3-Ablage ist unverändert aus dem Upstream und funktioniert für Basismedien. Entscheid D06 in `clinic-spec/config/decisions.json` lautet «Existing local persistent storage adapter; no new S3 cluster»; Objektspeicher ist also nicht der klinische Speicherplan. Was der Bucket in der Klinikversion enthält, steht unter [Dateispeicher](storage.md#s3-ablage-in-der-klinikversion).

Die Skripte arbeiten nur mit `recording.audio_path`. Sie lesen, verschieben oder löschen nie Klinikoriginale oder Audiosegmente. Die Ankeraufnahmen der Klinikdiktate haben keinen Audiopfad, deshalb berühren die Skripte sie nicht. Führen Sie die Skripte im Anwendungscontainer aus.

### Phase 1: lokale Ablage beibehalten

Mit `FILE_STORAGE_BACKEND=local` (Standard) laufen alle Lese- und Schreibzugriffe über den Speicherdienst, verwenden aber weiterhin lokale Dateien.

### Phase 2: ältere Pfade vereinheitlichen

Ältere Aufnahmen können absolute oder relative Werte in `audio_path` haben. Dieses Skript wandelt sie in das Format `local://` um, ohne Dateien zu verschieben:

```bash
python scripts/migrate_local_paths_to_local_locator.py --dry-run
python scripts/migrate_local_paths_to_local_locator.py
```

| Option | Beschreibung |
|---|---|
| `--dry-run` | Vorschau ohne Schreiben in die Datenbank |
| `--limit N` | Nur die ersten N Einträge verarbeiten |
| `--recording-id ID` | Eine Aufnahme verarbeiten |
| `--only-user ID` | Die Aufnahmen eines Kontos verarbeiten |
| `--allow-missing-file` | Auch vereinheitlichen, wenn die lokale Datei fehlt |
| `--report-jsonl <path>` | Einen JSONL-Bericht über alle Aktionen schreiben |

Das Skript ist idempotent.

### Phase 3: neue Uploads nach S3 senden

```bash
FILE_STORAGE_BACKEND=s3
S3_BUCKET_NAME=<bucket>
S3_ENDPOINT_URL=<endpoint>          # für MinIO oder einen anderen S3-kompatiblen Speicher
S3_ACCESS_KEY_ID=<access-key-id>
S3_SECRET_ACCESS_KEY=<secret>
S3_USE_PATH_STYLE=true              # für MinIO erforderlich
```

Nach einem Neustart gehen neue Uploads in den Bucket, und bestehende `local://`-Aufnahmen werden weiterhin aus dem lokalen Dateisystem ausgeliefert. Klinikoriginale bleiben in jedem Fall lokal.

!!! note "CORS für vorsignierte URLs"
    Die Anwendung liefert S3-Audio mit einer Weiterleitung auf eine vorsignierte URL aus. Wenn sich der Ursprung der Weboberfläche und der des Objektspeichers unterscheiden, muss der Bucket Cross-Origin-Anfragen vom Webursprung Ihrer Installation zulassen. Erlauben Sie mindestens die Methoden `GET` und `HEAD`, den Header `Range` (empfohlen für das Springen im Audio) und Ihren Webursprung.

    Beispiel für AWS S3 (Ursprung anpassen):

    ```json
    [
      {
        "AllowedHeaders": ["*"],
        "AllowedMethods": ["GET", "HEAD"],
        "AllowedOrigins": ["https://speakr.example.com"],
        "ExposeHeaders": ["Accept-Ranges", "Content-Length", "Content-Range", "Content-Type", "ETag"],
        "MaxAgeSeconds": 3000
      }
    ]
    ```

    Für MinIO richten Sie gleichwertige Regeln für den Bucket ein.

### Phase 4: bestehende Dateien nach S3 verschieben

```bash
python scripts/migrate_local_recordings_to_s3.py --dry-run
python scripts/migrate_local_recordings_to_s3.py --limit 100
```

| Option | Beschreibung |
|---|---|
| `--dry-run` | Vorschau ohne Hochladen und ohne Änderung der Datenbank |
| `--limit N` | Höchstens N Einträge pro Lauf migrieren |
| `--recording-id ID` | Eine Aufnahme migrieren |
| `--only-user ID` | Die Aufnahmen eines Kontos migrieren |
| `--verify-size` | Die hochgeladene Grösse mit der lokalen vergleichen. Immer eingeschaltet; die Option kann es nicht ausschalten. |
| `--delete-local-after-success` | Die lokale Datei nach einem erfolgreichen Hochladen löschen |
| `--report-jsonl <path>` | Einen JSONL-Bericht schreiben |

Das Skript überspringt Aufnahmen, die bereits auf `s3://` liegen, Aufnahmen ohne `local://`-Pfad, Aufnahmen mit dem Status PROCESSING oder QUEUED und Aufnahmen, deren lokale Datei fehlt. Es kann gefahrlos erneut laufen. Führen Sie es in mehreren Durchgängen aus, bis nichts mehr übrig ist.

### Optional: zwischengespeicherte Dauer nachtragen

Ältere Aufnahmen können ohne zwischengespeicherte `audio_duration_seconds` sein. Dieses Skript trägt den Wert mit `ffprobe` nach, für `local://`- und `s3://`-Aufnahmen:

```bash
python scripts/backfill_audio_duration_seconds.py --dry-run
python scripts/backfill_audio_duration_seconds.py --report-jsonl <berichtsdatei>.jsonl
```

Es ändert nur Aufnahmen, bei denen der Wert leer ist, und überspringt Aufnahmen mit gelöschtem Audio sowie Aufnahmen mit dem Status PROCESSING oder QUEUED. Optionen: `--dry-run`, `--limit N`, `--recording-id ID`, `--only-user ID`, `--report-jsonl <path>`, `--ffprobe-timeout SECONDS` (Standard `30`).

### Phase 5: lokale Kopien entfernen

Das Migrationsskript behält die lokalen Dateien, ausser Sie geben `--delete-local-after-success` an. Klinikoriginale sind nie Teil dieses Schritts.

### S3-Migration prüfen

1. Das Abspielen migrierter Basisaufnahmen leitet auf vorsignierte URLs weiter.
2. Neue Uploads haben einen `audio_path`, der mit `s3://` beginnt.
3. Eine erneute Erkennung funktioniert; der Worker kopiert das Objekt dafür in eine temporäre lokale Datei.
4. Ältere Aufnahmen haben nach dem Nachtragen einen Wert in `audio_duration_seconds`.

Im Klinikmodus archiviert die Aufbewahrung nur, und manuelles Löschen ist gesperrt; keines von beiden entfernt also Objekte aus dem Bucket. Die Bereinigung von Aufnahmen ohne Eigentümer löscht weiterhin Aufnahmen ohne Eigentümer samt ihren Medien; siehe [Aufbewahrung](retention.md).

### Zurück zur lokalen Ablage

1. Setzen Sie `FILE_STORAGE_BACKEND=local`.
2. Starten Sie die Anwendung neu.
3. Neue Uploads gehen wieder in die lokale Ablage.
4. Bestehende `s3://`-Aufnahmen funktionieren weiter, solange die S3-Einstellungen gesetzt bleiben.

Beide Ablagen lassen sich nebeneinander verwenden.
