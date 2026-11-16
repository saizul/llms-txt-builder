"""Page metadata and main-content extraction.

The main-content heuristic is a small readability-style scorer:

1. remove elements that are never content (scripts, navigation, forms, hidden elements, and blocks whose
   ``class``/``id`` look like sidebars, comments, share bars, cookie banners and similar boilerplate);
2. if the page has exactly one ``<main>`` / ``[role=main]`` / ``<article>`` with enough text, use it;
3. otherwise score every paragraph-like block by its length and comma count, credit its parent fully and its
   grandparent half, adjust by class/id hints and penalise by link density; the best scoring container wins;
4. fall back to ``<body>``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from llms_txt_builder.dom import Node, parse_html
from llms_txt_builder.markdown import MarkdownOptions, html_to_markdown
from llms_txt_builder.urls import absolutize

_NEGATIVE = re.compile(
    r"(?:^|[-_\s])(?:ad|ads|advert|advertisement|banner|breadcrumbs?|comment|comments|cookie|consent|disqus|"
    r"footer|foot|masthead|menu|modal|nav|navbar|navigation|newsletter|outbrain|pager|pagination|popup|promo|"
    r"related|share|sharing|sidebar|skip|social|sponsor|subscribe|taboola|tags|toc|toolbar|widget)(?:$|[-_\s])",
    re.I,
)
_POSITIVE = re.compile(r"article|body|content|entry|main|page|post|story|text|blog", re.I)
_ALWAYS_REMOVE = frozenset(
    {
        "script",
        "style",
        "noscript",
        "template",
        "svg",
        "canvas",
        "iframe",
        "form",
        "button",
        "input",
        "select",
        "textarea",
        "nav",
        "aside",
        "dialog",
        "object",
        "embed",
    }
)
_PROTECTED = frozenset({"html", "body", "main", "article", "#document"})
_HIDDEN_STYLE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.I)
_PARAGRAPH_TAGS = frozenset({"p", "pre", "td", "blockquote", "li", "dd"})
_CONTAINER_TAGS = frozenset({"div", "section", "article", "main", "td", "body", "blockquote"})
_WS = re.compile(r"\s+")


@dataclass
class PageInfo:
    url: str
    title: str = ""
    description: str = ""
    h1: str = ""
    lang: str = ""
    canonical: str = ""
    site_name: str = ""
    noindex: bool = False
    markdown_url: str = ""
    links: list[str] = field(default_factory=list)
    content_markdown: str = ""
    content_text: str = ""


def clean(text: str) -> str:
    return _WS.sub(" ", text or "").strip()


def _meta(doc: Node) -> dict[str, str]:
    out: dict[str, str] = {}
    for meta in doc.iter("meta"):
        key = (meta.get("name") or meta.get("property") or meta.get("itemprop")).lower()
        if key and key not in out:
            out[key] = clean(meta.get("content"))
    return out


def extract_links(doc: Node, base_url: str) -> list[str]:
    seen: set[str] = set()
    links: list[str] = []
    for a in doc.iter("a"):
        rel = a.get("rel").lower().split()
        if "nofollow" in rel:
            continue
        url = absolutize(base_url, a.get("href"))
        if url:
            url = url.split("#", 1)[0]
            if url not in seen:
                seen.add(url)
                links.append(url)
    return links


def _base_href(doc: Node, url: str) -> str:
    base = doc.find("base")
    if base is not None and base.get("href"):
        return absolutize(url, base.get("href")) or url
    return url


def _is_hidden(node: Node) -> bool:
    if "hidden" in node.attrs or node.get("aria-hidden").lower() == "true":
        return True
    return bool(_HIDDEN_STYLE.search(node.get("style")))


def _looks_like_boilerplate(node: Node) -> bool:
    if node.tag in _PROTECTED:
        return False
    if node.get("role").lower() in ("navigation", "banner", "contentinfo", "complementary", "search", "dialog"):
        return True
    hint = f"{node.get('class')} {node.get('id')}"
    if not hint.strip():
        return False
    return bool(_NEGATIVE.search(hint)) and not _POSITIVE.search(hint)


def _prune(root: Node, *, inside_main: bool = False) -> None:
    for child in list(root.children):
        if child.tag is None:
            continue
        tag = child.tag
        remove = tag in _ALWAYS_REMOVE or _is_hidden(child)
        if not remove and tag in ("header", "footer") and not inside_main:
            remove = True
        if not remove and _looks_like_boilerplate(child):
            remove = True
        if remove:
            child.remove()
            continue
        _prune(child, inside_main=inside_main or tag in ("main", "article"))


def _text_len(node: Node) -> int:
    return len(clean(node.text_content()))


def _link_density(node: Node) -> float:
    total = _text_len(node)
    if not total:
        return 0.0
    linked = sum(len(clean(a.text_content())) for a in node.iter("a"))
    return min(1.0, linked / total)


def _class_weight(node: Node) -> float:
    hint = f"{node.get('class')} {node.get('id')}"
    weight = 0.0
    if _POSITIVE.search(hint):
        weight += 25
    if _NEGATIVE.search(hint):
        weight -= 25
    return weight


def _explicit_main(body: Node) -> Node | None:
    for selector in ("main", "role", "article"):
        if selector == "role":
            nodes = [n for n in body.iter() if n.get("role").lower() == "main"]
        else:
            nodes = body.find_all(selector)
        nodes = [n for n in nodes if _text_len(n) >= 140]
        if len(nodes) == 1:
            return nodes[0]
        if selector == "article" and len(nodes) > 1:
            # Several articles (a listing page): use their common container if it is the main area.
            return None
    return None


def find_main_content(body: Node) -> Node:
    explicit = _explicit_main(body)
    if explicit is not None:
        return explicit
    scores: dict[int, tuple[Node, float]] = {}
    for block in body.iter():
        if block.tag not in _PARAGRAPH_TAGS:
            continue
        text = clean(block.text_content())
        if len(text) < 25:
            continue
        score = 1 + text.count(",") + text.count("、") + min(len(text) / 100, 3)
        parent = block.parent
        for share in (1.0, 0.5):
            if parent is None or parent.tag in ("#document", "html"):
                break
            if parent.tag in _CONTAINER_TAGS or parent.tag in ("ul", "ol", "table", "tbody", "tr"):
                key = id(parent)
                base = scores.get(key, (parent, _class_weight(parent)))[1]
                scores[key] = (parent, base + score * share)
            parent = parent.parent
    if not scores:
        return body
    best_node, best_score = body, 0.0
    for node, score in scores.values():
        final = score * (1 - _link_density(node))
        if final > best_score:
            best_node, best_score = node, final
    # Lists and table internals are rarely the whole article; climb to a real container.
    while best_node.tag in ("ul", "ol", "tbody", "tr", "table") and best_node.parent is not None:
        best_node = best_node.parent
    return best_node


def site_name_from_title(title: str) -> str:
    """``"Pricing | Example Co"`` -> ``"Example Co"``; empty when the title has no separator."""
    for sep in (" | ", " – ", " — ", " - ", " · ", " :: "):
        if sep in title:
            return title.rsplit(sep, 1)[1].strip()
    return ""


def strip_site_name(title: str, site_name: str) -> str:
    """Remove a trailing or leading ``" | Site"`` from a page title."""
    if not site_name:
        return title
    for sep in (" | ", " – ", " — ", " - ", " · ", " :: "):
        suffix = f"{sep}{site_name}"
        if title.endswith(suffix) and len(title) > len(suffix):
            return title[: -len(suffix)].strip()
        prefix = f"{site_name}{sep}"
        if title.startswith(prefix) and len(title) > len(prefix):
            return title[len(prefix) :].strip()
    return title


def extract_page(html: str, url: str, *, images: bool = True) -> PageInfo:
    """Extract metadata and the main content (as Markdown) from an HTML document."""
    doc = parse_html(html)
    base = _base_href(doc, url)
    meta = _meta(doc)
    title_node = doc.find("title")
    title = clean(title_node.text_content()) if title_node is not None else ""
    if not title:
        title = meta.get("og:title", "")
    html_node = doc.find("html")
    info = PageInfo(url=url, title=title)
    info.lang = html_node.get("lang") if html_node is not None else ""
    info.description = meta.get("description") or meta.get("og:description") or meta.get("twitter:description", "")
    info.site_name = meta.get("og:site_name", "") or meta.get("application-name", "")
    robots = f"{meta.get('robots', '')},{meta.get('googlebot', '')}".lower()
    info.noindex = "noindex" in robots or "none" in [p.strip() for p in robots.split(",")]
    for link in doc.iter("link"):
        rel = link.get("rel").lower().split()
        href = link.get("href")
        if not href:
            continue
        if "canonical" in rel and not info.canonical:
            info.canonical = absolutize(base, href) or ""
        if "alternate" in rel and link.get("type").lower() == "text/markdown" and not info.markdown_url:
            info.markdown_url = absolutize(base, href) or ""
    body = doc.find("body") or doc
    first_h1 = body.find("h1")
    info.h1 = clean(first_h1.text_content()) if first_h1 is not None else ""
    info.links = extract_links(body, base)

    _prune(body)
    main = find_main_content(body)
    markdown = html_to_markdown(main, MarkdownOptions(base_url=base, images=images))
    info.content_markdown = markdown
    info.content_text = prose_text(markdown)
    return info


_MD_LINK = re.compile(r"!?\[((?:[^\]\\]|\\.)*)\]\([^)]*\)")
_MD_MARKS = re.compile(r"(\*\*|~~|(?<![A-Za-z0-9\\])[*_]|[*_](?![A-Za-z0-9])|`)")


def prose_text(markdown: str) -> str:
    """Plain text of the prose paragraphs of ``markdown`` (headings, code, tables, lists and quotes skipped)."""
    out = []
    in_fence = False
    for block in markdown.split("\n\n"):
        stripped = block.strip()
        fences = sum(1 for line in stripped.splitlines() if line.lstrip().startswith(("```", "~~~")))
        skip = in_fence or stripped.startswith(("```", "~~~"))
        if fences % 2:
            in_fence = not in_fence
        if skip:
            continue
        if not stripped or stripped.startswith(("#", "|", "- ", ">", "![", "---")) or re.match(r"\d+\. ", stripped):
            continue
        text = _MD_LINK.sub(lambda m: m.group(1), stripped)
        text = _MD_MARKS.sub("", text)
        text = re.sub(r"\\(.)", r"\1", text)
        out.append(clean(text))
    return " ".join(t for t in out if t)


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def summarize_text(text: str, max_length: int = 160) -> str:
    """First sentence(s) of ``text`` that fit into ``max_length`` characters, cut at a word boundary."""
    text = clean(text)
    if len(text) <= max_length:
        return text
    out = ""
    for sentence in _SENTENCE_END.split(text):
        candidate = f"{out} {sentence}".strip()
        if len(candidate) > max_length:
            break
        out = candidate
    if out:
        return out
    cut = text[: max_length - 1].rsplit(" ", 1)[0].rstrip(",;:-")
    return cut + "…"
