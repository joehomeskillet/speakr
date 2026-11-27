# Diktatdaten schützen

[English](../../admin-guide/privacy.md)

Diese Seite beschreibt den aktuellen webbasierten Klinik-Build mit aktiviertem `CLINIC_MODE`. Klinikprüfungen sind keine Produktionsfreigabe. Die ausgelieferte Entwicklungskonfiguration ist ausschliesslich für synthetische Daten vorgesehen; `allow_real_data`, `production_approved` und `retention_policy_approved` stehen auf `false`. Produktive Aufbewahrungs- und Wiederherstellungswerte bleiben offen. Mocks und Fixtures belegen weder Philips-Geräte noch Entra, KIS oder Fusspedale; die Web-Paritätslücken bleiben offen.

## Daten und Zugriff

Ein Diktat kann Originalaudio mit Hashquittung, ASR-Arbeitsmedien, Text- und Audiorevisionen, Kontextrevisionen, Zusammenfassungen, Freigaben, Workflowhistorie, Importquittungen und Ausgabedatensätze besitzen. Auch Inventar- und Übertragungsdatensätze eines Importagenten können eine Quelldatei identifizieren. Beziehen Sie alle diese Speicher und ihre Sicherungen in die lokale Zugriffsprüfung ein.

| Daten | Hauptspeicher |
|---|---|
| Unveränderliches Original und aufgenommene Audiosegmente | Lokales `CLINIC_ORIGINALS_ROOT`, standardmässig `UPLOAD_FOLDER` |
| Gewöhnliche Aufnahmemedien und ASR-Arbeitskopien | Lokales oder S3-kompatibles Aufnahmebackend |
| Vollständige Textrevisionen, Kontext, Zusammenfassungen, Freigaben und Quittungen | Datenbank |
| Importinventar, Übernahmezustand und übertragene Blöcke | Datenbank; übernommene Originale werden separat gespeichert |
| Stimmeinwilligung und aufgenommene Stimmvektoren | Datenbank; Einwilligungshistorie und Einwilligungsaudit sind vom Zugriffsprotokoll getrennt |
| Auditereignisse | `audit_event`; exportierte Kopien benötigen separaten Schutz |
| Konvertierungs-, Sitzungs- und Wellenformdaten | Lokale Zwischenablage-, Sitzungs- und Cacheverzeichnisse |

Die Anwendung selbst verschlüsselt diese Serverspeicher im Ruhezustand nicht. Schützen Sie Datenbankvolumes, Medien, temporäre Speicherorte, Sicherungen und Deployment-Geheimnisse. Personen mit Host- oder Datenbankrechten können über die Webberechtigungen hinaus auf Daten zugreifen. Siehe [Dateispeicher](storage.md) zur Unterscheidung von Originalen und Arbeitskopien.

Verwenden Sie möglichst enge Pool- und individuelle **Fachrechte**. Eine aktive Standortadministration besitzt auditierten Lese- und Wiedergabezugriff auf fremde Diktate. Bearbeiten, Freigeben, Originalaudioexport und Archivieren benötigen eigene Rechte. Identitätsadministration allein erteilt keinen Diktatzugriff; Poolleitung ist auf ihren gerouteten Bereich beschränkt. Ein Audiodownload benötigt ausdrücklich `audio_export`, auch für den Autor.

Die eigenen Admin-Transkript-/Audiorouten speichern ihren Zugriffsnachweis vor der Auslieferung und antworten bei einem Fehler mit `503 audit_unavailable`. Lassen Sie Audit- und Wiedergabeprotokollierung eingeschaltet. Ein auditierter Lesezugriff ist keine Berechtigung zum Bearbeiten oder Weiterverbreiten des Inhalts. Siehe [Auditprotokoll](audit-log.md).

## Verarbeitung bleibt lokal

