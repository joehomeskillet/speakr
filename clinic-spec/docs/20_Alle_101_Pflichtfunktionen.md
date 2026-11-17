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
