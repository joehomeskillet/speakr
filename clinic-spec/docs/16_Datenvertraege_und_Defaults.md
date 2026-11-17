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
