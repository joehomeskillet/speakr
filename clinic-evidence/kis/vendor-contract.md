# KIS Host and Delivery Contracts (w188)

Reviewed 2026-11-18 on branch `clinic/wp-w188-kis-vendor-contract`.
Specification and contract evidence slice for PAR-EXP-001, R-026, R-027, R-028, R-029, R-030, G-KIS, and BT-EXP-001.

---

## 1. Executive Summary & Sizing

This work package establishes and records the exact host runtime and delivery contracts required for Hospital Information System (KIS / inesKIS) integration in the web-only clinic dictation platform (Speakr fork v0.11.1 at upstream commit `48d3018fb7d80b77f897bedd86bcc89b5034d1f5`).

In accordance with `clinic-spec/CLAUDE.md`, `AGENTS.md`, and the V4 web-only architectural boundaries:
- **No native client / No browser extensions**: The web client runs inside standard browsers or an approved Edge WebView2 host container. No native desktop helper, localhost tunnel, or clipboard daemon is permitted.
- **Fail-closed production integration**: In the absence of an executed, legally ratified manufacturer contract and mutual technical configuration, `DisabledAdapter` remains the active production implementation. Production gate **G-KIS** remains strictly **open**.
- **Deterministic synthetic development**: `MockKisAdapter` provides stateful, scriptable contract testing for development and CI, but is structurally barred from production environments (`MockInProductionError`).
- **Isolation of multi-target export (PAR-EXP-001 / BT-EXP-001)**: KIS delivery operates as an independent delivery target within the platform's multi-export lifecycle. Delivery failure or an `UNKNOWN` status in the KIS adapter does not invalidate or corrupt independent file (DOCX/Text) exports, and blind retries remain blocked.

---

## 2. Comparison: Internal Adapter Contract vs Required Host Runtime

The platform's internal adapter contract (`src/clinic/kis.py` and `clinic-spec/contracts/internal-adapters.md`) is designed to isolate the core clinical application from external vendor idiosyncrasies. The table below compares the internal implementation with the required vendor host runtime:

| Dimension | Existing Internal Adapter (`src/clinic/kis.py`) | Required Vendor Host Runtime (Edge WebView2 / inesKIS) | Compliance / Gap Analysis |
|---|---|---|---|
| **Host Runtime & Embedding** | Headless Python service interface invoked during export/release pipelines. Does not assume UI embedding. | Microsoft Edge WebView2 control embedded in the inesKIS clinical workstation shell (R-026, T-026). Requires isolated user data directory, profile isolation, and clean process teardown. | **Compatible boundary**: Flask/Vue web client renders inside WebView2. Localhost tunneling and native extensions are barred. |
| **Permitted Host Messages** | `check_context(context)` validates case and patient bindings against adapter memory or upstream database. | Structured `window.chrome.webview.postMessage` bridge exchanging signed JSON tokens. Messages restricted to: (1) context initialization, (2) case switch notification, (3) active user synchronization, (4) session termination. | **Strictly governed**: Unrestricted DOM manipulation, raw header injection (`X-KIS-Case`), and window title sniffing are rejected as untrusted. |
| **Identity & Authentication** | Local platform `user.id` is the primary anchor. Actions check actor permissions on the specific dictation and release. | Enterprise Identity / SSO (OIDC/Entra or Kerberos/IWA). Host transmits identity claims during context handshake. | **Local-first mapping**: Entra and external IdPs link strictly to existing internal `user.id` via audited linking (`src/clinic/identity.py`). No automatic email-based linking. Caller must hold authorized export role. |
| **Case & Patient Context** | `ClinicContextRevision` storing `source_system`, `tenant_scope`, `case_ref`, `patient_ref`. Bound via `bind_context_grant`. | inesKIS case context passing patient identifier (PID), case number (FID), clinical department, and encounter type. | **Replay-protected & append-only**: Context grants require cryptographic signature, strict audience, nonces, and < 60s TTL. Context is strictly bound to dictation revisions; no patient/case data in URLs or server access logs (R-027). |
| **Delivery Payload** | Frozen payload (`build_payload`): `release_id`, `dictation_id`, `text_hash` (Unicode NFC, LF, SHA-256), `document_type`, `release_type`, and nested case context. | Clinical document ingestion endpoint (HL7 FHIR `DocumentReference` + `Binary`, DICOM-SR, or vendor proprietary REST/SOAP). | **Frozen release binding**: Deliveries accept only immutable releases (`ClinicRelease`). Text hash matches exact reviewed editor content; no automatic rewriting or client-side tampering. |
| **Delivery Ledger & Idempotency** | `ClinicDelivery` table with composite key `(target, idempotency_key)`. State machine: `PENDING` -> `ACKNOWLEDGED` \| `FAILED` \| `UNKNOWN`. | Vendor document ingestion service with deduplication by client transaction key. | **Strict pre-commit**: Ledger row is durably committed as `PENDING` *before* the external adapter call. Replays with identical payload return recorded result without calling adapter (`sent=False`). Payload hash mismatch is refused. |
| **Lookup & Status Inquiry** | `lookup(idempotency_key, payload_hash) -> confirmed \| absent \| unknown`. | Status query API allowing reconciliation of in-flight or timed-out transmissions. | **Authoritative absence requirement**: To permit a safe retry, `lookup` returning `absent` must be an authoritative guarantee that no document exists or will asynchronously emerge. Ambiguous answers remain `unknown`. |
| **Amendment & Supersession** | Clinical releases are immutable. Context or transcript changes supersede active releases via `ClinicReleaseSupersession`. | Document versioning or addendum linking in the electronic patient record (EPR/KIS). | **Version-bound addenda (R-030)**: Corrections generate a new revision and new release. Delivery ledger treats amendments as distinct deliveries with fresh idempotency keys referencing the new release and linking the superseded release ID. |

