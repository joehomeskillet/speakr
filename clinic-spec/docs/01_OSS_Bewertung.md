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
