"""Turn collected pages into an llms.txt document and an llms-full.txt file."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from llms_txt_builder.collect import Collection, Page
from llms_txt_builder.config import Config, SectionRule
from llms_txt_builder.extract import PageInfo, clean, site_name_from_title, strip_site_name, summarize_text
from llms_txt_builder.llmstxt import OPTIONAL, Document, LinkItem, Section
from llms_txt_builder.urls import host_of


@dataclass
class BuildResult:
    document: Document
    full_text: str
    # url -> section name for every page placed; pages dropped by the grouping rules are listed separately.
    placement: dict[str, str] = field(default_factory=dict)
    dropped: list[tuple[str, str]] = field(default_factory=list)

    @property
    def text(self) -> str:
        return self.document.render()


def _site_name(config: Config, collection: Collection) -> str:
    if config.site.name:
        return config.site.name
    home = collection.homepage
    if home is not None:
        if home.site_name:
            return home.site_name
        from_title = site_name_from_title(home.title)
        if from_title:
            return from_title
        if home.title:
            return home.title
    if collection.pages:
        names = [p.info.site_name for p in collection.pages if p.info.site_name]
        if names:
            return max(set(names), key=names.count)
        return host_of(collection.pages[0].url).removeprefix("www.")
    return "Untitled site"


def _summary(config: Config, collection: Collection) -> str:
    if config.site.summary:
        return config.site.summary
    home = collection.homepage
    if home is not None:
        if home.description:
            return home.description
        if home.content_text:
            return summarize_text(home.content_text, config.output.max_description_length * 2)
    return ""


def page_title(info: PageInfo, config: Config, site_name: str) -> str:
    source = config.output.title_source
    title = info.title
    if config.output.strip_site_name:
        title = strip_site_name(title, site_name)
        if info.site_name and info.site_name != site_name:
            title = strip_site_name(title, info.site_name)
    if source == "h1":
        chosen = info.h1 or title
    elif source == "title":
        chosen = title or info.h1
    else:
        # auto: the title without the site name, unless it is missing or just the site name itself.
        chosen = title if title and title != site_name else (info.h1 or title)
    chosen = clean(chosen)
    if not chosen:
        path = urlsplit(info.url).path.strip("/")
        chosen = path.rsplit("/", 1)[-1].replace("-", " ").replace("_", " ").capitalize() or "Home"
    return chosen


def page_description(info: PageInfo, config: Config) -> str:
    source = config.output.description_source
    limit = config.output.max_description_length
    if source == "none":
        return ""
    meta = clean(info.description)
    if source == "meta":
        return _limit(meta, limit)
    if source == "content":
        return summarize_text(info.content_text, limit)
    return _limit(meta, limit) if meta else summarize_text(info.content_text, limit)


def _limit(text: str, limit: int) -> str:
    return text if len(text) <= limit else summarize_text(text, limit)


def _sort(items: list[tuple[Page, LinkItem]], how: str) -> list[tuple[Page, LinkItem]]:
    if how == "url":
        return sorted(items, key=lambda pi: pi[1].url)
    if how == "title":
        return sorted(items, key=lambda pi: pi[1].title.lower())
    return sorted(items, key=lambda pi: pi[0].order)


def _rule_for(url: str, rules: list[SectionRule]) -> SectionRule | None:
    return next((r for r in rules if r.matches(url)), None)


def build(collection: Collection, config: Config) -> BuildResult:
    site_name = _site_name(config, collection)
    doc = Document(title=site_name, summary=_summary(config, collection))
    doc.details = "\n\n".join(n.strip() for n in config.site.notes if n.strip())
    result = BuildResult(document=doc, full_text="")

    rules = config.sections
    grouped: dict[str, list[tuple[Page, LinkItem]]] = {}
    order: list[str] = [r.name for r in rules]
    default = config.output.default_section.strip()
    seen_urls: set[str] = set()
    for page in collection.pages:
        if page.url in seen_urls:
            continue
        seen_urls.add(page.url)
        info = page.info
        url = info.markdown_url if config.output.link_to_markdown and info.markdown_url else page.url
        item = LinkItem(page_title(info, config, site_name), url, page_description(info, config))
        rule = _rule_for(page.url, rules)
        if rule is not None:
            name = OPTIONAL if rule.optional else rule.name
        elif default:
            name = default
        else:
            result.dropped.append((page.url, "no section matched and output.default_section is empty"))
            continue
        if name not in order:
            order.append(name)
        grouped.setdefault(name, []).append((page, item))
        result.placement[page.url] = name

    # Sorting and per-section limits; overflow can move to the Optional section.
    rule_by_name = {r.name: r for r in rules}
    overflow: list[tuple[Page, LinkItem]] = []
    final: dict[str, list[tuple[Page, LinkItem]]] = {}
    for name in order:
        items = grouped.get(name, [])
        rule = rule_by_name.get(name)
        items = _sort(items, (rule.sort if rule and rule.sort else config.output.sort))
        if rule and rule.max_items and len(items) > rule.max_items:
            extra = items[rule.max_items :]
            items = items[: rule.max_items]
            for page, _ in extra:
                if rule.overflow == "optional" and not rule.optional:
                    result.placement[page.url] = OPTIONAL
                else:
                    result.placement.pop(page.url, None)
                    result.dropped.append((page.url, f"over max_items of section {name!r}"))
            if rule.overflow == "optional" and not rule.optional:
                overflow.extend(extra)
        final[name] = items
    if overflow:
        final.setdefault(OPTIONAL, [])
        final[OPTIONAL].extend(overflow)
        optional_rule = rule_by_name.get(OPTIONAL)
        if optional_rule and optional_rule.max_items:
            for page, _ in final[OPTIONAL][optional_rule.max_items :]:
                result.placement.pop(page.url, None)
                result.dropped.append((page.url, "over max_items of section 'Optional'"))
            final[OPTIONAL] = final[OPTIONAL][: optional_rule.max_items]

    for link in config.links:
        final.setdefault(OPTIONAL if link.section == OPTIONAL else link.section, []).append(
            (Page(link.url, 10**9, PageInfo(url=link.url)), LinkItem(link.title, link.url, link.description))
        )
        if link.section not in order:
            order.append(link.section)

    names = [n for n in order if n != OPTIONAL and final.get(n)]
    if final.get(OPTIONAL):
        names.append(OPTIONAL)  # by convention the Optional section comes last
    for name in names:
        doc.sections.append(Section(name, [item for _, item in final[name]]))

    if config.output.full:
        placed = [page for name in names for page, _ in final[name] if page.order < 10**9]
        result.full_text = build_full(doc, placed, config)
    return result


_HEADING = re.compile(r"^(#{1,6})(\s)", re.M)


def _heading_levels(markdown: str) -> list[int]:
    levels = []
    in_fence = False
    for line in markdown.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
        elif not in_fence:
            match = _HEADING.match(line)
            if match:
                levels.append(len(match.group(1)))
    return levels


def demote_headings(markdown: str, top_level: int = 3) -> str:
    """Shift headings so the highest one in ``markdown`` is ``top_level`` (pages sit under ``##`` headings)."""
    levels = _heading_levels(markdown)
    if not levels or min(levels) >= top_level:
        return markdown
    shift = top_level - min(levels)
    out = []
    in_fence = False
    for line in markdown.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
        if not in_fence:
            line = _HEADING.sub(lambda m: "#" * min(6, len(m.group(1)) + shift) + m.group(2), line)
        out.append(line)
    return "\n".join(out)


def _drop_leading_title(markdown: str, titles: set[str]) -> str:
    lines = markdown.lstrip().splitlines()
    if lines and lines[0].startswith("# "):
        heading = clean(lines[0][2:]).lower()
        if heading in titles:
            return "\n".join(lines[1:]).lstrip("\n")
    return markdown


def build_full(doc: Document, pages: list[Page], config: Config) -> str:
    """llms-full.txt: the site header followed by the full Markdown content of every listed page."""
    parts = [f"# {doc.title}"]
    if doc.summary:
        parts.append(f"> {doc.summary}")
    if doc.details:
        parts.append(doc.details)
    limit = config.output.full_max_chars_per_page
    for page in pages:
        info = page.info
        title = page_title(info, config, doc.title)
        body = _drop_leading_title(info.content_markdown, {title.lower(), clean(info.h1).lower()})
        if config.output.full_demote_headings:
            body = demote_headings(body)
        if limit and len(body) > limit:
            body = body[:limit].rsplit("\n", 1)[0].rstrip() + "\n\n[…]"
        header = f"## {title}\n\nSource: {page.url}"
        if info.description:
            header += f"\n\n> {clean(info.description)}"
        parts.append(header + ("\n\n" + body.strip() if body.strip() else ""))
    return "\n\n".join(parts).rstrip() + "\n"
