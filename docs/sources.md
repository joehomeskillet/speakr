# Sources and original handbook

[Deutsch](de/sources.md)

The English and German clinic guides describe the browser-based dictation workflow in this fork. The original English handbook documents upstream Speakr. It also covers options outside the clinic edition, including cloud transcription and companion apps. Use the clinic guides to determine which options are available here.

## Original handbook

[Download the original English handbook (ZIP, 12.9 MiB)](downloads/speakr-handbook-039a19b1.zip)

The archive contains all files in `docs/`, the original `mkdocs.yml`, and the repository's `LICENSE` from Murtaza Nasir and the Speakr contributors at commit `039a19b1f1c7f0ddaeac0a49b806845626f48a6c`. Text, screenshots and configuration in the archive retain their original contents. The upstream screenshots illustrate upstream Speakr. [Original documentation](https://github.com/murtaza-nasir/speakr/tree/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs) · [Source commit](https://github.com/murtaza-nasir/speakr/commit/039a19b1f1c7f0ddaeac0a49b806845626f48a6c) · [GNU Affero General Public License v3](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/LICENSE)

SHA-256 of the ZIP file: `1834c140bb5525ae473efb0a9b72d772e6d1b6eefbc31242676e299b88b3fde2`.

To build the original handbook locally, extract the archive, open the extracted directory containing `mkdocs.yml` and run the following commands in a Python virtual environment. The build creates the static handbook in `site/`. To browse it locally with working navigation, start the preview server and open the loopback address printed in the terminal.

```sh
python -m pip install -r docs/requirements-docs.txt
python -m mkdocs build
python -m mkdocs serve
```

## Clinic edition

The published handbook follows the clinic fork's main branch. Changes in development are available on the dev branch. [Published source (main)](https://github.com/joehomeskillet/speakr/tree/main/docs) · [Development source (dev)](https://github.com/joehomeskillet/speakr/tree/dev/docs)
