# 03 – Fachregeln für echte Funktionsparität
## Benutzer, Pools und Berechtigungen
Autoren und Schreibkräfte sind Rollen derselben stabilen User-ID; eine Person kann beide besitzen. Autorenpools teilen Wörter, Befehle, Vorlagen und Profilauswahl. Schreibkraftpools sind Empfänger-/Arbeitsgruppen. Gleichnamige Pools dürfen keine Rechte vermischen. Mehrfachmitgliedschaften, aktiver Ressourcenpool und zeitliche Gültigkeit sind explizit.

Default Autorensicht own. Administrativ erlaubte Sicht author_pool/all wird nur unter denselben Fall-/Mandantenrestriktionen angewandt. Staff-Scopes können einzelne Autoren, Autorenpools und all mit expliziten deny-Ausnahmen verwenden. Aktionsrechte für Löschen, Archivieren, Umbenennen, Priorität, Audioexport, Reassignment und Reopen sind separat. Autor-Defaultpool ist kein Leserecht. Technische Benutzerverwaltung gewährt keine klinischen Inhaltsrechte.

Routing unterstützt all_eligible, einzelne Person, staff_pool und self_correction. Es gibt pro Auftrag einen konsistenten Bearbeitungszustand und eine exklusive Lease, nicht unkontrolliert mehrere Abschriften. Fachliche Routingrelationen und Historie erhalten; mehrere ausgewählte Exportziele sind unabhängig vom Empfängerrouting.

## Zustandsachsen
Eingang receiving/stored/invalid/quarantined; Einreichung draft/completion_unknown/submitted/withdrawn; Zuordnung author_unresolved/case_unresolved/verified; ASR pending/running/succeeded/failed/skipped; Arbeit queued/in_review/corrected/acceptance_required/accepted/completed/reopened; Delivery not_requested/pending/confirmed/unknown/failed; Retention active/due/held/deleted. UIstatus ist eine nachvollziehbare Projektion, nicht ein freier Text ohne Guards.

EOL-Filter beim Geräteimport konfigurierbar, initial an. Eine bewusste freigegebene Autoimportpolicy darf stabile nicht-EOL-Dateien eines bekannten Autors zulassen, aber niemals Dateizeit als EOL-Nachweis ausgeben. Unbekannte Autorenschaft oder widersprüchliche Quelle führt in Klärung. Quelle wird nicht bei Kopierbeginn gelöscht.

## Korrektur, Abnahme und Fertigstellen
Korrektur speichert eine neue Text-/Audiobindung. Gleichzeitige Saves benötigen If-Match und gültige Leasegeneration. Optionale Zusatzabnahme mit Autor-/Autorenpoolrechten und Ausnahmen entspricht PAR-WF-009/010. Auch ohne Zusatzabnahme wird ungeprüfter KI-Text nicht automatisch final. Rejection erhält Begründung und zuständigen Bearbeiter. Fertigstellen und Ausgabe sind getrennte Seiteneffekte; Abschluss ohne Export bleibt möglich.

Ein ausdrücklich gewählter Arbeitsstand darf unter Policy exportiert/kopiert werden, aber nicht als medizinisch final signiert gelten. Finale klinische Ausgabe verlangt gültigen Kontext und passende aktuelle Freigabe. KIS bekommt den freigegebenen Herstellerstatus, initial Entwurf; keine künstliche elektronische Signatur. Mehrere gewählte Ausgabeziele protokollieren Einzelstatus. Ein UNKNOWN-Ziel erlaubt keinen blinden Wiederholungsversand.

## Änderungen und Schutz
Originalbytes bleiben unverändert; Insert/Overwrite/Trim erzeugt Audioeditrevision. Text-/Audio-/Falländerung invalidiert aktive Freigaben. Reopen fertiger Diktate ist berechtigt und erzeugt nachvollziehbare Revision; bereits bestätigte KIS-Dokumente nur per freigegebenem Nachtrag korrigieren. Geschützte Diktate benötigen zusätzliche Entsperrprüfung, niemals statt ACL. Passwort/Gerätekey nicht im Log oder Prompt. Kein Passwortschutz aus selbst entwickelter Kryptografie.

## Konfiguration und Listen
Zentral zwingende Policy gewinnt vor Pooldefault und persönlicher Auswahl; nicht fixierte Einstellungen bleiben persönlich. Bis 20 gespeicherte Filter, Multi-Gruppierung, Layout, Metadatensuche und XML-Spalten als kontrollierte Projektionen. XML ohne DTD/XXE, RegEx mit Längen-/Zeitlimits, Vorschau und Versionsnachweis. Keine Regexzuordnung von Patienten-/Integrations-IDs. Benachrichtigungen ohne Patientendaten im Browser-/OS-Pushtext. Optionale Nutzungsstatistik separat vom Sicherheits-/Freigabeaudit.
