# Sprecher-Engine: Lizenzprüfung der exakten Artefakte (GATE 0)

Abrufdatum aller Quellen: 2026-11-18. Reines Evidence-Paket, keine Modelldateien im Repo.
Kontext: `/nvmetank1/projects/speakr-deploy/bench/speaker/RECOMMENDATION.md` (sherpa-onnx + pyannote-segmentation-3.0-ONNX + 3D-Speaker ERes2Net-base).

## Ergebnistabelle

| Artefakt | Quelle (Primär) | Lizenz | Kommerziell | Redistribution | Auflagen | Konfidenz |
|---|---|---|---|---|---|---|
| a) sherpa-onnx (Bibliothek) | https://raw.githubusercontent.com/k2-fsa/sherpa-onnx/master/LICENSE (Apache License 2.0, Volltext geprüft) | Apache-2.0 | ja | ja | Lizenztext + NOTICE beilegen, Änderungen kennzeichnen | hoch |
| b1) pyannote/segmentation-3.0 (Original) | https://huggingface.co/api/models/pyannote/segmentation-3.0 (`cardData.license: mit`, Tag `license:mit`, `gated: auto`) | MIT | ja | ja | Copyright- und Lizenzhinweis beilegen. HF-Gating ist nur eine Anmeldung (Firma/Website, Marketing-Mails), keine Lizenzbeschränkung | hoch |
| b2) k2-fsa ONNX-Export `sherpa-onnx-pyannote-segmentation-3-0` | Beiliegende `LICENSE` im Archiv (MIT, "Copyright (c) 2022 CNRS"), `README.md` (konvertiert aus huggingface.co/pyannote/segmentation-3.0); Release https://github.com/k2-fsa/sherpa-onnx/releases/tag/speaker-segmentation-models (Asset `sherpa-onnx-pyannote-segmentation-3-0.tar.bz2`, 6958444 B, aktualisiert 2024-10-08); `export-onnx.py` Z. 111 verweist auf die HF-LICENSE | MIT (Weitergabe der LICENSE im Archiv) | ja | ja | MIT-Hinweis mitliefern; Archiv enthält LICENSE bereits | hoch (Lizenz), mittel (Trainingsdaten, s. u.) |
| c) 3D-Speaker ERes2Net-base, Datei `3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx` | ModelScope-API https://www.modelscope.cn/api/v1/models/iic/speech_eres2net_base_sv_zh-cn_3dspeaker_16k (`License: Apache License 2.0`, Revision `master`, letzte Änderung Unix 1692869850 = 2023-08-24); Toolkit-Repo https://github.com/modelscope/3D-Speaker (README: "released under the Apache License 2.0", LICENSE-Datei Apache-2.0 geprüft); ONNX-Konvertierung: https://github.com/k2-fsa/sherpa-onnx/releases/tag/speaker-recongition-models (Hinweis dort: "Each model has its own license") | Apache-2.0 (laut ModelScope-Modellseite) | ja | ja | Lizenztext beilegen; Trainingsdaten-Vorbehalt s. u. | mittel-hoch |

## Zuordnung lokaler Dateien (SHA-256)

Basis: `/nvmetank1/projects/speakr-deploy/bench/speaker/models/`.

| Lokale Datei | SHA-256 | Zuordnung |
|---|---|---|
| `3dspeaker_eres2net.onnx` (39593761 B) | `1a331345f04805badbb495c775a6ddffcdd1a732567d5ec8b3d5749e3c7a5e4b` | Identisch (SHA-256 erneut heruntergeladen und verglichen) mit Release-Asset `3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx` von https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/ |
| `seg.tar.bz2` | `24615ee884c897d9d2ba09bb4d30da6bb1b15e685065962db5b02e76e4996488` | Archiv des Segmentierungsmodells; Herkunft laut Dateiname/Inhalt `sherpa-onnx-pyannote-segmentation-3-0`. Byte-Vergleich mit dem Release-Asset nicht durchgeführt (GitHub liefert für dieses Asset kein `digest`) |
| `sherpa-onnx-pyannote-segmentation-3-0/model.onnx` | `220ad67ca923bef2fa91f2390c786097bf305bceb5e261d4af67b38e938e1079` | Inhalt des Archivs |
| `sherpa-onnx-pyannote-segmentation-3-0/model.int8.onnx` | `d582f4b4c6b48205de7e0643c57df0df5615a3c176189be3fc461e9d18827b5d` | Inhalt des Archivs |
| `sherpa-onnx-pyannote-segmentation-3-0/LICENSE` | `14d7016ad68e7394d6e6b78d96cc2ae431c905287b89674cfdf021e79e62b8ba` | MIT, "Copyright (c) 2022 CNRS" |

