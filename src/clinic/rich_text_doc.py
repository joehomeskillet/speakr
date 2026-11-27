"""Canonical rich-text document structure with stable node IDs.

Provides a pure-Python rich text model with serialisation to plain text and
sanitised HTML. The same structure produces formatted and unformatted clipboard
flavours. No HTML is ever passed through unsanitised.

Non-scope: persistence, routes, Flask integration, ASR alignment, audio anchors.
"""

from __future__ import annotations

import html
import hashlib
import json
import re
from dataclasses import dataclass, replace
from typing import Callable


# ─── Public error ─────────────────────────────────────────────────────────────

class RichTextError(ValueError):
    """The rich text operation was rejected."""

    def __init__(self, code: str, message: str | None = None):
        self.code = code
        super().__init__(f"{code}: {message}" if message else code)


# ─── Data model ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Run:
    """A contiguous run of text with uniform formatting."""
    text: str
    bold: bool = False
    italic: bool = False
    underline: bool = False
    # Stored document formatting, never the editor's display zoom. None inherits.
    font_family: str | None = None
    font_size_pt: int | None = None

    def __post_init__(self):
        if not isinstance(self.text, str):
            raise RichTextError("invalid_type", "Run.text must be a str")
        if any(ord(c) < 32 and c not in "\t\n\r" for c in self.text):
            raise RichTextError("control_character", "Run.text contains control characters")
        if self.font_family is not None and (
            type(self.font_family) is not str
            or self.font_family not in ("serif", "sans-serif", "monospace")
        ):
            raise RichTextError("invalid_font_family")
        if self.font_size_pt is not None and (
            type(self.font_size_pt) is not int or not 8 <= self.font_size_pt <= 72
        ):
            raise RichTextError("invalid_font_size")


@dataclass(frozen=True)
class Node:
    """A document node: paragraph, heading, or list item."""
    id: str
    kind: str  # "paragraph" | "heading" | "list_item"
    runs: tuple[Run, ...]
    level: int = 0  # heading level 1..3
    text_align: str | None = None
    line_spacing: float | None = None  # Unitless line-height multiplier.

    def __post_init__(self):
        if self.text_align is not None and (
            type(self.text_align) is not str
            or self.text_align not in ("left", "center", "right", "justify")
        ):
            raise RichTextError("invalid_text_align")
        if self.line_spacing is not None and (
            type(self.line_spacing) not in (int, float)
            or self.line_spacing not in (1, 1.15, 1.5, 2, 2.5, 3)
        ):
            raise RichTextError("invalid_line_spacing")
        if not isinstance(self.id, str) or self.id == "":
            raise RichTextError("invalid_id", "Node.id must be a non-empty string")
        if self.kind not in ("paragraph", "heading", "list_item"):
            raise RichTextError("invalid_kind", f"Node.kind must be paragraph|heading|list_item, got {self.kind!r}")
        if self.kind == "heading":
            if not isinstance(self.level, int) or not (1 <= self.level <= 3):
                raise RichTextError("invalid_heading_level", "Heading level must be 1, 2, or 3")
        else:
            if self.level != 0:
                raise RichTextError("invalid_level", "Only heading nodes may have a non-zero level")
        if not isinstance(self.runs, tuple):
            raise RichTextError("invalid_type", "Node.runs must be a tuple")
        if len(self.runs) == 0:
            raise RichTextError("empty_runs", "Node must have at least one run")
        for run in self.runs:
            if not isinstance(run, Run):
                raise RichTextError("invalid_type", "Node.runs must contain only Run instances")


@dataclass(frozen=True)
class Document:
    """A canonical rich-text document."""
    nodes: tuple[Node, ...]

    def __post_init__(self):
        if not isinstance(self.nodes, tuple):
            raise RichTextError("invalid_type", "Document.nodes must be a tuple")
        seen = set()
        for node in self.nodes:
            if not isinstance(node, Node):
                raise RichTextError("invalid_type", "Document.nodes must contain only Node instances")
            if node.id in seen:
                raise RichTextError("duplicate_id", f"Duplicate node id: {node.id}")
            seen.add(node.id)


# ─── Plain text conversion ────────────────────────────────────────────────────

