"""Convert an HTML subtree into clean CommonMark / GitHub-flavoured Markdown."""

from __future__ import annotations

import re
from dataclasses import dataclass

from llms_txt_builder.dom import Node
from llms_txt_builder.urls import absolutize

BLOCK_TAGS = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "body",
        "dd",
        "details",
        "dialog",
        "div",
        "dl",
        "dt",
        "fieldset",
        "figcaption",
        "figure",
        "footer",
        "form",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "hgroup",
        "hr",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "summary",
        "table",
        "ul",
        "#document",
        "html",
    }
)
SKIP_TAGS = frozenset(
    {
        "script",
        "style",
        "noscript",
        "template",
        "head",
        "svg",
        "canvas",
        "iframe",
        "object",
        "embed",
        "button",
        "input",
        "select",
        "textarea",
        "label",
        "video",
        "audio",
        "map",
        "math",
    }
)
_WS = re.compile(r"[ \t\r\n\f\u00a0]+")
_ESCAPE_ALWAYS = re.compile(r"([\\`\[\]<])")
_ESCAPE_EMPHASIS = re.compile(r"(?<![A-Za-z0-9])([*_])|([*_])(?![A-Za-z0-9])")
_LINE_START = re.compile(r"^(\s*)([#>+-]|\d+[.)])(?=\s)")


@dataclass
class MarkdownOptions:
    base_url: str = ""
    images: bool = True
    heading_offset: int = 0
    max_heading: int = 6


def escape_text(text: str) -> str:
    text = _ESCAPE_ALWAYS.sub(r"\\\1", text)
    return _ESCAPE_EMPHASIS.sub(lambda m: "\\" + (m.group(1) or m.group(2)), text)


def _escape_line_start(text: str) -> str:
    def fix(m: re.Match[str]) -> str:
        marker = m.group(2)
        if marker[0].isdigit():
            return m.group(1) + marker[:-1] + "\\" + marker[-1]  # "1." -> "1\."
        return m.group(1) + "\\" + marker

    return _LINE_START.sub(fix, text)