---

## 3. Inventory of Unavailable Manufacturer Contract Fields

Because a formal, bilaterally executed interface agreement with the KIS manufacturer is not yet delivered, the following contract parameters are formally identified as **UNRESOLVED / MISSING EVIDENCE**:

1. **Protocol & Wire Transport**:
   - Exact wire format: HL7 FHIR (R4/R5) `DocumentReference` vs. SOAP/IHE XDS.b (`Provide and Register Document Set-b`) vs. proprietary REST endpoint.
   - Exact TLS certificate chain, mutual authentication (mTLS) trust anchors, and cipher requirements.
2. **Network Topology & Addressing**:
   - Ingress endpoints for intranet KIS gateway, load balancer failover VIPs, and staging versus production URLs.
   - Network timeout SLAs (connection timeout, read timeout, maximum processing window).
3. **Authentication & Token Exchange**:
   - OIDC client credentials grant vs. RFC 7523 JWT bearer assertions vs. static API tokens.
   - Token refresh window, token lifetime, and public key endpoint (`jwks_uri`) for verifying host message signatures.
4. **Data Taxonomy & Schema Validation**:
   - Exact coding system for document types (e.g., LOINC codes, KAS document classes, hospital-specific document catalogue).
   - Character encoding constraints (UTF-8 strict vs. Windows-1252 / ISO-8859-1 legacy requirements).
   - Maximum document size, binary attachment limits, and support for formatted text (HTML / RTF / PDF-A embedded).
5. **Lookup & Authoritative Absence Semantics**:
   - Vendor SLA on status query consistency: whether query-after-timeout guarantees read-your-own-writes consistency.
   - Vendor definition of document identifiers: format and uniqueness guarantee of `external_document_id`.
6. **Edge WebView2 Host Message Schema**:
   - Structure of host message envelopes (`window.chrome.webview.postMessage` payload contract).
   - Bridge message acknowledgment handshake and origin whitelisting (`targetOrigin` validation).
7. **Legal Digital Signature & EPR Archival**:
   - Workstation-level vs. server-side clinical signature semantics.
   - Retention and legal sealing policies in the primary medical archive.

### Fail-Closed Production Behavior: Retention of `DisabledAdapter`

To prevent accidental data loss, phantom transmissions, or unverified patient data leaks, `src/clinic/kis.py` enforces the following production invariants:

- **Default Factory Binding**: In production, unconfigured installations automatically resolve to `DisabledAdapter`.
- **Refusal of Operations**:
  - `check_context(context)` returns `ContextCheck(ok=False, reason="KIS adapter disabled")`.
  - `deliver(payload, idempotency_key)` unconditionally raises `RuntimeError("DisabledAdapter must never be asked to deliver")`.
  - `deliver()` service entry point checks `adapter.enabled`: returns `DeliveryResult(outcome=REFUSED, reason="KIS adapter disabled: nothing was sent")`.
  - No row is written to `ClinicDelivery` during a refused disabled delivery.
  - `lookup(...)` returns `LOOKUP_UNKNOWN`.
