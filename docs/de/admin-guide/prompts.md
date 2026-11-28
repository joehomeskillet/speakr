# Anweisungen für Zusammenfassung, Titel und Erkennung

[English](../../admin-guide/prompts.md)

Der Klinik-Build liefert deutsche Standardprompts für Zusammenfassungen und Aufnahmetitel. Der Upstream-Verwaltungsreiter **Standard-Prompts** und der Kontoreiter **Prompt-Optionen** sind im Klinikmodus ausgeblendet. Es gibt keinen ersetzenden Klinik-Prompteditor. Bereits gezielt konfigurierte Prompts können die ausgelieferten Standards weiterhin übersteuern.

## Was die Klinikstandards verlangen

Die Standards liegen in `src/clinic/prompts.py`. Eine im Klinikmodus angelegte Datenbank wird damit initialisiert. Ein ungesetzter Admin-Prompt oder einer, der noch dem Upstream-Standard entspricht, wird bei eingeschaltetem Klinikmodus zum Klinik-Prompt aufgelöst. Ein anderer gespeicherter Admin-Prompt bleibt erhalten.

Der Zusammenfassungsprompt verlangt eine strukturierte deutsche Zusammenfassung eines ärztlichen Diktats aus einer Suchtklinik. Er verwendet diese Überschriften in dieser Reihenfolge:

- **Anlass / Fragestellung**
- **Suchtanamnese**
- **Aktueller Befund / Psychischer Status**
- **Diagnosen**
- **Medikation / Entzugsbehandlung**
- **Procedere / Nächste Schritte**
- **Offene Punkte / Unklarheiten**

Er weist das Modell an, nur diktierte Informationen zu verwenden, nicht belegte Abschnitte wegzulassen, keine Diagnosen, Dosierungen oder Empfehlungen zu erfinden und Zahlen, Daten und Namen zu erhalten. Unklare Passagen sollen mit `«[bitte prüfen]»` gekennzeichnet werden. Dies sind Modellanweisungen, keine durch Code erzwungene Garantie sachlicher Richtigkeit.

Der Titelprompt lautet:

```text
- Titel auf Deutsch
- Höchstens 8 Wörter
- Keine Wendungen wie "Diktat zu" oder "Bericht über"
- Nur das Hauptthema nennen (z. B. Aufnahme, Verlauf, Entzug, Austritt)
```

Die Anweisung zur Ausgabesprache verwendet im Klinikmodus standardmässig Deutsch, sofern das Benutzerkonto keine andere Ausgabesprache festlegt. Ein generierter Titel und eine Zusammenfassung bleiben vom klinischen Transkript getrennt; keiner von beiden erlaubt dessen Umschreibung.

## Gespeicherte Prompts und Vorrang

Sichtbare Tags mit Prompts haben Vorrang vor persönlichen und administrativen Standards. Ihre Prompttexte werden in Tag-Reihenfolge verbunden. Ohne solchen Vorrang gilt ein persönlicher Prompt vor dem Admin-Standard, dieser wiederum vor dem ausgelieferten Standard. Ein höher priorisierter Prompt ersetzt die unteren Anweisungen; er übernimmt die Klinikschutzregeln nicht automatisch.

Ordner bietet der Klinik-Fork nicht an. In aktualisierten Datenbanken kann eine alte Ordnerzuordnung weiterhin zwischen Tag- und persönlicher Ebene einen Prompt liefern; bei Zusammenfassungen gilt dies nur bei der Erzeugung für den Aufnahmebesitzer. Prüfen Sie solche übernommenen Daten, statt Ordner als Klinikablauf einzurichten.

Für eine revisionsgebundene Zusammenfassung erfolgt die Promptauflösung bei einem automatischen Auftrag für die diktierende Person und bei einem manuellen Auftrag für die anfordernde Person. Vorhandene persönliche Prompts können daher je nach anfordernder Person unterschiedliche Anweisungen ergeben. Die Admin-Schlüssel `admin_default_summary_prompt` und `admin_default_title_prompt` werden weiterhin gelesen, sind aber in der allgemeinen Einstellungsliste ausgeblendet. Die verborgenen Upstream-Editoren sind kein unterstütztes Klinik-Konfigurationsverfahren.

Ein geänderter Prompt wirkt nur auf spätere Erzeugung. Er generiert vorhandene Titel oder Zusammenfassungen nicht neu. Prüfen Sie nach einer Aktualisierung vorhandene Tag-, persönliche oder administrative Vorgaben, bevor Sie vom ausgelieferten Standard ausgehen.

## Aktuelle Diktatzusammenfassungen

Die aktuelle Klinikoberfläche zeigt **KI-Zusammenfassung** im **Schreibplatz** und in den **Diktatdetails**. Diese Funktion ist integriert. Sie ist eine schreibgeschützte Zusammenfassung der gespeicherten Textrevision einschliesslich gespeicherter Korrekturen, kein Chatassistent und keine automatische Umschreibung des Diktats.

