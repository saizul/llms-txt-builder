"""A tolerant, dependency-free HTML tree builder on top of :mod:`html.parser`.

It is not a full HTML5 tree construction algorithm, but it handles what real pages need for content extraction:
void elements, implicitly closed ``<p>``, ``<li>``, ``<dt>``/``<dd>``, ``<tr>``/``<td>``/``<th>`` and ``<option>``,
stray end tags, and unclosed elements at the end of the document.
"""

from __future__ import annotations

from collections.abc import Iterator
from html.parser import HTMLParser

VOID_ELEMENTS = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
)

# Opening one of these closes an open <p>.
_CLOSES_P = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "details",
        "div",
        "dl",
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
        "hr",
        "main",
        "nav",
        "ol",
        "p",
        "pre",
        "section",
        "table",
        "ul",
    }
)
# tag -> (tags it implicitly closes, tags that stop the search)
_IMPLICIT = {
    "li": ({"li"}, {"ul", "ol", "menu"}),
    "dt": ({"dt", "dd"}, {"dl"}),
    "dd": ({"dt", "dd"}, {"dl"}),
    "tr": ({"tr", "td", "th"}, {"table", "thead", "tbody", "tfoot"}),
    "td": ({"td", "th"}, {"tr", "table"}),
    "th": ({"td", "th"}, {"tr", "table"}),
    "option": ({"option"}, {"select", "datalist"}),
    "thead": ({"tbody", "tfoot", "tr", "td", "th"}, {"table"}),
    "tbody": ({"thead", "tbody", "tfoot", "tr", "td", "th"}, {"table"}),
    "tfoot": ({"thead", "tbody", "tr", "td", "th"}, {"table"}),
}


class Node:
    """An element (``tag`` set) or a text node (``tag is None``)."""

    __slots__ = ("attrs", "children", "parent", "tag", "text")

    def __init__(self, tag: str | None, attrs: dict[str, str] | None = None, text: str = "") -> None:
        self.tag = tag
        self.attrs = attrs or {}
        self.children: list[Node] = []
        self.parent: Node | None = None
        self.text = text

    @property
    def is_text(self) -> bool:
        return self.tag is None

    def append(self, child: Node) -> None:
        child.parent = self
        self.children.append(child)

    def remove(self) -> None:
        if self.parent is not None:
            self.parent.children = [c for c in self.parent.children if c is not self]
            self.parent = None

    def get(self, name: str, default: str = "") -> str:
        return self.attrs.get(name, default)

    @property
    def classes(self) -> list[str]:
        return self.attrs.get("class", "").split()

    def iter(self, tag: str | None = None) -> Iterator[Node]:
        """Depth-first iteration over element descendants (including ``self``)."""
        stack = [self]
        while stack:
            node = stack.pop()
            if node.tag is not None and (tag is None or node.tag == tag):
                yield node
            stack.extend(reversed([c for c in node.children if c.tag is not None]))

    def find(self, tag: str) -> Node | None:
        return next(self.iter(tag), None)

    def find_all(self, tag: str) -> list[Node]:
        return list(self.iter(tag))

    def text_content(self) -> str:
        parts: list[str] = []
        stack = [self]
        while stack:
            node = stack.pop()
            if node.tag is None:
                parts.append(node.text)
            elif node.tag not in ("script", "style", "template", "noscript"):
                stack.extend(reversed(node.children))
        return "".join(parts)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        if self.tag is None:
            return f"Text({self.text[:30]!r})"
        return f"<{self.tag} {self.attrs}>"


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("#document")
        self.stack: list[Node] = [self.root]

    @property
    def current(self) -> Node:
        return self.stack[-1]

    def _close_until(self, tags: set[str], stop: set[str]) -> None:
        for i in range(len(self.stack) - 1, 0, -1):
            tag = self.stack[i].tag
            if tag in stop:
                return
            if tag in tags:
                del self.stack[i:]
                return

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in _CLOSES_P:
            self._close_until({"p"}, {"button", "table", "li", "td", "th", "div", "section", "article"})
        if tag in _IMPLICIT:
            closes, stop = _IMPLICIT[tag]
            self._close_until(set(closes), set(stop))
        node = Node(tag, {k.lower(): (v or "") for k, v in attrs})
        self.current.append(node)
        if tag not in VOID_ELEMENTS:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        node = Node(tag, {k.lower(): (v or "") for k, v in attrs})
        self.current.append(node)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in VOID_ELEMENTS:
            return
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return
        if tag == "p":
            # "</p>" without an open <p> creates an empty paragraph in HTML5; nothing to do for extraction.
            return

    def handle_data(self, data: str) -> None:
        if not data:
            return
        children = self.current.children
        if children and children[-1].tag is None:
            children[-1].text += data
        else:
            self.current.append(Node(None, text=data))


def parse_html(html: str) -> Node:
    """Parse ``html`` into a :class:`Node` tree rooted at a ``#document`` node."""
    builder = _TreeBuilder()
    builder.feed(html)
    builder.close()
    return builder.root
