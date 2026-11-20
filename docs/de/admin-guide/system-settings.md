# Systemeinstellungen

[English](../../admin-guide/system-settings.md)

Öffnen Sie als Site-Administrator **System → Systemeinstellungen**. Halten Sie Ausgangswert und Geltungsbereich fest, speichern Sie eine Änderung und laden Sie die Seite zur Kontrolle neu. Wo unterstützt, hat der gespeicherte Datenbankwert Vorrang vor der Umgebungsvariable. Das Entfernen der Überschreibung stellt deren Wert wieder her. Reine Umgebungswerte werden beim Start eingelesen. Im [Auditprotokoll](audit-log.md) lässt sich eine Änderung über `admin.setting.changed` nachvollziehen.

| Aufgabe | Was Sie prüfen können |
|---|---|
| Speicher neuer Konten begrenzen | `default_storage_quota_mb` legt den Anfangswert fest. Bei bestehenden Konten ändern Sie das persönliche Limit in **Benutzer und Rollen**. |
| Abgewiesenen Upload untersuchen | Prüfen Sie Dateigrößenlimit, gegebenenfalls Videolimit und die Grenze des Reverse-Proxys. Eine größere Benutzerquote hebt das Anfragelimit nicht an. |
| Transkription einstellen | Prüfen Sie Zeitlimit und Anzahl der Wiederholungen am freigegebenen lokalen Connector. Unter **System → Modellwahl** ordnen Sie GPU-/CPU-ASR und Textmodell zu. Speichern Sie, laden Sie neu und prüfen Sie eine synthetische Aufnahme. |
| Automatische Zusammenfassungen steuern | Für die Erstellung nach erfolgreicher Erkennung müssen die Systemautomatik und die persönliche Zusammenfassungsoption des Autors aktiv sein. Die manuelle Aktion verwendet den zuletzt gespeicherten Text und das zugelassene lokale Textmodell. |
| Aufbewahrung ändern | **Automatische Archivierung** und **Archivieren nach Tagen** steuern den Aufbewahrungslauf für Aufnahmen. Eine Archivierung erhält die Daten. Diktatarchiv im Fachworkflow und Aufbewahrung von Auditzeilen folgen eigenen Regeln. |
| Anmeldung und Hinweise anpassen | Prüfen Sie Anmeldesperre, Inaktivitätsgrenze, Aufnahme- und Uploadhinweis, Zeitzone und Standarddarstellung vor einer standortweiten Änderung. |

## Videoeingang und Richtlinien

`VIDEO_PASSTHROUGH_ASR` ist standardmäßig ausgeschaltet. Im vorhandenen Video-Uploadweg gibt die aktivierte Option das Originalvideo ohne die übliche Audioextraktion an den Transkriptionsdienst weiter. Reine Audiodienste weisen Videos zurück. Schalten Sie die Option nur für einen geprüften, freigegebenen lokalen Dienst ein, der das Format akzeptiert. Reine Audio-Uploads sind davon unberührt. Die Aufbewahrungsoption `VIDEO_RETENTION` ist unabhängig.

Die Klinikrichtlinie begrenzt die verfügbaren Schalter. Eine Aktivierung von `api_query_tokens_enabled` oder `sso_api_tokens_enabled` oberhalb der Richtlinie ergibt HTTP 403. Auch die übernommene Modellerkennung kann gesperrt sein. Verfügbare Aufgabenmodelle wählen Sie auf der Klinikseite für Modellwahl. Schalter für Gruppenfreigaben, Webhooks oder Löschung schalten gesperrte Upstream-Wege nicht frei.

[Originalanleitung von Speakr](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/system-settings.md).
