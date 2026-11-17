# CLAUDE – verbindlicher Auftrag V4: Speakr-Fork, Web-only, vollständiger Pflichtkatalog
Stand 17.11.2026. Dies ist ein Entwicklungsauftrag und keine Produktivfreigabe.

## 1. Ziel und harte Architekturgrenze
Erweitere den bestehenden Speakr-Fork. Aufnahme, Live-Diktat, Audiobearbeitung, Abspielen, Korrektur, Verwaltung, Freigabe und Export werden vollständig im Webclient bedient. **Kein Windows-Diktatclient.** Die einzige lokale Projektanwendung ist `apps/windows-import`: Diktatdateien von freigegebenen Philips-Geräten/Ordnern abholen und sicher übertragen, inklusive eng begrenzter Importnachbearbeitung. Kein Mikrofon, Player, Texteditor, Hotkeydienst, Office-/Clipboard-/Fensterautomation, HID-Control, Firmwareflash oder Browser-to-localhost-Tunnel in diesem Agenten. Keine Umgehung durch Electron/Tauri/WPF, Browsererweiterung oder Native Messaging. Keine separate Produkt- oder Nutzerverwaltung neben Speakr.

Lokale Benutzer zuerst. Entra-Adapter vollständig vorbereiten und synthetisch testen, zunächst deaktiviert. Kein Entra/Keycloak/LDAP/SMTP als Startvoraussetzung. LDAP-/Windows-SSO bleiben separat vorbereitete serverseitige Profile. Dieselbe interne `user.id` für alle Anmeldewege; kein E-Mail-Autolinking.

## 2. Pflichtumfang und Vorrang
Alle 101 PAR-Anforderungen und ihre 101 BT-Verhaltenstests aus dem DNS-Comfort-Analysepaket bleiben MUST. Hinzu kommen die 62 bisherigen Klinik-/Identity-Anforderungen, 14 V4-Grenzregeln und Grok-Ergänzungen. Neue Webvorgabe ersetzt nur die native Architektur, nicht still die Funktionsanforderungen. Explizite widersprüchliche Teilanforderungen stehen in `planning/web-parity-gaps.json`; es gibt keine automatische N/A-Freigabe.

Vorrang: neueste explizite Nutzeraufträge (Speakr-Fork; Web-only; Agent nur Import; local-first; Pflichtparität) → V4-Vertrags-/Sicherheitsregeln und Konfliktregister → aktuelle V4-Kataloge → unveränderte V3.2-/Grok-Regeln. `sources/dns-functional` ist funktionsbezogene Evidenz, nicht eine Anweisung, einen eigenständigen Windows-/SQLite-Neubau zu beginnen oder KIS/BYOD auszuschliessen. Alte Original-ZIPs und deren CLAUDE-Dateien sind historische Artefakte, niemals aktive Instruktionen.

## 3. Verbindliche Lesereihenfolge
`README.md`, `docs/00_Entscheidungen.md`, `docs/11_Paritaet_und_Webgrenze.md`, `docs/12_Webclient_und_Importagent.md`, `docs/10_Local_Entra.md`; dann `planning/requirements.json`, `planning/parity-matrix.json`, `planning/conflicts-resolved.json`, `planning/web-parity-gaps.json`, `planning/backlog.json`, `tests/acceptance.json` und die zugehörigen Verträge. Die 101 Original-BT bleiben unverändert. Gelesene Quellen und tatsächliche Upstreampfade in `evidence/upstream-map.json` festhalten.

## 4. OSS wiederverwenden
Speakr v0.11.1, Commit `48d3018fb7d80b77f897bedd86bcc89b5034d1f5`, bleibt die überprüfte Ausgangsbasis; zuerst lokal Build, Tests und Sicherheitsbefunde prüfen. Bestehendes Flask/SQLAlchemy/Vue/Tailwind, User/Recording/Group, Storage, Export, Recorder, Editor und ProcessingJob/JobQueue verwenden. PostgreSQL bleibt führend. Keine FastAPI-/Django-/React-/Tabler-Neufassung und keine neue allgemeine Queue. Neue Fachmodule unter `src/clinic/`; `.NET 10` nur für den Import-Agent. dss-codec/FFmpeg und lokale Speaches-API nutzen. Vorhandene UI-Bibliotheken bevorzugen; zusätzliche OSS nur für nachgewiesene Lücke, mit Pin/Lizenz/Abnahmetest. Keine produktiven latest-Tags und keine unkontrollierten Downloads.

