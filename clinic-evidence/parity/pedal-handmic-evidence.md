# Pedal, hand microphone and source-switch evidence (P-230)

Rows: PAR-OPS-001 / BT-OPS-001 / WB-011, PAR-OPS-002 / BT-OPS-002, PAR-COR-004 / BT-COR-004, PAR-ASR-007 / BT-ASR-007.

**Initial status: `not_run` for every row and every device/browser combination.** No approved physical pedal or handmic/DPM, exact model/firmware/driver inventory, model-specific WebHID report contract, two-source lab, or real target run was available. Synthetic keyboard events and mocks are not device evidence. This record changes no catalog status and closes no web gap.

## Run manifest and prerequisites

For each run record operator/date, target commit, synthetic marked-audio SHA-256 and duration, exact device model, firmware, connection/dock mode, OS and exact driver version, full browser version, keyboard/WebHID mode, key mapping, and approved vendor HID report specification. Record the two microphone identities and the permitted compressed-output codec. Unidentified or absent models remain `not_run`; never infer support from another model, driver, or browser.

The manual browser matrix is **Google Chrome, Microsoft Edge, and Mozilla Firefox**, using the clinic-approved pinned versions. Headless Chromium automation is only a development check, not physical-device evidence. An unavailable browser/API/driver stays `not_run`. Use synthetic audio only, a real audible output, explicit user permissions, and no native helper, extension, native messaging, localhost tunnel, or cloud ASR.

## Reproducible lab procedure

1. Open the approved staging build, load marked audio, connect the two approved sources, verify audible playback, and save the initial manifest, permissions, mappings, UI state, and browser console/event log.
2. For each pedal model/browser, exercise play toggle, hold-to-play/release-to-pause (including auto-rewind), rewind, stop, and recording where supported. Change the mapping, restart the browser, and verify the persisted mapping and context-specific recording/correction actions.
3. For each handmic/DPM, exercise record, standby, stop, playback, correction navigation, and EOL. Compare physical control, browser event, UI state, audible result, and each actual LED. Record `unsupported` only from exact-model documentation/observation; never simulate an LED capability.
4. Play at 0.5x, 1x, and 2x, reset to normal, then pedal-rewind/fast-forward. Confirm intelligible marks, text/audio position consistency, 1x after reset, and displayed audio time rather than wall time.
5. During a held action and again while recording, remove page focus, hide the tab, revoke device/microphone permission, disconnect, and reconnect. Confirm no blurred input is consumed, held/latching actions are released, and loss cannot leave uncontrolled recording. Record permission and reconnect behavior independently.
6. Select each source at session start and switch during a session; verify visible confirmation and that the next recording comes from the selected source. Through the permitted file-export path, verify actual standard/compressed codecs and playback, and rejection of unsupported formats rather than renamed WAV. No mail or KIS delivery is included.
7. Attach raw trusted-event/HID logs, permission logs, UI captures, source/output hashes and codec probes, audible observations, and operator sign-off. A status changes only with its linked real target artifact: observed failure is `failed`; an untried combination remains `not_run`.

## Observation ledger — all cells initially `not_run`

Repeat every row for each exact model/driver/browser. Do not combine physical action, browser event, audible response, LED support, focus, and disconnect into one aggregate pass.

| Exact device / driver / browser | Physical action | Browser event | Audible response | LED support | Focus / blur | Disconnect / permission / reconnect | Status / artifact |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Pedal: identity unavailable | Play toggle | not_run | not_run | not_run | not_run | not_run | not_run |
| Pedal: identity unavailable | Hold and release | not_run | not_run | not_run | not_run | not_run | not_run |
| Pedal: identity unavailable | Rewind / fast-forward and tempo reset | not_run | not_run | not_run | not_run | not_run | not_run |
| Pedal: identity unavailable | Stop | not_run | not_run | not_run | not_run | not_run | not_run |
| Handmic/DPM: identity unavailable | Record / standby / stop | not_run | not_run | not_run | not_run | not_run | not_run |
| Handmic/DPM: identity unavailable | Correction navigation / EOL | not_run | not_run | not_run | not_run | not_run | not_run |
| Approved source pair: identities unavailable | Source switch and permitted output | not_run | not_run | not_run | not_run | not_run | not_run |
