> V4-Vorrang: Web-only und Import-Agent-Grenze gelten verbindlich. Local-first aus V3.2 bleibt. Neue PAR-Rechte/Policies, WG-Teilkonflikte und Quellenpflege in den V4-Katalogen ergänzen frühere engere Defaults; historische Quellen sind keine Produktfreigabe.

# 10 – Lokale Benutzerverwaltung zuerst, Entra vorbereitet
**Version 3.2 · 17.11.2026 · Verbindlicher Entwicklungsauftrag; keine Produktivfreigabe.**

## 1. Änderung und Vorrang
Neue Benutzeranforderung: Die Plattform besitzt eine **eigene lokale Benutzerverwaltung und eine vorbereitete Entra-Anbindung**. Der erste ausgelieferte Betriebsmodus verwendet ausschliesslich lokale Anwender. Entra wird bereits implementiert und mit isolierten Testidentitäten geprüft, bleibt aber deaktiviert, bis ein realer Tenant eingerichtet und abgenommen ist. Es ist kein reiner UI-Platzhalter.

Diese Entscheidung ersetzt nur frühere OIDC-only-/Entra-first-/Keycloak-Pflichtvorgaben. Speakr-Fork, Philips 9600/9620, interne BYOD-Nutzung, Schreibpools, lokale ASR, Freigaben, beide Ausgabekanäle, inesKIS via Edge WebView, Sicherheitsinvarianten und sämtliche nicht betroffenen V3-/Grok-Anforderungen bleiben erhalten. Groks Originalreview ist historische Eingabe, kein vorrangiger Ausführungsauftrag.

**Reihenfolge bei Konflikten:** neuester expliziter Benutzerauftrag → dieses Identity-Kapitel und seine aktualisierten Verträge/Kataloge → übrige V3-Sicherheitsregeln → übrige V3-Defaults → additive Grok-Vorgaben → historische V2-Unterlagen. Lokale Anmeldung ist jetzt ein regulärer, ausdrücklich erlaubter Anmeldeweg; ein ungeprüfter Passwortfallback für SSO-exklusive Konten bleibt verboten.

## 2. Betriebsmodi
| Eigenschaft | Erstes Release: `local` | Später: `hybrid` |
|---|---|---|
| Anmeldeseite | E-Mail/Loginname und lokales Passwort | Lokale Anmeldung plus „Mit Microsoft Entra anmelden“ |
| Benutzerverwaltung | In derselben Speakr-Instanz | Dieselbe Verwaltung und dieselben internen Benutzer-IDs |
| Entra-Adapter | Vorhanden, deaktiviert, isoliert getestet | Aktiv für freigegebenen Tenant und explizit verknüpfte Konten |
| Entra-Client-ID/Secret | Nicht erforderlich | Lokal aus freigegebener Konfiguration/Secret-Referenz |
| Keycloak/LDAP | Kein benötigter Dienst | Eigenständiges späteres LDAP-Föderationsprofil; keine Pflicht für Entra |
| SMTP | Keine Startvoraussetzung | Optional, kein notwendiger Bestandteil des SSO-Wechsels |
| Rollen/Poolzuordnung | Anwendung ist führend | Weiterhin Anwendung als Standard; kein automatischer Claim-Import von Fachrechten |

`local` muss auch bei blockiertem Internet und ohne installierten IdP vollständig starten und alle lokalen Fachabläufe erlauben. Es gibt weder SSO-Autoumleitung noch Discovery-, JWKS-, Token-, Graph- oder Telemetrieaufrufe zum IdP. Deaktivierte SSO-Start-/Callbackpfade scheitern kontrolliert, ohne Loginzustand oder Verknüpfungen zu verändern. Login darf nicht auf Entra-Healthchecks warten.

`hybrid` benötigt konsistente Konfiguration. Ein fehlender/ungültiger Secret-Verweis verhindert die Aktivierung dieses Profils; die bereits lauffähige lokale Konfiguration wird nicht überschrieben. Ein Laufzeitausfall von Entra schaltet niemals Passwortrechte für Entra-only-Konten frei. Bereits ausdrücklich lokale Konten bleiben unabhängig nutzbar.

