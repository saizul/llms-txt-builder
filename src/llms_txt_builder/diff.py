"""Structural comparison of two llms.txt files (for example a freshly built one and the live one)."""

from __future__ import annotations

import difflib
import json
from dataclasses import asdict, dataclass, field

from llms_txt_builder.llmstxt import Document, parse
from llms_txt_builder.urls import normalize_url


@dataclass
class Change:
    kind: str  # title | summary | details | section-added | section-removed | link-added | link-removed |
    # link-moved | link-title | link-description | section-order
    section: str = ""
    url: str = ""
    old: str = ""
    new: str = ""

    def describe(self) -> str:
        k = self.kind
        if k == "title":
            return f"title: {self.old!r} -> {self.new!r}"
        if k == "summary":
            return f"summary changed:\n    - {self.old}\n    + {self.new}"
        if k == "details":
            return "notes/details text changed"
        if k == "section-added":
            return f"+ section {self.section!r}"
        if k == "section-removed":
            return f"- section {self.section!r}"
        if k == "section-order":
            return f"section order: {self.old} -> {self.new}"
        if k == "link-added":
            return f"+ [{self.section}] {self.url}"
        if k == "link-removed":
            return f"- [{self.section}] {self.url}"
        if k == "link-moved":
            return f"~ {self.url} moved from {self.old!r} to {self.new!r}"
        if k == "link-title":
            return f"~ {self.url} title: {self.old!r} -> {self.new!r}"
        if k == "link-description":
            return f"~ {self.url} notes: {self.old!r} -> {self.new!r}"
        return f"{k} {self.section} {self.url}"  # pragma: no cover


@dataclass
class DiffResult:
    old_source: str
    new_source: str
    changes: list[Change] = field(default_factory=list)
    unified: str = ""

    @property
    def identical(self) -> bool:
        return not self.changes

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for change in self.changes:
            out[change.kind] = out.get(change.kind, 0) + 1
        return out

    def to_text(self) -> str:
        if self.identical:
            return f"{self.old_source} and {self.new_source} list the same content"
        lines = [f"--- {self.old_source}", f"+++ {self.new_source}"]
        lines.extend(c.describe() for c in self.changes)
        counts = self.counts()
        lines.append(f"{len(self.changes)} change(s): " + ", ".join(f"{v} {k}" for k, v in sorted(counts.items())))
        return "\n".join(lines)

    def to_json(self) -> str:
        return json.dumps(
            {
                "old": self.old_source,
                "new": self.new_source,
                "identical": self.identical,
                "counts": self.counts(),
                "changes": [asdict(c) for c in self.changes],
            },
            indent=2,
        )


def _links(doc: Document, *, normalize: bool) -> dict[str, tuple[str, str, str]]:
    out: dict[str, tuple[str, str, str]] = {}
    for section, item in doc.all_links():
        key = normalize_url(item.url) if normalize and "://" in item.url else item.url
        out.setdefault(key, (section.name, item.title, item.description))
    return out


def diff_documents(
    old: Document,
    new: Document,
    *,
    old_source: str = "old",
    new_source: str = "new",
    normalize: bool = True,
    ignore_descriptions: bool = False,
) -> DiffResult:
    result = DiffResult(old_source, new_source)
    changes = result.changes
    if old.title.strip() != new.title.strip():
        changes.append(Change("title", old=old.title, new=new.title))
    if " ".join(old.summary.split()) != " ".join(new.summary.split()):
        changes.append(Change("summary", old=old.summary, new=new.summary))
    if " ".join(old.details.split()) != " ".join(new.details.split()):
        changes.append(Change("details", old=old.details, new=new.details))

    old_names = [s.name for s in old.sections]
    new_names = [s.name for s in new.sections]
    for name in new_names:
        if name not in old_names:
            changes.append(Change("section-added", section=name))
    for name in old_names:
        if name not in new_names:
            changes.append(Change("section-removed", section=name))
    common_old = [n for n in old_names if n in new_names]
    common_new = [n for n in new_names if n in old_names]
    if common_old != common_new:
        changes.append(Change("section-order", old=" > ".join(common_old), new=" > ".join(common_new)))

    old_links = _links(old, normalize=normalize)
    new_links = _links(new, normalize=normalize)
    for url, (section, title, description) in new_links.items():
        if url not in old_links:
            changes.append(Change("link-added", section=section, url=url, new=title))
            continue
        o_section, o_title, o_description = old_links[url]
        if o_section != section:
            changes.append(Change("link-moved", section=section, url=url, old=o_section, new=section))
        if o_title != title:
            changes.append(Change("link-title", section=section, url=url, old=o_title, new=title))
        if not ignore_descriptions and " ".join(o_description.split()) != " ".join(description.split()):
            changes.append(Change("link-description", section=section, url=url, old=o_description, new=description))
    for url, (section, title, _) in old_links.items():
        if url not in new_links:
            changes.append(Change("link-removed", section=section, url=url, old=title))
    return result


def diff_texts(
    old_text: str,
    new_text: str,
    *,
    old_source: str = "old",
    new_source: str = "new",
    normalize: bool = True,
    ignore_descriptions: bool = False,
) -> DiffResult:
    result = diff_documents(
        parse(old_text),
        parse(new_text),
        old_source=old_source,
        new_source=new_source,
        normalize=normalize,
        ignore_descriptions=ignore_descriptions,
    )
    result.unified = "".join(
        difflib.unified_diff(
            old_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=old_source,
            tofile=new_source,
        )
    )
    return result
