# Speakr V8: geprüfte Änderungen im Klinik-Webclient

## P-141 – ASR-Wartezeit (PAR-ASR-003)

Die Klinik-API erlaubt berechtigten Bearbeitern nach Ablauf der Profilfrist den Wechsel von In Vorbereitung zu Zu schreiben. Ein lokaler synthetischer API-/DB-Test prüft eine verspätete ASR-Antwort als Nebenversion bei vorhandenem menschlichem Textkopf. Staging und Referenzvergleich bleiben offen.

## Implementierung P-084

Versionierte Importabsichten werden pro Datei in den bestehenden Klinik-Import übernommen. Synthetische HTTP-/DB-Tests prüfen Metadaten, Poolwahl, Empfänger, die drei ASR-Modi und den unveränderten nicht ausgewählten Inventareintrag. Staging- und Philips-Referenztests für BT-IMP-003 stehen noch aus.

## P-216 – Lokale Inline-Grafiken

Im Klinikbetrieb nimmt die Flask-API validierte PNGs pro Diktat auf und liefert sie nur nach erneuter Zugriffskontrolle über eine unveränderliche Inhalts-ID aus. Kein Abruf externer Quellen, keine aktive Grafik und keine Bildanhänge. Editoranschluss, Staging-Prüfung und BT-LIVE-005 bleiben offen.

## Q-05 vom 27. November 2026 – KIS nur lesend (X-KIS-1)

Die Freigabe schliesst das Diktat in Speakr ab. Dateiausgabe und Zwischenablage bleiben verfügbar. Der Klinikbetrieb bietet keinen KIS-Versand an. Bestehende Zustellbelege und Audit-Ereignisse bleiben erhalten. Der synthetische MockKIS-Kompatibilitätspfad erfordert eine explizite Aktivierung im Staging-Profil.

Auftraggeberabweichung, Status `not_run`: finale KIS-Signatur und KIS-Weiterverarbeitung aus PAR-WF-011 / BT-WF-011 und R-019 / T-019, KIS-Zustellung und Zustellquittung aus PAR-EXP-001 / BT-EXP-001 und R-028–029 / T-028–029 sowie KIS-Neuausstellung und KIS-Nachtrag aus PAR-WF-012 / BT-WF-012 und R-030 / T-030. P-078 und weitere KIS-Zustellpakete entfallen gemäss Q-05. Die zulässigen Teile dieser Anforderungen behalten ihren Abnahmeumfang.

## KI-Zusammenfassung im Diktat

Auf Staging `f6b7b0a2` fehlte der Zugang zur gespeicherten Zusammenfassung sowohl im Schreibplatz als auch in den Diktatdetails. Ein regulär angemeldetes QA-Konto bestätigte das für ein synthetisches Diktat bei 390 und 1440 Pixeln. Der ursprüngliche Summary-Worker konnte in einer getrennten Testdatenbank über das lokale Modell eine Zusammenfassung erzeugen, speichern und erneut lesen. Beide vorgegebenen Fakten und der unveränderte Ausgangstext wurden geprüft.

Ein synthetischer Autor meldete sich im Browser an und forderte eine Zusammenfassung über den neuen Klinik-Endpunkt an. Der reguläre Worker verarbeitete den korrigierten Text mit dem lokalen Modell. Das Ergebnis enthielt Luzern und Freitag aus der Korrektur und keine der überholten Angaben Basel und Montag. Es war nach dem Neuladen wieder abrufbar. Beide Textrevisionen und die Aufnahme blieben unverändert. Der Test verwendete eine von der vorherigen Version übernommene PostgreSQL-16.4-Datenbank.

## Öffnen, Anhören und Wiedererkennen auf Staging 0dc67654

Nach einem einzigen normalen Neuladen im bereits geöffneten, angemeldeten Handy-Browsertab lieferte die Verwaltung die neue CSS-Kennung. Der eingeklappte Rollenfilter war tatsächlich unsichtbar. Die Aufnahmenansicht zeigt beim Einstieg Suche, Status, Zeitraum, Pool und Autor. Bei 390 Pixeln lag die erste vollständige Karte zwischen y=687 und y=829. Per Touch öffnet der Titel das Diktat. Die Arbeitsliste behält einen separaten Bereich zum Auswählen. Bei 1440 Pixeln begann die erste Aufnahme bei y=433.

„Erneut transkribieren“ im Schreibplatz startete für den ersten QA-Fall den echten lokalen Auftrag 68. Der Auftrag wurde abgeschlossen und als „protected“ quittiert. Nach erneutem Öffnen war die menschliche Fassung bytegleich. Ein zweites QA-Diktat bestand aus zwei neutralen gesprochenen Sätzen. Auftrag 73 ergab wieder denselben Text und die Quittung „unchanged“. Es gab danach eine ASR-Fassung und keine menschliche Fassung. Der registrierte lokale Integrationstest prüft den zusätzlichen Fall eines geänderten ASR-Ergebnisses ohne menschliche Korrektur sowie die genaue Bindung an die aktuelle Audioversion.