Es wird initial kein dritter globaler Entra-only-Modus gebaut. Je Benutzer kann Entra-only bereits innerhalb von `hybrid` erzwungen werden. Ein optionales LDAP-Profil verwendet weiterhin einen OIDC-IdP mit LDAPS; keine Speicherung von AD-Passwörtern in der Fachanwendung. Zwei externe Issuer gleichzeitig sind kein Erfordernis dieses Releases.

## 3. Vorhandene OSS-Funktionen nutzen
Speakr dokumentiert lokale Benutzeranlage und SSO. Am festgelegten Commit besitzt `User` bereits eine interne Integer-ID, Benutzername, E-Mail, Passwortfeld und SSO-Felder. Die Auth-Routen verwenden Flask-Login, Flask-WTF, Rate-Limiting sowie vorhandene Hash-/Resetdienste [OSS31–33]. Diese Komponenten und die Adminoberfläche werden angepasst, nicht durch eine zweite Benutzerverwaltung ersetzt.

Der konkrete Stand enthält ein Passwortfeld mit deklarierter Länge 60. Moderne Hashformate dürfen dort nicht abgeschnitten werden. Vor Aktivierung neuer Passwortschreibpfade wird dieses Feld per getesteter additiver Migration auf einen ausreichend grossen Typ, vorzugsweise `Text`, erweitert. Die vorhandenen direkten Login-/Passwort-/Admin-/Resetpfade müssen denselben zentralen Prüfservice verwenden [OSS32, OSS33].

Eigene Ergänzungen beschränken sich auf Konto-/Providerpolicy, abgesicherte Wiederherstellung, klinische Rechte, Identitätsverknüpfung und fehlende Verwaltungsfunktionen. Kein neuer OAuth-Server, keine eigene Passwort-Kryptografie, kein Pflicht-Keycloak und keine neue allgemeine Nutzer-API neben einem unkontrolliert weiterlaufenden Altpfad.

## 4. Ein Benutzer, mehrere mögliche Anmeldewege
**Fachliche Referenz bleibt die vorhandene `user.id`.** Diktatautor, Schreibpool-Routing, Gerätezuordnung, Review, Freigaben und Audit referenzieren diese ID – niemals E-Mail, Benutzername, Entra-Subject oder Passwortkonto als fachlichen Primärschlüssel.

| Kontopolicy | Lokales Passwort | Entra | Zweck |
|---|---|---|---|
| `local_only` | Erlaubt, wenn aktiv und eingerichtet | Kein regulärer Login; nur autorisierter Linkvorgang | Standard für neu angelegte lokale Anwender |
| `entra_only` | Verboten, auch bei vorhandenem altem Hash | Erlaubt, wenn Provider aktiv und Identität verifiziert | Default nach erfolgreich bestätigter Entra-Verknüpfung |
| `local_and_entra` | Explizit erlaubt | Explizit erlaubt | Nur bewusste, auditierte Ausnahme, kein automatischer Fallback |
| `device_only` | Verboten | Verboten | Diktatautor ohne interaktive Anmeldung |

`account_status=disabled` sperrt alle interaktiven Wege und widerruft vorhandene Sitzungen. Diktate, Freigaben und Auditgeschichte bleiben erhalten. Neue Geräteimporte eines deaktivierten Autors dürfen gesichert werden, müssen aber in einen berechtigten Klärzustand gehen; weder still löschen noch einer anderen Person zuweisen. `device_only` ist nicht mit deaktiviert gleichzusetzen.

Eine vorhandene SSO-Verknüpfung ist keine zusätzliche Fachberechtigung. Rollen und Staff-Pools bleiben serverseitig geprüft. Ein Autor erhält durch seinen Standardpool weiterhin kein Leserecht auf fremde Pooldiktate. Konto-/Technikadministratoren erhalten nicht automatisch Audio- oder Transkriptzugriff.

## 5. Lokale Benutzerverwaltung im ersten Release
Die vorhandene Verwaltungsoberfläche erhält eine kompakte deutsche Ansicht „Benutzer & Zugriff“ mit Suche, Filtern und folgenden Angaben: Anzeigename, Benutzername, E-Mail/Loginname, Status, Anmeldepolicy, fachliche Rollen, Standard-Schreibpool, Staff-Pools, Geräteautorbezug, letzte Anmeldung und Entra-Linkstatus.

