# Systemstatistik

[English](../../admin-guide/statistics.md)

Der Klinik-Fork besitzt getrennte Ansichten für Systemverbrauch und klinische Aggregate. Sie zählen unterschiedliche Objekte und verwenden unterschiedliche Zugriffsregeln.

| Ansicht | Ort | Zugriff und Verfügbarkeit |
| --- | --- | --- |
| **Systemstatistik** | **Verwaltung → System**, `/admin#/system/statistik` | Für die Administration verfügbar |
| **Aktivität und Audit → Statistik** | `/admin#/aktivitaet` | Vorbereitete klinische Aggregate; die Anwendung hat keinen betrieblichen Weg zur Rechteverwaltung, daher wird die Seite ohne Statistikrecht zurückgehalten |
| **Verarbeitung** | **Verwaltung → Betrieb**, `/admin#/verarbeitung` | Zustand von Verarbeitungsaufträgen und kontrollierte Wiederholung |

Identitätsadministration und Poolleitung allein öffnen die Systemstatistik nicht. Die Administrationsrolle allein erteilt keinen Zugriff auf die getrennten klinischen Aggregate.

## Zeitraum und Übersicht

Wählen Sie in **Systemstatistik** 7, 30 oder 90 Tage; der Standard ist 30. Der gewählte Zeitraum endet heute und wird mit dem gleich langen vorherigen Zeitraum verglichen. Die Implementierung verwendet das aktuelle Kalenderdatum des Servers (`date.today()`), während Aufnahmezeitstempel UTC verwenden. Gehen Sie nicht von Europe/Zurich-Grenzen in dieser Ansicht aus. Eine heute angelegte und morgen transkribierte Aufnahme zählt in Aufnahme- und Verbrauchssummen zu unterschiedlichen Tagen.

Die Übersicht zeigt Aufnahmeanzahl, transkribiertes Audio, Modellanfragen, geschätzte Kosten, aktive Benutzer und Speicher. Das Aktivitätsdiagramm trennt Uploads, Browseraufnahmen, zusammengeführte Aufnahmen und sonstige Quellen. Der Zustand zeigt Aufnahmestatus, wartende und laufende Aufträge sowie letzte Fehler. Vergleichen Sie einen Fehlerprozentsatz mit der Zeilenanzahl, bevor Sie einen Ausschlag interpretieren.

**Dies sind Aufnahmezeilen, keine Klinikdiktatanzahlen.** Ein Klinikdiktat besitzt einen Aufnahmedatensatz als Anker sowie einen Quell- oder Arbeitsdatensatz. Die Systemansicht liest `Recording`, `TokenUsage` und `TranscriptionUsage`; Ankerzeilen können daher Aufnahmeanzahlen und Speichersummen erhöhen. Archivierte Anker- und Importarbeitszeilen beeinflussen auch die Archivanzahl. Der Speicherwert summiert erhaltene Aufnahme-Mediengrössen; er misst nicht jedes Byte im Originalspeicher, in Caches oder Sicherungen.

Die letzten fehlgeschlagenen Aufnahmen können Benutzernamen und unveränderten gespeicherten Fehlertext zeigen. Begrenzen Sie die Verteilung von Bildschirmaufnahmen oder exportierten Abbildungen. Die unten beschriebene Verarbeitungsansicht blendet Auftragsfehlerdetails dagegen aus.

## Verbrauch, Kosten und Limits

Die Modellaufteilung trennt Textmodellaufrufe, Embeddings und Transkription. Die Transkriptionsdauer wird in Tages- und Modellberechnungen auf Zehntelminuten gerundet; die Hauptanzeige und monatlichen Kontosummen verwenden ganze Minuten. Diese gerundeten Anzeigewerte sind nicht die zugrunde liegenden Budgetsekunden.

Kostenwerte sind Schätzungen, keine Rechnungen:

