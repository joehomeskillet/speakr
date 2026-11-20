# V8 browser acceptance harness (UI8-008)

pytest-playwright harness for the V8 product browser gates (V8-GATE-01, -18, -20, -21 in
`ui-v8/tests/TEST_MATRIX.md`). It runs against the real Flask app (a child process with a throwaway
SQLite database and synthetic users, no patient data), not against the static V8 prototype.

## What it does

- Seeds four synthetic roles: `author`, `typist` (pool grant play/edit/release, assignee of the one
  synthetic dictation), `lead` (administrator of the typists' staff pool group, not a site admin) and
  `admin`. Each role logs in once through the real login form; the storage state is reused.
- Visits the page registry `PAGES` (P01..P21) in `v8_browser_support.py`.
- Runs every mounted page for each allowed role at 1440x900, 1280x720 and 390x844, in light and dark.
  Dark mode is set through `localStorage.darkMode` and the test asserts that `html.dark` matches.
- Pages that are not mounted are reported as xfail with the reason "not mounted" (they are not run).
- Runs axe-core (WCAG 2.0/2.1/2.2 A and AA tags) on every mounted case when a local axe-core build is
  found: `$AXE_CORE_JS`, `node_modules/axe-core/axe.min.js`, or the global copies under
  `/usr/lib/node_modules`. Nothing is downloaded. Without one, `test_axe_core_available` is skipped
  with the reason and the page cases record "not run: no local axe-core".
- Writes screenshots and one result JSON per case to `tests/clinic/browser/out/` (gitignored; override
  with `V8_BROWSER_OUT`). File names start with the build commit: `<commit>[-dirty]__<page>-<view>-<role>-<viewport>-<theme>.png`.

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
PLAYWRIGHT_CHROMIUM_EXECUTABLE=<chromium> rtk venv/bin/python -m pytest tests/clinic/browser -q -p no:cacheprovider -rs
```

Select by page or role with `-k "P13"`, `-k "typist and dark"`. The registry test needs no browser:
`pytest tests/clinic/browser/test_v8_registry.py`.

## Add a page when it gets mounted

1. In `v8_browser_support.py` write a `_show_<view>(page, base)` function. It navigates as a user does
   (navigation entry, tab, row click) and ends with a wait for the view's own test id. Use
   `_click_noting` for clicks so an intercepted click becomes a finding, not a timeout.
2. In `PAGES` replace the bare entry, for example
   `PageEntry("P03", "Diktatdetails", "detail", ("typist", "author"), _show_detail)`: view name, the roles that
   reach it, and the show function. The entry then counts as mounted and is no longer xfail.
3. If the view needs data the seed lacks, extend the `SERVER` seed (synthetic data only).
4. Run `-k "<page id>"`, look at the screenshots and the findings, and keep `test_v8_registry.py` green.

For the mounted entries, "view" names the clinic view that carries the page's function at `clinic/main`
(P02 worklist, P06 correction, P10 and P12 admin panels, P13 resources). That states the mount, not
conformity to the V8 page specification.

## Not covered

No visual baselines or approvals, no native on-screen keyboard, no real audio, no Firefox or WebKit
(Chromium only), no manual screen reader pass, no Philips device, Entra, KIS or pedal evidence.
