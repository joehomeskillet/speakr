> V4-Vorrang: Web-only und Import-Agent-Grenze gelten verbindlich. Local-first aus V3.2 bleibt. Neue PAR-Rechte/Policies, WG-Teilkonflikte und Quellenpflege in den V4-Katalogen ergänzen frühere engere Defaults; historische Quellen sind keine Produktfreigabe.

# 09 – Quellen und Evidenz

Prüfdatum 17.11.2026. Primärquellen, selektive Codeinspektion. Nicht alle Online-Dokumentationen sind Release-spezifisch; der Code am gepinnten Commit entscheidet. Keine dieser Quellen ist eine lokale Abnahme.

## OSS01 – Speakr Repository
https://github.com/murtaza-nasir/speakr

Flask/SQLAlchemy, Vue/Tailwind, PostgreSQL, AGPL; Kandidat, kein Klinikprodukt.

## OSS02 – Speakr v0.11.1 Release
https://github.com/murtaza-nasir/speakr/releases/tag/v0.11.1

Am 17.11.2026 sichtbare Release-Baseline; Logging-Korrekturen. Keine eigene Laufzeitprüfung.

## OSS03 – Speakr konkreter Commit
https://github.com/murtaza-nasir/speakr/commit/48d3018fb7d80b77f897bedd86bcc89b5034d1f5

Commit-Ziel des sichtbaren Release-Links.

## OSS04 – Speakr Installation / PostgreSQL
https://murtaza-nasir.github.io/speakr/getting-started/installation/

PostgreSQL über SQLALCHEMY_DATABASE_URI dokumentiert.

## OSS05 – Speakr SSO
https://murtaza-nasir.github.io/speakr/admin-guide/sso-setup/

OIDC/Entra/Keycloak; dokumentiertes E-Mail-Linking ist für Klinikmodus zu ersetzen.

## OSS06 – Speakr Recording Sessions
https://murtaza-nasir.github.io/speakr/admin-guide/recording-sessions/

Vorhandene serverseitige Aufnahme-/Chunk-Sessions, nicht pauschal BYOD-konform.

## OSS07 – Speakr Privacy
https://murtaza-nasir.github.io/speakr/admin-guide/privacy/

Browserpersistenz, Audit-/Logging-Grenzen und nicht behauptete Compliance.

## OSS08 – Speakr Gruppen
https://murtaza-nasir.github.io/speakr/admin-guide/group-management/

Gruppenfreigaben sind keine klinischen Schreibpools.

## OSS09 – Speakr API
https://murtaza-nasir.github.io/speakr/user-guide/api-reference/

Vorhandene Aufnahmen-/Transkript-API; klinische Kommandos sind Erweiterungen.

## OSS10 – Speakr Queue
https://murtaza-nasir.github.io/speakr/advanced/job-queue-system/

Persistente Jobbasis vorhanden; Doku und gepinnter Code getrennt bewerten.

## OSS11 – Speakr App am Tag
https://raw.githubusercontent.com/murtaza-nasir/speakr/v0.11.1/src/app.py

Registrierung von Blueprints, bestehende Owner-/Share-ACL, db, services, init_db.

## OSS12 – Speakr Queue am Tag
https://raw.githubusercontent.com/murtaza-nasir/speakr/v0.11.1/src/services/job_queue.py

Dauerhafte ProcessingJob-Queue; keine zweite allgemeine Queue entwickeln.

## OSS13 – Speakr Gruppenmodell am Tag
https://raw.githubusercontent.com/murtaza-nasir/speakr/v0.11.1/src/models/organization.py

Group und GroupMembership wiederverwenden; Klinikrollen separat.

## OSS14 – Speakr Dockerfile am Tag
https://raw.githubusercontent.com/murtaza-nasir/speakr/v0.11.1/Dockerfile

Python-3.11-Baseline, Lockfiles, Offline-Assets; Bestand erhalten, Securityscan zusätzlich.

## OSS15 – Speakr Lizenz am Tag
https://raw.githubusercontent.com/murtaza-nasir/speakr/v0.11.1/LICENSE

AGPL-3.0-Lizenztext; Quellcodeangebot und Hinweise erhalten.

## OSS16 – OpenTranscribe Repository
https://github.com/attevon-llc/OpenTranscribe

Alternative; Organisations-/Teamverwaltung und bestimmte Org-Funktionen als managed-only beschrieben.

## OSS17 – OpenTranscribe Backend
https://github.com/attevon-llc/OpenTranscribe/blob/master/backend/app/README.md

FastAPI, verschiedene Auth-Verfahren einschliesslich LDAP/OIDC; keine Annahme klinischer Schreibpools.

## OSS18 – OpenTranscribe FAQ
https://docs.opentranscribe.app/docs/faq/

