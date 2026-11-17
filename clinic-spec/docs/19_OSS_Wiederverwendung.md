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
