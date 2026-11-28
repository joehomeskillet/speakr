"""Verwaltung inside the clinic shell (v8-114; V8 P10 to P18, DESIGN_SYSTEM section 1).

The administration pages share /admin#/<page>. The original shell URLs forward there, and the header menu
"Verwaltung & Seiten" opens them directly, with the breadcrumb Speakr > Verwaltung > <page>. Visibility follows the probe rule of
templates/admin.html: a GET the API authorises; a page is withheld when the API answers 403.

Reuses the harness of test_wp_mount_admin (real Flask app in a child process, throwaway SQLite database, synthetic users
only, headless Chromium): the site admin (w226admin) and a team lead without site admin rights (w226lead).
The browser cases need Playwright, its Chromium, the gitignored static/vendor and a built static/css/tailwind.css.
"""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import expect

from test_wp_mount_admin import (  # noqa: F401  (fixtures and helpers of the earlier mount package)
    ADMIN_EMAIL, LEAD_EMAIL, _login, browser, live_server,
)

# The imported fixtures are used by name as test arguments.
_FIXTURES = (browser, live_server)

ROOT = Path(__file__).resolve().parents[2]

# view id -> (hash route, label in menu and breadcrumb, heading the page component renders)
PAGES = {
    "clinic-admin-pools": ("#/verwaltung/schreibkraefte-pools", "Schreibkräfte-Pools", "Schreibkräfte-Pools"),
    "clinic-admin-authors": ("#/verwaltung/diktierende", "Diktierende und Autorenpools", "Diktierende und Autorenpools"),
    "clinic-admin-users": ("#/verwaltung/benutzer", "Benutzer und Rollen", "Benutzer und Rollen"),
    "clinic-admin-vocabulary": ("#/verwaltung/wortschatz", "Wortschatz", "Wortschatz"),
    "clinic-admin-commands": ("#/verwaltung/befehle-vorlagen", "Befehle und Vorlagen", "Befehle und Vorlagen"),
    "clinic-admin-asr": ("#/verwaltung/sprachprofile", "Sprachprofile", "Sprachprofile"),
    "clinic-admin-import": ("#/verwaltung/import", "Import", "Dateien und Geräteimport"),
    "clinic-admin-jobs": ("#/verwaltung/verarbeitung", "Verarbeitung", "Verarbeitung"),
    "clinic-admin-activity": ("#/verwaltung/aktivitaet", "Aktivität und Audit", "Aktivität und Audit"),
}
# Pages with a menu entry in components/header-controls.html (Dateien und Geräteimport keeps its own entry /#/import).
MENU_PAGES = [view for view in PAGES if view != "clinic-admin-import"]


def _grant_statistics(page):
    # The statistics route needs an explicit grant (statistics_access); the admin of the harness has none, so the API refuses
    # the probe of "Aktivität und Audit". Reachability of that page is checked with the route answered.
    page.route("**/api/clinic/v1/statistics/aggregates*", lambda route: route.fulfill(status=200, content_type="application/json", body="{}"))


def _canonical(base, legacy_hash):
    return base + "/admin" + legacy_hash.replace("#/verwaltung/", "#/")


def _mounted(page):
    page.locator(".app-loading-overlay").first.wait_for(state="detached", timeout=30000)


def _shell(page, view):
    return page.locator(f"[data-testid=clinic-admin-view][data-admin-view={view}]")


def _wait_page(page, view):
    _hash, _label, heading = PAGES[view]
    shell = _shell(page, view)
    shell.wait_for(state="visible", timeout=30000)
    shell.locator("h1, h2").filter(has_text=heading).first.wait_for(state="visible", timeout=30000)
    assert page.locator("[data-testid=clinic-admin-denied]").count() == 0
    return shell


def _open_from_menu(page, view):
    page.locator("[data-testid=clinic-pages-menu] summary").click()
    page.locator(f"a[data-clinic-admin-link={view}]").click()


