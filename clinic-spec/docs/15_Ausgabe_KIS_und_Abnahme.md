# 15 – Webausgabe, Abnahme und inesKIS
## Fachlicher Stand
Originalquelle → Arbeitsrevision → menschlich korrigiert → optional abgenommen → fertig. Ein Diktat ohne aktivierte zusätzliche Abnahme benötigt trotzdem eine menschliche Korrekturbestätigung vor finaler klinischer Ausgabe. Autor und Schreibkraft können dieselbe Person sein. KISsignatur bleibt eigener Hersteller-/Fachprozess.

Ausgabejob friert dictation_id, audio_revision, transcript_revision, context_revision, document_type, release/working-mode, renderer_version und content_hash ein. Jeder ausgewählte Kanal besitzt eigenen Idempotenzkey und Zustand. Ein bestätigter Kanal wird nicht erneut ausgeführt, weil ein anderer scheitert. Unklarer Seiteneffekt ist UNKNOWN; Lookup/Reconciliation vor neuem Versand, auch nach Restore.

## Dateikanäle
DOCX mit Vorlagen/Textmarken, RTF, ODT, TXT, freigegebene Originalaudioausgabe und formatierte/unformatierte Zwischenablage. Vorlagendateien werden zentral kopiert, Version/Hash bleiben erhalten; fehlende externe Ursprungsdatei zerstört keine bereits gespeicherte Vorlage. Bei Textmarkenfehler bewusst abbrechen statt ans falsche Dokumentende schreiben. Grafiken/Absätze/Umlaute/Tabellen und Formatverlust testen. Originalmakros/aktive Inhalte nicht ausführen.

Clientdateien nur via Upload/Pickers/Download; Serverablage über konfigurierten Alias mit Berechtigungs-/Pathprüfung. Bestehendes natives Officefenster, Windows-Zielfensterklasse, automatischer Mailanhang und Word-Add-in sind als offene WG-Teilforderungen geführt. Webupload eines Office-Dokuments, Download oder .eml sind nützliche Alternativen, aber ohne ausdrückliche Abnahme kein Ersatz ihres ursprünglichen BT.

## KIS unverändert ernst nehmen
Derselbe Speakr-Webclient wird nach Herstellervertrag in inesKIS/Edge-WebView eingebettet. Anzeige, SSO/Benutzerwechsel, Patient-/Fallkontext, Schreibvertrag und Quittung/Lookup sind fünf gesonderte Nachweise. Originalfunktionen ohne KIS ersetzen diese Anforderungen nicht. Keine KIS-API erfinden, kein Datenbankschreibzugriff, kein UI-/Clipboard-/Fenster-Scraping zur Patientenidentität.

Kontext über gebundenen vertrauenswürdigen Herstellerweg, interne user.id unabhängig von local/Entra. Patientwechsel in Host bindet vorhandenes Diktat nicht um. Akteure/Fallrechte serverseitig verifizieren. Token in WebViewprofilen, Cookies, Audio-/Mikrofon-/Downloadpermissions und Benutzerwechsel real testen. Auf Clientseite keine generische Hostobjekt- oder Native-Messagingbrücke.

## Nachträge und Wiederherstellung
Reopen ist neue Revision; bestätigte KISausgabe unverändert bis freigegebenem Nachtragsverfahren. Falscher Patient, Patientenmerge, Fallkorrektur und Rückweisung separate Prozesse. Dokument-/Audio-/Kontextreferenzen müssen auch DB-seitig zusammengehören. Restore der Plattform mit älterem Exportledger verlangt KIS-Abgleich, nicht automatisch neue Dokumente. Manueller Fallback erst nach geklärtem Kanalstatus.