sherpa-onnx-Version: Die benchte Laufzeitversion ist im Bench-Verzeichnis nicht festgehalten (unklar). Aktuelle Release-Linie: Tag `v2.0.1`, veröffentlicht 2026-10-26, Commit `af2d8d81dbfa61c4cea4c568da78f3982edca005`. Die Lizenz (Apache-2.0) gilt für das ganze Repo; bei Produktivnahme die Version pinnen.

## Offene Punkte (nicht geraten)

1. **3D-Speaker-Trainingsdaten unter CC BY-SA 4.0.** Der 3D-Speaker-Datensatz (https://3dspeaker.github.io/, Abschnitt License) steht unter CC BY-SA 4.0. Das ERes2Net-base-Modell "3dspeaker_16k" wurde laut Modellbeschreibung auf diesem Datensatz trainiert. Die Modell-Gewichte sind von ModelScope ausdrücklich als Apache-2.0 lizenziert. Ob die Share-Alike-Bedingung auf trainierte Gewichte durchschlägt, ist rechtlich nicht abschliessend geklärt. Aus den Primärquellen ergibt sich kein Verbot der kommerziellen Nutzung. Restrisiko gering bis mittel; Entscheid beim Rechtsverantwortlichen. Die 200k-Speaker-Variante nutzt andere Daten, ist aber 3x grösser im Pfad (nicht gebencht).
2. **Trainingsdaten von pyannote-segmentation-3.0** (öffentliche Diarization-Korpora) konnten nicht gelesen werden: Die Raw-README auf HF liefert ohne Token HTTP 401 (gated). Modelllizenz ist MIT; Lizenzen der Korpora wurden nicht einzeln geprüft (unklar). Gleiche Einordnung wie Punkt 1.
3. **Jahreszahl im MIT-Text:** lokale LICENSE nennt 2022 (CNRS); die frühere Empfehlung nannte 2023. Massgeblich ist der Text der LICENSE-Datei (2022); unkritisch.
4. **Gewichte-Datei-Revision auf ModelScope:** Die API liefert nur `Revision: master`, keine Commit-ID. Die Datei ist über SHA-256 eindeutig bestimmt.
5. Die Lizenzfeld-Angabe auf ModelScope ist eine Selbstauskunft des Herausgebers (Alibaba/DAMO). Ein separater Lizenz-Dateitext im Modell-Repo wurde nicht geprüft.

## Auflagen für die Umsetzung

- Dienst-Image/Release: Apache-2.0-Text (sherpa-onnx, 3D-Speaker) und MIT-Text (pyannote/CNRS) in einer `THIRD-PARTY-NOTICES` mitliefern.
- Keine Modellgewichte ins Git-Repo; Download mit festem SHA-256 (oben) im Deploy-Schritt.
- Kein HF-Token nötig (k2-fsa-Export ist ungated).

## Verdikt

**PASS.** Alle drei Artefakte tragen permissive Lizenzen (Apache-2.0, MIT, Apache-2.0), die kommerzielle Nutzung und Weitergabe erlauben. Die Lizenzfelder stammen aus Primärquellen. Restvorbehalt: CC-BY-SA-Trainingsdaten (Punkt 1) und nicht geprüfte pyannote-Trainingskorpora (Punkt 2); beides ist keine Lizenzbedingung der Gewichte selbst. Eine Alternative ist daher nicht erforderlich. Falls der Rechtsverantwortliche Punkt 1 ablehnt: nächstbestes Bench-Ergebnis mit klarer Lizenz wäre WeSpeaker ResNet34 (Embedding-EER 1.9 %), dessen Lizenz laut Empfehlung aber widersprüchlich belegt und ungeprüft ist; dann Re-Bench mit der ERes2Net-Variante `eres2net_base_200k` (andere Trainingsdaten) vorziehen.