def from_plain_text(text: str, id_factory: Callable[[], str]) -> Document:
    """Create a document from plain text: one paragraph per line."""
    if not isinstance(text, str):
        raise RichTextError("invalid_type", "text must be a str")
    if not callable(id_factory):
        raise RichTextError("invalid_type", "id_factory must be callable")

    lines = text.split("\n")
    nodes = []
    for line in lines:
        run = Run(text=line)
        node = Node(id=id_factory(), kind="paragraph", runs=(run,))
        nodes.append(node)
    return Document(nodes=tuple(nodes))


def to_plain_text(doc: Document) -> str:
    """Serialise document to plain text (no markup)."""
    if not isinstance(doc, Document):
        raise RichTextError("invalid_type", "doc must be a Document")
    lines = []
    for node in doc.nodes:
        text = "".join(run.text for run in node.runs)
        lines.append(text)
    return "\n".join(lines)


# ─── HTML conversion ──────────────────────────────────────────────────────────

_ALLOWED_TAGS = {
    "p", "h1", "h2", "h3", "ul", "li", "strong", "em", "u", "br", "b", "i"
}
# Tags to drop entirely (but keep their text content)
_DROP_TAGS = {"script", "style", "iframe", "object"}
# Tags that map to run formatting
_BOLD_TAGS = {"b", "strong"}
_ITALIC_TAGS = {"i", "em"}
_UNDERLINE_TAGS = {"u"}
# Self-closing tags we allow
_VOID_TAGS = {"br"}


def _escape(text: str) -> str:
    return html.escape(text, quote=True)


def to_html(doc: Document) -> str:
    """Serialise document to sanitised HTML.

    Only emits <p>, <h1>..<h3>, <ul>, <li>, <strong>, <em>, <u>, <br>.
    All text is HTML-escaped. No attributes at all.
    """
    if not isinstance(doc, Document):
        raise RichTextError("invalid_type", "doc must be a Document")

    parts = []
    in_list = False

    for node in doc.nodes:
        if node.kind == "list_item":
            if not in_list:
                parts.append("<ul>")
                in_list = True
            parts.append("<li>")
            parts.append(_runs_to_html(node.runs))
            parts.append("</li>")
        else:
            if in_list:
                parts.append("</ul>")
                in_list = False
            if node.kind == "heading":
                tag = f"h{node.level}"
                parts.append(f"<{tag}>")
                parts.append(_runs_to_html(node.runs))
                parts.append(f"</{tag}>")
            else:  # paragraph
                parts.append("<p>")
                parts.append(_runs_to_html(node.runs))
                parts.append("</p>")

    if in_list:
        parts.append("</ul>")

    return "".join(parts)


def _runs_to_html(runs: tuple[Run, ...]) -> str:
    parts = []
    for run in runs:
        text = _escape(run.text).replace("\n", "<br>")
        if run.bold:
            text = f"<strong>{text}</strong>"
        if run.italic:
            text = f"<em>{text}</em>"
        if run.underline:
            text = f"<u>{text}</u>"
        parts.append(text)
    return "".join(parts)


# ─── HTML parsing (sanitising) ────────────────────────────────────────────────

# Regex to find tags and text
_TAG_RE = re.compile(r"(<[^>]+>)|([^<]+)")

# javascript: URL detection
_JS_URL_RE = re.compile(r"javascript\s*:", re.IGNORECASE)

# Event handler attributes
_EVENT_ATTR_RE = re.compile(r"\s+on[a-zA-Z]+\s*=", re.IGNORECASE)

# style attribute
_STYLE_ATTR_RE = re.compile(r"\s+style\s*=", re.IGNORECASE)


def from_html(html_text: str, id_factory: Callable[[], str]) -> Document:
    """Parse sanitised HTML into a Document.

    Drops <script>, <style>, <iframe>, <object>, event handler attributes,
    style attributes, javascript: URLs and every unknown tag (keeping its text).
    Maps <b>/<strong>, <i>/<em>, <u> to run flags.
    """
    if not isinstance(html_text, str):
        raise RichTextError("invalid_type", "html must be a str")
    if not callable(id_factory):
        raise RichTextError("invalid_type", "id_factory must be callable")

    # First pass: strip dangerous content
    sanitised = _sanitise_html(html_text)

    # Second pass: parse into nodes
    nodes = _parse_html(sanitised, id_factory)
    return Document(nodes=tuple(nodes))


