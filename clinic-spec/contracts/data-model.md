# Datenmodell – Erweiterung der vorhandenen Speakr-Entitäten
**Eigene neue Tabellen, keine Beschreibung einer fertigen Speakr-Funktion.** Tabellenname/Typen werden in additive Migrationen umgesetzt. Bestehende User/Recording/Group-IDs und ORM weiterverwenden. Nicht automatisch die ganze Upstreammigration auf ein neues Framework umbauen.

## Entitäten
| Entität | Schlüssel/Bezug | Zweck |
|---|---|---|
| `clinic_external_identity` | UUID; unique `(issuer, subject)`; FK user.id | Stabile Anmeldung ohne E-Maillinking. |
| `clinic_role_assignment` | Nutzer, Rolle, Scope, Gültigkeit | Fachliche Rolle, nicht pauschaler Adminzugriff. |
| `clinic_pool` | UUID; unique group_id -> bestehende group | Staffpool auf vorhandener Mitgliedschaft. |
| `clinic_author_routing` | Autor, Standardpool, gültig ab/bis, erlaubte Overrides | Routing ohne Poolleserecht. |
| `clinic_agent` | UUID, mTLS-Keyreferenz, Zustand, erlaubte Geräte | Maschinenidentität getrennt vom Diktiergerät. |
| `clinic_device_assignment` | Gerätekennung, Autor/Shared, Gültigkeit, Metadatenpolicy | Historische, geprüfte Autorregel. |
| `clinic_ingest` / `clinic_ingest_chunk` | Upload-UUID; unique `(principal, request_id)` bzw. `(upload_id, sequence)` | Empfang vor sicherer Autorauflösung; Bytes können gesichert sein, ohne einen falschen Recording-Owner anzulegen. |
| `clinic_dictation` | UUID; recording_id unique/not null; Autor, Pool, Versionszähler | Erst nach Autorauflösung an vorhandenes Recording binden. |
| `clinic_original_version` | UUID; dictation_id; Hash; Originreferenz; unveränderliche Storage-ID | Originalbytes und Revisionen. |
| `clinic_context_revision` | UUID; dictation_id; Source/Tenant; Fall-/Patientreferenz; Nachweis; Rechteprüfung | Unveränderlicher geprüfter Kontext. |
| `clinic_transcript_revision` | UUID; dictation_id; original_version_id; Text; Text-Hash; Art ASR/HUMAN; Modellnachweis | ASR und Korrektur überschreiben einander nicht. |
| `clinic_review_lease` | dictation_id unique; holder; expiry; generation | Sperre/Fencing plus Optimistic Lock. |
| `clinic_release` | UUID; dictation_id; Audio-/Text-/Kontextrevision; Klasse/Modus; actor; Hash | Freigabe exakt eines überprüften Zustands. |
| `clinic_delivery` | UUID; release_id; Ziel; Key; Payloadhash; Status; externe Quittung | Unveränderlicher Versandauftrag/Abgleichledger. |
| `clinic_audit_event` | UUID; actor/action/object/result; Zeit; correlation | Appseitig append-only, keine Inhalte in Standardlogs. |
| `clinic_outbox` | UUID; intent; aggregate_id; version; Status | Dauerhafter Side-effect-Intent, Dispatch in die vorhandene Queue. |

## Zwingende DB-Garantien
Zusammengesetzte UNIQUE/FKs über `(dictation_id, version_id)` erzwingen Zugehörigkeit von Original, Text, Kontext und Freigabe. Ein vorhandener Fremdschlüssel auf nur `transcript_revision.id` genügt nicht. Export verweist auf die konkrete Freigabe und deren eingefrorenen Hash/Modus. Bei neuen Revisionen wird aktiver Freigabezeiger atomar invalidiert; bestehende Freigabeevidenz bleibt unverändert.

UTC/timestamptz für Klinikdaten; UI Europe/Zurich. UUIDs für neue öffentliche Klinikobjekte; keine Migration aller Upstream-Integer-IDs nötig. Enum-/Checkguards für fachliche Status. Unique `(delivery_key,target)`; derselbe Key mit anderem Payload => Konflikt. JSON-Felder nur für Metadaten, nicht als Ersatz aller referenziellen Constraints.

## Transaktionen
Original schreiben + flush/commitfähige Storagegarantie; dann DB-Metadaten/Status/Audit/Outbox in einer Transaktion; erst danach `STORED`. Crash vor DB-Commit: kein erfolgreiches Ack, verwaiste Bytes mit Schonfrist abgleichen. Keine falsche atomare DB+Filesystem-Garantie. Recovery darf eine vorhandene gültige Originalreferenz nie als Staging löschen.

Textsave/Freigabe prüfen If-Match, aktive Leasegeneration, aktuelle Berechtigung und Referenzen in derselben Transaktion. Gespeicherte Transkriptquelle stammt aus dem bestehenden ProcessingJob-Ergebnis, aber ein späteres Resultat darf keine korrigierte Version ersetzen. Ein verspäteter Worker benötigt passenden Attempt-/Fencingnachweis.

## Idempotenz und Audit
Idempotency-Key ist scoped nach Akteur, Operation und Objekt. Transportretry != Aufnahmeidentität. Beide getrennt persistieren. Ein Audiohash allein ist kein globaler fachlicher Deduplikationsschlüssel. Kein bestätigter KIS-Ausgang aus einem lokalen „request sent“ ableiten.

App-DB-Rollen dürfen Audit nicht updaten/löschen; Migrations-/Betriebsrollen getrennt. Löschledger/Legal Hold erst mit freigegebenen Policies aktivieren. Root-/DB-Admin-Restprivilegien sind organisatorisch zu kontrollieren, nicht durch ein Hashfeld wegdefiniert.


## Identity-Erweiterung V3.2
Normativ: `identity-contract.json`. Vorhandene User.id bleibt fachlicher Primärbezug. Additive Accountpolicy-/External-Identity-/Linkauftrag-/Recovery- und Session-Version-Felder erlauben den späteren Providerwechsel ohne Neuanlage von Autoren. Bestehende SSO-only-Konten beim Upgrade nicht still auf local_only umstellen. Passwortspalte vor modernen Hashes auf Text erweitern; alle direkten Altpfade auf zentralen Verifier umstellen.

Providerverknüpfung ist eine zusätzliche Authentisierungsrelation, nicht eine zweite Person. `(issuer, subject)` eindeutig; Entra `(tenant_id, object_id)` konsistent und pro Person eindeutig. Replay/konkurrierende Linkaufträge dürfen nicht zwei Users binden. `security_version`/Kontostatus werden für alle Provider geprüft. Initial `local`, Entra aus. Keine Pflicht-Keycloak-Migration und keine Email-basierte Datenübernahme.


KIS-Akteurszuordnung ist providerunabhängig: `clinic_external_actor_mapping(source_system, tenant_scope, external_actor_id, user_id, verified_by, verified_at, status)`. Externe KIS-Referenzen werden nicht als Entra-Subject interpretiert. Context-Grants binden interne user.id und Sitzung; keine künstliche OIDC-Identität für lokale Konten nötig.

## V4-Paritätserweiterung
Normativ ergänzend: `docs/16_Datenvertraege_und_Defaults.md` und `parity-domain-contract.json`. Autorenressourcenpools, flexible Empfänger, optionale Abnahme, Audioeditrevisionen und Outputversuche ergänzen die obige V3.2-Skizze; ein bisheriges einzelnes pool_id ist eine kompatible Projektion, kein Verbot zusätzlicher expliziter Scopes. Keine native Clientdatenbank.