- Der Worker übergibt Revisionstext und aufgelöste Anweisungen an ein bereites lokales Modell. Er ergänzt keinen älteren Aufnahmekontextblock mit Daten, Tags, Profilfeldern oder Notizen.
- Die Eingabe wird durch `transcript_length_limit` begrenzt, die Ausgabe durch `SUMMARY_MAX_TOKENS`. Ausgabesprache des Kontos und Zusammenfassungstemperatur gelten.
- Lesen erfordert **Ansehen**; manuelle Erzeugung zusätzlich **Bearbeiten** und einen verwendbaren gespeicherten Text mit mindestens 10 Zeichen.
- Eine geeignete erste ASR-Revision kann eine automatische Zusammenfassung einreihen. Das Öffnen des Bereichs liest nur den Status. Spätere Korrekturen machen die bisherige Zusammenfassung veraltet und erzeugen sie nicht neu.
- Eine Antwort muss verwendbar und vollständig sein (`finish_reason=stop`). Der Worker verwirft ein Ergebnis, wenn sich Textrevision, Rechte oder erforderliche lokale Modellbedingungen vor dem Speichern geändert haben.

Wählen Sie **Textmodell** unter [Modellwahl](model-configuration.md). Diese Auswahl betrifft die Diktatzusammenfassungen, nicht die Titelerzeugung. Dieselbe Seite erklärt die Bedingungen automatischer Zusammenfassungen, Auditeinträge und Fehlermeldungen.

Vergleichen Sie die Zusammenfassung eines synthetischen Diktats mit der gesprochenen Quelle und dem gespeicherten Transkript. Achten Sie besonders auf Verneinungen, Dosierungen, Daten und Unsicherheitsmarkierungen. Flüssige Ausgabe allein belegt weder Richtigkeit noch klinische Eignung.

## Titel und ältere Aufnahmezusammenfassungen

Der separate Folgeauftrag erzeugt nach der Transkription den Aufnahmetitel. Bei Aufnahmen mit Klinikdiktat überspringt er die normale Zusammenfassung; die Klinikzusammenfassung hat ihren eigenen revisionsgebundenen Auftrag. Unverknüpfte Altaufnahmen können weiterhin den älteren Zusammenfassungspfad samt Aufnahmekontext verwenden. Das Ausschalten automatischer Zusammenfassungen schaltet die Titelerzeugung nicht aus.

Der ältere Titel-/Zusammenfassungspfad wendet die Lokalitätsprüfung des Workers für Revisionszusammenfassungen nicht an. Verwenden Sie eine explizite lokale `TEXT_MODEL_BASE_URL` für die Installation oder lassen Sie sowohl Textmodell-URL als auch Schlüssel ungesetzt. Ein Schlüssel allein kann einen Client mit dem gehosteten Upstream-Standard erzeugen. Siehe [Modellkonfiguration](model-configuration.md).

## Erkennungshinweise

Transkriptionshinweise betreffen die Spracherkennung, nicht die Zusammenfassung. Im Klinikmodus verwerfen Stapelaufträge Initialprompts und Hotwords, solange `CLINIC_ASR_PROMPTS_ENABLED` nicht aktiviert ist. Bei Aktivierung teilen sich Initialprompt und Hotwords eine Grenze von 55 Wörtern: Der Initialprompt bleibt zuerst erhalten. Der mitgelieferte GPU-Dienst benötigt zusätzlich seinen eigenen Schalter `ASR_PROMPTS_ENABLED`. Profilfähigkeiten und Dienstunterstützung bleiben erforderlich; ein Promptschalter belegt weder akustisches Training noch Erkennungsqualität.

Der Upstream-Schritt zur kontextuellen Sprecherbenennung liefert im Klinikmodus kein Ergebnis. Ereignis-/Kalenderextraktion bietet der Klinik-Fork nicht an.

[Lokale Spracherkennung](whisperx-setup.md) beschreibt Wortschatz, Profile und Betriebsprüfungen.

## Grenzen und Temperaturen

| Einstellung | Standard | Wirkung |
| --- | --- | --- |
| `disable_auto_summarization` | `false` | Stoppt automatische Zusammenfassungen bei `true`; Titelerzeugung bleibt separat |
| `transcript_length_limit` | `30000` | Maximale Eingabezeichen; `-1` bedeutet unbegrenzt |
| `SUMMARY_MAX_TOKENS` | `3000` | Ausgabegrenze für Zusammenfassungen |
| `TITLE_MAX_TOKENS` | `5000` | Ausgabegrenze für Titel |
| `CLINIC_LLM_TIMEOUT_SECONDS` / `CLINIC_LLM_MAX_RETRIES` | `120` / `2` | Klinikgrenzen je Aufruf |
| Zusammenfassungstemperatur | `0.5` | Gilt ohne gültige administrative Vorgabe |
| Titeltemperatur | `0.7` | Gilt ohne gültige administrative Vorgabe |

Temperaturvorgaben werden bei jeder Anfrage gelesen und müssen zwischen 0 und 2 liegen. Ihre Upstream-Verwaltungsrouten bestehen weiter, der Temperatureditor ist im Klinikmodus jedoch ausgeblendet. Das Speichern einer unterstützten Einstellung verwendet den Auditdienst; Promptinhalte werden nicht als alte/neue Werte im Auditprotokoll gespeichert.

[Originalhandbuch von Speakr in der archivierten Fassung](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/prompts.md)
