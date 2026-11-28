import sys
from contextlib import nullcontext
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, PropertyMock

import pytest

from scripts.persona_panel.capture import (
    STAGING_ORIGIN,
    CaptureError,
    capture_views,
    read_credentials,
    routes_for,
    validate_routes,
)


@pytest.mark.parametrize(
    ("extra_fields", "expected_email"),
    [
        (f"url={STAGING_ORIGIN}\n", None),
        (
            f"email=author@example.invalid\nurl={STAGING_ORIGIN}/\n",
            "author@example.invalid",
        ),
    ],
)
def test_read_credentials_normalizes_url_and_preserves_password(
    tmp_path: Path, extra_fields: str, expected_email: str | None
) -> None:
    path = tmp_path / "credentials"
    path.write_text(
        f"username=p-author\npassword=synthetic=with=equals\nignored=extra\n{extra_fields}",
        encoding="utf-8",
    )

    credentials = read_credentials(path)

    assert credentials.username == "p-author"
    assert credentials.email == expected_email
    assert credentials.password == "synthetic=with=equals"
    assert credentials.url == STAGING_ORIGIN


def test_read_credentials_rejects_non_staging_url_without_disclosing_values(
    tmp_path: Path,
) -> None:
    path = tmp_path / "credentials"
    bad_url = f"{STAGING_ORIGIN}.example.invalid/private"
    secret = "synthetic-error-secret=123"
    path.write_text(
        f"username=p-author\npassword={secret}\nurl={bad_url}\n", encoding="utf-8"
    )

    with pytest.raises(CaptureError) as exc:
        read_credentials(path)

    assert secret not in str(exc.value)
    assert bad_url not in str(exc.value)


def test_read_credentials_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        read_credentials(tmp_path / "missing")


def test_validate_routes_accepts_lowercase_slugs_and_nested_paths() -> None:
    routes = ["#/neu", "#/arbeitsliste/offene-diktate"]
    assert validate_routes(routes) == routes


@pytest.mark.parametrize(
    "routes",
    [
        [],
        [""],
        ["#/"],
        ["/neu"],
        ["#/Neu"],
        ["#/neu/../aufnahmen"],
        ["#/neu//aufnahmen"],
        ["#/neu?next=aufnahmen"],
        ["#/neu", "#/aufnahmen%2fneu"],
    ],
)
def test_validate_routes_rejects_empty_or_unsafe_routes(routes: list[str]) -> None:
    with pytest.raises(CaptureError):
        validate_routes(routes)


@pytest.mark.parametrize(
    "persona", ["schreibkraft", "schreibkraft_ambulant", "leitung_schreibdienst"]
)
def test_routes_for_typists_maps_author_views_and_deduplicates_in_order(
    persona: str,
) -> None:
    routes = ["#/aufnahmen", "#/profil", "#/neu", "#/arbeitsliste", "#/profil"]
    assert routes_for(persona, routes) == ["#/arbeitsliste", "#/profil"]
    assert routes_for(persona, ["#/neu"]) == ["#/arbeitsliste"]


def test_routes_for_author_keeps_author_views_and_deduplicates_in_order() -> None:
    assert routes_for("arzt_ambulant", ["#/neu", "#/aufnahmen", "#/neu"]) == [
        "#/neu",
        "#/aufnahmen",
    ]


class _FakePlaywright:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.browser = MagicMock()
        playwright = MagicMock()
        playwright.chromium.launch.return_value = self.browser
        playwright.chromium.executable_path = "/nonexistent/chromium"
        package = ModuleType("playwright")
        sync_api = ModuleType("playwright.sync_api")
        sync_api.__dict__["sync_playwright"] = lambda: nullcontext(playwright)
        sync_api.__dict__["Error"] = type("PlaywrightError", (Exception,), {})
        monkeypatch.setitem(sys.modules, "playwright", package)
        monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)
        self.browser.new_context.side_effect = self._new_context
        self.contexts: list[MagicMock] = []

    def _new_context(self, **_options: object) -> MagicMock:
        context = MagicMock()
        page = context.new_page.return_value
        page.url = f"{STAGING_ORIGIN}/"
        page.goto.side_effect = lambda url, **_kwargs: setattr(page, "url", url)
        page.locator.return_value.evaluate.return_value = f"{STAGING_ORIGIN}/login"
        page.expect_response.return_value.__enter__.return_value.value.status = 302

        def screenshot(*, path: str | Path, **_options: object) -> bytes:
            image = page.url.encode()
            Path(path).write_bytes(image)
            return image

        page.screenshot.side_effect = screenshot
        self.contexts.append(context)
        return context


