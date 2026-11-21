# Anmeldung und Identitätsanbieter
<small>[English](../../admin-guide/sso-setup.md)</small>

Diese Seite beschreibt, wie sich Personen an der Klinikversion anmelden und in welchem Zustand die einzelnen Identitätsanbieter sind. Alles auf dieser Seite gilt, wenn `CLINIC_MODE` auf `1`, `true`, `yes` oder `on` gesetzt ist. Ohne Klinikmodus verhält sich der Fork wie Upstream-Speakr, einschliesslich der OIDC-Einmalanmeldung.

Im Klinikmodus ist die Anmeldung ausschliesslich lokal. Microsoft Entra ID, eine LDAP-Föderation und die Windows-Einmalanmeldung (IWA) sind als serverseitige Profile vorbereitet. Alle drei sind deaktiviert, und keine Einstellung, keine Umgebungsvariable und keine Konfigurationsdatei schaltet sie ein. Die Upstream-OIDC-Einmalanmeldung ist wirkungslos.

## Statusübersicht

| Funktion | Status | Hinweis |
|---|---|---|
| Lokale Anmeldung mit Benutzername oder E-Mail-Adresse und Passwort | Verfügbar | Der einzige funktionierende Anmeldeweg. |
| Upstream-OIDC-Einmalanmeldung (`ENABLE_SSO`, `SSO_*`) | Nur Upstream | Die Routen lehnen im Klinikmodus jede Anmeldung ab. |
| SSO-API-Zugriffstoken und `POST /api/v1/tokens/exchange` | Nur Upstream | Zusammen mit den persönlichen API-Token gesperrt. |
| Microsoft Entra ID | Vorbereitet, deaktiviert | Nur synthetische lokale Tests. Kein realer Mandant, keine klinische Freigabe. Gate G-ENTRA ist offen. |
| LDAP-Föderation | Vorbereitet, deaktiviert | Nur Profilprüfung. Gate G-LDAP ist offen. |
| Windows-Einmalanmeldung (IWA) | Vorbereitet, deaktiviert | Nur Vertragscode. Gate G-IWA ist offen. |
| Verzeichnisabgleich (**Verzeichnis**) | Vorbereitet, deaktiviert | Es ist kein Verzeichnisanbieter registriert. |
| Lokale Windows-Konten eines Arbeitsplatzes auslesen | Offene Lücke | WG-ADM-009. |

Nichts davon ist eine klinische Freigabe. Auch das Gate G-IDENTITY (Sicherheitsabnahme der lokalen Konten, des Bootstraps, der Passwörter, der Sitzungen und der Administration) ist offen. Es sperrt die lokale Anmeldung mit Echtdaten bis zu dieser Abnahme.

## Grundsätze für jeden Anmeldeweg

- **Ein Konto, ein Schlüssel.** Jeder Anmeldeweg führt auf die bestehende interne Benutzer-ID (`user.id`). Diktate, Pools, Fachrechte und Freigaben verweisen auf diese ID; Auditeinträge nennen handelnde Person und Zielkonto über diese ID, nicht über eine E-Mail-Adresse oder ein externes Subject.
- **Keine automatische Verknüpfung über die E-Mail-Adresse.** Eine vorbereitete externe Anmeldung erreicht ein Konto nur über eine ausdrückliche Zuordnung: bei Entra über eine Verknüpfung aus Issuer und Subject, die eine aktive Person mit **Identitätsadministration** beantragt und die betroffene Person bestätigt; bei LDAP über eine Subject-Zuordnung durch eine aktive Person mit **Identitätsadministration** (nur vorbereitet, nur im Prozessspeicher); bei IWA über die Windows-SID in einer Zuordnungsliste der Serverkonfiguration. E-Mail-Adresse, UPN und Anzeigename dienen nie dazu, ein Konto zu finden.
- **Keine automatische Kontoanlage.** Es gibt keine Selbstregistrierung und keine Kontoanlage bei einer ersten externen Anmeldung. Konten legt ein aktives Konto mit **Administrationsrolle** und **Identitätsadministration** an; siehe [Benutzerverwaltung](user-management.md).
- **Rechte kommen aus der Anwendung.** Claims eines Anbieters (Rollen, Gruppen) erteilen nie Rollen, Pools oder Diktatzugriff. Auch Identitätsrollen erteilen keinen Diktatzugriff. Unter **Benutzer und Rollen** > **Fachrechte** hält die Oberfläche fest: **Der Zugriff auf ein konkretes Diktat bleibt zusätzlich an Benutzer, Pools und Objektberechtigungen gebunden. Eine Identitätsrolle gibt keinen Diktatzugriff.**

