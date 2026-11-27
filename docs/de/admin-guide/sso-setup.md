# Lokale Anmeldung und Identitätsanbieter

[English](../../admin-guide/sso-setup.md)

Bei aktiviertem `CLINIC_MODE` ist die normale Web-Anmeldung mit lokalen Konten der Anmeldeweg. Die Anmeldeseite zeigt **Lokale Anmeldung: Aktiv** und **Einmalanmeldung: Nicht eingerichtet**. Microsoft Entra ID kann später folgen; es funktioniert heute nicht. Für Entra und LDAP-Föderation besteht vorbereiteter serverseitiger Code; der Anwendungsstart lässt ihn aber deaktiviert, und keine Verwaltungseinstellung und kein Umgebungsschalter vervollständigt die Anbindung.

Windows-Einmalanmeldung wird in diesem Fork nicht angeboten: keine integrierte Windows-Authentifizierung (IWA), kein Negotiate und kein Kerberos. Der Browser meldet niemanden anhand des Windows-Kontos eines Arbeitsplatzes an.

## Lokaler Zugriff

Verwenden Sie Benutzername oder E-Mail-Adresse (Anmeldename des Kontos; es wird nie eine E-Mail versendet) und das lokale Passwort. Die Anmeldung benötigt eine aktive Klinik-Kontorichtlinie, einen gespeicherten unterstützten Passwort-Hash und die Richtlinie **Nur lokal** oder **Lokal und Entra**. Ein ausstehender Passwortwechsel oder eine Anmeldesperre verhindert den Zugriff. **Keine Anmelderichtlinie** bedeutet, dass ein Bestandskonto keine Klinik-Kontorichtlinie besitzt; es bezeichnet keinen externen Anbieter.

Die Klinikanmeldung verwendet kein **Angemeldet bleiben**. Sitzungen sind an die Sicherheitsversion des Kontos gebunden. Deaktivierung, das Ausstellen eines Rücksetzcodes und relevante Rollen- oder Anmelderichtlinienänderungen machen bestehende Sitzungen ungültig. Sensible Identitätsverwaltung benötigt zusätzlich eine lokale Anmeldung innerhalb der letzten fünf Minuten.

Neue Konten verwenden **Nur lokal**. Entra-Richtlinien im Kontoeditor aktivieren Entra nicht. **Nur Gerät** und **Nur Entra** erlauben keine lokale Passwortanmeldung. Das Deaktivieren eines externen Anbieters stellt den lokalen Passwortzugang eines Entra-only-Kontos nicht stillschweigend wieder her.

Der Klinik-Fork bietet keine Selbstregistrierung, E-Mail-Verifizierung oder Passwortwiederherstellung per E-Mail. Die Identitätsadministration legt Konten an und stellt lokale Einmalcodes aus. Der Anmeldeendpunkt unterstützt das Einlösen von Codes, die Anmeldeseite besitzt dafür derzeit aber kein Formular. [Benutzerverwaltung](user-management.md) beschreibt Bootstrap, Aktivierung, Entsperrung und Kontoübernahme.

## Kontoidentität und Berechtigungen

- Jeder Anmeldeweg muss zur vorhandenen internen `user.id` führen; Diktateigentümerschaft und Zuweisungen bleiben an diese ID gebunden.
- Es gibt keine Verknüpfung anhand übereinstimmender E-Mail-Adresse, UPN oder Anzeigenamen und keine Kontoanlage bei der ersten externen Anmeldung.
- Externe Rollen oder Gruppen-Claims erteilen nicht automatisch Klinikrollen, Poolmitgliedschaften oder Diktatrechte.
- Identitätsadministration und Fachrechte bleiben getrennt. Ein Identitätsadministrationskennzeichen allein erteilt keinen Diktatzugriff.

## Ursprüngliche Anmeldung und Tokens

Die ursprüngliche OIDC-Anmeldung und die Bedienelemente zur Kontoverknüpfung sind im Klinikmodus inaktiv. `ENABLE_SSO` und `SSO_*` aktivieren die Klinik-Identitätsadapter nicht. Der Klinik-Fork bietet auch keine allgemeine API v1 oder SSO-Tokenaustausch für beliebige Anwendungen; Tokens bleiben dem Importagenten vorbehalten. Die Systemobergrenzen halten `sso_api_tokens_enabled` und Tokenzugriff über URL-Abfrageparameter deaktiviert.

## Vorbereiteter Entra-Adapter