def test_capture_views_isolates_personas_and_keeps_passwords_out_of_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = _FakePlaywright(monkeypatch)
    credentials_dir = tmp_path / "credentials"
    credentials_dir.mkdir()
    monkeypatch.setenv("PERSONA_PANEL_CREDENTIALS_DIR", str(credentials_dir))
    passwords = {
        "arzt_ambulant": "synthetic-author-secret=123",
        "schreibkraft": "synthetic-typist-secret=456",
    }
    for persona, password in passwords.items():
        (credentials_dir / f"persona-{persona}.txt").write_text(
            f"username=p-{persona}\nemail={persona}@example.invalid\npassword={password}\nurl={STAGING_ORIGIN}\n",
            encoding="utf-8",
        )
    out = tmp_path / "screenshots"

    views, errors = capture_views([*passwords, "pflege"], ["#/neu", "#/aufnahmen"], out)

    assert errors == {"pflege": "skipped: no staging account"}
    assert {persona: len(paths) for persona, paths in views.items()} == {
        "arzt_ambulant": 4,
        "schreibkraft": 2,
    }
    assert len(fake.contexts) == 2
    fake.browser.close.assert_called_once()
    expected_routes = {
        "arzt_ambulant": ["#/neu", "#/aufnahmen"],
        "schreibkraft": ["#/arbeitsliste"],
    }
    for context, persona in zip(fake.contexts, passwords, strict=True):
        context.new_page.assert_called_once()
        context.close.assert_called_once()
        assert all(path.is_relative_to(out) for path in views[persona])
        assert [path.read_text(encoding="utf-8") for path in views[persona]] == [
            f"{STAGING_ORIGIN}/{route}" for route in expected_routes[persona] for _ in range(2)
        ]
        assert [call.args[0] for call in context.new_page.return_value.set_viewport_size.call_args_list] == [
            {"width": 390, "height": 844}, {"width": 1440, "height": 900},
        ] * len(expected_routes[persona])
    assert len({path for paths in views.values() for path in paths}) == 6

    captured = capsys.readouterr()
    artifacts = [path for path in out.rglob("*") if path.is_file()]
    for password in passwords.values():
        assert password not in f"{views}{errors}{captured.out}{captured.err}"
        for path in artifacts:
            assert password not in str(path.relative_to(out))
            assert password.encode() not in path.read_bytes()


def test_capture_waits_for_rate_limiter_before_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakePlaywright(monkeypatch)
    monkeypatch.setenv("PERSONA_PANEL_CREDENTIALS_DIR", str(tmp_path))
    (tmp_path / "persona-chefarzt.txt").write_text(
        f"username=p-author\npassword=synthetic\nurl={STAGING_ORIGIN}\n", encoding="utf-8",
    )
    wait = MagicMock()
    monkeypatch.setattr("scripts.persona_panel.capture.time.sleep", wait)

    def rate_limited_context(**options: object) -> MagicMock:
        context = fake._new_context(**options)
        response = context.new_page.return_value.expect_response.return_value.__enter__.return_value.value
        type(response).status = PropertyMock(side_effect=[429, 302, 302])
        return context

    fake.browser.new_context.side_effect = rate_limited_context
    views, notes = capture_views(["chefarzt"], ["#/neu"], tmp_path / "run")

    wait.assert_called_once_with(60)
    assert not notes
    assert len(views["chefarzt"]) == 2