Der Klinik-Fork bietet weder Cloud-ASR-Konnektoren noch Cloud-Fallback. Es bleiben `openai_whisper` für einen lokalen OpenAI-kompatiblen Dienst, `asr_endpoint` für einen lokalen ASR-Dienst und der synthetische `clinic_stub`. Die OpenAI-kompatible CPU-Adresse `TRANSCRIPTION_BASE_URL` und die optionale GPU-Adresse `CLINIC_GPU_ASR_BASE_URL` werden im Klinikmodus auf Lokalität geprüft. Der übernommene Pfad über `ASR_BASE_URL` besitzt keine entsprechende Lokalitätsprüfung; konfigurieren Sie dort ausschliesslich einen durch die Institution kontrollierten lokalen Host.

**KI-Zusammenfassung** gehört zur aktuellen Klinikoberfläche. Sie liest eine gespeicherte Textrevision, lässt den Diktattext unverändert und bindet ihr Ergebnis an diese Revision. Spätere Korrekturen können die Zusammenfassung veralten lassen. Die Erzeugung benötigt ein lokales Textmodell und die entsprechenden Lese-/Bearbeitungsrechte; für diese revisionsgebundene Zusammenfassung gibt es keinen gehosteten Fallback. **Modellwahl** speichert Modellkennungen, keine Dienst-URLs oder Zugangsdaten.

Für Textmodellfunktionen muss `TEXT_MODEL_BASE_URL` lokal sein; jede konfigurierte `CHAT_MODEL_BASE_URL`, `TRANSLATION_MODEL_BASE_URL` und `EMBEDDING_BASE_URL` muss ebenfalls lokal sein. Die Positivliste ist `CLINIC_LOCAL_MODEL_HOSTS`, standardmässig `speaches,host.docker.internal,localhost`; Loopback- und private IP-Adressen werden ebenfalls akzeptiert. Andere Dienstnamen benötigen einen ausdrücklichen Eintrag. Siehe [Modellwahl](model-configuration.md).

Diese Prüfung schützt nicht jeden übernommenen Hintergrundpfad. Der automatische Titelschritt und Zusammenfassungen für Aufnahmen ohne verknüpftes Klinikdiktat verwenden weiterhin den übernommenen Textmodellclient. `TEXT_MODEL_API_KEY` ohne lokale `TEXT_MODEL_BASE_URL` wählt einen gehosteten Standard. Konfigurieren Sie einen lokalen Textendpunkt oder lassen Sie beide Werte offen. `disable_auto_summarization` stoppt automatische Zusammenfassungen, nicht automatische Titel.

Lassen Sie `ENABLE_INQUIRE_MODE=false`: Die übernommene Transkriptindizierung wird nicht durch die Klinik-Routensperre geschützt. Ein konfigurierter Embedding-Endpunkt erhält Transkriptpassagen. Die Klinikoberfläche bietet weder Ambrose-Chat noch eine diktatübergreifende Ask-Ansicht. Auch dem optionalen Sprecherdienst unter `CLINIC_SPEAKER_SERVICE_URL` fehlt eine Lokalitätsprüfung; lassen Sie den Wert offen oder verwenden Sie einen geprüften lokalen Dienst.

Lokal bedeutet nicht transportverschlüsselt. Ein `http://`-Endpunkt überträgt Daten auf seinem Netzwerkpfad ohne TLS. Prüfen Sie tatsächliche Endpunkte, Netzwerkgrenzen und Zertifikate. Hotwords und Wortschatzeinstellungen sind Decoderhinweise, kein Nachweis für akustisches Training oder gemessene Erkennungsqualität.

## Browserspeicher und Offlinebetrieb

Der Klinikrekorder hält ausstehendes Audio im Arbeitsspeicher und speichert es nicht absichtlich in IndexedDB, `localStorage` oder `sessionStorage`. Schlägt das Speichern fehl, lassen Sie die Seite offen und verwenden Sie **Erneut sichern** oder **Erneut hochladen**, solange die Aufnahme oder ausgewählte Datei noch verfügbar ist. Beim Verlassen oder Neuladen der Seite sowie bei einem Browserabsturz geht eine ungesendete Aufnahme aus dem Arbeitsspeicher verloren. **Entwurf sichern** speichert auf dem Server. Die übernommenen Audio-Wiederherstellungsspeicher sind im Klinikmodus standardmässig gesperrt.