Bei der Archivierung zeigt die normale Archivfläche das gespeicherte Datum des QA-Testfalls. Die bestehende Kopfzeile des Schreibplatzes hält Zurück und die Diktatansichten beim Wechsel an derselben Stelle. Dadurch steht der Titel auf schmalen Geräten tiefer als im statischen HTML-Prototyp. Zwölf Browserprüfungen haben die sechs Ansichten und die Rückkehr zur Ausgangsliste bestätigt.

## Nachweis vom 20. November um 16:16 Uhr

Auf Staging öffnete ein normal angemeldetes QA-Konto die eigene Aufnahme per Mausklick bei 1440 Pixeln und per Touch bei 390 Pixeln. In der Arbeitsliste wählte der Titeldruck die passende Zeile. Die vollständige Kennung ließ sich getrennt aufklappen. Beim Abspielen von 9,505 Sekunden echtem Testaudio markierte der Schreibplatz zwei aufeinanderfolgende Wörter im Transkript bei 0,209 und 0,344 Sekunden. Der lokale GPU-Dienst lieferte für 22 von 24 Wörtern passende Zeiten.

Ein QA-Autor speicherte und öffnete seine erste manuelle Korrektur. In einem separaten Fall speicherte die zugewiesene Schreibkraft eine Änderung und gab die Bearbeitung an den Schreibdienst zurück. Dafür verwendete sie ein auf den synthetischen Testpool beschränktes Recht. Der technische Site-Administrator übernahm die Sperre im selben Testdiktat. Das Audit verzeichnete den Halterwechsel. Die temporären Fachrechte wurden nach dem Test widerrufen.

Die endgültige Freigabe eines weiteren synthetischen Diktats antwortete mit HTTP 201. Das KIS-Simulationssystem quittierte genau eine Zustellung. Die ursprünglich mit HTTP 404 fehlgeschlagene Archivierung wurde auf Staging 6cdbe10a erneut geprüft. P07 archivierte den bereits freigegebenen QA-Fall nach gesonderter Berechtigung mit HTTP 200. Nach dem Klick zeigte die Archivliste bei 390 und 1440 Pixeln das Diktat. Das temporäre Recht wurde widerrufen. In vier unabhängigen lokalen Prozessen überstand derselbe Ablauf sowohl einen absichtlich gescheiterten Versuch mit vollständigem Rollback als auch den Neustart nach erfolgreicher Archivierung.

User 1 besitzt seine bestehende Site-Admin-Sicht auf fremde Klinikdiktate einschließlich Wiedergabe. Die gesonderten Fachrechte für Dokumentexport und Freigabe gelten für eigene Diktate. Alle 13 bisherigen Diktate sind weiterhin User 1 zugeordnet. Die Browserbelege stammen von neu angelegten QA-Konten. Für die Aufnahme ohne decodierbare Samples fehlt ein intaktes Original. Der physische Windows-Import und die Abnahme mit realer Hardware stehen aus.

## Staging und Teststand am 20. November 2026

| Bereich | Stand und Prüfumfang |
| --- | --- |
| Aufnahmen und Arbeitsliste | c5890b54 enthält den Zeilenaufruf, die Tastaturkorrektur nach dem Zurücknavigieren und die bei 390 Pixeln vollständig sichtbare erste Karte. Browserläufe haben das Wiederöffnen 24-mal geprüft. |
| Schreibplatz und Verwaltung | Der Integrationsstand enthält die gemeinsame Diktatnavigation, das integrierte Verwaltungsmenü, namentliche Zuweisungen und getrennte persönliche und administrative Wortschatzansichten. Die Prüfung verwendete synthetische Inhalte. |
| Freigabe und Korrektur | Der Korrekturclient sendet If-Match. Die lokale Browserprüfung bestätigte die vollständige Freigabekette mit aktueller Audiofassung, Abnahmeregel und Sperre. |
| Verlässlichkeit | Zwei Prozessabbrüche prüften die Wiederaufnahme der Benachrichtigungen. Die Exportprüfung las eine Datei über einen zweiten Anwendungsprozess. |
| Vollständiger Projektlauf | Auf 86ddee26 bestanden 11.657 Python-Tests, 5.809 serielle Kliniktests, 1.942 Paketprüfungen und 2.337 JavaScript-Tests. Die Suite für die nachfolgenden Änderungen läuft separat. |

Auf b7c776ee erreicht eine regulär angemeldete QA-Autorin den Schreibplatz über die Aufnahmenkarte. Ein neues synthetisches Diktat wurde über HTTPS angenommen und im Browser auf dem öffentlichen Staging abgespielt. Die lokale Browserprüfung bestätigt den vollständigen Freigabeweg samt aktualisierter Fallbindung. Im Liveversuch scheiterte die Spracherkennung an einer vom lokalen Dienst zurückgewiesenen Modellkennung. Der erste manuelle Speichervorgang ohne vorhandene Textrevision lieferte HTTP 503. Für beide Fälle läuft eine gesonderte Prüfung.

