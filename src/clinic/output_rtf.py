"""RTF 1.x document export for clinic transcripts.

Emits formatted transcript text (paragraphs, bold/italic formatting from
HTML or Markdown, and Unicode umlauts/characters as RTF \\uN escapes)
as deterministic RTF 1.x bytes. Provides plain-text fallback for unformatted
transcripts, and a structural RTF reader/validator for self-testing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from src.utils.transcript_render import (
    BASIC_TEMPLATE_FORMAT,
    parse_segments,
    render_segments,
)

# --- RTF escaping and formatting constants ---------------------------------

RTF_HEADER = (
    r"{\rtf1\ansi\ansicpg1252\deff0\nouicompat"
    "\r\n"
    r"{\fonttbl{\f0\fnil\fcharset0 Arial;}}"
    "\r\n"
    r"{\colortbl ;\red0\green0\blue0;}"
    "\r\n"
    r"{\*\generator Speakr Clinic RTF 1.0}\viewkind4\uc1"
)

# Inline tokens: HTML tags and Markdown delimiters
_INLINE_TOKEN_RE = re.compile(
    r"(?P<tag_b_open><(?:b|strong)>)|"
    r"(?P<tag_b_close></(?:b|strong)>)|"
    r"(?P<tag_i_open><(?:i|em)>)|"
    r"(?P<tag_i_close></(?:i|em)>)|"
    r"(?P<tag_u_open><u>)|"
    r"(?P<tag_u_close></u>)|"
    r"(?P<tag_br><br\s*/?>)|"
    r"(?P<md_bi>\*\*\*|___)|"
    r"(?P<md_b>\*\*|__)|"
    r"(?P<md_i>\*|_)",
    re.IGNORECASE,
)

# HTML paragraph tags
_HTML_P_RE = re.compile(r"<p\b[^>]*>(.*?)</p>", re.IGNORECASE | re.DOTALL)
_HTML_H_RE = re.compile(r"<h([1-6])\b[^>]*>(.*?)</h\1>", re.IGNORECASE | re.DOTALL)


class RTFValidationError(ValueError):
    """The RTF document is syntactically invalid or failed structural validation."""


@dataclass(frozen=True)
class Span:
    """A segment of text with inline styling."""

    text: str
    bold: bool = False
    italic: bool = False
    underline: bool = False


@dataclass
class Paragraph:
    """A logical paragraph consisting of styled spans."""

    spans: list[Span]
    heading_level: int = 0
    is_bullet: bool = False


def rtf_escape_char(ch: str) -> str:
    """Escape a single character for RTF 1.x output.

    ASCII printable characters (except \\, {, }) are preserved as-is.
    Control and Unicode characters are escaped using RTF \\uN? syntax
    where N is a signed 16-bit integer.
    """
    if ch == "\\":
        return r"\\"
    if ch == "{":
        return r"\{"
    if ch == "}":
        return r"\}"
    if ch == "\t":
        return r"\tab "
    if ch == "\n":
        return r"\line "

    cp = ord(ch)
    if 32 <= cp <= 126:
        return ch

    # Standard RTF Unicode escape with 1-byte fallback (?)
    if cp <= 0xFFFF:
        n = cp if cp <= 32767 else cp - 65536
        return f"\\u{n}?"

    # UTF-16 surrogate pairs for characters outside BMP (e.g. emoji)
    encoded = ch.encode("utf-16-le")
    w1 = int.from_bytes(encoded[:2], "little")
    w2 = int.from_bytes(encoded[2:], "little")
    n1 = w1 if w1 <= 32767 else w1 - 65536
    n2 = w2 if w2 <= 32767 else w2 - 65536
    return f"\\u{n1}?\\u{n2}?"


def rtf_escape(text: str) -> str:
    """Escape an entire string for RTF output."""
    return "".join(rtf_escape_char(c) for c in text)


def _has_matching_md_close(text: str, start_idx: int, delim: str) -> bool:
    """Check if an opening markdown delimiter has a matching close on the same line."""
    pos = start_idx + len(delim)
    while True:
        idx = text.find(delim, pos)
        if idx == -1:
            return False
        before = text[idx - 1] if idx > 0 else " "
        after = text[idx + len(delim)] if idx + len(delim) < len(text) else " "
        if delim == "_":
            if not before.isspace() and not after.isalnum():
                return True
        elif delim == "*":
            if not before.isspace():
                return True
        elif delim in ("**", "__", "***", "___"):
            if not before.isspace():
                return True
        pos = idx + 1


def _parse_inline(text: str) -> list[Span]:
    """Parse inline text into styled spans (HTML, Markdown, or plain text)."""
    if not text:
        return []

    spans: list[Span] = []
    bold = False
    italic = False
    underline = False

    md_b_active = False
    md_i_active = False
    md_bi_active = False

    last_end = 0
    for m in _INLINE_TOKEN_RE.finditer(text):
        start, end = m.span()
        kind = m.lastgroup

        # Validate markdown delimiters before toggling
        if kind == "md_i":
            delim = m.group(0)
            before = text[start - 1] if start > 0 else " "
            after = text[end] if end < len(text) else " "
            if not md_i_active:
                # Opening: must not be followed by space, and for _ not preceded by word char
                valid = not after.isspace() and (delim != "_" or not before.isalnum())
                if not valid or not _has_matching_md_close(text, start, delim):
                    continue
            else:
                # Closing: must not be preceded by space, and for _ not followed by word char
                valid = not before.isspace() and (delim != "_" or not after.isalnum())
                if not valid:
                    continue
        elif kind == "md_b":
            delim = m.group(0)
            if not md_b_active:
                after = text[end] if end < len(text) else " "
                if after.isspace() or not _has_matching_md_close(text, start, delim):
                    continue
            else:
                before = text[start - 1] if start > 0 else " "
                if before.isspace():
                    continue
        elif kind == "md_bi":
            delim = m.group(0)
            if not md_bi_active:
                after = text[end] if end < len(text) else " "
                if after.isspace() or not _has_matching_md_close(text, start, delim):
                    continue
            else:
                before = text[start - 1] if start > 0 else " "
                if before.isspace():
                    continue

        if start > last_end:
            spans.append(Span(text=text[last_end:start], bold=bold, italic=italic, underline=underline))
        last_end = end

        if kind == "tag_b_open":
            bold = True
        elif kind == "tag_b_close":
            bold = md_b_active or md_bi_active
        elif kind == "tag_i_open":
            italic = True
        elif kind == "tag_i_close":
            italic = md_i_active or md_bi_active
        elif kind == "tag_u_open":
            underline = True
        elif kind == "tag_u_close":
            underline = False
        elif kind == "tag_br":
            spans.append(Span(text="\n", bold=bold, italic=italic, underline=underline))
        elif kind == "md_bi":
            md_bi_active = not md_bi_active
            bold = md_b_active or md_bi_active
            italic = md_i_active or md_bi_active
        elif kind == "md_b":
            md_b_active = not md_b_active
            bold = md_b_active or md_bi_active
        elif kind == "md_i":
            md_i_active = not md_i_active
            italic = md_i_active or md_bi_active

    if last_end < len(text):
        spans.append(Span(text=text[last_end:], bold=bold, italic=italic, underline=underline))

    # Coalesce adjacent spans with identical styling
    coalesced: list[Span] = []
    for s in spans:
        if not s.text:
            continue
        if coalesced and (
            coalesced[-1].bold == s.bold
            and coalesced[-1].italic == s.italic
            and coalesced[-1].underline == s.underline
        ):
            coalesced[-1] = Span(
                text=coalesced[-1].text + s.text,
                bold=s.bold,
                italic=s.italic,
                underline=s.underline,
            )
        else:
            coalesced.append(s)

    return coalesced


def _parse_paragraphs(raw_text: str) -> list[Paragraph]:
    """Parse a full transcript string into a list of Paragraph objects."""
    if not raw_text:
        return []

    # If text is Whisper JSON segment list, convert it first
    segments = parse_segments(raw_text)
    if segments is not None:
        raw_text = render_segments(segments, BASIC_TEMPLATE_FORMAT)

    # Normalize line endings
    normalized = raw_text.replace("\r\n", "\n").replace("\r", "\n")

    # If text contains HTML <p> or <hN> tags, extract structured HTML blocks
    if _HTML_P_RE.search(normalized) or _HTML_H_RE.search(normalized):
        return _parse_html_blocks(normalized)

    # Standard line-based parsing (Markdown / Plain Text)
    lines = normalized.split("\n")
    paragraphs: list[Paragraph] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            paragraphs.append(Paragraph(spans=[]))
            continue

        # Markdown heading check
        heading_match = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading_match:
            level = len(heading_match.group(1))
            heading_text = heading_match.group(2)
            paragraphs.append(
                Paragraph(
                    spans=[Span(text=heading_text, bold=True)],
                    heading_level=level,
                )
            )
            continue

        # Bullet list check
        bullet_match = re.match(r"^[-+*]\s+(.*)$", line)
        if bullet_match and not (line.startswith("*") and line.rstrip().endswith("*")):
            bullet_text = bullet_match.group(1)
            paragraphs.append(
                Paragraph(
                    spans=_parse_inline(bullet_text),
                    is_bullet=True,
                )
            )
            continue

        paragraphs.append(Paragraph(spans=_parse_inline(line)))

    return paragraphs


def _parse_html_blocks(html_text: str) -> list[Paragraph]:
    """Extract paragraphs and headings from an HTML-tagged text."""
    paragraphs: list[Paragraph] = []
    # Match blocks like <p>...</p>, <h1>...</h1>, etc.
    block_re = re.compile(r"<(p|h[1-6])\b[^>]*>(.*?)</\1>", re.IGNORECASE | re.DOTALL)
    last_end = 0

    for m in block_re.finditer(html_text):
        start, end = m.span()
        # Any text before the tag block
        pre = html_text[last_end:start].strip()
        if pre:
            for line in pre.split("\n"):
                if line.strip():
                    paragraphs.append(Paragraph(spans=_parse_inline(line)))
        last_end = end

        tag = m.group(1).lower()
        content = m.group(2).strip()
        if tag.startswith("h"):
            level = int(tag[1])
            paragraphs.append(
                Paragraph(
                    spans=[Span(text=re.sub(r"<[^>]+>", "", content), bold=True)],
                    heading_level=level,
                )
            )
        else:
            paragraphs.append(Paragraph(spans=_parse_inline(content)))

    tail = html_text[last_end:].strip()
    if tail:
        for line in tail.split("\n"):
            if line.strip():
                paragraphs.append(Paragraph(spans=_parse_inline(line)))

    return paragraphs


# --- RTF Rendering ---------------------------------------------------------

def _format_span(span: Span) -> str:
    """Format a styled span into RTF."""
    escaped = rtf_escape(span.text)
    if not (span.bold or span.italic or span.underline):
        return escaped

    cmds: list[str] = []
    if span.bold:
        cmds.append(r"\b")
    if span.italic:
        cmds.append(r"\i")
    if span.underline:
        cmds.append(r"\ul")

    cmd_str = "".join(cmds)
    return f"{{{cmd_str} {escaped}}}"


def _render_rtf(paragraphs: list[Paragraph], *, title: str | None = None) -> bytes:
    """Render paragraphs into RTF 1.x bytes."""
    lines: list[str] = [RTF_HEADER]

    if title:
        esc_title = rtf_escape(title)
        lines.append(rf"\pard\sa240\sl276\slmult1\f0\fs28 {{\b {esc_title}}}\par")

    for p in paragraphs:
        if p.heading_level > 0:
            fs = 28 if p.heading_level == 1 else (26 if p.heading_level == 2 else 24)
            span_text = "".join(_format_span(s) for s in p.spans)
            lines.append(rf"\pard\sa200\sl276\slmult1\f0\fs{fs} {{\b {span_text}}}\par")
            continue

        p_str = r"\pard\sa180\sl276\slmult1\f0\fs24 "
        if p.is_bullet:
            p_str += r"\bullet\tab "

        for span in p.spans:
            p_str += _format_span(span)

        p_str += r"\par"
        lines.append(p_str)

    lines.append("}")
    rtf_content = "\r\n".join(lines)
    return rtf_content.encode("ascii")


# --- Structural RTF Reader & Validator -------------------------------------

class RTFDocument:
    """Represents a validated RTF document with parsed content."""

    def __init__(self, raw: str | bytes):
        if isinstance(raw, bytes):
            self.raw_text = raw.decode("ascii", errors="replace")
        else:
            self.raw_text = raw

        self.paragraphs: list[str] = []
        self.styled_paragraphs: list[list[Span]] = []
        self.is_valid: bool = False
        self.groups_balanced: bool = False
        self._parse()

    @property
    def full_text(self) -> str:
        return "\n".join(self.paragraphs)

    def _parse(self) -> None:
        raw = self.raw_text.strip()
        if not raw.startswith(r"{\rtf1"):
            raise RTFValidationError(r"Document does not start with {\rtf1")

        state_stack: list[dict[str, Any]] = [{"bold": False, "italic": False, "underline": False, "skip": False}]
        paragraphs: list[str] = []
        styled_paras: list[list[Span]] = []
        cur_para_text: list[str] = []
        cur_styled_spans: list[Span] = []
        cur_span_chars: list[str] = []
        cur_span_state = {"bold": False, "italic": False, "underline": False}

        def _flush_span():
            if cur_span_chars:
                text = "".join(cur_span_chars)
                cur_styled_spans.append(
                    Span(
                        text=text,
                        bold=cur_span_state["bold"],
                        italic=cur_span_state["italic"],
                        underline=cur_span_state["underline"],
                    )
                )
                cur_span_chars.clear()

        i = 0
        n = len(raw)
        uc = 1

        while i < n:
            c = raw[i]
            if c == "{":
                _flush_span()
                top = state_stack[-1].copy()
                rest = raw[i + 1 : i + 30]
                if rest.startswith(r"\fonttbl") or rest.startswith(r"\colortbl") or rest.startswith(r"\*"):
                    top["skip"] = True
                state_stack.append(top)
                cur_span_state = {
                    "bold": top["bold"],
                    "italic": top["italic"],
                    "underline": top["underline"],
                }
                i += 1
                continue

            if c == "}":
                _flush_span()
                if len(state_stack) <= 1:
                    raise RTFValidationError(f"Unmatched closing brace '}}' at index {i}")
                state_stack.pop()
                top = state_stack[-1]
                cur_span_state = {
                    "bold": top["bold"],
                    "italic": top["italic"],
                    "underline": top["underline"],
                }
                i += 1
                continue

            cur_state = state_stack[-1]
            if cur_state["skip"]:
                i += 1
                continue

            if c == "\\":
                i += 1
                if i >= n:
                    raise RTFValidationError("Trailing backslash in RTF stream")
                c2 = raw[i]
                if c2 in ("{", "}", "\\"):
                    cur_para_text.append(c2)
                    cur_span_chars.append(c2)
                    i += 1
                elif c2 == "'":
                    hex_str = raw[i + 1 : i + 3]
                    ch = chr(int(hex_str, 16))
                    cur_para_text.append(ch)
                    cur_span_chars.append(ch)
                    i += 3
                else:
                    m = re.match(r"([a-zA-Z]+)(-?[0-9]+)? ?", raw[i:])
                    if not m:
                        cur_para_text.append(c2)
                        cur_span_chars.append(c2)
                        i += 1
                        continue
                    cmd = m.group(1)
                    param = int(m.group(2)) if m.group(2) else None
                    i += len(m.group(0))

                    if cmd == "par":
                        _flush_span()
                        paragraphs.append("".join(cur_para_text))
                        styled_paras.append(list(cur_styled_spans))
                        cur_para_text.clear()
                        cur_styled_spans.clear()
                    elif cmd == "line":
                        cur_para_text.append("\n")
                        cur_span_chars.append("\n")
                    elif cmd == "tab":
                        cur_para_text.append("\t")
                        cur_span_chars.append("\t")
                    elif cmd == "b":
                        _flush_span()
                        is_on = True if param is None else (param != 0)
                        cur_state["bold"] = is_on
                        cur_span_state["bold"] = is_on
                    elif cmd == "i":
                        _flush_span()
                        is_on = True if param is None else (param != 0)
                        cur_state["italic"] = is_on
                        cur_span_state["italic"] = is_on
                    elif cmd == "ul":
                        _flush_span()
                        is_on = True if param is None else (param != 0)
                        cur_state["underline"] = is_on
                        cur_span_state["underline"] = is_on
                    elif cmd == "uc":
                        uc = param if param is not None else 1
                    elif cmd == "u" and param is not None:
                        cp = param if param >= 0 else param + 65536
                        ch = chr(cp)
                        cur_para_text.append(ch)
                        cur_span_chars.append(ch)
                        # Skip uc fallback characters
                        for _ in range(uc):
                            if i < n and raw[i] not in ("{", "}", "\\"):
                                i += 1
            elif c in ("\r", "\n"):
                i += 1
            else:
                cur_para_text.append(c)
                cur_span_chars.append(c)
                i += 1

        if len(state_stack) != 1:
            raise RTFValidationError(f"Unbalanced braces at end of stream (depth={len(state_stack) - 1})")

        _flush_span()
        if cur_para_text:
            paragraphs.append("".join(cur_para_text))
            styled_paras.append(list(cur_styled_spans))

        self.paragraphs = paragraphs
        self.styled_paragraphs = styled_paras
        self.groups_balanced = True
        self.is_valid = True


def validate_rtf(data: bytes | str) -> RTFDocument:
    """Validate that RTF data has balanced groups and standard RTF 1.x syntax."""
    return RTFDocument(data)


# --- Public Entry Points ---------------------------------------------------

def text_to_rtf(text: str, *, title: str | None = None) -> bytes:
    """Convert a transcript text string (Markdown, HTML, or plain text) into RTF 1.x bytes."""
    paragraphs = _parse_paragraphs(text)
    rtf_bytes = _render_rtf(paragraphs, title=title)
    # Self-validation check
    validate_rtf(rtf_bytes)
    return rtf_bytes


def get_rtf_bytes(source: Any, *, title: str | None = None) -> bytes:
    """Return RTF 1.x bytes for a given clinic transcript revision, release, recording, or text.

    Supported source types:
    - ClinicTranscriptRevision (extracts .body)
    - ClinicRelease (resolves the bound transcript revision)
    - Recording (extracts .transcription)
    - str (used as-is)
    - None (empty document)
    """
    if source is None:
        return text_to_rtf("", title=title)

    if isinstance(source, str):
        return text_to_rtf(source, title=title)

    # ClinicTranscriptRevision
    if hasattr(source, "body") and hasattr(source, "normalised_text_hash"):
        doc_title = title
        return text_to_rtf(source.body or "", title=doc_title)

    # ClinicRelease
    if hasattr(source, "transcript_revision_id"):
        from src.clinic.models import ClinicTranscriptRevision
        from src.database import db

        rev = db.session.get(ClinicTranscriptRevision, source.transcript_revision_id)
        if rev is None:
            raise ValueError(f"Bound transcript revision {source.transcript_revision_id} not found")
        doc_title = title or getattr(source, "document_type", None)
        return text_to_rtf(rev.body or "", title=doc_title)

    # Recording
    if hasattr(source, "transcription"):
        rec_title = title or getattr(source, "title", None)
        return text_to_rtf(source.transcription or "", title=rec_title)

    # Fallback if source has a 'body' attribute
    if hasattr(source, "body"):
        return text_to_rtf(getattr(source, "body") or "", title=title)

    raise TypeError(f"Unsupported transcript source type: {type(source).__name__}")
