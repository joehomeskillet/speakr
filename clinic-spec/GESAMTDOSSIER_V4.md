# Klinik Südhang – Speakr-Fork V4
## Konsolidiertes Pflichtenheft und SDD: Web-only, local-first, 101 Pflichtfunktionen

Stand 17.11.2026. Spezifikationslieferung; keine implementierte oder produktiv abgenommene Anwendung.

177 Anforderungen · 252 geplante Tests · 27 Umsetzungstasks · 13 offene Web-/Scope-Teilkollisionen. Originalanalyse und historische Pakete separat vollständig archiviert.

## Inhaltsübersicht
- 00_Entscheidungen.md
- 01_OSS_Bewertung.md
- 02_Architektur_Reuse.md
- 03_Fachregeln.md
- 04_Geraete_BYOD.md
- 05_Identity_KIS.md
- 06_Betrieb_Sicherheit.md
- 07_Lieferplan.md
- 08_V2_Uebernahme.md
- 09_Quellen.md
- 10_Local_Entra.md
- 11_Paritaet_und_Webgrenze.md
- 12_Webclient_und_Importagent.md
- 13_Audio_Live_Editor.md
- 14_Wortschatz_Profile_ASR.md
- 15_Ausgabe_KIS_und_Abnahme.md
- 16_Datenvertraege_und_Defaults.md
- 17_Abnahme_und_Evidenz.md
- 18_Web_Quellen.md
- 19_OSS_Wiederverwendung.md
- 20_Alle_101_Pflichtfunktionen.md

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


---

<!-- Quelle im Bauauftrag: docs/00_Entscheidungen.md -->

# 00 – Verbindliche Entscheidungen V4
| Entscheidung | Festlegung |
|---|---|
| Fachsystem | Kontrollierter Speakr-Fork, kein Ersatzbackend. |
| Client | Vollständig Web/PWA; keine native Diktatsoftware. |
| Lokale Ausnahme | Signierter Import-Agent, nur Abholung/Übertragung und eng begrenzte Quellenpflege. |
| Erstbetrieb | Lokale User-/Passwortverwaltung, keine externen Identitätsvoraussetzungen. |
| Entra/LDAP/IWA | Vorbereiten und synthetisch testen; separat freigeben, keine E-Mail-Identitätsverschmelzung. |
| Funktionsumfang | Alle 101 PAR-Funktionen MUST, inklusive späterer Lieferstufen. |
| Native Altbedienung | Als offene Teilkollision erfassen; Webalternative nie ohne Nachweis als 1:1 abhaken. |
| Technologie | Bestehendes Flask/SQLAlchemy/Vue/Tailwind, PostgreSQL und Speakr-Jobs. |
| Audio | Speakr-Recorder/Editor plus nichtdestruktive Revisionen; dss-codec/FFmpeg isoliert. |
| ASR/TTS | Lokale Speaches-Adapter; Live transcription-only, keine generativen Antworten. |
| Profile/Training | Echte Capabilities und Regression; Hotword-Prompt nicht als Akustiktraining verkaufen. |
| Pools | Autorenressourcenpools getrennt von Schreibkraftpools; Mehrfachmitgliedschaften und deny-Ausnahmen. |
| Freigaben | Arbeitsstand, menschliche Korrektur, optionale Abnahme und KIS-Signatur getrennt. |
| Ausgabe | Webdownload/Clipboard, serverseitige Dokumenterzeugung und KIS-Vertrag; keine OS-Fenstersteuerung. |
| Quellen | Originalartefakte bytegenau erhalten, nicht als Build-/Promptkontext verwenden. |

V4 ersetzt die vor dem Zwischeninput vorbereitete Windows-Companion-Architektur vollständig. Sie wird weder als versteckter Dienst noch als Browsererweiterung wieder eingeführt. Übernommene Altfunktionsverträge bleiben erhalten; widersprechende Architekturvorgaben aus dem Analysepaket (eigenständiger WPF-/SQLite-Neubau, Ausschluss von KIS/BYOD) gelten nicht.

Reversible Entwicklungswerte stehen in `config/dev.defaults.json`. Keine Aufbewahrungsfrist, echte Lastzahl, RPO/RTO, Rechtsfreigabe oder Herstellerfähigkeit daraus als klinisch bestätigt ableiten. Der lokale Pilot kann vor realem Entra-/KIS-Anschluss entwickelt werden. Gesamtablösung verlangt dagegen alle notwendigen funktionsbezogenen Gates und geklärte WG-Teilverträge.


---

<!-- Quelle im Bauauftrag: docs/01_OSS_Bewertung.md -->

# V4-Vorrang zur erhaltenen OSS-Bewertung
Speakr bleibt der Fork. Kein nativer Diktatclient; lokale Speakr-Benutzer zuerst. Zusätzliche OSS-Bausteine nur innerhalb Webclient/Server/Import-Agent. Neu geprüfte Details in docs/19_OSS_Wiederverwendung.md und evidence/web-sources.json. Frühere Entra-first-/Passwortfallback-Sätze werden durch docs/10_Local_Entra.md ersetzt, nicht wieder eingeführt.

# 01 – OSS-Auswahl und Abgrenzung
**Prüftiefe:** Primärdokumentation und ausgewählte Quelldateien. Kein vollständiges Codeaudit, keine Installation, keine klinische Zertifizierung. „Nicht nachgewiesen“ bedeutet nicht „existiert garantiert nicht“. Onlinedokumentation kann neuer oder älter als der gepinnte Tag sein; Implementierung orientiert sich am lokal verifizierten Tag.

## Plattformvergleich
| Projekt | Nachgewiesene Basis | Offene/fehlende klinische Funktionen | Entscheidung |
|---|---|---|---|
| **Speakr** | Flask/SQLAlchemy, Vue/Tailwind, PostgreSQL, OIDC, Aufnahme-/Transkriptfunktionen, Gruppen, API, persistente Jobs [OSS01,04–14] | Kein nachgewiesener Philips-EOL-/Importworkflow, keine klinische Freigabekette oder ines-Anbindung; ACL/Browserpersistenz anpassen | **Gewählte Basis als kontrollierter Fork**, nicht nur Konfiguration. |
| **OpenTranscribe** | FastAPI/Svelte, PostgreSQL, LDAP/OIDC und weitere Authmethoden, lokale ASR [OSS16–18] | Organisations-/Teamverwaltung und bestimmte Org-Audit-/Legal-Hold-Funktionen laut Repository nur Managed Edition; klinische Pools nicht nachgewiesen | Nicht wählen: mehr Betriebsbausteine und relevante Funktionsgrenzen. Community-Collections/-Sharing existieren trotzdem; nicht pauschal als Closed Source bezeichnen. |
| **Scriberr** | Selbst gehostete lokale Transkription; Go und SQLite [OSS19] | Passendes Rollen-/Pool-/Freigabe-/KIS-Modell im überprüften Umfang nicht nachgewiesen | Gute Einzel-/Transkriptionslösung, hier kein besserer Ausgangspunkt als Speakr. |
| **OpenScribe** | Klinische Dokumentationsoberfläche und lokale Transkription möglich [OSS20] | Fokus auf generierte klinische Notizen; Standard-Mischmodus verwendet externes LLM; kein nachgewiesener Philips-Poolworkflow | Kein Basisprodukt für originalnahe Diktatabschrift. Cloudstandard ausdrücklich nicht übernehmen. |
| Eigenes FastAPI-Produkt | Maximale Gestaltungsfreiheit | Auth, UI, Recorder, Editor, Jobstatus, Storage und Export müssten nochmals entstehen | Verworfen; nur bei nachgewiesenem unlösbarem Basiskonflikt per neuer Architekturentscheidung. |

## Wiederverwendungsentscheidung pro Baustein
| Bereich | Verwenden | Nur ergänzen/anpassen |
|---|---|---|
| Benutzer/SSO | Speakr lokale Authentisierung/Adminoberfläche, Authlib/OIDC, User und Sitzungsmechanik | Lokale Verwaltung zuerst; stabile interne User-ID, Kontopolicy, Widerruf und spätere explizite Entra-Verknüpfung. E-Mail-Autolinking entfernen. |
| Gruppen | Group/GroupMembership [OSS13] | Staff-Mitgliedschaft für Schreibpools; Autorenrouting separat. Keine Tagfreigaben als Zugriffspolitik. |
| Aufnahme | Speakr Recorder und serverseitige Sessions [OSS06] | BYOD-Klinikprofil ohne lokale Audiopersistenz, Unterbrechungs-/Manifest-/Finalisierungstests. |
| Wiedergabe/Editor | Vorhandener Player, Transkript-/Segmenteditor | Lease, Versionsschutz, Freigabebindung, klinische Oberfläche. |
| Dokumente | Bestehender Dokumentservice / python-docx | Klinikvorlage, eingefrorener freigegebener Inhalt, Exportledger. |
| Jobs | ProcessingJob und JobQueue [OSS10,12] | Originalschutz, atomare Intents, Fencing/Recovery-Nachweis; neue fachliche Jobarten. |
| Originalaudio | Bestehender Storage-Service | Unveränderliche Quellversionen ausserhalb löschbarer Derivate, Quittung/Restore. |
| DSS/DS2 | dss-codec (MIT) [OSS21–23] | Prozessadapter, Tests, Metadatenvalidierung. Decodererfolg != EOL-Nachweis. |
| ASR | Speaches (MIT) und faster-whisper [OSS24,26] | Lokale Allowlist, Modellpin, Timeouts und Ergebnisformatadapter. Keine neue ASR-API. |
| LDAP | Keycloak-LDAPS-Föderation [OSS27] | Realm-/Claimkonfiguration; keinen neuen LDAP-Client in der Fachanwendung. |
| Klinikworkflow | Kein passend abgenommener OSS-Baustein identifiziert | Kleine Domänenerweiterung für Routing, Bindung, Freigaben und Übergaben. |
| Philips-USB / inesKIS | Kein sofort einsetzbarer geprüfter OSS-Konnektor identifiziert | Eigene dünne Adapter; Geräte-/Herstellerdetails bleiben abnahmepflichtig. |

## Keine falsche Sparrechnung
Es gibt keine belastbare Prozentzahl „80 % fertig“ und keine Personentageschätzung. Die Wiederverwendung reduziert die erneute Entwicklung allgemeiner Funktionen, nicht die Verantwortung für klinische Berechtigungen, BYOD und Herstellerintegration. Der Fork enthält bewusste Patches; ein behauptetes offizielles Speakr-Klinikplugin existiert in diesem Auftrag nicht.

## Besondere Befunde
Die Speakr-Privatsphäredokumentation nennt IndexedDB-/Browserdaten und Auditgrenzen [OSS07]. Die SSO-Dokumentation beschreibt automatisches Linking anhand von E-Mail und einen Admin-Passwortfallback [OSS05]. Die Queue-Dokumentation beschreibt Aufräumverhalten bei gescheiterten Neu-Uploads [OSS10]. Diese Punkte sind **konkrete Prüfeingänge**, keine Behauptung eines vollständig analysierten aktuellen Exploits. Der gepinnte Quellcode hat Vorrang; dokumentierte Abweichungen werden per Test belegt.

OpenTranscribe wird nicht wegen des Namens oder eines pauschalen „zu gross“ verworfen. Seine zusätzlichen Infrastrukturdienste und die dokumentierten Managed-Edition-Grenzen liefern für dieses Projekt weniger klare Wiederverwendungsvorteile [OSS16–18]. Ein späterer Wechsel wird nicht automatisch ausgelöst, nur weil ein Speakr-Test angepasst werden muss.

**Präzisierung SSO:** Die aktuell gelesene Dokumentation nennt bereits `SSO_REQUIRE_VERIFIED_EMAIL=true` als Standard. Die V3-Vorgabe issuer+subject ist eine strengere, gezielte Klinikzuordnung; sie ist kein Nachweis, dass die aktuelle Standardinstallation beliebige ungeprüfte E-Mail-Adressen akzeptiert. Der Admin-Passwortfallback und erlaubte Account-Link-/Unlinkpfade werden separat geprüft [OSS05].


---

<!-- Quelle im Bauauftrag: docs/02_Architektur_Reuse.md -->

# 02 – Ein Speakr-Fork mit Webclient
## Komponenten
Ein modulares Flask-Fachbackend mit bestehenden Speakr-Modellen, PostgreSQL und vorhandener Jobverarbeitung. Serverrollen: HTTPS-Ingress, Speakr-Webprozess, separater Medien-/Jobworker aus demselben Projekt, lokale Speaches-Instanz, PostgreSQL und gesicherter persistenter Dateispeicher. Keine zweite allgemeine Queue und kein neuer S3-Cluster ohne konkreten Bedarf.

Der Webclient nutzt dieselben vorhandenen Recorder-/Editor-/Player-/Adminbausteine. Neue Clinicmodule liegen unter `src/clinic/`; UI-Erweiterungen folgen dem tatsächlichen Upstreamaufbau. Sourcepfade am gepinnten Commit zunächst prüfen. Kein Backend-/Frontend-Frameworkwechsel.

Der physische Windows-PC erhält ausschliesslich `apps/windows-import/`. Er beobachtet freigegebene Geräte/Ordner und sendet Originale mit Herkunftsdaten. Die gesamte Geräteauswahl, Metadatenkorrektur, Autorklärung und Poolarbeit erfolgt im Web über zentrale Statusdaten. Der Agent benötigt keinen eingeloggten Autor; Maschinenidentität ist kein Sprecherbeweis.

## Vertrauensgrenzen
BYOD und verwaltete Browser erhalten nur freigegebenen internen HTTPS-Zugang. Kein direkter DB-/Storage-/Speacheszugriff. Browser→Speakr-Sitzung→lokaler Audio-/ASR-Adapter. Speacheskeys bleiben serverseitig; keine api_key-Query im Browser. KIS-Einbettung bleibt der bestehende Host, kein zusätzlich installierter Projekt-WebView-Wrapper. Der Host erhält keine generische Native-/Shellbrücke.

Medienparser laufen ohne Internet, mit read-only System, begrenzten CPU/RAM/Timeouts und separaten Berechtigungen. Kontext-/Freigabe-/Identitysecrets gehören nicht in den Medienworker. Objekte werden in allen Routen, Downloads, Audio-Range-Anfragen, Jobs, Suchansichten und Legacy-Endpoints gegen dieselbe Clinicpolicy geprüft.

## Wiederverwendung statt Zweitsystem
User und Recording-IDs bleiben erhalten. Staff-Pools nutzen geeignete vorhandene Gruppen; Autorenressourcenpools erhalten expliziten eigenen Typ/Bezug. Neue Tabellen für Revisionen, Leases, Freigaben, Deliveryledger und Resource-Versionen ergänzen, nicht alte User-/Recordingtabellen duplizieren. Vorhandene ProcessingJob-/JobQueue-Klassen um fehlende sichere Claims/Fencing/Recovery ergänzen. Decoder und ASR als Adapter, nicht neue Codecs oder Inferenzserver schreiben.

## Betrieb und Forkpflege
Dedicated Klinikinstanz, keine Mischung mit privaten Meetingaufnahmen. Featureflags für öffentlichen Share, Chat/Inquire, externe Webhooks und unkontrollierte API-Tokens bleiben aus. Upstreamänderungen bewusst in Staging prüfen: ACL-/Legacyroute-Diff, Aufbewahrung, Text-/Audiorevisionen, Privacy und Identity. Quellstand/Build/Abhängigkeiten und Lizenzhinweise nachvollziehbar halten; keine Patientendaten/Secrets in veröffentlichten Fork-Quellarchiven. Das vollständige Originalartefakt-ZIP ist nicht der zur Veröffentlichung bestimmte Fork.


---

<!-- Quelle im Bauauftrag: docs/03_Fachregeln.md -->

# 03 – Fachregeln für echte Funktionsparität
## Benutzer, Pools und Berechtigungen
Autoren und Schreibkräfte sind Rollen derselben stabilen User-ID; eine Person kann beide besitzen. Autorenpools teilen Wörter, Befehle, Vorlagen und Profilauswahl. Schreibkraftpools sind Empfänger-/Arbeitsgruppen. Gleichnamige Pools dürfen keine Rechte vermischen. Mehrfachmitgliedschaften, aktiver Ressourcenpool und zeitliche Gültigkeit sind explizit.

Default Autorensicht own. Administrativ erlaubte Sicht author_pool/all wird nur unter denselben Fall-/Mandantenrestriktionen angewandt. Staff-Scopes können einzelne Autoren, Autorenpools und all mit expliziten deny-Ausnahmen verwenden. Aktionsrechte für Löschen, Archivieren, Umbenennen, Priorität, Audioexport, Reassignment und Reopen sind separat. Autor-Defaultpool ist kein Leserecht. Technische Benutzerverwaltung gewährt keine klinischen Inhaltsrechte.

Routing unterstützt all_eligible, einzelne Person, staff_pool und self_correction. Es gibt pro Auftrag einen konsistenten Bearbeitungszustand und eine exklusive Lease, nicht unkontrolliert mehrere Abschriften. Fachliche Routingrelationen und Historie erhalten; mehrere ausgewählte Exportziele sind unabhängig vom Empfängerrouting.

## Zustandsachsen
Eingang receiving/stored/invalid/quarantined; Einreichung draft/completion_unknown/submitted/withdrawn; Zuordnung author_unresolved/case_unresolved/verified; ASR pending/running/succeeded/failed/skipped; Arbeit queued/in_review/corrected/acceptance_required/accepted/completed/reopened; Delivery not_requested/pending/confirmed/unknown/failed; Retention active/due/held/deleted. UIstatus ist eine nachvollziehbare Projektion, nicht ein freier Text ohne Guards.

EOL-Filter beim Geräteimport konfigurierbar, initial an. Eine bewusste freigegebene Autoimportpolicy darf stabile nicht-EOL-Dateien eines bekannten Autors zulassen, aber niemals Dateizeit als EOL-Nachweis ausgeben. Unbekannte Autorenschaft oder widersprüchliche Quelle führt in Klärung. Quelle wird nicht bei Kopierbeginn gelöscht.

## Korrektur, Abnahme und Fertigstellen
Korrektur speichert eine neue Text-/Audiobindung. Gleichzeitige Saves benötigen If-Match und gültige Leasegeneration. Optionale Zusatzabnahme mit Autor-/Autorenpoolrechten und Ausnahmen entspricht PAR-WF-009/010. Auch ohne Zusatzabnahme wird ungeprüfter KI-Text nicht automatisch final. Rejection erhält Begründung und zuständigen Bearbeiter. Fertigstellen und Ausgabe sind getrennte Seiteneffekte; Abschluss ohne Export bleibt möglich.

Ein ausdrücklich gewählter Arbeitsstand darf unter Policy exportiert/kopiert werden, aber nicht als medizinisch final signiert gelten. Finale klinische Ausgabe verlangt gültigen Kontext und passende aktuelle Freigabe. KIS bekommt den freigegebenen Herstellerstatus, initial Entwurf; keine künstliche elektronische Signatur. Mehrere gewählte Ausgabeziele protokollieren Einzelstatus. Ein UNKNOWN-Ziel erlaubt keinen blinden Wiederholungsversand.

## Änderungen und Schutz
Originalbytes bleiben unverändert; Insert/Overwrite/Trim erzeugt Audioeditrevision. Text-/Audio-/Falländerung invalidiert aktive Freigaben. Reopen fertiger Diktate ist berechtigt und erzeugt nachvollziehbare Revision; bereits bestätigte KIS-Dokumente nur per freigegebenem Nachtrag korrigieren. Geschützte Diktate benötigen zusätzliche Entsperrprüfung, niemals statt ACL. Passwort/Gerätekey nicht im Log oder Prompt. Kein Passwortschutz aus selbst entwickelter Kryptografie.

## Konfiguration und Listen
Zentral zwingende Policy gewinnt vor Pooldefault und persönlicher Auswahl; nicht fixierte Einstellungen bleiben persönlich. Bis 20 gespeicherte Filter, Multi-Gruppierung, Layout, Metadatensuche und XML-Spalten als kontrollierte Projektionen. XML ohne DTD/XXE, RegEx mit Längen-/Zeitlimits, Vorschau und Versionsnachweis. Keine Regexzuordnung von Patienten-/Integrations-IDs. Benachrichtigungen ohne Patientendaten im Browser-/OS-Pushtext. Optionale Nutzungsstatistik separat vom Sicherheits-/Freigabeaudit.


---

<!-- Quelle im Bauauftrag: docs/04_Geraete_BYOD.md -->

# 04 – Geräteimport und interne Browseraufnahme
## Import-Agent ist die einzige lokale Projektanwendung
.NET-10-Dienst am physischen PC, minimale Rechte, signiertes Paket und genehmigter Updateweg. Er erkennt nur zentral freigegebene Geräte/Volumes/Ordner, erstellt stabile Snapshotkopien, liest vorhandene Metadaten, verschlüsselt einen begrenzten Spool, berechnet Prüfsummen, lädt wiederaufnehmbar hoch und meldet Status. Ereignisgetriggert mit 15-s-Abgleich; drei unveränderte Metadatenmessungen im Abstand von zwei Sekunden sind technische Startwerte, kein EOL-Beweis.

Geräte-ID, Headerautor und Registryzuordnung getrennt. Geteilte Geräte brauchen bestätigte Author-Metadaten oder Klärung im Web. Bei aktivem unbekanntem Autor sichere Aufnahme nicht verwerfen oder dem Windowsnutzer zuweisen. Idempotenz aus Quellnamespace/Aufnahme-ID/Originalrevision; reiner Dateihash nicht als globale Diktatidentität. Andere Karte, Headeränderung, Umbenennung und Reopen getrennt testen.

Dropfolder: mehrere explizite Pfade und Patterns, per Default nicht rekursiv wie Originalvertrag. Keine Reparse-/Symlinkflucht. Mehrfachimport und Metadatenwahl im Web; Agent verarbeitet nur signierte/gebundene Importaufträge. Lokale Aufnahme-, Playback-, Office-, Mikrofon-/Pedal-, Tokenpass-through-, Shell- oder universelle Dateiproxyfunktionen sind verboten.

## Quellenpflege ist keine Gerätesteuerung
Optionaler Backupordner nur für Originalsnapshots zugelassener Quellen. Nach freigegebener dauerhafter Serverquittung und aktivierter Löschpolicy darf exakt dieselbe Quellversion entfernt werden. Initial aus; Quellhash/Device-/Volumeidentität unmittelbar vor Delete erneut prüfen. Manuelle Quelllöschung benötigt ausdrückliche Webbestätigung mit gebundener Quelle und Audit. Keine arbiträren Deletepfade.

PIN/Autorenlisten/Aufnahmeverschlüsselung/Energiesparwerte setzen und Firmwareflash liegen ausserhalb des Agenten. Inventar und Firmwarevergleich können angezeigt werden. PAR-IMP-009/010 bleiben Pflicht und offener WG-Teilkonflikt, bis ein zulässiger direkter Browser-/Herstellerweg nachgewiesen ist oder der Auftraggeber die Teilklausel ändert. Alte SpeechExec-Adminsoftware ist höchstens dokumentierter Restbetrieb, keine Erfüllung durch den Fork.

## Browser
Eigener Webrecorder für verwaltete Geräte und internes BYOD. MIME per Capabilitytest, Mikrofonfreigabe und Trackstatus beachten. Audio wird an serverseitige Speakr-Sessions gesendet. Unregelmässige Chunkintervalle, fehlende Finalisierung, Verbindungs-/Anruf-/Sperrbildschirmereignisse werden sichtbar behandelt. Webaufnahme auf Mobilgeräten als Vordergrundbetrieb abnehmen; keine garantierte Hintergrundaufnahme.

BYOD: keine absichtliche Audio-/Textpersistenz, keine Dokumentdownloads. Verwalteter Webclient online ebenfalls ohne persistente Audioablage. Für die Originalfunktion autarker Recorder ein eigenes Managed-Web-Offlineprofil mit explizitem Gate vorbereiten; alle Fachfunktionen bleiben Browserfunktionen. Ohne Gate ist Offlinepersistenz aus, nicht durch einen nativen Ersatz aufgefangen.

Unbekannte Browser-/Gerätefähigkeiten geben capability_unavailable statt Erfolg zurück. APIs des Betriebssystems werden nicht durch Agentenrechte in den Browser verlängert. Host-/RDS-/ICA-Mikrofon- oder HID-Umleitung muss separat getestet werden; erfolgreicher USB-Upload beweist keine Remote-Diktatsteuerung.


---

<!-- Quelle im Bauauftrag: docs/05_Identity_KIS.md -->

> V4-Vorrang: Web-only und Import-Agent-Grenze gelten verbindlich. Local-first aus V3.2 bleibt. Neue PAR-Rechte/Policies, WG-Teilkonflikte und Quellenpflege in den V4-Katalogen ergänzen frühere engere Defaults; historische Quellen sind keine Produktfreigabe.

# 05 – Identität, LDAP und inesKIS-WebView
## Lokales Login zuerst, Entra vorbereitet
Normativ gilt `docs/10_Local_Entra.md`. Vorhandene Speakr-Benutzerverwaltung, lokale Authentisierung und Sitzungsmechanik wiederverwenden. Der erste Betriebsmodus ist `local`: kein Entra, kein Keycloak, kein LDAP und kein SMTP als Voraussetzung.

`hybrid` ergänzt später tenantgebundenes Entra-OIDC mit vorhandenem Authlib-Code. Eine interne `user.id` bleibt über sämtliche Anmeldewege unverändert. Externe Identität ist validierter Issuer + Subject; Tenant/Object kann für explizite Vorprovisionierung dienen. Keine automatische Verknüpfung über E-Mail, keine JIT-Registrierung und kein universeller Admin-Passwortbypass.

Rollen und Pools werden initial in der Anwendung verwaltet. Pro Konto gilt local_only, entra_only, local_and_entra oder device_only. Passwortreset ändert diese Policy nicht. Lokale Administratoren benötigen eigene Verwaltungsrechte, nicht automatisch klinischen Lesezugriff. Die LDAP-Fähigkeit bleibt als separates späteres Keycloak-/LDAPS-Profil bestehen [OSS05, OSS27, OSS31–38].

Entra-Adapter, sichere Linkaufträge und negative OIDC-Tests werden jetzt implementiert, jedoch erst nach realer Providerabnahme aktiviert. Fehlende Tenantdaten blockieren weder Entwicklung noch lokale Anmeldung.

## inesKIS: drei Adapter, ein kanonischer Vertrag
1. `MockKisAdapter`: stateful synthetisches Register, kann Speicherung mit anschliessendem Timeout simulieren. Nur Dev/Test.
2. `DisabledKisAdapter`: produktive Voreinstellung ohne Herstellernachweis; meldet `CAPABILITY_UNAVAILABLE`, tut keinen Netzwerkzugriff.
3. `InesKisAdapter`: erst gegen dokumentierten freigegebenen Vertrag implementieren. Kein erfundenes FHIR-/REST-/HL7-Endpoint.

Kanonische Methoden: `capabilities()`, `resolve_context(grant, actor)`, `authorize_case(context, actor, action)`, `deliver(frozen_payload, delivery_key)`, `lookup(delivery_key)`, `create_amendment(parent_receipt, frozen_payload)`. Das sind **eigene Schnittstellen**, keine Zusage entsprechender ines-Methodennamen.

## WebView und Kontext
Edge WebView bleibt Ziel. Keine eigene neue Desktop-WebView-App im Standardscope. Reale Hostversion/Origin, Benutzerprofile, externe Navigation, Mic/Clipboard/Downloads und SSO im Herstellerhost abnehmen. Bei fehlendem Hostzugang denselben Webscreen mit Mockkontext im Browser testen; das zählt nur als UI-/Contracttest.

Vorhandener Kontext wird über vertrauenswürdigen serverseitigen Austausch in einen opaken Einmal-Grant umgewandelt. Grant TTL 60 Sekunden als Devdefault, an vorhandene interne user.id, Sitzung, Mandant, Zweck und Zielinstanz gebunden; externe KIS-Benutzerreferenz über freigegebenes Mapping auflösen, nicht mit einem OIDC-Subject gleichsetzen. Grant ist kein Fallrecht. API prüft patient/case-Zusammengehörigkeit, klinische Einschränkungen und aktuelle Rolle. Code nicht in normalen Logs/Referrern. Lieferantenprotokoll kann anders aussehen; Adapter muss dieselben Sicherheitsinvarianten erreichen.

Diktat A bleibt am ursprünglichen Kontext A, wenn im KIS B geöffnet wird. Ein bewusster Wechsel erzeugt neue Kontextrevision und invalidiert Freigaben. Kein Fensterüberschrift-/DOM-/Clipboard-Parsen. Offlinediktate von Philips dürfen nicht nachträglich den offenen Fall des Docking-PCs erben.

## Ausgabevertrag
Export erstellt eingefrorenen Payload mit Diktat-ID, Audio-/Text-/Kontextversion, SHA-256, Dokumenttyp, Empfängerfall, Autor, Reviewer, Freigabe und Modus. Transaktionale Delivery-ID vor Netzwerkaufruf. Idempotency-Key je eingefrorenem Export und Ziel.

`CONFIRMED` nur mit hinreichend eindeutigem Herstellernachweis, z.B. externe Dokument-ID plus passendem Status/Versionsbezug. HTTP-202 heisst allein nur angenommen. Timeout nach möglichem Effekt => UNKNOWN. Nur bestätigtes Nichtvorhandensein oder ein tatsächlich nachgewiesener Hersteller-Idempotenzvertrag erlaubt erneute Neuanlage. Das gilt auch nach Restore. Bei UNKNOWN kein automatischer Zweitexport. Bewusste Notfallausnahme erfordert berechtigte, protokollierte Entscheidung.

## Sechs gebündelte Herstellerlieferungen statt Dauerfragen
Host-/Runtimevertrag; SSO-/Benutzerwechselvertrag; Patienten-/Fall-/Rechtevertrag; Dokument-/Entwurfs-/Signaturvertrag; Quittungs-/Lookup-/Nachtragsvertrag; Testsystem-/Lizenz-/Supportvertrag. Claude baut die Adaptergrenzen und Negativtests bereits vorher und markiert reale Integration als blockiert.

## Sitzungszustand ausdrücklich prüfen
Vorhandene OIDC-/Flask-Login-Mechanik wiederverwenden, aber nicht behaupten, jede Upstreamsession sei schon serverseitig. OAuth-/Refresh-Tokens dürfen im Klinikprofil nicht im Browser-Webstorage oder in nur signierten, lesbaren Cookies liegen. Im Checkout Token-/Sessionablage prüfen und nötigenfalls mit einer etablierten, zur Basis passenden serverseitigen Sessionbibliothek über PostgreSQL ergänzen. Cookie enthält nur einen opaken, widerrufbaren Sitzungsbezug; keine selbst entwickelte Kryptografie. S00 dokumentiert Bibliothek/Pin, S03 prüft Logout, Widerruf, Sessionrotation und mehrere Instanzen.


---

<!-- Quelle im Bauauftrag: docs/06_Betrieb_Sicherheit.md -->

> V4-Vorrang: Web-only und Import-Agent-Grenze gelten verbindlich. Local-first aus V3.2 bleibt. Neue PAR-Rechte/Policies, WG-Teilkonflikte und Quellenpflege in den V4-Katalogen ergänzen frühere engere Defaults; historische Quellen sind keine Produktfreigabe.

# 06 – Betrieb, Sicherheit, Lieferkette und Lizenz
## Entwicklungsprofile
`dev-mock`: synthetische Daten, Keycloak, Mock-ASR, Mock-KIS, simuliertes Geräteverzeichnis. Ohne Internet nach Bereitstellung der Images. Kein Erfolgsetikett für reale Integration.
`integration-local`: echte lokale Speaches-ASR und dss-codec, nur erlaubte Testdaten. CPU genügt für Smoke, Laufzeit nicht als Klinik-SLO ausgeben.
`production`: Start nur mit produktivem Policyoverlay und nachgewiesenen Gates. Mockauth, Mock-KIS, feste Demo-Secrets, offene Defaultkonten oder ungelockte Artefakte führen zum Abbruch.

## ASR-Defaults
Nur Transkription, keine Übersetzung, keine Zusammenfassung, keine Titelgenerierung mit LLM, keine Chat-/RAG-/Sprecheridentifikation. Sprache Deutsch, deterministic best-effort Parameter (Temperatur 0; keine Zusage mathematischer Deterministik). CPU-Smoke mit kleinem Whisper-Modell; Qualitätsevaluation mit `large-v3` als Kandidat. Modellrepo/-revision, Datei-Hashes, Lizenz, Quantisierung, Decoder, Parameter und Wort-/Segmentzeiten je Lauf erfassen.

NVIDIA/CUDA ist ein optionaler getesteter Beschleunigungspfad von faster-whisper [OSS26]. Keine Behauptung, private Arc-/AMD-Karten seien Klinikressourcen. Fehlende GPU führt zu CPU/Stub, nicht zu Cloud. VAD zunächst aus, bis Tests gegen leise Sprache, Negationen und Pausen Qualität belegen. Abschnitte mit unklarem Inhalt bleiben zur Kontrolle; kein LLM darf medizinische Lücken ergänzen.

## Original-/Fehlerschutz
Angenommene Rohdateien und Quellmetadaten werden getrennt von regenerierbaren Audioderivaten gehalten. Retention, failed-upload cleanup, reprocess, cancel, account-delete und admin-delete müssen Klinikschutz berücksichtigen. Der Originalstore darf von normalen Transkriptionsjobs nicht gelöscht werden. Löschung nur durch separaten berechtigten Lebenszyklusdienst unter Legal-Hold-/Retentionregel. Produktivfristen sind nicht mit Demo-Cleanup gleichzusetzen.

## Audit
Bestehende Auditbasis wiederverwenden, aber Fachübergänge synchron/atomar mit Statusänderung und append-only Berechtigung speichern. Ein best-effort UI-Audit allein genügt nicht für Freigaben oder Exportledger. Technische Logs ohne Audio, Text, Dateinamen, Fallnummern, Token und Keys. Leselogging und Fachaudit getrennt. Backup-/Betriebsadmins haben Restprivilegien; keine absolute Revisionssicherheitsbehauptung.

## Infrastruktur
Serverseitige Datenträgerverschlüsselung; getrennte Secrets; restriktive DB-/Filesystemrechte; TLS; Trusted-Proxy-Regeln; CSP/CSRF; Rate-/Upload-/Ressourcenlimits; keine externen CDN-/Analytics-/Crashupload-/Webhookziele. Egress-Allowlist getrennt: Web nur genehmigter IdP und benötigte interne Dienste; ASR/Medienworker kein Internet; KIS-Adapter nur Zielsystem. Updates/Modelle kontrolliert herunterladen und vor Echtbetrieb intern bereitstellen. Keine pauschale Freigabe aller privaten IPs als vertrauenswürdig.

Compose ist Bereitstellung für Dev/Pilot, keine HA-Garantie. Kein unnötiger Redis/MinIO/OpenSearch-Neuaufbau. Mehrere Webworker erst nach Nachweis konsistenter Rate-Limits/Sessions/Job-Ownersteuerung. Devdefault ein Webprozess und ein Queue-Owner; kein Multiworker-Claim ohne Test.

## Backup und Betriebsübergabe
DB/PITR, Audiooriginale, Versionen, Audit-/Deliveryledger, Konfigurationspins und Schlüsselwiederherstellung gemeinsam testen. `STORED` ist kein standortübergreifendes RPO 0. RPO/RTO pro Ausfallart und Datenklasse im Produktionsoverlay; fehlen sie, kein Echtdatenbetrieb. Nach Restore KIS-Ausgänge abgleichen, bevor neue Lieferungen erlaubt werden. Wiederanwendung freigegebener Löschentscheidungen und Legal Holds berücksichtigen.