Die Staging-Reparatur ergab zwölf abspielbare Audiodateien aus dreizehn übernommenen Diktaten. Das verbliebene DS2-Original enthält fünf leere Frames und ergibt beim Dekodieren null Audiosamples. Hardware-, externe KIS- und Produktionsabnahme sind offen.

## REST27 x20 gap4: explizite ASR-Wiederholung (Worktree, 20. November 2026)

Ein ausdrücklich gestarteter, weiterhin aktueller Repeat darf einen geänderten Maschinenstand als unveränderlichen ASR-Nachfolger speichern. Die Bindung an Ausgangsrevision, Audiofassung und Quellversuch sowie eine Ergebnisquittung liegen im bestehenden ProcessingJob. Recording-Quelle und Klinikanker zeigen denselben aktuellen Text. Duplikate, identische Ergebnisse und veraltete Versuche ersetzen ihn nicht; menschliche Korrekturen bleiben aktiv. Segmentlinks und Wortbindungen gehören zum neuen Textstand.

Fokussierter Lauf: **113 passed in 21.26s**, einschließlich echtem HTTP-Upload-/Repeat-/Queue-Pfad mit synthetischem lokalem Connector, veralteter Text-/Audiofassung, geändertem Quellversuch, neuerem Job, überholtem Worker mit geändertem Retry-Zähler oder Startzeit, menschlicher Korrektur während ASR und Ingest-Rollback. Befehle und Ausgaben: `.rest27-evidence/REPORT.md` und `.rest27-evidence/final-focused-tests.log` im Worktree `rest27-asr-successor`. Keine Aussage über ASR-Qualität oder PostgreSQL-Parallelität. Vollständige Projekt-Gates und Staging wurden nicht ausgeführt; Commit und Integration liegen beim Orchestrator.

## Staging mit regulären QA-Konten am 20. November

Auf afa9f9b3 hat der QA-Autor die erste manuelle Korrektur einer Aufnahme ohne ASR-Fassung im Schreibplatz gespeichert. Der Aufruf lieferte HTTP 200. Nach einem erneuten Öffnen in einer neuen Seite war derselbe Satz sichtbar. Die frühere 503 entstand, weil die erste menschliche Revision keine Referenz auf das vorhandene Original hatte.

Der zweite synthetische Upload durchlief die lokale GPU-Erkennung und wurde über die Oberfläche eingereicht. Die Klinik legte eine Textfassung mit 126 Zeichen und einer Segmentverknüpfung an. Die GPU lieferte im Produktpfad zunächst keine nutzbaren Wortzeiten. Die automatische Zusammenfassung ist für diesen Ablauf deaktiviert.

Ein über HTTPS angemeldeter Identitätsadministrator übernahm Staging-Konto 1 mit der bestehenden ID und 13 unverändert zugeordneten Diktaten. Das Konto besitzt jetzt die aktive lokale Klinikpolicy und einen expliziten Grant für Dokumentexport und Freigabe im eigenen Bestand. Die bestehende Site-Admin-Rolle hat die unter O4 vereinbarten Leserechte. Die Browserabnahme verwendet neue QA-Konten.

PR #2 wurde nach sechs grünen Projektprüfungen in clinic/main gemergt. Der Lauf auf 3475a94e bestand mit 11.664 Python-Tests, 5.816 seriellen Kliniktests, 1.942 Paketprüfungen und 2.352 JavaScript-Tests.

### Automatische Zusammenfassung neuer Klinikdiktate

Für neue Diktate kann die erste erfolgreiche lokale Transkription eine Zusammenfassung auslösen. Dazu muss in den Systemeinstellungen `disable_auto_summarization=false` gelten und der Autor die persönliche Option `auto_summarization` eingeschaltet haben. Die Klinik speichert die erste ASR-Fassung und deren Auftrag gemeinsam. Der bestehende Summary-Worker verarbeitet ihn mit dem freigegebenen lokalen Modell und den Objektrechten des Autors. Der Titel entsteht weiterhin im bisherigen Titelauftrag. Bestandsdiktate, erneute Erkennung und menschliche Korrekturen lösen keine nachträgliche Automatik aus. Ein berechtigter Nutzer kann die aktuelle Textfassung über die Diktatdetails weiterhin ausdrücklich zusammenfassen lassen.

## P-133 – Playerposition im Schreibplatz (PAR-COR-001)

Teilfunktion umgesetzt und lokal mit synthetischem Audio im Browser geprüft: Die Playerleiste lässt sich per Zeiger und Pfeiltasten im sichtbaren Editorbereich verschieben und zurücksetzen. Audioelement, laufende Wiedergabe, Textauswahl und Bearbeitungslease blieben auch beim Wechsel auf 390 px erhalten. Die Prüfung auf Staging und der unveränderte Originaltest BT-COR-001 mit Referenzvergleich stehen aus; keine Aussage zur vollständigen Parität.
