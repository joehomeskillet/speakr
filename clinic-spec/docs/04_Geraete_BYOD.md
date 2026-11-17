# 04 – Geräteimport und interne Browseraufnahme
## Import-Agent ist die einzige lokale Projektanwendung
.NET-10-Dienst am physischen PC, minimale Rechte, signiertes Paket und genehmigter Updateweg. Er erkennt nur zentral freigegebene Geräte/Volumes/Ordner, erstellt stabile Snapshotkopien, liest vorhandene Metadaten, verschlüsselt einen begrenzten Spool, berechnet Prüfsummen, lädt wiederaufnehmbar hoch und meldet Status. Ereignisgetriggert mit 15-s-Abgleich; drei unveränderte Metadatenmessungen im Abstand von zwei Sekunden sind technische Startwerte, kein EOL-Beweis.

Geräte-ID, Headerautor und Registryzuordnung getrennt. Geteilte Geräte brauchen bestätigte Author-Metadaten oder Klärung im Web. Bei aktivem unbekanntem Autor sichere Aufnahme nicht verwerfen oder dem Windowsnutzer zuweisen. Idempotenz aus Quellnamespace/Aufnahme-ID/Originalrevision; reiner Dateihash nicht als globale Diktatidentität. Andere Karte, Headeränderung, Umbenennung und Reopen getrennt testen.

Dropfolder: mehrere explizite Pfade und Patterns, per Default nicht rekursiv wie Originalvertrag. Keine Reparse-/Symlinkflucht. Mehrfachimport und Metadatenwahl im Web; Agent verarbeitet nur signierte/gebundene Importaufträge. Lokale Aufnahme-, Playback-, Office-, Mikrofon-/Pedal-, Tokenpass-through-, Shell- oder universelle Dateiproxyfunktionen sind verboten.

## Quellenpflege ist keine Gerätesteuerung
Optionaler Backupordner nur für Originalsnapshots zugelassener Quellen. Nach freigegebener dauerhafter Serverquittung und aktivierter Löschpolicy darf exakt dieselbe Quellversion entfernt werden. Initial aus; Quellhash/Device-/Volumeidentität unmittelbar vor Delete erneut prüfen. Manuelle Quelllöschung benötigt ausdrückliche Webbestätigung mit gebundener Quelle und Audit. Keine arbiträren Deletepfade.

PIN/Autorenlisten/Aufnahmeverschlüsselung/Energiesparwerte setzen und Firmwareflash liegen ausserhalb des Agenten. Inventar und Firmwarevergleich können angezeigt werden. PAR-IMP-009/010 bleiben Pflicht und offener WG-Teilkonflikt, bis ein zulässiger direkter Browser-/Herstellerweg nachgewiesen ist oder der Auftraggeber die Teilklausel ändert. Alte SpeechExec-Adminsoftware ist höchstens dokumentierter Restbetrieb, keine Erfüllung durch den Fork.

## Browser
Eigener Webrecorder für verwaltete Geräte und internes BYOD. MIME per Capabilitytest, Mikrofonfreigabe und Trackstatus beachten. Audio wird an serverseitige Speakr-Sessions gesendet. Unregelmässige Chunkintervalle, fehlende Finalisierung, Verbindungs-/Anruf-/Sperrbildschirmereignisse werden sichtbar behandelt. Webaufnahme auf Mobilgeräten als Vordergrundbetrieb abnehmen; keine garantierte Hintergrundaufnahme.

BYOD: keine absichtliche Audio-/Textpersistenz, keine Dokumentdownloads. Verwalteter Webclient online ebenfalls ohne persistente Audioablage. Für die Originalfunktion autarker Recorder ein eigenes Managed-Web-Offlineprofil mit explizitem Gate vorbereiten; alle Fachfunktionen bleiben Browserfunktionen. Ohne Gate ist Offlinepersistenz aus, nicht durch einen nativen Ersatz aufgefangen.

Unbekannte Browser-/Gerätefähigkeiten geben capability_unavailable statt Erfolg zurück. APIs des Betriebssystems werden nicht durch Agentenrechte in den Browser verlängert. Host-/RDS-/ICA-Mikrofon- oder HID-Umleitung muss separat getestet werden; erfolgreicher USB-Upload beweist keine Remote-Diktatsteuerung.