def _sanitise_html(html_text: str) -> str:
    """Remove dangerous tags and attributes, keep text of dropped tags."""
    # Remove script/style/iframe/object entirely (including content)
    for tag in _DROP_TAGS:
        pattern = re.compile(f"<{tag}[^>]*>.*?</{tag}>", re.IGNORECASE | re.DOTALL)
        html_text = pattern.sub("", html_text)

    # Remove javascript: URLs from any attribute
    html_text = _JS_URL_RE.sub("removed:", html_text)

    # Remove event handler attributes
    html_text = _EVENT_ATTR_RE.sub("", html_text)

    # Remove style attributes
    html_text = _STYLE_ATTR_RE.sub("", html_text)

    # Process tags: keep allowed, drop unknown (but keep content)
    def replace_tag(match):
        tag = match.group(1)
        # Check if it's a closing tag
        if tag.startswith("</"):
            tag_name = tag[2:-1].split()[0].lower()
            if tag_name in _ALLOWED_TAGS:
                return tag
            return ""
        # Opening or self-closing tag
        tag_content = tag[1:-1].strip()
        if tag_content.endswith("/"):
            tag_content = tag_content[:-1].strip()
        tag_name = tag_content.split()[0].lower() if tag_content else ""
        if tag_name in _ALLOWED_TAGS:
            # Strip all attributes from allowed tags
            clean_tag = f"<{tag_name}>"
            if tag.endswith("/>"):
                clean_tag = f"<{tag_name}/>"
            return clean_tag
        # Unknown tag: drop it entirely
        return ""

    html_text = _TAG_RE.sub(lambda m: replace_tag(m) if m.group(1) else _escape(m.group(2)), html_text)

    return html_text


def _parse_html(html_text: str, id_factory: Callable[[], str]) -> list[Node]:
    """Parse sanitised HTML into nodes."""
    nodes = []
    current_list_items = []

    # Simple state machine parser
    i = 0
    while i < len(html_text):
        if html_text[i] == "<":
            j = html_text.find(">", i)
            if j == -1:
                break
            tag = html_text[i:j+1]
            i = j + 1

            if tag in ("<p>", "<h1>", "<h2>", "<h3>", "<li>"):
                # Start of a node
                tag_name = tag[1:-1]
                level = int(tag_name[1]) if tag_name.startswith("h") else 0
                kind = "heading" if level else ("list_item" if tag_name == "li" else "paragraph")

                # Find closing tag
                close_tag = f"</{tag_name}>"
                k = html_text.find(close_tag, i)
                if k == -1:
                    content = html_text[i:]
                    i = len(html_text)
                else:
                    content = html_text[i:k]
                    i = k + len(close_tag)

                runs = _parse_runs(content)
                node = Node(id=id_factory(), kind=kind, runs=tuple(runs), level=level)

                if kind == "list_item":
                    current_list_items.append(node)
                else:
                    if current_list_items:
                        nodes.extend(current_list_items)
                        current_list_items = []
                    nodes.append(node)

            elif tag in ("</ul>",):
                if current_list_items:
                    nodes.extend(current_list_items)
                    current_list_items = []
            # Ignore other closing tags
        else:
            # Text outside tags - treat as paragraph
            j = html_text.find("<", i)
            if j == -1:
                text = html_text[i:]
                i = len(html_text)
            else:
                text = html_text[i:j]
                i = j
            if text.strip():
                run = Run(text=html.unescape(text))
                node = Node(id=id_factory(), kind="paragraph", runs=(run,))
                if current_list_items:
                    nodes.extend(current_list_items)
                    current_list_items = []
                nodes.append(node)

    if current_list_items:
        nodes.extend(current_list_items)

    return nodes