- **Production Gate G-KIS**: Remains in status `open` (`production_gate: true`) in `clinic-spec/planning/blockers.json`.

---

## 4. Synthetic Development Double: `MockKisAdapter` vs Real Acceptance Work

The codebase separates synthetic automated verification from real clinical hardware/vendor acceptance:

### Role of `MockKisAdapter`
- Enables full regression testing of the two-phase commit ledger, crash resilience, timeout recovery, and idempotency logic.
- Maintains a thread-safe, stateful in-memory registry of delivered documents (`documents[idempotency_key] -> {id, payload_hash, payload}`).
- Scriptable behavior sequence (`script=["accept", "reject", "timeout", "timeout_unsaved", "ambiguous", "crash"]`):
  - `accept`: Stores document, returns `AdapterAnswer("accepted", external_document_id)`.
  - `reject`: Stores nothing, returns `AdapterAnswer("rejected", reason=...)`.
  - `timeout`: Stores document, raises `AdapterTimeout` (simulating lost network response after server commit).
  - `timeout_unsaved`: Stores nothing, raises `AdapterTimeout` (simulating network failure before server commit).
  - `ambiguous`: Stores document, returns unrecognized status.
  - `crash`: Raises unexpected exception before storage.
- **Production Safety Ban**: Attempting to instantiate `MockKisAdapter` when `CLINIC_PROFILE=production` raises `MockInProductionError`. If the profile switches dynamically to `production`, `deliver()` aborts before calling the mock.

### Segregation of Real Acceptance Work
- `tests/clinic/test_mock_kis.py` and `tests/clinic/test_wp_trusted_context.py` test synthetic double contracts only.
- Mock execution **never** constitutes evidence for real KIS integration or closure of acceptance criteria T-026..T-030.
- Acceptance items T-026, T-027, T-028, T-029, and T-030 remain strictly `not_run`.
- Real vendor acceptance requires:
  1. Bilateral sign-off on the interface contract by hospital ICT and the KIS vendor.
  2. Provision of a certified integration testing environment with synthetic patient test records.
  3. Physical deployment inside the Edge WebView2 host container with mutual TLS and signed tokens.

---

## 5. Test Execution Evidence: Positive, Unauthorized, Stale-Version & Retry/Failure Cases

The following test suites in `tests/clinic/` verify the complete lifecycle of the KIS adapter contract, delivery ledger, and context binding:

### 1. Positive Cases
- **Accepted Delivery (`test_accepted_delivery_is_recorded`)**:
  - Pre-commits `ClinicDelivery` with state `PENDING`.
  - Adapter returns `accepted` with external document ID.
  - Ledger updates main row to `ACKNOWLEDGED`, persists `external_document_id`, and calculates 64-character SHA-256 payload digest.
- **Idempotent Replay (`test_replay_with_same_key_and_payload_sends_once`)**:
  - Repeated call with identical idempotency key and identical payload returns recorded `DELIVERED` outcome.
  - Flags `replayed=True`, `sent=False`.
  - External adapter is invoked exactly once; document count in adapter remains 1.
- **Reconciliation to Delivered (`test_reconcile_to_delivered_yields_exactly_one_document`)**:
  - Simulates adapter timeout where backend document was saved (`timeout`).
  - First attempt records `UNKNOWN`. Subsequent call without reconcile stays blocked.
  - `reconcile()` queries `adapter.lookup()`, receives `LOOKUP_CONFIRMED`, transitions ledger to `ACKNOWLEDGED`, and appends `#h0001:CONFIRMED` to history.
  - Subsequent delivery call returns `DELIVERED` without resending.
- **Valid Grant Binding (`test_valid_binding_preserves_history_and_supersedes_release`)**:
  - Context grant verified with valid issuer, audience, user, dictation, tenant, nonce, and timestamp.
  - Advances dictation version counter, preserves parent revision ID, and supersedes active release.

