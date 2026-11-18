# Paritätsmatrix DNS-Comfort (101 PAR-Funktionen)

Stand: Branch `clinic/parity-matrix` auf Basis `clinic/main`. Kein Anspruch auf volle Parität: `full_parity_claim_allowed` ist `false` (clinic-spec/planning/web-parity-gaps.json); die 13 dort gelisteten Lücken bleiben offen.

Status-Regeln: `implemented` nur mit bestandenem Test unter `tests/clinic`; Code ohne Test = `partial`; `open-web-gap` für alle Einträge aus web-parity-gaps.json (nie geschlossen). `removed-by-owner` wird nicht vergeben: Die Entfernungsentscheide betreffen Speakr-Zusatzfunktionen, keine PAR-Funktion.

Testlauf (einmal): `1531 passed, 1 skipped in 70.43s (0:01:10)`. Übersprungen: `tests/clinic/test_decoder.py:142` (benötigt CLINIC_DSS_CODEC_BIN und privates DS2-Fixture). Die Tests tragen keine PAR-IDs; die Zuordnung ist eine inhaltliche Einschätzung (Policy-, Pool-, Lease-, Release-, Export-, Ingest-Ebene). UI-Verhalten ist durch tests/clinic nirgends belegt.

## Zählung

| Status | Anzahl |
|---|---|
| implemented | 4 |
| partial | 25 |
| missing | 59 |
| open-web-gap | 13 |
| removed-by-owner | 0 |

## Matrix