AGPL, PostgreSQL/Redis/MinIO/OpenSearch; breiterer Betriebsstack.

## OSS19 – Scriberr
https://github.com/rishikanthc/Scriberr

Offline-Transkriptionsapp, Go und SQLite; kein geprüfter passender Klinikworkflow.

## OSS20 – OpenScribe
https://github.com/sammargolis/OpenScribe

Medizinischer Scribe; Standard-Mischworkflow sendet Transkript an externes LLM, nicht ungeprüft einsetzen.

## OSS21 – dss-codec
https://github.com/hirparak/dss-codec

MIT, Rust DSS/DS2->WAV, Passwortunterstützung; keine garantierte EOL-/Philips-Fachsemantik.

## OSS22 – dss-codec Releases
https://github.com/hirparak/dss-codec/releases

Bei Prüfung keine Releases; Commit beim ersten Bootstrap auflösen und festschreiben.

## OSS23 – dss-codec README
https://raw.githubusercontent.com/hirparak/dss-codec/master/README.md

CLI und DSS_CODEC_PASSWORD dokumentiert; kein eigener Codec nötig.

## OSS24 – Speaches
https://github.com/speaches-ai/speaches

MIT, lokale OpenAI-kompatible ASR-API mit faster-whisper, CPU/GPU.

## OSS25 – Speaches Releases
https://github.com/speaches-ai/speaches/releases

Versionen vorhanden; Lock-Schritt muss tatsächlichen Commit und Image-Digest erfassen.

## OSS26 – faster-whisper
https://github.com/SYSTRAN/faster-whisper

CPU- und NVIDIA/CUDA-Ausführung dokumentiert; Hardware/Qualität lokal benchmarken.

## OSS27 – Keycloak Administration
https://www.keycloak.org/docs/latest/server_admin/index.html

OIDC und LDAP/AD-Föderation; kein LDAP-Passwortcode im Fachprodukt.

## OSS28 – .NET Lifecycle
https://learn.microsoft.com/en-us/lifecycle/products/microsoft-net-and-net-core

.NET 10 unterstützt bis 14.11.2028; .NET 8 seit 10.11.2026 ausser Support.

## OSS29 – Speakr Auth-Routen am Tag
https://raw.githubusercontent.com/murtaza-nasir/speakr/v0.11.1/src/api/auth.py

Vorhandenes src.auth.sso und Authlib-Integration; Protokollcode weiterverwenden.

## OSS30 – Speakr Konnektoren
https://murtaza-nasir.github.io/speakr/advanced/custom-connectors/

Vorhandene Transkriptions-Konnektoren; Speaches-Anbindung ist lokal zu testen.


# Ergänzende Identity-Quellen V3.2

## OSS31 – Speakr lokale Benutzerverwaltung
https://murtaza-nasir.github.io/speakr/admin-guide/user-management/

Dokumentierte Benutzeranlage und Verwaltung; kein Nachweis aller geforderten klinischen Kontrollen.

## OSS32 – Speakr User-Modell am festgelegten Commit
https://raw.githubusercontent.com/murtaza-nasir/speakr/48d3018fb7d80b77f897bedd86bcc89b5034d1f5/src/models/user.py

Vorhandene Integer-User-ID, Passwort-/SSO-Felder; Passwortspalte deklariert String(60).

## OSS33 – Speakr Auth-Routen am festgelegten Commit
https://raw.githubusercontent.com/murtaza-nasir/speakr/48d3018fb7d80b77f897bedd86bcc89b5034d1f5/src/api/auth.py

Lokale Auth-/Reset-/Änderungspfade und vorhandene Bibliotheken; gezielte Härtung statt zweiter Auth-Engine.

## OSS34 – Werkzeug Security Helpers
https://werkzeug.palletsprojects.com/en/stable/utils/

Hash-/Verify-Funktionen und scrypt-Unterstützung.

## OSS35 – OWASP Authentication Cheat Sheet
https://cheatsheetseries.owasp.org/cheatsheets/Authentication_Cheat_Sheet.html

Passwortlänge, Throttling, Reauth und sichere föderierte Verknüpfung.

## OSS36 – OWASP Password Storage Cheat Sheet
https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html

Etablierte Password-Hash-Verfahren und Parametergrundlagen.

## OSS37 – OWASP Forgot Password Cheat Sheet
https://cheatsheetseries.owasp.org/cheatsheets/Forgot_Password_Cheat_Sheet.html

Einmaligkeit, Ablauf, sichere Tokens und Sitzungswiderruf.

## OSS38 – Microsoft Entra ID token claims
https://learn.microsoft.com/en-us/entra/identity-platform/id-token-claims-reference

Issuer/Audience/Tenant-Prüfung; E-Mail/UPN sind keine stabilen autoritativen Identitätsschlüssel.
