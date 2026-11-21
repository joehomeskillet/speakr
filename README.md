**Deutsch** · [English](README.en.md)

# Speakr – Web-Diktat für Kliniken

Dieser unabhängige Fork von [Speakr](https://github.com/murtaza-nasir/speakr) (Originalprojekt, Upstream) wird als webbasierte Ablösung für dns.comfort entwickelt, mit Fokus auf den Arbeitsablauf von dns.comfort.pro ([Hersteller](https://www.sonic-labs.net/produkte/dns-comfort-pro)). Er ist kein Produkt dieses Herstellers. Diktierende nehmen im Browser auf, die Spracherkennung läuft auf eigener Infrastruktur, und der Schreibdienst hört ab und korrigiert. Auch Prüfung und Freigabe erfolgen ausschliesslich im Webclient, ohne nativen Diktatclient.

Das Projekt richtet sich an Kliniken und Entwicklungsteams, die einen Diktat- und Schreibdienstablauf im Browser erproben oder weiterentwickeln wollen, ohne Audio an Cloud-Spracherkennung zu senden. Die Oberfläche des Klinikmodus ist deutschsprachig (Schweizer Schreibweise).

**Benutzerhandbuch:** [Deutsch](https://joehomeskillet.github.io/speakr/de/) · [English](https://joehomeskillet.github.io/speakr/)

> [!IMPORTANT]
> **Entwicklungsstand.** Der Fork ist nicht für den Produktivbetrieb freigegeben. Die Entwicklungsvorgaben des Projekts erlauben nur synthetische Daten; verwenden Sie keine echten Patientendaten. Was belegt ist und was fehlt, steht unter [Stand der Ablösung](#stand-der-ablösung).

![Schreibplatz von Speakr mit Korrektureditor und Audiowiedergabe für ein synthetisches Testdiktat](docs/images/clinic/editor.png)

*Schreibplatz mit Korrektureditor und Wiedergabe. Alle gezeigten Daten sind synthetisch; die optionale KI-Zusammenfassung ist in Prüfung.*

<table>
  <tr>
    <td width="64%" valign="top"><img src="docs/images/clinic/worklist.png" alt="Arbeitsliste des Schreibdiensts mit synthetischen Testdiktaten" width="100%"></td>
    <td width="36%" valign="top"><img src="docs/images/clinic/mobile-recordings.png" alt="Aufnahmenliste auf einem schmalen Smartphone-Bildschirm mit synthetischen Testdiktaten" width="100%"></td>
  </tr>
  <tr>
    <td>Arbeitsliste nach Schreibkräfte-Pool und Autor. Alle gezeigten Daten sind synthetisch.</td>
    <td>Aufnahmenliste auf dem Smartphone. Alle gezeigten Daten sind synthetisch.</td>
  </tr>
</table>

<details>
<summary><strong>Demos</strong> – zwei kurze Bildschirmclips (stumm, synthetische Daten)</summary>

<br>

Beide Clips sind stumm und zeigen synthetische Daten.

<img src="docs/images/clinic/wiedergabe-mit-wortmarkierung.gif" alt="Stummer Bildschirmclip: Der Schreibplatz spielt ein synthetisches Diktat ab und markiert das jeweils gesprochene Wort im Text" width="100%">

Abspielen des Originalaudios im Schreibplatz mit mitlaufender Wortmarkierung (8,75 s, 14 erkannte Wörter).

<img src="docs/images/clinic/text-korrigieren.gif" alt="Stummer Bildschirmclip: Im Korrektureditor wird ein synthetisches Transkript geändert, gespeichert und erneut geöffnet" width="100%">

Korrigieren, Speichern und erneutes Öffnen eines Transkripts (9,28 s).

</details>

[Funktionen](#funktionen) · [Stand der Ablösung](#stand-der-ablösung) · [Nachweise und Register](#nachweise-und-register) · [Erste Schritte](#erste-schritte) · [Mitwirken](#mitwirken) · [Herkunft und Lizenz](#herkunft-und-lizenz)

## Funktionen

Die Klinikfunktionen sind nur aktiv, wenn `CLINIC_MODE=1` gesetzt ist. Unabhängig von diesem Schalter hat der Fork einzelne Upstream-Funktionen entfernt, darunter Freigabelinks, Kalenderereignisse, den Inkognito-Modus, die Selbstregistrierung, die Passwortrücksetzung per E-Mail und mehrere Cloud-Spracherkennungsanbieter.

### Aufnahme im Browser

- Unter „Neue Aufnahme“ im Browser aufnehmen oder eine Audiodatei hochladen. Vor dem Start ist ein Titel erforderlich.
- „Entwurf sichern“ legt die Aufnahme auf dem Server ab, „Einreichen“ übergibt sie an den Schreibdienst. Audio wird standardmässig nicht im Browserspeicher abgelegt.
- DSS- und DS2-Diktatdateien werden nur mit passender Dateikennung angenommen. Das Original bleibt unverändert; für die Erkennung dekodiert ein fest gepinnter [dss-codec](https://github.com/hirparak/dss-codec) die Datei.

### Lokale Spracherkennung

- Erwartet wird ein OpenAI-kompatibler Spracherkennungsdienst, zum Beispiel Speaches. Er ist nicht Teil dieses Repositorys.
- Für diesen Anschluss (`TRANSCRIPTION_BASE_URL`) akzeptiert der Klinikmodus nur lokale oder private Adressen: Loopback, private und link-lokale Netze, Hostnamen ohne Domain wie einen Containernamen sowie Namen auf `.local`, `.internal`, `.lan` oder `.localhost`. Andere Adressen weist er ab; einen Cloud-Rückfall gibt es nicht. Geprüft wird die Adresse in der URL, nicht deren Namensauflösung. Der alternative Anschluss über `ASR_BASE_URL` wird nicht geprüft.
- Aufgenommene Dateien werden im Hintergrund über die bestehende Verarbeitungswarteschlange transkribiert. Ist `TRANSCRIPTION_WORD_TIMESTAMPS=1` gesetzt und liefert der Dienst Wortzeiten, markiert der Schreibplatz beim Abspielen das gesprochene Wort.
- Optional: [`deployment/gpu-asr-service`](deployment/gpu-asr-service) ist ein OpenAI-kompatibler Whisper-Dienst auf OpenVINO für Intel-Arc-GPUs. Er ist nicht die Standardeinstellung.

### Arbeitsliste und Zuweisung

- Die „Arbeitsliste“ ordnet Diktate nach Schreibkräfte-Pool und Autor. Sie zeigt Zähler für „Offen“, „In Bearbeitung“ und „Abgeschlossen“ und bietet Filter, Sortierung und Tastaturnavigation. Prioritäten reichen von „Bei Gelegenheit“ bis „Dringend“.
- Ein Diktat lässt sich namentlich zuweisen. Die Zuweisungsauswahl erscheint nur, wenn der Server eine Kandidatenliste liefert.
- Bearbeiten setzt eine exklusive, zeitlich begrenzte Übernahme voraus. Speichern aus einem veralteten Bearbeitungsstand wird abgewiesen. Die Bearbeitung lässt sich an den Schreibdienst zurückgeben.
- Autorenpools und Schreibkräfte-Pools sind getrennt.

### Schreibplatz: Abhören, Korrigieren, Versionen

- Wiedergabe mit 0,5- bis 2-facher Geschwindigkeit, Lautstärkeausgleich nur für die Wiedergabe (das Original bleibt unverändert) und Wortmarkierung mit Mitverfolgen.
- Fusspedale im Tastaturmodus: Die Funktions- oder Medientasten, die ein Pedal sendet, lassen sich Wiedergabeaktionen zuordnen (Abspielen/Pause, Abspielen bei gedrückter Taste, Zurück, Vor). WebHID wird nicht verwendet.
- Korrektureditor mit Rückgängig und Wiederholen, Suchen und Ersetzen, Schriftgrösse, Vollbild und Tastaturhilfe. Ohne Erkennungstext lässt sich manuell schreiben.
- Speichern setzt die aktuelle Übernahme voraus; die Verknüpfungen zwischen Text und Audio bleiben erhalten.
- Nicht destruktive Audiobearbeitung (Einfügen, Überschreiben, Ausschneiden, Kürzen) mit mindestens 16 Rückgängig-Schritten.
- Textrevisionen werden nur angehängt, nie überschrieben. Ein späteres Erkennungsergebnis wird als Nebenrevision gespeichert und ersetzt keine menschliche Korrektur. „Verlauf“ zeigt die Reihenfolge der Änderungen.

### Prüfen, Freigeben, Archiv

- „Prüfen und Freigeben“ zeigt die geprüfte Text-, Audio- und Kontextrevision. Eine Freigabe bindet genau diese Fassung. Ändert sich danach etwas, ist die Fassung erneut abzunehmen.
- Freigeben erfordert eine eigene Berechtigung. Freigeben, Fertigstellen und Archivieren sind getrennte Schritte.
- Das „Archiv“ listet abgeschlossene Diktate.
- Zum Stand der Dokumentausgabe siehe [Stand der Ablösung](#stand-der-ablösung).

### Rollen und Sichtbereiche

- Der Zugriff wird zentral entschieden. Diktierende sehen, hören und bearbeiten standardmässig nur eigene Diktate.
- Jede weitere Aktion braucht eine ausdrückliche Berechtigung mit Geltungsbereich: eigene Diktate, bestimmte Diktierende, ein Autorenpool oder alle. Ausdrückliche Verweigerungen gehen immer vor.
- Berechtigungen ändern nur Administratoren; Identitätsadministratoren und alle anderen Konten können das nicht. Jede Änderung wird protokolliert. Routen ohne Eintrag in der Routenrichtlinie sind im Klinikmodus gesperrt.
- Verwaltungsseiten erscheinen nur, wenn die API die Berechtigung bestätigt.

### Optional

- **Windows-Dateiimport** ([`apps/windows-import`](apps/windows-import/README.md)): Ein .NET-10-Agent lädt Diktatdateien aus ausdrücklich freigegebenen Ordnern oder Wechseldatenträgern unverändert über HTTPS mit Client-Zertifikat (mTLS) hoch. Er nimmt nicht auf, spielt nicht ab, bearbeitet keinen Text und programmiert keine Geräte. Ein Autorenhinweis aus dem DS2-Dateikopf gewährt keine Rechte.
- **Zusammenfassungen (in Prüfung):** Automatische Titel und Zusammenfassungen entstehen nur, wenn ein Textmodell konfiguriert ist; ohne Textmodell bleiben sie aus. Dabei wird der Transkripttext an dieses Modell gesendet, und der Klinikmodus prüft dessen Adresse auf diesem Weg nicht. Verwenden Sie ausschliesslich ein lokales Textmodell.

## Stand der Ablösung

Die Projektspezifikation ([`clinic-spec/CLAUDE.md`](clinic-spec/CLAUDE.md)) verlangt alle 101 Pflichtfunktionen aus dem Funktionsumfang des Originals sowie weitere Klinik-, Identitäts- und Grenzanforderungen. Keine Pflichtfunktion ist gestrichen; offene Punkte bleiben Anforderungen. 13 Teilklauseln kollidieren mit der reinen Webarchitektur und sind offen. Eine 1:1-Gleichwertigkeit mit dem Original wird nicht behauptet, und der vollständige Vergleich steht noch aus. Die zugehörigen Register sind unter [Nachweise und Register](#nachweise-und-register) verlinkt.

### Belegt

Im Testbetrieb mit synthetischen Benutzerabläufen:

- Aufnahmen und Uploads im Webclient sowie Transkription aufgenommener Dateien im Hintergrund
- lokale Spracherkennung, geprüft an einzelnen synthetischen Aufnahmen mit Wortzeiten
- Aufnahmenliste und Arbeitsliste
- Abhören und Schreiben im Schreibplatz
- Zuweisung über Schreibkräfte-Pools und Rückgabe an den Schreibdienst
- Prüfen und Freigeben
- Archiv

### Teilweise umgesetzt

- **Windows-Dateiimport:** mit einem simulierten Laufwerk getestet; der DS2-Autor wird aus der Originaldatei gelesen. Die Abnahme mit physischen Geräten fehlt.
- **Dokumentausgabe im Prüf- und Freigabeweg:** Der Export liefert derzeit Status, Dateiname und Hash. Das Herunterladen der fertigen Datei fehlt noch.
- **Vorlagen:** DOCX- und ODT-Vorlagen lassen sich verwalten, und Benutzende wählen eine Vorlagenversion aus. Diese Auswahl ist kein Einfügen von Textbausteinen. Das Einsetzen des Diktattexts in die Vorlage auf diesem Ausgabeweg fehlt noch.

### Offen

Fehlende Softwarefunktionen:

- fortlaufendes Live-Diktat über das Browsermikrofon mit laufenden Zwischenergebnissen (noch nicht durchgehend angebunden)
- sprachgesteuerter Aufnahmestart (VOX) im Browser
- erneutes Laden eines exportierten Pakets aus Audio und Text als bearbeitbare Sitzung
- Offline-Wiederherstellung im normalen Webclient
- Vorlesen per synthetischer Sprachausgabe (TTS)
- Schreiben des Geräteautors aus dem Web auf Philips-Geräte (offene Webgrenze, nicht umgesetzt)

Abnahme in realer Umgebung:

- physische Philips-Geräte LFH9600 und LFH9620, einschliesslich eines Gerätetests für das Schreiben des Autors
- echtes Fusspedal
- Partner-KIS (Krankenhausinformationssystem); die KIS-Prüfung verwendet derzeit einen Simulator
- produktive Anmeldedienste

Verhaltenslücken:

- Desktop-Diktat in andere Anwendungen und SpeechLock
- Einfügen in bereits geöffnete Word- oder Writer-Dokumente
- globale Tastenkürzel und Symbol im Infobereich (Tray)
- bestätigtes Training akustischer Profile aus Korrekturen

## Nachweise und Register

Die öffentlichen Projektregister beschreiben Anforderungen, ihre Zuordnung und die vorgesehenen Tests. Sie sind keine Testprotokolle und deutschsprachig. Ihre Statusfelder (`not_started`, `not_run`) geben den Stand der Spezifikation vom 17.11.2026 wieder, nicht den aktuellen Umsetzungsstand; diesen fasst [Stand der Ablösung](#stand-der-ablösung) zusammen.

- [Alle 101 Pflichtfunktionen](clinic-spec/docs/20_Alle_101_Pflichtfunktionen.md): Sollverhalten jeder Pflichtfunktion (PAR)
- [PAR-Paritätsmatrix](clinic-spec/planning/parity-matrix.csv) (auch als [JSON](clinic-spec/planning/parity-matrix.json)): Zuordnung jeder Pflichtfunktion zu Modulen, Wiederverwendung und Tests
- [Parität und Webgrenze](clinic-spec/docs/11_Paritaet_und_Webgrenze.md) und [offene Webgrenzen](clinic-spec/planning/web-parity-gaps.json): die 13 offenen Teilklauseln
- [Projektspezifikation](clinic-spec/CLAUDE.md): verbindlicher Entwicklungsauftrag
- [Benutzerhandbuch](https://joehomeskillet.github.io/speakr/de/) (auch auf [Englisch](https://joehomeskillet.github.io/speakr/)): Bedienung der Klinikversion

## Erste Schritte

Ausgangspunkt ist der Branch `main` mit der veröffentlichten Klinikversion. `config/docker-compose.example.yml` bezieht das Upstream-Image `learnedmachine/speakr` von Docker Hub; es enthält keinen Klinik-Build. Eine Compose-Datei für den Klinik-Stack liegt nicht im Repository.

### Voraussetzungen

- Git und Docker; der Image-Build ist für `linux/amd64` ausgelegt, weil der Prüfwert des DSS/DS2-Decoders nur für diese Architektur gilt
- ein OpenAI-kompatibler Spracherkennungsdienst unter einer lokalen oder privaten Adresse, zum Beispiel Speaches
- für die Tests ohne Docker: Python 3.11 und ffmpeg unter Linux sowie Node.js 18 oder ab 20 für die JavaScript-Tests

### Image bauen und starten

```bash
git clone -b main https://github.com/joehomeskillet/speakr.git
cd speakr
docker build --platform linux/amd64 -t speakr-clinic .
```

Das [`Dockerfile`](Dockerfile) installiert die Abhängigkeiten aus den Lock-Dateien mit Hash-Prüfung und baut den DSS/DS2-Decoder sowie das CSS. Mit `--build-arg LIGHTWEIGHT=1` entsteht ein Image ohne die Embedding-Abhängigkeiten.

Legen Sie die Umgebungsdatei ausserhalb des geklonten Verzeichnisses an, zum Beispiel als `../speakr-clinic.env`. Der Build kopiert das ganze Verzeichnis ins Image und schliesst `.env` nicht aus. Für den Klinikmodus genügen zunächst diese Werte; weitere Optionen der Spracherkennung beschreibt [`config/env.transcription.example`](config/env.transcription.example):

```dotenv
CLINIC_MODE=1
TRANSCRIPTION_BASE_URL=http://speaches:8000/v1
TRANSCRIPTION_API_KEY=not-needed-for-local
TRANSCRIPTION_MODEL=<Modellname Ihres Dienstes>
TRANSCRIPTION_WORD_TIMESTAMPS=1
```

Der Host in `TRANSCRIPTION_BASE_URL` muss aus dem Container erreichbar sein. Im Beispiel läuft der Spracherkennungsdienst als Container `speaches` in einem gemeinsamen Docker-Netzwerk. Legen Sie es bei Bedarf mit `docker network create <netzwerk>` an und verbinden Sie den Dienst mit `docker network connect <netzwerk> speaches`. Ohne `SQLALCHEMY_DATABASE_URI` verwendet das Image SQLite unter `/data/instance`; die Projektspezifikation sieht PostgreSQL als führende Datenbank vor.

```bash
docker run -d --name speakr-clinic --network <netzwerk> -p 8899:8899 \
  --env-file ../speakr-clinic.env \
  -v "$PWD/uploads:/data/uploads" -v "$PWD/instance:/data/instance" \
  speakr-clinic
```

Das erste Administratorkonto wird mit einem eigenen Skript angelegt:

```bash
docker exec -it speakr-clinic python scripts/clinic_bootstrap_admin.py --username <benutzername> --email <e-mail-adresse>
```

Das Passwort (15 bis 128 Zeichen) wird verdeckt abgefragt oder aus `CLINIC_BOOTSTRAP_PASSWORD` beziehungsweise `--password-stdin` gelesen, nie als Befehlszeilenargument. Ein zweiter Aufruf ändert nichts. Das Konto erhält Verwaltungs- und Identitätsrechte, aber keine klinische Rolle. Legen Sie Konten und Berechtigungen für Diktierende und Schreibkräfte anschliessend in der Verwaltung an. Die Anwendung ist unter `http://localhost:8899` erreichbar.

## Mitwirken

Beiträge sind willkommen. Die laufende Entwicklung findet auf dem Branch `dev` statt: Zweigen Sie von `dev` ab und eröffnen Sie Pull Requests auf `dev`.

### Entwicklungsumgebung und Tests

```bash
python3.11 -m venv venv
venv/bin/pip install --require-hashes --no-deps -r requirements.lock -r requirements-dev.lock
scripts/build_css.sh
venv/bin/python -m pytest tests/ -q -n 4 --dist loadfile -p no:cacheprovider --timeout=120
venv/bin/python -m pytest tests/clinic -q -p no:cacheprovider
npm ci
npm test
```

- `scripts/build_css.sh` lädt die gepinnte Tailwind-CLI, prüft ihren Hash und erzeugt `static/css/tailwind.css`.
- `scripts/clinic_pg_run.sh` führt `tests/clinic` gegen einen temporären PostgreSQL-Container aus (Docker erforderlich).
- Der Windows-Importagent wird unter Windows mit dem .NET-10-SDK getestet: `dotnet test apps/windows-import/Speakr.Clinic.ImportAgent.sln`.
- Bekanntes Problem: `tests/test_audit_log_coverage.py::test_no_marker_reaches_a_row_or_a_fallback_line` kann im parallelen Lauf reihenfolgeabhängig fehlschlagen und besteht einzeln.

### Regeln

- Legen Sie neue Fachlogik unter `src/clinic/` und Tests unter `tests/clinic/` ab. Behalten Sie den bestehenden Stack bei (Flask, SQLAlchemy, Vue, Tailwind) und nutzen Sie für Hintergrundarbeit `ProcessingJob` und `JobQueue`.
- Schwächen Sie bestehende Tests nie ab, um einen Lauf grün zu machen.
- Spracherkennung bleibt lokal. Kein nativer Diktatclient, keine Browsererweiterung.
- Verwenden Sie in Tests, Fixtures, Screenshots und Issues ausschliesslich synthetische Daten.
- Markieren Sie offene Punkte in [`clinic-spec/planning/web-parity-gaps.json`](clinic-spec/planning/web-parity-gaps.json) nicht als erledigt.

### Lizenz von Beiträgen

Beiträge stehen unter der GNU AGPL v3, der Lizenz dieses Repositorys. Wer eine geänderte Fassung über ein Netzwerk für andere bereitstellt, muss deren Nutzenden den Quellcode dieser Fassung anbieten (Abschnitt 13 der [Lizenz](LICENSE)). Bestehende Urheber- und Lizenzhinweise bleiben erhalten.

[`CONTRIBUTING.md`](CONTRIBUTING.md) und [`CLA.md`](CLA.md) stammen unverändert aus dem Upstream-Projekt. Sie beschreiben dessen Beitragsprozess und eine Contributor License Agreement für die dortige Doppellizenzierung. Aus dem Upstream-Projekt stammen auch die Workflows unter `.github/workflows`, darunter eine automatische CLA-Erinnerung zu Pull Requests.

## Herkunft und Lizenz

- Dieses Repository ist ein Fork von [Speakr](https://github.com/murtaza-nasir/speakr) (Originalprojekt, Upstream), Version v0.11.1, Upstream-Commit `48d3018fb7d80b77f897bedd86bcc89b5034d1f5`. Aufnahme, Transkriptionsanbindung, Verarbeitungswarteschlange, Benutzerverwaltung und Oberfläche bauen auf der Arbeit des Speakr-Projekts auf. Einzelne Upstream-Funktionen hat der Fork entfernt (siehe [Funktionen](#funktionen)).
- Fork: [github.com/joehomeskillet/speakr](https://github.com/joehomeskillet/speakr), Branch `main` (veröffentlichte Klinikversion) und Branch `dev` (laufende Entwicklung).
- Lizenz: GNU Affero General Public License, Version 3 (AGPL-3.0), siehe [`LICENSE`](LICENSE).
- Das Upstream-README beschreibt zusätzlich eine kommerzielle Lizenz. Diese kann nur das Upstream-Projekt vergeben; dieser Fork bietet keine andere Lizenz als die AGPL-3.0 an.
- Badges, Docker-Hub-Image, Releases, die Dokumentationswebsite des Upstream-Projekts und CI-Workflows (`.github/workflows`, `.forgejo`) gehören zum Upstream-Projekt und beschreiben nicht diesen Fork. Das Verzeichnis `docs/` enthält überwiegend die Upstream-Dokumentation; das Benutzerhandbuch dieses Forks ist oben verlinkt.
- Die DSS/DS2-Dekodierung nutzt [dss-codec](https://github.com/hirparak/dss-codec), im Dockerfile auf einen festen Commit gepinnt.
- dns.comfort und dns.comfort.pro sind Produkte ihres Herstellers. Die Namen beschreiben hier nur das Funktionsziel.