Pilotmonitoring: Agentheartbeat, Importfehler, ungeklärte Autor-/Fallzuordnung, Queuealter, ASRfehler, Speicher/Spool, KIS-UNKNOWN, Backupalter und Zertifikatsablauf. Runbooks für jede Klasse, ausführbar durch Stellvertretung. Alt-/Neusystem dürfen nicht gleichzeitig denselben Geräteauftrag importieren/löschen/versenden. Abschaltung erst nach Ende-zu-Ende-Fachabnahme.

## OSS-Lizenzen
Speakr wird unter AGPL-3.0 weiterverwendet [OSS15]. Das Quellcodeangebot an Benutzer der veränderten Netzwerkversion wird als Lieferanforderung eingeplant, einschliesslich passenden Build-/Änderungsstands; der Quellcode muss deshalb nicht automatisch in ein öffentliches GitHub-Repository mit Klinikdaten. Verpflichtungen dürfen aber nicht durch eine zusätzliche pauschale Geheimhaltung der AGPL-Quellen unterlaufen werden. Interne Nutzung ist kein allgemeiner Freibrief, Lizenzpflichten zu ignorieren. Rechtliche Detailprüfung vor Betrieb bleibt erforderlich.

dss-codec und Speaches nennen MIT [OSS21, OSS24]. FFmpeg-Lizenz hängt vom konkreten Build ab; Distributionshinweise, verwendete Codecoptionen und korrespondierende Lizenz-/Quellanforderungen im SBOM erfassen. Modelldateien haben eigene Lizenzen. Keine „alles MIT“-Behauptung. Signierte Agentenpakete, Image-Digests, Lockfiles, Lizenzen und Securityscan gehören zur Abgabe.

## Datenschutz-/Fachfreigabe
V2-Schutzbedarf bleibt bestehen. Diese technische Spezifikation setzt keine rechtlichen Aufbewahrungsfristen, behauptet keine medizinprodukterechtliche Einstufung und genehmigt kein privates Endgerät. Produktionsgate erfordert Datenschutz-/Security-/Fachentscheidungen, Benennung des Service Owners und realen Restore. Entwicklung mit synthetischen Fixtures geht währenddessen weiter.


---

<!-- Quelle im Bauauftrag: docs/07_Lieferplan.md -->

# 07 – Lieferplan und Reihenfolge
## Frühe Nachweise statt neuer Grundsatzfragen
S00: gepinnten Speakr-Build/Tests und vorhandene Erweiterungspunkte prüfen. P00: 101 Funktions-/Testverträge, Scopevorrang und Originaltrennung. P03: Browseraufnahme, Richtext, Mikrofonwahl, WebHID-/Tastaturgrenzen, Clipboard/Pickers und offene WG untersuchen. P10: echte Lexikon-/Aussprache-/Trainingscapabilities früh prüfen, nicht am Projektende.

Danach S01–S05: zentrale Policy, Original-/Jobschutz, lokale Userverwaltung und ein vollständiger synthetischer Verlauf. P01/P02/P04 liefern Autoren-/Schreibpools, ACL-/Workflowparität, zentrale Policies/Vorlagen/Metadaten. P05/P06/P08 liefern Webrecorder, Live, Editor, Audiooperationen und Korrektur. P07 liefert Import-Agent und Web-Importzentrale. P09/P10 liefern Wortschatz/Befehle/Profile/ASR. P11/P12 liefern Dokumente, Webhardware und Statistik. Vorhandene S06–S12 bleiben für ASR, Agent, BYOD, KIS und Betrieb/Abnahme erhalten; referenzierte Tasks nicht parallel doppelt implementieren.

`planning/backlog.json` ist die maschinenlesbare Abhängigkeitsliste. S-Tasks härten die bestehende Klinikbasis; P-Tasks erweitern die Parität. Jedes Modul implementiert einmal die gemeinsame Fachlogik. Additive Tests bauen auf denselben Entitäten auf. Nach lokalen Builds darf Claude die nächste unabhängige Aufgabe ohne erneute Freigabefrage bearbeiten.

## Releasegates
Ein lokaler synthetischer Prototyp braucht keine Entra-Secrets/Philips-Hardware. Ein echter Gerätepilot braucht Geräte-/Identity-/Daten-/Betriebsnachweise. BYOD und KIS erhalten eigene Freigaben. Ein vollständiger Ersatz verlangt alle 101 Original-BT sowie Datenschutz, Backup/Restore, fachliche Freigabe und Migration/Cutover. Native Webkonflikte und nicht nachgewiesene ASR-Trainingseigenschaften bleiben sichtbare Sperren für diese vollständige Aussage.

Eine Teilimplementierung ist kein genehmigter Funktionsverzicht. Kein automatischer Wechsel auf einen Windows-Diktatclient. Fachliche Budget-/Scopeentscheidungen gebündelt im Register, nicht als repetitive Fragen in jedem Codinglauf.


---

<!-- Quelle im Bauauftrag: docs/08_V2_Uebernahme.md -->

# 08 – Übernahme ohne stillen Verlust
V1/V2 bleiben im Originalarchiv. V3/V3.2 bestimmen Speakr-Fork und lokale Identitybasis; Groks Ergänzung bleibt erhalten. V4 übernimmt alle 62 R-Anforderungen und erweitert gezielt vorhandene Regeln: EOL-Filter on/off, zusätzliche Autoren-Sichtbereiche, Autorenressourcenpools, Empfängerwahl, optionale Zusatzabnahme und kontrollierte Quellenpflege sind nun ausdrücklich spezifiziert. Own-/EOL-/Delete-off bleiben sichere Defaults, nicht der maximal erlaubte Funktionsumfang.

Alle 101 PAR-Anforderungen aus dem neuen Analysepaket werden MUST. Die Quelle selbst bleibt unverändert, inklusive ursprünglicher Sollkennzeichnung, Milestones und manueller Evidenzstufe. In der aktiven Matrix ist ihre Pflichtpriorität geändert. Die 101 Original-BT werden nicht verkürzt. Quelle erlaubt keine heimliche Aufgabe bisheriger BYOD-/KIS-/Entra-/LDAP-Funktionen und keinen eigenständigen Windowsneubau.

Der neueste Zwischeninput beseitigt die zwischenzeitlich entworfene native Companionlösung. Dieses V4 enthält keine solche Implementierungsanweisung. Browsergrenzen werden nicht durch den Import-Agenten umgangen. Native Altfunktionsanteile bleiben im WG-Register unaufgelöst, bis ein geprüfter erlaubter Webweg oder eine ausdrückliche neue Umfangsentscheidung vorliegt.

Historische Aussagen in `sources/`, `supplements/` oder älteren Original-ZIPs sind Evidenz. Die aktive Wahrheit steht in README/CLAUDE, V4-Katalogen, V4-Verträgen und dem Konfliktregister. Kein automatisches Zurückkopieren einer älteren CLAUDE.md.


---

<!-- Quelle im Bauauftrag: docs/09_Quellen.md -->

> V4-Vorrang: Web-only und Import-Agent-Grenze gelten verbindlich. Local-first aus V3.2 bleibt. Neue PAR-Rechte/Policies, WG-Teilkonflikte und Quellenpflege in den V4-Katalogen ergänzen frühere engere Defaults; historische Quellen sind keine Produktfreigabe.

# 09 – Quellen und Evidenz

Prüfdatum 17.11.2026. Primärquellen, selektive Codeinspektion. Nicht alle Online-Dokumentationen sind Release-spezifisch; der Code am gepinnten Commit entscheidet. Keine dieser Quellen ist eine lokale Abnahme.

## OSS01 – Speakr Repository
https://github.com/murtaza-nasir/speakr

Flask/SQLAlchemy, Vue/Tailwind, PostgreSQL, AGPL; Kandidat, kein Klinikprodukt.

## OSS02 – Speakr v0.11.1 Release
https://github.com/murtaza-nasir/speakr/releases/tag/v0.11.1

Am 17.11.2026 sichtbare Release-Baseline; Logging-Korrekturen. Keine eigene Laufzeitprüfung.

## OSS03 – Speakr konkreter Commit
https://github.com/murtaza-nasir/speakr/commit/48d3018fb7d80b77f897bedd86bcc89b5034d1f5

Commit-Ziel des sichtbaren Release-Links.

## OSS04 – Speakr Installation / PostgreSQL
https://murtaza-nasir.github.io/speakr/getting-started/installation/

PostgreSQL über SQLALCHEMY_DATABASE_URI dokumentiert.

## OSS05 – Speakr SSO
https://murtaza-nasir.github.io/speakr/admin-guide/sso-setup/

OIDC/Entra/Keycloak; dokumentiertes E-Mail-Linking ist für Klinikmodus zu ersetzen.

## OSS06 – Speakr Recording Sessions
https://murtaza-nasir.github.io/speakr/admin-guide/recording-sessions/

Vorhandene serverseitige Aufnahme-/Chunk-Sessions, nicht pauschal BYOD-konform.

## OSS07 – Speakr Privacy
https://murtaza-nasir.github.io/speakr/admin-guide/privacy/

Browserpersistenz, Audit-/Logging-Grenzen und nicht behauptete Compliance.

## OSS08 – Speakr Gruppen
https://murtaza-nasir.github.io/speakr/admin-guide/group-management/

Gruppenfreigaben sind keine klinischen Schreibpools.

## OSS09 – Speakr API
https://murtaza-nasir.github.io/speakr/user-guide/api-reference/

Vorhandene Aufnahmen-/Transkript-API; klinische Kommandos sind Erweiterungen.

## OSS10 – Speakr Queue
https://murtaza-nasir.github.io/speakr/advanced/job-queue-system/

Persistente Jobbasis vorhanden; Doku und gepinnter Code getrennt bewerten.

## OSS11 – Speakr App am Tag
https://raw.githubusercontent.com/murtaza-nasir/speakr/v0.11.1/src/app.py

Registrierung von Blueprints, bestehende Owner-/Share-ACL, db, services, init_db.

## OSS12 – Speakr Queue am Tag
https://raw.githubusercontent.com/murtaza-nasir/speakr/v0.11.1/src/services/job_queue.py

Dauerhafte ProcessingJob-Queue; keine zweite allgemeine Queue entwickeln.

## OSS13 – Speakr Gruppenmodell am Tag
https://raw.githubusercontent.com/murtaza-nasir/speakr/v0.11.1/src/models/organization.py

Group und GroupMembership wiederverwenden; Klinikrollen separat.

## OSS14 – Speakr Dockerfile am Tag
https://raw.githubusercontent.com/murtaza-nasir/speakr/v0.11.1/Dockerfile

Python-3.11-Baseline, Lockfiles, Offline-Assets; Bestand erhalten, Securityscan zusätzlich.

## OSS15 – Speakr Lizenz am Tag
https://raw.githubusercontent.com/murtaza-nasir/speakr/v0.11.1/LICENSE

AGPL-3.0-Lizenztext; Quellcodeangebot und Hinweise erhalten.

## OSS16 – OpenTranscribe Repository
https://github.com/attevon-llc/OpenTranscribe

Alternative; Organisations-/Teamverwaltung und bestimmte Org-Funktionen als managed-only beschrieben.

## OSS17 – OpenTranscribe Backend
https://github.com/attevon-llc/OpenTranscribe/blob/master/backend/app/README.md

FastAPI, verschiedene Auth-Verfahren einschliesslich LDAP/OIDC; keine Annahme klinischer Schreibpools.

## OSS18 – OpenTranscribe FAQ
https://docs.opentranscribe.app/docs/faq/

AGPL, PostgreSQL/Redis/MinIO/OpenSearch; breiterer Betriebsstack.

## OSS19 – Scriberr
https://github.com/rishikanthc/Scriberr

Offline-Transkriptionsapp, Go und SQLite; kein geprüfter passender Klinikworkflow.

## OSS20 – OpenScribe
https://github.com/sammargolis/OpenScribe

Medizinischer Scribe; Standard-Mischworkflow sendet Transkript an externes LLM, nicht ungeprüft einsetzen.

## OSS21 – dss-codec
https://github.com/hirparak/dss-codec

MIT, Rust DSS/DS2->WAV, Passwortunterstützung; keine garantierte EOL-/Philips-Fachsemantik.

## OSS22 – dss-codec Releases
https://github.com/hirparak/dss-codec/releases

Bei Prüfung keine Releases; Commit beim ersten Bootstrap auflösen und festschreiben.

## OSS23 – dss-codec README
https://raw.githubusercontent.com/hirparak/dss-codec/master/README.md

CLI und DSS_CODEC_PASSWORD dokumentiert; kein eigener Codec nötig.

## OSS24 – Speaches
https://github.com/speaches-ai/speaches

MIT, lokale OpenAI-kompatible ASR-API mit faster-whisper, CPU/GPU.

## OSS25 – Speaches Releases
https://github.com/speaches-ai/speaches/releases

Versionen vorhanden; Lock-Schritt muss tatsächlichen Commit und Image-Digest erfassen.

## OSS26 – faster-whisper
https://github.com/SYSTRAN/faster-whisper

CPU- und NVIDIA/CUDA-Ausführung dokumentiert; Hardware/Qualität lokal benchmarken.

## OSS27 – Keycloak Administration
https://www.keycloak.org/docs/latest/server_admin/index.html

OIDC und LDAP/AD-Föderation; kein LDAP-Passwortcode im Fachprodukt.

## OSS28 – .NET Lifecycle
https://learn.microsoft.com/en-us/lifecycle/products/microsoft-net-and-net-core

.NET 10 unterstützt bis 14.11.2028; .NET 8 seit 10.11.2026 ausser Support.

## OSS29 – Speakr Auth-Routen am Tag
https://raw.githubusercontent.com/murtaza-nasir/speakr/v0.11.1/src/api/auth.py

Vorhandenes src.auth.sso und Authlib-Integration; Protokollcode weiterverwenden.

## OSS30 – Speakr Konnektoren
https://murtaza-nasir.github.io/speakr/advanced/custom-connectors/

Vorhandene Transkriptions-Konnektoren; Speaches-Anbindung ist lokal zu testen.


# Ergänzende Identity-Quellen V3.2

## OSS31 – Speakr lokale Benutzerverwaltung
https://murtaza-nasir.github.io/speakr/admin-guide/user-management/

Dokumentierte Benutzeranlage und Verwaltung; kein Nachweis aller geforderten klinischen Kontrollen.

## OSS32 – Speakr User-Modell am festgelegten Commit
https://raw.githubusercontent.com/murtaza-nasir/speakr/48d3018fb7d80b77f897bedd86bcc89b5034d1f5/src/models/user.py

Vorhandene Integer-User-ID, Passwort-/SSO-Felder; Passwortspalte deklariert String(60).

## OSS33 – Speakr Auth-Routen am festgelegten Commit
https://raw.githubusercontent.com/murtaza-nasir/speakr/48d3018fb7d80b77f897bedd86bcc89b5034d1f5/src/api/auth.py

Lokale Auth-/Reset-/Änderungspfade und vorhandene Bibliotheken; gezielte Härtung statt zweiter Auth-Engine.

## OSS34 – Werkzeug Security Helpers
https://werkzeug.palletsprojects.com/en/stable/utils/

Hash-/Verify-Funktionen und scrypt-Unterstützung.

## OSS35 – OWASP Authentication Cheat Sheet
https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html

Passwortlänge, Throttling, Reauth und sichere föderierte Verknüpfung.

## OSS36 – OWASP Password Storage Cheat Sheet
https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html

Etablierte Password-Hash-Verfahren und Parametergrundlagen.

## OSS37 – OWASP Forgot Password Cheat Sheet
https://cheatsheetseries.owasp.org/cheatsheets/Forgot_Password_Cheat_Sheet.html

Einmaligkeit, Ablauf, sichere Tokens und Sitzungswiderruf.

## OSS38 – Microsoft Entra ID token claims
https://learn.microsoft.com/en-us/entra/identity-platform/id-token-claims-reference

Issuer/Audience/Tenant-Prüfung; E-Mail/UPN sind keine stabilen autoritativen Identitätsschlüssel.


---

<!-- Quelle im Bauauftrag: docs/10_Local_Entra.md -->

> V4-Vorrang: Web-only und Import-Agent-Grenze gelten verbindlich. Local-first aus V3.2 bleibt. Neue PAR-Rechte/Policies, WG-Teilkonflikte und Quellenpflege in den V4-Katalogen ergänzen frühere engere Defaults; historische Quellen sind keine Produktfreigabe.

# 10 – Lokale Benutzerverwaltung zuerst, Entra vorbereitet
**Version 3.2 · 17.11.2026 · Verbindlicher Entwicklungsauftrag; keine Produktivfreigabe.**

## 1. Änderung und Vorrang
Neue Benutzeranforderung: Die Plattform besitzt eine **eigene lokale Benutzerverwaltung und eine vorbereitete Entra-Anbindung**. Der erste ausgelieferte Betriebsmodus verwendet ausschliesslich lokale Anwender. Entra wird bereits implementiert und mit isolierten Testidentitäten geprüft, bleibt aber deaktiviert, bis ein realer Tenant eingerichtet und abgenommen ist. Es ist kein reiner UI-Platzhalter.

Diese Entscheidung ersetzt nur frühere OIDC-only-/Entra-first-/Keycloak-Pflichtvorgaben. Speakr-Fork, Philips 9600/9620, interne BYOD-Nutzung, Schreibpools, lokale ASR, Freigaben, beide Ausgabekanäle, inesKIS via Edge WebView, Sicherheitsinvarianten und sämtliche nicht betroffenen V3-/Grok-Anforderungen bleiben erhalten. Groks Originalreview ist historische Eingabe, kein vorrangiger Ausführungsauftrag.

**Reihenfolge bei Konflikten:** neuester expliziter Benutzerauftrag → dieses Identity-Kapitel und seine aktualisierten Verträge/Kataloge → übrige V3-Sicherheitsregeln → übrige V3-Defaults → additive Grok-Vorgaben → historische V2-Unterlagen. Lokale Anmeldung ist jetzt ein regulärer, ausdrücklich erlaubter Anmeldeweg; ein ungeprüfter Passwortfallback für SSO-exklusive Konten bleibt verboten.

## 2. Betriebsmodi
| Eigenschaft | Erstes Release: `local` | Später: `hybrid` |
|---|---|---|
| Anmeldeseite | E-Mail/Loginname und lokales Passwort | Lokale Anmeldung plus „Mit Microsoft Entra anmelden“ |
| Benutzerverwaltung | In derselben Speakr-Instanz | Dieselbe Verwaltung und dieselben internen Benutzer-IDs |
| Entra-Adapter | Vorhanden, deaktiviert, isoliert getestet | Aktiv für freigegebenen Tenant und explizit verknüpfte Konten |
| Entra-Client-ID/Secret | Nicht erforderlich | Lokal aus freigegebener Konfiguration/Secret-Referenz |
| Keycloak/LDAP | Kein benötigter Dienst | Eigenständiges späteres LDAP-Föderationsprofil; keine Pflicht für Entra |
| SMTP | Keine Startvoraussetzung | Optional, kein notwendiger Bestandteil des SSO-Wechsels |
| Rollen/Poolzuordnung | Anwendung ist führend | Weiterhin Anwendung als Standard; kein automatischer Claim-Import von Fachrechten |

`local` muss auch bei blockiertem Internet und ohne installierten IdP vollständig starten und alle lokalen Fachabläufe erlauben. Es gibt weder SSO-Autoumleitung noch Discovery-, JWKS-, Token-, Graph- oder Telemetrieaufrufe zum IdP. Deaktivierte SSO-Start-/Callbackpfade scheitern kontrolliert, ohne Loginzustand oder Verknüpfungen zu verändern. Login darf nicht auf Entra-Healthchecks warten.

`hybrid` benötigt konsistente Konfiguration. Ein fehlender/ungültiger Secret-Verweis verhindert die Aktivierung dieses Profils; die bereits lauffähige lokale Konfiguration wird nicht überschrieben. Ein Laufzeitausfall von Entra schaltet niemals Passwortrechte für Entra-only-Konten frei. Bereits ausdrücklich lokale Konten bleiben unabhängig nutzbar.

Es wird initial kein dritter globaler Entra-only-Modus gebaut. Je Benutzer kann Entra-only bereits innerhalb von `hybrid` erzwungen werden. Ein optionales LDAP-Profil verwendet weiterhin einen OIDC-IdP mit LDAPS; keine Speicherung von AD-Passwörtern in der Fachanwendung. Zwei externe Issuer gleichzeitig sind kein Erfordernis dieses Releases.

## 3. Vorhandene OSS-Funktionen nutzen
Speakr dokumentiert lokale Benutzeranlage und SSO. Am festgelegten Commit besitzt `User` bereits eine interne Integer-ID, Benutzername, E-Mail, Passwortfeld und SSO-Felder. Die Auth-Routen verwenden Flask-Login, Flask-WTF, Rate-Limiting sowie vorhandene Hash-/Resetdienste [OSS31–33]. Diese Komponenten und die Adminoberfläche werden angepasst, nicht durch eine zweite Benutzerverwaltung ersetzt.

Der konkrete Stand enthält ein Passwortfeld mit deklarierter Länge 60. Moderne Hashformate dürfen dort nicht abgeschnitten werden. Vor Aktivierung neuer Passwortschreibpfade wird dieses Feld per getesteter additiver Migration auf einen ausreichend grossen Typ, vorzugsweise `Text`, erweitert. Die vorhandenen direkten Login-/Passwort-/Admin-/Resetpfade müssen denselben zentralen Prüfservice verwenden [OSS32, OSS33].

Eigene Ergänzungen beschränken sich auf Konto-/Providerpolicy, abgesicherte Wiederherstellung, klinische Rechte, Identitätsverknüpfung und fehlende Verwaltungsfunktionen. Kein neuer OAuth-Server, keine eigene Passwort-Kryptografie, kein Pflicht-Keycloak und keine neue allgemeine Nutzer-API neben einem unkontrolliert weiterlaufenden Altpfad.

## 4. Ein Benutzer, mehrere mögliche Anmeldewege
**Fachliche Referenz bleibt die vorhandene `user.id`.** Diktatautor, Schreibpool-Routing, Gerätezuordnung, Review, Freigaben und Audit referenzieren diese ID – niemals E-Mail, Benutzername, Entra-Subject oder Passwortkonto als fachlichen Primärschlüssel.

| Kontopolicy | Lokales Passwort | Entra | Zweck |
|---|---|---|---|
| `local_only` | Erlaubt, wenn aktiv und eingerichtet | Kein regulärer Login; nur autorisierter Linkvorgang | Standard für neu angelegte lokale Anwender |
| `entra_only` | Verboten, auch bei vorhandenem altem Hash | Erlaubt, wenn Provider aktiv und Identität verifiziert | Default nach erfolgreich bestätigter Entra-Verknüpfung |
| `local_and_entra` | Explizit erlaubt | Explizit erlaubt | Nur bewusste, auditierte Ausnahme, kein automatischer Fallback |
| `device_only` | Verboten | Verboten | Diktatautor ohne interaktive Anmeldung |

`account_status=disabled` sperrt alle interaktiven Wege und widerruft vorhandene Sitzungen. Diktate, Freigaben und Auditgeschichte bleiben erhalten. Neue Geräteimporte eines deaktivierten Autors dürfen gesichert werden, müssen aber in einen berechtigten Klärzustand gehen; weder still löschen noch einer anderen Person zuweisen. `device_only` ist nicht mit deaktiviert gleichzusetzen.

Eine vorhandene SSO-Verknüpfung ist keine zusätzliche Fachberechtigung. Rollen und Staff-Pools bleiben serverseitig geprüft. Ein Autor erhält durch seinen Standardpool weiterhin kein Leserecht auf fremde Pooldiktate. Konto-/Technikadministratoren erhalten nicht automatisch Audio- oder Transkriptzugriff.

## 5. Lokale Benutzerverwaltung im ersten Release
Die vorhandene Verwaltungsoberfläche erhält eine kompakte deutsche Ansicht „Benutzer & Zugriff“ mit Suche, Filtern und folgenden Angaben: Anzeigename, Benutzername, E-Mail/Loginname, Status, Anmeldepolicy, fachliche Rollen, Standard-Schreibpool, Staff-Pools, Geräteautorbezug, letzte Anmeldung und Entra-Linkstatus.

Pflichtaktionen: Benutzer anlegen; Profil bearbeiten; aktivieren/deaktivieren; Rollen und erlaubte Pools verwalten; lokalen Zugang initialisieren; Wiederherstellung auslösen; Login-Ratelimit gezielt entsperren; alle Sitzungen widerrufen. Für Entra-only wird „Lokales Passwort zurücksetzen“ nicht angeboten und serverseitig abgewiesen. Eine solche Aktion darf die Anmeldepolicy nicht heimlich ändern.

Self-Registration, offene Einladungen und Just-in-time-Autoprovisionierung bleiben aus. Für den ersten Aufbau genügt ein administrativ angelegter Benutzerbestand. E-Mail dient als lokaler Loginname gemäss vorhandener Speakr-Oberfläche, nicht als vertrauenswürdiger externer Identitätsnachweis. SMTP-Erreichbarkeit ist keine Voraussetzung; synthetische Testkonten verwenden `example.invalid`. E-Mail-/Benutzernamenskollisionen werden explizit aufgelöst, nicht durch Zusammenlegung von Daten.

Benutzer werden im MVP deaktiviert statt fachlich verknüpft hart gelöscht. Die Upstream-Löschaktion mit möglicher Inhaltslöschung muss für klinische Konten gesperrt sein. Aufbewahrungs-/Löschregeln für Fachdaten werden nicht durch Benutzerverwaltung ersetzt.

## 6. Bootstrap, Aktivierung und Wiederherstellung
**Erster Administrator:** lokale Operator-CLI/Managementcommand im Serverkontext, mit verdeckter Eingabe oder einmaligem geschützt ausgegebenem Aktivierungscode. Kein Defaultpasswort, kein öffentliches „erster Besucher wird Admin“, kein Secret in Argumentliste, Compose-Datei, Repository oder Standardlog. Transaktionale Prüfung verhindert parallele Bootstrap-Administratoren; nach erfolgreicher Initialisierung ist der Bootstrap abgeschlossen. Ein späterer Notfallzugang erfordert einen ausdrücklich dokumentierten Operatorprozess.

Der Bootstrap erhält technische und Identitätsverwaltungsrechte, aber keinen pauschalen Zugriff auf Patientendaten. Klinikrollen werden explizit zugewiesen und auditiert. Das letzte aktive, lokal anmeldefähige Identitätsadministratorkonto darf im hybriden Profil nicht versehentlich deaktiviert, auf Entra-only umgestellt oder mit unbekanntem Passwort unzugänglich gemacht werden. Dieser Betriebszugang ist kein Passwortbypass für beliebige Administratoren.

**Weitere Konten:** zuerst `pending_activation`; Berechtigte erzeugen einen kryptografisch zufälligen Einmalcode mit 30 Minuten Gültigkeit und mindestens 256 Bit Zufall. Gespeichert wird nur sein Hash, mit Nutzerbindung und Zweck (`activate_local` oder `reset_local`). Ausgabe einmalig in einer `no-store`-Antwort an die berechtigte Administration; Übergabe an den Nutzer über einen genehmigten separaten Kanal. Keine Patientendaten und keine Secrets in Audit, URL-Query oder Referrer. Der Code wird im vorgesehenen Formular per POST eingelöst.

Aktivierung/Reset erlauben nur Passwortsetzung, kein Lesen klinischer Daten. Nach erfolgreicher einmaliger Einlösung sind alle anderen Resetcodes ungültig; parallele Einlösung darf nur einmal gelingen. Danach erneute normale Anmeldung, keine automatische Vollsitzung durch Reset. Ein vergessenes Passwort braucht im Startbetrieb keinen Mailserver: Admin prüft die Person, widerruft bestehende Sitzungen und erzeugt einen neuen Einmalcode. SMTP-Selbstbedienung ist ein späteres optionales Profil, nicht Teil des Startzwangs.

Eine Wiederherstellung aktiviert weder ein deaktiviertes Konto noch lokale Anmeldung für `entra_only`. Solche Änderungen sind eigenständige, auditierte und berechtigte Verwaltungsaktionen. Neue lokale Nutzer setzen ihr Passwort selbst; Administratoren können kein bestehendes Passwort auslesen.

## 7. Passwort- und Sitzungsschutz
Die folgenden Werte sind konkrete Entwicklungsdefaults, keine behauptete klinische Sicherheitsfreigabe. Sie werden getestet statt bei jeder Umsetzung erneut abgefragt.

| Regel | Default |
|---|---|
| Passwortlänge | 15–128 Unicode-Zeichen; keine stille Kürzung, kein Trim und keine erfundene Komplexitätspflicht |
| Schwache Passwörter | Lokal bereitgestellte Blockliste; keine Passwortabfrage an externe Prüfdienste |
| Neue Hashes | Vorhandene Werkzeug-Funktionen, `scrypt:131072:8:1`, Salt durch Bibliothek |
| Alte Hashes | Nur über bekannten kompatiblen Legacy-Verifier prüfen; nach erfolgreichem Login rehashen; unbekannte Formate sperren |
| Hash-Ressourcen | Höchstens 2 gleichzeitige Hashprüfungen pro Webprozess als Startwert; Backpressure, Lasttest und Speichermessung |
| Falsche Kennwörter | 5 Fehler pro Login-ID in 15 Minuten → 15 Minuten temporäre Sperre; zusätzlich 120 Versuche pro vertrauenswürdig ermittelter Client-IP/15 Minuten als Startlimit |
| Dauerhafte Kontosperre | Getrennt vom temporären Ratelimit; nur berechtigte Verwaltung |
| Sitzungslebensdauer | 15 Minuten Inaktivität, 8 Stunden absolut; keine dauerhafte Remember-me-Anmeldung |
| Sensible Verwaltung | Erneute Authentisierung innerhalb der letzten 5 Minuten; CSRF-Schutz und revisionsgebundene Änderungen |
| Resetcodes | 30 Minuten, einmalig, gebundener Zweck und Benutzer; nur Hash persistiert |

Werkzeug liefert Hashing/Verifikation; OWASP beschreibt Passwortschutz, scrypt als etablierten Pfad und sichere Wiederherstellung [OSS34–37]. Das sind Grundlagen, keine Aussage, dass das unveränderte Speakr bereits alle oben definierten Kontrollen umsetzt.

Hash-Limits und Loginzähler gelten instanzübergreifend dort, wo mehrere Prozesse eingesetzt werden. Keine alleinigen prozesslokalen Zähler als Brute-Force-Schutz ausgeben; vorhandenen DB-basierten Login-Lock wiederverwenden und ergänzen. Proxy-IP-Header nur von erlaubten Proxys übernehmen. Bei fehlendem sicherem Schutz keine unbegrenzte Authentisierung anbieten. Fehlermeldungen dürfen unbekannte, deaktivierte oder falsch eingegebene Konten nicht unterscheidbar offenlegen.

Sitzung nur mit opaker HttpOnly-/Secure-Cookie-Referenz; Provider-Tokens bleiben serverseitig. Session-ID nach Login und Rechtewechsel rotieren. Deaktivierung, Passwortreset, Providerwechsel und expliziter Sitzungswiderruf erhöhen eine Sicherheitsversion; nächste geschützte Anfrage wird blockiert, laufende Streams spätestens innerhalb des bisherigen 60-Sekunden-Ziels beendet. Autosave/Heartbeat ist keine unbegrenzte Benutzeraktivität; Aufnahme bei Ablauf geschützt stoppen/finalisieren, ohne zusätzliche Leserechte zu erzeugen.

MFA-Felder und Step-up-Schnittstelle werden erweiterbar gehalten; lokale Passwortanmeldung wird nicht als MFA ausgegeben. Für Echtdatenbetrieb müssen Schutzbedarf und privilegierte Konten im G-IDENTITY-Gate bewertet werden. Es wird weder eine selbst gebaute MFA-Engine noch ein ungeprüftes MFA-Produkt zusätzlich installiert. Bestehende Gatepflichten bleiben bestehen.

## 8. Entra bereits vorbereiten und isoliert testen
Implementierung jetzt: vorhandener Authlib/OIDC-Client; tenantgebundene Discovery; Authorization Code mit state/nonce/PKCE; serverseitiger Codeaustausch; Signatur-/Issuer-/Audience-/Tenant-/Ablaufprüfung; sichere Sessionbildung; Logout; deaktivierter Start-/Callbackzustand; User-Mapping; Fehlerseite und Konfigurationsdiagnose ohne Secrets.

Benötigte Einstellungen: Tenant-ID, Client-ID, Secret-Dateireferenz, exakter erlaubter Issuer, Discovery- und Redirect-URI. Keine Konfiguration mit `common`/beliebigem Tenant. Nur erforderliche OIDC-Scopes, standardmässig `openid profile email`; kein Graph-Leserecht, keine Gruppensynchronisierung und kein `offline_access` ohne eigene Anforderung. Reale Zugangsdaten nicht im Chat oder beim Coding-Agenten anfordern.

Tests verwenden die bestehende Authbibliothek und isolierte OIDC-HTTP-/JWKS-Fixtures mit synthetischen Tokens. Optionaler Keycloak-Testcontainer darf separat gestartet werden, darf aber nicht zum Pflichtdienst des lokalen Produkts werden. Mockbeweise sind keine Entra-Abnahme. Der Upstream-Passwort-Adminbypass muss auch bei direkten URL-Aufrufen durch die Kontopolicy begrenzt sein.

**Entra-Konfiguration und Gates erst später:** App-Registrierung, tatsächliche Redirect-URI, Tenantzugriff, Conditional Access/MFA, WebView-Benutzerwechsel, Account-Lifecycle und erlaubter Identitäts-Egress. G-ENTRA blockiert nur die echte Entra-Aktivierung, nicht den lokalen Pilot. G-LDAP bleibt ein separates späteres Gate; die LDAP-Fähigkeit wird nicht gestrichen.

## 9. Sichere Verbindung bestehender lokaler Konten
Keine automatische Verknüpfung über gleiche E-Mail/Domain, `preferred_username`, Anzeigename oder den momentan im KIS sichtbaren Benutzer. Microsoft unterscheidet veränderliche Anzeige-/E-Mail-Claims von Identifikatoren [OSS38]. Externe Zuordnung verwendet den vollständig validierten `(issuer, subject)`-Schlüssel; Entra-`tenant_id`/`object_id` dienen zusätzlich dem expliziten Provisionierungsnachweis und werden nicht mit `sub` gleichgesetzt.

Standardverfahren ohne Graph: Identitätsadministration erstellt einen **ausdrücklichen Linkauftrag** für die vorhandene `user.id` und die verifizierten Entra-`tenant_id`/`object_id`. Der Benutzer bestätigt mit frischer lokaler Authentisierung und authentisiert sich zusätzlich bei Entra. Der Callback muss exakt zum erlaubten Tenant/Object und zum serverseitig gebundenen Linkauftrag passen. Erst dann werden Issuer/Subject atomar gebunden. Ohne Linkauftrag kein Selbst-Linking und keine Neuanlage mit Fachrechten.

Für `device_only`/Benutzer ohne lokalen Login ist eine separate administrativ nachgewiesene Vorprovisionierung zulässig. Die aufzulösenden Tenant/Object-Werte kommen über einen freigegebenen Administrationsweg; keine Suchheuristik nach E-Mail. Unbekannte Identität landet ohne Fachzugriff in einem neutralen Provisionierungsfehler, nicht in einem öffentlich lesbaren Benutzerverzeichnis.

Eindeutigkeitsbedingungen: eine externe Identität gehört genau einem internen User; ein bereits gebundenes Tenant/Object darf nicht auf einen zweiten User wechseln; konkurrierende Linkversuche scheitern kontrolliert. Die Verknüpfung enthält Akteur, Nachweis, Zeitpunkt und Revision. Umhängen erfordert eigenständige geprüfte Entknüpfung/Neuverknüpfung, kein `upsert` über E-Mail.

Default nach bestätigtem Link: `entra_only`, Sicherheitsversion erhöhen, vorhandene Sitzungen beenden und lokalen Hash entfernen. Die Konsequenz wird vorher angezeigt. `local_and_entra` ist nur eine explizite administrative Ausnahme mit Begründung; der Benutzer kann sie nicht selbst aktivieren. Lokale Passwörter, Diktate oder Rollen werden niemals nach Entra synchronisiert.

