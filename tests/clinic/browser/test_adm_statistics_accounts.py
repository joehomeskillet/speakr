"""Real-browser coverage for aggregate-only account grants and clinical access isolation."""

import os
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect

import v8_browser_support as support

API = "/api/clinic/v1"
AGGREGATES = API + "/statistics/aggregates"
READER_NAME = "Statistik P131"
READER_EMAIL = "statistics-p131@local.test"
AUTHOR_A = "author-v860"
AUTHOR_B = "Diktierende B P131"
POOL_A = "Autoren V860"
EMPTY = "Keine Statistikfreigaben für dieses Konto."
SEEDED_TEXT = "Anamnese Husten seit drei Tagen."

ORIGINAL_SEED = '''
import wave
from hashlib import sha256
from pathlib import Path
from src.clinic.startup import originals_root


def stored_original(dictation, name):
    key = f"orig/p131-{name}.wav"
    path = Path(originals_root(app)) / key
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        wav.writeframes(bytes(4 * 16000 * 2))
    audio = path.read_bytes()
    return ClinicOriginal(dictation_id=dictation.id, receipt="stored", origin_ref=f"device:synthetic-{name}",
                          storage_key=key, content_hash=sha256(audio).hexdigest(), size_bytes=len(audio))
'''

EXTRA_SEED = f'''
    user({READER_NAME!r}, {READER_EMAIL!r})
    author_b = user({AUTHOR_B!r}, "author-b-p131@local.test")
    pool_b = ClinicAuthorPool(name="Autorengruppe B P131", created_by=admin.id)
    db.session.add(pool_b)
    db.session.flush()
    pools.add_author_member(pool_b.id, author_b.id, actor_id=admin.id)
    rec_b = Recording(user_id=author_b.id, title="Diktat B P131", status="COMPLETED", original_filename="b.wav")
    db.session.add(rec_b)
    db.session.flush()
    dic_b = ClinicDictation(recording_id=rec_b.id, author_user_id=author_b.id, pool_id=pool_b.id, version_counter=0)
    db.session.add(dic_b)
    db.session.flush()
    original_b = stored_original(dic_b, "b")
    db.session.add(original_b)
    db.session.flush()
    add_asr_revision(dic_b, original_b, "Synthetischer Diktattext B.", "model:synthetic")
    init_audio_revision(dic_b, original_b, 4000)
    db.session.add(workflow.ClinicWorkflowDictation(
        id=dic_b.id, author_user_id=author_b.id, pool_id=pool_b.id, status=workflow.TO_WRITE, audio_seconds=4))
    db.session.commit()
'''


def _server_source():
    setup, rest = support.SERVER.split("\nwith app.app_context():", 1)
    seed, server = rest.split("\nserver =", 1)
    before_original, original = seed.split("    original = ClinicOriginal(", 1)
    _, after_original = original.split("    db.session.add(original)", 1)
    return (
        setup + ORIGINAL_SEED + "\nwith app.app_context():" + before_original
        + '    original = stored_original(dic, "a")\n    db.session.add(original)' + after_original
        + EXTRA_SEED + "\nserver =" + server
    )


def _open_grants(page, base, account=READER_NAME):
    url = base + "/admin#/benutzer"
    if page.url == url:
        page.reload()
    else:
        page.goto(url)
    view = page.get_by_test_id("clinic-users-view")
    expect(view.get_by_role("heading", name="Benutzer und Rollen", exact=True)).to_be_visible(
        timeout=support.SHOW_TIMEOUT_MS
    )
    view.get_by_role("button", name="Statistikzugriff", exact=True).click()
    user_id, = view.locator("#usr-pick").select_option(label=account, timeout=support.SHOW_TIMEOUT_MS)
    panel = view.get_by_test_id("statistics-grants")
    expect(panel).to_be_visible()
    return panel, int(user_id)