class MarkdownConverter:
    def __init__(self, options: MarkdownOptions | None = None) -> None:
        self.options = options or MarkdownOptions()

    # ---- public -----------------------------------------------------------------------------------------------
    def convert(self, node: Node) -> str:
        blocks = self._blocks(node)
        text = "\n\n".join(b for b in blocks if b.strip())
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip() + "\n" if text.strip() else ""

    # ---- block level --------------------------------------------------------------------------------------------
    def _blocks(self, node: Node) -> list[str]:
        """Render the children of a block container as a list of Markdown blocks."""
        blocks: list[str] = []
        inline: list[Node] = []

        def flush() -> None:
            if inline:
                text = self._inline_nodes(inline).strip()
                if text:
                    blocks.append(_escape_line_start(text))
                inline.clear()

        for child in node.children:
            if child.tag is not None and child.tag in SKIP_TAGS:
                continue
            if child.tag is not None and child.tag in BLOCK_TAGS:
                flush()
                blocks.extend(self._block(child))
            else:
                inline.append(child)
        flush()
        return blocks

    def _block(self, node: Node) -> list[str]:
        tag = node.tag or ""
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = min(self.options.max_heading, int(tag[1]) + self.options.heading_offset)
            text = self._inline_nodes(node.children).strip()
            return [f"{'#' * max(1, level)} {text}"] if text else []
        if tag == "p":
            text = self._inline_nodes(node.children).strip()
            return [_escape_line_start(text)] if text else []
        if tag == "hr":
            return ["---"]
        if tag == "pre":
            return [self._pre(node)]
        if tag in ("ul", "ol"):
            rendered = self._list(node)
            return [rendered] if rendered else []
        if tag == "blockquote":
            inner = "\n\n".join(self._blocks(node))
            if not inner.strip():
                return []
            return ["\n".join(f"> {line}" if line else ">" for line in inner.splitlines())]
        if tag == "table":
            return [self._table(node)]
        if tag == "dl":
            return self._definition_list(node)
        if tag == "figure":
            return self._blocks(node)
        if tag == "details":
            return self._blocks(node)
        if tag == "summary":
            text = self._inline_nodes(node.children).strip()
            return [f"**{text}**"] if text else []
        return self._blocks(node)

    def _pre(self, node: Node) -> str:
        code = node.find("code")
        language = ""
        for candidate in (code, node):
            if candidate is None:
                continue
            for cls in candidate.classes:
                if cls.startswith(("language-", "lang-")):
                    language = cls.split("-", 1)[1]
                    break
            if language:
                break
        body = node.text_content().strip("\n")
        fence = "```"
        while fence in body:
            fence += "`"
        return f"{fence}{language}\n{body}\n{fence}"

    def _list(self, node: Node, depth: int = 0) -> str:
        ordered = node.tag == "ol"
        try:
            number = int(node.get("start", "1") or 1)
        except ValueError:
            number = 1
        lines: list[str] = []
        for child in node.children:
            if child.tag != "li":
                continue
            marker = f"{number}." if ordered else "-"
            number += 1
            indent = " " * (len(marker) + 1)
            parts: list[str] = []
            inline: list[Node] = []

            def flush(inline: list[Node] = inline, parts: list[str] = parts) -> None:
                if inline:
                    text = self._inline_nodes(inline).strip()
                    if text:
                        parts.append(text)
                    inline.clear()

            for sub in child.children:
                if sub.tag in ("ul", "ol"):
                    flush()
                    nested = self._list(sub, depth + 1)
                    if nested:
                        parts.append(nested)
                elif sub.tag is not None and sub.tag in BLOCK_TAGS:
                    flush()
                    parts.extend(self._block(sub))
                elif sub.tag is None or sub.tag not in SKIP_TAGS:
                    inline.append(sub)
            flush()
            if not parts:
                continue
            first, rest = parts[0], parts[1:]
            item = f"{marker} " + first.replace("\n", "\n" + indent)
            for extra in rest:
                item += "\n" + "\n".join(indent + line if line else "" for line in extra.splitlines())
            lines.append(item)
        return "\n".join(lines)

    def _table(self, node: Node) -> str:
        rows: list[list[str]] = []
        header_row = -1
        for tr in node.iter("tr"):
            # Skip rows of nested tables.
            parent = tr.parent
            while parent is not None and parent.tag != "table":
                parent = parent.parent
            if parent is not node:
                continue
            cells = [c for c in tr.children if c.tag in ("td", "th")]
            if not cells:
                continue
            if header_row == -1 and all(c.tag == "th" for c in cells):
                header_row = len(rows)
            rows.append([self._inline_nodes(c.children).strip().replace("|", "\\|").replace("\n", " ") for c in cells])
        if not rows:
            return ""
        width = max(len(r) for r in rows)
        rows = [r + [""] * (width - len(r)) for r in rows]
        if header_row > 0:
            rows.insert(0, rows.pop(header_row))
        header = rows[0]
        body = rows[1:]
        out = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
        out.extend("| " + " | ".join(r) + " |" for r in body)
        return "\n".join(out)

    def _definition_list(self, node: Node) -> list[str]:
        out: list[str] = []
        for child in node.children:
            text = self._inline_nodes(child.children).strip() if child.tag in ("dt", "dd") else ""
            if not text:
                continue
            out.append(f"**{text}**" if child.tag == "dt" else text)
        return out

    # ---- inline level --------------------------------------------------------------------------------------------
    def _inline_nodes(self, nodes: list[Node]) -> str:
        text = "".join(self._inline(n) for n in nodes)
        # Collapse whitespace but keep hard line breaks.
        lines = [_WS.sub(" ", part).strip() for part in text.split("\x00BR\x00")]
        return "  \n".join(line for line in lines if line) if len(lines) > 1 else lines[0]

    def _inline(self, node: Node) -> str:
        if node.tag is None:
            return escape_text(_WS.sub(" ", node.text))
        tag = node.tag
        if tag in SKIP_TAGS:
            return ""
        if tag == "br":
            return "\x00BR\x00"
        if tag == "img":
            if not self.options.images:
                return ""
            src = absolutize(self.options.base_url, node.get("src")) if node.get("src") else None
            if not src:
                return ""
            alt = escape_text(_WS.sub(" ", node.get("alt")).strip())
            return f"![{alt}]({_url(src)})"
        inner = "".join(self._inline(c) for c in node.children)
        if tag == "a":
            href = absolutize(self.options.base_url, node.get("href")) if node.get("href") else None
            label = _WS.sub(" ", inner).strip()
            if not href or not label:
                return inner
            return f"[{label}]({_url(href)})"
        if tag in ("strong", "b"):
            return _wrap(inner, "**")
        if tag in ("em", "i", "cite"):
            return _wrap(inner, "*")
        if tag in ("del", "s", "strike"):
            return _wrap(inner, "~~")
        if tag in ("code", "kbd", "samp", "tt"):
            raw = _WS.sub(" ", node.text_content())
            if not raw.strip():
                return ""
            fence = "``" if "`" in raw else "`"
            pad = " " if raw.startswith("`") or raw.endswith("`") else ""
            return f"{fence}{pad}{raw}{pad}{fence}"
        if tag in BLOCK_TAGS:
            # A block element inside inline content (invalid but common): separate it with spaces.
            return f" {inner} "
        return inner


def _wrap(inner: str, marker: str) -> str:
    stripped = inner.strip()
    if not stripped:
        return inner
    lead = inner[: len(inner) - len(inner.lstrip())]
    trail = inner[len(inner.rstrip()) :]
    return f"{lead}{marker}{stripped}{marker}{trail}"


def _url(url: str) -> str:
    return url.replace(" ", "%20").replace("(", "%28").replace(")", "%29")


def html_to_markdown(node: Node, options: MarkdownOptions | None = None) -> str:
    return MarkdownConverter(options).convert(node)