def test_every_page_is_reachable_by_hash_with_breadcrumb_as_admin(live_server, browser):
    base, _ids = live_server
    page, js_errors = _login(browser, base, ADMIN_EMAIL)
    _grant_statistics(page)
    for view, (route, label, _heading) in PAGES.items():
        page.goto(base + "/" + route)
        _mounted(page)
        shell = _wait_page(page, view)
        crumb = shell.locator("[data-testid=clinic-admin-breadcrumb]")
        expect(crumb.get_by_role("link", name="Speakr")).to_have_attribute("href", "/#/aufnahmen")
        expect(crumb.locator('[aria-current="page"]')).to_have_text(label)
        assert [t.strip() for t in crumb.locator("a, span").all_inner_texts()] == ["Speakr", "›", "Verwaltung", "›", label]
        assert page.url == _canonical(base, route)
        # The primary tabs stay in the header; neither is the current page.
        assert page.locator("[data-testid=clinic-primary-nav] [aria-current=page]").count() == 0
    assert not js_errors, js_errors


def test_every_menu_entry_opens_its_page(live_server, browser):
    base, _ids = live_server
    page, js_errors = _login(browser, base, ADMIN_EMAIL)
    _grant_statistics(page)
    page.goto(base + "/#/aufnahmen")
    _mounted(page)
    for view in MENU_PAGES:
        _open_from_menu(page, view)
        _wait_page(page, view)
        assert page.url == _canonical(base, PAGES[view][0])
    # Browser back returns to the previous page, the breadcrumb link to Aufnahmen.
    page.go_back()
    _wait_page(page, MENU_PAGES[-2])
    _shell(page, MENU_PAGES[-2]).locator("[data-testid=clinic-admin-breadcrumb] a").click()
    page.locator("[data-testid=clinic-recordings-view]").wait_for(state="visible", timeout=30000)
    assert not js_errors, js_errors


def test_pools_authors_users_use_the_prototype_page_structure(live_server, browser):
    base, _ids = live_server
    page, js_errors = _login(browser, base, ADMIN_EMAIL)
    for view in ("clinic-admin-pools", "clinic-admin-authors", "clinic-admin-users"):
        page.goto(base + "/" + PAGES[view][0])
        _mounted(page)
        shell = _wait_page(page, view)
        assert shell.locator(".pagehead .eyebrow").text_content().strip() == "Verwaltung"
        assert shell.locator(".pagehead h1").count() == 1
        assert shell.locator(".pagehead .subline").count() == 1
        shell.locator(".stats .stat").first.wait_for(state="visible", timeout=30000)
        assert shell.locator(".stats .stat").count() == 4
    # Pools: table card with search and reset, status pill and an edit icon button per row.
    page.goto(base + "/" + PAGES["clinic-admin-pools"][0])
    _mounted(page)
    shell = _shell(page, "clinic-admin-pools")
    shell.locator(".filterbar input[type=search]").wait_for(state="visible", timeout=30000)
    # The harness has no staff pool: the page's own "Pool hinzufügen" action creates one.
    shell.locator("[data-testid=empty-state]").wait_for(state="visible", timeout=30000)
    shell.locator("[data-testid=create-open]").click()
    shell.locator("#cap-create-name").fill("Schreibpool Test")
    shell.locator("[data-testid=create-panel] button[type=submit]").click()
    shell.locator("[data-testid=pool-list] .tag").first.wait_for(state="visible", timeout=30000)
    assert shell.locator("[data-testid=pool-list] tbody tr").count() == 1
    assert shell.locator("[data-testid=pool-list] tbody tr strong").inner_text().strip() == "Schreibpool Test"
    shell.locator("[data-testid=pool-list] .icon-button").click()
    shell.locator("#cap-rename").wait_for(state="visible", timeout=30000)
    shell.locator("[data-testid=pool-filter]").fill("zzz-kein-treffer")
    shell.locator("[data-testid=filter-empty]").wait_for(state="visible", timeout=10000)
    shell.locator("[data-testid=pool-filter-reset]").click()
    assert shell.locator("[data-testid=filter-empty]").count() == 0
    # Authors: the filter bar filters the table rows.
    page.goto(base + "/" + PAGES["clinic-admin-authors"][0])
    _mounted(page)
    shell = _shell(page, "clinic-admin-authors")
    shell.locator("table tbody tr").first.wait_for(state="visible", timeout=30000)
    rows = shell.locator("table tbody tr").count()
    assert rows >= 2
    shell.locator("#authors-search").fill("w226typist")
    assert shell.locator("table tbody tr").count() == 1
    shell.locator(".filterbar .icon-button").click()
    assert shell.locator("table tbody tr").count() == rows
    assert not js_errors, js_errors


