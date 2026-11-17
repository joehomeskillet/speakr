# 14 – Wortschatz, Befehle, Profile und lokale KI
## Zwei verschiedene Verträge
Verwaltung von Wortlisten/Profilen ist nicht gleich nachgewiesene Wirkung in der Engine. Jede Ressource ist versioniert, scopegebunden (user/author_pool), mit Autor, Zeitpunkt und Quelle. Geschriebene/gesprochene Form, Aussprachevarianten, Status aktiv/negativ/vorgemerkt und Prüfhistorie getrennt. Textimport/export mit Unicode, Deduplizierung, Grenzwerten und Vorschau. Verwerfen/Benutzer/Pool sind unterschiedliche Entscheidungen; verschobene Prüfung bleibt eine ausstehende Aufgabe.

## Effectiveness-Gates
Capabilities: batch_transcription, live_transcription, hotword_bias, negative_lexicon, pronunciation_lexicon, pronunciation_training, language_adaptation, acoustic_adaptation, profile_checkpoint, alignment, local_tts. Jeder Eintrag enthält implemented, tested_model, testset_hash, evidence und Grenzen. Fehlende Fähigkeit liefert CAPABILITY_UNAVAILABLE, kein erfolgreiches leeres Jobresultat. Ein Hotwordprompt kann eine Hilfestellung sein, ist aber weder Negativlexikon mit garantierter Unterdrückung noch persönliches Akustiktraining. Fehlende Capabilities erfordern einen ASR-Adapter-/Modellspike, nicht die Streichung der PAR-Anforderung.

## Befehlsmodell
Befehle lösen nur erlaubte Webaktionen aus: Text-/Grafikbaustein, Punktuation/Format, Cursor innerhalb Editor, Draftaktion und Workflowaktion nach Rechteprüfung. Before/after-Hooks sind deklarative Aktionlisten mit Zyklus-/Tiefenlimit, keine Shell/JavaScript-eval oder OS-Tastensimulation. Schutzbedürftige Aktionen (Freigabe/Löschen/Empfängerwechsel) brauchen explizite Bestätigung; erkannter Sprachbefehl allein ist kein medizinischer Freigabenachweis. Befehlsversprecher dürfen keine Hintergrundaktion auslösen. Originaldiktat bleibt nachvollziehbar; Korrektur zeigt Befehl-/Textmarken.

## Profilmodell
Live- und Batchprofil pro Autor/Quelle mit optional gemeinsamem Checkpoint. Profilreferenz bindet Engine-/Modellhash, Ressourcenrevisionen, Parameter, Quelle und Persistenzpolicy never/ask/always. Zentraler Profilstand wird auf neuem Arbeitsplatz geladen; keine native Dragoninstallation. Anzeige eines Profils ist nicht die Behauptung formatkompatibler Dragonprofile. Originale Modelle/Schlüssel aus dem Archiv werden nicht importiert.

## Anpassung aus Korrekturen
Trainingsmaterial nur aus bewusst freigegebenen Korrekturversionen, mit Daten-/Berechtigungsnachweis. Text+Audio nur bei bestätigter vollständiger Übereinstimmung und passenden Ankern, keine nicht gesprochenen Ergänzungen als Wahrheit. Korpusmanifest mit Herkunft, Consent/Policyreference, Sprach-/Sprecher-/Gerätabdeckung und Ausschlussstatus. Jobs lokal in freigegebenen Zeitfenstern, Mindestdatenprüfung, reproduzierbarer Checkpoint, getrenntes Testset gegen Overfitting; ungeprüfter Checkpoint nie live aktivieren. Regression, Signoff und Rollback pflegen. Training ist eigenständige Enginefähigkeit, nicht bloss SQL-Update einer Liste.

## Betrieb und Qualität
Default lokale Batchtranskription, Sprache de, explizites Modell, keine Cloudumleitung. ASR-Timeout bietet manuelle Abschrift bei validem Audio. Bereits korrigierte Texte bleiben vor Reprozessierung geschützt. WER/CER plus kritische Fehler und Korrekturzeit; Zahlen, Negation, Medikamente, Dialekt, Überlappung und Lärm im freigegebenen Testsatz. Keine pauschale Genauigkeitsgarantie aus Modellnamen. Live/TTS können Hardwareanforderungen haben; CPU ist Entwicklungs-/Funktionspfad, kein Nachweis geforderter Latenz.
