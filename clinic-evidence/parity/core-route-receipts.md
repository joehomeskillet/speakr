# Core route and editor receipts (P-037)

Rows: PAR-WF-008, PAR-COR-006, PAR-COR-008, PAR-COR-010, PAR-ADM-003, PAR-ASR-001, PAR-OPS-009.
Run date 2026-11-27. Build: worktree `par-p-037`, base `7acabaf4` (`fork/dev`), no source changes. Data: synthetic only.
This file records evidence. It does not change `matrix.json`, and no open web gap is closed by it.

## Commands

| Id | Command (run from the worktree root) | Result |
| --- | --- | --- |
| C1 | `venv/bin/python -m pytest <23 test files, listed below> -q --tb=line -p no:cacheprovider --timeout=240 --deselect tests/clinic/test_manual_first_correction.py::test_signed_in_owner_types_saves_and_reopens` | `352 passed, 1 skipped, 1 deselected in 51.50s` |
| C2 | `node_modules/.bin/vitest run <10 UI test files, listed below> --reporter=json` | 161 tests, 161 passed, 0 failed, 0 pending |
| C3 | `venv/bin/python -m pytest tests/clinic/test_manual_first_correction.py tests/clinic/test_p03_detail_playback_browser.py tests/clinic/test_p06_audio_edit_browser.py -v --tb=short` | `6 failed, 2 passed in 237.86s` (see "Failed and unavailable") |

C1 files: test_workflow, test_wp_workflow_api, test_wp_workflow_actions, test_correction, test_revisions,
test_wp_correction_transaction, test_wp_correction_commit_primitives, test_wp_correction_api, test_wp_v8_save_contract,
test_playback_tempo, test_playback_gain, test_manual_first_correction, test_pools, test_wp_pools_api, test_wp_v8_pool_admin,
test_asr_local_only, test_par_asr_001_local_url_edges, test_whisper_segments, test_asr_fallback, test_clinic_models,
test_ingest, test_decoder, test_wp_audio_api (all under `tests/clinic/`).
C2 files (all under `tests/clinic/ui/`): playback-gain-ui, playback-tempo-ui, v8-save-contract, correction-save-ui,
workflow-actions-ui, v8-schreibplatz, compose-correction, correction-editor-ui, compose-admin-pools, compose-asr-profiles.

## Row receipts

Outcome is `passed` unless stated. "stub" means the ASR side is a local stub or mock, not a real engine.

### PAR-WF-008 workflow classification (Vorbereitung, Zu schreiben, Zu korrigieren)

| Run | Exact node | Outcome | Artifact |
| --- | --- | --- | --- |
| C1 | `tests/clinic/test_workflow.py::test_transition_table_allows_documented_moves_and_rejects_the_rest` | passed | transition table |
| C1 | `tests/clinic/test_workflow.py::test_offline_and_online_preparation_follow_recognition` | passed | Offline/Online preparation split |
| C1 | `tests/clinic/test_workflow.py::test_worklist_groups_by_status_with_count_and_audio_length` | passed | worklist grouping by status |
| C1 | `tests/clinic/test_workflow.py::test_close_restores_the_correction_queue` | passed | queue after close |
| C1 | `tests/clinic/test_wp_workflow_api.py::test_metadata_positive_stale_and_idempotent_retry` | passed | route, stale version, idempotent retry |
| C1 | `tests/clinic/test_wp_workflow_api.py::test_acceptance_commands_dispatch_with_revision_and_policy` | passed | route, acceptance commands |
| C1 | `tests/clinic/test_wp_workflow_actions.py::test_submit_reject_and_resubmit_are_independent_of_asr_job` | passed | actions do not depend on the ASR job |
| C2 | `workflow-actions-ui.test.js > workflow actions ui > shows only authorized known actions, grouped, apart from status` | passed | UI action groups |

Not shown: a browser run of the status classification (see C3).

### PAR-COR-006 save, reopen at position

