"""Versioned DOCX templates with explicit bookmark/insertion points for clinic dictations.

This module provides template selection, bookmark validation, and DOCX rendering that
integrates with the existing export infrastructure (``new_document``, ``add_document_header``).

It replaces implicit text-insertion with explicit, version-stamped bookmark references
so that downstream consumers can reliably locate insertion points and validate that
only approved bookmarks appear in the output.

The module is deliberately thin — all formatting and layout is delegated to the
existing ``src.services.document`` helpers.  New template versions are added by
extending the ``TEMPLATES`` mapping; existing renderers continue to work without
changes.
"""

from __future__ import annotations

from io import BytesIO
from typing import Dict, List, Optional

from docx import Document as DocxDocument
from docx.shared import Pt
from docx.enum.text import WD_PARAGRAPH_ALIGNMENT

from src.clinic.models import ClinicRelease, ClinicTranscriptRevision
from src.services.document import add_document_header, new_document
from src.clinic.export import RENDERER_VERSION

# ---------------------------------------------------------------------------
# Template version constants
# ---------------------------------------------------------------------------

class TemplateVersion:
    """Version identifiers for DOCX templates.

    New versions are added by extending :data:`TEMPLATES`.  Existing callers
    that reference a version that is not yet defined will receive a clear
    ``TemplateError`` rather than silent degradation.
    """

    V1 = "1.0"
    V2 = "2.0"
    V3 = "3.0"


# ---------------------------------------------------------------------------
# Template metadata & rendering
# ---------------------------------------------------------------------------

_TEMPLATES: Dict[str, dict] = {
    TemplateVersion.V1: {
        "name": "Standard Clinic Letter v1",
        "description": "Legacy template; bookmarks are implicit text runs.",
        "bookmarks": [],
    },
    TemplateVersion.V2: {
        "name": "Standard Clinic Letter v2",
        "description": "Explicit bookmark insertion points; validated on render.",
        "bookmarks": ["DICTATION_TITLE", "PATIENT_NAME", "PROVIDER_NAME", "DATE"],
    },
    TemplateVersion.V3: {
        "name": "Standard Clinic Letter v3",
        "description": "Versioned template with mandatory footer and formatting preservation.",
        "bookmarks": ["DICTATION_TITLE", "PATIENT_NAME", "PROVIDER_NAME", "DATE", "AUTHOR_SIGNATURE"],
    },
}


class TemplateError(ValueError):
    """Raised when a template version is unknown or a bookmark is unauthorised."""


def validate_template_version(version: str) -> None:
    """Raise ``TemplateError`` if *version* is not a defined template version."""
    if version not in _TEMPLATES:
        raise TemplateError(f"Unknown template version: {version}")


def validate_bookmarks(doc: DocxDocument, expected: List[str], version: str) -> None:
    """Ensure the document contains exactly the bookmarks listed for *version*.

    Unauthorised bookmarks (present but not in *expected*) cause a ``TemplateError``.
    Missing required bookmarks also cause a ``TemplateError``.

    This is the core safety gate: any bookmark that was not explicitly approved
    for the active template version aborts the render, preventing stale or
    injected markers from appearing in the output.
    """
    validate_template_version(version)

    # Collect bookmarks actually present in the document.
    # python-docx does not expose a built-in bookmark API, so we inspect the
    # underlying ZIP XML for w:bookmarkStart / w:bookmarkEnd nodes.
    from lxml import etree

    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}

    # Read document.xml from the part's ZIP archive via the .blob attribute
    xml_bytes = doc._part.document._element.xml  # fallback: lxml element tree
    # Try the parts approach
    found = False
    for part in doc._part.package.parts:
        if str(part.partname).endswith("/word/document.xml"):
            xml_bytes = part.blob
            found = True
            break
    if not found:
        # Fallback: use the document's internal XML representation
        xml_bytes = doc._part.document._element.xml

    root = etree.fromstring(xml_bytes if isinstance(xml_bytes, bytes) else xml_bytes.encode("utf-8"))

    present: set[str] = set()
    for bm in root.iter(".//w:bookmarkStart", ns):
        name = bm.get("w:name")
        if name:
            present.add(name)
    for bm in root.iter(".//w:bookmarkEnd", ns):
        name = bm.get("w:name")
        if name:
            present.add(name)

    expected = set(expected)

    unauthorised = present - expected
    if unauthorised:
        raise TemplateError(
            f"Template version {version} contains unauthorised bookmarks: {sorted(unauthorisation)}"
        )

    missing = expected - present
    if missing:
        raise TemplateError(
            f"Template version {version} is missing required bookmarks: {sorted(missing)}"
        )


