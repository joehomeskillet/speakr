# 02 – Ein Speakr-Fork mit Webclient
## Komponenten
Ein modulares Flask-Fachbackend mit bestehenden Speakr-Modellen, PostgreSQL und vorhandener Jobverarbeitung. Serverrollen: HTTPS-Ingress, Speakr-Webprozess, separater Medien-/Jobworker aus demselben Projekt, lokale Speaches-Instanz, PostgreSQL und gesicherter persistenter Dateispeicher. Keine zweite allgemeine Queue und kein neuer S3-Cluster ohne konkreten Bedarf.

Der Webclient nutzt dieselben vorhandenen Recorder-/Editor-/Player-/Adminbausteine. Neue Clinicmodule liegen unter `src/clinic/`; UI-Erweiterungen folgen dem tatsächlichen Upstreamaufbau. Sourcepfade am gepinnten Commit zunächst prüfen. Kein Backend-/Frontend-Frameworkwechsel.

Der physische Windows-PC erhält ausschliesslich `apps/windows-import/`. Er beobachtet freigegebene Geräte/Ordner und sendet Originale mit Herkunftsdaten. Die gesamte Geräteauswahl, Metadatenkorrektur, Autorklärung und Poolarbeit erfolgt im Web über zentrale Statusdaten. Der Agent benötigt keinen eingeloggten Autor; Maschinenidentität ist kein Sprecherbeweis.

## Vertrauensgrenzen
BYOD und verwaltete Browser erhalten nur freigegebenen internen HTTPS-Zugang. Kein direkter DB-/Storage-/Speacheszugriff. Browser→Speakr-Sitzung→lokaler Audio-/ASR-Adapter. Speacheskeys bleiben serverseitig; keine api_key-Query im Browser. KIS-Einbettung bleibt der bestehende Host, kein zusätzlich installierter Projekt-WebView-Wrapper. Der Host erhält keine generische Native-/Shellbrücke.

Medienparser laufen ohne Internet, mit read-only System, begrenzten CPU/RAM/Timeouts und separaten Berechtigungen. Kontext-/Freigabe-/Identitysecrets gehören nicht in den Medienworker. Objekte werden in allen Routen, Downloads, Audio-Range-Anfragen, Jobs, Suchansichten und Legacy-Endpoints gegen dieselbe Clinicpolicy geprüft.

## Wiederverwendung statt Zweitsystem
User und Recording-IDs bleiben erhalten. Staff-Pools nutzen geeignete vorhandene Gruppen; Autorenressourcenpools erhalten expliziten eigenen Typ/Bezug. Neue Tabellen für Revisionen, Leases, Freigaben, Deliveryledger und Resource-Versionen ergänzen, nicht alte User-/Recordingtabellen duplizieren. Vorhandene ProcessingJob-/JobQueue-Klassen um fehlende sichere Claims/Fencing/Recovery ergänzen. Decoder und ASR als Adapter, nicht neue Codecs oder Inferenzserver schreiben.

## Betrieb und Forkpflege
Dedicated Klinikinstanz, keine Mischung mit privaten Meetingaufnahmen. Featureflags für öffentlichen Share, Chat/Inquire, externe Webhooks und unkontrollierte API-Tokens bleiben aus. Upstreamänderungen bewusst in Staging prüfen: ACL-/Legacyroute-Diff, Aufbewahrung, Text-/Audiorevisionen, Privacy und Identity. Quellstand/Build/Abhängigkeiten und Lizenzhinweise nachvollziehbar halten; keine Patientendaten/Secrets in veröffentlichten Fork-Quellarchiven. Das vollständige Originalartefakt-ZIP ist nicht der zur Veröffentlichung bestimmte Fork.
