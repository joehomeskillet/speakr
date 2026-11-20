# Lokale Modelle für Transkription und Zusammenfassung wählen

[English](../../admin-guide/model-configuration.md)

Melden Sie sich mit einem aktiven Site-Administrationskonto an und öffnen Sie **Verwaltung → System → Modellwahl**. Die Seite unterscheidet GPU-Transkription, CPU-Transkription und das Textmodell der Zusammenfassung. Prüfen Sie je Zeile die gespeicherte Auswahl, die wirksame Kennung und deren Herkunft. Auch für einen inaktiven Pfad kann eine Auswahl gespeichert sein. Die Anzeige führt keinen Erreichbarkeitstest aus.

1. Wählen Sie eine Kennung aus dem lokalen Dienstkatalog. Meldet der Dienst keinen vollständigen Katalog, können Sie eine auf Ihrer Installation unterstützte Modellkennung eingeben. Adressen und Zugangsdaten gehören nicht in diese Felder.
2. Speichern Sie eine geänderte Auswahl und prüfen Sie nach erneutem Öffnen das wirksame Modell. Ein leeres Feld erbt die Vorgabe. Bei der CPU kann das der Standard des bestehenden Upload-Modellkatalogs sein. Die GPU übernimmt die Installations- oder Dienstvorgabe, die Zusammenfassung das konfigurierte lokale Textmodell.
3. Hat zwischenzeitlich eine andere Person gespeichert, laden Sie die aktuellen Werte und gleichen Sie die Änderung ab. Lehnt ein bestätigter Katalog eine Kennung ab, wählen Sie eine vom Dienst gemeldete Kennung.

Für automatische Zusammenfassungen müssen sowohl Systemeinstellung als auch persönliche Einstellung des Autors die Funktion erlauben. Nach der ersten erfolgreichen Transkription eines berechtigten Diktats kann der Auftrag für dessen Textfassung entstehen. Das Öffnen von **KI-Zusammenfassung** liest nur den Status. Das Transkript bleibt separat. Siehe [Prompts](prompts.md) und [lokale Spracherkennung](whisperx-setup.md).

[Originalhandbuch von Speakr in der archivierten Fassung](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/model-configuration.md)
