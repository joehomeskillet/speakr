# 07 – Lieferplan und Reihenfolge
## Frühe Nachweise statt neuer Grundsatzfragen
S00: gepinnten Speakr-Build/Tests und vorhandene Erweiterungspunkte prüfen. P00: 101 Funktions-/Testverträge, Scopevorrang und Originaltrennung. P03: Browseraufnahme, Richtext, Mikrofonwahl, WebHID-/Tastaturgrenzen, Clipboard/Pickers und offene WG untersuchen. P10: echte Lexikon-/Aussprache-/Trainingscapabilities früh prüfen, nicht am Projektende.

Danach S01–S05: zentrale Policy, Original-/Jobschutz, lokale Userverwaltung und ein vollständiger synthetischer Verlauf. P01/P02/P04 liefern Autoren-/Schreibpools, ACL-/Workflowparität, zentrale Policies/Vorlagen/Metadaten. P05/P06/P08 liefern Webrecorder, Live, Editor, Audiooperationen und Korrektur. P07 liefert Import-Agent und Web-Importzentrale. P09/P10 liefern Wortschatz/Befehle/Profile/ASR. P11/P12 liefern Dokumente, Webhardware und Statistik. Vorhandene S06–S12 bleiben für ASR, Agent, BYOD, KIS und Betrieb/Abnahme erhalten; referenzierte Tasks nicht parallel doppelt implementieren.

`planning/backlog.json` ist die maschinenlesbare Abhängigkeitsliste. S-Tasks härten die bestehende Klinikbasis; P-Tasks erweitern die Parität. Jedes Modul implementiert einmal die gemeinsame Fachlogik. Additive Tests bauen auf denselben Entitäten auf. Nach lokalen Builds darf Claude die nächste unabhängige Aufgabe ohne erneute Freigabefrage bearbeiten.

## Releasegates
Ein lokaler synthetischer Prototyp braucht keine Entra-Secrets/Philips-Hardware. Ein echter Gerätepilot braucht Geräte-/Identity-/Daten-/Betriebsnachweise. BYOD und KIS erhalten eigene Freigaben. Ein vollständiger Ersatz verlangt alle 101 Original-BT sowie Datenschutz, Backup/Restore, fachliche Freigabe und Migration/Cutover. Native Webkonflikte und nicht nachgewiesene ASR-Trainingseigenschaften bleiben sichtbare Sperren für diese vollständige Aussage.

Eine Teilimplementierung ist kein genehmigter Funktionsverzicht. Kein automatischer Wechsel auf einen Windows-Diktatclient. Fachliche Budget-/Scopeentscheidungen gebündelt im Register, nicht als repetitive Fragen in jedem Codinglauf.
