"""Tests for DOCX template generation with versioned bookmark/insertion points.

These tests exercise the ``docx_template`` module: template version selection,
bookmark validation, and rendering. They are intentionally focused — they do not
cover the full export pipeline (that is :file:`test_export_service.py`).
"""

from io import BytesIO

import pytest
import src.clinic.docx_template as dt
from docx import Document


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_docx(content_bytes: bytes) -> Document:
    """Load a DOCX from bytes and return a python-docx Document."""
    buf = BytesIO(content_bytes)
    return Document(buf)


# ---------------------------------------------------------------------------
# Template version & bookmark basics
# ---------------------------------------------------------------------------

def test_template_versions_are_defined():
    """All ``TemplateVersion`` constants exist in ``_TEMPLATES``."""
    for v in (dt.TemplateVersion.V1, dt.TemplateVersion.V2, dt.TemplateVersion.V3):
        assert v in dt._TEMPLATES


def test_template_version_v1_has_no_bookmarks():
    """V1 template has an empty bookmark list (implicit text runs)."""
    assert dt._expected_bookmarks(dt.TemplateVersion.V1) == []


def test_template_version_v2_bookmarks():
    """V2 template expects four standard bookmarks."""
    expected = dt._expected_bookmarks(dt.TemplateVersion.V2)
    assert set(expected) == {"DICTATION_TITLE", "PATIENT_NAME", "PROVIDER_NAME", "DATE"}


def test_template_version_v3_bookmarks():
    """V3 template expects five bookmarks including the signature."""
    expected = dt._expected_bookmarks(dt.TemplateVersion.V3)
    assert set(expected) == {
        "DICTATION_TITLE",
        "PATIENT_NAME",
        "PROVIDER_NAME",
        "DATE",
        "AUTHOR_SIGNATURE",
    }


# ---------------------------------------------------------------------------
# render() — positive cases (validate=False, bookmarks not checked)
# ---------------------------------------------------------------------------

def test_render_v2_no_validation():
    """V2 render without validation succeeds (bookmarks not checked)."""
    result = dt.render(
        version=dt.TemplateVersion.V2,
        title="Test Title",
        header_lines=["Status: freigegeben"],
        paragraphs=["First paragraph.", "Second paragraph."],
        validate=False,
    )
    doc = _make_docx(result)
    assert result  # non-empty bytes


def test_render_v3_no_validation():
    """V3 render without validation succeeds (bookmarks not checked)."""
    result = dt.render(
        version=dt.TemplateVersion.V3,
        title="Test Title",
        header_lines=["Status: freigegeben"],
        paragraphs=["First paragraph."],
        validate=False,
    )
    assert result  # non-empty bytes


# ---------------------------------------------------------------------------
# render() — validation enforcement (validate=True)
# ---------------------------------------------------------------------------

def test_render_v2_with_validation_requires_bookmarks():
    """V2 render with validate=True raises TemplateError when bookmarks are missing."""
    with pytest.raises(dt.TemplateError, match="missing required"):
        dt.render(
            version=dt.TemplateVersion.V2,
            title="Test Title",
            header_lines=["Status: freigegeben"],
            paragraphs=["First paragraph."],
            validate=True,
        )


def test_render_v3_with_validation_requires_bookmarks():
    """V3 render with validate=True raises TemplateError when bookmarks are missing."""
    with pytest.raises(dt.TemplateError, match="missing required"):
        dt.render(
            version=dt.TemplateVersion.V3,
            title="Test Title",
            header_lines=["Status: freigegeben"],
            paragraphs=["First paragraph."],
            validate=True,
        )


# ---------------------------------------------------------------------------
# _apply_template_format — V3 footer
# ---------------------------------------------------------------------------

def test_apply_v3_adds_footer():
    """V3 format adds a generator footer paragraph."""
    from src.clinic.docx_template import _apply_template_format

    # Generate a valid DOCX via render then inspect the footer
    result = dt.render(
        version=dt.TemplateVersion.V3,
        title="Test Title",
        header_lines=["Status: freigegeben"],
        paragraphs=["First paragraph."],
        validate=False,
    )
    doc = _make_docx(result)
    _apply_template_format(doc, dt.TemplateVersion.V3)
    last_para = doc.paragraphs[-1]
    assert "Generator:" in last_para.text


# ---------------------------------------------------------------------------
# TemplateError propagation
# ---------------------------------------------------------------------------

def test_template_error_is_value_error():
    """``TemplateError`` subclasses ``ValueError``."""
    try:
        raise dt.TemplateError("test message")
    except ValueError:
        pass  # expected
    else:
        pytest.fail("TemplateError should subclass ValueError")