# E-Mail, Kontowiederherstellung und interne Mitteilungen

[English](../../admin-guide/email-setup.md)

Eine Klinikinstallation startet ohne SMTP. Neue Konten werden lokal verwaltet. Selbstregistrierung, E-Mail-Verifikation und Passwortzurücksetzung per E-Mail stehen im Klinikmodus nicht zur Verfügung. Hat eine Person den Zugang verloren, wenden Sie sich für das vorgesehene Wiederherstellungsverfahren an die zuständige lokale Identitätsadministration.

Davon getrennt ist die optionale Transkriptionsbenachrichtigung aus dem Originalprojekt. Richtet der Betreiber SMTP ein und aktiviert ein Nutzer unter **Konto → Einstellungen** den Versand, kann eine abgeschlossene oder fehlgeschlagene Transkription eine E-Mail auslösen. Der Aufnahmetitel kann im Betreff stehen, bei einem Fehler auch eine gekürzte Fehlermeldung im Nachrichtentext. Berücksichtigen Sie diese Inhalte bei der Freigabe des Mailwegs und der Empfänger. Ein synthetischer Versandtest prüft den Mailtransport.

Anforderung, Ablehnung, Abnahme und Fertigstellung lösen [interne Workflow-Mitteilungen](webhooks.md) aus. Diese benötigen kein SMTP. Den Nachweis einer formalen Zustellung prüfen Sie unter **Ausgabe** beim betreffenden Diktat.

[Originalhandbuch von Speakr in der archivierten Fassung](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/email-setup.md)
