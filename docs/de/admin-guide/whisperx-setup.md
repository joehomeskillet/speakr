# Lokale Spracherkennung betreiben

[English](../../admin-guide/whisperx-setup.md)

Wählen Sie für die Klinikinstallation einen lokalen Erkennungsdienst. Der primäre OpenAI-kompatible Konnektor kann über **TRANSCRIPTION_BASE_URL** auf lokales Speaches zeigen. Verwendet die Installation den separaten Konnektor **asr_endpoint**, muss auch dessen **ASR_BASE_URL** auf einen lokalen Dienst zeigen. Tragen Sie keinen Cloud-Endpunkt ein. Der synthetische Klinik-Stub ist für Tests gedacht.

Der mitgelieferte OpenVINO-GPU-Dienst ist optional. Bei einem dafür vorgesehenen GPU-Fehler kann der Auftrag einmal auf den lokalen CPU-Konnektor zurückfallen. Prüfen Sie den CPU-Weg mit einer kurzen neutralen Sprachprobe: Auftrag abschließen lassen, anschließend Text und Wiedergabe vergleichen. Erwarten Sie eine Wortmarkierung, aktivieren Sie unterstützte Wortzeitstempel und vergleichen Sie mehrere Wörter mit derselben Aufnahme. Segmentanfänge allein ermöglichen keine wortgenaue Zuordnung.

Die [Modellwahl](model-configuration.md) erklärt GPU- und CPU-Vorgaben. Erkennungshinweise setzen Freigabe und Unterstützung durch den Dienst voraus. Im Klinikmodus gilt dafür eine Grenze von 55 Wörtern. Sprecheridentifikation und die cloudgestützte Diarisierung des Originalprojekts gehören nicht zum normalen Klinikablauf.

[Originalhandbuch von Speakr in der archivierten Fassung](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/whisperx-setup.md)
