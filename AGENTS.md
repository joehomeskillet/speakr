# AGENTS.md

## Purpose

Web-only clinic dictation fork of Speakr v0.11.1, pinned to upstream commit `48d3018fb7d80b77f897bedd86bcc89b5034d1f5`.

- Published release branch: `main`. Development and integration branch: `dev`.
- Remotes: `origin` is upstream Speakr; `fork` is the clinic fork.
- Work packages branch locally from `dev`. The orchestrator promotes tested changes from `dev` to `main` by pull request.

## Source of truth

`clinic-spec/CLAUDE.md` is the binding work order. It takes precedence over this file and over every other spec or planning document; if they disagree, follow `clinic-spec/CLAUDE.md`.

## Hard boundaries

- No native dictation client. No Windows, Electron, Tauri or WPF dictation app, no browser extension, no native messaging, no localhost tunnel.
- `apps/windows-import` is the only local agent. It only imports dictation files from approved devices and folders, plus the narrow post-processing the spec allows. It does not record, play, edit text or automate other applications.
- New domain code goes under `src/clinic/`. Clinic tests go under `tests/clinic/`.
- No framework rewrite. Keep the existing Flask, SQLAlchemy, Vue and Tailwind stack.
- No new general-purpose queue. Reuse `ProcessingJob` and `JobQueue`.
- No cloud ASR. Speech recognition runs locally only.
- Never weaken original tests. Do not edit, relax or delete existing assertions to make a test pass.
- Open web gaps listed in `clinic-spec/planning/web-parity-gaps.json` stay open. Do not mark them closed and do not drop their scope silently.
- A test is marked passed only with real execution evidence (verbatim command output). Mocks are not evidence for Philips devices, Entra, KIS or foot pedals.
- No production deploy. No push or merge without explicit authorization. No data deletion.

## Persona panel

Klinik Südhang is an addiction clinic. Most users have limited technical experience. Run the panel once per new or changed author, typist or team lead flow before the owner makes UX decisions. One run covers the flow change across its commits. Budget about 120,000 tokens per run on the subscription.
The outpatient centres are far from the main clinic and have no on-site IT. Their dictations are typed centrally in Bern.

```
python3 scripts/persona_panel/persona_panel.py walkthrough "Flow name" /path/to/staging.png
python3 scripts/persona_panel/persona_panel.py brainstorm "Question about the flow"
```

Use synthetic personas and staging screenshots with synthetic data only. Never include patient data or private fixtures. Findings are suggestions for the owner. Fix blocker findings or explain them in the work-package report.

## Gate

Run from the worktree root. Each line is a separate command, run in this order. Record the output verbatim.

```
uv pip install --python venv/bin/python --require-hashes --no-deps -r requirements.lock -r requirements-dev.lock
scripts/build_css.sh
venv/bin/python -m pytest tests/ -q -n 4 --dist loadfile -p no:cacheprovider --timeout=120
venv/bin/python -m pytest tests/clinic -q -p no:cacheprovider
venv/bin/python -I clinic-spec/scripts/Verify-SuedhangPackage.py --root clinic-spec
npm test
```

## Worker contract

- Each work package gets its own git worktree under `.claude/worktrees/`, branched from `dev`.
- Commit only on that work package's local branch. The orchestrator owns pushes and merges to `dev` and `main`.
- Report the commit hash and the verbatim gate output.
- The last line of the report is `WP-REPORT: DONE — <summary>` or `WP-REPORT: BLOCKED — <reason>`.
- Shell calls: every call is `rtk <cmd>`, one command per call. No `rtk proxy`, no `&&`, `;` or `||` chains, no pipes.

## Known baseline issue

`tests/test_audit_log_coverage.py::test_no_marker_reaches_a_row_or_a_fallback_line` fails order-dependently in the parallel gate run (`-n 4 --dist loadfile`) on the pinned commit, and passes when run alone. The root cause is not established. Do not weaken or alter the test to hide it. When it fails, report the verbatim output.