### 2. Unauthorized & Refused Cases
- **Disabled Adapter Refusal (`test_disabled_adapter_refuses_and_writes_nothing`)**:
  - `DisabledAdapter` passed to `deliver()` returns `REFUSED`.
  - `sent=False`, reason indicates adapter disabled.
  - Database contains 0 `ClinicDelivery` rows (no pollution or orphaned records).
- **Production Profile Ban on Mock (`test_mock_refuses_to_exist_in_production_profile`, `test_mock_instance_is_refused_if_profile_turns_production`)**:
  - Instantiation under `CLINIC_PROFILE=production` raises `MockInProductionError`.
  - Existing instance called under production profile returns `REFUSED` with `deliver_calls == 0`.
- **Unknown Context Refused (`test_context_unknown_to_kis_is_refused`)**:
  - Adapter `check_context` fails for unknown case/patient.
  - Returns `REFUSED` with `"context refused by KIS: case/patient unknown to KIS"`.
  - External delivery is not called (`deliver_calls == 0`).
- **Incomplete Context Refused (`test_incomplete_context_is_refused`)**:
  - Missing `patient_ref` or `case_ref` rejected at service gate before reaching adapter.
- **Browser Observations Rejected (`test_browser_observations_are_not_evidence`)**:
  - Headers (`X-KIS-Case`), DOM snippets (`<div>case-2</div>`), window titles, and transcript text rejected as invalid grants.

### 3. Stale-Version & Concurrency Cases
- **Mismatched Payload on Reused Key (`test_same_key_with_different_payload_is_refused`)**:
  - Reusing an existing idempotency key with different payload content returns `REFUSED` (`"idempotency key already used with a different payload"`).
  - Prevents accidental collision or cache poisoning.
- **Inactive or Superseded Release Refused (`test_inactive_or_superseded_release_is_refused`)**:
  - Releases with `is_active=False`, `superseded_by_id`, or `status="SUPERSEDED"` return `REFUSED`.
  - Zero adapter calls, zero delivery rows.
- **Stale Context Revision (`test_context_is_taken_only_from_the_bound_revision`)**:
  - Passing a context revision different from `release.context_revision_id` returns `REFUSED`.
- **Stale Dictation Version Counter (`test_stale_denied_or_unavailable_grant_can_be_retried`)**:
  - Attempting to bind a grant with an outdated `expected_version` raises `stale_version`.

### 4. Retry & Failure Cases
- **Ledger Pre-Commit on Adapter Crash (`test_ledger_row_exists_before_send`)**:
  - When adapter raises unexpected `RuntimeError` during send, the database transaction rolls back to the committed `PENDING` state.
  - Subsequent delivery attempt detects unconfirmed state, returns `UNKNOWN` (`replayed=True`), and blocks resend.
- **Timeout Blocks Blind Retry (`test_unknown_blocks_retry_until_reconcile`)**:
  - Simulating `timeout`, `ambiguous`, or `timeout_unsaved` transitions delivery to `UNKNOWN`.
  - Immediate redelivery is rejected with `UNKNOWN` (`"outcome unclear: reconcile first"`).
- **Reconciliation to Absent Allows Exactly One Retry (`test_reconcile_to_not_delivered_allows_one_new_attempt`)**:
  - When reconcile proves the document was not saved (`timeout_unsaved` -> `LOOKUP_ABSENT`), history logs `#h0001:ABSENT`.
  - Main row resets to `PENDING` and appends `#h0002:RETRY`.
  - Exactly one new delivery attempt is permitted, succeeding on the second call (`deliver_calls == 2`).
- **Undetermined Lookup Stays Blocked (`test_reconcile_unknown_lookup_stays_blocked`)**:
  - If `lookup()` returns `LOOKUP_UNKNOWN`, history logs `#h0001:UNDETERMINED`.
  - Delivery remains blocked in `UNKNOWN`.
- **Transactional Rollback on Write Failure (`test_rollback_preserves_release_and_does_not_consume_grant`)**:
  - Mid-operation database failure does not consume the grant nonce or corrupt the active release.

---

## 6. Original Behavior Test BT-EXP-001 Preservation

### Unchanged Oracle Contract
In accordance with `clinic-spec/CLAUDE.md` and `tests/acceptance.json`:
- **Test ID**: `BT-EXP-001` (Requirements `PAR-EXP-001`)
- **Given**: Corrected case; clipboard and RTF export options selected.
- **When**: Finalize and inspect both outputs. Restart, reopen options, make export destination of one adapter unwritable.
- **Then**: Selected destinations and user-specific options are preserved. Error in one adapter is reported separately; successful other export is auditable and intact.
- **Oracle Status**: `specified_not_observed`, `original_oracle_not_replaced: true`. All `given`, `when`, and `then` clauses remain byte-identical.

