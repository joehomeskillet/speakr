# 12 – Implementierungsvertrag Webclient / Import-Agent
## Clientprofile
`managed_web_online`: normale Browseranwendung auf freigegebenen Arbeitsplätzen. `byod_internal`: interne mobile Vordergrundaufnahme ohne persistente Audio-/Textbibliothek oder Dokumentdownload. `kis_webview`: derselbe Webclient mit geprüftem Host-/Kontextvertrag. `managed_web_offline`: getrennt freizugebende verschlüsselte Webpersistenz, initial deaktiviert. `import_agent`: Maschinenprincipal ohne Facheditor oder Mikrofon.

Der Server entscheidet Profil und Rechte über authentisierte Identität, erlaubten Ingress und freigegebene Policy. Ein User-Agent, `managed=true`, frei gesetzter Header oder bekannte IP allein beweist kein verwaltetes Endgerät. Bestehende Browserdaten lassen sich bei Offlineverlust/Rechteentzug nicht magisch remote löschen; Restrisiko ausdrücklich abnehmen.

## Erlaubter Importweg
1. Agent meldet zugelassene Quelle und Snapshot-Inventar; keine Autor-/Patientenrechte ableiten.
2. Web-Importzentrale zeigt dem berechtigten Nutzer verfügbare Quellen, offene Zuordnungen und Stapelstatus.
3. Server prüft Registry/Policy und erzeugt typed import intent für konkrete Source-/Snapshotversion.
4. Agent sendet Originalbytes/Hash/Metadaten resumable über HTTPS; Original unverändert.
5. Server persistiert Objekt plus Metadaten/Jobabsicht; gibt gebundene Quittung.
6. Nur aktivierte enge Importnachbearbeitung darf Backup/identische Quellversion entfernen. Quellwechsel erzeugt Konflikt.

Kein beliebiges `execute(command)`, `open_file(path)`, lokaler WebSocket oder Windows-Prozesszugriff. Maschinencredentials dürfen nicht Diktate abspielen, Texte lesen, Rollen setzen oder klinische Freigaben ausführen. Der Agent pollt nur seine signierte Importkonfiguration und gebundene Aufträge; er ist kein Remote-Managementprodukt.

## Browserhardware
Mikrofon via getUserMedia; Audiooutputwahl nur bei unterstützter API/Permission. Pedal als Tastatur im fokussierten Webclient oder als direktes WebHID-Gerät mit expliziter Freigabe. LEDs/Knöpfe erfordern modellbezogenen Reportvertrag; keine unbekannten HID-Reports senden. Stop/Disconnect und Browserpermissionentzug sind Sicherheitsfälle. WebHID besitzt begrenzte Browserabdeckung [WEB07]. Native globale Hotkeys und Fenstersteuerung werden nicht daraus abgeleitet [WEB06].

## Dateiausgabe und Netz
Dateien im Browser nur durch bewusste Nutzeraktion/erlaubte Handles oder Download. Serverdateipfade kommen aus konfigurierten Zielaliasen, nicht ungeprüften Nutzereingaben. Browser-Picker können fehlen oder abgebrochen werden [WEB08]. Download-HTTP-Erfolg beweist keine lokale Speicherung; getrennte Auditaktionen rendered, download_started und optional user_confirmed.

Ausgehendes Netz von Audio/ASR/Training zu externen APIs gesperrt. Login local hat keinen Identitäts-Egress. Spätere Entra-Zugriffe getrennt begrenzen. Browserassets/Modelle nicht aus CDNs nachladen. Keine geheime Ausweichroute im Agenten, wenn Browserpermission verweigert wird.