## 10. Rechteentzug und Conditional Access
Die Anwendung bleibt im Start- und Hybriddefault führend für Fachrollen und Poolzuordnung. Ein Entra-Claim erteilt keine Administrator- oder klinischen Rollen ohne ein zusätzlich freigegebenes Mapping. Linking ändert weder Autoren-/Gerätezuordnung noch historische Freigaben.

**Lokale Anmeldung durchläuft keine Entra-Conditional-Access-/MFA-Prüfung.** Deshalb darf ein Entra-only-Konto bei Entra-Ausfall nicht lokal einloggen, und ein bewusst duales Konto benötigt eine dokumentierte Risikofreigabe. Hybrider Betrieb bedeutet nicht zwei universell austauschbare Zugänge für jede Person.

Appseitige Deaktivierung wirkt unmittelbar auf alle App-Provider. Eine ausschliesslich in Entra erfolgende Kontosperre ist dagegen ohne eingerichteten Lifecycle-/Widerrufsweg kein Echtzeit-Widerruf bestehender App-Sitzungen. Für die spätere Entra-Freigabe gilt: dokumentierte maximale Sitzungsdauer plus Helpdesk-Prozess zur gleichzeitigen Appdeaktivierung; schnellerer automatischer Sync nur als separat implementierte Erweiterung. Nichts als sofortigen Entra-Offboarding-Effekt behaupten, was nicht getestet wurde.

## 11. Migration, Aktivierung und Rückfall
Erster Release liefert das additive Identity-Schema bereits mit: stabile interne Benutzer-ID, Kontopolicy, Sicherheitsversion, externe Identity-Tabelle und Linkaufträge. Der spätere Entra-Wechsel benötigt deshalb keine Verschiebung von Diktaten und keine Neuanlage von Autoren. Notwendige Upstreammigrations-/Datentypänderungen werden trotzdem versioniert und gegen PostgreSQL getestet; keine pauschale Zusage „nie eine Migration“.

Ablauf später: Backup und Stagingtest → Entra-Konfiguration separat validieren → lokales Administratorkonto prüfen → Hybridprofil aktivieren → ein Testkonto explizit verbinden → Rechte-/Verlaufs-/WebViewtests → gestufte weitere Verknüpfung. Autoren, Pools, Gerätetabelle und Freigaben vor/nach dem Wechsel müssen dieselben internen Referenzen haben.

Rückfall deaktiviert den Provider, setzt aber Entra-only-Konten **nicht** automatisch auf lokale Kennwörter zurück. Wiederherstellung durch lokale Identitätsadministration mit dokumentierter Prüfung, neuer Aktivierung und Audit. Keine Nutzung alter entfernten Hashes und keine Cloud-Ausfallhintertür. Daten-/Kontext-/Freigaberegeln bleiben dabei unverändert.

## 12. Was Claude ohne Rückfrage liefern muss
Lokalen Start ohne Entra/Keycloak/SMTP; funktionsfähige Benutzerverwaltung; Bootstrap-/Aktivierungs-/Resetprozess; Rollen-/Poolzuordnung; sessionsichere Anmeldung; additive Identity-Migrationen; vollständigen aber deaktivierten OIDC-Adapter; Profile `local` und `hybrid`; Linkworkflow und Ausschluss automatischer Kontenübernahme; isolierte Entra-Negativtests; dokumentierten Aktivierungs-/Rollbackplan. Anwendung und Anbieteranmeldung werden nicht als fertig behauptet, bevor echte Tests vorliegen.

R-009 und R-043 sowie S03 sind aktualisiert. R-051–R-062 und T-071–T-098 beschreiben die Ergänzung. T-009 ist nun der lokale Basistest. LDAP- und Entra-Realfreigaben liegen separat, nicht als Voraussetzung für die erste lokale Anmeldung. Tests starten `not_run`; Paketvalidierung ist kein Auth-/Kliniktest.


---

<!-- Quelle im Bauauftrag: docs/11_Paritaet_und_Webgrenze.md -->

# 11 – 101 Pflichtfunktionen, kein stiller Ersatz
## Quellenübernahme
Das hochgeladene Gesamtpaket enthält 101 Funktionsverträge und 101 Verhaltensfälle. Sie sind im funktionalen Arbeitskontext unverändert enthalten und in der V4-Matrix jeweils auf Module, Wiederverwendung, Regeln, Task und Test zurückgeführt. Ihre ursprünglichen Handbuch-/Static-Evidenzgrade bleiben sichtbar: Sie sind nicht automatisch auf eurer Installation beobachtete Tatsachen.

Die neue Pflicht bedeutet vollständige fachliche Abdeckung nach Referenz-/Zielvergleich, nicht automatisch dieselben EXE-Namen, proprietären Datenformate oder dieselbe Pixelgestaltung. Ein expliziter Verhaltenssatz wie „in geöffnetes natives Dokument einfügen“ darf jedoch nicht einfach in „DOCX herunterladen“ umbenannt werden.

## Neuester Zwischeninput
Alle Diktatfunktionen vollständig im Webclient. Der Import-Agent ist kein Ort für Funktionen, die Browser nicht leisten. Die vorbereitete Windows-Companion-Lösung ist verworfen. Kein nativer Ersatzclient, kein lokaler APIhost/Plugin für Fenster-/Office-/Hotkeyaktionen. Eine reine installierte PWA ist optional, aber kein nativer Wrapper.

## Klassen der Matrix
`web_design`: vorgesehener Web-/Serverweg, noch nicht implementiert oder getestet. `dependency_validation`: Browser-, Hardware-, Identity- oder ASRfähigkeit ist konkret nachzuweisen. `strict_web_conflict`: mindestens eine ursprüngliche Teilklausel kollidiert mit aktueller Clientgrenze oder hat noch keinen zulässigen nachgewiesenen Weg. Keine dieser Klassen ist ein bestandener Produkttest.

## Umgang mit offenen Kollisionen
Zulässige Teilfunktion bauen; Alternative separat kennzeichnen; Originaltest erhalten; Status offen. Kein automatisches N/A oder Herabstufen von MUST. Volle 1:1-Zusage und vollständige Abschaltung der Altsoftware sind bei offenen Kollisionen nicht freigegeben. Claude kann alle unabhängigen Teile entwickeln, ohne den Auftraggeber wiederholt um dieselbe Entscheidung zu bitten. Eine spätere ausdrückliche Änderung wird mit Datum/ID/Testdelta protokolliert, nicht retrospektiv als bereits erfüllt behauptet.

Der Pflichtkatalog enthält zahlreiche vollständig webgeeignete Funktionen. Die Kollision einzelner Teilklauseln ist daher kein Grund, den Gesamtbauauftrag aufzugeben oder das Backend neu zu wählen. Sie ist ein notwendiger Unterschied zwischen einem guten Implementierungsplan und einer falschen Paritätszusage.

## Offene Teilklauseln
| ID | Originale Teilklausel | Zulässiger Webweg / Grenze |
|---|---|---|
| PAR-ADM-009 | Lokale Windows-Konten des Arbeitsplatzes auslesen | AD-/Directory-Abgleich serverseitig und lokale Appkonten anbieten; lokale SAM-Auswahl ohne erlaubte Browser-/Serverschnittstelle bleibt offen. |
| PAR-LIVE-002 | Direktdiktat in fremde native Windows-Textfelder | Live-Diktat im Speakr-Webeditor, bewusster Clipboard-/Dateiexport; keine Gleichsetzung mit Fremdfeldeingabe. |
| PAR-LIVE-006 | SpeechLock an fremdes Windows-Fenster | Bindung an eigenen Webentwurf möglich; OS-Fensterbindung trotz anderem Vordergrundfenster nicht als erfüllt ausgeben. |
| PAR-LIVE-007 | Automatische native Fokusbindung/Always-on-top | Weblayout/Entwurfsbindung bereitstellen; keine allgemeine OS-Fenstersteuerung oder Always-on-top-Zusage. |
| PAR-IMP-009 | Schreibende Philips-Gerätekonfiguration | Geräteinventar lesen; Autorenlisten/PIN/Verschlüsselung/Energiesparen nur nach geprüftem direkten Browser-Hardwarevertrag. Import-Agent erhält keine Konfigurations-Schreibbefehle. |
| PAR-IMP-010 | Firmware auf PocketMemo schreiben | Firmwarekatalog/Versionsanzeige im Web; Flashen bleibt gesperrt ohne nachgewiesenen erlaubten Browserweg. Kein Flashen im Import-Agent. |
| PAR-EXP-004 | Einfügen in bereits geöffnetes natives Word-Dokument | DOCX aus serverseitiger Vorlage mit Textmarken und Download/Workflowablage; offenes natives Dokument ist eine gesonderte, ungelöste Teilanforderung. |
| PAR-EXP-005 | Einfügen in bereits geöffnetes natives Writer-Dokument | ODT aus Vorlage serverseitig erzeugen und herunterladen; keine Fernsteuerung der laufenden Writer-Instanz. |
| PAR-EXP-007 | Fensterklasse/-titel, Vordergrund und Tastensimulation | Expliziter Clipboard-/Downloadweg; keine Window-Enumeration, SendInput oder OS-Fokusautomation. |
| PAR-EXP-009 | Lokales Mailprogramm mit Audioanhang öffnen | Berechtigter Audio-Download und optional .eml-Artefakt nach Policy; automatischer lokaler Mailentwurf mit Anhang nicht damit gleichsetzen. |
| PAR-EXP-010 | Natives Word-Add-in mit aktueller Audiositzung | Webupload eines Dokuments mit bewusst gewählter Audiositzung; kein natives Add-in und keine automatische Gleichsetzung mit dem ursprünglichen Word-Workflow. |
| PAR-OPS-003 | Systemweite Hotkeys ohne Browserfokus | Konfigurierbare Tastenkürzel im Webclient; Keyboard Lock nur in tatsächlich erlaubter Browser-/Vollbildsituation, kein globaler OS-Hotkeydienst. |
| PAR-OPS-004 | Nativer Tray-/Autostart-/Hintergrundprozess | PWA/Browserstart und serverseitige Hintergrundjobs; Browser-Startup-Policy ist kein eigener Trayprozess. Nicht als unveränderte 1:1-Parität markieren. |


---

<!-- Quelle im Bauauftrag: docs/12_Webclient_und_Importagent.md -->

# 12 – Implementierungsvertrag Webclient / Import-Agent
## Clientprofile
`managed_web_online`: normale Browseranwendung auf freigegebenen Arbeitsplätzen. `byod_internal`: interne mobile Vordergrundaufnahme ohne persistente Audio-/Textbibliothek oder Dokumentdownload. `kis_webview`: derselbe Webclient mit geprüftem Host-/Kontextvertrag. `managed_web_offline`: getrennt freizugebende verschlüsselte Webpersistenz, initial deaktiviert. `import_agent`: Maschinenprincipal ohne Facheditor oder Mikrofon.

Der Server entscheidet Profil und Rechte über authentisierte Identität, erlaubten Ingress und freigegebene Policy. Ein User-Agent, `managed=true`, frei gesetzter Header oder bekannte IP allein beweist kein verwaltetes Endgerät. Bestehende Browserdaten lassen sich bei Offlineverlust/Rechteentzug nicht magisch remote löschen; Restrisiko ausdrücklich abnehmen.

## Erlaubter Importweg
1. Agent meldet zugelassene Quelle und Snapshot-Inventar; keine Autor-/Patientenrechte ableiten.
2. Web-Importzentrale zeigt dem berechtigten Nutzer verfügbare Quellen, offene Zuordnungen und Stapelstatus.
3. Server prüft Registry/Policy und erzeugt typed import intent für konkrete Source-/Snapshotversion.
4. Agent sendet Originalbytes/Hash/Metadaten resumable über HTTPS; Original unverändert.
5. Server persistiert Objekt plus Metadaten/Jobabsicht; gibt gebundene Quittung.
6. Nur aktivierte enge Importnachbearbeitung darf Backup/identische Quellversion entfernen. Quellwechsel erzeugt Konflikt.

Kein beliebiges `execute(command)`, `open_file(path)`, lokaler WebSocket oder Windows-Prozesszugriff. Maschinencredentials dürfen nicht Diktate abspielen, Texte lesen, Rollen setzen oder klinische Freigaben ausführen. Der Agent pollt nur seine signierte Importkonfiguration und gebundene Aufträge; er ist kein Remote-Managementprodukt.

## Browserhardware
Mikrofon via getUserMedia; Audiooutputwahl nur bei unterstützter API/Permission. Pedal als Tastatur im fokussierten Webclient oder als direktes WebHID-Gerät mit expliziter Freigabe. LEDs/Knöpfe erfordern modellbezogenen Reportvertrag; keine unbekannten HID-Reports senden. Stop/Disconnect und Browserpermissionentzug sind Sicherheitsfälle. WebHID besitzt begrenzte Browserabdeckung [WEB07]. Native globale Hotkeys und Fenstersteuerung werden nicht daraus abgeleitet [WEB06].

## Dateiausgabe und Netz
Dateien im Browser nur durch bewusste Nutzeraktion/erlaubte Handles oder Download. Serverdateipfade kommen aus konfigurierten Zielaliasen, nicht ungeprüften Nutzereingaben. Browser-Picker können fehlen oder abgebrochen werden [WEB08]. Download-HTTP-Erfolg beweist keine lokale Speicherung; getrennte Auditaktionen rendered, download_started und optional user_confirmed.

Ausgehendes Netz von Audio/ASR/Training zu externen APIs gesperrt. Login local hat keinen Identitäts-Egress. Spätere Entra-Zugriffe getrennt begrenzen. Browserassets/Modelle nicht aus CDNs nachladen. Keine geheime Ausweichroute im Agenten, wenn Browserpermission verweigert wird.


---

<!-- Quelle im Bauauftrag: docs/13_Audio_Live_Editor.md -->

# 13 – Webrecorder, Live-Diktat und Editor
## Bestehenden Recorder erweitern
Speakr Recording Sessions bleiben der Transport. Session hat stabile user.id, dictation_id, generation, state und monotone Sequenz. Browser ermittelt MIME/Codec, überträgt Originalchunks und meldet Finalisierung. MediaRecorder-Abschnitte sind nicht zwingend allein abspielbar [WEB09]; Reihenfolge und Header-/Enddaten müssen validiert werden. Maximaldauer/Bytes aus Entwicklungsconfig; Begrenzung sichtbar, keine stille Trunkierung.

Aufnahme, Stop/Pause, Playback, Cursor, Titel-/Versandphase und metadatengesteuerte Pflichten implementieren. Voice-activated Recording bekommt sichtbaren Modus, Schwellwert/Vor-/Nachlauf und Regression; das Original darf nicht unerkannt Wortanfänge verlieren. Gain/AGC für Wiedergabe verändert kein Original.

## Audiorevisionen
`audio_source` ist unveränderlich. `audio_edit_revision` enthält geordnete Segmente `{source_id, start_sample, end_sample, output_position}`; Sampleintervalle halb-offen. Aufnahme an Cursor erzeugt neue Quelle und Editoperation insert oder overwrite; trim entfernt Referenzen ab Cursor, nicht historische Dateien. Mindestens 16 Undo-Aktionen verfügbar; Redo nach neuem Branch invalidiert. Neue aktive Audiorevision invalidiert nicht mehr passende Textanker/Freigabe. Server rendert Wiedergabe-/ASRderivat reproduzierbar; Clientvorschau nutzt dieselben Referenzen. Originalschutz/Anker-Tests unabhängig vom Wellenformbild.

## Liveereignisse
Browser startet nach Session-/CSRF-Prüfung einen gleichursprünglichen WebSocket oder geeigneten bestehenden Streamingkanal zu Speakr; Origin-Allowlist und fortlaufende Autorisierung. Keine permanenten Keys im Browser, kein Speaches-Token in URL. Backend delegiert an lokale Speaches-Realtime mit `intent=transcription`, explizitem freigegebenem deutschen Modell und deaktivierter Antwortgenerierung [WEB04]. Nicht auf englische APIdefaults vertrauen. Fehlende Cancel/Truncate-Unterstützung im gepinnten Adapter mit Generationswechsel/Serverabbruch abfangen; keine zugesicherte API erfinden.

Eventfelder: session_id, generation, sequence, event_id, audio_revision_id, range, kind, transcript_revision. Ereignisse idempotent; alte Generation und duplicate nicht erneut anwenden. Partialtext klar vorläufig. Finale Phrase erst nach dauerhaftem Audio-/Textcommit als server_saved quittieren. Korrigierter Text gewinnt gegenüber verspätetem ASRlauf, kein stilles Überschreiben. Reconnect über letzte bestätigte Sequenz, keine blinde Neuaufnahme einer bereits gespeicherten Phrase. Löschung/Abbruch/Sessionablauf jeweils eigener Zustand.

## Editor und Audioanker
Richtext-Dokumentstruktur mit stabilen Knoten-/Segment-IDs, nicht ausschliesslich absolute Stringoffsets. Anker referenzieren Audioquelle/-revision und Wort-/Phrasenbereich. Menschlich neu eingefügter Text kann unaligned sein; UI weist dies aus. Bei Verschieben bleibt Segmentherkunft erhalten, bei freier Umschreibung keine erfundene wortgenaue Synchronisation. Unicode-/UTF-16-/Codepoint-Mapping testen. Textversion, Layout und Exportserialisierung kommen aus derselben kanonischen Struktur.

Player zeigt Waveform, aktuelle Phrase und echte Erkennungs-/Unsicherheitsinformationen; keine selbst erdachten Confidencewerte. Abspieltempo 0.5–2.0, Reset, Spulen, benannte Indizes, Fortsetzposition, Tipp-stoppt/Tippen-bei-Playback und Rücksprung in Zeit; Wortrücksprung nur mit validem Alignment. Audio- und Textundo getrennt, über gemeinsame Revision konsistent. Fussschalter darf dieselben Webbefehle auslösen, aber weder Lease noch Rechte umgehen.

## Lokaler Entwurf, Offline und Dateiformate
Online: alle bestätigten Phrasen serverseitig, mehrere Drafts und aktive Auswahl pro Tab; Cross-Tab-Konflikte statt last-write-wins. BYOD speichert keine Audiodaten persistent. Managed-Web-Offline initial aus; synthetische Implementierung mit explizitem Profil möglich. Audiochunks/Manifest lokal nur verschlüsselt, Contentkey nicht ungeschützt persistent, Schlüsselentsperrung vom Benutzer und sichere Standardbibliothek/WebCrypto. Persistenz nur nach tatsächlicher Storagebestätigung anzeigen. Keine Offline-Appanmeldung mit kopierten Servercookies; bei Reconnect neue AuthZ vor Upload. Browserquoten/Eviction/Privatmodus/Logout/OSwechsel testen. Kein Anspruch, dass Webspeicher Betriebssystembackups vollständig kontrolliert.

Portable .sdict-Bundles haben Formatversion, Manifest, Audioquellen, Editliste, Textrevisionen, Anker und Prüfsummen. Keine Tokens, Passwörter oder maschinellen Vertrauensentscheidungen. Import erzeugt neue interne Objektbezüge; vorhandene ACL-/Freigaben nicht aus Bundle übernehmen. ZIP-Traversal, Expansion, MIME, aktive Inhalte und Parserlimits prüfen. RTF/TXT laden/speichern sowie DOCX/ODT-Exports verwenden etablierte serverseitige Parser/Konverter. Konvertierungsverluste offen anzeigen; Bilder/Textmarken/Tabellen synthetisch testen.


---

<!-- Quelle im Bauauftrag: docs/14_Wortschatz_Profile_ASR.md -->

# 14 – Wortschatz, Befehle, Profile und lokale KI
## Zwei verschiedene Verträge
Verwaltung von Wortlisten/Profilen ist nicht gleich nachgewiesene Wirkung in der Engine. Jede Ressource ist versioniert, scopegebunden (user/author_pool), mit Autor, Zeitpunkt und Quelle. Geschriebene/gesprochene Form, Aussprachevarianten, Status aktiv/negativ/vorgemerkt und Prüfhistorie getrennt. Textimport/export mit Unicode, Deduplizierung, Grenzwerten und Vorschau. Verwerfen/Benutzer/Pool sind unterschiedliche Entscheidungen; verschobene Prüfung bleibt eine ausstehende Aufgabe.

## Effectiveness-Gates
Capabilities: batch_transcription, live_transcription, hotword_bias, negative_lexicon, pronunciation_lexicon, pronunciation_training, language_adaptation, acoustic_adaptation, profile_checkpoint, alignment, local_tts. Jeder Eintrag enthält implemented, tested_model, testset_hash, evidence und Grenzen. Fehlende Fähigkeit liefert CAPABILITY_UNAVAILABLE, kein erfolgreiches leeres Jobresultat. Ein Hotwordprompt kann eine Hilfestellung sein, ist aber weder Negativlexikon mit garantierter Unterdrückung noch persönliches Akustiktraining. Fehlende Capabilities erfordern einen ASR-Adapter-/Modellspike, nicht die Streichung der PAR-Anforderung.

## Befehlsmodell
Befehle lösen nur erlaubte Webaktionen aus: Text-/Grafikbaustein, Punktuation/Format, Cursor innerhalb Editor, Draftaktion und Workflowaktion nach Rechteprüfung. Before/after-Hooks sind deklarative Aktionlisten mit Zyklus-/Tiefenlimit, keine Shell/JavaScript-eval oder OS-Tastensimulation. Schutzbedürftige Aktionen (Freigabe/Löschen/Empfängerwechsel) brauchen explizite Bestätigung; erkannter Sprachbefehl allein ist kein medizinischer Freigabenachweis. Befehlsversprecher dürfen keine Hintergrundaktion auslösen. Originaldiktat bleibt nachvollziehbar; Korrektur zeigt Befehl-/Textmarken.

## Profilmodell
Live- und Batchprofil pro Autor/Quelle mit optional gemeinsamem Checkpoint. Profilreferenz bindet Engine-/Modellhash, Ressourcenrevisionen, Parameter, Quelle und Persistenzpolicy never/ask/always. Zentraler Profilstand wird auf neuem Arbeitsplatz geladen; keine native Dragoninstallation. Anzeige eines Profils ist nicht die Behauptung formatkompatibler Dragonprofile. Originale Modelle/Schlüssel aus dem Archiv werden nicht importiert.

## Anpassung aus Korrekturen
Trainingsmaterial nur aus bewusst freigegebenen Korrekturversionen, mit Daten-/Berechtigungsnachweis. Text+Audio nur bei bestätigter vollständiger Übereinstimmung und passenden Ankern, keine nicht gesprochenen Ergänzungen als Wahrheit. Korpusmanifest mit Herkunft, Consent/Policyreference, Sprach-/Sprecher-/Gerätabdeckung und Ausschlussstatus. Jobs lokal in freigegebenen Zeitfenstern, Mindestdatenprüfung, reproduzierbarer Checkpoint, getrenntes Testset gegen Overfitting; ungeprüfter Checkpoint nie live aktivieren. Regression, Signoff und Rollback pflegen. Training ist eigenständige Enginefähigkeit, nicht bloss SQL-Update einer Liste.

## Betrieb und Qualität
Default lokale Batchtranskription, Sprache de, explizites Modell, keine Cloudumleitung. ASR-Timeout bietet manuelle Abschrift bei validem Audio. Bereits korrigierte Texte bleiben vor Reprozessierung geschützt. WER/CER plus kritische Fehler und Korrekturzeit; Zahlen, Negation, Medikamente, Dialekt, Überlappung und Lärm im freigegebenen Testsatz. Keine pauschale Genauigkeitsgarantie aus Modellnamen. Live/TTS können Hardwareanforderungen haben; CPU ist Entwicklungs-/Funktionspfad, kein Nachweis geforderter Latenz.


---

<!-- Quelle im Bauauftrag: docs/15_Ausgabe_KIS_und_Abnahme.md -->

# 15 – Webausgabe, Abnahme und inesKIS
## Fachlicher Stand
Originalquelle → Arbeitsrevision → menschlich korrigiert → optional abgenommen → fertig. Ein Diktat ohne aktivierte zusätzliche Abnahme benötigt trotzdem eine menschliche Korrekturbestätigung vor finaler klinischer Ausgabe. Autor und Schreibkraft können dieselbe Person sein. KISsignatur bleibt eigener Hersteller-/Fachprozess.

Ausgabejob friert dictation_id, audio_revision, transcript_revision, context_revision, document_type, release/working-mode, renderer_version und content_hash ein. Jeder ausgewählte Kanal besitzt eigenen Idempotenzkey und Zustand. Ein bestätigter Kanal wird nicht erneut ausgeführt, weil ein anderer scheitert. Unklarer Seiteneffekt ist UNKNOWN; Lookup/Reconciliation vor neuem Versand, auch nach Restore.

## Dateikanäle
DOCX mit Vorlagen/Textmarken, RTF, ODT, TXT, freigegebene Originalaudioausgabe und formatierte/unformatierte Zwischenablage. Vorlagendateien werden zentral kopiert, Version/Hash bleiben erhalten; fehlende externe Ursprungsdatei zerstört keine bereits gespeicherte Vorlage. Bei Textmarkenfehler bewusst abbrechen statt ans falsche Dokumentende schreiben. Grafiken/Absätze/Umlaute/Tabellen und Formatverlust testen. Originalmakros/aktive Inhalte nicht ausführen.

Clientdateien nur via Upload/Pickers/Download; Serverablage über konfigurierten Alias mit Berechtigungs-/Pathprüfung. Bestehendes natives Officefenster, Windows-Zielfensterklasse, automatischer Mailanhang und Word-Add-in sind als offene WG-Teilforderungen geführt. Webupload eines Office-Dokuments, Download oder .eml sind nützliche Alternativen, aber ohne ausdrückliche Abnahme kein Ersatz ihres ursprünglichen BT.

## KIS unverändert ernst nehmen
Derselbe Speakr-Webclient wird nach Herstellervertrag in inesKIS/Edge-WebView eingebettet. Anzeige, SSO/Benutzerwechsel, Patient-/Fallkontext, Schreibvertrag und Quittung/Lookup sind fünf gesonderte Nachweise. Originalfunktionen ohne KIS ersetzen diese Anforderungen nicht. Keine KIS-API erfinden, kein Datenbankschreibzugriff, kein UI-/Clipboard-/Fenster-Scraping zur Patientenidentität.

Kontext über gebundenen vertrauenswürdigen Herstellerweg, interne user.id unabhängig von local/Entra. Patientwechsel in Host bindet vorhandenes Diktat nicht um. Akteure/Fallrechte serverseitig verifizieren. Token in WebViewprofilen, Cookies, Audio-/Mikrofon-/Downloadpermissions und Benutzerwechsel real testen. Auf Clientseite keine generische Hostobjekt- oder Native-Messagingbrücke.

## Nachträge und Wiederherstellung
Reopen ist neue Revision; bestätigte KISausgabe unverändert bis freigegebenem Nachtragsverfahren. Falscher Patient, Patientenmerge, Fallkorrektur und Rückweisung separate Prozesse. Dokument-/Audio-/Kontextreferenzen müssen auch DB-seitig zusammengehören. Restore der Plattform mit älterem Exportledger verlangt KIS-Abgleich, nicht automatisch neue Dokumente. Manueller Fallback erst nach geklärtem Kanalstatus.


---

<!-- Quelle im Bauauftrag: docs/16_Datenvertraege_und_Defaults.md -->

# 16 – Verbindliche Ergänzungen zum Datenmodell
Bestehende Speakr User/Recording/Group sowie SQLAlchemy-Migrationsmechanik bleiben. Die Tabellen-/Servicenamen hier sind Soll-Erweiterungen, keine Behauptung vorhandenen Upstreamcodes. `contracts/data-model.md`, `identity-contract.json`, `clinic-api.openapi.json` und `parity-api.openapi.json` ergänzen einander.

## Neue oder präzisierte Relationen
| Relation | Invariante |
|---|---|
| author_pool / author_pool_membership | Mehrfachzuordnung, gültig ab/bis; kein Staffrecht aus Namen. |
| action_grant / scope_rule | Aktion, user/author_pool/all, explizites deny; gleiche Policy für UI/API/Jobs. |
| routing_assignment | Person/Staffpool/all_eligible/self; historische Zuordnung bleibt unverändert. |
| audio_edit_revision / audio_segment | Geordnete halb-offene Quellintervalle, Samplebasis, Renderhash; keine Originalmodifikation. |
| text_audio_anchor | Stabile Textnode/-range mit Audioquellrevision; unaligned ist expliziter Zustand. |
| live_session / phrase_event | Sessiongeneration und Sequenz eindeutig; durable audio/text commit vor Quittung. |
| resource_revision | Vokabular, Befehle, Vorlage und Profil mit scope+revision; kein stilles Überschreiben aktiver Session. |
| review_proposal | Geprüftes Hinzufügen/Löschen/Commandchange mit Nutzer-/Poolentscheidung. |
| model_adaptation_run | Datenmanifest, Einwilligungs-/Policyreferenz, Modell-/Testhash, Status und Freigabe. |
| acceptance_rule / acceptance_decision | Optionale zusätzliche Abnahme getrennt von correction_confirmed und KISsignatur. |
| output_attempt | Version-/Kontextbindung pro Ziel, UNKNOWN als eigener Zustand, Payloadhash. |
| ingest_source_snapshot | Agent, source/volume/device, hash, Metadatenversion, Erkennungsstatus und Receipt. |
| source_cleanup_intent | Exakte Sourceversion, gebundene erfolgreiche Quittung, Freigabe, erneute Identitätsprüfung. |
| capability_evidence | Browser/Gerät/Engine + Version + Protokoll/Fixture + tatsächlicher Testbeleg. |

Zugehörigkeit mittels zusammengesetzter UNIQUE/FK oder gleichwertiger DBconstraint erzwingen. Transcript-/Audio-/Contextrevision aus Diktat B darf nie für Diktat A freigegeben/exportiert werden. Akteur, Recht, Scope und gültige Lease werden nicht allein im Frontend geprüft. DBtransaktion umfasst Status/Audit/Jobintent; externe Storage-/KISaktionen mit Reconciliation.

## Defaults ohne Rückfrage
Localauth an; Entra aus; Selfregistration aus; Patiententestdaten aus; Entwicklungsfixtures synthetisch. EOL-Filter an, Quellenlöschung aus, nicht rekursive Dropfolder, Autoannahme unbekannter Autoren aus. Alle Diktatfunktionen Web-only. BYODpersistenz aus, Managed-Web-Offline aus bis Gate. Realtime nur transcription-only; TTS lokal; kein Cloudfallback. Zusätzliche Abnahme im Testdefault an, fachlich konfigurierbar. 20 Filter und mindestens 16 Audio-Undo-Schritte. Fehlender Herstelleradapter ist disabled und ohne Seiteneffekt.

## Konsistenz der alten API
Bestehende Clinic-Endpoints bleiben kompatibel, sofern ihre Actor-/Kontextpolicy stimmt. Wo ein alter DTO genau einen Pool erwartet, bleibt dies ein kompatibler primary_pool-Projektionswert; zusätzliche Routing-/Ressourcenrelationen werden im Paritätsvertrag geführt. Keine implizite neue Leseberechtigung. Alte Routen entweder zentral härten oder für Clinicobjekte sperren; kein offenes Legacy-Bypassfenster neben neuen APIs.


---

<!-- Quelle im Bauauftrag: docs/17_Abnahme_und_Evidenz.md -->

# 17 – Abnahme ist nicht Paketvollständigkeit
## Drei getrennte Ebenen
1. Paketprüfung: IDs, referenzielle Abdeckung, unveränderte Quell-BT, Profile, Dateiintegrität und Archivinventar.
2. Implementierungstest: echte Unit-/DB-/Contract-/Browserausführung mit Buildstand und Artefakten.
3. Paritäts-/Klinikabnahme: autorisierte Referenz und Zielversion mit denselben freigegebenen Fixtures, fachlicher Vergleich und reale Geräte-/KIS-/Betriebsnachweise.

Die erste Ebene kann die anderen nicht ersetzen. Alle mitgelieferten Produkttests sind `not_run`. Die 101 Original-BT sind aus der Quelle kopiert und werden gegen Sollverhalten geprüft; Quelle ist Handbuch-/Analyseevidenz, keine automatisch beobachtete Südhangreferenz.

## Laufmanifest
run_id, test_id, requirement_ids, reference_or_target, application_version, git_commit, build_hash, OS/browser/device/firmware/model, fixture_hashes, actor_role, policy_revision, steps, observations, artifacts, UTCtimestamps, reviewer. Beobachtung und Erwartung separat. Synthetische WAVEfixtures sind Signal-/Transporttests, keine medizinische Sprachqualität. Kein echtes Patientendiktat in externen Agentprompts oder öffentlichen CIartefakten.

## Vergleich
Jeden Original-BT unverändert ausführen oder konkreten Blocker dokumentieren. UIpositionen dürfen anders sein, fachliche Aktion und Nachwirkung müssen belegt gleichwertig sein. Eine Webalternative bekommt eigenen WB-/Vergleichsbeleg; der ursprüngliche native Teiltest bleibt offen, bis der Auftraggeber eine ausdrückliche Änderung beschliesst. Ergebnis states not_run/running/passed/failed/blocked; `passed` braucht Evidence. Keine automatische N/A-Freigabe.

## Zusätzliche Tests
24 V4-Kreuztests prüfen Poolgrenzen, EOL, Quellversionen, Revisionen, Profile, Ausgaben und Archivtrennung. 20 Webtests prüfen keine native Hintertür, Agentrechte, Browserpersistenz, Hardware-/Offlineverhalten und fehlende Paritätszusage. Die 98 bisherigen Tests und 9 Grokfälle bleiben erhalten. Nach Implementierung gezielte Negativtests, abgesicherte Parser, Securityprüfung und Restore vor Echtdatenfreigabe.

## Releaseentscheidung
Lokaler synthetischer Prototyp kann vor allen Herstellerantworten entstehen. Reale einzelne Funktionen werden jeweils nach Gate freigegeben. Vollständige DNS-Comfort-/Philips-Ablösung erst bei 101 erfolgreichen fachlichen Vergleichen, keinen offenen scope-relevanten WG, freigegebener KIS/BYOD/Identity, Rollback, Betrieb und dokumentierter Cutoverzuständigkeit. Kein Erfolgsprozent aus kleinen Testzahlen als universelle Zuverlässigkeit melden.


---

<!-- Quelle im Bauauftrag: docs/18_Web_Quellen.md -->

# 18 – Neu geprüfte Primärquellen
Stand 17.11.2026. Bibliotheksdokumentation ist kein lokaler Kompatibilitätstest. Historische Quellen aus V3.2 bleiben in evidence/sources-v3_2.json erhalten.

## WEB01 – Speakr release and commit
https://github.com/murtaza-nasir/speakr/releases/tag/v0.11.1

Release v0.11.1 refers to commit 48d3018; pinned baseline retained, no build performed.

## WEB02 – Speakr recording sessions
https://murtaza-nasir.github.io/speakr/admin-guide/recording-sessions/

Existing server recording sessions/chunk transport; reuse, not proof of clinic privacy/completeness.

## WEB03 – Speakr mobile recording
https://murtaza-nasir.github.io/speakr/user-guide/recording/

Mobile foreground restrictions documented; not a native-background recording guarantee.

## WEB04 – Speaches realtime
https://speaches.ai/usage/realtime-api/

WebSocket transcription-only mode documented; specific interruption events are limited; local adapter must test its pinned version.

## WEB05 – Speaches TTS
https://speaches.ai/usage/text-to-speech/

Existing local TTS API; model, voice/language, license and local performance must be verified separately.

## WEB06 – Keyboard Lock
https://developer.chrome.com/docs/capabilities/web-apis/keyboard-lock

Keyboard Lock applies under browser/fullscreen/OS restrictions; not general native global hotkeys.

## WEB07 – WebHID
https://developer.mozilla.org/en-US/docs/Web/API/WebHID_API