Pflichtaktionen: Benutzer anlegen; Profil bearbeiten; aktivieren/deaktivieren; Rollen und erlaubte Pools verwalten; lokalen Zugang initialisieren; Wiederherstellung auslösen; Login-Ratelimit gezielt entsperren; alle Sitzungen widerrufen. Für Entra-only wird „Lokales Passwort zurücksetzen“ nicht angeboten und serverseitig abgewiesen. Eine solche Aktion darf die Anmeldepolicy nicht heimlich ändern.

Self-Registration, offene Einladungen und Just-in-time-Autoprovisionierung bleiben aus. Für den ersten Aufbau genügt ein administrativ angelegter Benutzerbestand. E-Mail dient als lokaler Loginname gemäss vorhandener Speakr-Oberfläche, nicht als vertrauenswürdiger externer Identitätsnachweis. SMTP-Erreichbarkeit ist keine Voraussetzung; synthetische Testkonten verwenden `example.invalid`. E-Mail-/Benutzernamenskollisionen werden explizit aufgelöst, nicht durch Zusammenlegung von Daten.

Benutzer werden im MVP deaktiviert statt fachlich verknüpft hart gelöscht. Die Upstream-Löschaktion mit möglicher Inhaltslöschung muss für klinische Konten gesperrt sein. Aufbewahrungs-/Löschregeln für Fachdaten werden nicht durch Benutzerverwaltung ersetzt.

## 6. Bootstrap, Aktivierung und Wiederherstellung
**Erster Administrator:** lokale Operator-CLI/Managementcommand im Serverkontext, mit verdeckter Eingabe oder einmaligem geschützt ausgegebenem Aktivierungscode. Kein Defaultpasswort, kein öffentliches „erster Besucher wird Admin“, kein Secret in Argumentliste, Compose-Datei, Repository oder Standardlog. Transaktionale Prüfung verhindert parallele Bootstrap-Administratoren; nach erfolgreicher Initialisierung ist der Bootstrap abgeschlossen. Ein späterer Notfallzugang erfordert einen ausdrücklich dokumentierten Operatorprozess.

Der Bootstrap erhält technische und Identitätsverwaltungsrechte, aber keinen pauschalen Zugriff auf Patientendaten. Klinikrollen werden explizit zugewiesen und auditiert. Das letzte aktive, lokal anmeldefähige Identitätsadministratorkonto darf im hybriden Profil nicht versehentlich deaktiviert, auf Entra-only umgestellt oder mit unbekanntem Passwort unzugänglich gemacht werden. Dieser Betriebszugang ist kein Passwortbypass für beliebige Administratoren.

**Weitere Konten:** zuerst `pending_activation`; Berechtigte erzeugen einen kryptografisch zufälligen Einmalcode mit 30 Minuten Gültigkeit und mindestens 256 Bit Zufall. Gespeichert wird nur sein Hash, mit Nutzerbindung und Zweck (`activate_local` oder `reset_local`). Ausgabe einmalig in einer `no-store`-Antwort an die berechtigte Administration; Übergabe an den Nutzer über einen genehmigten separaten Kanal. Keine Patientendaten und keine Secrets in Audit, URL-Query oder Referrer. Der Code wird im vorgesehenen Formular per POST eingelöst.

Aktivierung/Reset erlauben nur Passwortsetzung, kein Lesen klinischer Daten. Nach erfolgreicher einmaliger Einlösung sind alle anderen Resetcodes ungültig; parallele Einlösung darf nur einmal gelingen. Danach erneute normale Anmeldung, keine automatische Vollsitzung durch Reset. Ein vergessenes Passwort braucht im Startbetrieb keinen Mailserver: Admin prüft die Person, widerruft bestehende Sitzungen und erzeugt einen neuen Einmalcode. SMTP-Selbstbedienung ist ein späteres optionales Profil, nicht Teil des Startzwangs.

