# Tencent Cloud ASR und Klinikmodus

[English](../../advanced/tencent-asr.md)

Upstream-Speakr dokumentiert die Tencent-Cloud-Erkennung für Aufnahmedateien. Dabei wird Audio für einen Herstellerauftrag übertragen und das Ergebnis später abgeholt. Der Klinikmodus verlangt lokale Spracherkennung. Die Konnektor-Registry dieses Klinikbranches bietet `tencent_asr` nicht an.

## Eine bestehende Konfiguration umstellen

1. Vermerken Sie Konnektorname und Wartestand der Upstream-Installation, ohne Audio, Dienstgeheimnisse oder Auftragsinhalte zu kopieren. Ein offener Cloudauftrag muss nach den Verfahren der ursprünglichen Installation abgeglichen werden.
2. Stellen Sie eine lokale Engine für `POST /asr` oder die lokale OpenAI-kompatible Transkriptions-API bereit. Wählen Sie in der Klinik-App den passenden [Konnektor und das Modell](../advanced/custom-connectors.md). Tencent-Zugangsdaten und Cloud-Zwischenspeicher gehören nicht in die Klinikinstallation.
3. Sprechen Sie mit einem Klinik-Testkonto einen neutralen Satz. Prüfen Sie die lokale Verarbeitung, den gespeicherten Text, gegebenenfalls Sprecher- oder Wortzeitangaben und die Wiedergabe. Eine erfolgreiche Webanmeldung allein prüft lediglich den Webdienst.

Ein unbekannter Konnektor oder eine abgewiesene Fernadresse weist auf eine fehlerhafte Konfiguration hin. Korrigieren Sie den lokalen Dienst und erproben Sie den Ablauf mit synthetischem Material.

Originalreferenz zu Upstream-Speakr: [archivierte Upstream-Seite](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/advanced/tencent-asr.md).
