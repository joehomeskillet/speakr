# Diktate ohne Vektorindex finden

[English](../../admin-guide/vector-store.md)

Der Upstream-Vektorspeicher unterstützt mit Inquire die aufnahmeübergreifende semantische Suche. Der Klinik-Fork bietet weder diese Suchoberfläche noch eine Verwaltungsaktion zum erneuten Einbetten des Archivs an. Sein Reiter **Vektorspeicher** ist ausgeblendet, und keine Klinikseite nutzt den semantischen Upstream-Index.

## Diktate über Metadaten finden

Jede Liste wendet die Sichtbarkeitsregeln des angemeldeten Kontos an. Prüfen Sie Filter, Poolzuordnung und das Recht **Ansehen**, bevor Sie ein fehlendes Diktat als Suchproblem behandeln.

| Ansicht | Ort | Suche und Filter |
| --- | --- | --- |
| **Arbeitsliste** | `/#/arbeitsliste` | **Metadatensuche** und **Gespeicherte Filter** suchen oder filtern Metadaten wie Titel, Status, Priorität, diktierende Person, Pool und zugewiesene Person. Bis zu 20 gespeicherte Filter pro Benutzer und 20 Bedingungen pro Filter werden unterstützt; reguläre Ausdrücke werden abgewiesen. |
| **Aufnahmen** | `/#/aufnahmen` | Serverfilter wählen diktierende Person, Schreibkräfte-Pool, Aufnahmestatus und Zeitraum. Die Textsuche läuft nur über bereits geladene Zeilen, nicht über die gesamte Datenbank. |
| **Archiv** | `/#/archiv` | Schreibgeschützte Ansicht für den Workflow-Zustand **Zu archivieren**, mit Filtern für Zeitraum, diktierende Person und Schreibkräfte-Pool. Siehe [Aufbewahrung](retention.md). |

Diese Listen bieten keine semantische Suche über Transkripte. Innerhalb eines geöffneten Diktats arbeitet **Suchen und Ersetzen** auf dessen Text.

## Upstream-Indexierung ausgeschaltet lassen

Lassen Sie `ENABLE_INQUIRE_MODE` ungesetzt oder auf `false`, wie in `clinic-spec/config/upstream.env.example`. Die Einstellung steuert die automatische Indexierung nach der Verarbeitung und die Nachindexierung beim Start. Diese Hintergrundpfade erzwingen die Inquire-Funktionssperre des Klinikmodus nicht. Die Aktivierung der Indexierung ergänzt keine Klinik-Suchseite.

Verwenden Sie die Upstream-Migrations- und Einbettungswerkzeuge (`scripts/migrate_existing_recordings.py`, `scripts/migrate_docker.sh`) oder die aufnahmebezogene Aktion `process_chunks` nicht als Klinik-Verwaltungsverfahren. Nicht alle manuellen Indexierungspfade prüfen `ENABLE_INQUIRE_MODE` oder die Lokalität des Embedding-Endpunkts. Bei gesetzter `EMBEDDING_BASE_URL` kann indexierter Text dorthin gesendet werden; andernfalls kann ein installiertes lokales Embedding-Backend Vektoren erzeugen. Ein ausgeblendeter Reiter allein garantiert daher nicht, dass kein Index existiert.

Lassen Sie Einstellungen für Embedding-Dienste in einer Klinikinstallation weg, die diesen Upstream-Index nicht benötigt. Sicherungen einer aktualisierten Installation müssen dennoch vorhandene Transkriptabschnitte und Vektoren berücksichtigen; siehe [Speicher](storage.md) und [Datenschutz](privacy.md).

## Routensperren und Zusammenfassungen

Die Funktion `inquire` ist gesperrt, solange nicht alle konfigurierten Textmodell-Endpunkte die lokale Modellregel des Klinikmodus erfüllen. Auch bei lokalen Endpunkten wird eine Anfrage ohne Aufnahmeziel, einschliesslich der Ask-Seite, mit `403 clinic_feature_unsupported` abgewiesen. Verbliebene aufnahmebezogene Handler haben eigene Berechtigungsprüfungen. Dies ist kein unterstützter Klinik-Suchablauf; siehe [Ambrose](ambrose.md).

**KI-Zusammenfassung** verwendet die gespeicherte Textrevision eines einzelnen berechtigten Diktats. Sie benötigt ein lokales [Textmodell](model-configuration.md), keinen aufnahmeübergreifenden Vektorindex. Aktivieren Sie Inquire nicht, um Zusammenfassungen zu erhalten.

[Originalhandbuch von Speakr in der archivierten Fassung](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/vector-store.md)
