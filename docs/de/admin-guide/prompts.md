# Standard-Prompts
<small>[English](../../admin-guide/prompts.md)</small>

Diese Seite beschreibt die Prompts, die die Klinikversion an ein Textmodell (LLM) sendet: den Zusammenfassungs-Prompt und den Titel-Prompt, ihre Herkunft und wer sie ändern kann. Sie beschreibt zudem die Transkriptionshinweise, die einzelne Upstream-Prompt-Einstellungen an die Spracherkennung senden. Alles Folgende gilt nur, wenn `CLINIC_MODE` eingeschaltet ist (`1`, `true`, `yes` oder `on`).

In Upstream-Speakr bearbeitet die globale Administration den Standard-Zusammenfassungsprompt, den Standard-Titel-Prompt, die Standard-Transkriptionshinweise und die Temperaturen im Verwaltungsreiter **Standard-Prompts**. Im Klinikmodus wird dieser Reiter nicht angezeigt, und die Klinikversion hat keinen anderen Editor für diese Werte. Stattdessen liefert die Klinikversion deutsche Klinikvorgaben mit.

## Statusübersicht

| Element | Status in der Klinikversion |
|---|---|
| Verwaltungsreiter **Standard-Prompts** (Editor für den Zusammenfassungs-Prompt, **Standard-Transkriptionshinweise**, **Standard-Titel-Prompt**, Temperaturfelder, «Vollständige LLM-Prompt-Struktur anzeigen») | Nur Upstream: im Klinikmodus nicht angezeigt |
| Kontoreiter **Prompt-Optionen** (persönlicher Zusammenfassungs- und Titel-Prompt, Schalter **Auto-Zusammenfassen**) | Nur Upstream: im Klinikmodus nicht angezeigt |
| Mitgelieferter deutscher Klinik-Zusammenfassungs-Prompt und Klinik-Titel-Prompt | Verfügbar |
| Prompt-Rangfolge Tag > Ordner > Benutzer > Administrationsvorgabe > mitgelieferte Vorgabe | Verfügbar (Code); Administrations- und Benutzerebene ohne Klinik-Oberfläche |
| Prompt-Felder für Tags und Ordner unter **Tags & Ordner** auf der Kontoseite | Verfügbar (pro Benutzer, keine Administrationseinstellung) |
| Temperaturen (`/admin/llm-temperatures`) | Verfügbar nur als API, ohne Oberfläche |
| Transkriptionshinweise (Initial-Prompt, Hotwords) an die Spracherkennung | Standardmässig aus (`CLINIC_ASR_PROMPTS_ENABLED`) |
| Prompt für die kontextbezogene Sprecherbenennung | Nur Upstream: der Schritt liefert im Klinikmodus kein Ergebnis |
| Automatischer Standardtitel und automatische Standardzusammenfassung nach der Transkription | Standardmässig aus (kein LLM-Client, solange weder `TEXT_MODEL_BASE_URL` noch `TEXT_MODEL_API_KEY` gesetzt ist); Lokalitätsprüfung des Auftrags für automatischen Titel und automatische Zusammenfassung: Offene Lücke |
| Automatische lokale Diktatzusammenfassung mit dem Bereich **KI-Zusammenfassung** in **Schreibplatz** und **Diktatdetails** | In Prüfung |

## Haltung der Klinik zu generiertem Text

Der verbindliche Arbeitsauftrag des Forks verbietet generative klinische Umschreibung («keine generative klinische Umschreibung») und Cloud-Spracherkennung. Im Code ist eine Zusammenfassung immer ein eigener Text: Die Standardzusammenfassung wird in einem eigenen Feld der Aufnahme gespeichert, das Transkript bleibt unverändert. Eine Zusammenfassung wird nie in den Diktattext eingefügt und ersetzt ihn nie.