def _grant(page, panel, url, scope, label, target_name, version):
    panel.get_by_label("Freigabe für", exact=True).select_option(scope)
    target, = panel.get_by_label(label, exact=True).select_option(label=target_name)
    with page.expect_response(lambda r: r.url == url and r.request.method == "POST") as saved:
        panel.get_by_role("button", name="Statistik freigeben", exact=True).click()
    response = saved.value
    assert response.status == 201, response.text()
    assert response.request.post_data_json["expected_version"] == version
    body = response.json()
    assert body["version"] == version + 1
    grant = body["grant"]
    assert grant["scope"] == scope
    assert grant["author_ids"] == ([int(target)] if scope == "author" else [])
    assert grant["pool_ids"] == ([target] if scope == "author_pool" else [])
    expect(panel.get_by_text("Statistikzugriff wurde freigegeben.", exact=True)).to_be_visible()
    expect(panel.get_by_role("button", name="Zugriff entziehen", exact=True)).to_have_count(1)
    return grant


def _assert_grants(context, url, user_id, version, items):
    response = context.request.get(url)
    assert response.status == 200, response.text()
    assert response.headers["x-grant-version"] == str(version)
    assert response.json() == {"user_id": user_id, "version": version, "items": items}


def _revoke(page, panel, url, user_id, grant, version):
    revoke = panel.get_by_role("button", name="Zugriff entziehen", exact=True)
    question = panel.get_by_text("Statistikzugriff entziehen?", exact=True)
    revoke.click()
    expect(question).to_be_visible()
    cancel = panel.get_by_role("button", name="Abbrechen", exact=True)
    expect(cancel).to_be_focused()
    cancel.press("Escape")
    expect(question).to_be_hidden()
    expect(revoke).to_be_focused()
    _assert_grants(page.context, url, user_id, version, [grant])

    revoke.click()
    expect(question).to_be_visible()
    endpoint = f"{url}/{grant['id']}"
    with page.expect_response(lambda r: r.url == endpoint and r.request.method == "DELETE") as removed:
        panel.get_by_role("button", name="Entziehen", exact=True).click()
    response = removed.value
    assert response.status == 200, response.text()
    assert response.request.post_data_json == {"expected_version": version}
    assert response.json()["version"] == version + 1
    expect(panel.get_by_text("Statistikzugriff wurde entzogen.", exact=True)).to_be_visible()
    expect(panel.get_by_text(EMPTY, exact=True)).to_be_visible()


def _assert_author_aggregate(response, author_id, version):
    assert response.status == 200, response.text()
    assert response.headers["x-grant-version"] == str(version)
    payload = response.json()
    assert payload["group_by"] == ["author"]
    assert [(b["author_user_id"], b["count"]) for b in payload["buckets"]] == [(author_id, 1)]


def _check_activity_and_access(reader, owner, base, author_id, excluded_id):
    page = reader.new_page()
    support._open_shell_view(page, base, "#/verwaltung/aktivitaet")
    stats = page.get_by_test_id("statistics-section")
    expect(stats).to_be_visible(timeout=support.SHOW_TIMEOUT_MS)
    stats.get_by_role("checkbox", name="Diktiert von", exact=True).check()
    with page.expect_response(
        lambda r: urlsplit(r.url).path == AGGREGATES
        and r.request.method == "GET"
        and parse_qs(urlsplit(r.url).query).get("group_by") == ["author"]
    ) as loaded:
        stats.get_by_role("button", name="Statistik laden", exact=True).click()
    _assert_author_aggregate(loaded.value, author_id, version=3)
    table = stats.get_by_role("table", name="Statistik", exact=True)
    rows = table.locator("tbody tr")
    expect(rows).to_have_count(1)
    expect(rows.locator("td").nth(0)).to_have_text(f"Benutzer {author_id}")
    expect(rows.locator("td").nth(1)).to_have_text("1")
    expect(table.get_by_text(f"Benutzer {excluded_id}", exact=True)).to_have_count(0)

    token = reader.request.get(base + "/api/csrf-token")
    assert token.status == 200
    denied = reader.request.put(base + f"/admin/users/{author_id}",
                                headers={"X-CSRF-Token": token.json()["csrf_token"]},
                                data={"expected_version": 1, "username": "P131-nicht-erlaubt"})
    assert denied.status == 403, denied.text()

    recordings = owner.request.get(base + API + "/recordings")
    assert recordings.status == 200, recordings.text()
    row, = recordings.json()["items"]
    dictation = base + API + f"/dictations/{row['dictation_id']}"
    audio_url = dictation + "/audio/derived"
    text_url = dictation + "/correction"
    audio = owner.request.get(audio_url)
    assert audio.status == 200 and audio.body().startswith(b"RIFF"), audio.status
    text = owner.request.get(text_url)
    assert text.status == 200 and text.json()["text"] == SEEDED_TEXT
    assert reader.request.get(audio_url).status == 404
    assert reader.request.get(text_url).status == 404


