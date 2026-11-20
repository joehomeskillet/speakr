"""Render Material's controls in the language of the current handbook page."""

from copy import copy


def on_page_context(context, *, page, config, nav):
    if not page.file.src_uri.startswith("de/"):
        return context

    palette = []
    for entry in config.theme["palette"]:
        toggle = entry.get("toggle", {})
        if "name_de" in toggle:
            entry = {**entry, "toggle": {**toggle, "name": toggle["name_de"]}}
        palette.append(entry)

    # MkDocs shares its configuration between renders; keep this override page-local.
    localized = copy(config)
    localized.data = {**config, "theme": {**config.theme, "language": "de", "palette": palette}}
    return {**context, "config": localized}