| Run | Exact node | Outcome | Artifact |
| --- | --- | --- | --- |
| C1 | `tests/clinic/test_correction.py::test_state_saves_position_and_guards_stale_writes` | passed | stored audio position, stale write refused |
| C1 | `tests/clinic/test_wp_correction_transaction.py::test_successful_transaction` | passed | revision and position stored in one transaction (500 ms) |
| C1 | `tests/clinic/test_wp_correction_transaction.py::test_transaction_rolls_back_on_failure` | passed | rollback leaves no partial save |
| C1 | `tests/clinic/test_wp_correction_transaction.py::test_lease_mismatch_rejected` | passed | stale lease refused |
| C1 | `tests/clinic/test_wp_correction_api.py::test_save_derives_links_and_reports_unaligned` | passed | route save |
| C1 | `tests/clinic/test_wp_v8_save_contract.py::test_save_with_current_etag_returns_next_etag_and_chains` | passed | ETag chain |
| C1 | `tests/clinic/test_wp_v8_save_contract.py::test_stale_revision_in_if_match_is_409_revision_conflict` | passed | conflict |
| C1 | `tests/clinic/test_playback_tempo.py::test_resume_position_subtracts_back_ms` | passed | resume position rule |
| C1 | `tests/clinic/test_revisions.py::test_append_does_not_change_an_existing_revision` | passed | append-only revisions |
| C2 | `correction-save-ui.test.js > correction save ui > resumes the committed position from the server and keeps nothing in browser storage` | passed | UI reopen at the stored position |
| C2 | `compose-correction.test.js > compose-correction contract > loading and shared state > loads text, audio revision, links, resume position and defaults to the W6 mode` | passed | composed view reopen |
| C2 | `compose-correction.test.js > ... > save, W10 follow-up, stale revision and retry > sends the audio position of the player with the save` | passed | position sent with save |

Real browser save and reopen: `tests/clinic/test_manual_first_correction.py::test_signed_in_owner_types_saves_and_reopens` failed in C3 (environment, see below). Reopen at position is therefore evidenced at route level (Python) and component level (vitest) only, not in a real browser.

### PAR-COR-008 playback gain versus original bytes

| Run | Exact node | Outcome | Artifact |
| --- | --- | --- | --- |
| C1 | `tests/clinic/test_playback_gain.py::test_apply_gain_returns_new_list` | passed | gain returns a new list, input not mutated |
| C1 | `tests/clinic/test_playback_gain.py::test_apply_gain_clips_to_int16` | passed | clipping bound |
| C1 | `tests/clinic/test_playback_gain.py::test_gain_curve_peak_ceiling_not_exceeded` | passed | peak ceiling |
| C1 | `tests/clinic/test_playback_gain.py` (28 nodes) | 28 passed | curve, window stats, invalid input |
| C2 | `playback-gain-ui.test.js > playback gain ui > applies a manual gain exactly and leaves the source byte-identical` | passed | source buffer identical after gain |
| C2 | `playback-gain-ui.test.js > playback gain ui > raises a quiet fixture in auto mode, measurably, without touching the source buffer` | passed | auto gain, measured |
| C2 | `playback-gain-ui.test.js > playback gain ui > fails soft without an audio context: unavailable, no throw, no gain` | passed | fallback state "nicht verfügbar" |
| C1 | `tests/clinic/test_wp_audio_api.py::test_cut_invalidates_anchors_supersedes_release_and_keeps_original` | passed | stored original unchanged by audio edit |
| C1 | `tests/clinic/test_ingest.py::test_stored_bytes_match_the_stream_and_are_not_replaced` | passed | stored original equals the input stream |

The gain is applied in the browser player (Web Audio). The vitest nodes use an injected audio context and a synthetic buffer. No test measures gain through a real browser audio output.

### PAR-COR-010 correction workplace without ASR

| Run | Exact node | Outcome | Artifact |
| --- | --- | --- | --- |
| C1 | `tests/clinic/test_manual_first_correction.py::test_normal_save_ingress[audio]` | passed | real Flask ingress; recording status FAILED (no transcript), stored audio, first save, second save, SHA of original unchanged |
| C1 | `tests/clinic/test_manual_first_correction.py::test_normal_save_ingress[header]` | passed | same flow without an audio revision |
| C2 | `correction-editor-ui.test.js > correction editor ui > runs in manual mode when ASR produced no text and edits only with the lease` | passed | UI manual mode |
| C3 | `tests/clinic/test_manual_first_correction.py::test_signed_in_owner_types_saves_and_reopens` | failed | not evidence (environment, see below) |

ASR attribution: the two `test_normal_save_ingress` nodes call no ASR at all. The outage is modelled by the recording status FAILED and no transcript revision, with the stored audio present. No stub engine is involved. A real unreachable ASR endpoint is not tested at route level.

### PAR-ADM-003 staff pools

| Run | Exact node | Outcome | Artifact |
| --- | --- | --- | --- |
| C1 | `tests/clinic/test_pools.py::test_staff_member_sees_linked_author_pools_only` | passed | link to author pools |
| C1 | `tests/clinic/test_wp_pools_api.py::test_save_staff_pool_membership_and_rename` | passed | route, staff pool membership and rename |
| C1 | `tests/clinic/test_wp_pools_api.py::test_stale_revision_is_409_and_changes_nothing` | passed | stale revision |
| C1 | `tests/clinic/test_wp_v8_pool_admin.py` (18 nodes) | 18 passed | pool admin routes |
| C2 | `compose-admin-pools.test.js` (31 nodes) | 31 passed | pool admin UI component |

