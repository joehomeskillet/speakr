# Local-first defaults review (w270)

Reviewed 2026-11-18 on `clinic/wp-w270-local-defaults-review`. Catalog slice of T-098 only. No acceptance row was changed. No original BT oracle was rewritten.

## Active sources

| Source | What it says |
|---|---|
| `clinic-spec/config/dev.defaults.json` | `schema_version` 4, profile `local-web-parity-lab`. `identity.provider` `local`, `mode` `local`, `local_enabled` true, `entra_enabled` false, `keycloak_required` false, `smtp_required` false, `email_auto_link` false, `self_registration` false. Pointer `identity.configuration` = `config/auth.local.example.json`. Loaded by `src/clinic/config.py` `dev_defaults()`. |
| `clinic-spec/config/auth.local.example.json` | `schema_version` 1, `mode` `local`, `entra_enabled` false, `ldap_enabled` false, `keycloak_required` false, `smtp_required` false, `default_account_policy` `local_only`, `allow_implicit_password_fallback` false, `activation.real_provider_approved` false. `EntraConfig.from_dict` returns `enabled` false. |
| `clinic-spec/config/auth.hybrid.example.json` | Prepared later profile. Raw `entra_enabled` true, `mode` `hybrid`, `activation.real_provider_approved` false, tenant fields null. Effective adapter from `EntraConfig.from_dict` is disabled. |
| `clinic-spec/config/production.template.json` | `schema_version` 4, `enabled` false. Identity provider `local`, `entra_enabled` false, `local_enabled` true. Not loaded by `src/clinic/config.py`. |
| `clinic-spec/config/decisions.json` D09 | Local user management is the initial runtime default. Entra is implemented and disabled. No mandatory external directory, LDAP or SMTP for local operation. |
| `clinic-spec/tests/fixtures/demo.json` | `auth_profiles.default` `local`, `entra_enabled` false. Every user `login_policy` is `local_only`. Synthetic only. |
| `src/auth/sso.py` `get_sso_config` | `ENABLE_SSO` defaults to false. With the SSO env vars unset, `is_sso_enabled()` is false. Startup does not require a provider. |
| `src/clinic/startup.py` `run_startup_sweep` | Clinic startup sweeps stored originals. It does not read the auth profile and does not call `EntraConfig.from_dict`. |

`src/clinic/config.py` exposes `dev_defaults`, `client_capabilities` and `import_agent_boundary`. It does not load `identity.configuration`. The local-first auth file is therefore a catalog the test reads directly. The process default that actually gates stock SSO is the unset `ENABLE_SSO` flag.

## Identifiers present

- Requirements `R-009`, `R-046`, `R-062`, `V4-001`, `V4-002`, `V4-003`, `PAR-ADM-001`, `PAR-ADM-010` are in `clinic-spec/planning/requirements.json`. `R-062` lists `T-071` and `T-098`. `PAR-ADM-001` lists `BT-ADM-001`. `PAR-ADM-010` lists `BT-ADM-010`.
- 101 `PAR-*` ids match `planning/parity-matrix.json` and `sources/dns-functional/specs/feature_parity.json`.
- 101 `BT-*` ids match `tests/dns-behavior-tests.original.json` and `tests/acceptance.json`. For every BT id, `given`, `when` and `then` are equal, `source_when_then_unchanged` is true, and `original_oracle_not_replaced` is true.
- Grok additions `GRK-C01`–`GRK-C09` are in `supplements/grok-review/planning/grok-delta.json`. `GRK-T-01`–`GRK-T-09` are in `supplements/grok-review/tests/review-additional-tests.json` and in `tests/acceptance.json`. Supplemental rows are `not_run`.
- Retained invariants `INV-01`–`INV-16` are in `contracts/v2-invariants-retained.md`.
- `planning/web-parity-gaps.json` still has 13 open gaps and an empty `scope_waivers` list. `PAR-ADM-001` and `PAR-ADM-010` are not among those gaps, so their BT oracles are not waived.

## Discrepancies (active source only)