- Textmodell- und Embeddingkosten hängen von den Angaben des Modellservers ab; ohne gemeldete Kosten wird Null gezählt.
- Die Transkriptionspreistabelle setzt für `openai_whisper` $0.006 pro Minute an, auch bei einem lokalen Dienst wie Speaches. `asr_endpoint` und der synthetische `clinic_stub` erhalten diese Kostenschätzung nicht.
- `MODELS_ARE_LOCAL=true` ersetzt eine Nullkostenanzeige durch **Local**/**Lokal**; es beweist keine Endpunktlokalität und lässt eine von Null verschiedene Schätzung nicht verschwinden.
- Verbrauch hinter GPU-/CPU-Fallback wird dem Namen des CPU-Konnektors zugeordnet. Die Summen erkennen nicht, welches Ausführungsgerät verwendet wurde.
- Strom, Hardware und Personalkosten sind nicht enthalten.

Die Benutzertabelle zeigt für jedes Konto Aufnahme- und Speichersummen sowie Tokenverbrauch, Transkriptionszeit und geschätzte Kosten des aktuellen Kalendermonats. Diese Monatsspalten bleiben monatlich, auch wenn das Diagramm 90 Tage umfasst. Budgetbalken warnen ab 80% und erreichen bei 100% das Limit. Setzen Sie die Limits unter [Benutzerverwaltung](user-management.md): Speicher in MB, Tokens pro Monat und Transkription **in Sekunden** pro Monat. Prüfen Sie diese Grenzen, bevor Sie eine Verweigerung als unzureichende Systemkapazität diagnostizieren.

Ereignis-/Kalenderextraktion und Cloud-ASR-Konnektoren werden im Klinik-Fork nicht angeboten. Historische Verbrauchszeilen oder ursprüngliche Bezeichnungen aktivieren sie nicht erneut. Die verbliebene ursprüngliche Voice-Embedding-Diagnosekarte beweist keine verfügbare oder abgenommene Klinik-Sprecherregistrierung.

## Klinische Aggregate: vorbereitet, über normale Verwaltung nicht verfügbar

Der klinische Statistikcode zählt Klinikdiktate innerhalb ausdrücklicher Autoren- oder Autorenpoolrechte. Er leitet diese Rechte nicht aus Administration, Identitätsadministration, Poolleitung oder gewöhnlichen **Fachrechten** ab. Ein Statistikrecht erteilt weder Audio-, Transkript-, Export- noch Freigaberecht.

Der Rechtespeicher liegt im Arbeitsspeicher eines einzelnen Prozesses. Nur Tests rufen `grant_aggregate()` auf; die Anwendung bietet keine Seite, keinen Endpunkt und keinen Betreiberbefehl zur Einrichtung dieser Rechte. Sie würden weder einen Neustart überstehen noch automatisch einen anderen Prozess erreichen. Ohne Recht antwortet die Statistikprobe mit `403`, und die Verwaltung hält **Aktivität und Audit** zurück. Stellen Sie dies nicht als aktivierten Berichtsdienst dar.

Das vorbereitete Aggregationsverhalten ist:

- Ein Eintrag pro Klinikdiktat, gruppiert nach Status, Autor, Schreibkraft oder Zeitraum.
- Der Status unterscheidet eine aktuelle Freigabe, eine aktive Prüfsperre und sonstige offene Diktate; er entspricht nicht dem vollständigen Workflow-Statuskatalog.
- Zeitbasis ist Eingang oder Fertigstellung durch Freigabe. Eine Datumsgrenze bedeutet Mitternacht in **Europe/Zurich**; das Ende ist ausschliesslich. Zeitraumgruppierung unterstützt Tage, montags beginnende Wochen und Monate.
- Dauer verwendet erfasste Sekunden. Fehlende oder ungültige Dauern machen ein Ergebnis unvollständig. **Nicht erhoben** bedeutet nicht verfügbar, nicht Null.
- Ergebnisse enthalten IDs, Anzahlen und Dauern, keinen Transkripttext oder Audioinhalt.

Die Aggregatroute ist `GET /api/clinic/v1/statistics/aggregates`. Sie ist eine Klinik-Sitzungsroute, kein Zugriff über die entfernte allgemeine API v1. Die Oberfläche hat keinen CSV-Download. Vorbereiteter Exportcode ist ohne die fehlende Berechtigungseinbindung kein betrieblicher Exportdienst. Der Diktatverlauf bleibt aus Diktatansichten erreichbar, auch wenn diese Aggregatseite zurückgehalten wird.

## Verarbeitungszustand und Wiederholung

Öffnen Sie **Verarbeitung**, um Verarbeitungsaufträge zu prüfen. Der Zustandsendpunkt liest höchstens die neuesten 5000 Aufträge. Eine aktive Administration sieht alle Aufträge; andere angemeldete Benutzer sehen nur Aufträge von Diktaten, die sie ansehen dürfen. Die Antwort zeigt Kennungen, Typen, Zustände, Zeitstempel und bereinigte Fehlerklassen, keine Transkripte, Audiopfade, Anfrageparameter oder unveränderten Auftragsfehler.

Die Seite zeigt abgeschlossene, fehlgeschlagene und hängende Aufträge sowie letzte Fehler. Ein seit mehr als 1800 Sekunden laufender Auftrag gilt als hängend. Prüfen Sie den konfigurierten lokalen Dienst und Workerzustand; eine wachsende Warteschlange allein bestimmt die Ursache nicht.

**Erneut versuchen** benötigt eine aktive Administration sowie eine versionierte, CSRF-geschützte, idempotente Anfrage. Sie stellt einen neuen Auftrag ein. Eine Zustellung mit unbekanntem Ergebnis zeigt **Abgleich prüfen** und kann hier nicht wiederholt werden. Freigabe- und Zustellungsaufträge mit eigenem Operationsschlüssel benötigen ebenfalls ihren vorgesehenen Wiederherstellungsweg. Senden Sie eine unklare Zustellung nicht vor dem Abgleich erneut.

Die Seite bietet keinen nativen Tray-/Autostartbetrieb; WG-OPS-004 bleibt offen. [Lokale Spracherkennung](whisperx-setup.md) beschreibt lokale Dienstprüfungen, [Auditprotokoll](audit-log.md) die Auditabdeckung.
