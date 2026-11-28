"""Validate backup templates and render file names without touching storage."""

import re
from pathlib import PureWindowsPath
from uuid import UUID

from .output import PLACEHOLDERS, OutputNameContext, OutputNameError, expand_filename


_TEMPLATE = re.compile(rf"(?:[A-Za-z0-9 ._()\-]|%[%{''.join(PLACEHOLDERS)}])+")


def validate_template(value: str | None) -> str:
    if value is None or value == "":
        return ""
    if (not isinstance(value, str) or len(value) > 255 or value != value.strip()
            or not value.strip(" .") or _TEMPLATE.fullmatch(value) is None):
        raise OutputNameError("invalid backup filename template")
    return value


def backup_filename(template: str, context: OutputNameContext, original_filename: str, copy_id: UUID) -> str:
    """Use a distinct copy ID per backup. The writer must still create exclusively."""
    original = PureWindowsPath(original_filename)
    if not original.name or original.name != original_filename or not isinstance(copy_id, UUID):
        raise OutputNameError("backup requires an original basename and copy UUID")
    suffix = original.suffix
    pattern = validate_template(template)
    if suffix and pattern.lower().endswith(suffix.lower()):
        pattern = pattern[:-len(suffix)]
    pattern = pattern or original.stem.replace("%", "%%")
    # Leave space for the UUID and extension within a 255-byte file name.
    filename = expand_filename(pattern, context, extension=suffix[1:] or None, max_stem_length=52)
    return f"{filename.removesuffix(suffix)}_{copy_id.hex}{suffix}"