def _capture(browser, state, base):
    if not os.environ.get("P131_ARTIFACTS"):
        return
    out = support.ROOT / ".wp" / "artifacts"
    out.mkdir(parents=True, exist_ok=True)
    for size, (width, height) in support.VIEWPORTS.items():
        for theme in support.THEMES:
            context = browser.new_context(storage_state=state, viewport={"width": width, "height": height},
                                          **support.theme_context_options(theme))
            try:
                context.add_init_script(support.theme_init_script(theme))
                page = context.new_page()
                for label, account in (("rows", READER_NAME), ("empty", AUTHOR_A)):
                    panel, _ = _open_grants(page, base, account)
                    if label == "rows":
                        expect(panel.get_by_role("listitem")).to_contain_text(POOL_A)
                    else:
                        expect(panel.get_by_text(EMPTY, exact=True)).to_be_visible()
                    assert page.locator("html").evaluate("el => el.classList.contains('dark')") == (theme == "dark")
                    scope = panel.get_by_label("Freigabe für", exact=True)
                    scope.focus()
                    scope.press("Tab")
                    target = panel.get_by_label("Diktierende Person", exact=True)
                    expect(target).to_be_focused()
                    assert target.evaluate("el => getComputedStyle(el).outlineStyle !== 'none'")
                    target.blur()
                    for control in panel.locator("select, button").all():
                        box = control.bounding_box()
                        assert box and box["height"] >= 48
                        assert box["x"] >= 0 and box["x"] + box["width"] <= width
                    if height < 900:
                        panel.evaluate("el => el.scrollIntoView({block: 'center'})")
                    stem = out / f"statistics-grants-{label}-{size}-{theme}"
                    page.screenshot(path=stem.with_suffix(".png"), full_page=True)
                    token = page.locator('meta[name="csrf-token"]').get_attribute("content")
                    stem.with_suffix(".html").write_text(page.content().replace(token, "REDACTED"), encoding="utf-8")
                    if size == "1440x900" and theme == "light":
                        _snapshot_panel(page, stem.with_name(f"statistics-grants-{label}-render.html"))
            finally:
                context.close()


def _snapshot_panel(page, path):
    html = page.evaluate("""() => {
        const view = document.querySelector('[data-testid=clinic-users-view]').cloneNode(true);
        const selects = document.querySelectorAll('[data-testid=clinic-users-view] select');
        view.querySelectorAll('select').forEach((select, index) => {
            [...select.options].forEach((option, i) =>
                option.toggleAttribute('selected', i === selects[index].selectedIndex));
        });
        const css = [...document.styleSheets]
            .filter(sheet => sheet.href && /\\/(tokens|primitives|styles|tailwind|clinic-v8|clinic-admin)\\.css/.test(sheet.href))
            .flatMap(sheet => [...sheet.cssRules].map(rule => rule.cssText)).join('\\n');
        return '<!doctype html><html lang="de-CH"><head><meta charset="utf-8">' +
            '<meta name="viewport" content="width=device-width, initial-scale=1">' +
            '<title>Benutzer und Rollen</title><style>' + css + '</style></head>' +
            '<body class="v8-ui"><main class="cadm cadm-main">' + view.outerHTML + '</main></body></html>';
    }""")
    path.write_text(html, encoding="utf-8")


