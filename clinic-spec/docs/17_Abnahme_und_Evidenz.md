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