## Lokale Anmeldung (Verfügbar)

Die Anmeldeseite im Klinikmodus wird immer ohne SSO-Schaltfläche angezeigt, unabhängig von `ENABLE_SSO`. Ihr Seitenbereich **Identität und Zugriff** trägt die Kennzeichnung **Lokal** und die Zeilen **Lokale Anmeldung: Aktiv** und **Einmalanmeldung: Nicht eingerichtet**.

Das Formular nimmt einen Benutzernamen oder eine E-Mail-Adresse sowie das Passwort entgegen. Passt die Eingabe über den Benutzernamen zu einem Konto und über die E-Mail-Adresse zu einem anderen, schlägt die Anmeldung fehl. Eine Anmeldung gelingt nur, wenn alle folgenden Bedingungen erfüllt sind:

- Das Konto hat eine Klinik-Kontorichtlinie. Ein Konto, das über die Upstream-Docker-Variablen `ADMIN_USERNAME`, `ADMIN_EMAIL` und `ADMIN_PASSWORD` angelegt wurde, hat keine, bis es übernommen wird; siehe [Benutzerverwaltung](user-management.md).
- Der Status ist **Aktiv**.
- Ein Passwort ist gespeichert, als bcrypt- oder scrypt-Hash. Andere Hash-Formate stimmen nie überein.
- Die **Anmelderichtlinie** ist **Nur lokal** oder **Lokal und Entra**.
- Für das Konto besteht keine Anmeldesperre, weder für die Clientadresse noch kontoweit; siehe [Benutzerverwaltung](user-management.md#anmeldesperre).
- Es steht keine Passwortänderung aus. Nach **Einmalcode ausstellen** für ein aktives Konto ist keine Anmeldung möglich, auch nicht mit dem bisherigen Passwort, bis der Code eingelöst ist.

Jeder Fehlschlag zeigt dieselbe Meldung, **Anmeldung fehlgeschlagen.**, sodass ein unbekanntes, deaktiviertes oder falsch eingegebenes Konto nicht unterscheidbar ist. Es gibt kein «Angemeldet bleiben»: Eine Kliniksitzung ist nie eine dauerhafte Anmeldung. Die Sitzung ist an die Sicherheitsversion des Kontos gebunden. Das Deaktivieren des Kontos, das Ausstellen eines Einmalcodes zum Zurücksetzen des Passworts, eine Änderung der Anmelderichtlinie und eine Änderung der **Administrationsrolle** oder der **Identitätsadministration** erhöhen diese Version und beenden bestehende Sitzungen.

Eine erfolgreiche Anmeldung schreibt das Auditereignis `auth.login.succeeded` mit `method` `password`, eine fehlgeschlagene Anmeldung schreibt `auth.login.failed`. Die Sperreinstellungen `login_max_failed_attempts` und `login_lockout_minutes` sind unter [Systemeinstellungen](system-settings.md) beschrieben. Die erste Administration (**Administrationsrolle** und **Identitätsadministration**) wird einmalig an der Serverkonsole mit `python scripts/clinic_bootstrap_admin.py --username <benutzername> --email <e-mail-adresse>` angelegt. Das Passwort stammt aus `--password-stdin`, aus der Umgebungsvariablen `CLINIC_BOOTSTRAP_PASSWORD` (oder der mit `--password-env` benannten) oder aus einer verdeckten Eingabeaufforderung. Kontoanlage, Einmalcodes, Deaktivierung und der Schutz des letzten Kontos mit Identitätsadministration sind unter [Benutzerverwaltung](user-management.md) beschrieben.

### Anmelderichtlinien

| Wert | Bezeichnung in der Oberfläche | Lokales Passwort | Entra (vorbereitet) | Windows-Einmalanmeldung (vorbereitet) |
|---|---|---|---|---|
| `local_only` | **Nur lokal** | Erlaubt | Keine Anmeldung; nur der Verknüpfungsablauf | Erlaubt |
| `local_and_entra` | **Lokal und Entra** | Erlaubt | Erlaubt | Erlaubt |
| `entra_only` | **Nur Entra** | Verweigert | Erlaubt | Verweigert |
| `device_only` | **Nur Gerät** | Verweigert | Verweigert | Verweigert |

Neue Konten erhalten **Nur lokal**. Ein Konto ohne Klinik-Kontorichtlinie wird als **Keine Anmelderichtlinie** angezeigt und kann sich nicht anmelden.

Unter **Benutzer und Rollen** > **Konten** führt das Bearbeitungsfeld **Anmelderichtlinie** die Werte **Nur lokal**, **Lokal und Entra** und **Nur Entra** auf, auswählbar ist aber nur **Nur lokal**. Darunter steht der Hinweis **Entra ist vorbereitet, aber noch nicht aktiviert.** Als Änderung der Richtlinie akzeptiert der Server nur den Wechsel auf **Nur Entra**, und nur für ein aktives Konto mit einer aktiven Entra-Verknüpfung. Solange Entra deaktiviert ist, kann keine Verknüpfung bestehen; eine Änderung ist deshalb nicht möglich. **Lokal und Entra** lässt sich über das Web überhaupt nicht setzen. Lehnt der Server eine Änderung ab, antwortet er mit `409` und dem Code `login_policy_not_permitted`; die Oberfläche zeigt **Diese Anmelderichtlinie ist für das Konto nicht verfügbar.**

Der Server setzt ein Konto nie von **Nur Entra** auf eine lokale Richtlinie zurück und verweigert für ein solches Konto einen lokalen Einmalcode (**Für dieses Konto ist kein lokaler Einmalcode möglich.**). Das Deaktivieren eines Anbieters stellt deshalb für ein Konto mit **Nur Entra** nie die Passwortanmeldung wieder her.

## Upstream-OIDC-Einmalanmeldung (Nur Upstream)

Upstream-Speakr meldet Benutzer über einen OIDC-Anbieter an, eingerichtet mit `ENABLE_SSO` und den `SSO_*`-Variablen (Vorlage `config/env.sso.example`). Im Klinikmodus ist dieser Weg wirkungslos:

- `/auth/sso/login`, `/auth/sso/callback` und `/auth/sso/link` leiten mit der Meldung **SSO ist nicht konfiguriert. Wenden Sie sich an den Administrator.** weiter. Das geschieht auch, wenn `ENABLE_SSO=true` und alle `SSO_*`-Variablen gesetzt sind.
- `/auth/sso/unlink` antwortet mit `403` und dem Code `provider_unavailable`.
- Die Bedienelemente der Einmalanmeldung im Bereich **Anmeldung & Sicherheit** der Kontoseite sind ausgeblendet; der Passwortbereich bleibt.

Diese Variablen haben keine Wirkung auf die Klinikanmeldung. Die Upstream-Standardwerte im Code stehen in Klammern: `ENABLE_SSO` (`false`), `SSO_PROVIDER_NAME` (`SSO`), `SSO_CLIENT_ID`, `SSO_CLIENT_SECRET`, `SSO_DISCOVERY_URL`, `SSO_REDIRECT_URI`, `SSO_AUTO_REGISTER` (`true`), `SSO_ALLOWED_DOMAINS`, `SSO_DEFAULT_USERNAME_CLAIM` (`preferred_username`), `SSO_DEFAULT_NAME_CLAIM` (`name`), `SSO_DISABLE_PASSWORD_LOGIN` (`false`) und `SSO_REQUIRE_VERIFIED_EMAIL` (`true`).

Die Klinikvorlage `clinic-spec/config/upstream.env.example` setzt `ENABLE_SSO=false`, `SSO_AUTO_REGISTER=false`, `SSO_DISABLE_PASSWORD_LOGIN=false` und `SSO_REQUIRE_VERIFIED_EMAIL=true`. Sie hält fest, dass das Umstellen dieser Schalter die klinische Authentisierung nicht vervollständigt.

Folgende Upstream-Funktionen gibt es in der Klinikversion deshalb nicht: die automatische Registrierung bei der ersten SSO-Anmeldung und die Positivliste der E-Mail-Domänen; die automatische Verknüpfung eines bestehenden Kontos über eine gleiche E-Mail-Adresse; den Passwortzugang für Administratorinnen und Administratoren bei deaktivierter Passwortanmeldung; «Angemeldet bleiben» für SSO-Anmeldungen; das Verknüpfen und Lösen der Einmalanmeldung auf der Kontoseite.

### SSO-API-Zugriffstoken (Nur Upstream)

Upstream-Speakr erlaubt anderen Anwendungen, die API mit dem OIDC-Zugriffstoken einer Person aufzurufen oder es gegen ein gespeichertes API-Token zu tauschen. Im Klinikmodus ist das gesperrt:

- Die Funktion `personal_api_tokens` ist in `clinic-spec/config/dev.defaults.json` auf `false` gesetzt. Die Token-Routen und `POST /api/v1/tokens/exchange` antworten mit `403` und `clinic_feature_disabled`.
- Eine Anfrage, die sich mit einem API-Token anmeldet und keine Browsersitzung hat, wird auf jeder nicht öffentlichen Route mit `403` `clinic_feature_disabled` abgewiesen.
- Die Einstellung `sso_api_tokens_enabled` liest sich immer als `false`. Das Speichern eines anderen Werts antwortet mit `403` `clinic_policy_ceiling`. Ein abweichender gespeicherter Wert wird beim Start als `clinic policy overrides stored setting sso_api_tokens_enabled=<stored> (effective: False)` protokolliert.
- `sso_api_allowed_clients`, `sso_api_exchange_clients`, `sso_api_scopes`, `sso_api_client_names`, `sso_api_client_scopes` und die zugehörigen `SSO_API_*`-Variablen haben keine Wirkung.

Upstream-Speakr-Dokumentation (Verhalten ohne Klinikmodus, keine Anleitung für die Klinikversion): [SSO Setup (OIDC)](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/sso-setup.md) (English)

## Microsoft Entra ID (Vorbereitet, deaktiviert)

### Was vorhanden ist

- **Adapter.** Autorisierungscode-Ablauf mit PKCE (`S256`), State und Nonce sowie exakter Prüfung der Weiterleitungs-URI. Das ID-Token wird nur mit `RS256` angenommen; der Schlüssel wird über `kid` aus dem Schlüsselsatz des Anbieters gewählt. Geprüft werden `iss`, `tid`, `aud`, `exp`, `nbf` und `iat` mit 60 s Zeitabweichung (höchstens 300 s), `nonce` und `sub`. Das Subject ist der Claim `sub`. E-Mail, UPN und Name werden nicht beachtet.
- **Serverseitiger Transport.** Zeitlimits von 3,05 s (Verbindungsaufbau) und 5 s (Lesen), Antworten bis 128 KiB, keine Weiterleitungen, keine Proxys aus der Umgebung. Das Discovery-Dokument muss genau die konfigurierten Endpunkte nennen. Die Schlüssel kommen von `<authority>/discovery/v2.0/keys`, mit höchstens einer Schlüsselaktualisierung pro Austausch. Das Client-Secret wird erst beim Codeaustausch aus einer Datei gelesen.
- **Anbieter-Routen und ein ausdrücklicher Verknüpfungsablauf** (siehe unten).
- **Nachweis.** Die Tests verwenden ausschliesslich synthetische Schlüssel, Token, Benutzer und HTTP-Fixtures. Ihre Docstrings halten fest, dass sie keinen Nachweis einer realen Entra-Anmeldung darstellen; die zugehörigen Abnahmeeinträge bleiben `not_run`.

### Warum es sich nicht einschalten lässt

- Die Anwendung bindet die Anbieter-Routen mit einer deaktivierten Standardkonfiguration für Entra und einem Codeaustausch ein, der immer fehlschlägt. Keine Administrationseinstellung, keine Umgebungsvariable und keine Konfigurationsdatei zur Laufzeit aktiviert Entra. Der Transport wird nirgends in der Anwendung instanziiert.
- Die Anwendung liest weder `clinic-spec/config/auth.hybrid.example.json` noch `auth.local.example.json` noch den Block `identity` in `dev.defaults.json`. Änderungen daran bewirken nichts.
- Auch nach einer Einbindung gilt das Profil nur als aktiv, wenn `mode` den Wert `hybrid` hat, `entra_enabled` `true` ist und `activation.real_provider_approved` `true` ist. Andernfalls bricht jeder Einstiegspunkt ab, bevor eine DNS-Abfrage, eine HTTP-Anfrage, ein Schlüsselabruf oder ein Datenbankzugriff stattfindet.
- Die Aktivierung erfordert eine Codeänderung und das Gate G-ENTRA: realer Mandant (Tenant) und App-Registrierung, ausdrückliche Verknüpfungen, Conditional Access und MFA, WebView-SSO sowie ein gemessener Lebenszyklus- und Austrittsweg. Diese Seite nennt keine Aktivierungsschritte, weil es keine gibt.

Es gibt keinen Abgleich des Kontolebenszyklus mit Entra. Eine Sperre, die nur in Entra gesetzt wird, beendet keine Speakr-Sitzungen. Das Deaktivieren des Kontos in Speakr (**Deaktivieren**) erhöht seine Sicherheitsversion und beendet seine Sitzungen.

### Wie eine Anmeldung einem Konto zugeordnet würde

- Eine Entra-Anmeldung erreicht ein Konto nur über eine aktive Verknüpfung (Issuer und Subject), die der Verknüpfungsablauf angelegt hat. Der Mandant muss übereinstimmen. Enthält die Verknüpfung eine Entra-Objekt-ID, wird sie bei jeder Anmeldung geprüft.
- Das Konto muss **Aktiv** sein und **Nur Entra** oder **Lokal und Entra** haben.
- Unter den aktiven Verknüpfungen gibt es je Issuer und Subject eine, je Konto eine und je Mandant und Objekt-ID eine. Widerrufene Verknüpfungen bleiben als Verlauf erhalten. Eine neue Verknüpfung erfordert einen neuen administrativen Akt.
- Eine Ablehnung wird nur mit einem festen Code protokolliert: `clinic entra: login denied (<code>)` mit `no_link`, `tenant_mismatch`, `object_mismatch`, `account_not_active` oder `policy_forbids_entra`. Token, Code, State, Nonce, Verifier oder Secret werden nie protokolliert.

### Verknüpfungsablauf (vorbereitet, nur API)

Keine Seite der Administrationsoberfläche startet oder zeigt eine Verknüpfung. Die Kontenliste zeigt keinen Entra-Verknüpfungsstatus, obwohl die Spezifikation einen verlangt. Die vorbereitete API arbeitet so:

1. Eine Person mit **Identitätsadministration** und Status **Aktiv** sendet `POST /api/clinic/v1/identity/users/<id>/entra-link-requests` mit `object_id`, `expected_version` und dem eigenen Passwort sowie dem Header `X-CSRF-Token`. Die Antwort ist `201` mit einer `request_id`. Das Zielkonto muss **Aktiv** sein und **Nur lokal** oder **Lokal und Entra** haben. Das Ereignis `auth.sso.link_requested` wird geschrieben.
2. Die Zielperson sendet `POST /api/clinic/v1/identity/providers/entra/link/start` mit der `request_id` und dem eigenen Passwort und erhält eine `authorization_url`. Der Auftrag ist einmalig verwendbar, läuft nach 600 s ab und ist an Hashwerte des States und eines Werts der Browsersitzung gebunden.
3. Der Rückruf `GET /api/clinic/v1/identity/providers/entra/callback` prüft, dass der Claim `oid` der freigegebenen `object_id` entspricht, dass das Zielkonto weiterhin **Aktiv** ist und **Nur lokal** oder **Lokal und Entra** hat und dass seine Kontoversion seit dem Antrag unverändert ist. Danach bindet er Issuer und Subject in einer Transaktion. Das Ereignis `auth.sso.linked` wird geschrieben.

Nach einer bestätigten Verknüpfung wird aus einem Konto mit **Nur lokal** ein Konto mit **Nur Entra**. Sein lokaler Passwort-Hash wird entfernt, offene Einmalcodes werden widerrufen und seine Sicherheitsversion wird erhöht; seine Sitzungen enden. Das letzte lokal anmeldefähige Konto mit Identitätsadministration ist geschützt: Die Verknüpfung wird mit einem Konflikt abgewiesen.

Das Lösen erfolgt mit `POST /api/clinic/v1/identity/users/<id>/entra-links/<link_id>/unlink` und `expected_version` (Version der Verknüpfung). Es funktioniert auch bei deaktiviertem Anbieter und erfordert eine Sitzung, `X-CSRF-Token` und **Identitätsadministration**. Es wird mit `409` abgewiesen, solange das Konto **Aktiv** ist und **Nur Entra** hat, weil das Konto sonst jeden Anmeldeweg verlöre. Der Verknüpfungseintrag bleibt als widerrufen erhalten, die Sicherheitsversion wird erhöht und `auth.sso.unlinked` wird geschrieben.

Kein Webweg verknüpft oder löst die Entra-Identität eines anderen Kontos der Administration (ein Konto mit **Administrationsrolle** oder **Identitätsadministration**). Eine solche Anfrage erhält ein allgemeines `403`.

### Vorlagenschlüssel (werden nicht geladen)

`clinic-spec/config/auth.hybrid.example.json` zeigt die Form, die ein späteres Entra-Profil hätte. Die Datei ist nur eine Vorlage und wird zur Laufzeit nicht geladen. Auszug:

```json
{
  "mode": "hybrid",
  "entra_enabled": true,
  "entra": {
    "tenant_id": null,
    "client_id": null,
    "secret_file": null,
    "allowed_issuer": null,
    "discovery_url": null,
    "redirect_uri": null,
    "scopes": ["openid", "profile", "email"],
    "pkce": true
  },
  "activation": {
    "real_provider_approved": false,
    "approval_evidence": null,
    "notes": "Template only: null provider settings fail validation; do not deploy until G-ENTRA is approved."
  }
}
```

Der vorbereitete Code würde die Schlüssel so prüfen:

| Schlüssel | Regel |
|---|---|
| `tenant_id` | Eine GUID. `common`, `organizations` und `consumers` werden abgewiesen. |
| `client_id` | Pflichtangabe. |
| `secret_file` | Pfad einer Datei mit dem Client-Secret. Das Secret wird nie aus einer Umgebungsvariablen übernommen. |
| `allowed_issuer` | Genau `https://<host>/<tenant>/v2.0`, mit `login.microsoftonline.com`, `login.microsoftonline.us` oder `login.partner.microsoftonline.cn` als Host. |
| `discovery_url` | Genau der Issuer, gefolgt von `/.well-known/openid-configuration`. |
| `redirect_uri` | `https`, ohne Fragment. |
| `scopes` | Muss `openid` enthalten; erlaubt sind nur `openid`, `profile` und `email`. |
| `pkce` | Muss `true` sein. |

Dieselbe Datei und `auth.local.example.json` halten den Identitätsvertrag als Spezifikationswerte fest: `user_key` `existing_user.id`, `self_registration` `false`, `jit_provisioning` `false`, `email_auto_link` `false`, `authorization_source` `application`, `default_account_policy` `local_only`, `default_after_entra_link` `entra_only`, `remove_local_hash_after_entra_only_link` `true` und `allow_implicit_password_fallback` `false`.

## LDAP-Föderation (Vorbereitet, deaktiviert)

Die Anwendung bindet sich nie an ein Verzeichnis und speichert nie ein Verzeichnispasswort. Eine spätere Föderation wäre nach einer Freigabe von G-LDAP ein Identitätsanbieter vor LDAPS. Der Code prüft nur ein deaktiviertes Profil:

- Die Profilprüfung meldet das Profil immer als deaktiviert. Sie lehnt mit `ldap_capability_unavailable` ab, wenn `enabled` oder `ldap_enabled` `true` ist, wenn der Anbieter nicht `ldaps_federation` ist oder wenn `server_side` `false` ist.
- Schlüssel für Verzeichnispasswörter wie `bind_password` oder `ad_password` werden mit `end_user_password_rejected` abgewiesen. `claim_role_sync` muss `false` sein. Die Lebenszyklusquelle ist `application`, die Subject-Attribute sind `issuer` und `subject`.
- Subject-Verknüpfungen werden nur im Prozessspeicher gehalten. Ein Subject, das `@` enthält, wird als instabil abgewiesen. LDAP-Claims erteilen nie Rollen oder Pools.
- Die Anwendung verwendet dieses Profil nirgends. Die Verzeichnis-Routen verwenden nur seinen Fehlertyp.

## Windows-Einmalanmeldung (IWA) (Vorbereitet, deaktiviert)

Die Einstellungen der Windows-Einmalanmeldung bestehen nur im Code und sind standardmässig deaktiviert. Die Anwendung übergibt weder eine IWA-Konfiguration noch einen Speicher gegen Wiederholungen; `POST /api/clinic/v1/identity/providers/iwa/login` antwortet deshalb mit `422`. Der vorbereitete Vertrag:

- Er vertraut nur einer Zusicherung, die eine Server-Middleware im WSGI-Schlüssel `clinic.iwa.assertion` ablegt. Anfragen mit den Headern `X-IWA-*`, `X-Remote-User` oder `X-Forwarded-User` werden abgewiesen.
- Die Zusicherung braucht eine AD-SID, gilt 60 s und wird gegen Wiederholung einmalig beansprucht.
- Die SID führt nur über eine exakte, serverseitig verwaltete Liste auf eine `user.id`. Das Konto muss **Aktiv** sein und **Nur lokal** oder **Lokal und Entra** haben.

Eine solche Middleware gibt es im Repository nicht. Die Tests verwenden eine synthetische Zusicherung und sind kein Nachweis für Negotiate oder AD.

## Anbieterstatus und API-Antworten

`GET /api/clinic/v1/identity/providers` meldet die drei Anbieter. In der aktuellen Version sind alle drei deaktiviert:

```json
{"providers": [{"provider": "entra", "enabled": false},
               {"provider": "iwa", "enabled": false},
               {"provider": "ldap", "enabled": false}]}
```

Der Wert für `ldap` ist im Code fest auf `false` gesetzt.

Eine Route eines deaktivierten Anbieters antwortet mit `422` als `application/problem+json`, mit dem Titel **Anbieter nicht aktiviert** und dem Code `CAPABILITY_UNAVAILABLE`. Das geschieht vor jedem Datenbankzugriff, jedem Schreiben eines Verknüpfungsauftrags und jedem ausgehenden Aufruf. Ohne Klinikmodus antworten diese Routen mit `404`.

| Route | Zugriff |
|---|---|
| `GET /api/clinic/v1/identity/providers` | Öffentlich (läuft, bevor eine Sitzung besteht) |
| `POST .../providers/entra/login`, `GET .../providers/entra/callback`, `POST .../providers/iwa/login` | Öffentlich; die Handler prüfen CSRF, State und Browserbindung selbst |
| `POST .../providers/entra/link/start` | Angemeldete Sitzung, eigenes Passwort, `X-CSRF-Token` |
| `POST /api/clinic/v1/identity/users/<id>/entra-link-requests` | Angemeldete Sitzung, **Identitätsadministration**, eigenes Passwort, `X-CSRF-Token` |
| `POST /api/clinic/v1/identity/users/<id>/entra-links/<link_id>/unlink` | Angemeldete Sitzung, **Identitätsadministration**, `X-CSRF-Token` |

Weitere Antworten dieser Routen: `401` `UNAUTHORIZED` ohne angemeldete Sitzung. Ein falsches eigenes Passwort ergibt `403` `FORBIDDEN` ohne Protokollzeile. `409` `REVISION_CONFLICT` bei veralteter Version, bei einem Verknüpfungskonflikt, bei einem Lösen, das die Anmeldung unmöglich machen würde, und beim Schutz des letzten Kontos mit Identitätsadministration. Andere Ablehnungen ergeben ein allgemeines `403` `FORBIDDEN` und die Protokollzeile `clinic idp: refused (<code>)`. Ein Anfrageinhalt mit unbekannten Schlüsseln ergibt `INVALID_PAYLOAD`, ein unerwarteter Fehler `DEPENDENCY_UNAVAILABLE`.

## Verzeichnisabgleich (Vorbereitet, deaktiviert)

Die Seite **Verwaltung** > **Konten und Pools** > **Verzeichnis** (`/admin#/verzeichnis`) zeigt die Überschrift **Verzeichnisabgleich**, den Text **Der Verzeichnisabgleich ist nicht aktiviert.** und die Schaltfläche **Lokale Konten verwalten**. Ein Konto mit **Administrationsrolle**, aber ohne **Identitätsadministration**, sieht **Der Verzeichnisabgleich erfordert Rechte zur Identitätsverwaltung.** Konten ohne **Administrationsrolle** sehen den Eintrag **Verzeichnis** nicht.

Die Verzeichnis-Routen erfordern **Administrationsrolle** und **Identitätsadministration**, ein aktives Konto, keine ausstehende Passwortänderung und eine passende Kliniksitzung. Ein `POST` braucht zusätzlich den Header `X-CSRF-Token`. Ein Anfrageinhalt, der eine handelnde Person, einen Benutzer oder eine Rolle nennt, wird abgewiesen.

Es ist kein Verzeichnisanbieter registriert. `GET /api/clinic/v1/directory/capability` antwortet:

```json
{"directory_available": false, "local_accounts": true,
 "local_windows_accounts": "unavailable", "gap": "WG-ADM-009"}
```

Vorschau (`POST /api/clinic/v1/directory/preview`) und Anwendung (`POST /api/clinic/v1/directory/apply`) antworten mit `503` `ldap_capability_unavailable` und dem Titel **Verzeichnis nicht verfügbar**.

Der vorbereitete Abgleich, nur mit synthetischen Verzeichnisständen getestet, arbeitet so:

- Er vergleicht nur die unveränderliche Objekt-ID eines Verzeichniseintrags mit der Objekt-ID einer aktiven Entra-Verknüpfung. E-Mail-Adresse, Kontoname und Anzeigename werden nie verwendet.
- Die möglichen Aktionen sind `create_pending_user`, `update_display_name` und `deactivate`. Es wird nichts gelöscht. Mehrdeutige Einträge und Einträge ohne Objekt-ID erhalten keine Aktion.
- Die Anwendung ist an Version und Hashwert der Vorschau gebunden; andernfalls antwortet sie mit `409` `stale_version` oder `stale_state`. `create_pending_user` wird mit `422` `explicit_link_required` abgewiesen. Eine Identitätsadministration deaktiviert darüber nie das eigene Konto und ändert nie dessen Pools. Ausgewählte Änderungen laufen über dieselben Konto- und Pooldienste wie manuelle Änderungen.
- Die Prüfansicht erscheint nur, wenn ein Anbieter besteht. Zu ihren Bezeichnungen gehören **Vorschau laden**, **Neue Identitäten**, **Änderungen**, **Zu deaktivierende Identitäten**, **Poolzuordnungen**, **Auswahl prüfen**, **Änderungen bestätigen**, **Verbindlich anwenden** und **Es wird nichts gelöscht, Konten werden nur deaktiviert.**

Für das Auslesen der lokalen Windows-Konten eines Arbeitsplatzes gibt es keinen zulässigen Browser- oder Serverweg. Die Web-Paritätslücke WG-ADM-009 bleibt offen; der zulässige Webweg ist ein serverseitiger Verzeichnisabgleich zusammen mit lokalen Anwendungskonten.

## Auditereignisse und Protokollzeilen

| Ereignis | Geschrieben, wenn | Detailschlüssel |
|---|---|---|
| `auth.login.succeeded` | eine Anmeldung gelingt | `method` (`password`; vorbereitet: `entra`, `iwa`), `remember` (im Klinikmodus immer `false`) |
| `auth.login.failed` | eine Anmeldung, eine Codeeinlösung oder die Prüfung des aktuellen Passworts fehlschlägt | `reason` (`bad_password`, `locked`, `bad_recovery_code`, `bad_current_password`), `method` (`password`, `recovery`, `password_change`) |
| `auth.login.locked` | die Grenze falscher Passwörter erreicht ist, für eine Clientadresse oder kontoweit | `minutes` |
| `auth.password.changed` | ein Einmalcode eingelöst wird oder die Person ihr Passwort ändert | keine |
| `auth.sso.link_requested` | die Identitätsadministration eine Entra-Verknüpfung beantragt (vorbereitet) | keine |
| `auth.sso.linked` | eine Entra-Verknüpfung bestätigt wird (vorbereitet) | keine |
| `auth.sso.unlinked` | eine Entra-Verknüpfung widerrufen wird | keine |

Die Upstream-SSO-Routen, die ebenfalls `auth.sso.linked` und `auth.sso.unlinked` schreiben, sind im Klinikmodus nicht erreichbar. Der vollständige Katalog steht unter [Auditprotokoll](audit-log.md).

Der Identitätscode protokolliert nur feste Codes, zum Beispiel `clinic entra: link <id> for user <id> created by <id>`, `clinic entra: link <id> revoked by <id>`, `clinic entra: login denied (<code>)` und `clinic idp: refused (<code>)`.

## Aktivierungs-Gates

| Gate | Fehlender Nachweis | Status |
|---|---|---|
| G-IDENTITY | Abnahme der lokalen Konten, des Bootstraps, der Passwörter, der Sitzungen und der Administration; Entscheid zu MFA und Schutzbedarf; vertrauenswürdige Ingress-Netze | Offen |
| G-ENTRA | Realer Mandant und App-Registrierung, ausdrückliche Verknüpfungen, Conditional Access und MFA, WebView-SSO, gemessener Lebenszyklus- und Austrittsweg | Offen |
| G-LDAP | Freigegebener Identitätsanbieter und LDAPS-Vertrag, TLS, Zuordnung und Lebenszyklusprüfung | Offen |
| G-IWA | Verifizierte Windows-Einmalanmeldung und bestätigter AD-Abgleich, getrennt von Entra | Offen |

Mocks, Fixtures und synthetische Tests sind kein Nachweis für Entra, LDAP oder die Windows-Einmalanmeldung.
