# w064 — PostgreSQL application-role audit proof

**T-031 database assertion failed.** The configured application login could update
and delete an existing `audit_event` row. Both probes were rolled back.

## Scope and runtime

Executed on 2026-11-18 against isolated, disposable PostgreSQL 16.15
(`postgres:16.15-alpine`, temporary in-memory storage, loopback only), using only
synthetic data. The app's normal database initialization created the schema in
`w064_audit_clinic` with `CLINIC_MODE=1`. The configured login, PostgreSQL
`session_user`, and `current_user` were all `w064_app`; no `SET ROLE` was used.
The role had `LOGIN`, `NOSUPERUSER`, `NOCREATEDB`, `NOCREATEROLE`,
`NOREPLICATION`, and `NOBYPASSRLS`.

| Object | Observed baseline |
| --- | --- |
| `public.audit_event` | Owned by `w064_app`; default ACL (`relacl` is `NULL`), no column grants; effective `SELECT`, `INSERT`, `UPDATE`, `DELETE`, `TRUNCATE`, `REFERENCES`, `TRIGGER`. RLS disabled; no user triggers. |
| `public.audit_event_id_seq` | Default ACL; effective `USAGE`, `SELECT`, `UPDATE`. |

`src/models/audit.py` rejects ORM updates and deletes. The trigger installer in
`src/clinic/append_only_guards.py` covers other tables, including
`clinic_voice_consent_audit`, but not `audit_event`. `src/services/audit.py`
appends through `record_in_tx` or its queued writer and currently prunes expired
ordinary audit rows through the application session; archive/unarchive rows are
excluded from that pruning. The proof uses `record_in_tx` and targets the real
schema directly for the mutation probes.

## Executed cases

| Case | Observation |
| --- | --- |
| Append, rollback, retry, read | The first row was absent after rollback; the retried append committed and read back the expected event, group and outcome. Passed. |
| Raw `UPDATE` of a committed row | Accepted, `rowcount=1`, no SQLSTATE; denial assertion failed. |
| Raw `DELETE` of a committed row | Accepted, `rowcount=1`, no SQLSTATE; denial assertion failed. |

Both mutation cases verified the target before the probe and verified the row
after savepoint rollback. Stale-version handling does not apply: this table
has no versioned edit operation. Three cases executed: `2 failed, 1 passed in
1.62s`. A separate PostgreSQL run of `test_wiring_a.py` and `test_archive.py`
reported `38 passed, 2 errors in 10.47s`; both errors were existing archive
fixture teardown paths hitting `tag_user_id_fkey`. Neither run is a full gate.

To reproduce, set `PYTEST_DATABASE_URI` to a fresh disposable PostgreSQL
database using the configured app login, then run:

```sh
rtk env CLINIC_MODE=1 CLINIC_AUDIT_DB_PROOF=1 venv/bin/python -m pytest tests/clinic/test_audit_database_role.py -q -p no:cacheprovider
```

## Boundary and reconciliation

**w065-audit-db-guard:** separate audit ownership and maintenance from the
runtime login, prevent runtime mutation (including `TRUNCATE`), and verify
that append/read still work. Reconcile retention pruning with that boundary.
Keep both denial assertions failing until the guard is verified. The baseline
app login was itself the table owner; even a trigger would leave its owner with
DDL authority. The intended guard concerns the runtime login. Owners and DB
superusers retain administrative authority, and backup/restore operators can
replace the stored history. Those are operational privileges to restrict and
monitor separately; no privileged mutation or recovery drill was executed here.

Proposed reconciliation: attach this artifact to R-031/T-031 as a failed
database subcase. Full T-031 event coverage remains unexecuted: exercising it
requires approved synthetic fixtures and end-to-end access, assignment,
release, export and admin actions. Keep `BT-WF-006` and `BT-OPS-008` at
`not_run`: this is target-side audit integrity evidence for `PAR-WF-006` and
`PAR-OPS-008`, with no execution of the original dictation-history UI or job
failure scenarios and no reference comparison.