Limited availability and explicit device access; not universal foot pedal/microphone control.

## WEB08 – File saving
https://developer.mozilla.org/en-US/docs/Web/API/Window/showSaveFilePicker

Picker requires secure context/user activation and is not universally available; no arbitrary path write.

## WEB09 – MediaStream Recording
https://www.w3.org/TR/mediastream-recording/

Recording events/containers need finalization; separate chunks are not necessarily independently playable.

## WEB10 – dss-codec
https://github.com/hirparak/dss-codec

MIT decoder DSS/DS2 to WAV; password-protected input supported according to project, not Philips metadata/firmware proof.

## WEB11 – wavesurfer.js
https://wavesurfer.xyz/

Existing OSS waveform/regions plugins, BSD-3-Clause per project site; candidate only if existing Speakr UI lacks necessary functionality.

## WEB12 – Speakr privacy
https://murtaza-nasir.github.io/speakr/admin-guide/privacy/

Existing browser persistence paths need a clinic-specific audit; latest web documentation may differ from pin.


---

<!-- Quelle im Bauauftrag: docs/19_OSS_Wiederverwendung.md -->

# 19 – Wiederverwendung unter Web-only
| Bereich | Ausgangsbasis | Erweiterung / Grenze |
|---|---|---|
| Webanwendung | Speakr v0.11.1 am festgelegten Commit | Klinikmodule im vorhandenen Stack, nicht neuer Transkriptionsdienst. |
| Lokale User/SSO | Speakr User, Admin, Flask-Login/Authlib | Bestehende Pfade härten, local-first und vorbereitete verifizierte Entra-Links. |
| Aufnahme | Speakr-Webrecorder und Recording Sessions | Geordnete Chunks, Livegenerationen, Editrevisionen und Browserprofile. |
| Player/Editor | Tatsächlich im Checkout vorhandene Komponenten | Paritätslücken gezielt ergänzen; kein pauschales UI-Rewrite. |
| Wellenform | Vorhandene Komponente, sonst wavesurfer.js | Regions/Timeline wiederverwenden; keine neue allgemeine Waveformbibliothek. [WEB11] |
| Richtext | Vorhandener Speakr-Editor | Erst nach Gapnachweis etablierten OSS-Editor wählen und ADR erstellen; kein Cloudkollaborationszwang. |
| Live/Batch | Speaches / faster-whisper über bestehenden Konnektor | Eigene authentisierte Clinic-Sitzung kapselt lokalen Realtime-Modus. [WEB04] |
| Vorlesen | Lokale Speaches-TTS | Deutsche Stimme/Modelldatei/Lizenz prüfen; kein Browsercloud-Fallback. [WEB05] |
| DSS/DS2 | dss-codec isoliert | Decoder im Server, nicht pro Arbeitsplatz; Geräteabschluss/Metadaten separat. [WEB10] |
| Formate | Bestehender Dokumentservice/python-docx; bei nötig isolierter serverseitiger LibreOffice-Konverter | DOCX/ODT/RTF-Dateiverarbeitung, keine Client-COM-Steuerung und keine laufende fremde Officeinstanz. Converter-Makros/Netzwerk deaktivieren. |
| Offline-/Crypto | Browser/WebCrypto und etablierte Serialisierung | Keine eigene Verschlüsselungsprimitive; explizit eingeschränktes Managed-Webprofil. |
| Testautomation | Vorhandene Tests; für UI Playwright sofern nicht vorhanden | Bestehenden Runner nutzen, echte Browser zusätzlich zu Mocks; keine automatische reale Klinikanbindung. |
| Import | .NET10-Datei-/HTTP-/Servicebibliotheken | Keine NAudio-/Office-/Windows-Hotkeyfunktion für Diktat. |

Nicht als bereits vorhandene Speakr-Fähigkeit behaupten: 16-stufige Audioeditliste, klinische Wort-/Audioanker, author/staff-Poolsemantik, alle ursprünglichen Vorlagenformate, Windows-Fremdfenstersteuerung, Aussprache-/Akustiktraining oder Philips-Firmwareverwaltung. Diese Punkte erhalten eigene Implementierungs-/Nachweispakete oder WG.

Speaches dokumentiert transcription-only und TTS, aber die API-Verfügbarkeit belegt keine Dragon-ähnliche persönliche Akustikmodelloptimierung. Training/Negativlexikon/Phonetik werden separat spezifiziert. Keine neue ASR-Engine ohne begründeten Capabilitybefund, keine erste Dummy-Version mit dauerhaft als erledigt markiertem Trainingsknopf.

S00 löst alle zusätzlichen Versionen und Image-/Modellhashes real auf; keine erfundenen Pins. Lizenz-/Sicherheitsprüfung am tatsächlichen Distributionsumfang. Originalarchiv ist vertrauliche Projektunterlage und kein Bestandteil des veröffentlichten Forks. Aktueller Speakr-Releasebezug wurde erneut geprüft [WEB01]; kein Build wurde hier durchgeführt.


---

<!-- Quelle im Bauauftrag: docs/20_Alle_101_Pflichtfunktionen.md -->

# Vollständige Paritätsmatrix – 101 Pflichtfunktionen

Stand 17.11.2026. Alle MUST. Sollverhalten unverändert; Implementierung und Abnahme nicht ausgeführt.



## PAR-ADM-001 – Autoren und Schreibkräfte verwalten

**Sollverhalten:** Autoren und Schreibkräfte getrennt anlegen, bearbeiten und entfernen; Änderungen mit Übernehmen/OK verbindlich speichern.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/0 · Handbuchseiten 4, 5, 6, 7, 18, 19, 20 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `web_design` · **Task:** P01 · **Test:** BT-ADM-001.

**Umsetzung:** Lokale User-ID bleibt zentral. Mehrere Fachrollen zulässig. Entfernen aus aktiver Auswahl ist nicht unkontrolliertes Löschen referenzierter Diktate/Audits.

**Wiederverwendung/Module:** Speakr User/Auth/Admin/Group wiederverwenden; getrennte Autorenressourcenpools und attribut-/aktionsbasierte Zugriffe ergänzen. / `src/clinic/identity/ + src/clinic/policy/ + existing admin UI`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-ADM-002 – Autorenpools mit Mehrfachmitgliedschaft

**Sollverhalten:** Autoren können einem oder mehreren Autorenpools angehören; gemeinsame Ressourcen stehen Mitgliedern des gewählten Pools zur Verfügung.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/1 · Handbuchseiten 9, 16, 17 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `web_design` · **Task:** P01 · **Test:** BT-ADM-002.

**Umsetzung:** Eigene author_pool-Entität mit Mehrfachmitgliedschaften und aktivem Ressourcenpool. Keine Vermischung mit Empfänger-/Schreibpools.

**Wiederverwendung/Module:** Speakr User/Auth/Admin/Group wiederverwenden; getrennte Autorenressourcenpools und attribut-/aktionsbasierte Zugriffe ergänzen. / `src/clinic/identity/ + src/clinic/policy/ + existing admin UI`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-ADM-003 – Schreibkraftpools eigenständig verwalten

**Sollverhalten:** Schreibkraftpools gruppieren berechtigte Schreibkräfte; sie bleiben von Autorenpools getrennt.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/2 · Handbuchseiten 21, 22 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `web_design` · **Task:** P01 · **Test:** BT-ADM-003.

**Umsetzung:** Staff-Pools bleiben eigenständig. Bestehende Speakr-Gruppen nur mit explizitem Typ und zentraler Klinikpolicy weiterverwenden.

**Wiederverwendung/Module:** Speakr User/Auth/Admin/Group wiederverwenden; getrennte Autorenressourcenpools und attribut-/aktionsbasierte Zugriffe ergänzen. / `src/clinic/identity/ + src/clinic/policy/ + existing admin UI`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-ADM-004 – Zugriff nach Autor oder Autorenpool mit Ausnahmen

**Sollverhalten:** Schreibkraftzugriff kann auf einzelne Autoren, Autorenpools mit Ausnahmen oder alle Autoren begrenzt werden.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/3 · Handbuchseiten 38 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `web_design` · **Task:** P01 · **Test:** BT-ADM-004.

**Umsetzung:** author_scope = explicit_users | author_pools | all; deny-Ausnahmen haben Vorrang. Gleiche Predicate für Listen, Suchtreffer, Direktzugriff, Medien und Exporte.

**Wiederverwendung/Module:** Speakr User/Auth/Admin/Group wiederverwenden; getrennte Autorenressourcenpools und attribut-/aktionsbasierte Zugriffe ergänzen. / `src/clinic/identity/ + src/clinic/policy/ + existing admin UI`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-ADM-005 – Aktionen unabhängig voneinander berechtigen

**Sollverhalten:** Rechte steuern Löschen, Archivieren, Umbenennen, Priorität ändern, Audioexport, Umverteilen und Wiederaufnahme fertiggestellter Diktate getrennt.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/4 · Handbuchseiten 34, 35, 36, 37, 38 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `web_design` · **Task:** P01 · **Test:** BT-ADM-005.

**Umsetzung:** Separate Aktionsrechte, unter anderem delete, archive, rename, priority, audio_export, reassign, reopen. UI/Hotkey/API prüfen denselben Guard.

**Wiederverwendung/Module:** Speakr User/Auth/Admin/Group wiederverwenden; getrennte Autorenressourcenpools und attribut-/aktionsbasierte Zugriffe ergänzen. / `src/clinic/identity/ + src/clinic/policy/ + existing admin UI`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-ADM-006 – Autoren-Sichtbereiche und Schreibkraftauswahl

**Sollverhalten:** Autoren können nur eigene, die Diktate ihres Autorenpools oder alle Diktate sehen; die Empfängerauswahl kann auf Schreibpools begrenzt werden.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/5 · Handbuchseiten 35, 36 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `web_design` · **Task:** P01 · **Test:** BT-ADM-006.

**Umsetzung:** Default own; pool/all nur explizit administrativ gewährt und unter Fall-/Mandantenrestriktionen. Empfängerwahlrecht separat, ggf. nur Staff-Pools.

**Wiederverwendung/Module:** Speakr User/Auth/Admin/Group wiederverwenden; getrennte Autorenressourcenpools und attribut-/aktionsbasierte Zugriffe ergänzen. / `src/clinic/identity/ + src/clinic/policy/ + existing admin UI`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-ADM-007 – Voreingestellte Empfänger und Selbstkorrektur

**Sollverhalten:** Neue Diktate gehen an alle berechtigten Schreibkräfte oder die konfigurierte Person/Gruppe; ein Betrieb ohne Schreibkräfte erlaubt Selbstkorrektur.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/6 · Handbuchseiten 5, 18, 28 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `web_design` · **Task:** P01 · **Test:** BT-ADM-007.

**Umsetzung:** Explizite Routingziele all_eligible, person, staff_pool; self-correction ohne Schreibkräfte. Keine künstliche Pflicht zu genau einem Pool für jeden Vorgang.

**Wiederverwendung/Module:** Speakr User/Auth/Admin/Group wiederverwenden; getrennte Autorenressourcenpools und attribut-/aktionsbasierte Zugriffe ergänzen. / `src/clinic/identity/ + src/clinic/policy/ + existing admin UI`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-ADM-008 – Administrative und Statistik-Accounts

**Sollverhalten:** Administration und Statistikzugriff besitzen eigene Berechtigungen, unabhängig von der Autoren-/Schreibkraftfunktion.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/7 · Handbuchseiten 5, 72, 184 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `web_design` · **Task:** P01 · **Test:** BT-ADM-008.

**Umsetzung:** Identity-/System-/Statistikverwaltung getrennt von Audio-/Textleserecht. Statistiker sieht ausschliesslich freigegebene Aggregation/Datensätze.

**Wiederverwendung/Module:** Speakr User/Auth/Admin/Group wiederverwenden; getrennte Autorenressourcenpools und attribut-/aktionsbasierte Zugriffe ergänzen. / `src/clinic/identity/ + src/clinic/policy/ + existing admin UI`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-ADM-009 – Windows-/AD-Auswahl und bestätigter AD-Abgleich

**Sollverhalten:** Benutzer können aus lokalem Windows oder AD gewählt werden; gruppenbasierter Abgleich zeigt neue/entfernte Benutzer und Mitgliedschaften zur Bestätigung. E-Maildaten können aus AD stammen.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/8 · Handbuchseiten 7, 19, 22, 23, 29 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `strict_web_conflict` · **Task:** P01 · **Test:** BT-ADM-009.

**Umsetzung:** Appkonten lokal, AD-Abgleich später serverseitig read-only mit bestätigtem Diff. Lokale Windows-SAM des Endgeräts wird nicht vom Import-Agenten ausgelesen; Webkonflikt explizit offen.

**Wiederverwendung/Module:** Speakr User/Auth/Admin/Group wiederverwenden; getrennte Autorenressourcenpools und attribut-/aktionsbasierte Zugriffe ergänzen. / `src/clinic/identity/ + src/clinic/policy/ + existing admin UI`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-ADM-010 – Windows-SSO mit eindeutigem Kontext

**Sollverhalten:** Aktiviertes SSO verwendet den angemeldeten Windows-Nutzer und verhindert die Auswahl fremder Identitäten; Aktivierung für Client und Workflow getrennt.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/9 · Handbuchseiten 44, 45 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `dependency_validation` · **Task:** P01 · **Test:** BT-ADM-010.

**Umsetzung:** Windows-SSO nur über freigegebenen serverseitigen IWA-/Negotiate-Vertrag und verifiziertes SID-Mapping; keine Client-SSPI-Bibliothek. Initial local; kein Windowsname aus JS als Identität.

**Wiederverwendung/Module:** Speakr User/Auth/Admin/Group wiederverwenden; getrennte Autorenressourcenpools und attribut-/aktionsbasierte Zugriffe ergänzen. / `src/clinic/identity/ + src/clinic/policy/ + existing admin UI`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-ADM-011 – Benutzerstart, letzte Auswahl und Wechsel

**Sollverhalten:** Manueller Start merkt sich den letzten Nutzer; Verknüpfungen können Benutzer/Pool wählen; Wechsel beendet den alten Kontext mit behandelbaren ungesicherten Änderungen.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/10 · Handbuchseiten 76, 77, 79, 119, 120 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `web_design` · **Task:** P01 · **Test:** BT-ADM-011.

**Umsetzung:** Letzte Auswahl/Startargumente sind UI-Hinweise, keine Authentisierung. Wechsel mit Draft-Abbruch/Speichern und Sessiontrennung.

**Wiederverwendung/Module:** Speakr User/Auth/Admin/Group wiederverwenden; getrennte Autorenressourcenpools und attribut-/aktionsbasierte Zugriffe ergänzen. / `src/clinic/identity/ + src/clinic/policy/ + existing admin UI`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-CFG-001 – Zentrale Vorgaben und lokale Änderungen

**Sollverhalten:** Zentral gesetzte Profil-/Workflowvorgaben gelten beim Neustart erneut; nicht zentral fixierte Werte dürfen lokal eingestellt werden.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/11 · Handbuchseiten 32, 33, 54, 55, 56, 97, 102 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `web_design` · **Task:** P04 · **Test:** BT-CFG-001.

**Umsetzung:** Auflösung: Systempflicht > zentraler Nutzer-/Poolwert > erlaubter lokaler Wert. Abbrechen verwirft nur neue Änderungen; Herkunft jedes Werts anzeigen.

**Wiederverwendung/Module:** Vorhandene Speakr-Einstellungen/Storage wiederverwenden; versionierte zentrale Policies, Vorlagen und Metadatenregeln ergänzen. / `src/clinic/configuration/ + existing web settings`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-CFG-002 – Vorlagen zentral speichern und zuordnen

**Sollverhalten:** Benutzer- und Poolvorlagen besitzen Anzeigename, Anmerkung und Einfügemarke; eine Serverkopie bleibt nutzbar, wenn die Ursprungsdatei fehlt.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/12 · Handbuchseiten 42, 43, 44, 145, 155 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `web_design` · **Task:** P04 · **Test:** BT-CFG-002.

**Umsetzung:** Versionierte Serverkopie, Anzeige/Anmerkung/Einfügemarke, Zuordnung User/Autorenpool. Externe Originaldatei wird nach Import nicht vorausgesetzt.

**Wiederverwendung/Module:** Vorhandene Speakr-Einstellungen/Storage wiederverwenden; versionierte zentrale Policies, Vorlagen und Metadatenregeln ergänzen. / `src/clinic/configuration/ + existing web settings`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-CFG-003 – Versandkategorien und Pflichtangaben

**Sollverhalten:** Versandkategorien lassen sich als Sets definieren und beim Client bzw. Recorder verpflichtend abfragen; sie wirken auf Diktatanmerkungen.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/13 · Handbuchseiten 57, 58 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `web_design` · **Task:** P04 · **Test:** BT-CFG-003.

**Umsetzung:** Kategorieset mit required je Recorder/Client, Version im Auftrag fixieren. Keine klinische ID aus Kategorie ableiten.

**Wiederverwendung/Module:** Vorhandene Speakr-Einstellungen/Storage wiederverwenden; versionierte zentrale Policies, Vorlagen und Metadatenregeln ergänzen. / `src/clinic/configuration/ + existing web settings`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-CFG-004 – RegEx-basierte Metadatenzuweisung mit Vorschau

**Sollverhalten:** RegEx-Regeln können Titel, Anmerkung, Dringlichkeit und Frist aus erkanntem Text ableiten; maximale Textmenge und Testvorschau sind konfigurierbar. Integrations-ID-Ziele sind ausgeschlossen.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/14 · Handbuchseiten 47, 48, 49, 50, 51, 52 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `web_design` · **Task:** P04 · **Test:** BT-CFG-004.

**Umsetzung:** Timeout und Eingabegrenze für Regex; Vorschau benutzt denselben Code wie Anwendung. Ziele nur Titel/Anmerkung/Dringlichkeit/Frist, niemals User-/Patient-/Fall-ID.

**Wiederverwendung/Module:** Vorhandene Speakr-Einstellungen/Storage wiederverwenden; versionierte zentrale Policies, Vorlagen und Metadatenregeln ergänzen. / `src/clinic/configuration/ + existing web settings`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-CFG-005 – Konfigurierbare Metadatenspalten

**Sollverhalten:** Diktatliste kann zusätzliche Spalten aus XML-Metadaten sowie Zeit-/Namensformat verwenden.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/15 · Handbuchseiten 55, 56, 57 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `web_design` · **Task:** P04 · **Test:** BT-CFG-005.

**Umsetzung:** XML ohne externe Entitäten/Netzwerk lesen; konfigurierte Felder/Anzeigeformat. Identitäten und UTC-Werte nicht durch Anzeigeformat ändern.

**Wiederverwendung/Module:** Vorhandene Speakr-Einstellungen/Storage wiederverwenden; versionierte zentrale Policies, Vorlagen und Metadatenregeln ergänzen. / `src/clinic/configuration/ + existing web settings`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-REC-001 – Recorder mit Titel-, Aufnahme- und Versandphase

**Sollverhalten:** Autor kann ein Diktat benennen, Audio aufnehmen und vor Versand Metadaten ändern; Titel dient der Identifikation in der Diktatliste.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/16 · Handbuchseiten 128, 129, 130, 131 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `web_design` · **Task:** P05 · **Test:** BT-REC-001.

**Umsetzung:** Titel, Aufnahme und Versand vollständig im Speakr-Webclient. Kein Audio-Capture im Import-Agent.

**Wiederverwendung/Module:** Speakr-Webrecorder/Player, MediaRecorder/Web Audio; keine native Aufnahme. / `src/clinic/audio/ + existing browser recorder`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-REC-002 – Aufnahme- und Wiedergabesteuerung

**Sollverhalten:** Aufnahme, Stop, Wiedergabe, Anfang/Ende und Vor-/Rückspulen sind per UI und dokumentierten Hotkeys bedienbar.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/17 · Handbuchseiten 130, 131, 133 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `web_design` · **Task:** P05 · **Test:** BT-REC-002.

**Umsetzung:** Vorhandenen Webrecorder/Player mit eindeutiger Aufnahme-/Pause-/Stopp- und Cursorsteuerung erweitern; Tastenkürzel sind auf den berechtigten Browserkontext begrenzt.

**Wiederverwendung/Module:** Speakr-Webrecorder/Player, MediaRecorder/Web Audio; keine native Aufnahme. / `src/clinic/audio/ + existing browser recorder`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-REC-003 – Audio einfügen, überschreiben, kürzen und rückgängig

**Sollverhalten:** Recorder unterscheidet Einfügen/Überschreiben, kann ab Cursor abschneiden und letzte Aufnahmeoperationen mit dokumentiertem 16-stufigem Undo zurücknehmen.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/18 · Handbuchseiten 130, 131, 133 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `web_design` · **Task:** P05 · **Test:** BT-REC-003.

**Umsetzung:** Nichtdestruktive Edit Decision List im Web, mindestens 16 Undo-Schritte, Insert/Overwrite/Trim auf Audiosegmentreferenzen. Rendering serverseitig; Originale bleiben unverändert.

**Wiederverwendung/Module:** Speakr-Webrecorder/Player, MediaRecorder/Web Audio; keine native Aufnahme. / `src/clinic/audio/ + existing browser recorder`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-REC-004 – Stimmgesteuerte Aufnahme

**Sollverhalten:** Bei aktivierter Stimmsteuerung wird nur während erkannter Sprache aufgezeichnet; aktivierter Modus ist sichtbar.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/19 · Handbuchseiten 130, 133 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `dependency_validation` · **Task:** P05 · **Test:** BT-REC-004.

**Umsetzung:** Web-Audio/VAD lokal im Browser oder Klinikserver, sichtbarer Zustand, Pre-/Postroll und Referenztest; Rohaufnahme nicht ohne bewussten Modus verlieren.

**Wiederverwendung/Module:** Speakr-Webrecorder/Player, MediaRecorder/Web Audio; keine native Aufnahme. / `src/clinic/audio/ + existing browser recorder`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-REC-005 – Audio-Geräte und Aufnahme-/Wiedergabepegel

**Sollverhalten:** Aufnahme- und Wiedergabegerät sowie unterstützte Pegel lassen sich wählen; Steuergerät und Audiogerät sind unterscheidbar.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/20 · Handbuchseiten 131, 132, 167 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `dependency_validation` · **Task:** P05 · **Test:** BT-REC-005.

**Umsetzung:** getUserMedia/Audioauswahl und soweit unterstützt setSinkId; App-Gain getrennt von hardwareseitigem Windows-Mixerpegel. Capabilitycheck und Gerätetest, kein OS-Mixeragent.

**Wiederverwendung/Module:** Speakr-Webrecorder/Player, MediaRecorder/Web Audio; keine native Aufnahme. / `src/clinic/audio/ + existing browser recorder`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-REC-006 – Versandparameter und optionaler Diktatschutz

**Sollverhalten:** Versand fragt Titel, Anmerkung, Priorität/Frist, ASR, Empfänger, Abnahme und optionalen Diktatpasswortschutz ab.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/21 · Handbuchseiten 81, 82, 85, 131, 177, 178 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `web_design` · **Task:** P05 · **Test:** BT-REC-006.

**Umsetzung:** Formular vollständig: Metadaten, Empfänger, ASR, Abnahme, Passwortschutz. Passwort zusätzlich zur ACL, kein Zugriff nur durch Passwortbesitz; vorhandene Kryptobibliotheken.

**Wiederverwendung/Module:** Speakr-Webrecorder/Player, MediaRecorder/Web Audio; keine native Aufnahme. / `src/clinic/audio/ + existing browser recorder`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-REC-007 – Autarker Recorder und lokales Speichern

**Sollverhalten:** Recorder kann Audio laden, lokal speichern und im autarken Betrieb an einem konfigurierten Ziel ablegen; Serverausfall darf nicht als erfolgreicher Workflowversand erscheinen.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/22 · Handbuchseiten 118, 131, 132 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `dependency_validation` · **Task:** P05 · **Test:** BT-REC-007.

**Umsetzung:** Web-PWA, Datei öffnen/speichern via explizitem Nutzerzugriff; optional verschlüsselter Managed-Browser-Offlineentwurf nach eigenem Gate. BYOD ohne persistentes Audio. Keine Garantie bei geschlossenem Browser/OS-Eviction; kein nativer Recorder.

**Wiederverwendung/Module:** Speakr-Webrecorder/Player, MediaRecorder/Web Audio; keine native Aufnahme. / `src/clinic/audio/ + existing browser recorder`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-REC-008 – Audio-Text-Sitzungen und Textdateien speichern/laden

**Sollverhalten:** Eigene Sitzungen enthalten Audio und bearbeitbaren Text; RTF und TXT können geöffnet/gespeichert werden. Neue Sitzungen müssen verlustfrei wieder geöffnet werden.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/23 · Handbuchseiten 80, 83, 87, 112 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `dependency_validation` · **Task:** P05 · **Test:** BT-REC-008.

**Umsetzung:** Versioniertes .sdict-Webbundle mit Manifest, Audio, Richtext und Ankern; TXT/RTF über geprüfte Parser/Konverter. Reopen-Gleichheit und ZIP-/Parserlimits. Kein Binärformat des Altprogramms nachbauen.

**Wiederverwendung/Module:** Speakr-Webrecorder/Player, MediaRecorder/Web Audio; keine native Aufnahme. / `src/clinic/audio/ + existing browser recorder`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-LIVE-001 – Live-Erkennung im eigenen Diktierfenster

**Sollverhalten:** Gesprochener Text erscheint während der Sitzung im eigenen Diktierfenster; Autor kann direkt korrigieren und Audio plus Text zur delegierten Korrektur senden.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/24 · Handbuchseiten 75, 79, 80, 84 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `dependency_validation` · **Task:** P06 · **Test:** BT-LIVE-001.

**Umsetzung:** Mikrofon im Browser, Audiotransport an eigene Speakr-Sitzung; Speakr delegiert an lokale Speaches-Realtime-Transkription. Kein externes Browser-SpeechRecognition-Backend.

**Wiederverwendung/Module:** Speakr-Webeditor plus lokale Speaches-Realtime-API; keine Desktop-Fokusbrücke. / `src/clinic/live/ + existing web editor`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-LIVE-002 – Diktieren in allgemeine Windows-Textfenster

**Sollverhalten:** Erkannter Text kann an der ausgewählten Position einer unterstützten allgemeinen Desktopanwendung erscheinen; keine KIS-spezifische Datenverbindung.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/25 · Handbuchseiten 75, 79 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `strict_web_conflict` · **Task:** P06 · **Test:** BT-LIVE-002.

**Umsetzung:** Eigener Webeditor und bewusster Transfer implementieren; direkte Texteingabe in beliebige native Fremdfenster bleibt unter Web-only ungeklärt. Nicht über Import-Agent umgehen.

**Wiederverwendung/Module:** Speakr-Webeditor plus lokale Speaches-Realtime-API; keine Desktop-Fokusbrücke. / `src/clinic/live/ + existing web editor`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-LIVE-003 – Sofortige Entwurfssicherung pro Phrase

**Sollverhalten:** SonicPad sichert jede gesprochene Phrase im Entwurf; nach Neustart ist die Entwurfsliste verfügbar, aufbewahrtes Audio bleibt trotz Textkorrektur erhalten.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/26 · Handbuchseiten 82, 83, 86, 87 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `dependency_validation` · **Task:** P06 · **Test:** BT-LIVE-003.

**Umsetzung:** Jede finalisierte Phrase serverseitig mit Originalaudio und Revision bestätigen. Bei Offlineprofil nur verschlüsselte lokale Webpersistenz ausdrücklich quittieren, nicht als Serverversand; bestätigte Bytes nicht mit lesbarer kompletter Datei verwechseln.

**Wiederverwendung/Module:** Speakr-Webeditor plus lokale Speaches-Realtime-API; keine Desktop-Fokusbrücke. / `src/clinic/live/ + existing web editor`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-LIVE-004 – Mehrere Entwürfe und bewusstes Verwerfen

**Sollverhalten:** Mehrere Diktierfenster/Entwürfe können parallel bestehen; Laden, Wechseln, Speichern und Löschen sind getrennt. Beim Schliessen ungesicherter Änderungen wird gefragt.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/27 · Handbuchseiten 82, 83, 84 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `web_design` · **Task:** P06 · **Test:** BT-LIVE-004.

**Umsetzung:** Mehrere draft_id-Namespaces; explizit speichern/verwerfen/abbrechen. Ein Neustart darf nicht Texte zweier Autoren vermischen.

**Wiederverwendung/Module:** Speakr-Webeditor plus lokale Speaches-Realtime-API; keine Desktop-Fokusbrücke. / `src/clinic/live/ + existing web editor`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-LIVE-005 – Rich-Text-Bearbeitung und Versandformat

**Sollverhalten:** Diktierfenster unterstützt Schrift, Ausrichtung, Zeilenabstand und Grafiken; Versand kann Formatierung behalten oder bewusst auf reinen Text reduzieren. Leeren/Schliessen erfolgt nur nach erfolgreicher Übergabe.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/28 · Handbuchseiten 79, 80, 85, 86, 87, 111, 112 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `web_design` · **Task:** P06 · **Test:** BT-LIVE-005.

**Umsetzung:** Kanonisches Rich-Text-Dokument mit kontrollierten lokalen Grafiken; plain-text Export bewusst. Clear/Close nur nach dem richtigen erfolgreichen Empfang, nicht HTTP-Requeststart.

**Wiederverwendung/Module:** Speakr-Webeditor plus lokale Speaches-Realtime-API; keine Desktop-Fokusbrücke. / `src/clinic/live/ + existing web editor`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-LIVE-006 – Diktierfokus an Fenster binden

**Sollverhalten:** SpeechLock bindet Diktat und Befehle an ein unterstütztes Fenster trotz Arbeit in anderem Fenster; Zustand und Umschaltung sind sichtbar.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/29 · Handbuchseiten 98, 99, 110 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `strict_web_conflict` · **Task:** P06 · **Test:** BT-LIVE-006.

**Umsetzung:** Nur Sitzungs-/Draftbindung im eigenen Webclient; native SpeechLock-Teilforderung bleibt blockiert.

**Wiederverwendung/Module:** Speakr-Webeditor plus lokale Speaches-Realtime-API; keine Desktop-Fokusbrücke. / `src/clinic/live/ + existing web editor`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-LIVE-007 – Automatische Fokusbindung und immer oben

**Sollverhalten:** Bestimmte Fenster können beim Fokus automatisch gebunden und optional oben gehalten werden; globale Deaktivierung schaltet diese Funktion aus.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/30 · Handbuchseiten 99, 111, 112 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `strict_web_conflict` · **Task:** P06 · **Test:** BT-LIVE-007.

**Umsetzung:** Webinterne Bindung/Layout zulässig. Keine automatische Fensterklassenerkennung oder Always-on-top-Steuerung fremder Programme.

**Wiederverwendung/Module:** Speakr-Webeditor plus lokale Speaches-Realtime-API; keine Desktop-Fokusbrücke. / `src/clinic/live/ + existing web editor`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-LIVE-008 – Text und Aussprache vorlesen

**Sollverhalten:** Markierter Text oder Text ab Cursor kann vorgelesen werden; Stimme und Geschwindigkeit sind konfigurierbar, fehlende Stimmen werden gemeldet.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/31 · Handbuchseiten 81, 84, 108, 109 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `dependency_validation` · **Task:** P06 · **Test:** BT-LIVE-008.

**Umsetzung:** Lokales TTS über vorhandene Speaches-TTS-Schnittstelle, nur vorab geprüfte Modelldateien ohne Egress; Browserstimme ausschliesslich nach geprüftem lokalen Betrieb. Fehlende deutsche Stimme als Fehler.

**Wiederverwendung/Module:** Speakr-Webeditor plus lokale Speaches-Realtime-API; keine Desktop-Fokusbrücke. / `src/clinic/live/ + existing web editor`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-IMP-001 – Geräte erkennen und Diktate auflisten

**Sollverhalten:** Workflow Pass erkennt angeschlossene Geräte/Wechseldatenträger und zeigt deren Diktate, Geräteanzahl und Status; beobachtete Gerätetypen sind wählbar.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/32 · Handbuchseiten 169, 170, 171, 173, 174, 199, 200 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `dependency_validation` · **Task:** P07 · **Test:** BT-IMP-001.

**Umsetzung:** Registry und aktuelle Geräteansicht getrennt; Geräte-/Treiberstatus, Typfilter, Wiederanschluss dedupliziert. Reales 9600/9620-Labor bleibt Pflicht.

**Wiederverwendung/Module:** Vorhandenen Import-/Storage-/Jobpfad und dss-codec weiterverwenden; Gerätekonfiguration ist separater Adapter, kein Codecmerkmal. / `apps/windows-import/ + src/clinic/ingest/ + web import UI`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-IMP-002 – Autor aus Geräte-ID oder Aufnahme-Autor auflösen

**Sollverhalten:** Exklusive Geräte können über Geräte-ID, geteilte Geräte über das Autor-Feld im DSS/DSSPro-Diktat zugeordnet werden; unbekannte Zuordnung erfordert bewusste Auswahl.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/33 · Handbuchseiten 26, 27, 28, 176 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `dependency_validation` · **Task:** P07 · **Test:** BT-IMP-002.

**Umsetzung:** Exklusive Gerätebindung oder validiertes Header-Autorfeld; unbekannt/manuell eindeutig auflösen. Kein Fallback auf Desktoplogin.

**Wiederverwendung/Module:** Vorhandenen Import-/Storage-/Jobpfad und dss-codec weiterverwenden; Gerätekonfiguration ist separater Adapter, kein Codecmerkmal. / `apps/windows-import/ + src/clinic/ingest/ + web import UI`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-IMP-003 – Manueller und gemeinsamer Geräteversand

**Sollverhalten:** Mehrere Gerätediktate können selektiert, mit Metadaten versehen und gemeinsam versandt werden; Original-/Automatisch-Werte bleiben unterscheidbar.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/34 · Handbuchseiten 171, 176, 177, 178 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `web_design` · **Task:** P07 · **Test:** BT-IMP-003.

**Umsetzung:** Mehrfachauswahl mit tri-state keep/enable/disable für ASR und keep/set für Metadaten; Ergebnisse pro Datei, keine atomare Erfolgsbehauptung des ganzen Stapels.

**Wiederverwendung/Module:** Vorhandenen Import-/Storage-/Jobpfad und dss-codec weiterverwenden; Gerätekonfiguration ist separater Adapter, kein Codecmerkmal. / `apps/windows-import/ + src/clinic/ingest/ + web import UI`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-IMP-004 – Automatischer Versand und EOL-Grenze

**Sollverhalten:** Automatischer Versand verarbeitet nur zugeordnete Autoren; optional nur als beendet/EOL markierte Diktate.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/35 · Handbuchseiten 170, 173, 175 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `web_design` · **Task:** P07 · **Test:** BT-IMP-004.

**Umsetzung:** Autosend separat on/off, EOL-Filter separat on/off. Default nur EOL; ausgeschalteter Filter erlaubt stabile bekannte Aufnahmen gemäss bewusst aktivierter Policy, nennt sie aber nicht EOL.

**Wiederverwendung/Module:** Vorhandenen Import-/Storage-/Jobpfad und dss-codec weiterverwenden; Gerätekonfiguration ist separater Adapter, kein Codecmerkmal. / `apps/windows-import/ + src/clinic/ingest/ + web import UI`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-IMP-005 – Quelllöschung nach bestätigter Übernahme

**Sollverhalten:** Nach erfolgreichem Versand kann ein Diktat automatisch vom Gerät gelöscht werden; manuelles Löschen fragt nach. Neubau löscht erst nach dauerhaft bestätigter Übernahme.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/36 · Handbuchseiten 173, 178 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `web_design` · **Task:** P07 · **Test:** BT-IMP-005.

**Umsetzung:** Funktion implementieren, initial off. Löschrecht, unveränderte Quellversion, durable receipt, Sicherungs-/Retentionfreigabe prüfen; USB-Wechsel vor Löschung blockiert.