def render(
    version: str,
    title: str,
    header_lines: List[str],
    paragraphs: List[str],
    *,
    validate: bool = True,
) -> bytes:
    """Render a DOCX from the given content using the template *version*.

    Parameters
    ----------
    version:
        Template version string (e.g. ``TemplateVersion.V2``).
    title:
        Document title; rendered in the header.
    header_lines:
        Additional header lines (status, IDs, hashes, renderer version).
    paragraphs:
        Body paragraph text; each element becomes one paragraph.
    validate:
        When ``True`` (default), validate that the produced document contains
        exactly the bookmarks expected for *version*.  Set to ``False`` only
        for diagnostics or when the template has no bookmarks.

    Returns
    -------
    bytes
        The DOCX file contents as a bytes object, ready for download or further
        processing.

    Raises
    ------
    TemplateError
        If *version* is unknown, or when ``validate`` is ``True`` the produced
        document does not contain the expected bookmarks (or contains
        unauthorised ones).
    """
    validate_template_version(version)

    doc = new_document()
    add_document_header(doc, title, header_lines)

    for paragraph_text in paragraphs:
        doc.add_paragraph(paragraph_text)

    # Apply version-specific formatting
    _apply_template_format(doc, version)

    # Validate bookmarks if requested
    if validate:
        expected_bookmarks = _expected_bookmarks(version)
        if expected_bookmarks:
            validate_bookmarks(doc, expected_bookmarks, version)

    buffer = BytesIO()
    doc.save(buffer)
    return buffer.getvalue()


def _apply_template_format(doc: DocxDocument, version: str) -> None:
    """Apply version-specific formatting overrides.

    Currently only V3 adds a mandatory footer line with the renderer version.
    Future versions can extend this without changing the render API.
    """
    from docx.oxml.ns import qn

    if version == TemplateVersion.V3:
        # Footer: "Generator: clinic-export-3" centred at the bottom of the document.
        # We add a trailing paragraph with the footer text; the editor styles
        # the footer position in the template stylesheet.
        footer_para = doc.add_paragraph()
        footer_para.alignment = WD_PARAGRAPH_ALIGNMENT.CENTER
        run = footer_para.add_run(f"Generator: {RENDERER_VERSION}")
        run.font.size = Pt(8)
        run.font.color.rgb = None  # auto / inherit


def _expected_bookmarks(version: str) -> List[str]:
    """Return the bookmark list that must be present for *version*."""
    return _TEMPLATES[version]["bookmarks"]


# ---------------------------------------------------------------------------
# Convenience: render from a clinic source (release or draft)
# ---------------------------------------------------------------------------

def render_from_source(
    source: ClinicRelease | ClinicTranscriptRevision,
    *,
    allow_draft: bool = False,
    filename_label: str | None = None,
    validate: bool = True,
) -> dict:
    """Render a DOCX from a ``ClinicRelease`` or ``ClinicTranscriptRevision``.

    Returns a dict compatible with the existing ``RenderedExport`` contract
    (``content``, ``filename``, ``mimetype``, ``sha256``, plus
    ``template_version`` and ``bookmarks_valid`` fields).
    """
    from src.clinic.export import _header_lines as _eh, _paragraphs as _ep

    if isinstance(source, ClinicRelease):
        title = "Freigegebenes Diktat"
        header = _eh(source)
        body = source.body or ""
    else:
        if not allow_draft:
            raise ValueError("Draft not explicit; pass allow_draft=True")
        title = f"{RENDERER_VERSION} – Entwurf"
        header = _eh(source)
        body = source.body or ""

    paragraphs = _ep(body)

    rendered_bytes = render(
        version=source.document_type or TemplateVersion.V1,
        title=title,
        header_lines=header,
        paragraphs=paragraphs,
        validate=validate,
    )

    from hashlib import sha256

    digest = sha256(rendered_bytes).hexdigest()

    from io import BytesIO

    filename = (
        f"clinic-{source.mode}-{source.dictation_id}-{source.transcript_revision_id}.docx"
    )

    return {
        "content": rendered_bytes,
        "filename": filename,
        "mimetype": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "sha256": digest,
        "template_version": source.document_type or TemplateVersion.V1,
        "bookmarks_valid": validate,
    }