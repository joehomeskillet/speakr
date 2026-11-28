# Recorder-QA (RD-03)

Soll-Matrix aus SDD `qa/ACCEPTANCE.md`; alle Zeilen `not_run` in RD-00. Nur synthetische Audiodaten. Vor jedem Test RT-03 separat prüfen: alle vier Vorlagen, danach Recorder; kein Mithören oder automatischer Start bei Auswahl. P0-Verstösse sperren die Freigabe; P1-Abweichungen begründen.

| ID | Prio | Probe und erwartetes Ergebnis | Ebene |
|---|---|---|---|
| T01 | P0 | Autorisiertes Standardmikrofon: nach Vorlagenwahl startet ein Klick, kein Pflichtdialog; beim Erstzugriff nur nötige Berechtigung/Freigabe. | Echtes Gerät |
| T02 | P0 | Mikrofonzugriff verweigern: Abhilfe/erneute Prüfung, kein laufender Timer. | Echter Browser |
| T03 | P0 | Stummes/fehlendes Signal: kein «bereit» aus Gerätename, kein Fake-Pegel. | Echtes Gerät |
| T04 | P0 | 20 Minuten, Pause/Fortsetzen: Dauer ohne Pause, keine verlorenen Chunks, Pause behauptet nicht «Mikrofon aus» bei offenem Stream. | Echtes Gerät |
| T05 | P0 | Netz im Upload trennen: kein Erfolg, tatsächliche Datenverfügbarkeit, autorisierter Retry derselben ID. | Staging |
| T06 | P0 | 100 % Transport bei verzögertem Commit: kein «gespeichert» bis zur dauerhaften ACK für den Vorgang. | Browser + Staging-Speicher |
| T07 | P0 | ACK nach Commit verlieren und Status abfragen; parallel wiederholen, danach dieselbe ID mit anderen Bytes prüfen: exakt ein Diktat, veränderter Inhalt Konflikt. | DB/Datei + Staging |
| T08 | P0 | 401/403, Reauth gleiches vs. anderes Konto: Statusabgleich für den berechtigten Besitzer; kein Zugriff, Replay oder Weitergabe im fremden Kontext. | Staging-Auth |
| T09 | P0 | Stopp → Save → Reload/Retry ohne Einreichen: nur Entwurf, kein Schreibauftrag, kein Mail/KIS-Transfer. Explizites Einreichen separat regressionsprüfen. | Staging + Workflow/Audit |
| T10 | P0 | Speichern ohne und mit bestätigtem Empfang: Speicher/Versand/Eingang getrennt, bei `notApplicable` ausgeblendet; `received` nur mit Beleg. | Staging + Schreibdienst (D4) |
| T11 | P0 | 320×640, 390×844, 768×1024, 1440×900, 1920×1080, 200 % Zoom: kein horizontales Scrollen, Hauptaktion erreichbar, lange Gerätenamen und mobiles Menü bedienbar. | Browser + Sichtprüfung |
| T12 | P0 | Hell/dunkel und Systempräferenz: Kontrast, Fokus, kein heller Flash; Ready, Aufnahme, Pause, Transfer/Commit, Saved, Fehler und Mikrofonzustände vergleichen. | Browser + Sichtprüfung |
| T13 | P0 | Tastatur, Screenreader und reduced motion: Fokus folgt Zustand sinnvoll, keine Timerflut, Aktionen erreichbar, 44 px Touch-Ziele prüfen. | Manuelle A11y |
| T14 | P0 | Navigation, Reload, Sleep, Hintergrund, Tabwechsel, Logout auf Shared PC: keine unbemerkte Aufgabe/falsche Erfolgsmeldung; Recovery nur nach Policy. | Echtes OS/Gerät |
| T15 | P0 | Ungeklärte ASR-/LLM-/Speicherziele: keine Standortzusage, nur genehmigte Pfade; kein externer Fallback. | Staging-Konfig + Datenschutz |
| T16 | P0 | Kein freigegebenes Profil: verständliche Abhilfe; Aufnahme-/Dateipfad serverseitig nicht umgehbar. | Contract + Staging |
| T17 | P1 | VOX an/aus: Ergebnis und Erklärung entsprechen tatsächlicher Nachbearbeitung; Einstellungsverhalten gemäss D1/persistiert nur wie freigegeben, nicht mit ASR-Opt-out verwechseln. | Unit + Browser |
| T18 | P0 | Transfer ohne bekanntes Total: indeterminiert, keine erfundenen Prozent oder ETA. | UI + Browser |
| T19 | P0 | Doppelstart/-stopp/-retry, verzögerte ACK und neue Aufnahme: Single-flight, alte Antwort ändert aktuellen Vorgang nicht. | Unit + Browser/Staging |
| T20 | P0 | Recovery ohne Daten, Quota, beschädigter Blob: keine Kopiezusage; intakte freigegebene Kopie nur nach autorisiertem Lesen/Integritätscheck. | Unit + Managed-Staging (D2) |
| T21 | P1 | Erlaubte/unerlaubte, falsch deklarierte oder übergrosse Audiodatei: Validierung vor Commit, kein Inhaltslog, gleicher ACK-Vertrag. | Contract + Staging |
| T22 | P0 | Berechtigungsentzug, Wechsel/Abziehen während Aufnahme, Bluetooth-Unterbruch: sichtbarer Fehler, kein stilles Weiterzählen oder stiller Gerätewechsel. | Echtes Gerät |
| T23 | P0 | Shared PC, anderes Konto/Mandant, fremde ID: kein Zugriff auf Status, Daten oder lokale Recovery; BYOD schreibt nichts absichtlich persistent. | Staging-Auth + Storage |
| T24 | P1 | Arbeitsliste, Playerposition, Fusspedal: separater Auftrag, keine Abnahme durch Recorder-Paket. | Separater echter Hardwaretest |

**Regressionseinstieg (vorhanden, nicht ausgeführt):** `tests/clinic/ui/{compose-recording,recorder-phases-ui,clinic-recorder-adapter,vox-gate,p04-submit-draft,compose-offline,offline-store-ui}.test.js`; `tests/clinic/{test_ingest,test_ingest_recovery,test_wp_v8_send_route,test_wp_offline_policy}.py`; `tests/clinic/browser/{test_v8_recorder_adapter,test_v8_recorder_draft_audio}.py`. Wiederholbare Browserabläufe mit vorhandener Browser-Infrastruktur und stabilen Selektoren; Fake-Medien sind Regressionsevidenz, kein Nachweis für echte Mikrofone, Philips, Entra, KIS oder Pedal. Bestehende Tests und offene Web-Paritätslücken nicht umdeuten.

**Freigabe:** G1 Mapping und betroffene D-Entscheide; G2 echte Capture-/Gerätematrix (Chrome/Edge/Firefox/Safari auf eingesetzten OS inkl. Mobil/Tablet); G3 dauerhafte Persistenz, Langaufnahme/Limits, Retry/Auth und DB-/Speicherfehler; G4 Datenschutz/Rechte/Shared PC; G5 A11y/Sichtprüfung gegen helle/dunkle Desktop-/Mobil-Referenz; G6 reale rollenbasierte Staging-Abnahme (Diktierende, Pflege, Schreibdienst, IT/Datenschutz) und Rückrollprobe. Zwölf synthetische Persona-Profile dienen zur Auswahl der Aufgaben, nicht als Nutzertest. Pro Lauf Build, Browser/OS/Gerät, Kontext, Soll/Ist, unveränderte Befehlsausgabe oder datensparsames Artefakt, Ergebnis, Prüfer und Defect-ID festhalten. Kein Gate in RD-00 als bestanden markiert.