Eine Wiederherstellung aktiviert weder ein deaktiviertes Konto noch lokale Anmeldung für `entra_only`. Solche Änderungen sind eigenständige, auditierte und berechtigte Verwaltungsaktionen. Neue lokale Nutzer setzen ihr Passwort selbst; Administratoren können kein bestehendes Passwort auslesen.

## 7. Passwort- und Sitzungsschutz
Die folgenden Werte sind konkrete Entwicklungsdefaults, keine behauptete klinische Sicherheitsfreigabe. Sie werden getestet statt bei jeder Umsetzung erneut abgefragt.

| Regel | Default |
|---|---|
| Passwortlänge | 15–128 Unicode-Zeichen; keine stille Kürzung, kein Trim und keine erfundene Komplexitätspflicht |
| Schwache Passwörter | Lokal bereitgestellte Blockliste; keine Passwortabfrage an externe Prüfdienste |
| Neue Hashes | Vorhandene Werkzeug-Funktionen, `scrypt:131072:8:1`, Salt durch Bibliothek |
| Alte Hashes | Nur über bekannten kompatiblen Legacy-Verifier prüfen; nach erfolgreichem Login rehashen; unbekannte Formate sperren |
| Hash-Ressourcen | Höchstens 2 gleichzeitige Hashprüfungen pro Webprozess als Startwert; Backpressure, Lasttest und Speichermessung |
| Falsche Kennwörter | 5 Fehler pro Login-ID in 15 Minuten → 15 Minuten temporäre Sperre; zusätzlich 120 Versuche pro vertrauenswürdig ermittelter Client-IP/15 Minuten als Startlimit |
| Dauerhafte Kontosperre | Getrennt vom temporären Ratelimit; nur berechtigte Verwaltung |
| Sitzungslebensdauer | 15 Minuten Inaktivität, 8 Stunden absolut; keine dauerhafte Remember-me-Anmeldung |
| Sensible Verwaltung | Erneute Authentisierung innerhalb der letzten 5 Minuten; CSRF-Schutz und revisionsgebundene Änderungen |
| Resetcodes | 30 Minuten, einmalig, gebundener Zweck und Benutzer; nur Hash persistiert |

Werkzeug liefert Hashing/Verifikation; OWASP beschreibt Passwortschutz, scrypt als etablierten Pfad und sichere Wiederherstellung [OSS34–37]. Das sind Grundlagen, keine Aussage, dass das unveränderte Speakr bereits alle oben definierten Kontrollen umsetzt.

Hash-Limits und Loginzähler gelten instanzübergreifend dort, wo mehrere Prozesse eingesetzt werden. Keine alleinigen prozesslokalen Zähler als Brute-Force-Schutz ausgeben; vorhandenen DB-basierten Login-Lock wiederverwenden und ergänzen. Proxy-IP-Header nur von erlaubten Proxys übernehmen. Bei fehlendem sicherem Schutz keine unbegrenzte Authentisierung anbieten. Fehlermeldungen dürfen unbekannte, deaktivierte oder falsch eingegebene Konten nicht unterscheidbar offenlegen.

Sitzung nur mit opaker HttpOnly-/Secure-Cookie-Referenz; Provider-Tokens bleiben serverseitig. Session-ID nach Login und Rechtewechsel rotieren. Deaktivierung, Passwortreset, Providerwechsel und expliziter Sitzungswiderruf erhöhen eine Sicherheitsversion; nächste geschützte Anfrage wird blockiert, laufende Streams spätestens innerhalb des bisherigen 60-Sekunden-Ziels beendet. Autosave/Heartbeat ist keine unbegrenzte Benutzeraktivität; Aufnahme bei Ablauf geschützt stoppen/finalisieren, ohne zusätzliche Leserechte zu erzeugen.

MFA-Felder und Step-up-Schnittstelle werden erweiterbar gehalten; lokale Passwortanmeldung wird nicht als MFA ausgegeben. Für Echtdatenbetrieb müssen Schutzbedarf und privilegierte Konten im G-IDENTITY-Gate bewertet werden. Es wird weder eine selbst gebaute MFA-Engine noch ein ungeprüftes MFA-Produkt zusätzlich installiert. Bestehende Gatepflichten bleiben bestehen.