def test_activity_is_withheld_when_the_api_refuses_the_statistics_probe(live_server, browser):
    base, _ids = live_server
    page, js_errors = _login(browser, base, ADMIN_EMAIL)  # no statistics grant: the real API answers 403
    page.goto(base + "/#/aufnahmen")
    _mounted(page)
    page.locator("[data-testid=clinic-pages-menu] summary").click()
    page.wait_for_function("document.querySelector('a[data-clinic-admin-link=clinic-admin-activity]').hidden", timeout=30000)
    assert page.locator("a[data-clinic-admin-link=clinic-admin-pools]").is_visible()
    page.goto(_canonical(base, PAGES["clinic-admin-activity"][0]))
    _mounted(page)
    denied = page.get_by_test_id("clinic-admin-denied")
    expect(denied).to_have_attribute("role", "status")
    expect(denied).to_contain_text("Aktivität und Audit")
    assert page.url == _canonical(base, PAGES["clinic-admin-activity"][0])
    assert page.request.get(base + "/api/clinic/v1/statistics/aggregates").status == 403
    assert not page.get_by_test_id("clinic-activity-view").count()
    assert not js_errors, js_errors


def test_withheld_page_is_hidden_and_not_shown_on_403(live_server, browser):
    base, _ids = live_server
    page, js_errors = _login(browser, base, ADMIN_EMAIL)
    page.route("**/admin/users*", lambda route: route.fulfill(status=403, content_type="application/json", body="{}"))
    page.goto(base + "/#/aufnahmen")
    _mounted(page)
    page.locator("[data-testid=clinic-pages-menu] summary").click()
    page.locator("a[data-clinic-admin-link=clinic-admin-pools]").wait_for(state="visible", timeout=30000)
    page.wait_for_function("document.querySelector('a[data-clinic-admin-link=clinic-admin-users]').hidden", timeout=30000)
    assert page.locator("a[data-clinic-admin-link=clinic-admin-authors]").is_hidden()
    assert page.locator("a[data-clinic-admin-link=clinic-admin-pools]").is_visible()
    page.goto(_canonical(base, PAGES["clinic-admin-users"][0]))
    _mounted(page)
    denied = page.get_by_test_id("clinic-admin-denied")
    expect(denied).to_have_attribute("role", "status")
    expect(denied).to_contain_text("Benutzer und Rollen")
    assert page.url == _canonical(base, PAGES["clinic-admin-users"][0])
    assert page.locator("[data-testid=clinic-users-view]").count() == 0
    assert not js_errors, js_errors


def test_non_admin_gets_no_admin_page_and_no_menu_entry(live_server, browser):
    base, _ids = live_server
    page, js_errors = _login(browser, base, LEAD_EMAIL)
    page.goto(base + "/" + PAGES["clinic-admin-users"][0])
    _mounted(page)
    page.locator("[data-testid=clinic-admin-denied]").wait_for(state="visible", timeout=30000)
    assert page.locator("[data-testid=clinic-users-view]").count() == 0
    assert page.locator("[data-testid=clinic-admin-breadcrumb]").inner_text().replace("\n", " ").endswith("Benutzer und Rollen")
    page.locator("[data-testid=clinic-pages-menu] summary").click()
    assert page.locator("a[data-clinic-admin-link]").count() == 0
    assert not js_errors, js_errors


def test_admin_menu_entries_and_view_markup_exist_only_in_clinic_mode():
    index = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
    block = index[index.index("V8 Verwaltung views (v8-114): the administration pages"):index.index("end V8 Verwaltung views (v8-114)")]
    assert "clinic-admin-view" in block
    # The block sits inside the {% if clinic_mode %} section of the shell views.
    before = index[:index.index("V8 Verwaltung views (v8-114): the administration pages")]
    assert before.rfind("{% if clinic_mode %}") > before.rfind("{% endif %}") or "{% endif %}" not in before[before.rfind("{% if clinic_mode %}"):]
    header = (ROOT / "templates" / "components" / "header-controls.html").read_text(encoding="utf-8")
    assert header.index("data-clinic-admin-link") > header.index("{% if clinic_mode %}")
    assert header.index("data-clinic-admin-link") < header.index("{% else %}")


