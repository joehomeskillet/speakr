# Quellen und Originalhandbuch

[English](../sources.md)

Das deutsche und das englische Klinikhandbuch beschreiben, wie Sie auf dieser Plattform mit Diktaten im Browser arbeiten. Das englische Originalhandbuch beschreibt das Upstream-Projekt Speakr. Es behandelt auch Funktionen außerhalb der Klinikedition, darunter Cloud-Transkription und Begleitanwendungen. Welche Funktionen hier zur Verfügung stehen, lesen Sie im Klinikhandbuch.

## Originalhandbuch

[Englisches Originalhandbuch herunterladen (ZIP, 12,9 MiB)](../downloads/speakr-handbook-039a19b1.zip)

Das Archiv enthält alle Dateien aus `docs/`, die ursprüngliche `mkdocs.yml` und die Datei `LICENSE` von Murtaza Nasir und den Speakr-Mitwirkenden aus Commit `039a19b1f1c7f0ddaeac0a49b806845626f48a6c`. Texte, Abbildungen und Konfiguration sind im Original erhalten. Die Screenshots im Archiv zeigen das Upstream-Produkt. [Originaldokumentation](https://github.com/murtaza-nasir/speakr/tree/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs) · [Quellcommit](https://github.com/murtaza-nasir/speakr/commit/039a19b1f1c7f0ddaeac0a49b806845626f48a6c) · [GNU Affero General Public License v3](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/LICENSE)

SHA-256 der ZIP-Datei: `1834c140bb5525ae473efb0a9b72d772e6d1b6eefbc31242676e299b88b3fde2`.

Um das Originalhandbuch lokal zu bauen, entpacken Sie das Archiv und öffnen den Ordner mit der `mkdocs.yml`. Führen Sie dort in einer virtuellen Python-Umgebung die folgenden Befehle aus. Der Build legt das statische Handbuch unter `site/` ab. Starten Sie zum lokalen Lesen den Vorschauserver und öffnen Sie die im Terminal angezeigte lokale Adresse.

```sh
python -m pip install -r docs/requirements-docs.txt
python -m mkdocs build
python -m mkdocs serve
```

## Klinikedition

Das veröffentlichte Handbuch folgt dem Hauptzweig der Klinikedition. Änderungen in Entwicklung stehen im Zweig dev. [Veröffentlichter Stand (main)](https://github.com/joehomeskillet/speakr/tree/main/docs) · [Entwicklungsstand (dev)](https://github.com/joehomeskillet/speakr/tree/dev/docs)