Das Entscheidungsregister (`clinic-spec/config/decisions.json`, Entscheid D21) führt weiterhin «No LLM rewrite/chat/summaries/voiceprints/RAG/public share/personal webhooks». Diese Datei ist als Entwicklungsvorgabe gekennzeichnet, nicht als klinische Freigabe. Der Code hält Chat, Inquire und generative Zusammenfassungen stattdessen nur mit lokalen Modellen verfügbar: Sie bleiben gesperrt, solange nicht alle konfigurierten Modell-Endpunkte (Text, Chat, Übersetzung, Embedding) lokal sind (`LOCAL_MODEL_FEATURES` in `src/clinic/config.py`). Der Schalter `features.generative_summary: false` in `clinic-spec/config/dev.defaults.json` entscheidet darüber nicht. Die beiden Positionen sind in der Spezifikation nicht bereinigt. Keine Zusammenfassungsfunktion des Forks hat eine klinische Freigabe, und die vorhandenen Testnachweise sind ausschliesslich synthetisch. Die Qualität der Zusammenfassungen wurde nicht gemessen.

## Mitgelieferte Klinik-Prompts

Bei eingeschaltetem Klinikmodus sind die mitgelieferten Vorgaben für Zusammenfassung und Titel deutsche Klinik-Prompts. Sie sind in `src/clinic/prompts.py` definiert ([Quelltext im Klinik-Fork](https://github.com/joehomeskillet/speakr/blob/main/src/clinic/prompts.py)); die Upstream-Vorgaben in `src/config/prompts.py` bleiben unverändert.

**Wann sie gelten.** Der Klinik-Prompt ersetzt die Administrationsvorgabe, wenn deren Wert (`admin_default_summary_prompt` oder `admin_default_title_prompt`) nicht gesetzt ist oder noch der Upstream-Vorgabe entspricht. Jeder andere Administrations-, Benutzer-, Tag- oder Ordner-Prompt hat wie in Upstream Vorrang. Eine Datenbank, die bei eingeschaltetem Klinikmodus initialisiert wird, erhält die Klinik-Prompts als Startwerte. Eine Datenbank, die ohne Klinikmodus initialisiert wurde, enthält die Upstream-Vorgabe; bei eingeschaltetem Klinikmodus wird diese als Klinik-Prompt aufgelöst.

**Zusammenfassungs-Prompt.** Er verlangt eine strukturierte deutsche Klinikzusammenfassung eines ärztlichen Diktats aus einer Suchtklinik (Entzugs- und Suchtbehandlung), in Markdown, mit diesen Überschriften in dieser Reihenfolge:

- `Anlass / Fragestellung`
- `Suchtanamnese` (Substanzen, Konsummuster, letzte Einnahme, frühere Entzüge und Therapien)
- `Aktueller Befund / Psychischer Status` (einschliesslich Entzugssymptomen und genannter Scores, zum Beispiel CIWA-Ar, COWS)
- `Diagnosen` (wie diktiert; ICD-10-Codes nur, wenn sie genannt werden)
- `Medikation / Entzugsbehandlung`
- `Procedere / Nächste Schritte`
- `Offene Punkte / Unklarheiten` (unklare oder widersprüchliche Stellen im Diktat)

Seine Regeln weisen das Modell an, ausschliesslich Inhalte aus dem Transkript zu verwenden und nichts zu erfinden, einen Abschnitt wegzulassen, zu dem das Diktat nichts sagt, keine zusätzlichen Diagnosen, Dosierungen oder Empfehlungen hinzuzufügen, Zahlen, Dosierungen, Daten und Namen exakt wie diktiert zu übernehmen, knapp in medizinischem Deutsch zu schreiben und unsichere oder unverständliche Passagen der Spracherkennung mit `«[bitte prüfen]»` zu markieren, statt zu raten. Diese Regeln sind Anweisungen an das Modell. Kein Code prüft, ob eine Zusammenfassung sie einhält.

**Titel-Prompt.** Wörtlich:

```text
- Titel auf Deutsch
- Höchstens 8 Wörter
- Keine Wendungen wie "Diktat zu" oder "Bericht über"
- Nur das Hauptthema nennen (z. B. Aufnahme, Verlauf, Entzug, Austritt)
```

**Ausgabesprache.** Im Klinikmodus werden Zusammenfassungen und Titel auf Deutsch angefordert, sofern im Konto des Benutzers keine andere Ausgabesprache eingestellt ist.

## Prompt-Rangfolge

Der Code behält für beide Prompts die Rangfolge von Upstream bei.

Zusammenfassung: Tag > Ordner > Benutzer > Administrationsvorgabe > mitgelieferte Vorgabe.

- **Tag:** Jeder für den Benutzer sichtbare Tag mit einem Zusammenfassungs-Prompt trägt bei; die Prompts werden in der Reihenfolge verbunden, in der die Tags hinzugefügt wurden.
- **Ordner:** Der Ordner-Prompt gilt nur, wenn die Zusammenfassung für den Eigentümer der Aufnahme erstellt wird.
- **Benutzer:** der persönliche Zusammenfassungs-Prompt des Benutzers.
- **Administrationsvorgabe:** `admin_default_summary_prompt`, mit der Klinikregel oben.
- **Mitgelieferte Vorgabe:** der deutsche Klinik-Prompt.

Titel: Tag > Ordner > Benutzer > Administrationsvorgabe (`admin_default_title_prompt`) > mitgelieferte Vorgabe, mit derselben Verbindungsregel für Tag-Prompts.

Eine höhere Ebene ersetzt die tieferen Ebenen; sie ergänzt sie nicht. Ein Tag-, Ordner- oder persönlicher Prompt ersetzt deshalb den mitgelieferten Klinik-Prompt samt seinen Regeln (nichts erfinden, keine zusätzlichen Diagnosen, Dosierungen oder Empfehlungen, Markierungen `«[bitte prüfen]»`). Benutzer können Tag- und Ordner-Prompts auf der Kontoseite selbst setzen; die Klinikversion hat dafür weder eine administrative Steuerung noch eine Prüfung.

Ein Prompt enthält nur Anweisungen. Der Code fügt das Transkript und die Anweisung zur Ausgabesprache hinzu, bei Titeln zusätzlich die Regel, nur mit dem Titel zu antworten, und bei der Standardzusammenfassung den unten beschriebenen Kontextblock. Ein geänderter Prompt gilt nur für Zusammenfassungen und Titel, die danach erstellt werden; bestehende werden nicht neu erstellt.

Wo sich jede Ebene in der Klinikversion ändern lässt:

| Ebene | Ort | Wer |
|---|---|---|
| Tag, Ordner | Kontoseite, Reiter **Tags & Ordner**, Bearbeitungsdialog eines Tags oder Ordners, Reiter **Zusammenfassung & Vorlagen**: Felder **Benutzerdefinierter Zusammenfassungs-Prompt** (Tag) bzw. **Benutzerdefinierter Prompt** (Ordner) und **Titel-Prompt**. Der Unterreiter für Ordner erscheint nur, wenn Ordner eingeschaltet sind (`enable_folders`). | Der angemeldete Benutzer, für die eigenen Tags und Ordner |
| Benutzer | Keine Oberfläche: Der Kontoreiter **Prompt-Optionen** wird nicht angezeigt. Ein früher gespeicherter persönlicher Prompt gilt weiterhin. | — |
| Administrationsvorgabe | Keine Oberfläche: Der Verwaltungsreiter wird nicht angezeigt, und die Schlüssel sind in der Liste **Systemeinstellungen** ausgeblendet. | Siehe unten |
| Mitgelieferte Vorgabe | Nur im Code | — |

Die Einstellungsschlüssel `admin_default_summary_prompt` und `admin_default_title_prompt` werden weiterhin gelesen. Der Standard-Endpunkt `POST /admin/settings` nimmt sie von der globalen Administration (Konto mit **Administrationsrolle**, `User.is_admin`) weiterhin an; das Merkmal **Identitätsadministration**, eine Poolleitung (**Leitung**) sowie die Rechte **Freigeben** und **Originalaudio exportieren** berechtigen dazu nicht. Bei eingeschaltetem Auditprotokoll wird jede Änderung im **System-Auditprotokoll** als `admin.setting.changed` nur mit dem Schlüsselnamen erfasst, ohne alten oder neuen Text (siehe [Auditprotokoll](audit-log.md)). Die Klinikversion hat für eine solche Änderung weder eine Oberfläche noch ein unterstütztes Klinikverfahren. Der Anfrageinhalt lautet `{"key": "admin_default_summary_prompt", "value": "<prompt>", "setting_type": "string"}` (entsprechend für `admin_default_title_prompt`); eine Browsersitzung braucht zusätzlich ein gültiges CSRF-Token. Ein leerer Wert oder der Text der Upstream-Vorgabe lässt wieder den mitgelieferten Klinik-Prompt gelten. Eine geänderte Administrationsvorgabe wirkt auf die Zusammenfassungen und Titel aller Benutzer, die keinen Tag-, Ordner- oder persönlichen Prompt haben.

Die Schlüssel `admin_default_initial_prompt`, `admin_default_hotwords` und `admin_default_contextual_speaker_prompt` sind in der Einstellungsliste ebenfalls ausgeblendet. Die kontextbezogene Sprecherbenennung liefert im Klinikmodus kein Ergebnis, ihr Prompt bleibt daher ohne Wirkung.

## Temperaturen

Die Temperaturen für Zusammenfassungs-, Titel- und Chat-Anfragen haben im Klinikmodus keine Oberfläche. Die API bleibt für die globale Administration (`User.is_admin`) verfügbar:

- `GET /admin/llm-temperatures` liefert je Art den wirksamen Wert, seine Quelle (`admin` oder `default`) und die Vorgabe.
- `POST /admin/llm-temperatures` mit `{"values": {"summary": 0.2, "chat": null}}` speichert einen Wert zwischen 0 und 2; `null` oder eine leere Zeichenkette löscht ihn. Eine unbekannte Art ergibt 400 `unknown_temperature`, ein ungültiger Wert 400 `invalid_temperature`.

| Art | Vorgabe |
|---|---|
| `summary` | 0.5 |
| `title` | 0.7 |
| `chat` | 0.7 |

Die Werte werden bei jeder Anfrage gelesen; eine Änderung gilt ab der nächsten Anfrage ohne Neustart. Bei eingeschaltetem Auditprotokoll wird jede Änderung als `admin.setting.changed` mit dem Schlüssel (`llm_temperature_summary`, `llm_temperature_title` oder `llm_temperature_chat`) und ohne Werte erfasst. Die Upstream-Temperatur für die Ereigniserkennung gibt es im Fork nicht. Chat hat keine Klinik-Oberfläche (siehe [Ambrose](ambrose.md)).

## Wo Prompts in der freigegebenen Version verwendet werden

Im Klinikmodus ruft ein Transkriptionsauftrag das Textmodell nie selbst auf. Nachdem das Transkript gespeichert ist, stellt er einen Folgeauftrag vom Typ `summarize` in die Zusammenfassungs-Warteschlange. Dieser Auftrag führt zuerst den Titelschritt aus, danach die Zusammenfassung, wenn die automatische Zusammenfassung gilt. Sie gilt, wenn alle folgenden Bedingungen erfüllt sind:

- Die Systemeinstellung `disable_auto_summarization` ist nicht `true` (Vorgabe `false`; sichtbar unter **Systemeinstellungen**).
- Die persönliche Einstellung des Benutzers zur automatischen Zusammenfassung ist nicht ausgeschaltet (Vorgabe ein; ihr Schalter **Auto-Zusammenfassen** liegt im ausgeblendeten Reiter **Prompt-Optionen**, Benutzer haben im Klinikmodus also keine Oberfläche dafür).
- Ein Textmodell ist konfiguriert (`TEXT_MODEL_BASE_URL` oder `TEXT_MODEL_API_KEY` gesetzt).

Der Titelschritt läuft unabhängig von `disable_auto_summarization`. Er ruft das Textmodell nur auf, wenn keine Benennungsvorlage greift oder die Vorlage den KI-Titel verwendet.

Die Standardzusammenfassung verwendet das gespeicherte Transkript der Aufnahme, gekürzt auf `transcript_length_limit` Zeichen (Vorgabe 30000, `-1` = keine Begrenzung). Die Anfrage enthält einen Kontextblock mit dem aktuellen Datum, Aufnahmedatum und Titel der Aufnahme, Ordner, Tags, Name, Funktion und Firma des Eigentümers, soweit gesetzt, sowie den Notizen des Eigentümers zur Aufnahme, wenn er deren Einbezug gewählt hat. Die Ausgabe ist durch `SUMMARY_MAX_TOKENS` (Vorgabe 3000) begrenzt, Titel durch `TITLE_MAX_TOKENS` (Vorgabe 5000). Im Klinikmodus gelten für jeden Textmodell-Aufruf `CLINIC_LLM_TIMEOUT_SECONDS` (Vorgabe 120) und `CLINIC_LLM_MAX_RETRIES` (Vorgabe 2); siehe [Modellkonfiguration](model-configuration.md).

Die freigegebenen Klinikansichten **Schreibplatz** und **Diktatdetails** zeigen keine Zusammenfassung an.

**Offene Lücke.** Dieser automatische Folgepfad hat keine eigene Lokalitätsprüfung. Er läuft, sobald `TEXT_MODEL_BASE_URL` oder `TEXT_MODEL_API_KEY` gesetzt ist; ist nur ein Schlüssel gesetzt, gilt die Code-Vorgabe `https://openrouter.ai/api/v1`, ein gehosteter Dienst. Nur die manuellen Standardrouten (Zusammenfassung erstellen oder neu verarbeiten, Titel neu erzeugen, übersetzen, Zusammenfassung starten) sind im Klinikmodus gesperrt, solange nicht alle konfigurierten Text-, Chat-, Übersetzungs- und Embedding-URLs lokal sind. `disable_auto_summarization` stoppt automatische Zusammenfassungen, aber nicht automatische Titel. Konfigurieren Sie im Klinikmodus kein gehostetes Textmodell; siehe [Modellkonfiguration](model-configuration.md).

## Transkriptionshinweise

Die Upstream-Karte **Standard-Transkriptionshinweise** (Standard-Initial-Prompt und Standard-Hotwords) gehörte zum ausgeblendeten Verwaltungsreiter. Im Klinikmodus verwerfen Batch-Transkriptionsaufträge Initial-Prompt und Hotwords aus allen Quellen (Anfrage beim Hochladen oder erneuten Verarbeiten, Tag, Ordner, Benutzer, Administrationsvorgabe), sofern `CLINIC_ASR_PROMPTS_ENABLED` nicht eingeschaltet ist (Vorgabe aus). Ist der Schalter ein, teilen sich Prompt und Hotwords eine Obergrenze von 55 Wörtern: Der Initial-Prompt wird zuerst übernommen, die Hotwords erhalten die restlichen Wörter. Der mitgelieferte GPU-Spracherkennungsdienst hat einen eigenen Schalter `ASR_PROMPTS_ENABLED` (Vorgabe aus); Hinweise erreichen die Spracherkennung auf der GPU nur, wenn beide Schalter eingeschaltet sind. Hinweise beeinflussen nur die Dekodierung; sie sind kein akustisches Training. Einzelheiten: [Lokale Spracherkennung](whisperx-setup.md) und [Systemeinstellungen](system-settings.md).

## In Prüfung: automatische lokale Diktatzusammenfassung

> **In Prüfung.** Diese Funktion ist nicht Teil der aktuell freigegebenen Version. Bezeichnungen und Verfügbarkeit können sich ändern. Die Beschreibung folgt dem Code, der sich in Prüfung befindet.

Die Funktion ergänzt die Klinikansichten **Schreibplatz** (eingeklappt, wird beim Öffnen geladen) und **Diktatdetails** (aufgeklappt) um den schreibgeschützten Bereich **KI-Zusammenfassung**.

**Prompt.** Die Zusammenfassung verwendet dieselbe Rangfolge wie oben (Tag > Ordner > Benutzer > Administrationsvorgabe > mitgelieferte Vorgabe, im Klinikmodus der deutsche Klinik-Prompt). Sie wird für den Benutzer aufgelöst, für den der Auftrag läuft: bei einer automatischen Zusammenfassung für den Autor, bei einer manuellen für den anfordernden Benutzer. Die Ordnerebene gilt nur, wenn dieser Benutzer Eigentümer der Aufnahme des Diktats ist.

**Eingabe.** Das Modell erhält die aktuell gespeicherte Textrevision des Diktats, die bereits korrigiert sein kann, nicht den rohen Erkennungstext. Der Text wird auf `transcript_length_limit` gekürzt. Anders als bei der Standardzusammenfassung wird kein Kontextblock (Datum, Titel, Tags, Benutzerprofil) hinzugefügt. Ausgabesprache ist die Ausgabesprache des Benutzers, standardmässig Deutsch.

**Grenzen.** Es gelten `SUMMARY_MAX_TOKENS` (Vorgabe 3000) und die Temperatur `summary`. Eine Antwort, deren Abschlussgrund nicht `stop` ist oder die leer ist, lässt den Auftrag fehlschlagen. Die Anfrage ignoriert Proxy-Umgebungsvariablen und folgt keinen Weiterleitungen. Fehlermeldungen des Anbieters werden durch eine allgemeine Meldung ersetzt, damit Fehlertexte das Transkript nicht zitieren. Im Klinikmodus gelten für den Aufruf zudem `CLINIC_LLM_TIMEOUT_SECONDS` und `CLINIC_LLM_MAX_RETRIES`.

Eine Zusammenfassung entsteht nur mit einem betriebsbereiten lokalen Textmodell; eine gehostete Ausweichlösung gibt es nicht. Das Modell lässt sich pro Aufgabe auf der Systemseite **Modellwahl**, Feld **Textmodell**, festlegen; auch diese Seite ist in Prüfung, und das Feld wirkt nur auf diese Diktatzusammenfassungen. Nur eine aktive globale Administration (**Administrationsrolle** und ein Klinikkonto mit dem Status **Aktiv**) kann **Modellwahl** öffnen und speichern; **Identitätsadministration**, **Leitung**, **Freigeben** und **Originalaudio exportieren** berechtigen dazu nicht.

Den automatischen Auslöser, wer eine Zusammenfassung lesen oder anfordern darf, die Bindung an eine Textrevision, die Auditereignisse und die Testnachweise beschreibt die Seite [Modellkonfiguration](model-configuration.md) im Abschnitt zu den automatischen Diktatzusammenfassungen.

## Zugehörige Einstellungen

| Einstellung | Vorgabe | Wirkung |
|---|---|---|
| `disable_auto_summarization` (Systemeinstellungen) | `false` | `true` stoppt automatische Zusammenfassungen für alle Benutzer; Titel laufen weiter |
| `transcript_length_limit` (Systemeinstellungen) | `30000` | Zeichenzahl des Texts für eine Zusammenfassung oder einen Titel; `-1` = keine Begrenzung; Audit mit altem und neuem Wert |
| `SUMMARY_MAX_TOKENS` (Umgebung) | `3000` | Höchstzahl ausgegebener Tokens einer Zusammenfassung |
| `TITLE_MAX_TOKENS` (Umgebung) | `5000` | Höchstzahl ausgegebener Tokens eines Titels |
| `CLINIC_LLM_TIMEOUT_SECONDS` / `CLINIC_LLM_MAX_RETRIES` (Umgebung) | `120` / `2` | Grenzen pro Aufruf im Klinikmodus |
| `TEXT_MODEL_BASE_URL` (Umgebung) | Code-Vorgabe `https://openrouter.ai/api/v1`, nur verwendet, wenn gesetzt oder ein Schlüssel vorhanden ist | Endpunkt des Textmodells; er und jede gesetzte `CHAT_`-/`TRANSLATION_`-/`EMBEDDING_`-URL müssen für die gesperrten generativen Funktionen lokal sein |
| `CLINIC_LOCAL_MODEL_HOSTS` (Umgebung) | `speaches,host.docker.internal,localhost` | Kommagetrennte Dienstnamen, die als lokal gelten; ein gesetzter Wert ersetzt die Standardliste (Loopback- und private Adressen gelten immer als lokal) |
| `CLINIC_ASR_PROMPTS_ENABLED` (Umgebung) | aus | Lässt Initial-Prompt und Hotwords zur Spracherkennung durch (Obergrenze 55 Wörter) |

## Upstream-Referenz

Die Upstream-Seite erklärt den Editor **Standard-Prompts**, die Ansicht «Vollständige LLM-Prompt-Struktur anzeigen», Strategien zum Schreiben von Prompts und kreative Beispiele für Tag-Prompts (Rezepte, Vorlesungen, Code-Reviews, Brainstorming). Nichts davon ist ein Klinikverfahren: Der Editor wird im Klinikmodus nicht angezeigt, und die Beispiele passen nicht zu klinischen Diktaten.

Upstream-Speakr-Dokumentation (fixiertes Original, nur Upstream): [Default Prompts](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/prompts.md)