**Wiederverwendung/Module:** Vorhandenen Import-/Storage-/Jobpfad und dss-codec weiterverwenden; Gerätekonfiguration ist separater Adapter, kein Codecmerkmal. / `apps/windows-import/ + src/clinic/ingest/ + web import UI`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-IMP-006 – Backupkopie und Aufbewahrungsfrist

**Sollverhalten:** Vor Versand kann Originalaudio in einen konfigurierten Backupordner kopiert werden; Dateiname aus Platzhaltern und optionale Löschfrist sind einstellbar.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/37 · Handbuchseiten 174, 175 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `dependency_validation` · **Task:** P07 · **Test:** BT-IMP-006.

**Umsetzung:** Backup vor Versand, Hashgleichheit und kollisionsfreier Name. Retentionjob nur für eigene Backupobjekte mit genehmigter Frist, niemals beliebige Ordner leeren.

**Wiederverwendung/Module:** Vorhandenen Import-/Storage-/Jobpfad und dss-codec weiterverwenden; Gerätekonfiguration ist separater Adapter, kein Codecmerkmal. / `apps/windows-import/ + src/clinic/ingest/ + web import UI`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-IMP-007 – Datei- und Mehrfachimport von Audio und Dokumenten

**Sollverhalten:** Autoren und Schreibkräfte können Audio WAV/DSS/DS2/AMR sowie dokumentierte Office-/RTF-/TXT-/PDF-/Bilddateien importieren; Mehrfachimport bewahrt einzelne Dateinamen.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/38 · Handbuchseiten 119, 134, 135, 182, 183 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `dependency_validation` · **Task:** P07 · **Test:** BT-IMP-007.

**Umsetzung:** Audiocodecs und Dokumentanhänge getrennt validieren: WAV/DSS/DS2/AMR und dokumentierte Office/RTF/TXT/PDF/Bilder. Importsupport nicht aus Extension ableiten; keine Makroausführung.

**Wiederverwendung/Module:** Vorhandenen Import-/Storage-/Jobpfad und dss-codec weiterverwenden; Gerätekonfiguration ist separater Adapter, kein Codecmerkmal. / `apps/windows-import/ + src/clinic/ingest/ + web import UI`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-IMP-008 – Dropfolder-Autoversand

**Sollverhalten:** Mehrere Eingangspfade mit Autor, Pool, Endungsmuster, Priorität, Sprache und ASR-Wahl werden überwacht; dokumentierter Altumfang umfasst keine rekursiven Unterordner.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/39 · Handbuchseiten 61, 62, 194 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `web_design` · **Task:** P07 · **Test:** BT-IMP-008.

**Umsetzung:** Mehrere benannte Dropfolder, Autor/Autorenpool/Pattern/Priorität/Sprache/ASR; non-recursive Default als Referenzvertrag, stabile Snapshots und Retryledger.

**Wiederverwendung/Module:** Vorhandenen Import-/Storage-/Jobpfad und dss-codec weiterverwenden; Gerätekonfiguration ist separater Adapter, kein Codecmerkmal. / `apps/windows-import/ + src/clinic/ingest/ + web import UI`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-IMP-009 – Geräteverwaltung, Autorenlisten und Schutzoptionen

**Sollverhalten:** Geräte können inventarisiert und mit Autoren-/Listenwerten, Aufnahmeverschlüsselung, PIN/Sperrregeln und Energiesparwerten konfiguriert werden, soweit Gerät unterstützt.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/40 · Handbuchseiten 63, 64, 65, 66, 67, 68, 69, 70 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `strict_web_conflict` · **Task:** P07 · **Test:** BT-IMP-009.

**Umsetzung:** Import-Agent darf Inventar und vorhandene Metadaten lesen, nicht PIN/Authorlisten/Recording-Settings schreiben. Webdirekter Herstellerweg nur nach realem Nachweis; Funktionslücke offen lassen.

**Wiederverwendung/Module:** Vorhandenen Import-/Storage-/Jobpfad und dss-codec weiterverwenden; Gerätekonfiguration ist separater Adapter, kein Codecmerkmal. / `apps/windows-import/ + src/clinic/ingest/ + web import UI`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-IMP-010 – Firmwarebestand und Versionsvergleich

**Sollverhalten:** Geräteverwaltung hinterlegt Firmware pro Typ; Workflow Pass vergleicht bei Anschluss Versionen und kann ein älteres Gerät aktualisieren.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/41 · Handbuchseiten 71, 72 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `strict_web_conflict` · **Task:** P07 · **Test:** BT-IMP-010.

**Umsetzung:** Webinventar mit Firmwarebestand und Vergleich. Flashen nicht im Import-Agent; bis zu freigegebenem direktem Webweg ein Paritätsblocker.

**Wiederverwendung/Module:** Vorhandenen Import-/Storage-/Jobpfad und dss-codec weiterverwenden; Gerätekonfiguration ist separater Adapter, kein Codecmerkmal. / `apps/windows-import/ + src/clinic/ingest/ + web import UI`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-WF-001 – Arbeitsliste für Autoren und Schreibkräfte

**Sollverhalten:** Workflowzentrale zeigt berechtigte Diktate mit Titel, Status, Autor, Priorität/Frist, Bearbeiter/Zeit, Anmerkung, Erstellzeit, Dauer, ID und Empfänger.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/42 · Handbuchseiten 119, 121, 125, 126 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `web_design` · **Task:** P02 · **Test:** BT-WF-001.

**Umsetzung:** Alle Pflichtspalten und Dokument-only-Fälle; Autor/Schreibkraft haben passenden Öffnungsmodus, gemeinsame Policy.

**Wiederverwendung/Module:** Speakr Recording/Group/Jobs als Basis; unterschiedliche Statusachsen, Aktionsguards, Listenprojektion und Freigaben ergänzen. / `src/clinic/workflow/ + existing web lists`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-WF-002 – Automatische und manuelle Aktualisierung

**Sollverhalten:** Arbeitsliste aktualisiert sich automatisch; F5 erzwingt eine Aktualisierung.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/43 · Handbuchseiten 121, 122, 123 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `web_design` · **Task:** P02 · **Test:** BT-WF-002.

**Umsetzung:** UI-Push/Poll plus F5 in Liste; F5 im Editor hat anderen gebundenen Befehl. Kontextdispatcher verhindert Browserreload.

**Wiederverwendung/Module:** Speakr Recording/Group/Jobs als Basis; unterschiedliche Statusachsen, Aktionsguards, Listenprojektion und Freigaben ergänzen. / `src/clinic/workflow/ + existing web lists`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-WF-003 – Gespeicherte Filter und Metadatensuche

**Sollverhalten:** Bis zu 20 benannte Filter kombinieren Autor/Pool und Status; Suche findet Begriffe in Diktatmetadaten und markiert Treffer.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/44 · Handbuchseiten 123, 128 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `web_design` · **Task:** P02 · **Test:** BT-WF-003.

**Umsetzung:** 20 benannte Filter mindestens referenztreu, geplanter Grenzwert 20 mit verständlicher Ablehnung des 21. Filters; Suchhighlight nach ACL.

**Wiederverwendung/Module:** Speakr Recording/Group/Jobs als Basis; unterschiedliche Statusachsen, Aktionsguards, Listenprojektion und Freigaben ergänzen. / `src/clinic/workflow/ + existing web lists`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-WF-004 – Sortieren, Gruppieren und Layout merken

**Sollverhalten:** Spalten lassen sich verschieben, sortieren und nach mehreren Merkmalen gruppieren; Fenster-/Listenlayout wird pro Nutzer erinnert.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/45 · Handbuchseiten 127, 128, 164, 165 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `web_design` · **Task:** P02 · **Test:** BT-WF-004.

**Umsetzung:** Nutzergebundenes Spalten-/Gruppier-/Sortierprofil; keine vom anderen Konto übernommenen Layouts auf Shared Workstations.

**Wiederverwendung/Module:** Speakr Recording/Group/Jobs als Basis; unterschiedliche Statusachsen, Aktionsguards, Listenprojektion und Freigaben ergänzen. / `src/clinic/workflow/ + existing web lists`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-WF-005 – Diktatmetadaten bearbeiten und neu zuweisen

**Sollverhalten:** Berechtigte Nutzer können Titel, Anmerkung, Priorität, Frist und zuständige Schreibkraft ändern.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/46 · Handbuchseiten 123, 126, 135, 136 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `web_design` · **Task:** P02 · **Test:** BT-WF-005.

**Umsetzung:** Metadatenversion und Zuweisung getrennt vom Text; If-Match/Lease und Audit. Umverteilen widerruft laufenden Altzugriff.

**Wiederverwendung/Module:** Speakr Recording/Group/Jobs als Basis; unterschiedliche Statusachsen, Aktionsguards, Listenprojektion und Freigaben ergänzen. / `src/clinic/workflow/ + existing web lists`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-WF-006 – Verlauf eines Diktats anzeigen

**Sollverhalten:** Zu einem Diktat können Log-/Verlaufsinformationen aufgerufen werden.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/47 · Handbuchseiten 47, 124 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `web_design` · **Task:** P02 · **Test:** BT-WF-006.

**Umsetzung:** Objektgebundener Verlauf inkl. Quittungen/Fehler, nicht technische Logs mit Patiententext. Ctrl+I gleiche Berechtigung wie Menü.

**Wiederverwendung/Module:** Speakr Recording/Group/Jobs als Basis; unterschiedliche Statusachsen, Aktionsguards, Listenprojektion und Freigaben ergänzen. / `src/clinic/workflow/ + existing web lists`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-WF-007 – Exklusive Bearbeitung und Freigabe

**Sollverhalten:** Öffnen des Vorgangs setzt In Bearbeitung und verhindert paralleles Bearbeiten; Schliessen gibt frei.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/48 · Handbuchseiten 125, 135, 136, 137 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `web_design` · **Task:** P02 · **Test:** BT-WF-007.

**Umsetzung:** Renewable Lease + fencing + Version. Crash-Recovery gibt keine bestätigten Texte auf; normal close release und getesteter Timeout.

**Wiederverwendung/Module:** Speakr Recording/Group/Jobs als Basis; unterschiedliche Statusachsen, Aktionsguards, Listenprojektion und Freigaben ergänzen. / `src/clinic/workflow/ + existing web lists`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-WF-008 – Vorbereitung, Abschrift und Korrektur unterscheiden

**Sollverhalten:** Neu angelegt/In Vorbereitung unterscheiden sich von Zu schreiben und Zu korrigieren; erfolgreiche Spracherkennung führt zur Korrektur, Audio ohne Erkennung zur Abschrift.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/49 · Handbuchseiten 125, 126, 131, 137, 177 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `web_design` · **Task:** P02 · **Test:** BT-WF-008.

**Umsetzung:** Mehrdimensionale Zustände in referenznahe UI projizieren. Textloser ASR-Fehler ist nicht korrektur- oder freigabefertig.

**Wiederverwendung/Module:** Speakr Recording/Group/Jobs als Basis; unterschiedliche Statusachsen, Aktionsguards, Listenprojektion und Freigaben ergänzen. / `src/clinic/workflow/ + existing web lists`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-WF-009 – Abnahme aktivieren und Berechtigte festlegen

**Sollverhalten:** Mit Abnahme führt nach Korrektur zur Freigabe durch Autor oder regelbasiert berechtigte Autoren/Autorenpools mit Ausnahmen.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/50 · Handbuchseiten 12, 13, 14, 28, 29, 30, 31, 161 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `web_design` · **Task:** P02 · **Test:** BT-WF-009.

**Umsetzung:** approval_required pro Auftrag/Policy; Abnahme durch explizite Autoren-/Poolregeln mit Ausnahmen. Optionaler Zusatzschritt ersetzt nicht die menschliche Korrekturbestätigung.

**Wiederverwendung/Module:** Speakr Recording/Group/Jobs als Basis; unterschiedliche Statusachsen, Aktionsguards, Listenprojektion und Freigaben ergänzen. / `src/clinic/workflow/ + existing web lists`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-WF-010 – Rückweisung und erneute Abnahme

**Sollverhalten:** Abnahmeberechtigter kann bestätigen oder zur Schreibkraft zurückverweisen; Bestätigung ermöglicht anschliessende Fertigstellung.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/51 · Handbuchseiten 161 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `web_design` · **Task:** P02 · **Test:** BT-WF-010.

**Umsetzung:** Zurückweisung mit Grund an Bearbeitung, Freigabe an exakte aktuelle Revision; neue Korrektur erfordert erneute ggf. Pflichtabnahme.

**Wiederverwendung/Module:** Speakr Recording/Group/Jobs als Basis; unterschiedliche Statusachsen, Aktionsguards, Listenprojektion und Freigaben ergänzen. / `src/clinic/workflow/ + existing web lists`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-WF-011 – Fertigstellen und Weiterverarbeitung

**Sollverhalten:** F5/Fertigstellen öffnet Weiterverarbeitungsoptionen; bestätigte Optionen werden ausgeführt, Vorgang wird fertig gemeldet und Editor geschlossen. Ohne Export bleibt der fertige Fall im Workflow.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/52 · Handbuchseiten 147, 148, 149, 158 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `web_design` · **Task:** P02 · **Test:** BT-WF-011.

**Umsetzung:** Abschlussdialog separat von Exporterfolg. Ohne Export gültiger Abschluss, mit mehreren Zielen je Ziel eigenes Ledger. Abbruch verändert keinen Fachstatus.

**Wiederverwendung/Module:** Speakr Recording/Group/Jobs als Basis; unterschiedliche Statusachsen, Aktionsguards, Listenprojektion und Freigaben ergänzen. / `src/clinic/workflow/ + existing web lists`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-WF-012 – Fertiges Diktat wieder zur Korrektur öffnen

**Sollverhalten:** Autor oder entsprechend berechtigte Schreibkraft kann einen fertigen Fall entriegeln, korrigieren oder erneut delegieren.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/53 · Handbuchseiten 38, 138, 144, 162 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `web_design` · **Task:** P02 · **Test:** BT-WF-012.

**Umsetzung:** Reopen ist explizites Recht und neue Arbeitsrevision, nie Mutieren eines historisch finalen Dokuments; KIS braucht Nachtragsprozess.

**Wiederverwendung/Module:** Speakr Recording/Group/Jobs als Basis; unterschiedliche Statusachsen, Aktionsguards, Listenprojektion und Freigaben ergänzen. / `src/clinic/workflow/ + existing web lists`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-WF-013 – Archivieren, Löschen und Aufbewahrungsfrist

**Sollverhalten:** Berechtigte Nutzer melden Fälle zum Archivieren/Löschen; automatische Behandlung fertiger Diktate nach Tagen ist konfigurierbar. Archiv ist nach Nutzer-ID/Monat organisiert und nicht mehr aktive Arbeitsliste.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/54 · Handbuchseiten 36, 37, 45, 52, 53, 54, 124, 125, 126 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `web_design` · **Task:** P02 · **Test:** BT-WF-013.

**Umsetzung:** Archivprojektion User-ID/Monat und Rechtsprüfung; Delete/Archive unterscheidbar. Retention parametrisiert, Default keine automatische Löschung.

**Wiederverwendung/Module:** Speakr Recording/Group/Jobs als Basis; unterschiedliche Statusachsen, Aktionsguards, Listenprojektion und Freigaben ergänzen. / `src/clinic/workflow/ + existing web lists`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-WF-014 – Benachrichtigungen bei Abnahme und Fertigstellung

**Sollverhalten:** Autoren können Benachrichtigungen für zur Abnahme bereite oder fertiggestellte Diktate erhalten; lokale Hinweise sind konfigurierbar.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/55 · Handbuchseiten 11, 12, 29, 128, 164, 165 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `web_design` · **Task:** P02 · **Test:** BT-WF-014.

**Umsetzung:** In-App-/Desktop-Hinweise; konfigurierter interner Mailadapter optional. SMTP-Ausfall verhindert nicht lokalen Start oder fachlichen Abschluss.

**Wiederverwendung/Module:** Speakr Recording/Group/Jobs als Basis; unterschiedliche Statusachsen, Aktionsguards, Listenprojektion und Freigaben ergänzen. / `src/clinic/workflow/ + existing web lists`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-COR-001 – Manuelle Abschrift mit Player und Vorlage

**Sollverhalten:** Audio ohne ASR wird mit frei positionierbarem Player abgespielt; Erfassen öffnet eine passende Benutzer-/Poolvorlage oder Dateiauswahl.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/56 · Handbuchseiten 143, 144, 145, 146, 147 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `web_design` · **Task:** P08 · **Test:** BT-COR-001.

**Umsetzung:** Frei platzierbarer Player innerhalb der Weboberfläche, vorhandenen Browserplayer verwenden; Benutzer-/Poolvorlage und Dateiupload anbieten. Kein zusätzliches natives Playerfenster.

**Wiederverwendung/Module:** Speakr-Player/Editor weiterverwenden; stabile Segmentanker, Wort-/Phrasenbezug und ergonomische Korrekturmodi ergänzen. / `src/clinic/review/ + existing browser editor/player`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-COR-002 – Synchroner Text-Audio-Editor

**Sollverhalten:** Korrektur erhält Verbindung zwischen Wort-/Phrasenposition und Audio auch bei Übertippen, Formatieren und Verschieben von Textpassagen.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/57 · Handbuchseiten 137, 138, 139, 140 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `dependency_validation` · **Task:** P08 · **Test:** BT-COR-002.

**Umsetzung:** Stabile Textsegment-IDs mit immutable audio spans; edit/format/move transformiert Anker. Rein getippter Text ohne erfundene Audiozuordnung.

**Wiederverwendung/Module:** Speakr-Player/Editor weiterverwenden; stabile Segmentanker, Wort-/Phrasenbezug und ergonomische Korrekturmodi ergänzen. / `src/clinic/review/ + existing browser editor/player`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-COR-003 – Wellenform, aktuelle Phrase und Erkennungsmarken

**Sollverhalten:** Korrektur zeigt skalierbare/ausblendbare Wellenform, aktuelle Phrase und sichtbare Bereiche für Erkennung, Befehle, Unsicherheit oder fehlende Erkennung.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/58 · Handbuchseiten 139, 140 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `dependency_validation` · **Task:** P08 · **Test:** BT-COR-003.

**Umsetzung:** Vorhandene Wellenform soweit vorhanden erweitern; Skalierung, Phrase, Command-/Unknown-/Unrecognizedmarkierungen mit nachvollziehbarer Herkunft.

**Wiederverwendung/Module:** Speakr-Player/Editor weiterverwenden; stabile Segmentanker, Wort-/Phrasenbezug und ergonomische Korrekturmodi ergänzen. / `src/clinic/review/ + existing browser editor/player`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-COR-004 – Tempo und Spulen

**Sollverhalten:** Wiedergabe lässt sich bis Faktor 0.5/2 verändern; Rechtsklick setzt Normaltempo; Vor-/Rückspulen ist per Tastatur/Fusssteuerung möglich.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/59 · Handbuchseiten 140, 144, 147 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `dependency_validation` · **Task:** P08 · **Test:** BT-COR-004.

**Umsetzung:** 0.5x bis 2x und Normaltempo per Rechtsklick im Browserplayer; Audiozeit statt Wandzeit, getestetes Web-Audio-/Playback-Stretching, fokussierte Tastatur-/WebHID-Bedienung.

**Wiederverwendung/Module:** Speakr-Player/Editor weiterverwenden; stabile Segmentanker, Wort-/Phrasenbezug und ergonomische Korrekturmodi ergänzen. / `src/clinic/review/ + existing browser editor/player`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-COR-005 – Zwei Korrekturmodi und automatische Rücksprünge

**Sollverhalten:** Wiedergabe kann beim Tippen stoppen oder während Übertippen weiterlaufen; Start kann um konfigurierte Zeit oder unterstützte Wortzahl zurückspringen.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/60 · Handbuchseiten 86, 140, 141, 142, 146, 147 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `dependency_validation` · **Task:** P08 · **Test:** BT-COR-005.

**Umsetzung:** Zwei explicit modes stop_on_type / continue_on_type; backstep ms oder Wörter nur bei belegten Wortankern, nicht geschätzte Wörter.

**Wiederverwendung/Module:** Speakr-Player/Editor weiterverwenden; stabile Segmentanker, Wort-/Phrasenbezug und ergonomische Korrekturmodi ergänzen. / `src/clinic/review/ + existing browser editor/player`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-COR-006 – Zwischenspeichern und an Position fortsetzen

**Sollverhalten:** Korrekturen lassen sich während Bearbeitung speichern; Unterbrechung erhält Textstand und Wiedergabeposition.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/61 · Handbuchseiten 137, 138, 143, 144 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `web_design` · **Task:** P08 · **Test:** BT-COR-006.

**Umsetzung:** Autosave und Ctrl+S speichern Textrevision plus Abspielposition atomar nachvollziehbar; Konflikt nicht still überschreiben.

**Wiederverwendung/Module:** Speakr-Player/Editor weiterverwenden; stabile Segmentanker, Wort-/Phrasenbezug und ergonomische Korrekturmodi ergänzen. / `src/clinic/review/ + existing browser editor/player`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-COR-007 – Audioindizes verwalten

**Sollverhalten:** Benutzer können benannte/verwaltete Indizes an Audiopositionen setzen und aus einer Indexliste anspringen.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/62 · Handbuchseiten 145, 146, 147 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `web_design` · **Task:** P08 · **Test:** BT-COR-007.

**Umsetzung:** Index ist eigener Name + audio_revision + Position; Editliste transformiert Position oder meldet ungültig, löscht kein Audio.

**Wiederverwendung/Module:** Speakr-Player/Editor weiterverwenden; stabile Segmentanker, Wort-/Phrasenbezug und ergonomische Korrekturmodi ergänzen. / `src/clinic/review/ + existing browser editor/player`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-COR-008 – Automatische Wiedergabelautstärke

**Sollverhalten:** AGC kann die Wiedergabelautstärke angleichen, ohne das unveränderte Originaldiktat zu ersetzen.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/63 · Handbuchseiten 86, 145 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `dependency_validation` · **Task:** P08 · **Test:** BT-COR-008.

**Umsetzung:** AGC nur Wiedergabederivat, Originalhash bleibt gleich. Kein Gleichsetzen mit besserer Erkennungsqualität.

**Wiederverwendung/Module:** Speakr-Player/Editor weiterverwenden; stabile Segmentanker, Wort-/Phrasenbezug und ergonomische Korrekturmodi ergänzen. / `src/clinic/review/ + existing browser editor/player`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-COR-009 – Editierfunktionen, Auswahl und Phrase vormerken

**Sollverhalten:** Editor unterstützt Undo/Redo, Zwischenablage, Groß-/Kleinschreibung, Wort-/Absatzauswahl und Vormerken einer Phrase für spätere Vokabularprüfung.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/64 · Handbuchseiten 138, 139, 140, 141, 142, 159, 160, 161 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `web_design` · **Task:** P08 · **Test:** BT-COR-009.

**Umsetzung:** Editoperationen, Case/Selection und Vokabular-Vormerkliste; Vorschlag != veröffentlichter Wortschatz.

**Wiederverwendung/Module:** Speakr-Player/Editor weiterverwenden; stabile Segmentanker, Wort-/Phrasenbezug und ergonomische Korrekturmodi ergänzen. / `src/clinic/review/ + existing browser editor/player`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-COR-010 – Korrekturplatz ohne ASR-Installation

**Sollverhalten:** Bearbeiten eines bereits erkannten Diktats braucht am Schreibplatz keine lokale Dragon-/ASR-Engine; Text und Audio bleiben verfügbar.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/65 · Handbuchseiten 80, 137, 181 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `web_design` · **Task:** P08 · **Test:** BT-COR-010.

**Umsetzung:** Schreibplatz benötigt keine lokale ASR-/Dragon-Installation. Vorhandene Transkripte/Audio auch bei Worker-Ausfall bearbeiten.

**Wiederverwendung/Module:** Speakr-Player/Editor weiterverwenden; stabile Segmentanker, Wort-/Phrasenbezug und ergonomische Korrekturmodi ergänzen. / `src/clinic/review/ + existing browser editor/player`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-VOC-001 – Benutzer- und Poolwortschatz

**Sollverhalten:** Wörter/Phrasen besitzen geschriebene und optional gesprochene Form; persönliche und gemeinsame Poolwortlisten sind getrennt pflegbar.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/66 · Handbuchseiten 16, 34, 35, 39, 40 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `web_design` · **Task:** P09 · **Test:** BT-VOC-001.

**Umsetzung:** Persönliche und Autorenpool-Lexika mit written/spoken form, Version und Vorschlags-/Freigabestatus; getrennt von Staff-Pools.

**Wiederverwendung/Module:** Kein eigener Spracherkenner; versionierte Wörter-/Befehlsverwaltung ergänzen und tatsächliche Enginewirkung messen. / `src/clinic/vocabulary/ + web vocabulary UI`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-VOC-002 – Textwortlisten importieren und exportieren

**Sollverhalten:** Wortlisten können aus Text importiert und als vollständige Textliste exportiert werden. Es werden neu erstellte Testlisten verwendet, keine Altbestandsmigration.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/67 · Handbuchseiten 40 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `web_design` · **Task:** P09 · **Test:** BT-VOC-002.

**Umsetzung:** UTF-8/definierte Legacyencodingoptionen, Preview, deterministische Duplikatregel und Roundtrip; nur neue Testlisten.

**Wiederverwendung/Module:** Kein eigener Spracherkenner; versionierte Wörter-/Befehlsverwaltung ergänzen und tatsächliche Enginewirkung messen. / `src/clinic/vocabulary/ + web vocabulary UI`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-VOC-003 – Negativwortliste und Reaktivierung

**Sollverhalten:** Gelöschte Wörter werden für Nutzer oder Pool unterdrückt; spätere Reaktivierung ist möglich. Unterdrückung bleibt unabhängig von einem neu geladenen Profil wirksam.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/68 · Handbuchseiten 39, 40, 90, 91, 95 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `dependency_validation` · **Task:** P09 · **Test:** BT-VOC-003.

**Umsetzung:** Persistente Negativliste und Reaktivierung müssen Enginewirkung nachweisen; Prompt oder Ergebnis-nachträglich-löschen ist kein harter Lexikonausschluss.

**Wiederverwendung/Module:** Kein eigener Spracherkenner; versionierte Wörter-/Befehlsverwaltung ergänzen und tatsächliche Enginewirkung messen. / `src/clinic/vocabulary/ + web vocabulary UI`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-VOC-004 – Vorbehaltswortliste prüfen

**Sollverhalten:** Änderungen eingeschränkt berechtigter Nutzer werden vorgemerkt; Prüfer entscheidet Pool, nur Nutzer oder verwerfen, jeweils für Hinzufügen und Löschen.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/69 · Handbuchseiten 34, 35, 40, 41 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `web_design` · **Task:** P09 · **Test:** BT-VOC-004.

**Umsetzung:** Reviewliste mit add/remove und Entscheidung personal/pool/reject; offene Änderungen haben keine globale ASR-Wirkung.

**Wiederverwendung/Module:** Kein eigener Spracherkenner; versionierte Wörter-/Befehlsverwaltung ergänzen und tatsächliche Enginewirkung messen. / `src/clinic/vocabulary/ + web vocabulary UI`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-VOC-005 – Sitzungsänderungen prüfen oder zurückstellen

**Sollverhalten:** Neue/gelöschte Wörter und neue/geänderte Befehle werden beim Sitzungsende geprüft, Nutzer/Pool zugeordnet oder verworfen; Abbruch merkt sie für später.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/70 · Handbuchseiten 93, 94, 95, 96, 97, 159, 160, 161 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `web_design` · **Task:** P09 · **Test:** BT-VOC-005.

**Umsetzung:** Sitzungsende stellt Wort-/Befehlsänderungen zur Prüfung. Abbrechen bewahrt pending; explizite Freigabe erzeugt neue Ressourcenversion.

**Wiederverwendung/Module:** Kein eigener Spracherkenner; versionierte Wörter-/Befehlsverwaltung ergänzen und tatsächliche Enginewirkung messen. / `src/clinic/vocabulary/ + web vocabulary UI`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-VOC-006 – Vokabular durchsuchen und aus Markierung ergänzen

**Sollverhalten:** Vokabularsuche bietet Anfang/Ende/Vorkommen, geschriebene/gesprochene Form und passende Filter; markierter Text kann direkt als Phrase übernommen werden.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/71 · Handbuchseiten 88, 89, 90, 91 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `web_design` · **Task:** P09 · **Test:** BT-VOC-006.

**Umsetzung:** Search prefix/suffix/contains über written/spoken; Markierung als editierbarer Vorschlag und bestätigte Mehrfachlöschung.

**Wiederverwendung/Module:** Kein eigener Spracherkenner; versionierte Wörter-/Befehlsverwaltung ergänzen und tatsächliche Enginewirkung messen. / `src/clinic/vocabulary/ + web vocabulary UI`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-VOC-007 – Aussprache trainieren und Phonetik bearbeiten

**Sollverhalten:** Je nach Erkennerversion kann Aussprache trainiert, vorgelesen und als phonetische Formen bearbeitet werden; das Handbuch schränkt Bearbeitung bereits für Dragon 11 ein.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/72 · Handbuchseiten 91, 92, 93, 108, 109 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `dependency_validation` · **Task:** P09 · **Test:** BT-VOC-007.

**Umsetzung:** Phonetik-/Trainingsfähigkeit der Referenzversion und der neuen Engine getrennt prüfen. Keine leeren Schaltflächen, keine Behauptung dass Vorlesen Training sei.

**Wiederverwendung/Module:** Kein eigener Spracherkenner; versionierte Wörter-/Befehlsverwaltung ergänzen und tatsächliche Enginewirkung messen. / `src/clinic/vocabulary/ + web vocabulary UI`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-VOC-008 – Befehle, Makros und Textbausteine verwalten

**Sollverhalten:** Persönliche/Poolbefehle sind verwaltbar und exportierbar; Benutzerbefehle können vor/nach einem eingebauten Kommando Aktionen ausführen und in Menüs erscheinen. Globale Text-/Grafikbausteine sind beim Diktieren nutzbar.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/73 · Handbuchseiten 41, 42, 96, 113, 114, 115, 116, 117, 130 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `dependency_validation` · **Task:** P09 · **Test:** BT-VOC-008.

**Umsetzung:** Versionierte persönliche/Pool-Befehle mit before/after-Hooks auf erlaubte Webaktionen/Text-/Grafikbausteine, ohne eval/Shell/OS-Input. Native Makroaktionen nicht vortäuschen.

**Wiederverwendung/Module:** Kein eigener Spracherkenner; versionierte Wörter-/Befehlsverwaltung ergänzen und tatsächliche Enginewirkung messen. / `src/clinic/vocabulary/ + web vocabulary UI`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-ASR-001 – Lokale Hintergrundtranskription

**Sollverhalten:** Zur Hintergrund-Erkennung markierte Audioaufträge werden in Text plus Korrekturinformationen umgesetzt; Korrektur ist ein nachgelagerter menschlicher Schritt.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/74 · Handbuchseiten 26, 28, 59, 60, 61, 137, 177, 194 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `web_design` · **Task:** P10 · **Test:** BT-ASR-001.

**Umsetzung:** Gepinnte lokale Batch-ASR, Text und Zeitbezug, defekte Eingaben isolieren. Unabhängiger menschlicher Reviewstatus.

**Wiederverwendung/Module:** Speaches/faster-whisper als Basis; Fähigkeitsvertrag, Profilspeicher und echte getrennte Anpassungsjobs, nicht vorgetäuschtes Lernen. / `src/clinic/asr/ + existing transcription connector`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-ASR-002 – Getrennte Online-/Offlineprofile und Quellen

**Sollverhalten:** Autoren können für Live- und Hintergrund-Erkennung getrennte Profile für unterschiedliche Aufnahmequellen führen; ohne Trennung kann ein Profil beide Aufgaben bedienen.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/75 · Handbuchseiten 9, 24, 25, 26 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `dependency_validation` · **Task:** P10 · **Test:** BT-ASR-002.

**Umsetzung:** Author+source+mode-basierte Profilreferenzen, shared/separate explizit. Model-/Adapterversionen, Lexika und tatsächliche trainierbare Komponenten getrennt.

**Wiederverwendung/Module:** Speaches/faster-whisper als Basis; Fähigkeitsvertrag, Profilspeicher und echte getrennte Anpassungsjobs, nicht vorgetäuschtes Lernen. / `src/clinic/asr/ + existing transcription connector`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-ASR-003 – ASR-Wartezeit mit manueller Ersatzbearbeitung

**Sollverhalten:** Nach konfigurierbarer maximaler Wartezeit kann Hintergrund-Erkennung übersprungen und manuelle Abschrift angeboten werden.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/76 · Handbuchseiten 47, 52 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `web_design` · **Task:** P10 · **Test:** BT-ASR-003.

**Umsetzung:** Konfigurierbarer Timeout/Fallback, deaktivierbar. Verspätete ASR darf manuell korrigierte Revision niemals überschreiben.

**Wiederverwendung/Module:** Speaches/faster-whisper als Basis; Fähigkeitsvertrag, Profilspeicher und echte getrennte Anpassungsjobs, nicht vorgetäuschtes Lernen. / `src/clinic/asr/ + existing transcription connector`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-ASR-004 – Optimierung aus geprüfter Korrektur

**Sollverhalten:** Fertigstellung kann geprüften Text oder Text+Audio zur Verbesserung von Wortschatz/Sprach-/Akustikmodell verwenden; Text+Audio verlangt vollständige Übereinstimmung.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/77 · Handbuchseiten 60, 61, 149 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `dependency_validation` · **Task:** P10 · **Test:** BT-ASR-004.

**Umsetzung:** Korrekturtraining ist ein echter lokaler Trainings-/Adaptionsjob mit evaluiertem Modellartefakt. Text-only vs acoustic-data trennen; LLM-Nachbearbeitung/Hotwordprompt kein Ersatz.

**Wiederverwendung/Module:** Speaches/faster-whisper als Basis; Fähigkeitsvertrag, Profilspeicher und echte getrennte Anpassungsjobs, nicht vorgetäuschtes Lernen. / `src/clinic/asr/ + existing transcription connector`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-ASR-005 – Optimierungsfenster und Mindestdatenmenge

**Sollverhalten:** Zeitfenster können aufwendige Modelloptimierungen begrenzen; akustische Daten können gesammelt werden, bis genügend Material vorliegt.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/78 · Handbuchseiten 59, 60, 61 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `dependency_validation` · **Task:** P10 · **Test:** BT-ASR-005.

**Umsetzung:** Optimierungsfenster und Mindestmenge versionierte Policy; zu wenig Daten => waiting_data, nicht succeeded. Erkennungsjobs separat priorisieren.

**Wiederverwendung/Module:** Speaches/faster-whisper als Basis; Fähigkeitsvertrag, Profilspeicher und echte getrennte Anpassungsjobs, nicht vorgetäuschtes Lernen. / `src/clinic/asr/ + existing transcription connector`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-ASR-006 – Profil-Speicherung und Arbeitsstationswechsel

**Sollverhalten:** Profiländerungen lassen sich nie/nach Rückfrage/immer speichern und optional zentral hinterlegen; ein neuer Arbeitsplatz kann den gespeicherten Stand laden.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/79 · Handbuchseiten 24, 25, 32, 33, 97, 99, 100, 102 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `dependency_validation` · **Task:** P10 · **Test:** BT-ASR-006.

**Umsetzung:** Never/ask/always speichern, zentral versionieren, Konflikte am zweiten Arbeitsplatz, keine biometrische Sprecheridentifikation.

**Wiederverwendung/Module:** Speaches/faster-whisper als Basis; Fähigkeitsvertrag, Profilspeicher und echte getrennte Anpassungsjobs, nicht vorgetäuschtes Lernen. / `src/clinic/asr/ + existing transcription connector`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-ASR-007 – Diktierquelle und Versand-Audioformat

**Sollverhalten:** Quelle kann während Sitzung gewechselt oder beim Start vorgegeben werden; für Versand ist Standardaudio oder unterstützte Kompression wählbar.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/80 · Handbuchseiten 87, 100, 112, 113, 117, 118 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `dependency_validation` · **Task:** P10 · **Test:** BT-ASR-007.

