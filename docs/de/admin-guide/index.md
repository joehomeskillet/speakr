# Administrationshandbuch
<small>[English](../../admin-guide/index.md)</small>

Dieser Teil richtet sich an Administratorinnen und Administratoren des Klinik-Forks: eines reinen Web-Diktatsystems auf Basis von Speakr v0.11.1 (festgelegter Upstream-Commit 48d3018). Die Seiten beschreiben das Verhalten bei eingeschaltetem Klinikmodus. Wo eine Upstream-Funktion von Speakr in der Klinikversion ausgeblendet, gesperrt oder entfernt ist, sagt die jeweilige Seite das und nennt die Klinik-Alternative.

Die Klinikversion ist ein Entwicklungsstand. Das geladene Vertragsprofil ist `local-web-parity-lab` mit `synthetic_only: true` und `allow_real_data: false`. Nichts in diesem Handbuch ist eine klinische Freigabe, eine Zertifizierung, eine Produktivfreigabe oder eine Aussage über eine 1:1-Parität mit dem bisherigen Diktatprodukt.

## Klinikmodus

- Der Klinikmodus ist nur eingeschaltet, wenn `CLINIC_MODE` den Wert `1`, `true`, `yes` oder `on` hat. Der Wert wird bei jedem Aufruf gelesen. Bei ausgeschaltetem Klinikmodus gelten die hier beschriebenen Klinik-Routen, Prüfungen und Verwaltungsseiten nicht, und das Upstream-Verhalten bleibt unverändert.
- Im Klinikmodus sind Funktionen standardmässig gesperrt. Eine Funktion ist nur nutzbar, wenn `dev.defaults.json` sie unter `features` nennt und auf `true` setzt. Ausnahme: `chat`, `inquire` und `generative_summary` beachten ihren Schalter nicht und hängen von der Lokalität der Textmodelle ab (siehe unten «Nur lokale Verarbeitung»). Die Datei wird aus `clinic-spec/config` im Repository-Stammverzeichnis gelesen oder aus dem Verzeichnis in `CLINIC_CONFIG_DIR`. Fehlt eine Vertragsdatei, bricht jede Prüfung, die sie liest, mit einem Fehler ab (Fail-closed); der Klinikmodus arbeitet nie mit einer unbekannten Richtlinie. Keine Verwaltungsseite und keine Systemeinstellung schaltet diese Funktionen ein.
- Eine Routenprüfung ordnet jeden Endpunkt ein. Ein Endpunkt, der nicht in der Routentabelle steht, wird mit `403 clinic_unclassified_route` abgelehnt. Eine Route, deren Funktion gesperrt ist, wird mit `403 clinic_feature_disabled` abgelehnt.

## Wo die Verwaltung liegt

Der Eintrag **Administration** im Benutzermenü (englische Oberfläche: **Admin**) öffnet `/admin`, die **Verwaltung**. Nur Konten mit der **Administrationsrolle** sehen den Eintrag und gelangen auf die Seite; die Routenprüfung lehnt jedes andere Konto mit `403 forbidden` ab. Ohne Fragment (`#/…`) ist die Startseite **Benutzer und Rollen**. Die Beschriftungen der Klinik-Navigation gibt es nur auf Deutsch.

Das Kopfzeilenmenü **Verwaltung & Seiten** verweist auf einen Teil der Seiten. Seine Bereiche **Organisation** (**Schreibkräfte-Pools**, **Diktierende und Autorenpools**, **Benutzer und Rollen**) und **Sprache und Verarbeitung** (**Wortschatz**, **Sprachbefehle und Vorlagen**, **ASR und Sprachprofile**, **Verarbeitung**) sowie der Eintrag **Aktivität und Audit** im Bereich **Betrieb und Zugriff** erscheinen nur für Konten mit der **Administrationsrolle**. Zwei Menübeschriftungen weichen von der Navigation unter `/admin` ab: **Sprachbefehle und Vorlagen** öffnet **Befehle und Vorlagen**, und **ASR und Sprachprofile** öffnet **Sprachprofile**. Der Menüeintrag **Dateien und Geräteimport** öffnet die Importansicht für Benutzer `/#/import`, nicht `/admin#/import`. Alle übrigen Seiten erreichen Sie über die Navigation unter `/admin`.

