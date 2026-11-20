# V8 browser acceptance harness (UI8-008)

pytest-playwright harness for the V8 product browser gates (V8-GATE-01, -18, -20, -21 in
`ui-v8/tests/TEST_MATRIX.md`). It runs against the real Flask app (a child process with a throwaway
SQLite database and synthetic users, no patient data), not against the static V8 prototype.

## What it does

- Seeds four synthetic roles: `author`, `typist` (pool grant play/edit/release, assignee of the one
  synthetic dictation), `lead` (administrator of the typists' staff pool group, not a site admin) and
  `admin`. Each role logs in once through the real login form; the storage state is reused.
- P20 uses a fresh anonymous context and the regular `/login` form, without submitting it.
- The matrix adds one completed synthetic transcription job to its isolated fixture. Background
  workers are disabled. Other browser scenarios keep the original seed. No ASR service is called.
- Visits the page registry `PAGES` (P01..P21) in `v8_browser_support.py`.
- Runs every mounted page for each allowed role at 1440x900, 1280x720 and 390x844, in light and dark.
  Dark mode is set through `localStorage.darkMode` and the test asserts that `html.dark` matches.
- All 21 pages are active: 180 browser cases, with no "not mounted" xfails. A registry assertion
  fails if any P01–P21 entry loses its navigation function.
- P03/P07/P16/P17/P18 open the synthetic recording row and then its actual detail/review/output/
  processing/history tab. P06 keeps its worklist-row route. Content assertions require the seeded
  author/pool, transcript, revision/history, scoped job or unavailable export targets, not just a shell.
- The previous P12 Abnahme check and P13 resources-menu check remain; each then opens its actual
  V8 accounts or vocabulary page. Existing roles remain unchanged.
- Runs axe-core (WCAG 2.0/2.1/2.2 A and AA tags) on every mounted case when a local axe-core build is
  found: `$AXE_CORE_JS`, `node_modules/axe-core/axe.min.js`, or the global copies under
  `/usr/lib/node_modules`. Nothing is downloaded. Without one, `test_axe_core_available` is skipped
  with the reason and the page cases record "not run: no local axe-core".
- Writes screenshots and one result JSON per case to `tests/clinic/browser/out/` (gitignored; override
  with `V8_BROWSER_OUT`). File names start with the build commit: `<commit>[-dirty]__<page>-<view>-<role>-<viewport>-<theme>.png`.
  Hard failures also write `__failure.png` and `__failure.json`. Result JSON includes the reached URL
  and, for dictation pages, the row's dictation ID.

## Page and account coverage

| Page | Account(s) | Required content |
| --- | --- | --- |
| P01 Aufnahmen | author, typist, lead, admin | Loaded recording count |
| P02 Arbeitsliste | author, typist, lead, admin | Workflow status totals |
| P03 Diktatdetails | author | Seeded author, pool and status axes |
| P04 Neue Aufnahme | author | Author identity and recorder controls, without recording |
| P05 Live-Diktat | author | Live controls and ready caption, without starting a session |
| P06 Schreibplatz | typist | Worklist row and synthetic transcript |
| P07 Prüfen und Freigeben | typist | Matching dictation ID, transcript and text revision |
| P08 Archiv | author | Filters and successfully loaded empty archive |
| P09 Dateien und Geräteimport | admin | Empty import requests and device inventory |
| P10 Schreibkräfte-Pools | admin | Pool administration heading |
| P11 Diktierende und Autorenpools | admin | Synthetic author and author pool |
| P12 Benutzer und Rollen | admin | Abnahme check, then accounts table with synthetic author |
| P13 Wortschatz | author, typist, lead, admin | Resources menu, then vocabulary panel and empty state |
| P14 Sprachbefehle und Vorlagen | author | Command overview and successfully loaded empty catalog |
| P15 ASR und Sprachprofile | admin | Capabilities and training panel, without starting training |
| P16 Ausgabe und Integrationen | author | Not released, export targets disabled |
| P17 Verarbeitung | author | Exactly one completed job of the opened dictation |
| P18 Aktivität und Audit | author | Dictation history with text revision |
| P19 Einstellungen | author | Settings origins and pedal panel |
| P20 Anmeldung und Zugriff | anonymous | Regular login form and identity/access information |
| P21 Offline und Wiederherstellung | author | Connection and unavailable local drafts |

These are read-only visits: no release, export, import, training, recording or settings action is
submitted. Normal app reads can write audit events or manage the editor lease. The fixture has no
released report, archived dictation, device inventory or managed-offline permission; those empty or
unavailable states are intentional, not evidence for their operational workflows.

## Hard failures and findings

Hard failures: the view does not show, an uncaught JavaScript exception, the requested theme is not applied.

Findings are recorded in the result JSON and printed at the end of the run, but they do not fail the run
by default: axe violations, horizontal page overflow, console errors, HTTP errors, and clicks that another
element intercepts (the harness then dispatches the click and notes it). `V8_STRICT=1` turns all of them
into failures. Findings are not waived; a green default run is not an accessibility or layout approval.

## Run it

Prerequisites in the worktree: `venv`, `static/vendor` (gitignored, copy or link it) and a built
`static/css/tailwind.css` (`scripts/build_css.sh`). Without them the app fixture is skipped with a reason.

```
rtk env PLAYWRIGHT_CHROMIUM_EXECUTABLE=<chromium> V8_BROWSER_OUT=<artifact-directory> venv/bin/python -m pytest tests/clinic/browser/test_v8_browser_pages.py -q -n 4 --dist load -p no:cacheprovider -rs
```

Select by page or role with `-k "P13"`, `-k "typist and dark"`. The registry test needs no browser:
`rtk venv/bin/python -m pytest tests/clinic/browser/test_v8_registry.py`.

For one initial case per page, omit `-n 4 --dist load` and select
`-k '1440x900 and light and (author or P06 or P07 or P09 or P10 or P11 or P12 or P15 or P20)'`.
The full matrix contains 180 browser cases plus the active-page and local axe-core checks.
Parallel workers each start their own throwaway app/database. JSON results remain per case.

## Maintain the page registry

1. In `v8_page_navigation.py` write a `show_<view>(page, base)` function. It navigates as a user does
   (navigation entry, tab, row click) and asserts meaningful loaded content in that view. Use
   `_click_noting` for clicks so an intercepted click becomes a finding, not a timeout.
2. In `PAGES` register the page, for example
   `PageEntry("P03", "Diktatdetails", "detail", ("typist", "author"), _show_detail)`: view name, the roles that
   reach it, and the show function. Never replace a failed navigation proof with skip or xfail.
3. If the matrix needs data the shared seed lacks, extend `MATRIX_SEED` (synthetic data only).
4. Run `-k "<page id>"`, look at the screenshots and the findings, and keep `test_v8_registry.py` green.

For the mounted entries, "view" names the clinic view that carries the page's function at `clinic/main`
(P02 worklist, P06 correction, P10 and P12 admin panels; P13 keeps its historical `resources` case ID). That states the mount, not
conformity to the V8 page specification.

## Not covered

No visual baselines or approvals, no native on-screen keyboard, no real audio, no Firefox or WebKit
(Chromium only), no manual screen reader pass, no Philips device, Entra, KIS or pedal evidence.
