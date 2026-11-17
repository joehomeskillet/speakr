# Synthetische Testdaten

`generated/` enthält drei gültige WAV-Dateien (Ton/Stille), zwei absichtlich ungültige Mediendateien und ein neues synthetisches Benutzer-/Pool-/Gerätemanifest. Die Werte sind neu erfunden; es wurde keine Bestandsdatenbank verwendet.

Die WAVs prüfen Transport, Formaterkennung, Kanalzahl, Samplingrate, Dauer und Wiedergabe. Sie enthalten **keine Sprache**. Sie bestehen weder die Spracherkennungs- noch die DSS/DS2-Paritätsprüfung.

`specs/behavior_tests.json` beschreibt 24 Fixturegruppen. Nur die hier ausdrücklich vorhandenen Daten sind vorbereitet. Sprachaufnahmen, echte Gerätevarianten, Office-Vorlagen, Referenzbildschirme und ein eingerichtetes Windows-/AD-Labor müssen durch die jeweiligen Testschritte erzeugt werden. Der Fixturekatalog behauptet nicht deren Existenz.

Die Namen Alpha/Beta/Gamma, Schreib1/Schreib2, AdminLab und StatLab entsprechen der Gruppe F-ID. Das JSON ist ein **Testrezept**, kein Import in die Originalsoftware. Die Referenzbenutzer über deren normale Administration frisch anlegen; der Neubau erhält einen eigenen Test-Seed ausschliesslich für seine neue Datenhaltung. Pool-/Rollenänderungen einzelner Fälle stehen im jeweiligen BT-Fall.

Reproduktion in einen neuen/ leeren Ordner:

```sh
python research_tools/make_synthetic_fixtures.py --output fixtures/new_run --dry-run
python research_tools/make_synthetic_fixtures.py --output fixtures/new_run
```

Die vorhandenen WAV-Prüfsummen und Mediendaten stehen in `generated/synthetic_state.json`.