def test_statistics_grants_persist_and_do_not_allow_clinical_access(browser, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("CLINIC_ORIGINALS_ROOT", str(tmp_path / "originals"))
    with support.serve_app(tmp_path, _server_source()) as base:
        admin_state = support.login(browser, base, support.ROLES["admin"])
        reader_state = support.login(browser, base, READER_EMAIL)
        owner_state = support.login(browser, base, support.ROLES["author"])
        admin = browser.new_context(storage_state=admin_state, viewport={"width": 1440, "height": 900})
        reader = browser.new_context(storage_state=reader_state)
        owner = browser.new_context(storage_state=owner_state)
        try:
            page = admin.new_page()
            panel, reader_id = _open_grants(page, base)
            url = base + API + f"/statistics/users/{reader_id}/grants"
            aggregates = base + AGGREGATES + "?group_by=author"
            expect(panel.get_by_text(EMPTY, exact=True)).to_be_visible()
            _assert_grants(admin, url, reader_id, version=0, items=[])
            assert reader.request.get(aggregates).status == 403

            author = _grant(page, panel, url, "author", "Diktierende Person", AUTHOR_B, version=0)
            author_b, = author["author_ids"]
            _assert_author_aggregate(reader.request.get(aggregates), author_b, version=1)
            panel, _ = _open_grants(page, base)
            _assert_grants(admin, url, reader_id, version=1, items=[author])
            expect(panel.get_by_role("listitem")).to_contain_text(AUTHOR_B)
            _revoke(page, panel, url, reader_id, author, version=1)
            _assert_grants(admin, url, reader_id, version=2, items=[])

            pool = _grant(page, panel, url, "author_pool", "Autorengruppe", POOL_A, version=2)
            panel, _ = _open_grants(page, base)
            expect(panel.get_by_role("listitem")).to_contain_text(POOL_A)
            _assert_grants(admin, url, reader_id, version=3, items=[pool])
            users = page.get_by_test_id("clinic-users-view")
            author_option = users.locator("#usr-pick").get_by_text(AUTHOR_A, exact=True)
            author_a = int(author_option.get_attribute("value"))
            _check_activity_and_access(reader, owner, base, author_a, author_b)
            _capture(browser, admin_state, base)

            _revoke(page, panel, url, reader_id, pool, version=3)
            panel, _ = _open_grants(page, base)
            expect(panel.get_by_text(EMPTY, exact=True)).to_be_visible()
            expect(page.locator(f'#usr-pick option[value="{author_a}"]')).to_have_text(AUTHOR_A)
            _assert_grants(admin, url, reader_id, version=4, items=[])
            assert reader.request.get(aggregates).status == 403

            target = panel.get_by_label("Diktierende Person", exact=True)
            target.select_option(label=AUTHOR_A)
            token = admin.request.get(base + "/api/csrf-token").json()["csrf_token"]
            other_write = admin.request.post(url, headers={"X-CSRF-Token": token}, data={
                "expected_version": 4, "scope": "author", "author_ids": [author_b],
            })
            assert other_write.status == 201, other_write.text()
            save = panel.get_by_role("button", name="Statistik freigeben", exact=True)
            with page.expect_response(lambda r: r.url == url and r.request.method == "POST") as conflict:
                save.click()
            assert conflict.value.status == 409
            expect(panel.get_by_role("alert")).to_contain_text("Die Freigaben wurden inzwischen geändert.")
            expect(save).to_be_disabled()
            panel.get_by_role("button", name="Aktuellen Stand laden", exact=True).click()
            expect(target).to_have_value(str(author_a))
            expect(panel.get_by_role("listitem")).to_contain_text(AUTHOR_B)
            expect(save).to_be_enabled()
        finally:
            owner.close()
            reader.close()
            admin.close()