## 8. Entra bereits vorbereiten und isoliert testen
Implementierung jetzt: vorhandener Authlib/OIDC-Client; tenantgebundene Discovery; Authorization Code mit state/nonce/PKCE; serverseitiger Codeaustausch; Signatur-/Issuer-/Audience-/Tenant-/Ablaufprüfung; sichere Sessionbildung; Logout; deaktivierter Start-/Callbackzustand; User-Mapping; Fehlerseite und Konfigurationsdiagnose ohne Secrets.

Benötigte Einstellungen: Tenant-ID, Client-ID, Secret-Dateireferenz, exakter erlaubter Issuer, Discovery- und Redirect-URI. Keine Konfiguration mit `common`/beliebigem Tenant. Nur erforderliche OIDC-Scopes, standardmässig `openid profile email`; kein Graph-Leserecht, keine Gruppensynchronisierung und kein `offline_access` ohne eigene Anforderung. Reale Zugangsdaten nicht im Chat oder beim Coding-Agenten anfordern.

Tests verwenden die bestehende Authbibliothek und isolierte OIDC-HTTP-/JWKS-Fixtures mit synthetischen Tokens. Optionaler Keycloak-Testcontainer darf separat gestartet werden, darf aber nicht zum Pflichtdienst des lokalen Produkts werden. Mockbeweise sind keine Entra-Abnahme. Der Upstream-Passwort-Adminbypass muss auch bei direkten URL-Aufrufen durch die Kontopolicy begrenzt sein.

**Entra-Konfiguration und Gates erst später:** App-Registrierung, tatsächliche Redirect-URI, Tenantzugriff, Conditional Access/MFA, WebView-Benutzerwechsel, Account-Lifecycle und erlaubter Identitäts-Egress. G-ENTRA blockiert nur die echte Entra-Aktivierung, nicht den lokalen Pilot. G-LDAP bleibt ein separates späteres Gate; die LDAP-Fähigkeit wird nicht gestrichen.

## 9. Sichere Verbindung bestehender lokaler Konten
Keine automatische Verknüpfung über gleiche E-Mail/Domain, `preferred_username`, Anzeigename oder den momentan im KIS sichtbaren Benutzer. Microsoft unterscheidet veränderliche Anzeige-/E-Mail-Claims von Identifikatoren [OSS38]. Externe Zuordnung verwendet den vollständig validierten `(issuer, subject)`-Schlüssel; Entra-`tenant_id`/`object_id` dienen zusätzlich dem expliziten Provisionierungsnachweis und werden nicht mit `sub` gleichgesetzt.

Standardverfahren ohne Graph: Identitätsadministration erstellt einen **ausdrücklichen Linkauftrag** für die vorhandene `user.id` und die verifizierten Entra-`tenant_id`/`object_id`. Der Benutzer bestätigt mit frischer lokaler Authentisierung und authentisiert sich zusätzlich bei Entra. Der Callback muss exakt zum erlaubten Tenant/Object und zum serverseitig gebundenen Linkauftrag passen. Erst dann werden Issuer/Subject atomar gebunden. Ohne Linkauftrag kein Selbst-Linking und keine Neuanlage mit Fachrechten.

Für `device_only`/Benutzer ohne lokalen Login ist eine separate administrativ nachgewiesene Vorprovisionierung zulässig. Die aufzulösenden Tenant/Object-Werte kommen über einen freigegebenen Administrationsweg; keine Suchheuristik nach E-Mail. Unbekannte Identität landet ohne Fachzugriff in einem neutralen Provisionierungsfehler, nicht in einem öffentlich lesbaren Benutzerverzeichnis.

Eindeutigkeitsbedingungen: eine externe Identität gehört genau einem internen User; ein bereits gebundenes Tenant/Object darf nicht auf einen zweiten User wechseln; konkurrierende Linkversuche scheitern kontrolliert. Die Verknüpfung enthält Akteur, Nachweis, Zeitpunkt und Revision. Umhängen erfordert eigenständige geprüfte Entknüpfung/Neuverknüpfung, kein `upsert` über E-Mail.

