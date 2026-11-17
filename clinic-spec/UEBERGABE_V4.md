# Übergabe V4 – Speakr-Fork als reiner Webclient
Die neueste Vorgabe ist integriert: keine native Diktatanwendung; der lokale Agent holt nur Diktatdateien ab. Alle 101 Originalfunktionen bleiben Pflicht, wobei 13 Funktionen mindestens eine explizit offene native/Web-Teilklausel enthalten. Eine Alternative ist noch kein bestandener 1:1-Nachweis.

## Umfang
177 Anforderungen, 252 Produkttests (alle not_run), 27 Umsetzungstasks, 21 typisierte neue Paritätskommandos. Speakr bleibt gepinnte Basis, lokale Benutzer zuerst, Entra vorbereitet/deaktiviert, LDAP/IWA separat, interne BYOD-Aufnahme und inesKIS-Verträge erhalten.

## Originalkatalog nach Bereich
| Bereich | Funktionen |
|---|---:|
| Identitäten, Pools und Berechtigungen | 11 |
| Zentrale Vorgaben und Metadaten | 5 |
| Audiorecorder und Sitzungen | 8 |
| Live-Diktat und Entwürfe | 8 |
| Geräte- und Dateiimporte | 10 |
| Arbeitsliste, Workflow und Freigabe | 14 |
| Abschrift und synchronisierte Korrektur | 10 |
| Wortschatz und Befehle | 8 |
| Lokale Erkennung und Profilfunktionen | 7 |
| Dokumente und allgemeine Desktopausgabe | 10 |
| Hardwarebedienung, Dienste und Statistik | 10 |

## Dateien
Vollständiges Archiv mit 24 bytegenauen Gesprächsoriginalen; darunter DnsComfort_Claude_Gesamtpaket(1).zip mit allen 1467 Einträgen. Einzeldateien in verschachtelten Archiven bleiben ungekürzt erhalten. Aktueller Arbeitsauftrag getrennt unter 01_Claude_Bauauftrag. Kein Originalprogramm ausgeführt, keine Altdatenbank importiert, keine dekompilierte Implementierung in den Fork übernommen.

## Tests dieser Lieferung
Spezifikations-/Integritätsprüfung, acht negative Validatorfälle und fünf JSON-Schemafälle. Das ist kein Speakr-Build, keine Windows-/Browser-/ASR-/Geräte-/KIS-Prüfung und keine Klinikfreigabe. Die genauen Resultate stehen in den Prüfberichten.

## Start
Im aktuellen Bauauftrag START_CLAUDE.md verwenden; bei bestehendem Fork deltaorientiert integrieren und Fortschritt erhalten. Originalartefaktordner nicht als Coding-Kontext öffnen oder ins Repository aufnehmen. Für neue Repositories optional Initialize-SuedhangWorkspace.ps1, standardmässig Dry-Run; Paketvalidator read-only.
