# Fortschritt V4
## Tatsächlich erledigt in dieser Lieferung
Spezifikationen konsolidiert, 101 Funktionen und Original-BT übernommen, Web-only-Grenze und 13 offene Teilkonflikte dokumentiert; Originalartefakte inventarisiert. Paketprüfungen separat unter evidence/package-checks.json.
## Nicht erledigt
Keine Anwendung implementiert; kein Speakr-Build oder Windows-/Browser-/Geräte-/KIS-/ASR-Test ausgeführt. Alle 252 Produkttests stehen auf not_run. Keine Produktivfreigabe und keine Scopeausnahme.
## Nächste Codearbeit
S00 Baseline prüfen; P00 Quell-/Paritätsvorrang; P03 Browserfähigkeit/Agentgrenze; lokales Identity-/ACL-Slice. Unabhängig P10 tatsächliche ASR-/Lexikon-/Trainingscapabilities prüfen.
## Bei vorhandener Implementierung
Bestehenden Fortschritt nicht durch diese Anfangsnotiz löschen; versioniert zusammenführen und bereits belegte Tests mit realen Artefakten übernehmen.

## P-145 – PAR-COR-005
Zeitbasierter Rücksprung im Schreibplatz (Standard 2 s, 0 s ohne Rücksprung) lokal in Chromium mit synthetischem Audio geprüft: Tipp-Pause, Weiterlaufmodus, manuelle Pause/Neustart und Tastaturemulation der Pedalsteuerung. Referenzvergleich, Wortzahl-Rücksprung und physisches Pedal bleiben `not_run`.