Default nach bestätigtem Link: `entra_only`, Sicherheitsversion erhöhen, vorhandene Sitzungen beenden und lokalen Hash entfernen. Die Konsequenz wird vorher angezeigt. `local_and_entra` ist nur eine explizite administrative Ausnahme mit Begründung; der Benutzer kann sie nicht selbst aktivieren. Lokale Passwörter, Diktate oder Rollen werden niemals nach Entra synchronisiert.

## 10. Rechteentzug und Conditional Access
Die Anwendung bleibt im Start- und Hybriddefault führend für Fachrollen und Poolzuordnung. Ein Entra-Claim erteilt keine Administrator- oder klinischen Rollen ohne ein zusätzlich freigegebenes Mapping. Linking ändert weder Autoren-/Gerätezuordnung noch historische Freigaben.

**Lokale Anmeldung durchläuft keine Entra-Conditional-Access-/MFA-Prüfung.** Deshalb darf ein Entra-only-Konto bei Entra-Ausfall nicht lokal einloggen, und ein bewusst duales Konto benötigt eine dokumentierte Risikofreigabe. Hybrider Betrieb bedeutet nicht zwei universell austauschbare Zugänge für jede Person.

Appseitige Deaktivierung wirkt unmittelbar auf alle App-Provider. Eine ausschliesslich in Entra erfolgende Kontosperre ist dagegen ohne eingerichteten Lifecycle-/Widerrufsweg kein Echtzeit-Widerruf bestehender App-Sitzungen. Für die spätere Entra-Freigabe gilt: dokumentierte maximale Sitzungsdauer plus Helpdesk-Prozess zur gleichzeitigen Appdeaktivierung; schnellerer automatischer Sync nur als separat implementierte Erweiterung. Nichts als sofortigen Entra-Offboarding-Effekt behaupten, was nicht getestet wurde.

## 11. Migration, Aktivierung und Rückfall
Erster Release liefert das additive Identity-Schema bereits mit: stabile interne Benutzer-ID, Kontopolicy, Sicherheitsversion, externe Identity-Tabelle und Linkaufträge. Der spätere Entra-Wechsel benötigt deshalb keine Verschiebung von Diktaten und keine Neuanlage von Autoren. Notwendige Upstreammigrations-/Datentypänderungen werden trotzdem versioniert und gegen PostgreSQL getestet; keine pauschale Zusage „nie eine Migration“.

Ablauf später: Backup und Stagingtest → Entra-Konfiguration separat validieren → lokales Administratorkonto prüfen → Hybridprofil aktivieren → ein Testkonto explizit verbinden → Rechte-/Verlaufs-/WebViewtests → gestufte weitere Verknüpfung. Autoren, Pools, Gerätetabelle und Freigaben vor/nach dem Wechsel müssen dieselben internen Referenzen haben.

Rückfall deaktiviert den Provider, setzt aber Entra-only-Konten **nicht** automatisch auf lokale Kennwörter zurück. Wiederherstellung durch lokale Identitätsadministration mit dokumentierter Prüfung, neuer Aktivierung und Audit. Keine Nutzung alter entfernten Hashes und keine Cloud-Ausfallhintertür. Daten-/Kontext-/Freigaberegeln bleiben dabei unverändert.

## 12. Was Claude ohne Rückfrage liefern muss
Lokalen Start ohne Entra/Keycloak/SMTP; funktionsfähige Benutzerverwaltung; Bootstrap-/Aktivierungs-/Resetprozess; Rollen-/Poolzuordnung; sessionsichere Anmeldung; additive Identity-Migrationen; vollständigen aber deaktivierten OIDC-Adapter; Profile `local` und `hybrid`; Linkworkflow und Ausschluss automatischer Kontenübernahme; isolierte Entra-Negativtests; dokumentierten Aktivierungs-/Rollbackplan. Anwendung und Anbieteranmeldung werden nicht als fertig behauptet, bevor echte Tests vorliegen.

R-009 und R-043 sowie S03 sind aktualisiert. R-051–R-062 und T-071–T-098 beschreiben die Ergänzung. T-009 ist nun der lokale Basistest. LDAP- und Entra-Realfreigaben liegen separat, nicht als Voraussetzung für die erste lokale Anmeldung. Tests starten `not_run`; Paketvalidierung ist kein Auth-/Kliniktest.
