"""Lint an llms.txt file against the structure of the llmstxt.org proposal."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass, field
from urllib.parse import urlsplit

from llms_txt_builder.fetch import Fetcher
from llms_txt_builder.llmstxt import HEADING, LIST_ITEM, Document, LinkItem, parse, parse_link_line

ERROR = "error"
WARNING = "warning"

# code -> (severity, short title). The README documents every code.
RULES: dict[str, tuple[str, str]] = {
    "LT001": (ERROR, "file is empty"),
    "LT002": (ERROR, "first content line is not an H1"),
    "LT003": (ERROR, "more than one H1"),
    "LT004": (WARNING, "no blockquote summary after the H1"),
    "LT005": (WARNING, "blockquote summary is not directly after the H1"),
    "LT006": (ERROR, "heading level 3 or deeper"),
    "LT007": (ERROR, "list item in a section is not a Markdown link"),
    "LT008": (WARNING, "text after a link is not introduced with a colon"),
    "LT009": (ERROR, "link URL is not a valid http(s) URL"),
    "LT010": (WARNING, "section has no links"),
    "LT011": (WARNING, "duplicate URL"),
    "LT012": (WARNING, "duplicate section name"),
    "LT013": (WARNING, "Optional section is not the last section"),
    "LT014": (WARNING, "non-list content inside a link section"),
    "LT015": (ERROR, "link has an empty name"),
    "LT016": (ERROR, "link is dead"),
    "LT017": (WARNING, "link redirects"),
    "LT018": (ERROR, "file is not valid UTF-8"),
    "LT019": (WARNING, "empty notes after the colon"),
    "LT020": (WARNING, "H1 is empty"),
    "LT021": (WARNING, "relative link URL"),
    "LT022": (WARNING, "section heading is empty"),
}


@dataclass
class Issue:
    code: str
    line: int
    message: str
    severity: str = ""

    def __post_init__(self) -> None:
        if not self.severity:
            self.severity = RULES[self.code][0]


@dataclass
class Report:
    source: str
    issues: list[Issue] = field(default_factory=list)
    links_checked: int = 0

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == ERROR]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == WARNING]

    def add(self, code: str, line: int, message: str) -> None:
        self.issues.append(Issue(code, line, message))

    def ok(self, *, strict: bool = False) -> bool:
        return not self.errors and not (strict and self.warnings)

    def sorted(self) -> list[Issue]:
        return sorted(self.issues, key=lambda i: (i.line, i.code))

    # ---- output -----------------------------------------------------------------------------------------------
    def to_text(self) -> str:
        lines = [f"{self.source}:{i.line}: {i.code} {i.severity}: {i.message}" for i in self.sorted()]
        summary = f"{len(self.errors)} error(s), {len(self.warnings)} warning(s)"
        if self.links_checked:
            summary += f", {self.links_checked} link(s) checked"
        lines.append(summary)
        return "\n".join(lines)

    def to_json(self) -> str:
        return json.dumps(
            {
                "source": self.source,
                "errors": len(self.errors),
                "warnings": len(self.warnings),
                "links_checked": self.links_checked,
                "issues": [asdict(i) for i in self.sorted()],
            },
            indent=2,
        )

    def to_github(self) -> str:
        """GitHub Actions workflow commands, shown as annotations on the file."""
        out = []
        for issue in self.sorted():
            kind = "error" if issue.severity == ERROR else "warning"
            message = issue.message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
            out.append(f"::{kind} file={self.source},line={max(1, issue.line)},title={issue.code}::{message}")
        return "\n".join(out)


def _valid_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.netloc) and " " not in url


def lint_text(text: str, source: str = "llms.txt") -> Report:
    report = Report(source)
    text = text.removeprefix("﻿")
    lines = text.splitlines()
    if not text.strip():
        report.add("LT001", 1, "the file has no content")
        return report

    first = next((i for i, line in enumerate(lines, start=1) if line.strip()), 1)
    first_line = lines[first - 1].strip()
    match = HEADING.match(first_line)
    if not match or len(match.group("hashes")) != 1:
        report.add("LT002", first, f"expected '# Site name', found {first_line[:60]!r}")
    elif not (match.group("text") or "").strip():
        report.add("LT020", first, "the H1 has no text")

    doc = parse(text)
    _check_structure(lines, doc, report)
    _check_sections(doc, report)
    return report


def _check_structure(lines: list[str], doc: Document, report: Report) -> None:
    in_fence = False
    h1_count = 0
    first_h2 = doc.sections[0].line if doc.sections else len(lines) + 1
    for number, raw in enumerate(lines, start=1):
        stripped = raw.strip()
        if stripped.startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = HEADING.match(stripped)
        if not match:
            continue
        level = len(match.group("hashes"))
        if level == 1:
            h1_count += 1
            if h1_count > 1:
                report.add("LT003", number, "only one H1 (the site name) is allowed")
        elif level >= 3:
            report.add("LT006", number, f"H{level} headings are not part of the format; use H2 sections and lists")
        elif level == 2 and not (match.group("text") or "").strip():
            report.add("LT022", number, "the H2 has no text")
    # Summary placement.
    if doc.title_line:
        after = next((i for i in range(doc.title_line, len(lines)) if lines[i].strip()), None)
        if after is None or not lines[after].strip().startswith(">"):
            blockquote_line = next(
                (
                    i + 1
                    for i in range(doc.title_line, min(first_h2 - 1, len(lines)))
                    if lines[i].strip().startswith(">")
                ),
                None,
            )
            if blockquote_line:
                report.add("LT005", blockquote_line, "move the summary blockquote directly below the H1")
            else:
                report.add("LT004", doc.title_line, "add a '> summary' line below the H1")


def _check_sections(doc: Document, report: Report) -> None:
    seen_names: dict[str, int] = {}
    seen_urls: dict[str, int] = {}
    for index, section in enumerate(doc.sections):
        key = section.name.strip().lower()
        if key in seen_names:
            report.add("LT012", section.line, f"section {section.name!r} already defined on line {seen_names[key]}")
        else:
            seen_names[key] = section.line
        if section.is_optional and index != len(doc.sections) - 1:
            report.add("LT013", section.line, "by convention the Optional section comes last")
        if not section.items:
            report.add("LT010", section.line, f"section {section.name!r} has no links")
        for number, line in section.other:
            stripped = line.strip()
            if HEADING.match(stripped):
                continue  # already reported as LT006 / LT003
            if LIST_ITEM.match(line):
                report.add("LT007", number, f"expected '- [name](url): notes', found {stripped[:60]!r}")
            elif not line.startswith((" ", "\t")):
                report.add("LT014", number, "link sections should only contain a list of links")
        for item in section.items:
            rest = item.rest
            if rest and not rest.startswith(":"):
                report.add("LT008", item.line, f"write '- [name](url): notes', found {rest[:40]!r} after the link")
            elif rest == ":":
                report.add("LT019", item.line, "remove the colon or add notes after it")
            if not item.title:
                report.add("LT015", item.line, "the link text is empty")
            if not item.url:
                report.add("LT009", item.line, "the link has no URL")
            elif "://" not in item.url and not item.url.startswith("//"):
                report.add("LT021", item.line, f"{item.url!r} is relative; absolute URLs work for every reader")
            elif not _valid_url(item.url):
                report.add("LT009", item.line, f"{item.url!r} is not a valid http(s) URL")
            if item.url in seen_urls:
                report.add("LT011", item.line, f"{item.url} is already listed on line {seen_urls[item.url]}")
            elif item.url:
                seen_urls[item.url] = item.line


def lint(text: str, source: str = "llms.txt") -> Report:
    """Lint llms.txt ``text``; ``source`` is used in messages."""
    return lint_text(text, source)


def lint_bytes(data: bytes, source: str = "llms.txt") -> Report:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        report = Report(source)
        line = data[: exc.start].count(b"\n") + 1
        report.add("LT018", line, f"invalid UTF-8 byte at offset {exc.start}")
        return report
    return lint(text, source)


async def check_links(
    report: Report, doc: Document, fetcher: Fetcher, *, allow_redirects: bool = True, same_host_only: bool = False
) -> None:
    """Request every link (HEAD, then GET when HEAD is refused) and report dead or redirecting ones."""
    items = doc.all_links()
    seen: dict[str, int] = {}
    targets = []
    for _, item in items:
        if not _valid_url(item.url) or item.url in seen:
            continue
        seen[item.url] = item.line
        targets.append(item)
    if same_host_only and targets:
        host = urlsplit(targets[0].url).netloc
        targets = [t for t in targets if urlsplit(t.url).netloc == host]

    async def one(item: LinkItem) -> None:
        url, line = item.url, item.line
        result = await fetcher.head_or_get(url)
        if result.error is not None:
            report.add("LT016", line, f"{url}: {result.error}")
        elif result.status >= 400:
            report.add("LT016", line, f"{url}: HTTP {result.status}")
        elif not allow_redirects and result.final_url.rstrip("/") != url.rstrip("/"):
            report.add("LT017", line, f"{url} redirects to {result.final_url}")

    await asyncio.gather(*(one(t) for t in targets))
    report.links_checked = len(targets)


__all__ = ["RULES", "Issue", "Report", "check_links", "lint", "lint_bytes", "parse_link_line"]
