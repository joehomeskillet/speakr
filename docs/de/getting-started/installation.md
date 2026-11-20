# Klinikfassung installieren

[English](../../getting-started/installation.md)

Diese Anleitung baut den Fork für einen Testbetrieb auf einem einzelnen Host aus dem Quellcode. Wählen Sie den öffentlichen Branch `main` und notieren Sie den ausgegebenen Commit, damit sich der Stand erneut bauen lässt.

```sh
git clone --branch main --single-branch https://github.com/joehomeskillet/speakr.git
cd speakr
git rev-parse HEAD
docker build --build-arg PRODUCTION=1 -t speakr-clinic:local .
```

## Dienste vorbereiten

Verwenden Sie einen Linux-x86-64-Host mit Docker und Platz für Images, Audio, Sicherungen und Modellgewichte. Das mehrstufige [Dockerfile](https://github.com/joehomeskillet/speakr/blob/main/Dockerfile) lädt beim Build die festgelegten Python-Pakete, Frontend-Ressourcen, FFmpeg und den DSS-Decoder. Die Bezugsquellen müssen beim Bauen erreichbar sein. Die Modelle laufen in Ihrer eigenen Infrastruktur. Der Bau des Webimages stellt weder Modellgewichte noch einen Erkennungsdienst bereit.

Richten Sie ein privates Docker-Netz namens `clinic-services` ein und verbinden Sie Ihren lokalen ASR-Dienst damit. Das Beispiel setzt einen innerhalb des Netzes als `asr` erreichbaren Dienst auf Port 9000 mit Multipart-API `POST /asr` voraus. Tragen Sie Ihren tatsächlichen privaten Dienst und Port ein. Ein **lokaler**, OpenAI-kompatibler Server verwendet den [anderen Konnektor](../advanced/custom-connectors.md).

Legen Sie `~/.config/speakr-clinic/app.env` nur für das Betriebskonto lesbar an (Modus 0600). Für das ASR-Beispiel gelten folgende Werte:

```dotenv
CLINIC_MODE=true
TRANSCRIPTION_CONNECTOR=asr_endpoint
ASR_BASE_URL=http://asr:9000
```

Ergänzen Sie in der Datei einen eindeutigen, starken Zufallswert aus Ihrer Geheimnisverwaltung für `SECRET_KEY`. Er muss bei Neustarts und über Replikate hinweg stabil bleiben. Lassen Sie die Upstream-Variablen `ADMIN_USERNAME`, `ADMIN_EMAIL` und `ADMIN_PASSWORD` frei. Das erste Klinikkonto entsteht im folgenden Konsolenschritt. Für optionale Textgenerierung konfigurieren Sie separat die freigegebene lokale Modelladresse `TEXT_MODEL_BASE_URL` und den dort angebotenen `TEXT_MODEL_NAME`. Zugangsdaten für gehostete Anbieter gehören nicht in diese Konfiguration.

## Start und Aufruf

```sh
docker run -d --name speakr-clinic --network clinic-services \
  --env-file "$HOME/.config/speakr-clinic/app.env" \
  --publish 127.0.0.1:8899:8899 \
  --volume speakr-clinic-instance:/data/instance \
  --volume speakr-clinic-uploads:/data/uploads \
  speakr-clinic:local
```

Für diesen Versuch verwendet das Image standardmäßig SQLite im Instanz-Volume. Für PostgreSQL legen Sie vorab die Datenbank und deren Zugang an und hinterlegen die PostgreSQL-Verbindungszeichenfolge privat in `SQLALCHEMY_DATABASE_URI`. Sichern Sie diese Datenbank zusätzlich zum Upload-Volume. Das eingecheckte [Compose-Beispiel](https://github.com/joehomeskillet/speakr/blob/main/config/docker-compose.example.yml) verweist auf ein Upstream-Image. Wenn Sie es anpassen, verwenden Sie das hier gebaute Klinik-Image, die private Konfiguration und beide Volume-Einbindungen.

Öffnen Sie am Docker-Host `http://localhost:8899/login`. Für andere Rechner und Telefone stellen Sie vor dem privaten Anwendungsport einen Reverse-Proxy mit vertrauenswürdigem HTTPS bereit. Das Mikrofon auf einem entfernten Browser benötigt einen sicheren Browserkontext.

## Erstes Identitätsadministratorkonto

Führen Sie den folgenden Schritt einmal gegen dieselbe Anwendung und Datenbank aus. Ersetzen Sie Beispielkonto und Adresse vor dem Aufruf durch den gewählten technischen Kontakt.

```sh
docker exec -it -e SPEAKR_SKIP_BACKGROUND_TASKS=true speakr-clinic \
  python scripts/clinic_bootstrap_admin.py \
  --username clinic-admin --email admin@example.invalid
```

Geben Sie die Passphrase verdeckt im Terminal ein (15–128 Zeichen). Anmeldenamen haben 2–20 Zeichen. Eine bereits initialisierte Installation meldet, dass sie nichts geändert hat. Melden Sie sich unter `/login` an. Legen Sie über die Benutzerverwaltung ein separates Test-Autorenkonto an und vergeben Sie nur die Rechte für das [erste Diktat](../getting-started.md).

Prüfen Sie vor der Nutzung Wiedergabe, lokale Erkennung, erneutes Öffnen einer gespeicherten Korrektur, den Sichtbereich eines zweiten Kontos sowie eine Wiederherstellung von Datenbank und Audio in einer getrennten Instanz. Halten Sie Buildkonfiguration, Modellkennungen, Rechte und getesteten Commit bei der Sicherungsanleitung fest. Beiträge zum Fork gehen über `dev`.

## Videoeingang im gemeinsamen Importpfad

Lassen Sie `VIDEO_PASSTHROUGH_ASR=false` bei einem reinen Audiodienst gesetzt. Bei aktiviertem Schalter sendet der gemeinsame Importpfad das vollständige Originalvideo an den ASR-Dienst. Herkömmliche Audio-APIs lehnen nicht unterstützte Videocontainer ab. Das Klinikformular ist für Audiodateien vorgesehen. Prüfen Sie die Änderung mit synthetischem Material, bevor Sie Video für einen gesonderten Importweg aktivieren.

## Wartung und Aktualisierung

Terminieren Sie TLS am Proxy und veröffentlichen Sie am App-Container nur den privaten Zugang. Begrenzen Sie den Zugriff auf Konfiguration und Sicherungen. Verwenden Sie auf etwaigen Repliken denselben Sitzungsschlüssel. Datenbank-, Instanz- und Uploadpfade benötigen passende Rechte für den tatsächlichen Containerprozess. Audiodateien gehören auf einen dauerhaften Speicher.

Halten Sie vor einem Update laufenden Image-Digest und Quellcommit fest. Sichern Sie Datenbank und Audio konsistent und stellen Sie beides in einer getrennten Testumgebung wieder her. Bauen Sie den Kandidaten vom vorgesehenen `main`-Commit. Geben Sie ihm eine eigene Testdatenbank und eigene Volumes. Prüfen Sie Anmeldung, Wiedergabe, neuen Testentwurf, lokale Erkennung und gespeicherte Korrektur. Lassen Sie beim Wechsel aktive Aufträge abschließen und genau einen Queue-Eigentümer arbeiten. Bewahren Sie das alte Image und die passende Sicherung bis zur erfolgreichen Abnahme auf.

Messen Sie für die Kapazitätsplanung Aufnahmegrößen, Wachstum von Upload- und Datenbankspeicher sowie Wartezeit und Erkennungsdauer. Prüfen Sie typische gleichzeitige Last, bevor Sie Workerzahlen erhöhen. Siehe [Verarbeitungsaufträge](../advanced/job-queue-system.md) und [Speicherverwaltung](../admin-guide/storage.md).

Originalreferenz zu Upstream-Speakr: [archivierte Upstream-Seite](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/getting-started/installation.md).