| Gruppe | Seite | Direktlink |
|---|---|---|
| **Konten und Pools** | **Benutzer und Rollen** | `/admin#/benutzer` |
| | **Schreibkräfte-Pools** | `/admin#/schreibkraefte-pools` |
| | **Diktierende und Autorenpools** | `/admin#/diktierende` |
| | **Verzeichnis** | `/admin#/verzeichnis` |
| **Inhalte** | **Kategorien** | `/admin#/kategorien` |
| | **Regeln** | `/admin#/regeln` |
| | **Spalten** | `/admin#/spalten` |
| | **Wortschatz** | `/admin#/wortschatz` |
| | **Befehle und Vorlagen** | `/admin#/befehle-vorlagen` |
| **Richtlinien** | **Abnahme** | `/admin#/abnahme` |
| **Betrieb** | **Vorgaben** | `/admin#/vorgaben` |
| | **Sprachprofile** | `/admin#/sprachprofile` |
| | **Import** ([Geräte- und Ordnerimport](recording-sessions.md#gerate-und-ordnerimport-verwaltungsseite-import)) | `/admin#/import` |
| | **Verarbeitung** | `/admin#/verarbeitung` |
| | **Aktivität und Audit** | `/admin#/aktivitaet` |
| **System** | **Systemstatistik** | `/admin#/system/statistik` |
| | **Systemeinstellungen** | `/admin#/system/einstellungen` |
| | **System-Auditprotokoll** | `/admin#/system/audit` |

- Für Konten mit der **Administrationsrolle** leitet die App-Oberfläche ihre neun Links `/#/verwaltung/<slug>` (`schreibkraefte-pools`, `diktierende`, `benutzer`, `wortschatz`, `befehle-vorlagen`, `sprachprofile`, `import`, `verarbeitung`, `aktivitaet`) an `/admin#/<slug>` weiter. Andere Konten bleiben in der App-Oberfläche; eine Seite, deren Prüfung abgelehnt wird, zeigt dort **Diese Seite ist für dieses Konto nicht freigegeben.**
- Beim Laden prüft `/admin` die meisten Seiten mit einer GET-Anfrage an ihre API; **Regeln**, **Verzeichnis** und die **System**-Seiten werden ohne Prüfung angezeigt. Während der Prüfung zeigt die Seite **Berechtigungen werden geprüft …**. Eine Seite, deren API für das Konto mit 401 oder 403 antwortet, wird zurückgehalten und im Hinweis **Für dieses Konto nicht freigegeben: …** aufgeführt. Bei einem Netzwerkfehler oder einer 404-Antwort bleibt die Seite sichtbar und zeigt ihren eigenen Zustand.
- **Aktivität und Audit** wird in der freigegebenen Version für jedes Konto zurückgehalten, auch für die globale Administration. Die Prüfung dieser Seite verlangt eine Statistikberechtigung, und Statistikberechtigungen kann nur Servercode anlegen, den die Anwendung nirgends aufruft. Status: Vorbereitet, deaktiviert. Siehe [Systemstatistik](statistics.md).
- Die **System**-Seiten sind die Upstream-Reiter für Statistik, Einstellungen und Auditprotokoll, in die Klinik-Navigation übernommen. Die Upstream-Reiter **Benutzerverwaltung**, **Standard-Prompts**, **Gruppen** und **Vektorspeicher** werden im Klinikmodus nicht angezeigt.

## Weitere Verwaltungsseiten

Diese Seiten von `/admin` sind keine Systemeinstellungen. Jede hat eigene Regeln. **Wortschatz** beschreibt [Lokale Spracherkennung](whisperx-setup.md#die-seite-wortschatz), **Import** die Seite [Aufnahmesitzungen](recording-sessions.md#gerate-und-ordnerimport-verwaltungsseite-import), **Abnahme** der Abschnitt [Freigaberecht](#freigaberecht) und die [Gruppenverwaltung](group-management.md#freigabe-freigeben-und-abnahme).

| Seite | Geltungsbereich | Status |
|---|---|---|
| **Kategorien** | Kategoriegruppen, die das angemeldete Konto sieht | Speichern: Offene Lücke; Durchsetzung von Pflichtgruppen: Verfügbar |
| **Regeln** | Ein Diktat | Verfügbar |
| **Spalten** | System | Verfügbar |
| **Befehle und Vorlagen** | Eigenes Konto, Autorenpool oder System | Befehle und Vorlagen: Verfügbar; **Bausteine**: Offene Lücke |
| **Vorgaben** | Nur das eigene Konto | Verfügbar |

### Kategorien

- Die Seite listet unter **Kategorien der Gruppe** die Kategoriegruppen auf, die das angemeldete Konto verwenden darf: Systemgruppen, Gruppen seiner Autorenpools und eigene Gruppen. Der Editor bietet **Kategorie hinzufügen**, **Entfernen**, **Pflichtfeld**, **Speichern** und **Vorschau Sendeformular**.
- **Speichern** schlägt immer mit **Das Speichern ist fehlgeschlagen.** fehl: Die Seite sendet den Befehl `save_category_set`, der nicht im Katalog der Paritätsbefehle steht, und der Server weist unbekannte Befehle ab. Keine Route, keine Seite und kein Skript legt eine Kategoriegruppe an oder ändert sie.
- Die Durchsetzung wirkt: Solange eine Pflichtgruppe für eine diktierende Person sichtbar ist, wird das Speichern der Diktateigenschaften ohne gültige Kategorie dieser Gruppe mit `422 missing_category` abgelehnt.

### Regeln

- Geben Sie die **Diktat-ID** ein, und klicken Sie auf **Regeln laden**. Der Bereich **Regeln für Metadaten** nimmt bis zu 20 Regeln (**Muster**, höchstens 256 Zeichen; **Zielfeld**; **Ersetzung**) und einen **Eingabetext** auf.
- **Vorschau** zeigt **Bisheriger Wert** und **Neuer Wert**. **Übernehmen** schreibt nur, was die Vorschau gezeigt hat: Der Server berechnet die Vorschau neu und lehnt das Schreiben ab, wenn sich Regeln oder Eingabe geändert haben. Die Regeln selbst werden nicht gespeichert.
- Zielfelder sind Titel, Diktatname, Bemerkungen, Notizen, Priorität, Dringlichkeit, Frist und Fälligkeitsdatum. Kontext- und Identitätsfelder (Benutzer-, Autoren-, Patienten-, Fall-, Integrations-, Client- und Konto-IDs) werden abgelehnt.
- Vorschau und Übernehmen verlangen **Bearbeiten** an diesem Diktat; die **Administrationsrolle** allein genügt nicht. Eine übernommene Änderung wird wie andere Metadatenänderungen des Diktats protokolliert.

### Spalten

- **Metadatenspalten** bearbeitet die **Spaltendefinitionen** mit **Bezeichnung**, **Anzeigetyp** und einem optionalen XML-Pfad. Jedes angemeldete Konto liest die aktiven Definitionen; die aktive globale Administration sieht auch inaktive.
- Speichern darf nur eine aktive globale Administration (sonst `403 POLICY_DENIED`). Die Seite erlaubt höchstens 30 Spalten (**Es sind höchstens 30 Spalten möglich.**). Ein Spaltenname beginnt mit einem Buchstaben und hat höchstens 64 Buchstaben, Ziffern oder Unterstriche. Ein XML-Pfad ist eine Folge von höchstens acht einfachen Elementnamen, getrennt durch `/`, mit höchstens 256 Zeichen, ohne Attribute, Platzhalter oder Bedingungen.
- Jedes Speichern wird als `admin.setting.changed` mit dem Schlüssel `other` protokolliert, ohne Spaltenname und Werte, solange `audit_log_enabled` eingeschaltet ist.

### Befehle und Vorlagen

- Die Seite hat die Teile **Befehle**, **Bausteine** und **Vorlagen**.
- **Bausteine** zeigt **Bausteine sind noch nicht verfügbar: Die Serverressource fehlt. Diese Teilanforderung bleibt offen.** Status: Offene Lücke.
- Persönliche Befehle und Vorlagen gehören dem angemeldeten Konto. Befehle und Vorlagen eines Pools darf jedes Mitglied des Autorenpools speichern, Systemvorlagen jedes Konto mit der **Administrationsrolle**; siehe [Wirkung auf gemeinsame Poolressourcen](group-management.md#wirkung-auf-gemeinsame-poolressourcen).
- Vorlagen sind DOCX- oder ODT-Dateien mit höchstens 2 MiB. Das Speichern von Befehlen und Vorlagen schreibt keine Auditzeile.

### Vorgaben

- **Empfänger und Arbeitsplatz** legt den **Standardempfänger** fest (**Alle berechtigten Personen**, **Bestimmte Person**, **Personalpool**, **Selbstkorrektur**) und die **Abweichende Auswahl beim Diktat** (**Nur der Standard**, **Nur Personalpools**, **Frei wählbar**).
- Die Seite bearbeitet nur den Standardempfänger des angemeldeten Kontos; der Server lehnt jedes andere Ziel ab. Es ist keine systemweite Vorgabe. Eine Person oder ein Personalpool als Standard verlangt den eigenen Autorenpool des Kontos; sonst zeigt die Seite **Kein Autorenpool zugewiesen.**
- Der Standardempfänger wird je Konto gespeichert. Die letzte Auswahl und die **Arbeitsplatz-Einstellungen** liegen nur im Arbeitsspeicher des Prozesses: Sie gehen bei einem Neustart verloren und können sich zwischen Arbeitsprozessen unterscheiden. Werte, die eine Klinikobergrenze festlegt, zeigen **Zentral festgelegt**.
- Es wird keine Auditzeile geschrieben.

## Rollen und Rechte

Die Klinikversion trennt die Verwaltung vom Zugriff auf Diktatinhalte. Halten Sie die folgenden Rollen und Rechte auseinander. Jede wird einzeln erteilt.

### Globale Administration

- Erteilt durch: eine lokale Identitätsadministration; sie setzt die **Administrationsrolle** (`User.is_admin`) in **Benutzer und Rollen**.
- Erlaubt: den Zugang zu `/admin` und zu allen Routen, welche die Routenprüfung als administrativ einordnet.
- Mehrere Klinikfunktionen verlangen zusätzlich eine *aktive* globale Administration: die **Administrationsrolle** und ein Klinikkonto mit dem Status **Aktiv**. Eine deaktivierte Administration oder eine ohne Klinikkonto-Eintrag ist keine aktive globale Administration. Diese Prüfung gilt unter anderem für das Ändern von Fachrechten, das Festlegen von Poolleitungen, das Veröffentlichen der Abnahmerichtlinie, das erneute Ausführen von Aufträgen in **Verarbeitung** und die Diktatübersicht der Administration.
- Inhaltszugriff: Eine aktive globale Administration darf jedes Klinikdiktat ansehen und abspielen und seine Korrekturen lesen. In der Arbeitsliste sieht sie jedes Diktat; jede fremde Zeile, die der Server tatsächlich zurückgibt, wird als `recording.content.read` mit `part=worklist` protokolliert. Die Administrationsendpunkte `/api/admin/clinic/dictations/<id>/transcript` und `/audio` öffnen Transkript oder Audio jedes Benutzers ohne Begründungsabfrage. Sie schreiben zuerst den Auditeintrag (`recording.content.read` mit `part=transcript` oder `recording.media.played`). Lässt sich der Eintrag nicht speichern, lautet die Antwort `503 audit_unavailable`, und es wird nichts ausgeliefert.
- Zuweisung: Eine aktive globale Administration darf jedes Diktat zuweisen oder neu zuweisen, ohne das Fachrecht **Ansehen** und ohne Leitungsrolle; eine Verweigerung für **Neu zuordnen** verhindert es trotzdem.
- Erlaubt nicht: Bearbeiten, Exportieren, Freigeben, Originalaudio exportieren, Archivieren oder Löschen bei Diktaten anderer Personen. Dafür braucht es Fachrechte, wie bei jedem anderen Benutzer.

### Lokale Identitätsadministration

- Erteilt durch: das Merkmal **Identitätsadministration** am Konto. Die erste Administration (**Administrationsrolle** und **Identitätsadministration**) wird einmalig auf dem Server mit `scripts/clinic_bootstrap_admin.py` angelegt; siehe [Benutzerverwaltung](user-management.md).
- Erlaubt: die Kontoverwaltung in **Benutzer und Rollen** › **Konten**: Konten anlegen, bearbeiten, deaktivieren und reaktivieren, die beiden Rollenmerkmale ändern sowie **Einmalcode ausstellen**. Dafür sind **Administrationsrolle** und **Identitätsadministration** zusammen nötig, ausserdem der Status **Aktiv**, keine ausstehende Passwortänderung, eine passende lokale Sitzung und eine Anmeldung innerhalb der letzten 300 Sekunden. Sonst lehnt der Server mit `403 unauthorized` oder `403 reauth_required` ab; die Kontoansicht zeigt **Keine Berechtigung für diese Aktion.** oder **Bitte erneut anmelden und die Änderung nochmals ausführen.**
- Für andere Administrations- oder Identitätsadministrationskonten lässt sich kein Einmalcode ausstellen (`admin_recovery_forbidden`); diese Konten werden nur über die Serverkonsole wiederhergestellt. Einzelheiten: [Benutzerverwaltung](user-management.md).
- Erlaubt nicht: zusätzlichen Diktatzugriff. Das Merkmal selbst gewährt keinen Zugriff auf Diktate. Da es nur zusammen mit der **Administrationsrolle** wirkt, hat eine aktive Identitätsadministration den Inhaltszugriff der globalen Administration (Ansehen, Abspielen, Korrekturen lesen), aber keine weiteren Rechte. Die Kontoansicht hält fest: **Identitätsrollen geben keinen Zugriff auf Diktate. Fachrechte werden separat erteilt.** Eine Identitätsadministration darf die Fachrechte eines Benutzers lesen, ändern darf sie nur eine aktive globale Administration.
- Wird die **Administrationsrolle** entzogen, entfällt auch die **Identitätsadministration**. Im Klinikmodus setzt die Administration nie Passwörter, und Konten werden deaktiviert, nicht gelöscht.

### Poolleitungen

- Erteilt durch: Eine aktive globale Administration kennzeichnet ein Mitglied eines **Schreibkräfte-Pools** als **Leitung**. Intern wird dafür die Upstream-Rolle der Gruppenadministration der zugehörigen Gruppe verwendet.
- Erlaubt: In der Arbeitsliste sieht die Leitung nicht zugewiesene Diktate, die keine Entwürfe sind, aus den Autorenpools, die an den geleiteten Schreibkräfte-Pool weitergeleitet werden. Die Leitung darf sie zuweisen oder neu zuweisen, wenn sie das Fachrecht **Ansehen** besitzt und keine Verweigerung für **Neu zuordnen** hat. Die Leitung darf die Verwendungszahlen des geleiteten Pools lesen.
- Erlaubt nicht: Pools anlegen, umbenennen oder deaktivieren, Mitgliedschaften ändern, Leitungen festlegen, Fachrechte ändern oder `/admin` öffnen.
- Ein deaktiviertes Klinikkonto beendet alle Leitungsfunktionen, und das Zuweisen verlangt ein aktives Klinikkonto.
- Das Benutzermenü zeigt Leitungen ohne **Administrationsrolle** weiterhin den Upstream-Eintrag **Gruppen**, der auf `/group-management` verweist. Im Klinikmodus wird diese Seite mit `403 clinic_feature_disabled` abgelehnt.

Siehe [Pools (Gruppenverwaltung)](group-management.md).

### Fachrechte

- Fachrechte sind Berechtigungen je Benutzer in **Benutzer und Rollen** › **Fachrechte**. Jeder Eintrag hat eine Wirkung (**Erlauben** / **Verweigern**), einen Geltungsbereich (**Eigene Diktate**, **Pool**, **Alle Diktate**) und die Aktionen, die er abdeckt. Eine zutreffende Verweigerung gewinnt immer.
- Fachrechte ändert nur eine aktive globale Administration. Alle anderen sehen **Nur Lesezugriff. Änderungen sind nur durch Administration möglich.**
- Sichere Voreinstellung für eigene Diktate: Eine diktierende Person darf ihre eigenen Diktate ansehen, abspielen und bearbeiten. Jede andere Aktion verlangt eine ausdrückliche Berechtigung, auch bei eigenen Diktaten.

### Freigaberecht

- Die Aktion **Freigeben** ist ein eigenes Fachrecht. Niemand besitzt es standardmässig, auch nicht die globale Administration.
- Es wird benötigt für **Zur Abnahme einreichen** (zusammen mit **Bearbeiten**), **Zusätzlich abnehmen**, **Zur Korrektur zurückweisen**, **Wieder öffnen** und **Endgültig freigeben**.
- Die Seite **Abnahme** legt je Diktat (geladen über die **Diktat-ID**) fest, ob eine Abnahme erforderlich ist (**Freigabe erforderlich**) und wer abnehmen darf (**Berechtigte Freigebende**). Diese Richtlinie veröffentlicht nur eine aktive globale Administration. Ohne veröffentlichte Richtlinie ist keine Abnahme erforderlich. Vor der ersten Veröffentlichung zeigt das Kontrollkästchen die aktuelle Einstellung des Diktats im Arbeitsablauf (standardmässig aus); vorausgewählt ist es nur bei einem Diktat, das noch keinen Eintrag im Arbeitsablauf hat.
- Die endgültige Freigabe verlangt ausserdem einen vertrauenswürdigen KIS-Kontext. Status des KIS: Simuliert (nur mit `CLINIC_PROFILE=staging`, synthetischer MockKIS, kein KIS-Nachweis); sonst ist der Adapter deaktiviert, und die endgültige Freigabe wird abgelehnt.

### Recht zum Export des Originalaudios

- Die Aktion **Originalaudio exportieren** ist von **Exportieren** (Textexport) getrennt.
- Das Herunterladen der Originalaudiodatei verlangt dieses Recht. Es gehört nicht zur Voreinstellung für eigene Diktate, und die globale Administration erhält es nicht durch ihren Inhaltszugriff.
- Ein lokales Mailprogramm mit dem Audio als Anhang zu öffnen, ist die offene Lücke WG-EXP-009.

## Statusbegriffe

Alle Verwaltungsseiten verwenden diese Begriffe.

| Status | Bedeutung |
|---|---|
| Verfügbar | Teil der freigegebenen Klinikversion und im Klinikmodus nutzbar. |
| Standardmässig aus | Teil der freigegebenen Version, aber ausgeschaltet, bis der dokumentierte Schalter gesetzt wird: eine Umgebungsvariable, die der Betrieb auf dem Server setzt (Beispiel: GPU-zuerst-Spracherkennung, `CLINIC_GPU_ASR_BASE_URL`), oder eine Systemeinstellung der globalen Administration (Beispiel: **Automatische Archivierung**). |
| Vorbereitet, deaktiviert | Der Code ist vorhanden, aber die freigegebene Version hat keinen unterstützten Schalter, der ihn einschaltet. Die Tests sind nur synthetisch; es gibt keinen echten Mandanten, kein echtes Verzeichnis und keine klinische Freigabe. Beispiele: Anmeldung mit Microsoft Entra, Windows-SSO (IWA), der Verzeichnisabgleich, Statistikberechtigungen, die verwaltete Offline-Speicherung. |
| Simuliert | Funktioniert nur gegen Testdaten oder eine Simulation, nie gegen das echte System. Beispiele: das KIS (Mock-Adapter mit `CLINIC_PROFILE=staging`) und die Philips-Geräteseite (Simulatormodus des Importagenten und Testdaten). Kein Nachweis für ein echtes Gerät oder KIS. |
| In Prüfung | Nicht Teil der aktuell freigegebenen Version. Beschriftungen und Verfügbarkeit können sich ändern. Derzeit nur die Verwaltungsseite **Modellwahl** (Modellauswahl für GPU- und CPU-Transkription und für die Zusammenfassung) und die automatische Diktatzusammenfassung, die an die erste Spracherkennungsrevision gebunden ist und eine eigene Prüfung auf ein lokales Modell hat. Die freigegebene Version führt stattdessen den Upstream-Folgeauftrag für Titel und Zusammenfassung aus (siehe «Nur lokale Verarbeitung»). |
| Nur Upstream | Im Upstream-Speakr vorhanden, in der Klinikversion aber ausgeblendet, gesperrt oder entfernt. |
| Offene Lücke | Eine dokumentierte Lücke, die offen bleibt. Dazu gehören alle Web-Paritätslücken in `clinic-spec/planning/web-parity-gaps.json` und die weiteren Lücken, welche die einzelnen Seiten nennen. |

Das Register der Web-Paritätslücken ([Quelle im Klinik-Fork](https://github.com/joehomeskillet/speakr/blob/main/clinic-spec/planning/web-parity-gaps.json)) führt derzeit 13 Lücken, alle mit dem Status `open`: WG-ADM-009, WG-LIVE-002, WG-LIVE-006, WG-LIVE-007, WG-IMP-009, WG-IMP-010, WG-EXP-004, WG-EXP-005, WG-EXP-007, WG-EXP-009, WG-EXP-010, WG-OPS-003 und WG-OPS-004. Das Register setzt `full_parity_claim_allowed` auf `false`.

## Nur lokale Verarbeitung

- Die Spracherkennung ist nur für lokale Dienste vorgesehen; es gibt keine Cloud-Anbindung und keine Ausweichlösung in die Cloud. Die Klinikversion registriert drei Transkriptionsanbindungen: `openai_whisper` (ein lokaler OpenAI-kompatibler Dienst wie Speaches), `asr_endpoint` (WhisperX oder whisper-asr-webservice) und `clinic_stub` (ein synthetischer Testersatz). Die Cloud-Anbindungen aus dem Upstream sind entfernt.
- Im Klinikmodus lehnt die Anbindung `openai_whisper` eine leere oder nicht lokale `TRANSCRIPTION_BASE_URL` mit einem Konfigurationsfehler ab; das GPU-Ziel `CLINIC_GPU_ASR_BASE_URL` wird gleich geprüft. Diese ASR-Prüfung ist weiter gefasst als die Prüfung der Textmodelle weiter unten: Sie akzeptiert auch Link-Local-Adressen, einteilige Hostnamen und Namen auf `.local`, `.internal`, `.lan` oder `.localhost`. Siehe [Lokale Spracherkennung](whisperx-setup.md).
- Offene Lücke: `ASR_BASE_URL` der Anbindung `asr_endpoint` wird nicht auf Lokalität geprüft. Tragen Sie dort nur lokale Hosts ein.
- Zuerst GPU, dann CPU (Status: Standardmässig aus): Ist `CLINIC_GPU_ASR_BASE_URL` gesetzt, geht ein Auftrag zuerst an den GPU-Dienst. Bei einem Verbindungsfehler, einer Zeitüberschreitung, einer HTTP-5xx-Antwort oder einem fehlerhaften `/healthz` läuft derselbe Auftrag einmal auf dem CPU-Ziel. Die GPU wird nicht erneut versucht; ein Fehler auf der CPU beendet diesen Versuch, und ob der Auftrag erneut läuft, entscheidet die Auftragswarteschlange (siehe [Lokale Spracherkennung](whisperx-setup.md)).
- Hotwords, Wortschatz und Profileinstellungen sind Hinweise an den Decoder, kein akustisches Training. Im Klinikmodus werden Transkriptionshinweise verworfen, ausser `CLINIC_ASR_PROMPTS_ENABLED` ist eingeschaltet. Optimierungsläufe, die in **Sprachprofile** mit **Optimierung starten** begonnen werden, werden eingereiht und danach vom Verarbeitungsdienst mit `CAPABILITY_UNAVAILABLE` abgelehnt; kein Modell wird trainiert oder angepasst.
- Lokale KI (Status in der Klinik-Oberfläche: Nur Upstream): Die Routen für Chat, Inquire und generative Zusammenfassungen sind im Klinikmodus gesperrt, ausser `TEXT_MODEL_BASE_URL` ist lokal und jede gesetzte `CHAT_MODEL_BASE_URL`, `TRANSLATION_MODEL_BASE_URL` und `EMBEDDING_BASE_URL` ist ebenfalls lokal. Lokal bedeutet hier Loopback, eine private Adresse oder ein Host aus `CLINIC_LOCAL_MODEL_HOSTS` (Standard `speaches,host.docker.internal,localhost`); andere einteilige Hostnamen zählen nicht. Die Aufhebung der Sperre fügt keine Klinik-Oberfläche hinzu. Anfragen, die keine Aufnahme nennen, werden weiterhin mit `403 clinic_feature_unsupported` abgelehnt, und alle anderen Anfragen verlangen das Recht **Bearbeiten** für jede genannte Aufnahme. Siehe [Ambrose-Einstellungen](ambrose.md) und [Vektorspeicher](vector-store.md).
- Automatischer Titel und automatische Zusammenfassung (Status: Standardmässig aus, kein LLM-Client, solange weder `TEXT_MODEL_BASE_URL` noch `TEXT_MODEL_API_KEY` gesetzt ist; Lokalitätsprüfung des Auftrags für automatischen Titel und automatische Zusammenfassung: Offene Lücke): Im Klinikmodus reiht die freigegebene Version nach jeder Transkription einen Folgeauftrag für Titel und Zusammenfassung der Aufnahme ein. Das ist die Upstream-Zusammenfassung der Aufnahme, nicht die automatische Diktatzusammenfassung mit dem Status In Prüfung. Dieser Auftrag hat keine eigene Lokalitätsprüfung. Der KI-Titel wird angefordert, sobald ein LLM-Client vorhanden ist und die Aufnahme keinen vom Benutzer gewählten Titel hat (eine Benennungsvorlage ohne KI-Titel-Anteil überspringt ihn ebenfalls). `disable_auto_summarization` und die Einstellung des Benutzers für automatische Zusammenfassungen überspringen nur die Zusammenfassung. Ein LLM-Client entsteht, sobald `TEXT_MODEL_BASE_URL` oder `TEXT_MODEL_API_KEY` gesetzt ist; ohne `TEXT_MODEL_BASE_URL` verwendet er eine gehostete Standardadresse. Lassen Sie deshalb beide Variablen leer, oder setzen Sie `TEXT_MODEL_BASE_URL` auf einen lokalen Dienst. Setzen Sie nie nur `TEXT_MODEL_API_KEY`.
- Der verbindliche Auftrag schliesst Cloud-Spracherkennung und eine generative Umschreibung klinischer Texte aus.

Einzelheiten: [Lokale Spracherkennung](whisperx-setup.md), [Modellkonfiguration](model-configuration.md).

## Themenübersicht

| Seite | Inhalt in der Klinikversion | Status |
|---|---|---|
| Administrationshandbuch (diese Seite) | Klinikmodus, Navigation der Verwaltung, Rollen und Rechte, Statusbegriffe, nur lokale Verarbeitung | Verfügbar |
| [Benutzerverwaltung](user-management.md) | **Benutzer und Rollen**: Konten, Poolbezüge, Fachrechte; erste Administration (**Administrationsrolle** und **Identitätsadministration**); Einmalcodes statt von der Administration gesetzter Passwörter; Deaktivieren statt Löschen | Verfügbar; Entra-Anmelderichtlinien: Vorbereitet, deaktiviert |
| [Pools (Gruppenverwaltung)](group-management.md) | Autorenpools und Schreibkräfte-Pools, Poolleitungen, Deaktivierung und Gültigkeitszeitraum, Freigabe- und Originalaudiorecht; Upstream-Gruppen und -Teams | Pools: Verfügbar; Upstream-Gruppen: Nur Upstream |
| [Systemstatistik](statistics.md) | **Systemstatistik** (Nutzungsstatistik aus dem Upstream) und die eingegrenzte Klinikstatistik in **Aktivität und Audit** | Systemstatistik: Verfügbar; Klinikstatistik: Vorbereitet, deaktiviert |
| [Systemeinstellungen](system-settings.md) | **Systemeinstellungen**: globale Einstellungen, Obergrenzen des Klinikmodus, protokollierte Änderungen | Verfügbar |
| [Standard-Prompts](prompts.md) | Mitgelieferte deutsche Klinik-Prompts für Zusammenfassung und Titel; der Upstream-Reiter zum Bearbeiten der Prompts wird nicht angezeigt | Mitgelieferte Prompts: Verfügbar; Prompt-Editor: Nur Upstream |
| [Modellkonfiguration](model-configuration.md) | Lokale Text- und Transkriptionsmodelle, über Umgebungsvariablen gesetzt; die Seite **Modellwahl** je Aufgabe | Konfiguration über Umgebungsvariablen: Verfügbar; **Modellwahl**: In Prüfung |
| [Ambrose-Einstellungen](ambrose.md) | Upstream-Assistent (Chat und Inquire); keine Klinik-Oberfläche | Nur Upstream |
| [Vektorspeicher](vector-store.md) | Upstream-Index für die semantische Suche; Reiter im Klinikmodus nicht angezeigt | Nur Upstream |
| [Aufnahmesitzungen](recording-sessions.md) | Hochladen im Klinik-Rekorder, kein Audio im Browserspeicher, Upstream-Sitzungsgrenzen für die übrigen Hochladewege | Verfügbar |
| [Webhooks](webhooks.md) | Benutzer-Webhooks sind im Klinikmodus gesperrt; stattdessen Arbeitsablauf-Hinweise in der Anwendung | Webhooks: Nur Upstream |
| [E-Mail-Einrichtung](email-setup.md) | SMTP ist optional; E-Mail-Bestätigung und Passwortrücksetzung sind entfernt; die Upstream-Mail nach der Transkription ist nicht für den klinischen Einsatz geprüft | Verarbeitungsbenachrichtigung per E-Mail: Standardmässig aus; Bestätigung und Rücksetzung: Nur Upstream |
| [Anmeldung und Identitätsanbieter](sso-setup.md) | Nur lokale Anmeldung; Upstream-OIDC-SSO wird abgelehnt; Profile für Entra, Windows-SSO (IWA) und LDAP | OIDC: Nur Upstream; Entra, IWA, LDAP: Vorbereitet, deaktiviert |
| [Lokale Spracherkennung](whisperx-setup.md) | Lokale ASR-Dienste, optionaler GPU-Dienst mit einer CPU-Ausweichlösung, synthetische Testanbindung; WhisperX als Upstream-Referenz | Verfügbar; GPU-Ausweichlösung: Standardmässig aus |
| [Aufbewahrung](retention.md) | Der Aufbewahrungslauf archiviert und löscht nie; die übrigen Wege, die Daten löschen, nennt die Seite; Archivieren von Diktaten über den Arbeitsablauf; Ansicht **Archiv** nur lesend | Automatische Archivierung: Standardmässig aus; genehmigte Aufbewahrungsrichtlinie: Offene Lücke |
| [Datenschutz und regulierte Daten](privacy.md) | Was wo gespeichert wird, Browserspeicher, Einwilligung für die eigene Stimme, Offline-Speicherung | Verfügbar; verwaltete Offline-Speicherung: Vorbereitet, deaktiviert |
| [Auditprotokoll](audit-log.md) | **System-Auditprotokoll**, Ereigniskatalog, Audit-Einstellungen; Bereinigung im Klinikmodus zurückgehalten | Verfügbar |
| [Dateispeicher](storage.md) | Unveränderliche lokale Originale unter `CLINIC_ORIGINALS_ROOT`; lokale und S3-Ablage aus dem Upstream für Aufnahmemedien | Verfügbar |
| [Migrationsanleitung](migration-guide.md) | Umstellung einer bestehenden Instanz auf die Klinikversion: erste Administration, Übernahme bestehender Konten, Übernahme bisheriger Aufnahmen | Verfügbar |

## Upstream-Referenz

Upstream-Dokumentation von Speakr: [Admin Guide (fixiertes Original, Englisch)](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/admin-guide/index.md). Sie beschreibt Upstream-Speakr, nicht die Klinikversion.