def _parse_runs(html_fragment: str) -> list[Run]:
    """Parse HTML fragment into runs with formatting."""
    runs = []
    # Stack of active formatting tags
    bold = False
    italic = False
    underline = False

    i = 0
    while i < len(html_fragment):
        if html_fragment[i] == "<":
            j = html_fragment.find(">", i)
            if j == -1:
                break
            tag = html_fragment[i:j+1].lower()
            i = j + 1

            if tag in ("<strong>", "<b>"):
                bold = True
            elif tag in ("</strong>", "</b>"):
                bold = False
            elif tag in ("<em>", "<i>"):
                italic = True
            elif tag in ("</em>", "</i>"):
                italic = False
            elif tag == "<u>":
                underline = True
            elif tag == "</u>":
                underline = False
            elif tag == "<br>" or tag == "<br/>":
                runs.append(Run(text="\n", bold=bold, italic=italic, underline=underline))
            # Ignore other tags
        else:
            j = html_fragment.find("<", i)
            if j == -1:
                text = html_fragment[i:]
                i = len(html_fragment)
            else:
                text = html_fragment[i:j]
                i = j
            if text:
                runs.append(Run(text=html.unescape(text), bold=bold, italic=italic, underline=underline))

    # Merge adjacent runs with same formatting
    if not runs:
        return [Run(text="")]
    merged = [runs[0]]
    for run in runs[1:]:
        last = merged[-1]
        if (last.bold == run.bold and last.italic == run.italic
                and last.underline == run.underline):
            merged[-1] = Run(text=last.text + run.text, bold=last.bold,
                             italic=last.italic, underline=last.underline)
        else:
            merged.append(run)
    return merged


# ─── Clipboard flavours ───────────────────────────────────────────────────────

def to_clipboard_flavors(doc: Document) -> dict[str, str]:
    """Return both clipboard flavours: text/plain and text/html."""
    if not isinstance(doc, Document):
        raise RichTextError("invalid_type", "doc must be a Document")
    return {
        "text/plain": to_plain_text(doc),
        "text/html": to_html(doc),
    }


# ─── Text replacement ─────────────────────────────────────────────────────────

def replace_text(doc: Document, node_id: str, new_text: str) -> Document:
    """Replace text of a node, keeping its ID and first run's formatting."""
    if not isinstance(doc, Document):
        raise RichTextError("invalid_type", "doc must be a Document")
    if not isinstance(node_id, str):
        raise RichTextError("invalid_type", "node_id must be a str")
    if not isinstance(new_text, str):
        raise RichTextError("invalid_type", "new_text must be a str")
    if any(ord(c) < 32 and c not in "\t\n\r" for c in new_text):
        raise RichTextError("control_character", "new_text contains control characters")

    new_nodes = []
    found = False
    for node in doc.nodes:
        if node.id == node_id:
            found = True
            if not node.runs:
                raise RichTextError("empty_runs", "Node has no runs")
            first_run = node.runs[0]
            new_run = Run(text=new_text, bold=first_run.bold,
                          italic=first_run.italic, underline=first_run.underline,
                          font_family=first_run.font_family, font_size_pt=first_run.font_size_pt)
            new_node = replace(node, runs=(new_run,))
            new_nodes.append(new_node)
        else:
            new_nodes.append(node)

    if not found:
        raise RichTextError("unknown_node", f"Node not found: {node_id}")

    return Document(nodes=tuple(new_nodes))


# ─── Canonical JSON & content hash ────────────────────────────────────────────

def canonical_json(doc: Document) -> str:
    """Stable JSON serialisation with sorted keys, ensure_ascii=False."""
    if not isinstance(doc, Document):
        raise RichTextError("invalid_type", "doc must be a Document")

    def node_to_dict(node: Node) -> dict:
        return {
            "id": node.id,
            "kind": node.kind,
            "level": node.level,
            **{key: value for key, value in (
                ("text_align", node.text_align), ("line_spacing", node.line_spacing),
            ) if value is not None},
            "runs": [
                {"text": run.text, "bold": run.bold, "italic": run.italic, "underline": run.underline,
                 **{key: value for key, value in (
                     ("font_family", run.font_family), ("font_size_pt", run.font_size_pt),
                 ) if value is not None}}
                for run in node.runs
            ],
        }

    data = {"nodes": [node_to_dict(n) for n in doc.nodes]}
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash(doc: Document) -> str:
    """SHA-256 of canonical_json (ids are part of the structure)."""
    if not isinstance(doc, Document):
        raise RichTextError("invalid_type", "doc must be a Document")
    return hashlib.sha256(canonical_json(doc).encode("utf-8")).hexdigest()
