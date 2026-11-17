# Klinik Südhang – Speakr-Fork V4, vollständiger Webclient
**Stand 17.11.2026 · implementierbare Spezifikation, keine fertige Anwendung.**

Dieses Paket konsolidiert die letzte lokale Benutzer-/Entra-Fassung V3.2, Groks ergänzenden Review und das neue DNS-Comfort-Analysepaket. Neuester Zwischeninput ist verbindlich: **kein Windows-Diktatclient; nur die Diktatabholung hat einen lokalen Agenten.**

## Feststehend
Speakr-Fork; Webaufnahme/-bearbeitung/-korrektur; Philips 9600/9620; lokaler Import-Agent; interne BYOD-Webapp; lokale Benutzer im ersten Release; Entra vorbereitet/deaktiviert; LDAP-/Windows-SSO als gesonderte serverseitige Profile; lokale ASR; Autoren- und Schreibkraftpools; DOCX/Text sowie inesKIS über freigegebenen Edge-WebView-/Kontext-/Schreibvertrag. Kein öffentliches Publishing und keine still ergänzte Cloud-ASR.

## Umfang
177 Anforderungen: 62 übernommene Klinik-/Identity-Anforderungen, 101 unveränderte fachliche PAR-Pflichtfunktionen, 14 V4-Grenzregeln. 252 Tests: 98 V3.2, 101 Original-BT, 9 Grok, 24 V4- und 20 Webtests. Alles zunächst nicht implementiert/nicht ausgeführt. Die Paritätsmatrix weist ausserdem 13 Anforderungen mit mindestens einer offenen Web-/Scope-Kollision aus. Diese sind **nicht gestrichen**, nicht automatisch genehmigt und nicht gleichbedeutend mit 13 vollständig unimplementierbaren Funktionen.

## Navigation
- `CLAUDE.md` / `START_CLAUDE.md`: auszuführender Auftrag, Rückfragepolitik, Vorrang.
- `docs/11_Paritaet_und_Webgrenze.md`: Source-Abgleich und ausdrückliche Widersprüche.
- `docs/12_Webclient_und_Importagent.md`: Web-only-Vertrag; erlaubte Agentaufgaben.
- `docs/13_Audio_Live_Editor.md` bis `docs/17_Abnahme_und_Evidenz.md`: Implementierungsdetails.
- `planning/parity-matrix.json` und `.csv`: alle 101 Zuordnungen, Module, Tests, Status.
- `planning/requirements.json`, `planning/backlog.json`, `tests/acceptance.json`: vollständige maschinenlesbare Arbeitsgrundlage.
- `contracts/`: bestehender API-/Datenvertrag plus Web-/Paritätsergänzungen.
- `sources/dns-functional/`: unveränderte funktionale Quellartefakte und synthetische Fixtures.
- `supplements/grok-review/`: erhaltene Ergänzung, soweit nicht durch neuere Vorgaben ersetzt.
- `scripts/`: optionaler Workspace-Bootstrap und read-only Paketvalidator.

## Start
Für einen neuen Fork `scripts/Initialize-SuedhangWorkspace.ps1` zuerst im Dry-Run, dann bewusst mit `-Apply` ausführen. Nur dieser Bauauftrag wird nach `clinic-spec/` kopiert, kein Originalarchiv. Für bestehenden Fork `START_CLAUDE.md` verwenden, nicht neu initialisieren. Python-Validator: `python scripts/Verify-SuedhangPackage.py --root .`.

Keine Appinstallation und keine Codeausführung von Originalsoftware erfolgen allein durch Entpacken oder Paketvalidierung. Keine Aussage über tatsächliche DS2-, Browser-, ASR-, KIS- oder Hardwareparität ohne reale Tests. Alle vorhandenen Originalartefakte liegen im vollständigen Archiv separat unter `02_Originalartefakte/`, mit Prüfsummen unter `03_Provenienz/`.
