> V4-Vorrang: Web-only und Import-Agent-Grenze gelten verbindlich. Local-first aus V3.2 bleibt. Neue PAR-Rechte/Policies, WG-Teilkonflikte und Quellenpflege in den V4-Katalogen ergänzen frühere engere Defaults; historische Quellen sind keine Produktfreigabe.

# 05 – Identität, LDAP und inesKIS-WebView
## Lokales Login zuerst, Entra vorbereitet
Normativ gilt `docs/10_Local_Entra.md`. Vorhandene Speakr-Benutzerverwaltung, lokale Authentisierung und Sitzungsmechanik wiederverwenden. Der erste Betriebsmodus ist `local`: kein Entra, kein Keycloak, kein LDAP und kein SMTP als Voraussetzung.

`hybrid` ergänzt später tenantgebundenes Entra-OIDC mit vorhandenem Authlib-Code. Eine interne `user.id` bleibt über sämtliche Anmeldewege unverändert. Externe Identität ist validierter Issuer + Subject; Tenant/Object kann für explizite Vorprovisionierung dienen. Keine automatische Verknüpfung über E-Mail, keine JIT-Registrierung und kein universeller Admin-Passwortbypass.

Rollen und Pools werden initial in der Anwendung verwaltet. Pro Konto gilt local_only, entra_only, local_and_entra oder device_only. Passwortreset ändert diese Policy nicht. Lokale Administratoren benötigen eigene Verwaltungsrechte, nicht automatisch klinischen Lesezugriff. Die LDAP-Fähigkeit bleibt als separates späteres Keycloak-/LDAPS-Profil bestehen [OSS05, OSS27, OSS31–38].

Entra-Adapter, sichere Linkaufträge und negative OIDC-Tests werden jetzt implementiert, jedoch erst nach realer Providerabnahme aktiviert. Fehlende Tenantdaten blockieren weder Entwicklung noch lokale Anmeldung.

## inesKIS: drei Adapter, ein kanonischer Vertrag
1. `MockKisAdapter`: stateful synthetisches Register, kann Speicherung mit anschliessendem Timeout simulieren. Nur Dev/Test.
2. `DisabledKisAdapter`: produktive Voreinstellung ohne Herstellernachweis; meldet `CAPABILITY_UNAVAILABLE`, tut keinen Netzwerkzugriff.
3. `InesKisAdapter`: erst gegen dokumentierten freigegebenen Vertrag implementieren. Kein erfundenes FHIR-/REST-/HL7-Endpoint.

Kanonische Methoden: `capabilities()`, `resolve_context(grant, actor)`, `authorize_case(context, actor, action)`, `deliver(frozen_payload, delivery_key)`, `lookup(delivery_key)`, `create_amendment(parent_receipt, frozen_payload)`. Das sind **eigene Schnittstellen**, keine Zusage entsprechender ines-Methodennamen.

## WebView und Kontext
Edge WebView bleibt Ziel. Keine eigene neue Desktop-WebView-App im Standardscope. Reale Hostversion/Origin, Benutzerprofile, externe Navigation, Mic/Clipboard/Downloads und SSO im Herstellerhost abnehmen. Bei fehlendem Hostzugang denselben Webscreen mit Mockkontext im Browser testen; das zählt nur als UI-/Contracttest.

Vorhandener Kontext wird über vertrauenswürdigen serverseitigen Austausch in einen opaken Einmal-Grant umgewandelt. Grant TTL 60 Sekunden als Devdefault, an vorhandene interne user.id, Sitzung, Mandant, Zweck und Zielinstanz gebunden; externe KIS-Benutzerreferenz über freigegebenes Mapping auflösen, nicht mit einem OIDC-Subject gleichsetzen. Grant ist kein Fallrecht. API prüft patient/case-Zusammengehörigkeit, klinische Einschränkungen und aktuelle Rolle. Code nicht in normalen Logs/Referrern. Lieferantenprotokoll kann anders aussehen; Adapter muss dieselben Sicherheitsinvarianten erreichen.

Diktat A bleibt am ursprünglichen Kontext A, wenn im KIS B geöffnet wird. Ein bewusster Wechsel erzeugt neue Kontextrevision und invalidiert Freigaben. Kein Fensterüberschrift-/DOM-/Clipboard-Parsen. Offlinediktate von Philips dürfen nicht nachträglich den offenen Fall des Docking-PCs erben.

## Ausgabevertrag
Export erstellt eingefrorenen Payload mit Diktat-ID, Audio-/Text-/Kontextversion, SHA-256, Dokumenttyp, Empfängerfall, Autor, Reviewer, Freigabe und Modus. Transaktionale Delivery-ID vor Netzwerkaufruf. Idempotency-Key je eingefrorenem Export und Ziel.

`CONFIRMED` nur mit hinreichend eindeutigem Herstellernachweis, z.B. externe Dokument-ID plus passendem Status/Versionsbezug. HTTP-202 heisst allein nur angenommen. Timeout nach möglichem Effekt => UNKNOWN. Nur bestätigtes Nichtvorhandensein oder ein tatsächlich nachgewiesener Hersteller-Idempotenzvertrag erlaubt erneute Neuanlage. Das gilt auch nach Restore. Bei UNKNOWN kein automatischer Zweitexport. Bewusste Notfallausnahme erfordert berechtigte, protokollierte Entscheidung.

## Sechs gebündelte Herstellerlieferungen statt Dauerfragen
Host-/Runtimevertrag; SSO-/Benutzerwechselvertrag; Patienten-/Fall-/Rechtevertrag; Dokument-/Entwurfs-/Signaturvertrag; Quittungs-/Lookup-/Nachtragsvertrag; Testsystem-/Lizenz-/Supportvertrag. Claude baut die Adaptergrenzen und Negativtests bereits vorher und markiert reale Integration als blockiert.

## Sitzungszustand ausdrücklich prüfen
Vorhandene OIDC-/Flask-Login-Mechanik wiederverwenden, aber nicht behaupten, jede Upstreamsession sei schon serverseitig. OAuth-/Refresh-Tokens dürfen im Klinikprofil nicht im Browser-Webstorage oder in nur signierten, lesbaren Cookies liegen. Im Checkout Token-/Sessionablage prüfen und nötigenfalls mit einer etablierten, zur Basis passenden serverseitigen Sessionbibliothek über PostgreSQL ergänzen. Cookie enthält nur einen opaken, widerrufbaren Sitzungsbezug; keine selbst entwickelte Kryptografie. S00 dokumentiert Bibliothek/Pin, S03 prüft Logout, Widerruf, Sessionrotation und mehrere Instanzen.
