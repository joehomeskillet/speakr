# Appearance, themes and accessibility

[Deutsch](../de/advanced/ui-themes.md)

The clinic workspace has light and dark surfaces, desktop worklists and a compact phone view. The upstream appearance system separately stores an account theme, colour scheme and brightness. The clinic components have their own layout rules, so check the actual recording and administration views after changing an available preference.

## Verify a change

1. In an authorized test account, use the available appearance controls. Return to the recording list and open a synthetic dictation. At narrow width, check that a recording title opens the same dictation as on desktop.
2. Check Play, focus indication, button names, status badges, the correction toolbar and any form validation in light and dark mode. Use the keyboard to reach and activate controls. Check the actual transcript as well as the surrounding chrome.
3. Reopen the page and check what persists for the account and browser. A service-worker or browser cache can briefly serve older assets; record the version before comparing two screenshots.

## Adding or maintaining a theme

The [theme registry](https://github.com/joehomeskillet/speakr/blob/main/src/config/ui_themes.py) owns IDs, availability and the allowed markup slots. Shared components supply defaults; a theme can override a named slot. An unknown or withdrawn stored ID resolves to the default. Use the project tokens and preserve control labels, keyboard handling and contrast. Run `python -m pytest tests/test_ui_themes.py tests/test_ui_theme_slots.py` and check the [actual clinic screens](../screenshots.md) at phone and desktop widths.

Original reference for upstream Speakr: [archived upstream page](https://github.com/murtaza-nasir/speakr/blob/039a19b1f1c7f0ddaeac0a49b806845626f48a6c/docs/advanced/ui-themes.md).
