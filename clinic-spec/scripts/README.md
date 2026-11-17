# Hilfsskripte V4
## Initialize-SuedhangWorkspace.ps1
Nur für ein **neues** Arbeitsverzeichnis. PowerShell 5.1+, Git. Standard Dry-Run; `-Apply` klont den gepinnten öffentlichen Speakr-Stand, verifiziert Commit, erzeugt `clinic/main` und kopiert ausschliesslich `01_Claude_Bauauftrag` nach `clinic-spec/`. Keine Installation, keine Ausführung der App, keine Produktivänderung und kein Push. Bestehendes Ziel wird abgewiesen. Die Ursprungs-CLAUDE.md im neuen Checkout wird gesichert. Protokoll unter TEMP/SuedhangSpecBootstrap.

```powershell
.\scripts\Initialize-SuedhangWorkspace.ps1 -Destination C:\codex\suedhang-dictation
.\scripts\Initialize-SuedhangWorkspace.ps1 -Destination C:\codex\suedhang-dictation -Apply
```

Bei bereits begonnenem Fork nicht neu initialisieren, sondern START_CLAUDE.md und Versionsdelta verwenden. Dieses Skript wurde hier statisch geprüft, nicht unter Windows ausgeführt.

## Verify-SuedhangPackage.py
Python 3.10+, nur Standardbibliothek. Read-only, solange `--report` nicht ausdrücklich gesetzt wird. Prüft 101 Quellfunktionen, unveränderte Originaltests, Referenzen, Tasks, Web-only/local-first, Hashes und optional Original-ZIP-CRC. Niemals Originalprogramme starten, Archive extrahieren oder Datenbanken öffnen.

```text
python scripts/Verify-SuedhangPackage.py --root .
python 01_Claude_Bauauftrag/scripts/Verify-SuedhangPackage.py --root . --deep-originals
```

Erster Aufruf im Bauauftragsordner, zweiter im Gesamtpaket. `passed` besagt nur, dass die **Paketprüfungen** bestanden sind. Es sind keine 252 Produkt-/Kliniktests gelaufen. Nach echten Änderungen im Fork muss ein neuer Manifeststand bewusst erzeugt werden; ein Hashfehler darf nicht durch Abschwächung von Originaltests beseitigt werden.