Row stays partial. These are component and route tests, not a browser run of the admin page (see C3).

### PAR-ASR-001 local background transcription

| Run | Exact node | Outcome | Artifact | ASR |
| --- | --- | --- | --- | --- |
| C1 | `tests/clinic/test_asr_local_only.py::TestOpenAIWhisperConnectorClinicGuard` (5 nodes) | passed | non-local URL refused | none |
| C1 | `tests/clinic/test_par_asr_001_local_url_edges.py` (all nodes of the file) | passed | URL edge cases | none |
| C1 | `tests/clinic/test_whisper_segments.py::test_processing_persists_segments_without_diarization` | passed | segments persisted | stub (mock connector) |
| C1 | `tests/clinic/test_asr_fallback.py::test_gpu_connection_error_falls_back` | passed | GPU outage falls back to CPU target | stub (local HTTP stub) |
| C1 | `tests/clinic/test_asr_fallback.py::test_gpu_4xx_does_not_fall_back` | passed | client error not retried | stub |
| C1 | `tests/clinic/test_asr_fallback.py::test_cpu_failure_after_fallback_is_not_retried` | passed | both targets down, one attempt each | stub |

Row stays partial. Every ASR response above comes from a stub or mock. No real local engine ran.

### PAR-OPS-009 original and working copy kept apart

| Run | Exact node | Outcome | Artifact |
| --- | --- | --- | --- |
| C1 | `tests/clinic/test_clinic_models.py::test_original_cannot_be_updated_in_place` | passed | original immutable |
| C1 | `tests/clinic/test_clinic_models.py::test_original_cannot_be_deleted` | passed | original not deletable |
| C1 | `tests/clinic/test_ingest.py::test_stored_bytes_match_the_stream_and_are_not_replaced` | passed | stored bytes equal stream |
| C1 | `tests/clinic/test_decoder.py::test_good_wav_decodes_and_original_untouched` | passed | decoder leaves the original alone |
| C1 | `tests/clinic/test_wp_audio_api.py::test_cut_invalidates_anchors_supersedes_release_and_keeps_original` | passed | edit keeps the original |
| C1 | `tests/clinic/test_manual_first_correction.py::test_normal_save_ingress[audio]` | passed | original SHA identical before and after saves |

## Skipped

| Exact node | Reason (verbatim) |
| --- | --- |
| `tests/clinic/test_decoder.py::test_real_ds2_decodes_to_16k_mono_pcm` | `Requires CLINIC_DSS_CODEC_BIN and CLINIC_PRIVATE_DS2_FIXTURE` (PAR-OPS-009, Philips DS2: stays not_run) |

## Failed and unavailable

Failed (C3, 6 nodes), all in the browser harness:

- `tests/clinic/test_manual_first_correction.py::test_signed_in_owner_types_saves_and_reopens`: `Locator.click: Timeout 30000ms exceeded ... <div class="app-loading-overlay">…</div> intercepts pointer events`
- `tests/clinic/test_p03_detail_playback_browser.py::test_read_only_detail_plays_seeks_cut_audio_without_a_lease_or_export[390-844]` and `[1440-900]`, `::test_detail_explains_a_missing_media_source`: `Locator.click: Timeout 30000ms exceeded` waiting for the row
- `tests/clinic/test_p06_audio_edit_browser.py::test_cut_trim_undo_redo_and_reopen_with_real_player` and `::test_segment_insert_overwrite_microphone_and_persisted_undo`: `Locator.click: Timeout 30000ms exceeded` waiting for `open-link`

Cause, found by inspection: the worktree has no `static/vendor` (gitignored, copied or linked per `tests/clinic/browser/README.md`; the `vendor` symlinks in other worktrees point to deleted directories) and no `static/css/tailwind.css`. `templates/index.html` loads `vendor/js/vue.global.js`, so the Vue app never mounts and the loading overlay stays. This is an environment gap, not a product finding. The same six nodes were not changed by this package. The two `test_normal_save_ingress` nodes and all vitest nodes do not need the browser and passed.

Unavailable:

- Real browser save, reopen at position, gain audible through a real player, and the isolated unavailable-ASR browser flow: not run (above).
- Visual screenshots at 1440x900, 1280x720, 390x844 in light and dark: not taken. This package changes no view, and the harness cannot mount the app here.
- Real local ASR engine, Philips devices, Entra, KIS and foot pedals: no evidence from these runs. Those rows stay not_run where applicable.

## Staging check mapping (orchestrator)

Compare Aufnahmen, then Schreibplatz: save and reopen with a synthetic recording (PAR-COR-006), play with gain (PAR-COR-008, compare against the original download for byte identity, PAR-OPS-009), then a stored recording with an isolated unreachable ASR fixture (PAR-COR-010, PAR-ASR-001).