**Umsetzung:** Quelle während Sitzung nur mit bestätigter neuer Segmentgrenze; Exportcodec wirklich vorhanden, keine Extension-Umetikettierung.

**Wiederverwendung/Module:** Speaches/faster-whisper als Basis; Fähigkeitsvertrag, Profilspeicher und echte getrennte Anpassungsjobs, nicht vorgetäuschtes Lernen. / `src/clinic/asr/ + existing transcription connector`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-EXP-001 – Mehrere Exportwege pro Abschluss

**Sollverhalten:** Pro Nutzer gespeicherte Weiterverarbeitungsoptionen erlauben mehrere Ausgaben desselben korrigierten Diktats.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/81 · Handbuchseiten 149, 150, 151, 158 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `web_design` · **Task:** P11 · **Test:** BT-EXP-001.

**Umsetzung:** Mehrere benannte DeliveryTargets pro Abschluss, unabhängig committed/failed/unknown. UNKNOWN sperrt unkontrollierte Wiederholung, nicht belegte andere Ergebnisse.

**Wiederverwendung/Module:** Speakr-Dokumentservice plus serverseitige Konverter und Browser-Picker/Clipboard; keine COM-Automation. / `src/clinic/delivery/ + browser download/clipboard UI`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-EXP-002 – Zwischenablage formatiert/unformatiert

**Sollverhalten:** Korrigierter Text kann mit oder ohne Formatierung in Zwischenablage kopiert werden.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/82 · Handbuchseiten 149 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `dependency_validation` · **Task:** P11 · **Test:** BT-EXP-002.

**Umsetzung:** Clipboard plain/RichText explizite Benutzeraktion im verwalteten Desktop; kein BYOD-Export. Kopieren ist nicht klinische Finalfreigabe oder KIS-Ack.

**Wiederverwendung/Module:** Speakr-Dokumentservice plus serverseitige Konverter und Browser-Picker/Clipboard; keine COM-Automation. / `src/clinic/delivery/ + browser download/clipboard UI`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-EXP-003 – RTF-Ausgabe

**Sollverhalten:** Korrigierter Text inklusive Formatierung/Grafik kann in RTF geschrieben, im Workflow verwaltet und optional geöffnet werden.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/83 · Handbuchseiten 150, 151, 152 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `dependency_validation` · **Task:** P11 · **Test:** BT-EXP-003.

**Umsetzung:** RTF mit Format-/Bildroundtrip; geprüfter Serializer/Writerpfad statt handgestricktem Escapeformat. Lesbarkeit unabhängig testen.

**Wiederverwendung/Module:** Speakr-Dokumentservice plus serverseitige Konverter und Browser-Picker/Clipboard; keine COM-Automation. / `src/clinic/delivery/ + browser download/clipboard UI`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-EXP-004 – Word-Ausgabe mit Vorlage und Textmarke

**Sollverhalten:** Word-Export kann Vorlagen, Textmarken, ein geöffnetes Dokument oder Dokument im Workflow nutzen; Text oder formatierter Inhalt wählbar.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/84 · Handbuchseiten 150, 153, 154, 155 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `strict_web_conflict` · **Task:** P11 · **Test:** BT-EXP-004.

**Umsetzung:** DOCX serverseitig aus Vorlage/Bookmarks erzeugen und im Workflow speichern. Upload einer bestehenden Datei ist möglich, aber keine Steuerung eines am Client geöffneten Word-Dokuments.

**Wiederverwendung/Module:** Speakr-Dokumentservice plus serverseitige Konverter und Browser-Picker/Clipboard; keine COM-Automation. / `src/clinic/delivery/ + browser download/clipboard UI`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-EXP-005 – OpenOffice-Writer-/ODT-Ausgabe

**Sollverhalten:** Writer-Ausgabe bietet Vorlage, offenes Dokument, Text/Formatierung und optionale weitere Bearbeitung.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/85 · Handbuchseiten 150, 153, 154, 155 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `strict_web_conflict` · **Task:** P11 · **Test:** BT-EXP-005.

**Umsetzung:** ODT serverseitig über isolierten geprüften Konverter, Vorlagen und Formatvergleich. Keine Steuerung eines geöffneten Writer-Prozesses.

**Wiederverwendung/Module:** Speakr-Dokumentservice plus serverseitige Konverter und Browser-Picker/Clipboard; keine COM-Automation. / `src/clinic/delivery/ + browser download/clipboard UI`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-EXP-006 – Dateiname, Ablageort und Kollisionen

**Sollverhalten:** Dateinamen können abgefragt oder aus Platzhaltern generiert werden; Ablage im Workflow oder an benanntem Pfad ist wählbar.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/86 · Handbuchseiten 152, 153, 154, 159 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `dependency_validation` · **Task:** P11 · **Test:** BT-EXP-006.

**Umsetzung:** Vorlagenname, Kollisionsregel, Workflowablage und Serverzielalias; lokale Pfade nur vom Nutzer ausdrücklich über Browser-Picker autorisiert. Downloadstart nicht als auf Datenträger gespeichert quittieren.

**Wiederverwendung/Module:** Speakr-Dokumentservice plus serverseitige Konverter und Browser-Picker/Clipboard; keine COM-Automation. / `src/clinic/delivery/ + browser download/clipboard UI`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-EXP-007 – Allgemeiner Fenstertransfer

**Sollverhalten:** Zielanwendung kann über Fensterklasse/-titel gewählt und in Vordergrund gebracht werden; Übertragung erfolgt als Clipboard-Text, Tastensimulation oder formatiert. Keine KIS-Speziallogik.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/87 · Handbuchseiten 155, 156, 157 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `strict_web_conflict` · **Task:** P11 · **Test:** BT-EXP-007.

**Umsetzung:** Text/HTML-Clipboard oder Datei mit Nutzeraktion; keine Enumeration von OS-Fenstern und keine simulierten Tasten. Native Teilforderung bleibt blocker.

**Wiederverwendung/Module:** Speakr-Dokumentservice plus serverseitige Konverter und Browser-Picker/Clipboard; keine COM-Automation. / `src/clinic/delivery/ + browser download/clipboard UI`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-EXP-008 – Geschriebenes Dokument kopieren

**Sollverhalten:** Bei manuell geschriebenen Diktaten kann das fertige Dokument an einen ausgewählten oder generierten Dateipfad kopiert werden.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/88 · Handbuchseiten 158, 159 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `dependency_validation` · **Task:** P11 · **Test:** BT-EXP-008.

**Umsetzung:** Datei serverseitig an erlaubten Zielalias kopieren oder Browser-Speicherdialog; keine Schreibberechtigung auf beliebige lokale Pfade durch manipuliertes Requestfeld.

**Wiederverwendung/Module:** Speakr-Dokumentservice plus serverseitige Konverter und Browser-Picker/Clipboard; keine COM-Automation. / `src/clinic/delivery/ + browser download/clipboard UI`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-EXP-009 – Originalaudio speichern oder Mailentwurf öffnen

**Sollverhalten:** Berechtigte Nutzer können Aufnahme auf Datenträger speichern oder an das lokale Standardmailprogramm übergeben.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/89 · Handbuchseiten 38, 162 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `strict_web_conflict` · **Task:** P11 · **Test:** BT-EXP-009.

**Umsetzung:** Audio-Download separat berechtigen; optional .eml-Erzeugung mit Anhang als ausdrücklich anderer Webweg. mailto garantiert keine Audioanlage, kein behaupteter erfolgreicher Mailversand.

**Wiederverwendung/Module:** Speakr-Dokumentservice plus serverseitige Konverter und Browser-Picker/Clipboard; keine COM-Automation. / `src/clinic/delivery/ + browser download/clipboard UI`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-EXP-010 – Word-Add-in: Dokument und Audio übergeben

**Sollverhalten:** Word-Integration kann RTF+Audio, RTF+Vorlage+Audio, Text+Audio oder nur Word-Dokument senden; ohne aktive Diktatsitzung nur Dokument.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/90 · Handbuchseiten 88, 180, 181 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `strict_web_conflict` · **Task:** P11 · **Test:** BT-EXP-010.

**Umsetzung:** Web-Dokumentupload plus gebundene vorhandene Audiofassung; dokument-only ist eigenständig. Kein natives Add-in, keine zweite Clientanwendung. Ursprünglicher Add-in-Ablauf bleibt blockierter Teilumfang.

**Wiederverwendung/Module:** Speakr-Dokumentservice plus serverseitige Konverter und Browser-Picker/Clipboard; keine COM-Automation. / `src/clinic/delivery/ + browser download/clipboard UI`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-OPS-001 – Fusssteuerung und Tastenzuordnung

**Sollverhalten:** Unterstützte Fussschalter steuern Wiedergabe/Spulen/Aufnahme; Haltemodus und Zuordnung sind konfigurierbar, Gerätestatus wird angezeigt.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/91 · Handbuchseiten 103, 104, 105, 167, 168, 200 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `dependency_validation` · **Task:** P12 · **Test:** BT-OPS-001.

**Umsetzung:** Pedal als fokussierte Tastatureingabe oder gerätespezifisch per WebHID mit Nutzerberechtigung; echte Geräte-/Browsermatrix. Keine generelle HID- oder Hintergrundgarantie.

**Wiederverwendung/Module:** Speakr-Betrieb/Audit und direkte Browser-Eingaben; Agent ausschliesslich Import. / `src/clinic/operations/ + browser input adapters + import-only agent`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-OPS-002 – Mikrofon-/Diktiergerätsteuerung und Rückmeldung

**Sollverhalten:** Handmikrofon-/DPM-Tasten und Schiebeschalter steuern Aufnahme, Standby, Wiedergabe, Korrektur und Navigation; unterstützte LEDs zeigen Zustand.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/92 · Handbuchseiten 105, 106, 107, 108, 129, 200 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `dependency_validation` · **Task:** P12 · **Test:** BT-OPS-002.

**Umsetzung:** Audiomikrofon per Browser; Steuerknöpfe/LEDs nur über getesteten direkten WebHID-Vertrag. Kein Hersteller-SDK im Import-Agent für Diktatbedienung.

**Wiederverwendung/Module:** Speakr-Betrieb/Audit und direkte Browser-Eingaben; Agent ausschliesslich Import. / `src/clinic/operations/ + browser input adapters + import-only agent`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-OPS-003 – Hotkeys frei setzen und zurücksetzen

**Sollverhalten:** Kommandos können über einstellbare lokale/globale Hotkeys ausgelöst und auf Standard zurückgesetzt werden.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/93 · Handbuchseiten 109, 110, 111, 132, 133, 142, 147, 166 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `strict_web_conflict` · **Task:** P12 · **Test:** BT-OPS-003.

**Umsetzung:** Tastenkürzel im Webkontext, Konflikte/Browserreservierungen anzeigen. Systemweite Hotkeys bei fremdem Vordergrundfenster sind kein zugesagtes Webfeature.

**Wiederverwendung/Module:** Speakr-Betrieb/Audit und direkte Browser-Eingaben; Agent ausschliesslich Import. / `src/clinic/operations/ + browser input adapters + import-only agent`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-OPS-004 – Autostart, Tray und programmatischer Start

**Sollverhalten:** Client/Workflow/Pass besitzen Hintergrund-/Traybetrieb und Startoptionen für Benutzer/Pool; Schliessen einer Liste kann minimieren, Beenden ist separat.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/94 · Handbuchseiten 117, 118, 119, 120, 121, 164, 165, 168, 170, 171, 172, 179 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `strict_web_conflict` · **Task:** P12 · **Test:** BT-OPS-004.

**Umsetzung:** Installierbare reine PWA optional; kein Electron/Tauri/WPF/WebView-Wrapper. Jobs laufen serverseitig nach Tab-Schliessen weiter; nativer Tray-Autostart nicht als PWA-Erfolg ausgeben.

**Wiederverwendung/Module:** Speakr-Betrieb/Audit und direkte Browser-Eingaben; Agent ausschliesslich Import. / `src/clinic/operations/ + browser input adapters + import-only agent`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-OPS-005 – Statistikerfassung und Aufbewahrung

**Sollverhalten:** Anwendungsnutzung und Diktatverlauf werden separat aktivierbar und mit zeitlicher Begrenzung erfasst.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/95 · Handbuchseiten 72, 73, 184 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `web_design` · **Task:** P12 · **Test:** BT-OPS-005.

**Umsetzung:** Optionale Nutzungs-/Verlaufsstatistik getrennt vom unverzichtbaren Sicherheitsaudit. Statistik aus => nicht erhoben, nicht Nullnutzung.

**Wiederverwendung/Module:** Speakr-Betrieb/Audit und direkte Browser-Eingaben; Agent ausschliesslich Import. / `src/clinic/operations/ + browser input adapters + import-only agent`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-OPS-006 – Diktat-, Frist- und Bearbeitungsstatistiken

**Sollverhalten:** Berichte umfassen Mengen/Dauer nach Autor/Status, verpasste Fristen, Löschungen, Bearbeitungs-/Abnahme-/Fertigstellungszeiten und Anwendungsnutzung.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/96 · Handbuchseiten 184, 185, 186, 187 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `web_design` · **Task:** P12 · **Test:** BT-OPS-006.

**Umsetzung:** Rollen-/Zeitraum-/Autorfilter, unterschiedliche Zeitskalen, vollständige Provenienz bei Aggregation, keine unberechtigte Personenleistungsansicht.

**Wiederverwendung/Module:** Speakr-Betrieb/Audit und direkte Browser-Eingaben; Agent ausschliesslich Import. / `src/clinic/operations/ + browser input adapters + import-only agent`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-OPS-007 – Statistik als CSV und ohne UI

**Sollverhalten:** Statistiken können per UI oder CLI nach Zielpfad, Zeitraum und Berichtsauswahl exportiert werden; ohne UI ist vorgesehen.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/97 · Handbuchseiten 187, 188 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `web_design` · **Task:** P12 · **Test:** BT-OPS-007.

**Umsetzung:** Gemeinsamer Reportservice für UI und autorisierte CLI, gleiche Parameternormalisierung; Fehlerexitcode statt leerem Erfolgs-CSV. Kein pauschaler Direkt-DB-Lesezugriff.

**Wiederverwendung/Module:** Speakr-Betrieb/Audit und direkte Browser-Eingaben; Agent ausschliesslich Import. / `src/clinic/operations/ + browser input adapters + import-only agent`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-OPS-008 – Dienstbetrieb, Jobs und beobachtbare Fehler

**Sollverhalten:** Zentrale Verarbeitung läuft unabhängig von sichtbarer Clientoberfläche; Aufträge, fehlende Ablage, fehlender Autor/Pool und ausgefallene Engine müssen nachvollziehbar behandelt werden.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/98 · Handbuchseiten 192, 193, 194, 196, 197, 198, 199 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `web_design` · **Task:** P12 · **Test:** BT-OPS-008.

**Umsetzung:** Speakr-Worker unabhängig von offener UI; durable Jobs, verständliche Fehler und kontextbezogener Retry. Kein proprietäres Jobdateiprotokoll nachbauen.

**Wiederverwendung/Module:** Speakr-Betrieb/Audit und direkte Browser-Eingaben; Agent ausschliesslich Import. / `src/clinic/operations/ + browser input adapters + import-only agent`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-OPS-009 – Original und Arbeitskopie getrennt aufbewahren

**Sollverhalten:** Unveränderte Eingangsdatei wird getrennt von Wiedergabe-/Erkennungsarbeitskopie gehalten; Arbeitskopie kann komprimiert und geschützt sein.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/99 · Handbuchseiten 45, 46, 194, 195 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `web_design` · **Task:** P12 · **Test:** BT-OPS-009.

**Umsetzung:** Original unverändert, Edit-/Playback-/ASRderivate getrennt ACL/Keys. Kein Export der Herstellerdatenbank oder SecureDS-Bypass.

**Wiederverwendung/Module:** Speakr-Betrieb/Audit und direkte Browser-Eingaben; Agent ausschliesslich Import. / `src/clinic/operations/ + browser input adapters + import-only agent`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.



## PAR-OPS-010 – Gerätesteuerung in RDP-/ICA-Sitzungen

**Sollverhalten:** Zentrale Workflow-Pass-Vorgaben unterscheiden lokale sowie RDP-/ICA-Gerätewege. Nachbau muss den vereinbarten Remote-Gerätepfad eigenständig nachweisen; lokaler Import belegt keine Remote-Unterstützung.

**Quelle:** sources/dns-functional/specs/feature_parity.json#/features/100 · Handbuchseiten 54, 55 · Evidenz manual_specification_not_runtime_observed.

**Webklasse:** `dependency_validation` · **Task:** P12 · **Test:** BT-OPS-010.

**Umsetzung:** Import lokal am physischen PC, Status/Zuordnung in autorisiertem Webclient auch über RDP/ICA. Mikrofon-/HID-Umleitung als direkte Browser/Host-Fähigkeit testen; Agent wird kein Remote-Control-Bridge.

**Wiederverwendung/Module:** Speakr-Betrieb/Audit und direkte Browser-Eingaben; Agent ausschliesslich Import. / `src/clinic/operations/ + browser input adapters + import-only agent`.

**Status:** not_started / not_run; keine automatische Abweichungsfreigabe.


---

# Gesamter Anforderungskatalog
Alle folgenden Einträge sind Sollvorgaben, keine Implementierungsbehauptung.

## R-001 – Ist-Funktionsumfang

101 unverändert übernommene PAR-Funktionsverträge zusätzlich zu den bisherigen Anforderungen vollständig als MUST nachbilden; keine stille Streichung oder automatische N/A-Freigabe.

**Abnahme:** 101 PAR→BT→Implementierungs-/Referenzevidenz-Verknüpfungen vorhanden; vollständige Ablösung erst mit bestandenem Paritätsgate.

**Tests:** T-001 · **Status:** specified_not_validated

## R-002 – Gerätekompatibilität

LFH/DPM9600 und 9620 werden pro eingesetzter Firmware, Dock und Konfiguration geprüft.

**Abnahme:** Je produktiv erlaubter Kombination liegt ein Hardwareprotokoll vor; gesperrter Massenspeicher bleibt sichtbarer Blocker.

**Tests:** T-002 · **Status:** specified_not_validated

## R-003 – Abschluss getrennt von Stabilität

Technische Stabilität und fachlicher Abschluss getrennt. Autosend besitzt expliziten EOL-Filter, default an; bewusst autorisiertes Abschalten gemäss PAR-IMP-004, nie EOL aus Dateizeit ableiten.

**Abnahme:** EOL an/off/Autosend off und unbekannter Autor getestet; technische Stabilität nie als Abschlussnachweis ausgegeben.

**Tests:** T-003, T-063 · **Status:** specified_not_validated

## R-004 – Quellen- und Versionsidentität

Retries sind idempotent; nachträgliche Aufnahmeänderungen überschreiben keine vorhandene Version.

**Abnahme:** Wiederholtes Docking liefert denselben Auftrag; geänderte Quelle erzeugt einen verknüpften Revisionsfall.

**Tests:** T-004, T-067 · **Status:** specified_not_validated

## R-005 – Autorbezug

Gerätebindung, Header-Autor und geteilte Nutzung werden getrennt bewertet.

**Abnahme:** Widersprüchliche oder fehlende Zuordnung bleibt gesperrt; Windows-Login und Stimme sind kein stiller Ersatz.

**Tests:** T-005 · **Status:** specified_not_validated

## R-006 – Original und Metadaten

Originalbytes, Hash, Quellmetadaten und Importversion bleiben nachvollziehbar erhalten.

**Abnahme:** Instruktions-/Hauptaudio, Priorität, EOL und Verschlüsselung werden getrennt nachgewiesen oder als nicht unterstützt markiert.

**Tests:** T-006 · **Status:** specified_not_validated

## R-007 – Quittierung und Quelllöschung

Serverquittung erfordert dauerhaftes Objekt und committed Metadaten. Konfigurierbare Gerätequelllöschung implementieren, im Pilot standardmässig aus; getrennte Freigabe und Quellversionscheck vor Löschung.

**Abnahme:** PAR-IMP-005 mit on/off/Netzabbruch/Quellwechsel; unbestätigte oder veränderte Quelle bleibt erhalten.

**Tests:** T-007, T-054 · **Status:** specified_not_validated

## R-008 – Agentensicherheit

Signierter Agent mit .NET 10 LTS, minimalen Rechten, verschlüsseltem begrenztem Spool und widerrufbarer Maschinenidentität.

**Abnahme:** Installation/Update über genehmigten Verteilweg; Standardnutzerbetrieb; Widerruf und Zertifikatswechsel getestet.

**Tests:** T-008 · **Status:** specified_not_validated

## R-009 – Lokale Anmeldung zuerst, SSO vorbereitet

Erster Betriebsmodus verwendet lokale Speakr-Konten ohne Entra/Keycloak/LDAP/SMTP. Entra-OIDC wird vollständig vorbereitet, deaktiviert ausgeliefert und isoliert getestet; spätere hybride Anmeldung nutzt dieselbe interne user.id.

**Abnahme:** Lokaler Start und Fachablauf ohne IdP; keine Provider-Netzaufrufe bei deaktiviertem Entra; geschützte Sessions und spätere explizite Verknüpfung nachgewiesen.

**Tests:** T-009, T-053, T-071, T-082 · **Status:** specified_not_validated

## R-010 – LDAP-Fähigkeit

LDAP/AD kann über eine unterstützte IdP-Föderation angebunden werden; klinische App speichert keine AD-Passwörter.

**Abnahme:** Geforderte Betriebsvariante ist getestet; optionale zweite Variante wird nicht ohne Test als unterstützt bezeichnet.

**Tests:** T-010, T-070 · **Status:** specified_not_validated

## R-011 – Objektberechtigungen

Default Autor nur eigene Aufträge. Explizite Autor-Sichtbereiche own/author_pool/all, Staff-Zugriff nach Autor/Autorenpool/all mit deny-Ausnahmen und Aktionsrechten; klinische Fallrestriktionen zusätzlich.

**Abnahme:** Listen/Suche/Direktzugriff/Medien/Export nutzen dieselbe zentrale Policy; weder Staff-Pool noch Autorenpool allein verleihen ungeprüfte Rechte.

**Tests:** T-011, T-052, T-066 · **Status:** specified_not_validated

## R-012 – Routing

Autorenpools und Schreibkraftpools getrennt; versionierte Routingregeln zu all_eligible/person/staff_pool, erlaubte Overrides und Selbstkorrektur ohne Schreibkräfte.

**Abnahme:** PAR-ADM-002..007, Mehrfachmitgliedschaften/Ausnahmen/Defaults und historische Routingstände geprüft.

**Tests:** T-012, T-066 · **Status:** specified_not_validated

## R-013 – Patient/Fall

Aufnahme, Autor, Schreibpool, Patient und Fall sind eigenständige Zuordnungen mit Herkunftsnachweis.

**Abnahme:** Unbekannter Fall führt zu Klärung, nicht zu KI-Zuordnung; Export ohne verifizierte Bindung ist blockiert.

**Tests:** T-013 · **Status:** specified_not_validated

## R-014 – Lokale ASR

Audio-/Textinhalte und ASR-Verarbeitung verbleiben in freigegebener Klinik-Infrastruktur.

**Abnahme:** Worker läuft mit bereits bereitgestellten Modellen ohne Internet-Egress; keine Patientendaten in externen Logs.

**Tests:** T-014 · **Status:** specified_not_validated

## R-015 – ASR-Nachweis

Modell, Decoder, Parameter, Sprache und Derivatversion werden pro Lauf versioniert.

**Abnahme:** Qualität und Korrekturzeit werden am freigegebenen Testsatz gemessen; Reprozessierung überschreibt keinen korrigierten Text.

**Tests:** T-015 · **Status:** specified_not_validated

## R-016 – Manueller Arbeitsweg

Bei ASR-Ausfall bleiben Originalaudio und manuelle Abschrift verfügbar, sofern das Audio selbst validiert ist.

**Abnahme:** GPU-/Worker-Ausfall blockiert nicht die manuelle Bearbeitung; defektes Audio bleibt Fehlerfall.

**Tests:** T-016 · **Status:** specified_not_validated

## R-017 – Editor und Konflikte

Editor bietet Audio, Zeitposition, Geschwindigkeit, Autosave, Versionskontrolle und zeitlich begrenzte Bearbeitungsübernahme.

**Abnahme:** Zwei Schreibkräfte können sich nicht still überschreiben; Lease-Verlust verhindert veraltete Schreibzugriffe.

**Tests:** T-017, T-062 · **Status:** specified_not_validated

## R-018 – Arbeitsplatzergonomie im Browser

Originale Pedal-/Mikrofon-/Hotkey-Forderungen erhalten. Webbedienung über Browser-Tastatur/WebHID nach Nachweis; fehlende globale/nativ erforderliche Funktion bleibt offener Paritätskonflikt.

**Abnahme:** V4-Webgrenzen und zugeordnete Tests nachgewiesen; offene Browser-/Hersteller-/Paritätskonflikte nicht als bestanden darstellen.

**Tests:** T-018 · **Status:** specified_not_validated

## R-019 – Freigabeverantwortung

Arbeitsstandtransfer, Korrekturbestätigung, optionale Zusatzabnahme und finale KIS-Signatur getrennt. Keine KI-Autofreigabe; Live-/Office-Arbeitsstand darf nicht als final abgenommen bezeichnet werden.

**Abnahme:** PAR-WF-009..012 und PAR-LIVE/EXP im autorisierten Modus, fachliche finale Ausgaben nur nach gültiger Policy.

**Tests:** T-019 · **Status:** specified_not_validated

## R-020 – Freigabebindung

Freigabe bindet Diktat, Audio-/Textversion, Text-Hash, Kontextrevision, Dokumenttyp und freigebende Person.

**Abnahme:** Text-/Kontextänderung invalidiert Freigabe; eine fremde Diktatversion wird auch auf DB-Ebene abgewiesen.

**Tests:** T-020, T-061, T-062 · **Status:** specified_not_validated

## R-021 – Manueller Export

Alle PAR-EXP-Ausgabewege auf verwalteten Arbeitsplätzen implementieren: DOCX/RTF/ODT/Text, Clipboard, Word/Writer, allgemeine Fenster, Audio, Mailentwurf und Word-Add-in; BYOD bleibt eingeschränkt.

**Abnahme:** Je Ziel eigene Quittung und Format-/Interaktionsnachweise; Dokument-only/Mehrfachausgabe/Abbruch/Fehler/UNKNOWN getrennt.

**Tests:** T-021 · **Status:** specified_not_validated

## R-022 – Interner BYOD-Zugang

Private Smartphones erreichen nur freigegebenen HTTPS-Ingress im internen BYOD-Netz.

**Abnahme:** Kein direkter Zugang zu AD, DB, Dateispeicher oder KI; kein öffentliches Publishing und kein implizit hinzugefügtes VPN.

**Tests:** T-022 · **Status:** specified_not_validated

## R-023 – Mobile Aufnahme

Aufnahme wird nur auf validierten iOS-/Android-Browserkombinationen im Vordergrund angeboten.

**Abnahme:** Anruf, Sperren, App-Wechsel, Roaming und Mikrofonverlust sind getestet; Unterbrechungen bleiben sichtbar.

**Tests:** T-023 · **Status:** specified_not_validated

## R-024 – Browser-Aufnahme-/Wiederherstellungsvertrag

Chunks und Containerabschluss geordnet prüfen. BYOD ohne persistente Ablage; freigegebenes Managed-Web-Offlineprofil separat, kein nativer Recorder und kein erfolgreicher Versand ohne Serverquittung.

**Abnahme:** V4-Webgrenzen und zugeordnete Tests nachgewiesen; offene Browser-/Hersteller-/Paritätskonflikte nicht als bestanden darstellen.

**Tests:** T-024, T-064, WB-010 · **Status:** specified_not_validated

## R-025 – BYOD-Datenminimierung

Keine absichtliche persistente Audio-/Textablage in Service Worker, IndexedDB oder localStorage; keine Dokumentdownloads im BYOD-MVP.

**Abnahme:** App-Speicher wird geprüft, Restrisiken des Betriebssystems werden genehmigt; keine Behauptung forensisch garantierter Datenfreiheit.

**Tests:** T-025, T-055, WB-007 · **Status:** specified_not_validated

## R-026 – Edge-WebView-Vertrag

inesKIS-Einbettung, Runtime, SSO, Profilisolation, Audio und erlaubte Hostnachrichten werden mit Hersteller abgenommen.

**Abnahme:** Testclient zeigt die Plattform im vereinbarten Host und besteht Benutzerwechsel-/Navigationstests.

**Tests:** T-026 · **Status:** specified_not_validated

## R-027 – KIS-Kontext

Patient/Fall, Benutzer, Mandant, Zweck, Ablauf und Kontextrevision kommen über einen vertrauenswürdigen Vertrag.

**Abnahme:** Falscher Benutzer/Fall, Replay, falsche Audience und fehlende Rechte sind blockiert; kein Patient im URL-/Accesslog.

**Tests:** T-027, T-069 · **Status:** specified_not_validated

## R-028 – KIS-Ausgabe

Dokumenttyp, Entwurf/final, Inhalt, Autor, Freigabe und Statusabfrage folgen einer freigegebenen Herstellerschnittstelle.

**Abnahme:** Dokument ist im richtigen Fall sichtbar und über externe Dokument-ID sowie Version/Inhalt nachvollziehbar.

**Tests:** T-028 · **Status:** specified_not_validated

## R-029 – Unklarer KIS-Ausgang

Timeout nach Versand ergibt UNKNOWN und Abgleich statt blindem Retry oder automatischem Export über Zweitkanal.

**Abnahme:** Simulierter Timeout nach KIS-Speicherung erzeugt kein zweites Dokument.

**Tests:** T-029, T-065 · **Status:** specified_not_validated

## R-030 – KIS-Kontextwechsel und Nachträge

Kontext an Diktat binden, Patientwechsel nie still umhängen; nur dokumentierte KIS-Übergabe. Allgemeiner Fenstertransfer ist unter Web-only kein erlaubter KIS-Rückfall.

**Abnahme:** V4-Webgrenzen und zugeordnete Tests nachgewiesen; offene Browser-/Hersteller-/Paritätskonflikte nicht als bestanden darstellen.

**Tests:** T-030, T-069 · **Status:** specified_not_validated

## R-031 – Audit

Relevante Zugriffe, Zuordnungen, Freigaben, Exporte und administrative Aktionen sind getrennt von technischen Logs protokolliert.

**Abnahme:** App kann eigene Auditereignisse nicht ändern/löschen; privilegierte Eingriffe und Restrisiko sind dokumentiert.

**Tests:** T-031 · **Status:** specified_not_validated

## R-032 – Betriebsfreigabe

Monitoring, Runbooks, Patch-/Zertifikatsmanagement, Service Owner und Stellvertretung sind vorhanden.

**Abnahme:** Störung wird erkannt und ohne Entwicklerwissen anhand Runbook behandelt; Updates/Rollback getestet.

**Tests:** T-032 · **Status:** specified_not_validated

## R-033 – Backup/Restore

RPO/RTO pro Datenklasse und Ausfallszenario sind fachlich genehmigt und durch Restore belegt.

**Abnahme:** Audio, DB, Freigaben, Audit und Exportledger werden konsistent wiederhergestellt; KIS-Abgleich nach Restore.

**Tests:** T-033 · **Status:** specified_not_validated

## R-034 – Datenschutz und Zweck

Schutzbedarf, Rechtsgrundlage, DSFA-Screening, Aufbewahrung, Löschung und regulatorische Zweckbestimmung sind freigegeben.

**Abnahme:** Keine produktiven Echtdaten vor erforderlicher Freigabe; Fristen und Legal-Hold-Verfahren dokumentiert.

**Tests:** T-034 · **Status:** specified_not_validated

## R-035 – Softwarelieferkette

Abhängigkeiten, Modelle, Lizenzen, Images und Builds sind nachvollziehbar gepinnt und geprüft.

**Abnahme:** Keine latest-Tags oder unkontrollierten Modell-Downloads im Betrieb; Secrets/Patientendaten fehlen in CI.

**Tests:** T-035, T-057 · **Status:** specified_not_validated

## R-036 – Zustandskonsistenz

Jobs, Idempotenz, Objektablage und fachliche Übergänge haben transaktionale Regeln und Abgleichprozesse.

**Abnahme:** Crash an jeder kritischen Persistenzgrenze führt weder zu still verlorenen Aufträgen noch zu unzulässigen Doppelaktionen.

**Tests:** T-036, T-068 · **Status:** specified_not_validated

## R-037 – Leistung und Nutzen

Lastgrenzen und Bearbeitungszeiten werden aus erhobenen Volumen und gemessenen Pilotdaten abgeleitet.

**Abnahme:** Vereinbarte Spitzenlast und SLO bestehen; Korrekturaufwand wird mit dem bisherigen Prozess verglichen.

**Tests:** T-037 · **Status:** specified_not_validated

## R-038 – Migration und Ablösung

Aktive Ablösung mit eindeutig führendem System pro Auftrag, Rollback und Altarchivzugriff planen. Keine automatische historische Datenübernahme aus dem Analysearchiv; neuer Bestand und neue Testdaten.

**Abnahme:** Alle 101 Paritätsfälle plus Projektgates erfüllt, offene Aufträge kontrolliert abgeschlossen/gesondert behandelt; Alt-DB nie automatisch importiert.

**Tests:** T-038 · **Status:** specified_not_validated

## R-039 – Sicherheitsgrenzen

Dateiparser laufen isoliert mit Limits; jegliche Objekt- und Dienstschnittstelle hat minimale Rechte.

**Abnahme:** Manipulierte Dateien, Header-Spoofing, Traversal, Ressourcenerschöpfung und unzulässiger Egress werden abgefangen.

**Tests:** T-039 · **Status:** specified_not_validated

## R-040 – Betriebs-/Kostenentscheidung

Wartung, Support, Herstellerintegration, Geräteprovisionierung und Weiterentwicklung sind budgetiert.

**Abnahme:** Benannte Zuständigkeit und genehmigter Gesamtbetriebsvergleich liegen vor; kostenlose Lizenzen gelten nicht als Kostenfreiheit.

**Tests:** T-040 · **Status:** specified_not_validated

## R-041 – OSS-Fork statt Neuaufbau

Speakr-Basis mit nachgewiesenem Pin und erhaltener Upstreamstruktur verwenden.

**Abnahme:** Checkout SHA entspricht Lock; bestehende UI/ORM/Queue nachweislich weiterverwendet.

**Tests:** T-051 · **Status:** specified_not_validated

## R-042 – Klinikmodus/Legacy-Bypass

Sämtliche Datenrouten im dedizierten Klinikmodus unterliegen klinischer Policy oder sind gesperrt.

**Abnahme:** Routeinventar vollständig; neue unbekannte Route deny; Stock-Exports/Admin/Search können ACL nicht umgehen.

**Tests:** T-052 · **Status:** specified_not_validated

## R-043 – Klinische Identitätszuordnung

E-Mail-Autolinking, JIT-Fachkonten und impliziter Passwortfallback bleiben verboten. Reguläre lokale Anmeldung wird ausdrücklich per Kontopolicy erlaubt; Entra-only und device-only umgehen diese Policy nicht.

**Abnahme:** Gleiche Mail mit anderem iss/sub wird nicht verknüpft; vorprovisionierter Geräteautor wird korrekt aufgelöst.

**Tests:** T-053 · **Status:** specified_not_validated

## R-044 – Webpersistenzprofile

BYOD bleibt ohne absichtliche persistente Audio-/Textablage. Explizites managed_web_offline ist erst nach Policy/Key-/Browser-/Eviction-Nachweis zulässig. Keine Patientendaten durch clientseitigen Profilwechsel freischalten.

**Abnahme:** V4-Webgrenzen und zugeordnete Tests nachgewiesen; offene Browser-/Hersteller-/Paritätskonflikte nicht als bestanden darstellen.