def test_owned_page_heads_and_panels_survive_reloads(live_server, browser):
    base, _ids = live_server
    page, errors = _login(browser, base, ADMIN_EMAIL)
    _grant_statistics(page)
    for view in ("clinic-admin-vocabulary", "clinic-admin-commands", "clinic-admin-asr", "clinic-admin-jobs", "clinic-admin-activity"):
        page.goto(base + "/" + PAGES[view][0])
        shell = _wait_page(page, view)
        expect(shell.locator(".pagehead")).to_have_count(1)
        expect(shell.locator(".pagehead h1")).to_have_count(1)
        assert shell.locator(".pagehead h1").bounding_box()["height"] > 20
        expect(shell.locator(".panel").first).to_be_visible()
        assert shell.locator(".panel").first.evaluate("el => parseFloat(getComputedStyle(el).paddingTop)") > 0
        if view == "clinic-admin-jobs":
            shell.get_by_test_id("reload").click()
            expect(shell.locator(".pagehead")).to_have_count(1)
            expect(shell.get_by_test_id("state-filter")).to_be_visible()
    assert not errors, errors


def test_asr_settings_persist_and_render_in_both_views(live_server, browser, tmp_path):
    import os

    base, _ids = live_server
    admin, errors = _login(browser, base, ADMIN_EMAIL)
    route = base + "/#/verwaltung/sprachprofile"
    proofs = Path(os.environ.get("P100_PROOFS", str(tmp_path)))
    proofs.mkdir(parents=True, exist_ok=True)

    def open_view(view):
        if view == "admin":
            admin.goto(route)
            _wait_page(admin, "clinic-admin-asr")
        else:
            admin.goto(base + "/#/ressourcen")
            _mounted(admin)
            admin.get_by_role("tab", name="ASR-Profile").click()
        return admin.get_by_role("form", name="Spracherkennung")

    def capture(view, state):
        panel = open_view(view)
        expect(panel.get_by_label("Speicherregel")).to_be_enabled()
        for theme in ("light", "dark"):
            admin.evaluate("dark => document.documentElement.classList.toggle('dark', dark)", theme == "dark")
            for width, height in ((1440, 900), (1280, 720), (390, 844)):
                admin.set_viewport_size({"width": width, "height": height})
                assert admin.evaluate("document.documentElement.scrollWidth <= innerWidth")
                for control in panel.locator("select, button").all():
                    if control.is_visible():
                        box = control.bounding_box()
                        assert box["x"] >= 0 and box["x"] + box["width"] <= width
                assert panel.get_by_role("row").count() == (3 if state == "rows" else 0)
                admin.screenshot(path=proofs / f"{view}-{state}-{theme}-{width}x{height}.png", animations="disabled", full_page=True)
        admin.set_viewport_size({"width": 1440, "height": 900})

    for view in ("admin", "resources"):
        capture(view, "empty")
    panel = open_view("admin")
    panel.get_by_role("radio", name="Aufnahmegerät").click()
    panel.get_by_label("Speicherregel").select_option("ask")
    panel.get_by_role("button", name="Übernehmen").click()
    expect(panel.get_by_text("Gespeichert.", exact=True)).to_be_visible()

    second, second_errors = _login(browser, base, ADMIN_EMAIL)
    second.goto(route)
    _wait_page(second, "clinic-admin-asr")
    expect(second.get_by_role("radio", name="Aufnahmegerät")).to_have_attribute("aria-checked", "true")
    expect(second.get_by_label("Speicherregel")).to_have_value("ask")
    expect(second.get_by_text("Getrennte Akustikprofile werden nicht unterstützt.", exact=False)).to_be_visible()

    author, author_errors = _login(browser, base, "w226other@local.test")
    denied = author.goto(_canonical(base, "#/verwaltung/sprachprofile"))
    assert denied.status == 403
    expect(author.get_by_label("Speicherregel")).to_have_count(0)

    def synthetic_capabilities(route):
        response = route.fetch()
        data = response.json()
        data["profile"]["name"] = "Klinik Deutsch"
        data["capabilities"] = {"configured": ["batch", "live"], "proven": []}
        route.fulfill(response=response, json=data)

    panel.get_by_label("Quellenzuordnung").select_option("separate")
    panel.get_by_label("Quelle für Hintergrund-Erkennung").select_option("headset")
    panel.get_by_role("button", name="Übernehmen").click()
    expect(panel.get_by_text("Gespeichert.", exact=True)).to_be_visible()
    second.reload()
    expect(second.get_by_label("Quellenzuordnung")).to_have_value("separate")
    expect(second.get_by_label("Quelle für Hintergrund-Erkennung")).to_have_value("headset")
    admin.route("**/api/clinic/v1/asr-profiles", synthetic_capabilities)
    for view in ("admin", "resources"):
        capture(view, "rows")
    assert not (errors + second_errors + author_errors)