| ID | Titel | Status | Evidenz | Hinweis |
|---|---|---|---|---|
| PAR-ADM-001 | Autoren und Schreibkräfte verwalten | partial | `src/clinic/identity.py`<br>`tests/clinic/test_identity_lifecycle.py::test_create_local_user_is_pending_without_password`<br>`tests/clinic/test_identity_lifecycle.py::test_disable_blocks_login_and_reactivate_restores` | Lokaler Benutzer-Lebenszyklus getestet. Getrennte Anlage/Bearbeitung von Autoren und Schreibkräften samt Übernehmen/OK-Admin-UI ist nicht getestet. |
| PAR-ADM-002 | Autorenpools mit Mehrfachmitgliedschaft | partial | `src/clinic/pools.py:87`<br>`tests/clinic/test_pools.py::test_same_name_pools_do_not_transfer_rights`<br>`tests/clinic/test_pools.py::test_removing_membership_removes_derived_rights` | Autorenpools und Mitglieder als Service implementiert; Mehrfachmitgliedschaft nicht gezielt getestet, keine Admin-UI. |
| PAR-ADM-003 | Schreibkraftpools eigenständig verwalten | partial | `src/clinic/pools.py:95`<br>`tests/clinic/test_pools.py::test_staff_member_sees_linked_author_pools_only` | Schreibkraftpools als Service mit Verknüpfung zu Autorenpools; eigenständige Verwaltung per UI fehlt. |
| PAR-ADM-004 | Zugriff nach Autor oder Autorenpool mit Ausnahmen | implemented | `tests/clinic/test_s01_policy.py::test_author_scope_grant_reaches_only_the_listed_authors`<br>`tests/clinic/test_s01_policy.py::test_author_pool_grant_needs_the_recordings_pool`<br>`tests/clinic/test_s01_policy.py::test_deny_by_author_wins_over_a_grant`<br>`tests/clinic/test_pools.py::test_pool_grant_is_limited_to_that_pool_and_those_actions`<br>`tests/clinic/test_pools.py::test_deny_beats_every_grant` | Zugriff nach Autor oder Pool mit Deny-Ausnahmen auf Policy-Ebene getestet. Admin-UI zur Pflege fehlt. |
| PAR-ADM-005 | Aktionen unabhängig voneinander berechtigen | implemented | `tests/clinic/test_s01_policy.py::test_sensitive_actions_on_own_recording_need_a_grant`<br>`tests/clinic/test_s01_policy.py::test_own_scope_grant_adds_an_action_on_own_recordings_only`<br>`tests/clinic/test_s01_policy.py::test_pool_grant_without_actions_confers_nothing` | Aktionen einzeln berechtigbar (Policy-Ebene). Admin-UI fehlt. |
| PAR-ADM-006 | Autoren-Sichtbereiche und Schreibkraftauswahl | partial | `src/clinic/pools.py:405`<br>`tests/clinic/test_pools.py::test_staff_member_sees_linked_author_pools_only`<br>`tests/clinic/test_s01_routes.py::test_lists_counts_and_search_show_only_own_recordings` | Sichtbereiche getestet; Schreibkraftauswahl im Versandweg fehlt. |
| PAR-ADM-007 | Voreingestellte Empfänger und Selbstkorrektur | missing | - | Kein Code und kein Test unter src/clinic/ bzw. tests/clinic/ für diese Funktion gefunden. |
| PAR-ADM-008 | Administrative und Statistik-Accounts | partial | `tests/clinic/test_pools.py::test_admin_flag_alone_grants_nothing`<br>`tests/clinic/test_s01_review_findings.py::test_13_stats_scope_all_is_forbidden` | Admin-Flag vergibt keine Fachrechte; ein eigener Statistik-Account ist nicht implementiert. |
| PAR-ADM-009 | Windows-/AD-Auswahl und bestätigter AD-Abgleich | open-web-gap | - | Offene Web-Lücke WG-ADM-009 (clinic-spec/planning/web-parity-gaps.json, status open): Lokale Windows-Konten des Arbeitsplatzes auslesen. Bleibt offen. Unabhängig umsetzbar: serverseitiger AD-/Directory-Abgleich, lokale Appkonten. |
| PAR-ADM-010 | Windows-SSO mit eindeutigem Kontext | partial | `src/clinic/entra.py`<br>`tests/clinic/test_entra_adapter.py::test_happy_path_with_prelinked_subject` | Entra-OIDC-Adapter nur gegen Mock/Fixtures; kein Windows-SSO-Nachweis (AGENTS.md: Mocks sind keine Evidenz für Entra). |
| PAR-ADM-011 | Benutzerstart, letzte Auswahl und Wechsel | missing | - | Kein Code und kein Test unter src/clinic/ bzw. tests/clinic/ für diese Funktion gefunden. |
| PAR-CFG-001 | Zentrale Vorgaben und lokale Änderungen | partial | `src/clinic/config.py`<br>`tests/clinic/test_config_profiles.py::test_setting_ceilings`<br>`tests/clinic/test_config_profiles.py::test_locked_features_in_clinic_mode`<br>`tests/clinic/test_config_profiles.py::test_client_cannot_select_its_profile` | Serverseitige Profile, Sperren und Obergrenzen; keine lokalen Änderungen pro Arbeitsplatz/UI. |
| PAR-CFG-002 | Vorlagen zentral speichern und zuordnen | missing | - | Kein Code und kein Test unter src/clinic/ bzw. tests/clinic/ für diese Funktion gefunden. |
| PAR-CFG-003 | Versandkategorien und Pflichtangaben | missing | - | Kein Code und kein Test unter src/clinic/ bzw. tests/clinic/ für diese Funktion gefunden. |
| PAR-CFG-004 | RegEx-basierte Metadatenzuweisung mit Vorschau | missing | - | Kein Code und kein Test unter src/clinic/ bzw. tests/clinic/ für diese Funktion gefunden. |
| PAR-CFG-005 | Konfigurierbare Metadatenspalten | missing | - | Kein Code und kein Test unter src/clinic/ bzw. tests/clinic/ für diese Funktion gefunden. |
| PAR-REC-001 | Recorder mit Titel-, Aufnahme- und Versandphase | missing | - | Kein Code und kein Test unter src/clinic/ bzw. tests/clinic/ für diese Funktion gefunden. |
| PAR-REC-002 | Aufnahme- und Wiedergabesteuerung | missing | - | Kein Code und kein Test unter src/clinic/ bzw. tests/clinic/ für diese Funktion gefunden. |
| PAR-REC-003 | Audio einfügen, überschreiben, kürzen und rückgängig | missing | - | Kein Code und kein Test unter src/clinic/ bzw. tests/clinic/ für diese Funktion gefunden. |
| PAR-REC-004 | Stimmgesteuerte Aufnahme | missing | - | Kein Code und kein Test unter src/clinic/ bzw. tests/clinic/ für diese Funktion gefunden. |
| PAR-REC-005 | Audio-Geräte und Aufnahme-/Wiedergabepegel | missing | - | Kein Code und kein Test unter src/clinic/ bzw. tests/clinic/ für diese Funktion gefunden. |
| PAR-REC-006 | Versandparameter und optionaler Diktatschutz | missing | - | Kein Code und kein Test unter src/clinic/ bzw. tests/clinic/ für diese Funktion gefunden. |
| PAR-REC-007 | Autarker Recorder und lokales Speichern | missing | - | Kein Code und kein Test unter src/clinic/ bzw. tests/clinic/ für diese Funktion gefunden. |
| PAR-REC-008 | Audio-Text-Sitzungen und Textdateien speichern/laden | missing | - | Kein Code und kein Test unter src/clinic/ bzw. tests/clinic/ für diese Funktion gefunden. |
| PAR-LIVE-001 | Live-Erkennung im eigenen Diktierfenster | missing | - | Kein Code und kein Test unter src/clinic/ bzw. tests/clinic/ für diese Funktion gefunden. |
| PAR-LIVE-002 | Diktieren in allgemeine Windows-Textfenster | open-web-gap | - | Offene Web-Lücke WG-LIVE-002 (clinic-spec/planning/web-parity-gaps.json, status open): Direktdiktat in fremde native Windows-Textfelder. Bleibt offen. Unabhängig umsetzbar: Live-Diktat im Webeditor, bewusster Clipboard-/Dateiexport. |
| PAR-LIVE-003 | Sofortige Entwurfssicherung pro Phrase | missing | - | Kein Entwurfsspeicher pro Phrase im Code. |
| PAR-LIVE-004 | Mehrere Entwürfe und bewusstes Verwerfen | missing | - | Kein Code und kein Test unter src/clinic/ bzw. tests/clinic/ für diese Funktion gefunden. |
| PAR-LIVE-005 | Rich-Text-Bearbeitung und Versandformat | missing | - | Kein Code und kein Test unter src/clinic/ bzw. tests/clinic/ für diese Funktion gefunden. |
| PAR-LIVE-006 | Diktierfokus an Fenster binden | open-web-gap | - | Offene Web-Lücke WG-LIVE-006 (clinic-spec/planning/web-parity-gaps.json, status open): SpeechLock an fremdes Windows-Fenster. Bleibt offen. |
| PAR-LIVE-007 | Automatische Fokusbindung und immer oben | open-web-gap | - | Offene Web-Lücke WG-LIVE-007 (clinic-spec/planning/web-parity-gaps.json, status open): Automatische native Fokusbindung/Always-on-top. Bleibt offen. |
| PAR-LIVE-008 | Text und Aussprache vorlesen | missing | - | Kein Code und kein Test unter src/clinic/ bzw. tests/clinic/ für diese Funktion gefunden. |
| PAR-IMP-001 | Geräte erkennen und Diktate auflisten | partial | `apps/windows-import/src/Speakr.Clinic.ImportAgent/SourceWalker.cs`<br>`src/clinic/agent_channel.py`<br>`tests/clinic/test_agent_channel.py::test_inventory_grants_no_rights_and_creates_no_intent` | Inventar-Kanal serverseitig getestet; echte Philips-Geräte nicht (Hardware-Tests not_run). |
| PAR-IMP-002 | Autor aus Geräte-ID oder Aufnahme-Autor auflösen | partial | `src/clinic/ingest.py`<br>`tests/clinic/test_ingest.py::test_missing_author_keeps_the_file_until_an_author_is_known` | Datei bleibt bis zur Autorzuordnung erhalten; Auflösung aus Geräte-ID/Aufnahme-Autor nicht belegt. |
| PAR-IMP-003 | Manueller und gemeinsamer Geräteversand | partial | `src/clinic/agent_channel_ingest.py`<br>`tests/clinic/test_agent_channel.py::test_upload_with_interruption_and_resume`<br>`tests/clinic/test_agent_channel.py::test_replay_returns_same_receipt_and_stores_once` | Resumable Upload und Quittung getestet; manueller/gemeinsamer Geräteversand per UI fehlt. |
| PAR-IMP-004 | Automatischer Versand und EOL-Grenze | missing | - | Kein Code und kein Test unter src/clinic/ bzw. tests/clinic/ für diese Funktion gefunden. |
| PAR-IMP-005 | Quelllöschung nach bestätigter Übernahme | partial | `apps/windows-import/src/Speakr.Clinic.ImportAgent/PostImport.cs`<br>`tests/clinic/test_agent_channel.py::test_removal_needs_policy_intent_and_dictation_id`<br>`tests/clinic/test_agent_channel.py::test_receipt_does_not_permit_removal_by_default` | Serverseitige Löschfreigabe nur mit Intent und Quittung getestet; C#-Agent-Löschung liegt ausserhalb tests/clinic, echte Geräte nicht geprüft. |
| PAR-IMP-006 | Backupkopie und Aufbewahrungsfrist | partial | `apps/windows-import/src/Speakr.Clinic.ImportAgent/PostImport.cs:43` | move_backup im Agenten vorhanden; Aufbewahrungsfrist/Bereinigung fehlt; kein Test unter tests/clinic. |
| PAR-IMP-007 | Datei- und Mehrfachimport von Audio und Dokumenten | partial | `src/clinic/ingest.py`<br>`tests/clinic/test_ingest.py::test_stored_bytes_match_the_stream_and_are_not_replaced` | Einzel-Audioingest getestet; Mehrfachimport und Dokumente fehlen. |
| PAR-IMP-008 | Dropfolder-Autoversand | missing | - | Kein Code und kein Test unter src/clinic/ bzw. tests/clinic/ für diese Funktion gefunden. |
| PAR-IMP-009 | Geräteverwaltung, Autorenlisten und Schutzoptionen | open-web-gap | - | Offene Web-Lücke WG-IMP-009 (clinic-spec/planning/web-parity-gaps.json, status open): Schreibende Philips-Gerätekonfiguration. Bleibt offen. |
| PAR-IMP-010 | Firmwarebestand und Versionsvergleich | open-web-gap | - | Offene Web-Lücke WG-IMP-010 (clinic-spec/planning/web-parity-gaps.json, status open): Firmware auf PocketMemo schreiben. Bleibt offen. |
| PAR-WF-001 | Arbeitsliste für Autoren und Schreibkräfte | partial | `tests/clinic/test_s01_routes.py::test_lists_counts_and_search_show_only_own_recordings` | Sichtbarkeitsgefilterte Listen; eigene Arbeitsliste mit Status-Spalten fehlt. |
| PAR-WF-002 | Automatische und manuelle Aktualisierung | missing | - | Kein Code und kein Test unter src/clinic/ bzw. tests/clinic/ für diese Funktion gefunden. |
| PAR-WF-003 | Gespeicherte Filter und Metadatensuche | missing | - | Kein Code und kein Test unter src/clinic/ bzw. tests/clinic/ für diese Funktion gefunden. |
| PAR-WF-004 | Sortieren, Gruppieren und Layout merken | missing | - | Kein Code und kein Test unter src/clinic/ bzw. tests/clinic/ für diese Funktion gefunden. |
| PAR-WF-005 | Diktatmetadaten bearbeiten und neu zuweisen | missing | - | Kein Code und kein Test unter src/clinic/ bzw. tests/clinic/ für diese Funktion gefunden. |
| PAR-WF-006 | Verlauf eines Diktats anzeigen | partial | `src/clinic/revisions.py:110`<br>`tests/clinic/test_revisions.py::test_parent_chain_and_history_order`<br>`tests/clinic/test_pools.py::test_history_rows_are_written_and_append_only` | Revisions- und Pool-Verlauf serverseitig; Verlaufsanzeige (UI) fehlt. |
| PAR-WF-007 | Exklusive Bearbeitung und Freigabe | implemented | `src/clinic/lease.py`<br>`tests/clinic/test_review_lease.py::test_acquire_heartbeat_expiry_and_takeover`<br>`tests/clinic/test_review_lease.py::test_double_acquire_by_two_users_yields_one_holder`<br>`tests/clinic/test_review_lease.py::test_foreign_holder_is_rejected` | Exklusive Bearbeitung per Lease inkl. Ablauf/Übernahme getestet. UI-Anbindung offen. |
| PAR-WF-008 | Vorbereitung, Abschrift und Korrektur unterscheiden | missing | - | Kein Code und kein Test unter src/clinic/ bzw. tests/clinic/ für diese Funktion gefunden. |
| PAR-WF-009 | Abnahme aktivieren und Berechtigte festlegen | missing | - | Kein Code und kein Test unter src/clinic/ bzw. tests/clinic/ für diese Funktion gefunden. |
| PAR-WF-010 | Rückweisung und erneute Abnahme | missing | - | Kein Code und kein Test unter src/clinic/ bzw. tests/clinic/ für diese Funktion gefunden. |
| PAR-WF-011 | Fertigstellen und Weiterverarbeitung | partial | `src/clinic/release.py:135`<br>`tests/clinic/test_release_binding.py::test_release_binds_exact_versions_and_stores_decision` | Freigabe an exakte Versionen gebunden; Weiterverarbeitung/Workflow-Schritt Fertigstellen fehlt. |
| PAR-WF-012 | Fertiges Diktat wieder zur Korrektur öffnen | partial | `src/clinic/release.py:235`<br>`tests/clinic/test_release_binding.py::test_new_revision_makes_release_inactive`<br>`tests/clinic/test_release_binding.py::test_new_release_after_change_is_active_old_stays_readable` | Neue Revision macht Freigabe inaktiv; Wiedereröffnen als Workflow-Aktion fehlt. |
| PAR-WF-013 | Archivieren, Löschen und Aufbewahrungsfrist | partial | `tests/clinic/test_s01_review_findings.py::test_1_merge_with_a_delete_flag_is_refused_while_hard_delete_is_locked` | Hartes Löschen gesperrt; Archivierung und Aufbewahrungsfrist nicht implementiert. |
| PAR-WF-014 | Benachrichtigungen bei Abnahme und Fertigstellung | missing | - | Kein Code und kein Test unter src/clinic/ bzw. tests/clinic/ für diese Funktion gefunden. |
| PAR-COR-001 | Manuelle Abschrift mit Player und Vorlage | missing | - | Kein Code und kein Test unter src/clinic/ bzw. tests/clinic/ für diese Funktion gefunden. |
| PAR-COR-002 | Synchroner Text-Audio-Editor | missing | - | Kein Code und kein Test unter src/clinic/ bzw. tests/clinic/ für diese Funktion gefunden. |
| PAR-COR-003 | Wellenform, aktuelle Phrase und Erkennungsmarken | missing | - | Kein Code und kein Test unter src/clinic/ bzw. tests/clinic/ für diese Funktion gefunden. |
| PAR-COR-004 | Tempo und Spulen | missing | - | Kein Code und kein Test unter src/clinic/ bzw. tests/clinic/ für diese Funktion gefunden. |
| PAR-COR-005 | Zwei Korrekturmodi und automatische Rücksprünge | missing | - | Kein Code und kein Test unter src/clinic/ bzw. tests/clinic/ für diese Funktion gefunden. |
| PAR-COR-006 | Zwischenspeichern und an Position fortsetzen | partial | `src/clinic/revisions.py:79`<br>`tests/clinic/test_revisions.py::test_append_does_not_change_an_existing_revision` | Zwischenstände als unveränderliche Revisionen; Positions-Fortsetzung im Editor fehlt. |
| PAR-COR-007 | Audioindizes verwalten | missing | - | Kein Code und kein Test unter src/clinic/ bzw. tests/clinic/ für diese Funktion gefunden. |
| PAR-COR-008 | Automatische Wiedergabelautstärke | missing | - | Kein Code und kein Test unter src/clinic/ bzw. tests/clinic/ für diese Funktion gefunden. |
| PAR-COR-009 | Editierfunktionen, Auswahl und Phrase vormerken | missing | - | Kein Code und kein Test unter src/clinic/ bzw. tests/clinic/ für diese Funktion gefunden. |
| PAR-COR-010 | Korrekturplatz ohne ASR-Installation | missing | - | Kein Code und kein Test unter src/clinic/ bzw. tests/clinic/ für diese Funktion gefunden. |
| PAR-VOC-001 | Benutzer- und Poolwortschatz | missing | - | Kein Code und kein Test unter src/clinic/ bzw. tests/clinic/ für diese Funktion gefunden. |
| PAR-VOC-002 | Textwortlisten importieren und exportieren | missing | - | Kein Code und kein Test unter src/clinic/ bzw. tests/clinic/ für diese Funktion gefunden. |
| PAR-VOC-003 | Negativwortliste und Reaktivierung | missing | - | Kein Code und kein Test unter src/clinic/ bzw. tests/clinic/ für diese Funktion gefunden. |
| PAR-VOC-004 | Vorbehaltswortliste prüfen | missing | - | Kein Code und kein Test unter src/clinic/ bzw. tests/clinic/ für diese Funktion gefunden. |
| PAR-VOC-005 | Sitzungsänderungen prüfen oder zurückstellen | missing | - | Kein Code und kein Test unter src/clinic/ bzw. tests/clinic/ für diese Funktion gefunden. |
| PAR-VOC-006 | Vokabular durchsuchen und aus Markierung ergänzen | missing | - | Kein Code und kein Test unter src/clinic/ bzw. tests/clinic/ für diese Funktion gefunden. |
| PAR-VOC-007 | Aussprache trainieren und Phonetik bearbeiten | missing | - | Kein Code und kein Test unter src/clinic/ bzw. tests/clinic/ für diese Funktion gefunden. |
| PAR-VOC-008 | Befehle, Makros und Textbausteine verwalten | missing | - | Kein Code und kein Test unter src/clinic/ bzw. tests/clinic/ für diese Funktion gefunden. |
| PAR-ASR-001 | Lokale Hintergrundtranskription | partial | `tests/clinic/test_asr_local_only.py::TestOpenAIWhisperConnectorClinicGuard`<br>`tests/clinic/test_whisper_segments.py::test_processing_persists_segments_without_diarization` | Nur lokale ASR-Endpunkte erlaubt, Segmente/Zeitstempel persistiert; kein Test mit echter lokaler Engine (nur Stub/Mock). |
| PAR-ASR-002 | Getrennte Online-/Offlineprofile und Quellen | partial | `src/clinic/config.py`<br>`tests/clinic/test_config_profiles.py::test_default_client_profile_is_managed_web_online` | Clientprofil-Default getestet; Offline-Profil und Quellenwahl fehlen. |
| PAR-ASR-003 | ASR-Wartezeit mit manueller Ersatzbearbeitung | partial | `tests/clinic/test_revisions.py::test_late_asr_does_not_replace_a_human_correction` | Späte ASR überschreibt keine manuelle Korrektur; Wartezeit-UI mit manuellem Ersatz fehlt. |
| PAR-ASR-004 | Optimierung aus geprüfter Korrektur | missing | - | Kein Code und kein Test unter src/clinic/ bzw. tests/clinic/ für diese Funktion gefunden. |
| PAR-ASR-005 | Optimierungsfenster und Mindestdatenmenge | missing | - | Kein Code und kein Test unter src/clinic/ bzw. tests/clinic/ für diese Funktion gefunden. |
| PAR-ASR-006 | Profil-Speicherung und Arbeitsstationswechsel | missing | - | Kein Code und kein Test unter src/clinic/ bzw. tests/clinic/ für diese Funktion gefunden. |
| PAR-ASR-007 | Diktierquelle und Versand-Audioformat | missing | - | Kein Code und kein Test unter src/clinic/ bzw. tests/clinic/ für diese Funktion gefunden. |
| PAR-EXP-001 | Mehrere Exportwege pro Abschluss | partial | `src/clinic/export.py:118`<br>`src/clinic/export.py:123`<br>`tests/clinic/test_export_service.py::test_released_export_contains_exactly_the_bound_text` | Nur Text- und DOCX-Download; weitere Exportwege fehlen. |
| PAR-EXP-002 | Zwischenablage formatiert/unformatiert | missing | - | Kein Code und kein Test unter src/clinic/ bzw. tests/clinic/ für diese Funktion gefunden. |
| PAR-EXP-003 | RTF-Ausgabe | missing | - | Kein Code und kein Test unter src/clinic/ bzw. tests/clinic/ für diese Funktion gefunden. |
| PAR-EXP-004 | Word-Ausgabe mit Vorlage und Textmarke | open-web-gap | `src/clinic/export.py:123`<br>`tests/clinic/test_export_service.py::test_docx_roundtrip_umlauts_and_long_text` | Offene Web-Lücke WG-EXP-004 (clinic-spec/planning/web-parity-gaps.json, status open): Einfügen in bereits geöffnetes natives Word-Dokument. Bleibt offen. Unabhängig umsetzbar: DOCX-Erzeugung vorhanden (src/clinic/export.py:123). |
| PAR-EXP-005 | OpenOffice-Writer-/ODT-Ausgabe | open-web-gap | - | Offene Web-Lücke WG-EXP-005 (clinic-spec/planning/web-parity-gaps.json, status open): Einfügen in bereits geöffnetes natives Writer-Dokument. Bleibt offen. |
| PAR-EXP-006 | Dateiname, Ablageort und Kollisionen | partial | `src/clinic/export.py:103`<br>`tests/clinic/test_export_service.py::test_filename_sanitising` | Dateinamen-Bereinigung getestet; Ablageort/Kollisionsbehandlung fehlt. |
| PAR-EXP-007 | Allgemeiner Fenstertransfer | open-web-gap | - | Offene Web-Lücke WG-EXP-007 (clinic-spec/planning/web-parity-gaps.json, status open): Fensterklasse/-titel, Vordergrund und Tastensimulation. Bleibt offen. |
| PAR-EXP-008 | Geschriebenes Dokument kopieren | missing | - | Kein Code und kein Test unter src/clinic/ bzw. tests/clinic/ für diese Funktion gefunden. |
| PAR-EXP-009 | Originalaudio speichern oder Mailentwurf öffnen | open-web-gap | - | Offene Web-Lücke WG-EXP-009 (clinic-spec/planning/web-parity-gaps.json, status open): Lokales Mailprogramm mit Audioanhang öffnen. Bleibt offen. |
| PAR-EXP-010 | Word-Add-in: Dokument und Audio übergeben | open-web-gap | - | Offene Web-Lücke WG-EXP-010 (clinic-spec/planning/web-parity-gaps.json, status open): Natives Word-Add-in mit aktueller Audiositzung. Bleibt offen. |
| PAR-OPS-001 | Fusssteuerung und Tastenzuordnung | missing | - | Kein Code und kein Test unter src/clinic/ bzw. tests/clinic/ für diese Funktion gefunden. |
| PAR-OPS-002 | Mikrofon-/Diktiergerätsteuerung und Rückmeldung | missing | - | Kein Code und kein Test unter src/clinic/ bzw. tests/clinic/ für diese Funktion gefunden. |
| PAR-OPS-003 | Hotkeys frei setzen und zurücksetzen | open-web-gap | - | Offene Web-Lücke WG-OPS-003 (clinic-spec/planning/web-parity-gaps.json, status open): Systemweite Hotkeys ohne Browserfokus. Bleibt offen. |
| PAR-OPS-004 | Autostart, Tray und programmatischer Start | open-web-gap | - | Offene Web-Lücke WG-OPS-004 (clinic-spec/planning/web-parity-gaps.json, status open): Nativer Tray-/Autostart-/Hintergrundprozess. Bleibt offen. |
| PAR-OPS-005 | Statistikerfassung und Aufbewahrung | missing | - | Kein Code und kein Test unter src/clinic/ bzw. tests/clinic/ für diese Funktion gefunden. |
| PAR-OPS-006 | Diktat-, Frist- und Bearbeitungsstatistiken | missing | - | Kein Code und kein Test unter src/clinic/ bzw. tests/clinic/ für diese Funktion gefunden. |
| PAR-OPS-007 | Statistik als CSV und ohne UI | missing | - | Kein Code und kein Test unter src/clinic/ bzw. tests/clinic/ für diese Funktion gefunden. |
| PAR-OPS-008 | Dienstbetrieb, Jobs und beobachtbare Fehler | partial | `tests/clinic/test_s01_review_findings.py::test_5_the_owner_may_cancel_an_own_job`<br>`tests/clinic/test_s01_review_findings.py::test_5_a_user_cannot_touch_a_foreign_job` | Job-Zugriffsschutz getestet; Dienstbetrieb und beobachtbare Fehler-Sicht fehlen. |
| PAR-OPS-009 | Original und Arbeitskopie getrennt aufbewahren | implemented | `src/clinic/models.py`<br>`tests/clinic/test_clinic_models.py::test_original_cannot_be_updated_in_place`<br>`tests/clinic/test_clinic_models.py::test_original_cannot_be_deleted`<br>`tests/clinic/test_ingest.py::test_stored_bytes_match_the_stream_and_are_not_replaced`<br>`tests/clinic/test_decoder.py::test_good_wav_decodes_and_original_untouched` | Unveränderliches Original, getrennte Arbeitskopie/Decoder getestet. Kein Nachweis für Philips-Echtgeräte. |
| PAR-OPS-010 | Gerätesteuerung in RDP-/ICA-Sitzungen | missing | - | Kein Code und kein Test unter src/clinic/ bzw. tests/clinic/ für diese Funktion gefunden. |
