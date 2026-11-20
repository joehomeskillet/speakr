# Anweisungen für Zusammenfassung, Titel und Spracherkennung

[English](../../admin-guide/prompts.md)

Die Klinikedition liefert deutsche Vorgaben für Zusammenfassungen und Aufnahmetitel. Die Zusammenfassung gliedert Anlass, Anamnese, aktuellen Befund, Diagnosen, Medikation, weiteres Vorgehen und offene Punkte. Die Vorlage fordert, sich an die diktierten Aussagen zu halten und Unsicherheiten mit **[bitte prüfen]** zu kennzeichnen. Auch flüssig formulierte Sätze müssen anhand der Quelle geprüft werden.

Vergleichen Sie an einem synthetischen Diktat mit gespeicherter Textfassung die **KI-Zusammenfassung** mit der Aufnahme. Ändert sich der Text, prüfen Sie vor einer erneuten Anforderung den Bezug zur Fassung. Generierter Titel und Transkript sind getrennte Ergebnisse.

Der Upstream-Editor **Standard-Prompts** ist im Klinikmodus ausgeblendet. Eine bestehende, ausdrücklich gesetzte Vorgabe mit höherer Priorität kann weiter greifen. Für unveränderte alte Upstream-Defaults werden die Klinikvorlagen verwendet. Transkriptionshinweise beeinflussen die Erkennung und benötigen die passende lokale ASR-Fähigkeit sowie den aktivierten Hinweisschalter. Das Textmodell wählen Sie unter [Modellwahl](model-configuration.md).

[Originalhandbuch von Speakr in der archivierten Fassung](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/prompts.md)