def test_non_admin_gets_denied_on_every_page_and_no_admin_flag(live_server, browser):
    base, _ids = live_server
    page, js_errors = _login(browser, base, LEAD_EMAIL)
    page.goto(base + "/#/aufnahmen")
    assert page.evaluate("document.querySelector('#app').dataset.clinicAdmin") == "False"
    # A team lead has the delegated team page. No site-administration URL grants that role site access.
    for view, (route, _label, _heading) in PAGES.items():
        page.goto("about:blank")
        response = page.goto(_canonical(base, route))
        assert response.status == 403, view
        assert not page.get_by_test_id("clinic-admin-view").count(), view
        assert not page.locator("[data-testid=clinic-import-view], [data-testid=clinic-jobs-view], [data-testid=clinic-vocabulary-view]").count(), view
    assert not js_errors, js_errors




def _luminance(rgb):
    channels = [int(v) / 255 for v in rgb]
    channels = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _contrast(foreground, background):
    hi, lo = sorted((_luminance(foreground), _luminance(background)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _rgb(css):
    return [float(v) for v in css[css.index("(") + 1:css.index(")")].split(",")[:3]]


def test_real_dark_error_and_removal_confirmation_contrast(live_server, browser):
    base, _ids = live_server
    page, errors = _login(browser, base, ADMIN_EMAIL)
    page.goto(_canonical(base, PAGES["clinic-admin-authors"][0]))
    authors = _wait_page(page, "clinic-admin-authors")
    page.evaluate("document.documentElement.classList.add('dark')")
    authors.locator("#authors-pools").click()
    authors.get_by_role("button", name="Entfernen").first.click()
    confirm = authors.get_by_role("alertdialog").locator("p")
    def contrast(node):
        expect(node).to_be_visible()
        colors = node.evaluate("""el => {
            const fg = getComputedStyle(el).color;
            // Composite translucent surfaces in paint order before calculating relative luminance.
            const layers = [];
            for (let n = el; n; n = n.parentElement) layers.unshift(getComputedStyle(n).backgroundColor);
            const ctx = document.createElement('canvas').getContext('2d');
            ctx.fillStyle = 'white'; ctx.fillRect(0, 0, 1, 1);
            for (const color of layers) { ctx.fillStyle = color; ctx.fillRect(0, 0, 1, 1); }
            const rgb = Array.from(ctx.getImageData(0, 0, 1, 1).data).slice(0, 3);
            return [fg, 'rgb(' + rgb.join(',') + ')'];
        }""")
        assert _contrast(_rgb(colors[0]), _rgb(colors[1])) >= 4.5, colors
    contrast(confirm)
    page.route("**/pool-admin/pools*", lambda r: r.fulfill(status=500, json={}))
    page.goto(_canonical(base, PAGES["clinic-admin-pools"][0]))
    page.evaluate("document.documentElement.classList.add('dark')")
    contrast(_shell(page, "clinic-admin-pools").locator(".note.burgundy").first)
    assert not errors, errors


def test_canonical_pool_stylesheet_precedes_tailwind(live_server, browser):
    base, _ids = live_server
    page, errors = _login(browser, base, ADMIN_EMAIL)
    page.goto(base + "/admin")
    page.get_by_test_id("clinic-v8-panel-admin-pools").click()
    shell = _wait_page(page, "clinic-admin-pools")
    styles = page.locator('link[rel=stylesheet]').evaluate_all("(els) => els.map(e => new URL(e.href).pathname)")
    assert styles.count('/static/css/clinic-admin.css') == 1
    assert styles.index('/static/css/clinic-v8.css') < styles.index('/static/css/clinic-admin.css') < styles.index('/static/css/tailwind.css')
    assert shell.locator('.pagehead h1').evaluate('el => parseFloat(getComputedStyle(el).fontSize)') >= 28
    assert not errors, errors


def test_accounts_status_search_editor_and_mobile_card(live_server, browser):
    base, _ids = live_server
    page, errors = _login(browser, base, ADMIN_EMAIL)
    page.goto(_canonical(base, PAGES["clinic-admin-users"][0]))
    shell = _wait_page(page, "clinic-admin-users")
    card = shell.locator('.cadm-panel[aria-labelledby=acc-title]')
    rows = card.locator('tbody tr')
    expect(rows.first).to_be_visible()
    count = rows.count()
    assert all(t.strip() for t in card.locator('thead th').all_inner_texts())
    expect(card.locator('tbody .cadm-account-status .pill-status')).to_have_count(count)
    for name in ('Rolle', 'Status', 'Anmelderichtlinie', 'Sortierung'):
        expect(card.get_by_role('combobox', name=name, exact=True)).to_be_visible()
    card.get_by_role('searchbox').fill('w226typist')
    expect(rows).to_have_count(1)
    card.get_by_role('button',name='Filter zurücksetzen').click()
    expect(rows).to_have_count(count)
    card.get_by_role('combobox',name='Status',exact=True).select_option('disabled')
    expect(rows).to_have_count(0)
    card.get_by_role('button',name='Filter zurücksetzen').click()
    expect(rows).to_have_count(count)
    card.get_by_role('button',name='Bearbeiten: w226admin',exact=True).click()
    editor = card.get_by_role('form', name='Konto bearbeiten: w226admin')
    expect(editor.locator('#acc-username')).to_be_visible()
    expect(editor.get_by_role('heading')).to_have_text('Konto bearbeiten: w226admin')
    apply, cancel = (editor.get_by_role('button',name=k,exact=True) for k in ('Übernehmen','Abbrechen'))
    assert apply.bounding_box()['y'] == cancel.bounding_box()['y']
    expect(editor.get_by_role('button', name='OK', exact=True)).to_have_count(0)
    expect(editor.get_by_role('button', name='Speichern und schließen', exact=True)).to_be_visible()
    cancel.click()
    shell.get_by_role('button', name='Fachrechte', exact=True).click()
    rights = shell.get_by_role('region',name='Fachrechte',exact=True)
    expect(rights.get_by_role('status')).to_contain_text('Benutzer')
    rights.get_by_label('Benutzer', exact=True).select_option(label='w226typist')
    expect(rights.locator('#grants-title')).to_be_visible()
    shell.get_by_role('button',name='Konten',exact=True).click()
    page.set_viewport_size({'width':390,'height':844})
    hint = card.locator('p.hint[role=status]')
    expect(hint).to_be_visible()
    box, area = hint.bounding_box(), card.bounding_box()
    assert box['x'] + box['width'] <= area['x'] + area['width'] + 1
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    assert not errors, errors


def test_jobs_filter_matches_the_refresh_button_at_both_widths(live_server, browser):
    base, _ids = live_server
    page, errors = _login(browser, base, ADMIN_EMAIL)
    page.goto(base + "/#/verwaltung/verarbeitung")
    shell = _wait_page(page, "clinic-admin-jobs")
    field = shell.get_by_test_id("state-filter")
    refresh = shell.get_by_test_id("reload")
    for width, height in ((1440, 900), (390, 844)):
        page.set_viewport_size({"width": width, "height": height})
        style = field.evaluate("""el => {
            const s = getComputedStyle(el);
            return [s.appearance, s.backgroundImage, s.fontWeight];
        }""")
        assert style[0] == "none" and "svg" in style[1] and style[2] == "400", style
        select_box, button_box = field.bounding_box(), refresh.bounding_box()
        assert select_box["y"] == button_box["y"]
        assert select_box["height"] == button_box["height"] == 38
        assert refresh.evaluate("el => getComputedStyle(el).fontSize") == "13px"
    assert not errors, errors
