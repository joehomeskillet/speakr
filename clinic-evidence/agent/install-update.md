# Agent install, update and rollback evidence (w073, T-008)

Status: not_run. No Windows lab host, no PowerShell and no signing certificate were available to this work package. No result below is a pass.

Scope: PAR-IMP-001, PAR-OPS-008, R-008, R-032. Acceptance case T-008 (Agentensicherheit). Original behaviour tests BT-IMP-001 and BT-OPS-008 are unchanged.

## Procedure

Run `tests/clinic/windows/InstallUpdateChecks.ps1` on a disposable Windows lab host as a standard user with `-ConfirmDisposableLab`. Supply the lab root, the v1, v2 and deliberately broken v2 packages and the install, update, rollback, rotate and revoke scripts of the approved distribution path. Use synthetic data only. Paste the JSON output into the table below and keep the raw output as the artifact.

## Results

| Check | Status | Blocked prerequisite or observation |
| --- | --- | --- |
| standard-user-service | not_run | Windows lab host, installed agent |
| spool-acl | not_run | Windows lab host, spool directory |
| version-signature | not_run | Windows lab host, signing certificate (Authenticode) |
| update-ok | not_run | Windows lab host, approved update script |
| update-failed-rollback | not_run | Windows lab host, broken v2 package, rollback script |
| spool-preserved (queued synthetic import across failed update) | not_run | depends on update-failed-rollback |
| stale-version-rejected | not_run | Windows lab host, v1 and v2 packages |
| unauthorized-install-denied | not_run | Windows lab host, standard-user account |
| credential-rotation | not_run | Windows lab host, rotation script, server-side machine identity |
| credential-revocation | not_run | Windows lab host, revocation script, server-side machine identity |

## Evidence limits

- Earlier 51/51 simulator results are historical evidence only and do not satisfy T-008.
- `apps/windows-import/packaging/Build-Package.ps1` produces an unsigned development package; Authenticode needs a Windows signing host.
- Gates G-DEVICE and G-OPS stay open. Mock results prove nothing for Philips devices.

## Proposed parity updates

PAR-IMP-001 and PAR-OPS-008: keep status open, T-008 not_run, add this file as a pending evidence reference. No row is marked passed.