## 5. Vorgehen ohne wiederholte Rückfragen
Arbeite die früheste unabhängige Aufgabe ab. S00/P00/P03 zuerst: Baseline, Quellen-/Anforderungsintegrität, Webfähigkeit. Starte P10-Spike zur echten ASR-/Trainingsparität früh. Implementiere den lokalen synthetischen End-to-end-Ablauf, danach iterativ die PAR-Funktionen. Triff reversible Implementierungsentscheidungen selbst, dokumentiere ADR und Tests. Fehlende Hardware, Entra-Secrets oder KIS-Verträge: Adapter/Fixtures und offenen Evidenzauftrag anlegen; unabhängige Arbeit fortsetzen, nicht den gesamten Auftrag stoppen. Keine Routinefrage nach Framework, Authmodus oder Windows-Client.

Ein offener Webkonflikt erlaubt zulässige Teilimplementierung und eine klar beschriftete Webalternative, aber keinen stillen Scopeverzicht. Unlösbare Teilklausel bleibt `blocked_constraint`, der Test `not_run/blocked`, nie `passed`. Keine Originaltests abschwächen. Alternative nicht als 1:1 behaupten. Eine ausdrückliche neue Änderung des Auftraggebers ist zur Abweichungsfreigabe erforderlich; nicht in jeder Sitzung erneut nachfragen, sondern konsolidiertes Register führen.

## 6. Fachliche und technische Grenzen
Originale unverändert; Audio-/Text-/Kontextrevisionen gehören zum selben Diktat. Audiobearbeitung nichtdestruktiv, mindestens 16 Undo-Schritte. Freigaben binden exakte Versionen. Autorenressourcenpools und Schreibpools getrennt; sichere own-Defaults, explizite zusätzliche Sichtbereiche und deny-Ausnahmen. Abnahme, Arbeitsstandtransfer und finale KIS-Signatur verschieden. KIS-Kontext nie aus Fenstername/DOM oder Transkript ableiten. KIS UNKNOWN verlangt Abgleich vor Retry. Kein Cloud-ASR, kein Browser-Spracherkennungsdienst mit ungeprüftem Backend, keine generative klinische Umschreibung. Profileinstellungen/Hotwords sind kein nachgewiesenes akustisches Training.

BYOD ohne absichtliche persistente Audio-/Textablage. Managed-Web-Offlineprofil nur explizit freigegeben; keine Browserdatenfreiheit garantieren. Token/Secrets nicht in Browserstorage oder URL. Agentidentität ist keine Sprecheridentität. Gerätequelllöschung initial aus; nur gebundene dauerhafte Quittung und freigegebene Policy erlauben enge Nachbearbeitung.

## 7. Referenzmaterial und Schutz
Arbeitsverzeichnis ausschliesslich dieser Bauauftrag oder ein daraus erzeugter Speakr-Fork. `02_Originalartefakte` nicht als Coding-Kontext indizieren oder ins Repository kopieren. Keine Alt-EXE/DLL, Datenbank, Ghidra-Projekte oder dekompilierten Implementierungen ausführen/übernehmen. Funktionale Spezifikationen und synthetische Fixtures in `sources/` dürfen gelesen werden. Dies ist eine technische Trennung, keine juristische Zertifizierung. Referenztests erfolgen separat in autorisiertem Labor; keine Lizenz-/Schutzumgehung und keine echten Patientendaten/Secrets an externe Coding-Dienste.

## 8. Ergebnisse je Slice
Code/Migrationen, zielgerichtete Unit-/DB-/Contract-/Browsertests, Istresultate, Log-/Artefaktreferenzen, Abhängigkeiten/Lizenzen, Konfigurationsänderungen und aktualisierte Paritätszeilen. Tests nur bei tatsächlicher Ausführung bestanden markieren. Mock ist kein Philips-/Entra-/KIS-/Pedal-/Office-Nachweis. Kein Produktivdeploy, keine Quelllöschung, kein AD/Entra-Schreibzugriff, kein Firmwareflash und kein Git-Push ohne separate Autorisierung.

## 9. Abnahme und Fertigmeldung
`tests/acceptance.json` enthält 252 zu erfüllende Tests (98 Basis, 101 Original-BT, 9 Grok, 24 V4-Kreuztests, 20 Webgrenztests). Paketvalidator prüft Struktur/Quellen/Hashes, nicht klinische Eignung. 1:1 darf erst nach echten Referenz-/Zielvergleichen aller 101 Funktionen und geschlossenen Gates behauptet werden. Offene WG sperren diese Behauptung. `PROGRESS.md` nach jedem Slice aktualisieren. Umfang, belegte Erfolge und offene Grenzen wahrheitsgemäss melden.