**Tests:** T-054 · **Status:** specified_not_validated

## R-045 – Upstream-Browserpersistenz beschränken

Recorder-, FailedUpload-, Incognito-, Serviceworker- und Sitzungspfad speichern keine Klinik-Audio-/Textdaten absichtlich persistent.

**Abnahme:** Browserstorage-Sentineltest über normale und Fehlerpfade; Logout/Timeout/Refresh/Crash-Reload abgedeckt.

**Tests:** T-055 · **Status:** specified_not_validated

## R-046 – Unabhängige Mockentwicklung

Fehlende klinische Zugänge blockieren nur zugehörige Realabnahme, nicht synthetische Implementierung.

**Abnahme:** Dev-End-to-End ohne Klinikcredentials; produktiver Start mit Mock zwingend abgewiesen.

**Tests:** T-056, T-063 · **Status:** specified_not_validated

## R-047 – Lieferkette und Source-Angebot

Pins, vorhandene Lockmechanik, Lizenzen, Source-Angebot und Upgradetests dokumentieren.

**Abnahme:** Release-/Commitverifikation, Image-/Modellpins, SBOM und source-link passend zur laufenden Forkversion.

**Tests:** T-057 · **Status:** specified_not_validated

## R-048 – Zugriffskanal absichern

BYOD/Managed/Agentrollen stammen aus vertrauenswürdiger Server-/Ingressentscheidung.

**Abnahme:** Gefälschter Kanalheader/User-Agent/Host führt nicht zu Export-/Poolrechten.

**Tests:** T-058 · **Status:** specified_not_validated

## R-049 – Keine zweite generische Infrastruktur

Vorhandene Aufnahme-/Export-/Job-/Storagebausteine verwenden; neue klinische Fachlogik gezielt abgrenzen.

**Abnahme:** Reuse-map pro Slice; kein FastAPI-Neuprojekt, keine zweite Jobqueue, kein unfundierter Infrastrukturcluster.

**Tests:** T-059 · **Status:** specified_not_validated

## R-050 – Policy ist unveränderliche Obergrenze

Upstream SystemSettings dürfen verbotene Sharing-/Cloud-/Persistenz-/Exportfunktionen nicht reaktivieren.

**Abnahme:** API-/Settingsänderung wird abgewiesen oder effektiv gesperrt; Startup-Check erkennt Konflikte.

**Tests:** T-060 · **Status:** specified_not_validated

## R-051 – Lokale Benutzerverwaltung

Vorhandene Speakr-Adminfunktionen für Anlegen, Bearbeiten, Deaktivieren, Rollen und Schreibpools erweitern; keine zweite Benutzerverwaltung.

**Abnahme:** Verwaltung und Fachablauf funktionieren ohne externen IdP/SMTP; Rollen geben keine impliziten Inhaltsrechte.

**Tests:** T-071, T-072, T-092, WB-006 · **Status:** specified_not_validated

## R-052 – Sicherer lokaler Bootstrap

Ersten Identitätsadministrator ausschliesslich über geschützten Operatorweg initialisieren; keine Defaultpasswörter/öffentliche Erstregistrierung.

**Abnahme:** Parallel-/Wiederholungsversuche legen keinen unerlaubten Admin an; letzter lokaler Identitätsadmin geschützt.

**Tests:** T-073, T-074 · **Status:** specified_not_validated

## R-053 – Lokale Aktivierung und Reset

Zweckgebundene, gehashte, ablaufende Einmalcodes; kein SMTP-Zwang, kein Auto-Login und keine Policyänderung durch Reset.

**Abnahme:** Replay/Parallelität/Ablauf/Disabled-/Entra-only-Fälle abgewiesen; keine Geheimnisse in Logs.

**Tests:** T-075, T-076, T-077, T-093 · **Status:** specified_not_validated

## R-054 – Passwortschutz und Schema

Etablierte Bibliotheken für Hash/Verify; ausreichend breite Passwortspalte; Blockliste, keine Kürzung und rate-limited Ressourcen.

**Abnahme:** Hashspeicherung und Legacy-Rehash gegen PostgreSQL getestet; Passphrase-/Last-/Unbekanntkonto-Fälle geprüft.

**Tests:** T-078, T-079, T-080 · **Status:** specified_not_validated

## R-055 – Widerrufbare providerunabhängige Sitzung

Kontostatus und Sicherheitsversion bei jedem geschützten Zugriff, Rotation, CSRF und definierter Ablauf.

**Abnahme:** Deaktivierung/Reset/Linkwechsel beendet alte Sitzungen; Benutzer-/WebViewwechsel isoliert.

**Tests:** T-081, T-094 · **Status:** specified_not_validated

## R-056 – Entra implementiert, initial aus

Bibliotheksgestützter OIDC-Code, Profile und Fixturetests schon im Erstauftrag; keine IdP-Netz-/Secretpflicht im lokalen Modus.

**Abnahme:** Deaktivierte Endpunkte ohne Effekt; falscher Tenant/Issuer/Audience/Nonce abgewiesen; kein Graphpflichtpfad.

**Tests:** T-082, T-083, T-084 · **Status:** specified_not_validated

## R-057 – Explizite Identitätsverknüpfung

Gleiche interne user.id behalten; Entra-Link nur über autorisierten benutzer-/tenantgebundenen Auftrag und geprüfte externe Identität.

**Abnahme:** Keine Emailübernahme, kein Duplicate User, keine Umhängung klinischer Historie; konkurrierendes Linking abgefangen.

**Tests:** T-085, T-086, T-087 · **Status:** specified_not_validated

## R-058 – Per-Konto-Loginpolicy

local_only, entra_only, local_and_entra, device_only unterscheiden; kein SSO-Ausfallbypass oder Reset zu unerlaubtem lokalen Zugang.

**Abnahme:** Direkte Upstream-/Adminpfade befolgen dieselbe Policy; dualer Zugang nur ausdrücklich auditiert.

**Tests:** T-077, T-088, T-089, T-097 · **Status:** specified_not_validated

## R-059 – Lokale Fachrechte auch bei SSO

Anwendung ist initial führend für Rollen/Pools; Loginverfahren und Rechtezuweisung getrennt.

**Abnahme:** Claims/Email verleihen keine Fachrechte; nur eigene Diktate bzw. autorisierter Staffpool.

**Tests:** T-090, T-092 · **Status:** specified_not_validated

## R-060 – Nichtdestruktive Identity-Migration

Additive Daten-/Policyänderungen, stabile vorhandene User-IDs, kein Harddelete klinisch referenzierter Benutzer.

**Abnahme:** Upgrade/Restore/Providerwechsel bewahrt Autoren, Pools, Geräte, Freigaben und Audit.

**Tests:** T-086, T-091 · **Status:** specified_not_validated

## R-061 – Provider-Aktivierung und Lifecycle

G-ENTRA/G-LDAP separat; lokale Abnahme bleibt möglich. Lifecycle-/CA-Grenzen und expliziter Rückfall dokumentiert.

**Abnahme:** Fehlerhafte Hybridkonfiguration/Entra-Ausfall öffnen keinen lokalen Zugang; Entra-only-Rückfall erfordert neue explizite lokale Aktivierung.

**Tests:** T-088, T-095, T-096 · **Status:** specified_not_validated

## R-062 – Konsolidierter Claude-Auftrag

OIDC-only-/Entra-first-Defaults in Dokumenten, Katalogen, Fixtures und Profilen ersetzen; Grok-Ergänzung erhalten.

**Abnahme:** Lokales End-to-End mit Userverwaltung und deaktiviertem vorbereitetem Entra-Code; keine Rückfrage nach Tenantsecrets.

**Tests:** T-071, T-098 · **Status:** specified_not_validated

## PAR-ADM-001 – Autoren und Schreibkräfte verwalten

Autoren und Schreibkräfte getrennt anlegen, bearbeiten und entfernen; Änderungen mit Übernehmen/OK verbindlich speichern.

**Abnahme:** Unveränderter BT-ADM-001 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-ADM-001 · **Status:** specified_not_validated

## PAR-ADM-002 – Autorenpools mit Mehrfachmitgliedschaft

Autoren können einem oder mehreren Autorenpools angehören; gemeinsame Ressourcen stehen Mitgliedern des gewählten Pools zur Verfügung.

**Abnahme:** Unveränderter BT-ADM-002 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-ADM-002 · **Status:** specified_not_validated

## PAR-ADM-003 – Schreibkraftpools eigenständig verwalten

Schreibkraftpools gruppieren berechtigte Schreibkräfte; sie bleiben von Autorenpools getrennt.

**Abnahme:** Unveränderter BT-ADM-003 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-ADM-003 · **Status:** specified_not_validated

## PAR-ADM-004 – Zugriff nach Autor oder Autorenpool mit Ausnahmen

Schreibkraftzugriff kann auf einzelne Autoren, Autorenpools mit Ausnahmen oder alle Autoren begrenzt werden.

**Abnahme:** Unveränderter BT-ADM-004 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-ADM-004 · **Status:** specified_not_validated

## PAR-ADM-005 – Aktionen unabhängig voneinander berechtigen

Rechte steuern Löschen, Archivieren, Umbenennen, Priorität ändern, Audioexport, Umverteilen und Wiederaufnahme fertiggestellter Diktate getrennt.

**Abnahme:** Unveränderter BT-ADM-005 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-ADM-005 · **Status:** specified_not_validated

## PAR-ADM-006 – Autoren-Sichtbereiche und Schreibkraftauswahl

Autoren können nur eigene, die Diktate ihres Autorenpools oder alle Diktate sehen; die Empfängerauswahl kann auf Schreibpools begrenzt werden.

**Abnahme:** Unveränderter BT-ADM-006 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-ADM-006 · **Status:** specified_not_validated

## PAR-ADM-007 – Voreingestellte Empfänger und Selbstkorrektur

Neue Diktate gehen an alle berechtigten Schreibkräfte oder die konfigurierte Person/Gruppe; ein Betrieb ohne Schreibkräfte erlaubt Selbstkorrektur.

**Abnahme:** Unveränderter BT-ADM-007 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-ADM-007 · **Status:** specified_not_validated

## PAR-ADM-008 – Administrative und Statistik-Accounts

Administration und Statistikzugriff besitzen eigene Berechtigungen, unabhängig von der Autoren-/Schreibkraftfunktion.

**Abnahme:** Unveränderter BT-ADM-008 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-ADM-008 · **Status:** specified_not_validated

## PAR-ADM-009 – Windows-/AD-Auswahl und bestätigter AD-Abgleich

Benutzer können aus lokalem Windows oder AD gewählt werden; gruppenbasierter Abgleich zeigt neue/entfernte Benutzer und Mitgliedschaften zur Bestätigung. E-Maildaten können aus AD stammen.

**Abnahme:** Unveränderter BT-ADM-009 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-ADM-009 · **Status:** specified_not_validated

## PAR-ADM-010 – Windows-SSO mit eindeutigem Kontext

Aktiviertes SSO verwendet den angemeldeten Windows-Nutzer und verhindert die Auswahl fremder Identitäten; Aktivierung für Client und Workflow getrennt.

**Abnahme:** Unveränderter BT-ADM-010 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-ADM-010 · **Status:** specified_not_validated

## PAR-ADM-011 – Benutzerstart, letzte Auswahl und Wechsel

Manueller Start merkt sich den letzten Nutzer; Verknüpfungen können Benutzer/Pool wählen; Wechsel beendet den alten Kontext mit behandelbaren ungesicherten Änderungen.

**Abnahme:** Unveränderter BT-ADM-011 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-ADM-011 · **Status:** specified_not_validated

## PAR-CFG-001 – Zentrale Vorgaben und lokale Änderungen

Zentral gesetzte Profil-/Workflowvorgaben gelten beim Neustart erneut; nicht zentral fixierte Werte dürfen lokal eingestellt werden.

**Abnahme:** Unveränderter BT-CFG-001 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-CFG-001 · **Status:** specified_not_validated

## PAR-CFG-002 – Vorlagen zentral speichern und zuordnen

Benutzer- und Poolvorlagen besitzen Anzeigename, Anmerkung und Einfügemarke; eine Serverkopie bleibt nutzbar, wenn die Ursprungsdatei fehlt.

**Abnahme:** Unveränderter BT-CFG-002 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-CFG-002 · **Status:** specified_not_validated

## PAR-CFG-003 – Versandkategorien und Pflichtangaben

Versandkategorien lassen sich als Sets definieren und beim Client bzw. Recorder verpflichtend abfragen; sie wirken auf Diktatanmerkungen.

**Abnahme:** Unveränderter BT-CFG-003 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-CFG-003 · **Status:** specified_not_validated

## PAR-CFG-004 – RegEx-basierte Metadatenzuweisung mit Vorschau

RegEx-Regeln können Titel, Anmerkung, Dringlichkeit und Frist aus erkanntem Text ableiten; maximale Textmenge und Testvorschau sind konfigurierbar. Integrations-ID-Ziele sind ausgeschlossen.

**Abnahme:** Unveränderter BT-CFG-004 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-CFG-004 · **Status:** specified_not_validated

## PAR-CFG-005 – Konfigurierbare Metadatenspalten

Diktatliste kann zusätzliche Spalten aus XML-Metadaten sowie Zeit-/Namensformat verwenden.

**Abnahme:** Unveränderter BT-CFG-005 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-CFG-005 · **Status:** specified_not_validated

## PAR-REC-001 – Recorder mit Titel-, Aufnahme- und Versandphase

Autor kann ein Diktat benennen, Audio aufnehmen und vor Versand Metadaten ändern; Titel dient der Identifikation in der Diktatliste.

**Abnahme:** Unveränderter BT-REC-001 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-REC-001 · **Status:** specified_not_validated

## PAR-REC-002 – Aufnahme- und Wiedergabesteuerung

Aufnahme, Stop, Wiedergabe, Anfang/Ende und Vor-/Rückspulen sind per UI und dokumentierten Hotkeys bedienbar.

**Abnahme:** Unveränderter BT-REC-002 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-REC-002 · **Status:** specified_not_validated

## PAR-REC-003 – Audio einfügen, überschreiben, kürzen und rückgängig

Recorder unterscheidet Einfügen/Überschreiben, kann ab Cursor abschneiden und letzte Aufnahmeoperationen mit dokumentiertem 16-stufigem Undo zurücknehmen.

**Abnahme:** Unveränderter BT-REC-003 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-REC-003 · **Status:** specified_not_validated

## PAR-REC-004 – Stimmgesteuerte Aufnahme

Bei aktivierter Stimmsteuerung wird nur während erkannter Sprache aufgezeichnet; aktivierter Modus ist sichtbar.

**Abnahme:** Unveränderter BT-REC-004 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-REC-004 · **Status:** specified_not_validated

## PAR-REC-005 – Audio-Geräte und Aufnahme-/Wiedergabepegel

Aufnahme- und Wiedergabegerät sowie unterstützte Pegel lassen sich wählen; Steuergerät und Audiogerät sind unterscheidbar.

**Abnahme:** Unveränderter BT-REC-005 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-REC-005 · **Status:** specified_not_validated

## PAR-REC-006 – Versandparameter und optionaler Diktatschutz

Versand fragt Titel, Anmerkung, Priorität/Frist, ASR, Empfänger, Abnahme und optionalen Diktatpasswortschutz ab.

**Abnahme:** Unveränderter BT-REC-006 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-REC-006 · **Status:** specified_not_validated

## PAR-REC-007 – Autarker Recorder und lokales Speichern

Recorder kann Audio laden, lokal speichern und im autarken Betrieb an einem konfigurierten Ziel ablegen; Serverausfall darf nicht als erfolgreicher Workflowversand erscheinen.

**Abnahme:** Unveränderter BT-REC-007 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-REC-007 · **Status:** specified_not_validated

## PAR-REC-008 – Audio-Text-Sitzungen und Textdateien speichern/laden

Eigene Sitzungen enthalten Audio und bearbeitbaren Text; RTF und TXT können geöffnet/gespeichert werden. Neue Sitzungen müssen verlustfrei wieder geöffnet werden.

**Abnahme:** Unveränderter BT-REC-008 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-REC-008, WB-016 · **Status:** specified_not_validated

## PAR-LIVE-001 – Live-Erkennung im eigenen Diktierfenster

Gesprochener Text erscheint während der Sitzung im eigenen Diktierfenster; Autor kann direkt korrigieren und Audio plus Text zur delegierten Korrektur senden.

**Abnahme:** Unveränderter BT-LIVE-001 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-LIVE-001, WB-015 · **Status:** specified_not_validated

## PAR-LIVE-002 – Diktieren in allgemeine Windows-Textfenster

Erkannter Text kann an der ausgewählten Position einer unterstützten allgemeinen Desktopanwendung erscheinen; keine KIS-spezifische Datenverbindung.

**Abnahme:** Unveränderter BT-LIVE-002 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-LIVE-002 · **Status:** specified_not_validated

## PAR-LIVE-003 – Sofortige Entwurfssicherung pro Phrase

SonicPad sichert jede gesprochene Phrase im Entwurf; nach Neustart ist die Entwurfsliste verfügbar, aufbewahrtes Audio bleibt trotz Textkorrektur erhalten.

**Abnahme:** Unveränderter BT-LIVE-003 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-LIVE-003 · **Status:** specified_not_validated

## PAR-LIVE-004 – Mehrere Entwürfe und bewusstes Verwerfen

Mehrere Diktierfenster/Entwürfe können parallel bestehen; Laden, Wechseln, Speichern und Löschen sind getrennt. Beim Schliessen ungesicherter Änderungen wird gefragt.

**Abnahme:** Unveränderter BT-LIVE-004 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-LIVE-004 · **Status:** specified_not_validated

## PAR-LIVE-005 – Rich-Text-Bearbeitung und Versandformat

Diktierfenster unterstützt Schrift, Ausrichtung, Zeilenabstand und Grafiken; Versand kann Formatierung behalten oder bewusst auf reinen Text reduzieren. Leeren/Schliessen erfolgt nur nach erfolgreicher Übergabe.

**Abnahme:** Unveränderter BT-LIVE-005 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-LIVE-005 · **Status:** specified_not_validated

## PAR-LIVE-006 – Diktierfokus an Fenster binden

SpeechLock bindet Diktat und Befehle an ein unterstütztes Fenster trotz Arbeit in anderem Fenster; Zustand und Umschaltung sind sichtbar.

**Abnahme:** Unveränderter BT-LIVE-006 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-LIVE-006 · **Status:** specified_not_validated

## PAR-LIVE-007 – Automatische Fokusbindung und immer oben

Bestimmte Fenster können beim Fokus automatisch gebunden und optional oben gehalten werden; globale Deaktivierung schaltet diese Funktion aus.

**Abnahme:** Unveränderter BT-LIVE-007 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-LIVE-007 · **Status:** specified_not_validated

## PAR-LIVE-008 – Text und Aussprache vorlesen

Markierter Text oder Text ab Cursor kann vorgelesen werden; Stimme und Geschwindigkeit sind konfigurierbar, fehlende Stimmen werden gemeldet.

**Abnahme:** Unveränderter BT-LIVE-008 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-LIVE-008 · **Status:** specified_not_validated

## PAR-IMP-001 – Geräte erkennen und Diktate auflisten

Workflow Pass erkennt angeschlossene Geräte/Wechseldatenträger und zeigt deren Diktate, Geräteanzahl und Status; beobachtete Gerätetypen sind wählbar.

**Abnahme:** Unveränderter BT-IMP-001 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-IMP-001 · **Status:** specified_not_validated

## PAR-IMP-002 – Autor aus Geräte-ID oder Aufnahme-Autor auflösen

Exklusive Geräte können über Geräte-ID, geteilte Geräte über das Autor-Feld im DSS/DSSPro-Diktat zugeordnet werden; unbekannte Zuordnung erfordert bewusste Auswahl.

**Abnahme:** Unveränderter BT-IMP-002 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-IMP-002 · **Status:** specified_not_validated

## PAR-IMP-003 – Manueller und gemeinsamer Geräteversand

Mehrere Gerätediktate können selektiert, mit Metadaten versehen und gemeinsam versandt werden; Original-/Automatisch-Werte bleiben unterscheidbar.

**Abnahme:** Unveränderter BT-IMP-003 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-IMP-003 · **Status:** specified_not_validated

## PAR-IMP-004 – Automatischer Versand und EOL-Grenze

Automatischer Versand verarbeitet nur zugeordnete Autoren; optional nur als beendet/EOL markierte Diktate.

**Abnahme:** Unveränderter BT-IMP-004 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-IMP-004 · **Status:** specified_not_validated

## PAR-IMP-005 – Quelllöschung nach bestätigter Übernahme

Nach erfolgreichem Versand kann ein Diktat automatisch vom Gerät gelöscht werden; manuelles Löschen fragt nach. Neubau löscht erst nach dauerhaft bestätigter Übernahme.

**Abnahme:** Unveränderter BT-IMP-005 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-IMP-005 · **Status:** specified_not_validated

## PAR-IMP-006 – Backupkopie und Aufbewahrungsfrist

Vor Versand kann Originalaudio in einen konfigurierten Backupordner kopiert werden; Dateiname aus Platzhaltern und optionale Löschfrist sind einstellbar.

**Abnahme:** Unveränderter BT-IMP-006 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-IMP-006 · **Status:** specified_not_validated

## PAR-IMP-007 – Datei- und Mehrfachimport von Audio und Dokumenten

Autoren und Schreibkräfte können Audio WAV/DSS/DS2/AMR sowie dokumentierte Office-/RTF-/TXT-/PDF-/Bilddateien importieren; Mehrfachimport bewahrt einzelne Dateinamen.

**Abnahme:** Unveränderter BT-IMP-007 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-IMP-007 · **Status:** specified_not_validated

## PAR-IMP-008 – Dropfolder-Autoversand

Mehrere Eingangspfade mit Autor, Pool, Endungsmuster, Priorität, Sprache und ASR-Wahl werden überwacht; dokumentierter Altumfang umfasst keine rekursiven Unterordner.

**Abnahme:** Unveränderter BT-IMP-008 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-IMP-008 · **Status:** specified_not_validated

## PAR-IMP-009 – Geräteverwaltung, Autorenlisten und Schutzoptionen

Geräte können inventarisiert und mit Autoren-/Listenwerten, Aufnahmeverschlüsselung, PIN/Sperrregeln und Energiesparwerten konfiguriert werden, soweit Gerät unterstützt.

**Abnahme:** Unveränderter BT-IMP-009 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-IMP-009, WB-012 · **Status:** specified_not_validated

## PAR-IMP-010 – Firmwarebestand und Versionsvergleich

Geräteverwaltung hinterlegt Firmware pro Typ; Workflow Pass vergleicht bei Anschluss Versionen und kann ein älteres Gerät aktualisieren.

**Abnahme:** Unveränderter BT-IMP-010 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-IMP-010 · **Status:** specified_not_validated

## PAR-WF-001 – Arbeitsliste für Autoren und Schreibkräfte

Workflowzentrale zeigt berechtigte Diktate mit Titel, Status, Autor, Priorität/Frist, Bearbeiter/Zeit, Anmerkung, Erstellzeit, Dauer, ID und Empfänger.

**Abnahme:** Unveränderter BT-WF-001 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-WF-001 · **Status:** specified_not_validated

## PAR-WF-002 – Automatische und manuelle Aktualisierung

Arbeitsliste aktualisiert sich automatisch; F5 erzwingt eine Aktualisierung.

**Abnahme:** Unveränderter BT-WF-002 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-WF-002 · **Status:** specified_not_validated

## PAR-WF-003 – Gespeicherte Filter und Metadatensuche

Bis zu 20 benannte Filter kombinieren Autor/Pool und Status; Suche findet Begriffe in Diktatmetadaten und markiert Treffer.

**Abnahme:** Unveränderter BT-WF-003 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-WF-003 · **Status:** specified_not_validated

## PAR-WF-004 – Sortieren, Gruppieren und Layout merken

Spalten lassen sich verschieben, sortieren und nach mehreren Merkmalen gruppieren; Fenster-/Listenlayout wird pro Nutzer erinnert.

**Abnahme:** Unveränderter BT-WF-004 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-WF-004 · **Status:** specified_not_validated

## PAR-WF-005 – Diktatmetadaten bearbeiten und neu zuweisen

Berechtigte Nutzer können Titel, Anmerkung, Priorität, Frist und zuständige Schreibkraft ändern.

**Abnahme:** Unveränderter BT-WF-005 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-WF-005 · **Status:** specified_not_validated

## PAR-WF-006 – Verlauf eines Diktats anzeigen

Zu einem Diktat können Log-/Verlaufsinformationen aufgerufen werden.

**Abnahme:** Unveränderter BT-WF-006 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-WF-006 · **Status:** specified_not_validated

## PAR-WF-007 – Exklusive Bearbeitung und Freigabe

Öffnen des Vorgangs setzt In Bearbeitung und verhindert paralleles Bearbeiten; Schliessen gibt frei.

**Abnahme:** Unveränderter BT-WF-007 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-WF-007 · **Status:** specified_not_validated

## PAR-WF-008 – Vorbereitung, Abschrift und Korrektur unterscheiden

Neu angelegt/In Vorbereitung unterscheiden sich von Zu schreiben und Zu korrigieren; erfolgreiche Spracherkennung führt zur Korrektur, Audio ohne Erkennung zur Abschrift.

**Abnahme:** Unveränderter BT-WF-008 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-WF-008 · **Status:** specified_not_validated

## PAR-WF-009 – Abnahme aktivieren und Berechtigte festlegen

Mit Abnahme führt nach Korrektur zur Freigabe durch Autor oder regelbasiert berechtigte Autoren/Autorenpools mit Ausnahmen.

**Abnahme:** Unveränderter BT-WF-009 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-WF-009 · **Status:** specified_not_validated

## PAR-WF-010 – Rückweisung und erneute Abnahme

Abnahmeberechtigter kann bestätigen oder zur Schreibkraft zurückverweisen; Bestätigung ermöglicht anschliessende Fertigstellung.

**Abnahme:** Unveränderter BT-WF-010 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-WF-010 · **Status:** specified_not_validated

## PAR-WF-011 – Fertigstellen und Weiterverarbeitung

F5/Fertigstellen öffnet Weiterverarbeitungsoptionen; bestätigte Optionen werden ausgeführt, Vorgang wird fertig gemeldet und Editor geschlossen. Ohne Export bleibt der fertige Fall im Workflow.

**Abnahme:** Unveränderter BT-WF-011 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-WF-011 · **Status:** specified_not_validated

## PAR-WF-012 – Fertiges Diktat wieder zur Korrektur öffnen

Autor oder entsprechend berechtigte Schreibkraft kann einen fertigen Fall entriegeln, korrigieren oder erneut delegieren.

**Abnahme:** Unveränderter BT-WF-012 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-WF-012 · **Status:** specified_not_validated

## PAR-WF-013 – Archivieren, Löschen und Aufbewahrungsfrist

Berechtigte Nutzer melden Fälle zum Archivieren/Löschen; automatische Behandlung fertiger Diktate nach Tagen ist konfigurierbar. Archiv ist nach Nutzer-ID/Monat organisiert und nicht mehr aktive Arbeitsliste.

**Abnahme:** Unveränderter BT-WF-013 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-WF-013 · **Status:** specified_not_validated

## PAR-WF-014 – Benachrichtigungen bei Abnahme und Fertigstellung

Autoren können Benachrichtigungen für zur Abnahme bereite oder fertiggestellte Diktate erhalten; lokale Hinweise sind konfigurierbar.

**Abnahme:** Unveränderter BT-WF-014 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-WF-014 · **Status:** specified_not_validated

## PAR-COR-001 – Manuelle Abschrift mit Player und Vorlage

Audio ohne ASR wird mit frei positionierbarem Player abgespielt; Erfassen öffnet eine passende Benutzer-/Poolvorlage oder Dateiauswahl.

**Abnahme:** Unveränderter BT-COR-001 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-COR-001 · **Status:** specified_not_validated

## PAR-COR-002 – Synchroner Text-Audio-Editor

Korrektur erhält Verbindung zwischen Wort-/Phrasenposition und Audio auch bei Übertippen, Formatieren und Verschieben von Textpassagen.

**Abnahme:** Unveränderter BT-COR-002 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-COR-002 · **Status:** specified_not_validated

## PAR-COR-003 – Wellenform, aktuelle Phrase und Erkennungsmarken

Korrektur zeigt skalierbare/ausblendbare Wellenform, aktuelle Phrase und sichtbare Bereiche für Erkennung, Befehle, Unsicherheit oder fehlende Erkennung.

**Abnahme:** Unveränderter BT-COR-003 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-COR-003 · **Status:** specified_not_validated

## PAR-COR-004 – Tempo und Spulen

Wiedergabe lässt sich bis Faktor 0.5/2 verändern; Rechtsklick setzt Normaltempo; Vor-/Rückspulen ist per Tastatur/Fusssteuerung möglich.

**Abnahme:** Unveränderter BT-COR-004 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-COR-004 · **Status:** specified_not_validated

## PAR-COR-005 – Zwei Korrekturmodi und automatische Rücksprünge

Wiedergabe kann beim Tippen stoppen oder während Übertippen weiterlaufen; Start kann um konfigurierte Zeit oder unterstützte Wortzahl zurückspringen.

**Abnahme:** Unveränderter BT-COR-005 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-COR-005 · **Status:** specified_not_validated

## PAR-COR-006 – Zwischenspeichern und an Position fortsetzen

Korrekturen lassen sich während Bearbeitung speichern; Unterbrechung erhält Textstand und Wiedergabeposition.

**Abnahme:** Unveränderter BT-COR-006 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-COR-006 · **Status:** specified_not_validated

## PAR-COR-007 – Audioindizes verwalten

Benutzer können benannte/verwaltete Indizes an Audiopositionen setzen und aus einer Indexliste anspringen.

**Abnahme:** Unveränderter BT-COR-007 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-COR-007 · **Status:** specified_not_validated

## PAR-COR-008 – Automatische Wiedergabelautstärke

AGC kann die Wiedergabelautstärke angleichen, ohne das unveränderte Originaldiktat zu ersetzen.

**Abnahme:** Unveränderter BT-COR-008 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-COR-008 · **Status:** specified_not_validated

## PAR-COR-009 – Editierfunktionen, Auswahl und Phrase vormerken

Editor unterstützt Undo/Redo, Zwischenablage, Groß-/Kleinschreibung, Wort-/Absatzauswahl und Vormerken einer Phrase für spätere Vokabularprüfung.

**Abnahme:** Unveränderter BT-COR-009 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-COR-009 · **Status:** specified_not_validated

## PAR-COR-010 – Korrekturplatz ohne ASR-Installation

Bearbeiten eines bereits erkannten Diktats braucht am Schreibplatz keine lokale Dragon-/ASR-Engine; Text und Audio bleiben verfügbar.

**Abnahme:** Unveränderter BT-COR-010 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-COR-010 · **Status:** specified_not_validated

## PAR-VOC-001 – Benutzer- und Poolwortschatz

Wörter/Phrasen besitzen geschriebene und optional gesprochene Form; persönliche und gemeinsame Poolwortlisten sind getrennt pflegbar.

**Abnahme:** Unveränderter BT-VOC-001 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-VOC-001 · **Status:** specified_not_validated

## PAR-VOC-002 – Textwortlisten importieren und exportieren

Wortlisten können aus Text importiert und als vollständige Textliste exportiert werden. Es werden neu erstellte Testlisten verwendet, keine Altbestandsmigration.

**Abnahme:** Unveränderter BT-VOC-002 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-VOC-002 · **Status:** specified_not_validated

## PAR-VOC-003 – Negativwortliste und Reaktivierung

Gelöschte Wörter werden für Nutzer oder Pool unterdrückt; spätere Reaktivierung ist möglich. Unterdrückung bleibt unabhängig von einem neu geladenen Profil wirksam.

**Abnahme:** Unveränderter BT-VOC-003 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-VOC-003 · **Status:** specified_not_validated

## PAR-VOC-004 – Vorbehaltswortliste prüfen

Änderungen eingeschränkt berechtigter Nutzer werden vorgemerkt; Prüfer entscheidet Pool, nur Nutzer oder verwerfen, jeweils für Hinzufügen und Löschen.

**Abnahme:** Unveränderter BT-VOC-004 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-VOC-004 · **Status:** specified_not_validated

## PAR-VOC-005 – Sitzungsänderungen prüfen oder zurückstellen

Neue/gelöschte Wörter und neue/geänderte Befehle werden beim Sitzungsende geprüft, Nutzer/Pool zugeordnet oder verworfen; Abbruch merkt sie für später.

**Abnahme:** Unveränderter BT-VOC-005 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-VOC-005 · **Status:** specified_not_validated

## PAR-VOC-006 – Vokabular durchsuchen und aus Markierung ergänzen

Vokabularsuche bietet Anfang/Ende/Vorkommen, geschriebene/gesprochene Form und passende Filter; markierter Text kann direkt als Phrase übernommen werden.

**Abnahme:** Unveränderter BT-VOC-006 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-VOC-006 · **Status:** specified_not_validated

## PAR-VOC-007 – Aussprache trainieren und Phonetik bearbeiten

Je nach Erkennerversion kann Aussprache trainiert, vorgelesen und als phonetische Formen bearbeitet werden; das Handbuch schränkt Bearbeitung bereits für Dragon 11 ein.

**Abnahme:** Unveränderter BT-VOC-007 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-VOC-007 · **Status:** specified_not_validated

## PAR-VOC-008 – Befehle, Makros und Textbausteine verwalten

Persönliche/Poolbefehle sind verwaltbar und exportierbar; Benutzerbefehle können vor/nach einem eingebauten Kommando Aktionen ausführen und in Menüs erscheinen. Globale Text-/Grafikbausteine sind beim Diktieren nutzbar.

**Abnahme:** Unveränderter BT-VOC-008 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-VOC-008 · **Status:** specified_not_validated

## PAR-ASR-001 – Lokale Hintergrundtranskription

Zur Hintergrund-Erkennung markierte Audioaufträge werden in Text plus Korrekturinformationen umgesetzt; Korrektur ist ein nachgelagerter menschlicher Schritt.

**Abnahme:** Unveränderter BT-ASR-001 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-ASR-001 · **Status:** specified_not_validated

## PAR-ASR-002 – Getrennte Online-/Offlineprofile und Quellen

Autoren können für Live- und Hintergrund-Erkennung getrennte Profile für unterschiedliche Aufnahmequellen führen; ohne Trennung kann ein Profil beide Aufgaben bedienen.

**Abnahme:** Unveränderter BT-ASR-002 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-ASR-002 · **Status:** specified_not_validated

## PAR-ASR-003 – ASR-Wartezeit mit manueller Ersatzbearbeitung

Nach konfigurierbarer maximaler Wartezeit kann Hintergrund-Erkennung übersprungen und manuelle Abschrift angeboten werden.

**Abnahme:** Unveränderter BT-ASR-003 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-ASR-003 · **Status:** specified_not_validated

## PAR-ASR-004 – Optimierung aus geprüfter Korrektur

Fertigstellung kann geprüften Text oder Text+Audio zur Verbesserung von Wortschatz/Sprach-/Akustikmodell verwenden; Text+Audio verlangt vollständige Übereinstimmung.

**Abnahme:** Unveränderter BT-ASR-004 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-ASR-004 · **Status:** specified_not_validated

## PAR-ASR-005 – Optimierungsfenster und Mindestdatenmenge

Zeitfenster können aufwendige Modelloptimierungen begrenzen; akustische Daten können gesammelt werden, bis genügend Material vorliegt.

**Abnahme:** Unveränderter BT-ASR-005 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-ASR-005 · **Status:** specified_not_validated

## PAR-ASR-006 – Profil-Speicherung und Arbeitsstationswechsel

Profiländerungen lassen sich nie/nach Rückfrage/immer speichern und optional zentral hinterlegen; ein neuer Arbeitsplatz kann den gespeicherten Stand laden.

**Abnahme:** Unveränderter BT-ASR-006 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-ASR-006 · **Status:** specified_not_validated