Das garantiert keinen Browser ohne Spuren. Oberflächeneinstellungen, Pedalkonfiguration und die ID des zuletzt gewählten Arbeitslistenfilters können bestehen bleiben. Der übernommene Uploadpfad für Dateiteile kann Dateiname, Grösse, Änderungszeit und Upload-Sitzungs-ID im Browserspeicher behalten. Downloads, Browsercaches und Betriebssystemkopien liegen ausserhalb der Kontrolle der Anwendung. Verwenden Sie synthetische Diktate für Screenshots und Support-Reproduktionen; vermeiden Sie klinische Inhalte in Kontonamen und Betriebsnotizen.

Der Service Worker speichert Anwendungsdateien unter `/static/`, keine authentifizierten Inhaltsantworten. Melden Sie sich an gemeinsam genutzten Computern ab und schliessen Sie andere Anwendungstabs; nehmen Sie nicht an, dass ein Inaktivitätsablauf jeden Browserspeicher oder den Zustand eines anderen Tabs löscht.

Die Clientklassifikation ist eine Serverentscheidung, keine Browserheader- oder User-Agent-Angabe. Aktuelle Anfragen verwenden das Standardprofil `managed_web_online`, da die vertrauenswürdige Ingress-Zuordnung nicht angeschlossen ist. Ein serverseitig als BYOD klassifiziertes Profil erlaubt weder persistentes Audio noch Bündel-Export; ein beliebiges Privatgerät wird von diesem Build aber nicht automatisch als BYOD erkannt.

Verwalteter Offlinespeicher ist vorbereitet, aber deaktiviert. `GET /api/clinic/v1/offline-web/capability` meldet `available: false`; kein Anbieter für Browserprüfung und Berechtigungen ist verbunden. **Offline und Wiederherstellung** bietet deshalb keine lokale Wiederherstellung. Bereits auf dem Server bestätigte Daten bleiben über die Arbeitsliste erreichbar.

## Identität, Zugangsdaten und optionale Stimmprofile

Verwenden Sie lokale Kontoerstellung und einmalige Wiederherstellungscodes über die [Benutzerverwaltung](user-management.md). Sitzungscookies sind `HttpOnly` und `SameSite=Lax`; ihr `Secure`-Flag benötigt `SESSION_COOKIE_SECURE=true`. Betreiben Sie die Anwendung hinter HTTPS und setzen Sie `session_idle_timeout_minutes` auf die lokal vereinbarte Inaktivitätsgrenze. Die 15 Minuten Inaktivität und 8 Stunden absolute Laufzeit im Entwicklungsvertrag ersetzen keine durchgesetzten Sitzungseinstellungen.

Der Klinik-Fork bietet weder Selbstregistrierung, E-Mail-Verifikation, Passwortrücksetzung per Mail, allgemeine API v1 noch persönliche API-Tokens. Tokenbasierter Maschinenzugang ist dem Importagent-Kanal vorbehalten. Tokens in Query-Parametern sind deaktiviert. Halten Sie Zugangsdaten aus URLs, Browserspeicher, Screenshots und Supportprotokollen fern; schützen Sie die Deployment-Geheimnisse des Importagenten und seinen vertrauenswürdigen mTLS-Ingress separat.

Stimmeinwilligung betrifft ausschliesslich die eigene Stimme der angemeldeten Person zur Autorenerkennung. Weder Administration noch Dienste dürfen für andere einwilligen oder Stimmen aufnehmen; Patienten werden über diese Funktion nicht registriert. Die Einwilligungshistorie speichert Erklärungshash und Status. Enrollment speichert Vektoren, nicht das temporäre Beispielaudio. Ein Widerruf entfernt die aufgenommenen Vektoren dieser Person in derselben Transaktion wie Widerruf und Einwilligungs-Auditeintrag; die Historie bleibt.