Der Entra-Code bereitet Authorization-Code-Anmeldung mit PKCE, State, Nonce, Issuer-/Mandantenprüfung und ausdrücklicher Verknüpfung zu einem vorhandenen Klinikkonto vor. Die Anwendung bindet ihn mit deaktivierter `EntraConfig` und verweigernder Austauschfunktion ein. Die Beispieldateien `auth.hybrid.example.json` und `auth.local.example.json` werden von dieser Anwendungseinbindung nicht geladen. Ihre Bearbeitung aktiviert keine Anmeldung.

Der vorbereitete Verknüpfungsablauf benötigt eine ausdrückliche Anfrage der Identitätsadministration und Bestätigung durch die Zielperson. Er ordnet Konten nicht über die E-Mail-Adresse zu. Ein bestätigter Übergang von ausschliesslich lokal zu ausschliesslich Entra erhält die interne Benutzer-ID, entfernt den lokalen Passwort-Hash, macht Wiederherstellungscodes ungültig und beendet Sitzungen. Er schützt die letzte lokal nutzbare Identitätsadministration. Dies sind vorbereitete Verhaltensweisen, keine Anleitung zur Aktivierung eines echten Verzeichnisses.

Die laufende Anwendungseinbindung hat keine Entra-Lebenszyklussynchronisierung. Das Sperren einer Person nur in Entra ersetzt nicht die Deaktivierung ihres Klinikkontos. Synthetische Anbietertests beweisen weder Mandantenanmeldung noch Conditional Access, MFA oder Offboarding gegen einen echten Anbieter.

## LDAP und Windows-Anmeldung

Das LDAP-Modul prüft ein deaktiviertes serverseitiges Föderationsprofil. Die Anwendung erstellt keine Verzeichnisverbindung und verarbeitet keine Verzeichnispasswörter von Endbenutzern. Das Profil bietet keine echte LDAP-Anmeldung oder Rollensynchronisierung.

Windows-Einmalanmeldung wird in diesem Fork nicht angeboten (siehe oben). Vom Client gelieferte Identitätsheader können keine Anmeldung erzeugen. Kein Browservorgang liest die lokalen Windows-Konten eines Arbeitsplatzes; WG-ADM-009 bleibt offen.

## Anbieter- und Verzeichnisstatus

`GET /api/clinic/v1/identity/providers` meldet in der aktuellen Anwendungseinbindung `entra`, `iwa` und `ldap` mit `enabled: false`. Der Eintrag `iwa` ist ein veralteter Platzhalter, der deaktiviert bleibt; er ist keine Anmeldemethode dieses Forks. Ein deaktivierter Anbietereinstieg antwortet mit `422 CAPABILITY_UNAVAILABLE` und **Anbieter nicht aktiviert**. Dies sind Klinik-Identitätsrouten, kein allgemeiner API-Tokenzugriff.

Unter **Verwaltung → Konten und Pools → Verzeichnis** (`/admin#/verzeichnis`) sieht die Identitätsadministration, dass der Verzeichnisabgleich nicht aktiviert ist. **Lokale Konten verwalten** führt zur Kontoverwaltung zurück. Die Verzeichniskapazität meldet `directory_available: false`, `local_accounts: true`, `local_windows_accounts: "unavailable"` und die Lücke `WG-ADM-009`. Kein Verzeichnisanbieter ist registriert; Vorschau und Anwendung antworten mit `503 ldap_capability_unavailable`.

Der vorbereitete Verzeichnisabgleich verwendet unveränderliche externe Objekt-IDs und eine geprüfte Vorschau; er gleicht nicht anhand von Namen oder E-Mail-Adressen ab. Er ist kein nachgewiesener Verzeichnisimport oder Offboarding-Prozess. Behalten Sie die lokale Kontoverwaltung als Betriebsweg bei.

## Nachweisgrenzen

Die Aktivierung eines Anbieters benötigt vollständige Einbindung und echte Abnahmenachweise für den gewählten Mandanten oder das Verzeichnis. Lokale Fixtures und synthetische Schlüssel belegen keine Entra- oder LDAP-Abnahme. Die Sicherheitsabnahme lokaler Konten bleibt ebenfalls eine getrennte Bereitstellungsvoraussetzung; dieses Handbuch beschreibt Codeverhalten, keine Produktionsfreigabe.

Für lokale Anmeldung, Fehlversuche, Sperren und Passwortwechsel bestehen Audit-Ereignistypen. Vorbereitete Anbieterverknüpfungen haben getrennte SSO-Ereignistypen. [Auditprotokoll](audit-log.md) beschreibt Konfiguration und Abdeckung, [Benutzerverwaltung](user-management.md) die aktuellen Kontoverfahren.
