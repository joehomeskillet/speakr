"""Page registry of the V8 browser harness (no browser needed)."""

import v8_browser_support as support


def test_registry_covers_p01_to_p21_once():
    assert [p.page_id for p in support.PAGES] == [f"P{n:02d}" for n in range(1, 22)]


def test_mounted_entries_name_view_and_roles():
    mounted = [p for p in support.PAGES if p.mounted]
    assert [page.view for page in mounted] == [
        "recordings", "worklist", "detail", "recording", "live", "correction", "review",
        "archive", "import", "admin/pools", "admin/authors", "admin/accounts", "resources",
        "admin/commands", "admin/asr", "output", "jobs", "activity", "settings", "login", "offline",
    ]
    for page in mounted:
        if page.page_id == "P20":
            assert page.roles == ("anonymous",)
        else:
            assert page.roles and set(page.roles) <= set(support.ROLES), page.page_id


def test_unmounted_entries_carry_no_roles():
    assert all(not p.roles for p in support.PAGES if not p.mounted)


def test_matrix_is_the_v8_plan():
    assert support.VIEWPORTS == {"1440x900": (1440, 900), "1280x720": (1280, 720), "390x844": (390, 844)}
    assert support.THEMES == ("light", "dark")
