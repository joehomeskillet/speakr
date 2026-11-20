# Lokale Anmeldung und Identitätsanbieter

[English](../../admin-guide/sso-setup.md)

Verwenden Sie das lokale Anmeldeformular mit dem Konto, das die Identitätsadministration eingerichtet hat. **Einmalanmeldung: Nicht eingerichtet** bedeutet, dass die Instanz keine aktive Einmalanmeldung anbietet. Eine übereinstimmende E-Mail-Adresse verknüpft keine Konten. Die OIDC-Konfiguration, Kontoverknüpfung und SSO-API-Token des Originalprojekts sind im Klinikmodus inaktiv.

Im Code ist die ausdrückliche Entra-Verknüpfung mit einem bestimmten Bestandskonto vorbereitet. Beim bestätigten Wechsel auf ausschließliche Entra-Anmeldung bleiben interne Benutzerkennung und Diktatzuordnung erhalten. Lokales Passwort und offene Wiederherstellungscodes werden ungültig, laufende Sitzungen enden. Ein nutzbares lokales Konto für die Identitätsadministration muss erhalten bleiben. Für einen echten Verzeichnisdienst ist eine eigene Abnahme erforderlich.

LDAP und integrierte Windows-Anmeldung sind ebenfalls vorbereitet und deaktiviert. Lokale Identitätstests belegen weder einen bestimmten Entra-Mandanten noch einen Domänencontroller oder einen Arbeitsplatz. Den aktuell nutzbaren lokalen Weg bis zur Wiederherstellung beschreibt die [Benutzerverwaltung](user-management.md).

[Originalhandbuch von Speakr in der archivierten Fassung](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/sso-setup.md)