1. **Raw hybrid switch versus effective adapter.** `auth.hybrid.example.json` sets `entra_enabled` true. `EntraConfig.from_dict` (`src/clinic/entra.py`) stays disabled because `mode == "hybrid"`, `entra_enabled` and `activation.real_provider_approved` must all hold. A reader of the raw JSON can mistake the prepared file for an enabled provider. The active startup profile is `auth.local.example.json`, where the raw switch is already false.
2. **Auth profile is not applied at startup.** `dev.defaults.json` points at `config/auth.local.example.json`, but `src/clinic/config.py` never opens that file and `run_startup_sweep` does not either. Local-first at process start is the stock SSO default (`ENABLE_SSO` false), not an enforcement of the clinic auth profile.
3. **Flag coverage differs across the three profile files.** `auth.local.example.json` has `ldap_enabled`, `keycloak_required` and `smtp_required`, all false. `dev.defaults.json` `identity` has `keycloak_required` and `smtp_required` but no `ldap_enabled`. `production.template.json` `identity` has none of those three keys. None of the present values requires a provider. The omission is a catalog gap, not a second active mode.
4. **Schema versions differ by contract.** Auth examples are `schema_version` 1. Dev defaults and the production template are `schema_version` 4. The checked-in dev file is the current envelope, not a stale copy. `dev_defaults()` accepts any JSON object and does not reject a substituted file that would set a provider requirement. That loader behaviour was not changed; this package cannot edit `src/clinic/config.py`.
5. **Stock SSO auto-register default is not the clinic profile.** `get_sso_config` defaults `SSO_AUTO_REGISTER` to true when that variable is unset (`src/auth/sso.py`). `config/env.sso.example` sets `ENABLE_SSO=false` and `SSO_AUTO_REGISTER=true`. Clinic profiles set `self_registration` and `jit_provisioning` false. The auto-register default is unused while SSO is disabled. It would diverge from the clinic profile if SSO were turned on without an explicit false. Recorded only. Upstream SSO code was not edited.

## Historical material (not active defaults)

- `clinic-spec/supplements/grok-review/README.md` keeps earlier reuse notes, including an external directory profile. Its banner says those historical task wordings do not outrank `clinic-spec/CLAUDE.md` or the V4 catalogs.
- `clinic-spec/docs/10_Local_Entra.md` section 1 says earlier OIDC-only and provider-mandatory sentences are replaced by the local-first decision. The Grok original review is historical input.
- `config/env.sso.example` is the stock upstream example. It is not a clinic profile and does not enable SSO.

## Cases executed

| Case | Result |
|---|---|
| Positive local profiles, fixture, decision D09, SSO env unset | Asserted in `tests/clinic/test_local_first_catalog_consistency.py`. |
| Unauthorized / unapproved Entra, including a stale provider-required document | `EntraConfig.from_dict` disabled. `begin_authorization` raises `entra_disabled` on the first call and on the retry. No authorization URL. |
| Approved but incomplete tenant | `EntraConfigError` `tenant_id_invalid`. Adapter is not enabled. |
| Missing `CLINIC_CONFIG_DIR` | `dev_defaults()` raises `FileNotFoundError`. |
| Job retry schedule | `jobs.max_attempts` 3 and `retry_seconds` `[5, 30, 120]` with `smtp_required` false and both ASR cloud fallbacks false. |

## Not executed (do not mark passed)

- **T-098** remains `not_run` with empty `evidence`. The catalog half of its expected result was checked. The local end-to-end user-admin flow and any real provider acceptance were not run. `kind` is `automatable_except_explicit_real_provider_acceptance`.
- **BT-ADM-001** remains `not_run`. Oracle unchanged (empty lab config, two authors, two writers, cancel/confirm, restart, remove one user). The demo fixture has those roles as `local_only` aliases. The UI lab was not run.
- **BT-ADM-010** remains `not_run`. Oracle unchanged (two bound Windows accounts, SSO for the workflow only, unknown account must not become a default author). Prerequisite absent: no Windows test accounts and no fixture `F-AD` session. This is not one of the 13 web gaps, so the oracle is not waived and was not replaced.
- Identity `VersionConflict` on a stale account version was not re-run. `tests/clinic/test_identity_lifecycle.py` already calls that entry point. This focused command did not run that file.
- No PostgreSQL database was used. No Entra, directory, KIS, Philips or foot-pedal call was made.

## Proposed row updates for the reconciliation owner

Do not set these to passed.

| Id | Proposed status | Note |
|---|---|---|
| T-098 | `not_run` | Catalog slice evidenced here. Full local E2E and real provider acceptance still open. |
| BT-ADM-001 | `not_run` | Oracle unchanged. UI lab not run. |
| BT-ADM-010 | `not_run` | Oracle unchanged. Windows SSO prerequisite absent. |
| R-009, R-046, R-062, V4-001, V4-002, V4-003, PAR-ADM-001, PAR-ADM-010 | `specified_not_validated` | Unchanged. |
| GRK-T-01 … GRK-T-09 | `not_run` | Present in both catalogs. Not executed. |
