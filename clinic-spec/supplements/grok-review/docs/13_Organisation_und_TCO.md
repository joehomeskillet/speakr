# Entscheidungsregister, Datenschutzvorbereitung und TCO
## Keine erfundenen Verantwortlichen
`planning/decision-register.json` enthält Rollen, benötigte Entscheidungen, Fälligkeit relativ zum Projektmeilenstein und erforderliche Evidenz. Namen sind noch nicht vorgegeben. Claude kann die technische Arbeit fortführen, darf aber weder Joël noch eine andere Person ungefragt als klinischen Product Owner oder rechtlich Freigebenden eintragen.

Technische Defaults sind bereits durch V3 gesetzt. Fachliche Entscheidungen werden zum passenden Meilenstein gebündelt vorgelegt, nicht als Einzelrückfrage bei jeder Implementierung. Geheimnisse/Zugangsdaten werden lokal über den vorgesehenen Konfigurationsweg bereitgestellt, nicht im Chat abgefragt.

## Datenschutz-/Zweckunterlage konkret vorbereiten
Vorlage mit Zweck, Nutzerrollen, Eingabe-/Ausgabedaten, Datenflüssen, Speicherorten, Entra-Anmeldemetadaten versus lokalem Audio/Text, Empfängern, Zugriffen, Fehlzuordnungsfolgen, BYOD-Restspuren, Retention/Löschung/Legal Hold, Backupwiederherstellung und verantwortlicher Entscheidung. Die vorgesehenen Schutzmassnahmen mit Tests verknüpfen. Keine definitive rechtliche oder medizinprodukterechtliche Einstufung durch Claude vorgeben. Dieses Addendum macht keine neue Rechtsaussage und ersetzt keine zuständige Bewertung.

## Wirtschaftlichkeit ohne erneute Stackdiskussion
Als gemeinsamer Planungsrahmen dienen **36 Monate** für Bestand und den in V3 gewählten Speakr-Fork. Das ist ein Vergleichsdefault, kein genehmigtes Budget. Gleicher Funktions-/Support-/Verfügbarkeitsumfang; Anfangsinvestitionen und laufende Kosten getrennt. Eine Teilablösung wird separat benannt, statt sie mit vollständiger Ablösung zu vergleichen.

Zu erfassen: Bestandsverträge; Einführung; interne Entwicklung/Teststunden und interner Stundensatz; Herstellerintegration; Geräteadministration inklusive möglicher Rest-SpeechExec-Installation; Hardware; Storage/Backup; laufender Betrieb; Security-/Modell-/Browser-/KIS-Upgrades; Anwenderschulung; Stellvertretung und Exit. Gemeinsame Infrastrukturkosten mit gleicher Zuordnungsregel auf beide Seiten verteilen. Kein Spekulationsaufschlag ohne Annahmenbegründung.

Formel: Einmalige Einführung + 36 × monatliche Kosten + 3 × jährliche Zusatzkosten + separat begründete einmalige Exit-/Migrationskosten. Stundenbasierte Positionen sind Stunden × genehmigter Satz und dürfen nicht nochmals als Pauschale doppelt gezählt werden. Unbekannte Werte bleiben `null` und verhindern einen scheinbar fertigen Gesamtbetrag. CHF 0 ist nur mit ausdrücklich bestätigter Nullkostenannahme zulässig.

Ausfall-/Risikokosten getrennt als Szenario ausweisen, nicht als angeblich gemessene Tatsache. Lizenzpreisfreiheit allein ist keine Einsparrechnung; umgekehrt ist eigener Wartungsaufwand kein Beweis, dass das Projekt unwirtschaftlich ist. Konkretes Ergebnis erst nach realen Eingaben.
