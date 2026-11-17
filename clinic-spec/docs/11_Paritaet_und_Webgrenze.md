# 11 – 101 Pflichtfunktionen, kein stiller Ersatz
## Quellenübernahme
Das hochgeladene Gesamtpaket enthält 101 Funktionsverträge und 101 Verhaltensfälle. Sie sind im funktionalen Arbeitskontext unverändert enthalten und in der V4-Matrix jeweils auf Module, Wiederverwendung, Regeln, Task und Test zurückgeführt. Ihre ursprünglichen Handbuch-/Static-Evidenzgrade bleiben sichtbar: Sie sind nicht automatisch auf eurer Installation beobachtete Tatsachen.

Die neue Pflicht bedeutet vollständige fachliche Abdeckung nach Referenz-/Zielvergleich, nicht automatisch dieselben EXE-Namen, proprietären Datenformate oder dieselbe Pixelgestaltung. Ein expliziter Verhaltenssatz wie „in geöffnetes natives Dokument einfügen“ darf jedoch nicht einfach in „DOCX herunterladen“ umbenannt werden.

## Neuester Zwischeninput
Alle Diktatfunktionen vollständig im Webclient. Der Import-Agent ist kein Ort für Funktionen, die Browser nicht leisten. Die vorbereitete Windows-Companion-Lösung ist verworfen. Kein nativer Ersatzclient, kein lokaler APIhost/Plugin für Fenster-/Office-/Hotkeyaktionen. Eine reine installierte PWA ist optional, aber kein nativer Wrapper.

## Klassen der Matrix
`web_design`: vorgesehener Web-/Serverweg, noch nicht implementiert oder getestet. `dependency_validation`: Browser-, Hardware-, Identity- oder ASRfähigkeit ist konkret nachzuweisen. `strict_web_conflict`: mindestens eine ursprüngliche Teilklausel kollidiert mit aktueller Clientgrenze oder hat noch keinen zulässigen nachgewiesenen Weg. Keine dieser Klassen ist ein bestandener Produkttest.

## Umgang mit offenen Kollisionen
Zulässige Teilfunktion bauen; Alternative separat kennzeichnen; Originaltest erhalten; Status offen. Kein automatisches N/A oder Herabstufen von MUST. Volle 1:1-Zusage und vollständige Abschaltung der Altsoftware sind bei offenen Kollisionen nicht freigegeben. Claude kann alle unabhängigen Teile entwickeln, ohne den Auftraggeber wiederholt um dieselbe Entscheidung zu bitten. Eine spätere ausdrückliche Änderung wird mit Datum/ID/Testdelta protokolliert, nicht retrospektiv als bereits erfüllt behauptet.

Der Pflichtkatalog enthält zahlreiche vollständig webgeeignete Funktionen. Die Kollision einzelner Teilklauseln ist daher kein Grund, den Gesamtbauauftrag aufzugeben oder das Backend neu zu wählen. Sie ist ein notwendiger Unterschied zwischen einem guten Implementierungsplan und einer falschen Paritätszusage.

## Offene Teilklauseln
| ID | Originale Teilklausel | Zulässiger Webweg / Grenze |
|---|---|---|
| PAR-ADM-009 | Lokale Windows-Konten des Arbeitsplatzes auslesen | AD-/Directory-Abgleich serverseitig und lokale Appkonten anbieten; lokale SAM-Auswahl ohne erlaubte Browser-/Serverschnittstelle bleibt offen. |
| PAR-LIVE-002 | Direktdiktat in fremde native Windows-Textfelder | Live-Diktat im Speakr-Webeditor, bewusster Clipboard-/Dateiexport; keine Gleichsetzung mit Fremdfeldeingabe. |
| PAR-LIVE-006 | SpeechLock an fremdes Windows-Fenster | Bindung an eigenen Webentwurf möglich; OS-Fensterbindung trotz anderem Vordergrundfenster nicht als erfüllt ausgeben. |
| PAR-LIVE-007 | Automatische native Fokusbindung/Always-on-top | Weblayout/Entwurfsbindung bereitstellen; keine allgemeine OS-Fenstersteuerung oder Always-on-top-Zusage. |
| PAR-IMP-009 | Schreibende Philips-Gerätekonfiguration | Geräteinventar lesen; Autorenlisten/PIN/Verschlüsselung/Energiesparen nur nach geprüftem direkten Browser-Hardwarevertrag. Import-Agent erhält keine Konfigurations-Schreibbefehle. |
| PAR-IMP-010 | Firmware auf PocketMemo schreiben | Firmwarekatalog/Versionsanzeige im Web; Flashen bleibt gesperrt ohne nachgewiesenen erlaubten Browserweg. Kein Flashen im Import-Agent. |
| PAR-EXP-004 | Einfügen in bereits geöffnetes natives Word-Dokument | DOCX aus serverseitiger Vorlage mit Textmarken und Download/Workflowablage; offenes natives Dokument ist eine gesonderte, ungelöste Teilanforderung. |
| PAR-EXP-005 | Einfügen in bereits geöffnetes natives Writer-Dokument | ODT aus Vorlage serverseitig erzeugen und herunterladen; keine Fernsteuerung der laufenden Writer-Instanz. |
| PAR-EXP-007 | Fensterklasse/-titel, Vordergrund und Tastensimulation | Expliziter Clipboard-/Downloadweg; keine Window-Enumeration, SendInput oder OS-Fokusautomation. |
| PAR-EXP-009 | Lokales Mailprogramm mit Audioanhang öffnen | Berechtigter Audio-Download und optional .eml-Artefakt nach Policy; automatischer lokaler Mailentwurf mit Anhang nicht damit gleichsetzen. |
| PAR-EXP-010 | Natives Word-Add-in mit aktueller Audiositzung | Webupload eines Dokuments mit bewusst gewählter Audiositzung; kein natives Add-in und keine automatische Gleichsetzung mit dem ursprünglichen Word-Workflow. |
| PAR-OPS-003 | Systemweite Hotkeys ohne Browserfokus | Konfigurierbare Tastenkürzel im Webclient; Keyboard Lock nur in tatsächlich erlaubter Browser-/Vollbildsituation, kein globaler OS-Hotkeydienst. |
| PAR-OPS-004 | Nativer Tray-/Autostart-/Hintergrundprozess | PWA/Browserstart und serverseitige Hintergrundjobs; Browser-Startup-Policy ist kein eigener Trayprozess. Nicht als unveränderte 1:1-Parität markieren. |
