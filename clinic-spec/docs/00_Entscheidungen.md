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