Benutzer verwalten ihre Einwilligung in **Einstellungen**; auch das Panel **Eigenes Stimmprofil (freiwillig)** unter **Sprachprofile** betrifft ausschliesslich die eigene Stimme der aktuellen Administration. Enrollment ist eine API-Fähigkeit, kein Beispielrekorder in der Klinikoberfläche. Die vorbereitete Autorenvorschlagskomponente belegt keinen funktionierenden Geräte- oder Sprecheridentifikationsablauf.

## Aufbewahrung, ausgehende Daten und Protokolle

Der Klinik-Fork bietet keine automatische endgültige Löschung. Der Aufbewahrungslauf archiviert Aufnahmezeilen und behält Medien; Klinikdiktate verwenden die ausdrückliche Archivaktion. Die tägliche Klinik-Auditbereinigung löscht keine Zeilen. Bestimmte Lebenszyklusbereinigungen für temporäre Uploads, eigentümerlose Aufnahmezeilen und widerrufene Stimmvektoren bestehen weiterhin. Legen Sie Aufbewahrungs- und Sicherungsverantwortung lokal fest; siehe [Aufbewahrung](retention.md).

Öffentliche Freigabelinks, interne Freigaben, Webhooks, Ordner, Inkognitomodus, Ereignis-/Kalenderextraktion und das What's-New-Popup werden vom Klinik-Fork nicht angeboten. Sie sind keine alternativen Wege zur Zustellung oder zum Schutz eines Diktats.

Workflowhinweise innerhalb der Anwendung bleiben dort. Optionale übernommene SMTP-Verarbeitungshinweise können Aufnahmetitel nach aussen tragen; lassen Sie SMTP unkonfiguriert, solange der Standort diese Verwendung nicht geprüft hat. Siehe [E-Mail einrichten](email-setup.md). Ein S3-Backend und konfigurierte Modelldienste sind ebenfalls Datenziele, nicht bloss technische Einstellungen. Vorbereitete KIS- und Identitätsprovider-Anbindungen belegen keine Freigabe für ein reales Fremdsystem.

Halten Sie `LOG_LEVEL` beim Umgang mit schützenswerten Daten auf `INFO` oder höher. Debugprotokollierung kann Prompts, Modellantworten, Namen und Providerfehlerdetails offenlegen. Die Positivlisten der Auditdetails bereinigen nicht jedes Anwendungs-, Proxy- oder Containerprotokoll. Schützen Sie diese Protokolle und exportierte Auditdateien, beschränken Sie den Zugriff und definieren Sie ihre Aufbewahrung ausserhalb der Anwendung.

## Betriebliche Prüfung

Prüfen Sie vor einem lokal freigegebenen Deployment lokale Modellendpunkte und TLS, enge Rollen und Poolrechte, sichere Cookies und Sitzungsgrenzen, Speichermounts und Verschlüsselung, unabhängigen Auditschutz und eine vollständige Wiederherstellung. Lassen Sie `ENABLE_INQUIRE_MODE`, `ENABLE_AUTO_PROCESSING` und `ENABLE_AUTO_EXPORT` aus sowie `PRESERVE_TEMP_AUDIO` ungesetzt. Diese übernommenen Hintergrundmechanismen sind kein Klinik-Freigabeverfahren.

Halten Sie offene Nachweise ausdrücklich fest. Lücken der Lokalitätsprüfung, Browserprofilzuordnung, deaktivierter verwalteter Offlinespeicher, produktive Aufbewahrungs-/Wiederherstellungsfreigabe und das offene Web-Paritätsregister bleiben Grenzen. Ein grüner synthetischer Test schliesst sie nicht.