## PAR-ASR-007 – Diktierquelle und Versand-Audioformat

Quelle kann während Sitzung gewechselt oder beim Start vorgegeben werden; für Versand ist Standardaudio oder unterstützte Kompression wählbar.

**Abnahme:** Unveränderter BT-ASR-007 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-ASR-007 · **Status:** specified_not_validated

## PAR-EXP-001 – Mehrere Exportwege pro Abschluss

Pro Nutzer gespeicherte Weiterverarbeitungsoptionen erlauben mehrere Ausgaben desselben korrigierten Diktats.

**Abnahme:** Unveränderter BT-EXP-001 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-EXP-001 · **Status:** specified_not_validated

## PAR-EXP-002 – Zwischenablage formatiert/unformatiert

Korrigierter Text kann mit oder ohne Formatierung in Zwischenablage kopiert werden.

**Abnahme:** Unveränderter BT-EXP-002 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-EXP-002 · **Status:** specified_not_validated

## PAR-EXP-003 – RTF-Ausgabe

Korrigierter Text inklusive Formatierung/Grafik kann in RTF geschrieben, im Workflow verwaltet und optional geöffnet werden.

**Abnahme:** Unveränderter BT-EXP-003 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-EXP-003 · **Status:** specified_not_validated

## PAR-EXP-004 – Word-Ausgabe mit Vorlage und Textmarke

Word-Export kann Vorlagen, Textmarken, ein geöffnetes Dokument oder Dokument im Workflow nutzen; Text oder formatierter Inhalt wählbar.

**Abnahme:** Unveränderter BT-EXP-004 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-EXP-004, WB-013 · **Status:** specified_not_validated

## PAR-EXP-005 – OpenOffice-Writer-/ODT-Ausgabe

Writer-Ausgabe bietet Vorlage, offenes Dokument, Text/Formatierung und optionale weitere Bearbeitung.

**Abnahme:** Unveränderter BT-EXP-005 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-EXP-005 · **Status:** specified_not_validated

## PAR-EXP-006 – Dateiname, Ablageort und Kollisionen

Dateinamen können abgefragt oder aus Platzhaltern generiert werden; Ablage im Workflow oder an benanntem Pfad ist wählbar.

**Abnahme:** Unveränderter BT-EXP-006 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-EXP-006, WB-014 · **Status:** specified_not_validated

## PAR-EXP-007 – Allgemeiner Fenstertransfer

Zielanwendung kann über Fensterklasse/-titel gewählt und in Vordergrund gebracht werden; Übertragung erfolgt als Clipboard-Text, Tastensimulation oder formatiert. Keine KIS-Speziallogik.

**Abnahme:** Unveränderter BT-EXP-007 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-EXP-007 · **Status:** specified_not_validated

## PAR-EXP-008 – Geschriebenes Dokument kopieren

Bei manuell geschriebenen Diktaten kann das fertige Dokument an einen ausgewählten oder generierten Dateipfad kopiert werden.

**Abnahme:** Unveränderter BT-EXP-008 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-EXP-008 · **Status:** specified_not_validated

## PAR-EXP-009 – Originalaudio speichern oder Mailentwurf öffnen

Berechtigte Nutzer können Aufnahme auf Datenträger speichern oder an das lokale Standardmailprogramm übergeben.

**Abnahme:** Unveränderter BT-EXP-009 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-EXP-009 · **Status:** specified_not_validated

## PAR-EXP-010 – Word-Add-in: Dokument und Audio übergeben

Word-Integration kann RTF+Audio, RTF+Vorlage+Audio, Text+Audio oder nur Word-Dokument senden; ohne aktive Diktatsitzung nur Dokument.

**Abnahme:** Unveränderter BT-EXP-010 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-EXP-010 · **Status:** specified_not_validated

## PAR-OPS-001 – Fusssteuerung und Tastenzuordnung

Unterstützte Fussschalter steuern Wiedergabe/Spulen/Aufnahme; Haltemodus und Zuordnung sind konfigurierbar, Gerätestatus wird angezeigt.

**Abnahme:** Unveränderter BT-OPS-001 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-OPS-001, WB-011 · **Status:** specified_not_validated

## PAR-OPS-002 – Mikrofon-/Diktiergerätsteuerung und Rückmeldung

Handmikrofon-/DPM-Tasten und Schiebeschalter steuern Aufnahme, Standby, Wiedergabe, Korrektur und Navigation; unterstützte LEDs zeigen Zustand.

**Abnahme:** Unveränderter BT-OPS-002 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-OPS-002 · **Status:** specified_not_validated

## PAR-OPS-003 – Hotkeys frei setzen und zurücksetzen

Kommandos können über einstellbare lokale/globale Hotkeys ausgelöst und auf Standard zurückgesetzt werden.

**Abnahme:** Unveränderter BT-OPS-003 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-OPS-003 · **Status:** specified_not_validated

## PAR-OPS-004 – Autostart, Tray und programmatischer Start

Client/Workflow/Pass besitzen Hintergrund-/Traybetrieb und Startoptionen für Benutzer/Pool; Schliessen einer Liste kann minimieren, Beenden ist separat.

**Abnahme:** Unveränderter BT-OPS-004 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-OPS-004 · **Status:** specified_not_validated

## PAR-OPS-005 – Statistikerfassung und Aufbewahrung

Anwendungsnutzung und Diktatverlauf werden separat aktivierbar und mit zeitlicher Begrenzung erfasst.

**Abnahme:** Unveränderter BT-OPS-005 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-OPS-005 · **Status:** specified_not_validated

## PAR-OPS-006 – Diktat-, Frist- und Bearbeitungsstatistiken

Berichte umfassen Mengen/Dauer nach Autor/Status, verpasste Fristen, Löschungen, Bearbeitungs-/Abnahme-/Fertigstellungszeiten und Anwendungsnutzung.

**Abnahme:** Unveränderter BT-OPS-006 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-OPS-006 · **Status:** specified_not_validated

## PAR-OPS-007 – Statistik als CSV und ohne UI

Statistiken können per UI oder CLI nach Zielpfad, Zeitraum und Berichtsauswahl exportiert werden; ohne UI ist vorgesehen.

**Abnahme:** Unveränderter BT-OPS-007 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-OPS-007 · **Status:** specified_not_validated

## PAR-OPS-008 – Dienstbetrieb, Jobs und beobachtbare Fehler

Zentrale Verarbeitung läuft unabhängig von sichtbarer Clientoberfläche; Aufträge, fehlende Ablage, fehlender Autor/Pool und ausgefallene Engine müssen nachvollziehbar behandelt werden.

**Abnahme:** Unveränderter BT-OPS-008 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-OPS-008 · **Status:** specified_not_validated

## PAR-OPS-009 – Original und Arbeitskopie getrennt aufbewahren

Unveränderte Eingangsdatei wird getrennt von Wiedergabe-/Erkennungsarbeitskopie gehalten; Arbeitskopie kann komprimiert und geschützt sein.

**Abnahme:** Unveränderter BT-OPS-009 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-OPS-009 · **Status:** specified_not_validated

## PAR-OPS-010 – Gerätesteuerung in RDP-/ICA-Sitzungen

Zentrale Workflow-Pass-Vorgaben unterscheiden lokale sowie RDP-/ICA-Gerätewege. Nachbau muss den vereinbarten Remote-Gerätepfad eigenständig nachweisen; lokaler Import belegt keine Remote-Unterstützung.

**Abnahme:** Unveränderter BT-OPS-010 gegen Referenz und Fork plus V4-Sicherheitsguards; keine stillen Ausnahmen.

**Tests:** BT-OPS-010 · **Status:** specified_not_validated

## V4-001 – Speakr-Fork als einziges Fachsystem

Vorhandene Speakr-Domäne/DB/Auth/Queue/Frontend erweitern; kein paralleler WPF-/FastAPI-Workflowserver.

**Abnahme:** Zugeordnete V4-Negativtests und Laufzeitnachweise bestehen.

**Tests:** X4-022 · **Status:** specified_not_validated

## V4-002 – 101-fache Pflichtparität

Jede PAR-Anforderung bleibt zwingend bis explizit versionierter Änderung durch den Auftraggeber; fehlende Enginefunktion ist blocker, kein Erfolg.

**Abnahme:** Zugeordnete V4-Negativtests und Laufzeitnachweise bestehen.

**Tests:** X4-001, X4-002, X4-003, X4-012 · **Status:** specified_not_validated

## V4-003 – Getrennter Buildkontext

Analyseoriginale/Pseudocode/Alt-DB ausserhalb des Buildkontexts und des Fork-Repositories; nur freigegebene Verhaltensverträge verwenden.

**Abnahme:** Zugeordnete V4-Negativtests und Laufzeitnachweise bestehen.

**Tests:** X4-021 · **Status:** specified_not_validated

## V4-004 – Vertrauenswürdige Browserprofile

Webclient für alle Fachaktionen. BYOD ohne Persistenz; managed_web_offline nur nach explizitem Profil-/Securitygate. Clientseitige Flags begründen keine zusätzlichen Rechte.

**Abnahme:** V4-Webgrenzen und zugeordnete Tests nachgewiesen; offene Browser-/Hersteller-/Paritätskonflikte nicht als bestanden darstellen.

**Tests:** WB-008, WB-009, X4-005, X4-006 · **Status:** specified_not_validated

## V4-005 – Import-Agent ohne Diktatfunktionen

Agent darf nur erlaubte Geräte/Ordner inventarisieren, Dateien sichern, Metadaten/Quittungen transportieren und eng begrenzte Importnachbearbeitung ausführen. Keine Aufnahme, Wiedergabe, HID-Steuerung, Hotkeys, Office-, Fenster- oder Clipboardsteuerung; kein lokaler API-Tunnel.

**Abnahme:** V4-Webgrenzen und zugeordnete Tests nachgewiesen; offene Browser-/Hersteller-/Paritätskonflikte nicht als bestanden darstellen.

**Tests:** WB-002, WB-004, X4-009, X4-010, X4-011, X4-020 · **Status:** specified_not_validated

## V4-006 – Nichtdestruktive Audiooperationen

Audiofassung, Editliste, Undo und Textanker konsistent und unveränderliche Originale; mindestens 16 Undo-Schritte.

**Abnahme:** Zugeordnete V4-Negativtests und Laufzeitnachweise bestehen.

**Tests:** X4-007, X4-024 · **Status:** specified_not_validated

## V4-007 – Live-Web-Ereignisvertrag

Audio-/Text-/Phrasengenerationen binden; durable serverseitiger Commit vor server_saved. Browser-RAM oder optionaler lokaler Cache ist keine Serverquittung.

**Abnahme:** V4-Webgrenzen und zugeordnete Tests nachgewiesen; offene Browser-/Hersteller-/Paritätskonflikte nicht als bestanden darstellen.

**Tests:** X4-008 · **Status:** specified_not_validated

## V4-008 – Echte ASR-Fähigkeiten

Capabilities aus realen Prüfungen, explizite Profil-/Lexikon-/Trainingssemantik; kein Hotwordprompt als Ersatz für akustische Optimierung.

**Abnahme:** Zugeordnete V4-Negativtests und Laufzeitnachweise bestehen.

**Tests:** WB-018, X4-013, X4-014 · **Status:** specified_not_validated

## V4-009 – Mehrkanal-Delivery

Unabhängige gebundene Exportversuche und idempotente oder manuell geklärte Seiteneffekte; UNKNOWN niemals blind erneut ausführen.

**Abnahme:** Zugeordnete V4-Negativtests und Laufzeitnachweise bestehen.

**Tests:** X4-015, X4-016, X4-023 · **Status:** specified_not_validated

## V4-010 – Geräteadministration unter Webgrenze

Inventar/Firmwarevergleich im Web; Gerätkonfiguration/Firmware bleiben Originalpflichten, sind aber weder Import-Agent-Aufgaben noch ohne belegten direkten Webweg als umgesetzt zu markieren.

**Abnahme:** V4-Webgrenzen und zugeordnete Tests nachgewiesen; offene Browser-/Hersteller-/Paritätskonflikte nicht als bestanden darstellen.

**Tests:** X4-004 · **Status:** specified_not_validated

## V4-011 – Windows-/AD-Identität serverseitig

Initial lokale Appkonten. Später serverseitige Directory-Diffs/Windows-IWA nur über geprüfte Infrastruktur; weder Import-Agent noch Browsername begründen eine Benutzeridentität.

**Abnahme:** V4-Webgrenzen und zugeordnete Tests nachgewiesen; offene Browser-/Hersteller-/Paritätskonflikte nicht als bestanden darstellen.

**Tests:** X4-017, X4-018 · **Status:** specified_not_validated

## V4-012 – Nachweisbasierte finale Abnahme

Referenz-, Ziel-, Vergleichs- und Projektgate-Evidenz pro Anforderung; Paketprüfungen getrennt von Software-/Laborabnahme.

**Abnahme:** Zugeordnete V4-Negativtests und Laufzeitnachweise bestehen.

**Tests:** X4-019 · **Status:** specified_not_validated

## V4-013 – Ausschliesslicher Webclient

Diktieren, Editieren, Abspielen, Live-ASR, Korrektur, Freigabe, Benutzerverwaltung und Export werden ausschliesslich im Speakr-Webclient bedient. Kein installierter nativer Diktatclient, kein Wrapper oder Plugin als Umgehung.

**Abnahme:** WB-001 bis WB-005 und Paket-/Quellcodegrenze bestehen.

**Tests:** WB-001, WB-002, WB-003, WB-004, WB-005 · **Status:** specified_not_validated

## V4-014 – Konflikte nicht verschweigen

Webgrenzen niemals als automatische N/A-Freigabe oder stillen Ersatz der 101 Originalfunktionen behandeln. WG-Register, Original-BT und Webalternative getrennt; offene Konflikte verhindern 1:1-Zusage.

**Abnahme:** WB-019/WB-020 und alle Original-BT erhalten; keine automatisch genehmigte Ausnahme.

**Tests:** WB-017, WB-019, WB-020 · **Status:** specified_not_validated

---

# Vollständiger Testindex
Die ausführlichen Original- und Zusatzschritte stehen unverändert beziehungsweise ergänzt in `tests/acceptance.json`. Alle 252 Produktfälle starten mit `not_run`.

| Test | Szenario | Anforderungen | Status |
|---|---|---|---|
| T-001 | Ist-Funktionsumfang | R-001 | not_run |
| T-002 | Gerätekompatibilität | R-002 | not_run |
| T-003 | Abschluss getrennt von Stabilität | R-003 | not_run |
| T-004 | Quellen- und Versionsidentität | R-004 | not_run |
| T-005 | Autorbezug | R-005 | not_run |
| T-006 | Original und Metadaten | R-006 | not_run |
| T-007 | Quittierung und Quelllöschung | R-007 | not_run |
| T-008 | Agentensicherheit | R-008 | not_run |
| T-009 | Lokale Anmeldung als Erstprofil | R-009 | not_run |
| T-010 | LDAP-Fähigkeit | R-010 | not_run |
| T-011 | Objektberechtigungen | R-011 | not_run |
| T-012 | Routing | R-012 | not_run |
| T-013 | Patient/Fall | R-013 | not_run |
| T-014 | Lokale ASR | R-014 | not_run |
| T-015 | ASR-Nachweis | R-015 | not_run |
| T-016 | Manueller Arbeitsweg | R-016 | not_run |
| T-017 | Editor und Konflikte | R-017 | not_run |
| T-018 | Arbeitsplatzergonomie | R-018 | not_run |
| T-019 | Freigabeverantwortung | R-019 | not_run |
| T-020 | Freigabebindung | R-020 | not_run |
| T-021 | Manueller Export | R-021 | not_run |
| T-022 | Interner BYOD-Zugang | R-022 | not_run |
| T-023 | Mobile Aufnahme | R-023 | not_run |
| T-024 | Mobile Wiederherstellung | R-024 | not_run |
| T-025 | BYOD-Datenminimierung | R-025 | not_run |
| T-026 | Edge-WebView-Vertrag | R-026 | not_run |
| T-027 | KIS-Kontext | R-027 | not_run |
| T-028 | KIS-Ausgabe | R-028 | not_run |
| T-029 | Unklarer KIS-Ausgang | R-029 | not_run |
| T-030 | Kontextwechsel und Korrektur | R-030 | not_run |
| T-031 | Audit | R-031 | not_run |
| T-032 | Betriebsfreigabe | R-032 | not_run |
| T-033 | Backup/Restore | R-033 | not_run |
| T-034 | Datenschutz und Zweck | R-034 | not_run |
| T-035 | Softwarelieferkette | R-035 | not_run |
| T-036 | Zustandskonsistenz | R-036 | not_run |
| T-037 | Leistung und Nutzen | R-037 | not_run |
| T-038 | Migration und Ablösung | R-038 | not_run |
| T-039 | Sicherheitsgrenzen | R-039 | not_run |
| T-040 | Betriebs-/Kostenentscheidung | R-040 | not_run |
| X-001 | Dateigrösse stabil, EOL falsch; später EOL gesetzt und wieder entfernt | R-003, R-004 | not_run |
| X-002 | Transkript von Diktat B als Freigabe/Export für Diktat A referenzieren | R-020, R-028 | not_run |
| X-003 | Patient A aufgenommen; ines wechselt zu B; alter Tab speichert/versendet | R-027, R-030 | not_run |
| X-004 | KIS speichert; Antwort geht verloren; danach Plattformrestore | R-028, R-029, R-033 | not_run |
| X-005 | Smartphone sperren, Anruf, WLAN weg, RAM-Puffer voll, Browser beendet | R-023, R-024, R-025 | not_run |
| X-006 | Crash vor/nach Objektwrite, DB-Commit, Jobclaim und Ack | R-007, R-036 | not_run |
| X-007 | WebView/RDS-Benutzerwechsel mit bestehenden Cookies | R-009, R-011, R-026 | not_run |
| X-008 | Geteiltes Gerät, gleicher Dateiname auf anderem Gerät, Karten-/PC-Wechsel | R-004, R-005 | not_run |
| X-009 | Lease abgelaufen; alte Schreibkraft speichert; ASR-Lauf trifft verspätet ein | R-017, R-020, R-036 | not_run |
| X-010 | Schreibkraft korrigiert, hat aber keine klinische Freigaberolle | R-019, R-028 | not_run |
| T-051 | Upstream-Pin und Struktur | R-041 | not_run |
| T-052 | Legacyrouten und unbekannte Route | R-042, R-011 | not_run |
| T-053 | Keine E-Mail-Identitätsübernahme | R-043, R-009 | not_run |
| T-054 | Originalschutz bei Queuefehler | R-044, R-007 | not_run |
| T-055 | Browserstorage über Fehlerpfade | R-045, R-025 | not_run |
| T-056 | Dev-Mocks sind keine Produktion | R-046 | not_run |
| T-057 | Source und Artefaktpins | R-047, R-035 | not_run |
| T-058 | Kanalspoofing | R-048 | not_run |
| T-059 | Nachweis Wiederverwendung | R-049 | not_run |
| T-060 | Hot-Setting kann Klinikpolicy nicht aushebeln | R-050 | not_run |
| T-061 | Fremde Transkriptversion auf DB-Ebene | R-020 | not_run |
| T-062 | Späte Autosaves nach Freigabe | R-017, R-020 | not_run |
| T-063 | Simulator-EOL nicht als Hardwarebeweis | R-003, R-046 | not_run |
| T-064 | Fehlender Container-/Byteabschnitt | R-024 | not_run |
| T-065 | Timeout nach tatsächlicher Mock-Speicherung | R-029 | not_run |
| T-066 | Autor ist kein Poolleser | R-012, R-011 | not_run |
| T-067 | Gleiche Bytes verschiedene Quellen | R-004 | not_run |
| T-068 | Crash zwischen Original und DB-Ack | R-036 | not_run |
| T-069 | Kontextwechsel im Mehrfensterbetrieb | R-027, R-030 | not_run |
| T-070 | LDAPS-Föderationsprofil | R-010 | not_run |
| T-071 | Lokaler Start ohne IdP | R-009, R-051, R-062 | not_run |
| T-072 | Lokale Nutzer und Pools | R-051 | not_run |
| T-073 | Bootstrap konkurrierend | R-052 | not_run |
| T-074 | Letzter lokaler Admin | R-052 | not_run |
| T-075 | Codeaktivierung einmalig | R-053 | not_run |
| T-076 | Reset ohne SMTP | R-053 | not_run |
| T-077 | Reset ohne Policybypass | R-053, R-058 | not_run |
| T-078 | Passwortformat in PostgreSQL | R-054 | not_run |
| T-079 | Passwortrichtlinie | R-054 | not_run |
| T-080 | Geteilte Ratelimits | R-054 | not_run |
| T-081 | Kontodeaktivierung und Sessionversion | R-055 | not_run |
| T-082 | Deaktiviertes Entra | R-009, R-056 | not_run |
| T-083 | OIDC-Verifikation | R-056 | not_run |
| T-084 | OIDC minimaler Netzwerkumfang | R-056 | not_run |
| T-085 | Explizites Linking | R-057 | not_run |
| T-086 | Historie beim Providerwechsel | R-057, R-060 | not_run |
| T-087 | Konkurrierende externe Bindung | R-057 | not_run |
| T-088 | Entra-Ausfall | R-058, R-061 | not_run |
| T-089 | Policy über Legacyrouten | R-058 | not_run |
| T-090 | SSO verleiht keine Rollen | R-059 | not_run |
| T-091 | Upgrade und Harddelete | R-060 | not_run |
| T-092 | Administrative Rechte getrennt | R-051, R-059 | not_run |
| T-093 | Keine Secrets in Artefakten | R-053 | not_run |
| T-094 | Sitzung und Reauth | R-055 | not_run |
| T-095 | Hybride Aktivierung mit Fehler | R-061 | not_run |
| T-096 | Offboarding-Vertrag | R-061 | not_run |
| T-097 | Device-only und deaktivierter Autor | R-058 | not_run |
| T-098 | Konsistente Gesamtspezifikation | R-062 | not_run |
| BT-ADM-001 | Verhaltensfall | PAR-ADM-001 | not_run |
| BT-ADM-002 | Verhaltensfall | PAR-ADM-002 | not_run |
| BT-ADM-003 | Verhaltensfall | PAR-ADM-003 | not_run |
| BT-ADM-004 | Verhaltensfall | PAR-ADM-004 | not_run |
| BT-ADM-005 | Verhaltensfall | PAR-ADM-005 | not_run |
| BT-ADM-006 | Verhaltensfall | PAR-ADM-006 | not_run |
| BT-ADM-007 | Verhaltensfall | PAR-ADM-007 | not_run |
| BT-ADM-008 | Verhaltensfall | PAR-ADM-008 | not_run |
| BT-ADM-009 | Verhaltensfall | PAR-ADM-009 | not_run |
| BT-ADM-010 | Verhaltensfall | PAR-ADM-010 | not_run |
| BT-ADM-011 | Verhaltensfall | PAR-ADM-011 | not_run |
| BT-CFG-001 | Verhaltensfall | PAR-CFG-001 | not_run |
| BT-CFG-002 | Verhaltensfall | PAR-CFG-002 | not_run |
| BT-CFG-003 | Verhaltensfall | PAR-CFG-003 | not_run |
| BT-CFG-004 | Verhaltensfall | PAR-CFG-004 | not_run |
| BT-CFG-005 | Verhaltensfall | PAR-CFG-005 | not_run |
| BT-REC-001 | Verhaltensfall | PAR-REC-001 | not_run |
| BT-REC-002 | Verhaltensfall | PAR-REC-002 | not_run |
| BT-REC-003 | Verhaltensfall | PAR-REC-003 | not_run |
| BT-REC-004 | Verhaltensfall | PAR-REC-004 | not_run |
| BT-REC-005 | Verhaltensfall | PAR-REC-005 | not_run |
| BT-REC-006 | Verhaltensfall | PAR-REC-006 | not_run |
| BT-REC-007 | Verhaltensfall | PAR-REC-007 | not_run |
| BT-REC-008 | Verhaltensfall | PAR-REC-008 | not_run |
| BT-LIVE-001 | Verhaltensfall | PAR-LIVE-001 | not_run |
| BT-LIVE-002 | Verhaltensfall | PAR-LIVE-002 | not_run |
| BT-LIVE-003 | Verhaltensfall | PAR-LIVE-003 | not_run |
| BT-LIVE-004 | Verhaltensfall | PAR-LIVE-004 | not_run |
| BT-LIVE-005 | Verhaltensfall | PAR-LIVE-005 | not_run |
| BT-LIVE-006 | Verhaltensfall | PAR-LIVE-006 | not_run |
| BT-LIVE-007 | Verhaltensfall | PAR-LIVE-007 | not_run |
| BT-LIVE-008 | Verhaltensfall | PAR-LIVE-008 | not_run |
| BT-IMP-001 | Verhaltensfall | PAR-IMP-001 | not_run |
| BT-IMP-002 | Verhaltensfall | PAR-IMP-002 | not_run |
| BT-IMP-003 | Verhaltensfall | PAR-IMP-003 | not_run |
| BT-IMP-004 | Verhaltensfall | PAR-IMP-004 | not_run |
| BT-IMP-005 | Verhaltensfall | PAR-IMP-005 | not_run |
| BT-IMP-006 | Verhaltensfall | PAR-IMP-006 | not_run |
| BT-IMP-007 | Verhaltensfall | PAR-IMP-007 | not_run |
| BT-IMP-008 | Verhaltensfall | PAR-IMP-008 | not_run |
| BT-IMP-009 | Verhaltensfall | PAR-IMP-009 | not_run |
| BT-IMP-010 | Verhaltensfall | PAR-IMP-010 | not_run |
| BT-WF-001 | Verhaltensfall | PAR-WF-001 | not_run |
| BT-WF-002 | Verhaltensfall | PAR-WF-002 | not_run |
| BT-WF-003 | Verhaltensfall | PAR-WF-003 | not_run |
| BT-WF-004 | Verhaltensfall | PAR-WF-004 | not_run |
| BT-WF-005 | Verhaltensfall | PAR-WF-005 | not_run |
| BT-WF-006 | Verhaltensfall | PAR-WF-006 | not_run |
| BT-WF-007 | Verhaltensfall | PAR-WF-007 | not_run |
| BT-WF-008 | Verhaltensfall | PAR-WF-008 | not_run |
| BT-WF-009 | Verhaltensfall | PAR-WF-009 | not_run |
| BT-WF-010 | Verhaltensfall | PAR-WF-010 | not_run |
| BT-WF-011 | Verhaltensfall | PAR-WF-011 | not_run |
| BT-WF-012 | Verhaltensfall | PAR-WF-012 | not_run |
| BT-WF-013 | Verhaltensfall | PAR-WF-013 | not_run |
| BT-WF-014 | Verhaltensfall | PAR-WF-014 | not_run |
| BT-COR-001 | Verhaltensfall | PAR-COR-001 | not_run |
| BT-COR-002 | Verhaltensfall | PAR-COR-002 | not_run |
| BT-COR-003 | Verhaltensfall | PAR-COR-003 | not_run |
| BT-COR-004 | Verhaltensfall | PAR-COR-004 | not_run |
| BT-COR-005 | Verhaltensfall | PAR-COR-005 | not_run |
| BT-COR-006 | Verhaltensfall | PAR-COR-006 | not_run |
| BT-COR-007 | Verhaltensfall | PAR-COR-007 | not_run |
| BT-COR-008 | Verhaltensfall | PAR-COR-008 | not_run |
| BT-COR-009 | Verhaltensfall | PAR-COR-009 | not_run |
| BT-COR-010 | Verhaltensfall | PAR-COR-010 | not_run |
| BT-VOC-001 | Verhaltensfall | PAR-VOC-001 | not_run |
| BT-VOC-002 | Verhaltensfall | PAR-VOC-002 | not_run |
| BT-VOC-003 | Verhaltensfall | PAR-VOC-003 | not_run |
| BT-VOC-004 | Verhaltensfall | PAR-VOC-004 | not_run |
| BT-VOC-005 | Verhaltensfall | PAR-VOC-005 | not_run |
| BT-VOC-006 | Verhaltensfall | PAR-VOC-006 | not_run |
| BT-VOC-007 | Verhaltensfall | PAR-VOC-007 | not_run |
| BT-VOC-008 | Verhaltensfall | PAR-VOC-008 | not_run |
| BT-ASR-001 | Verhaltensfall | PAR-ASR-001 | not_run |
| BT-ASR-002 | Verhaltensfall | PAR-ASR-002 | not_run |
| BT-ASR-003 | Verhaltensfall | PAR-ASR-003 | not_run |
| BT-ASR-004 | Verhaltensfall | PAR-ASR-004 | not_run |
| BT-ASR-005 | Verhaltensfall | PAR-ASR-005 | not_run |
| BT-ASR-006 | Verhaltensfall | PAR-ASR-006 | not_run |
| BT-ASR-007 | Verhaltensfall | PAR-ASR-007 | not_run |
| BT-EXP-001 | Verhaltensfall | PAR-EXP-001 | not_run |
| BT-EXP-002 | Verhaltensfall | PAR-EXP-002 | not_run |
| BT-EXP-003 | Verhaltensfall | PAR-EXP-003 | not_run |
| BT-EXP-004 | Verhaltensfall | PAR-EXP-004 | not_run |
| BT-EXP-005 | Verhaltensfall | PAR-EXP-005 | not_run |
| BT-EXP-006 | Verhaltensfall | PAR-EXP-006 | not_run |
| BT-EXP-007 | Verhaltensfall | PAR-EXP-007 | not_run |
| BT-EXP-008 | Verhaltensfall | PAR-EXP-008 | not_run |
| BT-EXP-009 | Verhaltensfall | PAR-EXP-009 | not_run |
| BT-EXP-010 | Verhaltensfall | PAR-EXP-010 | not_run |
| BT-OPS-001 | Verhaltensfall | PAR-OPS-001 | not_run |
| BT-OPS-002 | Verhaltensfall | PAR-OPS-002 | not_run |
| BT-OPS-003 | Verhaltensfall | PAR-OPS-003 | not_run |
| BT-OPS-004 | Verhaltensfall | PAR-OPS-004 | not_run |
| BT-OPS-005 | Verhaltensfall | PAR-OPS-005 | not_run |
| BT-OPS-006 | Verhaltensfall | PAR-OPS-006 | not_run |
| BT-OPS-007 | Verhaltensfall | PAR-OPS-007 | not_run |
| BT-OPS-008 | Verhaltensfall | PAR-OPS-008 | not_run |
| BT-OPS-009 | Verhaltensfall | PAR-OPS-009 | not_run |
| BT-OPS-010 | Verhaltensfall | PAR-OPS-010 | not_run |
| GRK-T-01 | KIS-Vertrag nicht durch Mock ersetzen | R-026, R-027, R-028, R-029 | not_run |
| GRK-T-02 | Hardware-Evidenz korrekt kennzeichnen | R-002, R-003, R-004, R-005, R-006 | not_run |
| GRK-T-03 | ASR-Messung und vorab festgelegte Grenzen | R-014, R-015, R-016, R-037 | not_run |
| GRK-T-04 | Entscheidungen ohne erfundene Verantwortliche | R-001, R-019, R-032, R-033, R-034, R-040 | not_run |
| GRK-T-05 | TCO null ist nicht CHF 0 | R-037, R-040 | not_run |
| GRK-T-06 | Kein stiller Scopewechsel zur Teilablösung | R-001, R-038, R-040 | not_run |
| GRK-T-07 | Schreibpoolbedienung früh nachweisen | R-017, R-018, R-019, R-021 | not_run |
| GRK-T-08 | Datenschutzvorlage ist keine Freigabe | R-025, R-031, R-033, R-034 | not_run |
| GRK-T-09 | Additive Vertrags-/Migrationsintegration | R-020, R-035, R-036, R-041, R-046, R-049 | not_run |
| X4-001 | Poolverwechslung | V4-002, PAR-ADM-002, PAR-ADM-003, PAR-ADM-004 | not_run |
| X4-002 | Erweiterte Autorensicht | V4-002, PAR-ADM-006 | not_run |
| X4-003 | EOL-Konfigurationsgrenze | V4-002, PAR-IMP-004 | not_run |
| X4-004 | Löschung falscher Quelle | V4-010, PAR-IMP-005 | not_run |
| X4-005 | Gefälschtes Managed-Web-Profil | V4-004, PAR-REC-007 | not_run |
| X4-006 | Web-Offlinecache und Neustart | V4-004, PAR-LIVE-003, PAR-REC-007 | not_run |
| X4-007 | Undo und Freigabe | V4-006, PAR-REC-003, PAR-COR-002 | not_run |
| X4-008 | Späte Live-Events | V4-007, PAR-LIVE-001, PAR-LIVE-004 | not_run |
| X4-009 | Keine fremde Fensterbindung | V4-005, PAR-LIVE-006, PAR-EXP-007 | not_run |
| X4-010 | Keine lokale Brücke | V4-005, PAR-OPS-004 | not_run |
| X4-011 | Keine KIS-Automation | V4-005, PAR-EXP-007 | not_run |
| X4-012 | Schutz-Diktat | V4-002, PAR-REC-006 | not_run |
| X4-013 | Modelltraining nur echt | V4-008, PAR-ASR-004, PAR-ASR-005, PAR-VOC-007 | not_run |
| X4-014 | Negativlexikon | V4-008, PAR-VOC-003 | not_run |
| X4-015 | Mehrkanal-Timeout | V4-009, PAR-EXP-001 | not_run |
| X4-016 | Arbeitsstand vs. final | V4-009, PAR-LIVE-002, PAR-WF-009, PAR-EXP-004 | not_run |
| X4-017 | AD-Diff ohne Seiteneffekt | V4-011, PAR-ADM-009 | not_run |
| X4-018 | Windows-SSO unbekannt | V4-011, PAR-ADM-010 | not_run |
| X4-019 | Statistik aus, Audit an | V4-012, PAR-OPS-005, PAR-OPS-007 | not_run |
| X4-020 | Remote-Sitzung und Browserhardware | V4-005, PAR-OPS-010 | not_run |
| X4-021 | Kein Altcode im Fork | V4-003 | not_run |
| X4-022 | Vollständige Pflichtmenge | V4-001 | not_run |
| X4-023 | Web-Dokument und Audio-Paketbindung | V4-009, PAR-EXP-010 | not_run |
| X4-024 | Änderungen invalidieren Asranker | V4-006, PAR-COR-002, PAR-REC-008 | not_run |
| WB-001 | Web ohne Installation | V4-013 | not_run |
| WB-002 | Agent hat keine Capture-API | V4-005 | not_run |
| WB-003 | Kein Native-Host-Umweg | V4-013 | not_run |
| WB-004 | Agent-Credentials eng | V4-005 | not_run |
| WB-005 | Browserfokus verlieren | V4-013 | not_run |
| WB-006 | Lokaler Start ohne IdP | R-051 | not_run |
| WB-007 | BYOD-Datenminimierung | R-025 | not_run |
| WB-008 | Offline-Keys | V4-004 | not_run |
| WB-009 | Kein Offline-Rechtebypass | V4-004 | not_run |
| WB-010 | MIME-/Containerfinalisierung | R-024 | not_run |
| WB-011 | Geprüfter Pedalweg | PAR-OPS-001 | not_run |
| WB-012 | Keine Hardwarekonfiguration im Agent | PAR-IMP-009 | not_run |
| WB-013 | Keine automatische Office-Parität | PAR-EXP-004 | not_run |
| WB-014 | Picker-Kollision | PAR-EXP-006 | not_run |
| WB-015 | Eigene Live-Sitzung | PAR-LIVE-001 | not_run |
| WB-016 | RTF/ODT/Richtext-Roundtrip | PAR-REC-008 | not_run |
| WB-017 | Original-BT unverändert | V4-014 | not_run |
| WB-018 | ASR-Training keine Attrappe | V4-008 | not_run |
| WB-019 | Webkonflikt bleibt Pflicht | V4-014 | not_run |
| WB-020 | Referenz und Zielnachweis | V4-014 | not_run |

---

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