### Target Slice Contribution
This slice establishes the formal contract for KIS delivery within the multi-target export framework:
1. **Failure Domain Isolation**: KIS delivery failure (timeout, network partition, or rejection) produces an isolated target failure or `UNKNOWN` state recorded in `ClinicDelivery`. It does not abort, delete, or roll back independent file exports (DOCX/RTF) or clipboard operations.
2. **Per-Target Status Reporting**: Consumer APIs (`/dictations/{dictation_id}/output-attempts`) expose independent per-target execution receipts.
3. **Prevention of Uncontrolled Cascades**: An `UNKNOWN` KIS outcome blocks repeated KIS attempts without revoking previously acknowledged exports or secondary export formats.
4. **Catalog Status**: BT-EXP-001 remains `not_run`. Full end-to-end multi-target testing is scheduled for consumer package `w288-compare-exp`.

---

## 7. Handover to Scheduled Consumers

| Consumer Package | Owned Scope & Handover Responsibilities |
|---|---|
| `w189-kis-vendor-adapter` | Implements the concrete vendor adapter class inheriting from `KisAdapter`. Uses the required wire protocols, TLS/token configuration, error mappings, and authoritative absence rules recorded herein. Retains `DisabledAdapter` as default. |
| `w288-compare-exp` | Integrates multi-export targets (DOCX, RTF, Clipboard, KIS) and executes comparative verification against `BT-EXP-001`. Validates that KIS adapter failure cleanly isolates from secondary file targets. |

---

## 8. Proposed Row Updates for Reconciliation Owner

The following updates are proposed for the serialized reconciliation package. **No acceptance test is marked passed.**

| Identifier | Catalog | Proposed Status | Justification / Evidence Note |
|---|---|---|---|
| **R-026** | `clinic-spec/planning/requirements.json` | `specified_not_validated` | Edge WebView2 host requirements compared and recorded in `clinic-evidence/kis/vendor-contract.md`. Live vendor runtime not executed. |
| **R-027** | `clinic-spec/planning/requirements.json` | `specified_not_validated` | Context contract, grant binding, and log protection verified via synthetic tests (`test_wp_trusted_context.py`). |
| **R-028** | `clinic-spec/planning/requirements.json` | `specified_not_validated` | Delivery ledger and frozen payload contract recorded. Live KIS output endpoint not available. |
| **R-029** | `clinic-spec/planning/requirements.json` | `specified_not_validated` | Timeout handling, `UNKNOWN` state, and reconciliation logic verified with `MockKisAdapter` (`test_mock_kis.py`). |
| **R-030** | `clinic-spec/planning/requirements.json` | `specified_not_validated` | Amendment and context supersession semantics recorded. Browser observation fallback strictly barred. |
| **PAR-EXP-001** | `clinic-spec/planning/parity-matrix.json` | `partial` | KIS delivery channel boundaries and failure isolation defined. End-to-end multi-export comparative run pending `w288-compare-exp`. |
| **T-026** | `clinic-spec/tests/acceptance.json` | `not_run` | Vendor WebView2 acceptance requires live clinical workstation environment. |
| **T-027** | `clinic-spec/tests/acceptance.json` | `not_run` | Real KIS context exchange requires active hospital directory and token service. |
| **T-028** | `clinic-spec/tests/acceptance.json` | `not_run` | Real document delivery requires certified vendor endpoint. |
| **T-029** | `clinic-spec/tests/acceptance.json` | `not_run` | Synthetic timeout verified; live network timeout reconciliation requires vendor environment. |
| **T-030** | `clinic-spec/tests/acceptance.json` | `not_run` | Context change supersession verified locally; live inesKIS window transfer test not run. |
| **BT-EXP-001** | `clinic-spec/tests/acceptance.json` | `not_run` | Oracle unchanged. Integration test across multiple adapters scheduled for consumer `w288-compare-exp`. |
| **G-KIS** | `clinic-spec/planning/blockers.json` | `open` | Production gate remains open. Real KIS manufacturer contract is unresolved. |
