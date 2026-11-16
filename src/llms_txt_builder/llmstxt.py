"""The llms.txt document model, a tolerant parser and the renderer.

Format (https://llmstxt.org/), in this order:

1. an H1 with the name of the project or site (the only required part);
2. a blockquote with a short summary;
3. zero or more Markdown sections of any type except headings (details and notes);
4. zero or more H2 sections, each a list of links ``- [name](url)``, optionally followed by ``: notes``.

An H2 named ``Optional`` holds secondary links that a reader can skip when a shorter context is needed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

OPTIONAL = "Optional"

LINK_ITEM = re.compile(
    r"^(?P<indent>\s*)[-*+]\s+\[(?P<title>(?:[^\]\\]|\\.)*)\]\((?P<url><[^>]*>|[^)\s]*)(?:\s+\"[^\"]*\")?\)(?P<rest>.*)$"
)
LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
HEADING = re.compile(r"^(?P<hashes>#{1,6})(?:\s+(?P<text>.*?))?\s*#*\s*$")


@dataclass
class LinkItem:
    title: str
    url: str
    description: str = ""
    line: int = 0
    # Whatever followed the closing parenthesis in the source file (used by the validator).
    rest: str = field(default="", compare=False, repr=False)

    def render(self) -> str:
        title = self.title.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
        url = self.url.replace(" ", "%20").replace("(", "%28").replace(")", "%29")
        out = f"- [{title}]({url})"
        description = " ".join(self.description.split())
        if description:
            out += f": {description}"
        return out


@dataclass
class Section:
    name: str
    items: list[LinkItem] = field(default_factory=list)
    line: int = 0
    # Lines in the section that are not link items: (line number, text)
    other: list[tuple[int, str]] = field(default_factory=list)

    @property
    def is_optional(self) -> bool:
        return self.name.strip().lower() == OPTIONAL.lower()


@dataclass
class Document:
    title: str = ""
    summary: str = ""
    details: str = ""
    sections: list[Section] = field(default_factory=list)
    title_line: int = 0
    summary_line: int = 0

    def section(self, name: str) -> Section | None:
        return next((s for s in self.sections if s.name == name), None)

    def all_links(self) -> list[tuple[Section, LinkItem]]:
        return [(s, item) for s in self.sections for item in s.items]

    def render(self) -> str:
        parts = [f"# {self.title.strip()}"]
        if self.summary.strip():
            summary = " ".join(self.summary.split())
            parts.append(f"> {summary}")
        if self.details.strip():
            parts.append(self.details.strip())
        for section in self.sections:
            if not section.items:
                continue
            body = "\n".join(item.render() for item in section.items)
            parts.append(f"## {section.name.strip()}\n\n{body}")
        return "\n\n".join(parts) + "\n"


def unescape(text: str) -> str:
    return re.sub(r"\\(.)", r"\1", text)


def parse_link_line(line: str) -> LinkItem | None:
    match = LINK_ITEM.match(line)
    if not match:
        return None
    rest = match.group("rest").strip()
    description = rest[1:].strip() if rest.startswith(":") else ""
    url = match.group("url")
    if url.startswith("<") and url.endswith(">"):
        url = url[1:-1]
    return LinkItem(unescape(match.group("title")).strip(), url.strip(), description, rest=rest)


def parse(text: str) -> Document:
    """Parse llms.txt text. The parser never fails; use :mod:`llms_txt_builder.validate` to find problems."""
    doc = Document()
    text = text.lstrip("﻿")
    lines = text.splitlines()
    in_fence = False
    detail_lines: list[str] = []
    current: Section | None = None
    summary_lines: list[str] = []
    for i, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            in_fence = not in_fence
        heading = None if in_fence else HEADING.match(stripped)
        if heading and len(heading.group("hashes")) == 1 and not doc.title_line:
            doc.title = (heading.group("text") or "").strip()
            doc.title_line = i
            continue
        if heading and len(heading.group("hashes")) == 2:
            current = Section((heading.group("text") or "").strip(), line=i)
            doc.sections.append(current)
            continue
        if current is None:
            if not doc.title_line:
                continue
            if stripped.startswith(">") and not doc.summary_line and not detail_lines:
                doc.summary_line = i
            if doc.summary_line and not detail_lines and stripped.startswith(">"):
                summary_lines.append(stripped[1:].strip())
                continue
            if stripped or detail_lines:
                detail_lines.append(line)
            continue
        if not stripped:
            continue
        item = parse_link_line(line)
        if item is not None:
            item.line = i
            current.items.append(item)
        else:
            current.other.append((i, line))
    doc.summary = " ".join(s for s in summary_lines if s)
    doc.details = "\n".join(detail_lines).strip()
    return doc
