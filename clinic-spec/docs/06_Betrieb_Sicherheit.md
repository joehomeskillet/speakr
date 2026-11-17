> V4-Vorrang: Web-only und Import-Agent-Grenze gelten verbindlich. Local-first aus V3.2 bleibt. Neue PAR-Rechte/Policies, WG-Teilkonflikte und Quellenpflege in den V4-Katalogen ergänzen frühere engere Defaults; historische Quellen sind keine Produktfreigabe.

# 06 – Betrieb, Sicherheit, Lieferkette und Lizenz
## Entwicklungsprofile
`dev-mock`: synthetische Daten, Keycloak, Mock-ASR, Mock-KIS, simuliertes Geräteverzeichnis. Ohne Internet nach Bereitstellung der Images. Kein Erfolgsetikett für reale Integration.
`integration-local`: echte lokale Speaches-ASR und dss-codec, nur erlaubte Testdaten. CPU genügt für Smoke, Laufzeit nicht als Klinik-SLO ausgeben.
`production`: Start nur mit produktivem Policyoverlay und nachgewiesenen Gates. Mockauth, Mock-KIS, feste Demo-Secrets, offene Defaultkonten oder ungelockte Artefakte führen zum Abbruch.

## ASR-Defaults
Nur Transkription, keine Übersetzung, keine Zusammenfassung, keine Titelgenerierung mit LLM, keine Chat-/RAG-/Sprecheridentifikation. Sprache Deutsch, deterministic best-effort Parameter (Temperatur 0; keine Zusage mathematischer Deterministik). CPU-Smoke mit kleinem Whisper-Modell; Qualitätsevaluation mit `large-v3` als Kandidat. Modellrepo/-revision, Datei-Hashes, Lizenz, Quantisierung, Decoder, Parameter und Wort-/Segmentzeiten je Lauf erfassen.

NVIDIA/CUDA ist ein optionaler getesteter Beschleunigungspfad von faster-whisper [OSS26]. Keine Behauptung, private Arc-/AMD-Karten seien Klinikressourcen. Fehlende GPU führt zu CPU/Stub, nicht zu Cloud. VAD zunächst aus, bis Tests gegen leise Sprache, Negationen und Pausen Qualität belegen. Abschnitte mit unklarem Inhalt bleiben zur Kontrolle; kein LLM darf medizinische Lücken ergänzen.

## Original-/Fehlerschutz
Angenommene Rohdateien und Quellmetadaten werden getrennt von regenerierbaren Audioderivaten gehalten. Retention, failed-upload cleanup, reprocess, cancel, account-delete und admin-delete müssen Klinikschutz berücksichtigen. Der Originalstore darf von normalen Transkriptionsjobs nicht gelöscht werden. Löschung nur durch separaten berechtigten Lebenszyklusdienst unter Legal-Hold-/Retentionregel. Produktivfristen sind nicht mit Demo-Cleanup gleichzusetzen.

## Audit
Bestehende Auditbasis wiederverwenden, aber Fachübergänge synchron/atomar mit Statusänderung und append-only Berechtigung speichern. Ein best-effort UI-Audit allein genügt nicht für Freigaben oder Exportledger. Technische Logs ohne Audio, Text, Dateinamen, Fallnummern, Token und Keys. Leselogging und Fachaudit getrennt. Backup-/Betriebsadmins haben Restprivilegien; keine absolute Revisionssicherheitsbehauptung.

## Infrastruktur
Serverseitige Datenträgerverschlüsselung; getrennte Secrets; restriktive DB-/Filesystemrechte; TLS; Trusted-Proxy-Regeln; CSP/CSRF; Rate-/Upload-/Ressourcenlimits; keine externen CDN-/Analytics-/Crashupload-/Webhookziele. Egress-Allowlist getrennt: Web nur genehmigter IdP und benötigte interne Dienste; ASR/Medienworker kein Internet; KIS-Adapter nur Zielsystem. Updates/Modelle kontrolliert herunterladen und vor Echtbetrieb intern bereitstellen. Keine pauschale Freigabe aller privaten IPs als vertrauenswürdig.

Compose ist Bereitstellung für Dev/Pilot, keine HA-Garantie. Kein unnötiger Redis/MinIO/OpenSearch-Neuaufbau. Mehrere Webworker erst nach Nachweis konsistenter Rate-Limits/Sessions/Job-Ownersteuerung. Devdefault ein Webprozess und ein Queue-Owner; kein Multiworker-Claim ohne Test.

## Backup und Betriebsübergabe
DB/PITR, Audiooriginale, Versionen, Audit-/Deliveryledger, Konfigurationspins und Schlüsselwiederherstellung gemeinsam testen. `STORED` ist kein standortübergreifendes RPO 0. RPO/RTO pro Ausfallart und Datenklasse im Produktionsoverlay; fehlen sie, kein Echtdatenbetrieb. Nach Restore KIS-Ausgänge abgleichen, bevor neue Lieferungen erlaubt werden. Wiederanwendung freigegebener Löschentscheidungen und Legal Holds berücksichtigen.

Pilotmonitoring: Agentheartbeat, Importfehler, ungeklärte Autor-/Fallzuordnung, Queuealter, ASRfehler, Speicher/Spool, KIS-UNKNOWN, Backupalter und Zertifikatsablauf. Runbooks für jede Klasse, ausführbar durch Stellvertretung. Alt-/Neusystem dürfen nicht gleichzeitig denselben Geräteauftrag importieren/löschen/versenden. Abschaltung erst nach Ende-zu-Ende-Fachabnahme.

## OSS-Lizenzen
Speakr wird unter AGPL-3.0 weiterverwendet [OSS15]. Das Quellcodeangebot an Benutzer der veränderten Netzwerkversion wird als Lieferanforderung eingeplant, einschliesslich passenden Build-/Änderungsstands; der Quellcode muss deshalb nicht automatisch in ein öffentliches GitHub-Repository mit Klinikdaten. Verpflichtungen dürfen aber nicht durch eine zusätzliche pauschale Geheimhaltung der AGPL-Quellen unterlaufen werden. Interne Nutzung ist kein allgemeiner Freibrief, Lizenzpflichten zu ignorieren. Rechtliche Detailprüfung vor Betrieb bleibt erforderlich.

dss-codec und Speaches nennen MIT [OSS21, OSS24]. FFmpeg-Lizenz hängt vom konkreten Build ab; Distributionshinweise, verwendete Codecoptionen und korrespondierende Lizenz-/Quellanforderungen im SBOM erfassen. Modelldateien haben eigene Lizenzen. Keine „alles MIT“-Behauptung. Signierte Agentenpakete, Image-Digests, Lockfiles, Lizenzen und Securityscan gehören zur Abgabe.

## Datenschutz-/Fachfreigabe
V2-Schutzbedarf bleibt bestehen. Diese technische Spezifikation setzt keine rechtlichen Aufbewahrungsfristen, behauptet keine medizinprodukterechtliche Einstufung und genehmigt kein privates Endgerät. Produktionsgate erfordert Datenschutz-/Security-/Fachentscheidungen, Benennung des Service Owners und realen Restore. Entwicklung mit synthetischen Fixtures geht währenddessen weiter.
