from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from fakesite import HOST, FakeSite, with_fetcher
from llms_txt_builder.diff import diff_texts
from llms_txt_builder.llmstxt import Document, LinkItem, Section, parse
from llms_txt_builder.validate import RULES, check_links, lint, lint_bytes

GOOD = """# Example Docs

> Static documentation for small teams.

Important notes:

- Prices are in USD.
- The API is in beta.

## Docs

- [Installation](https://www.example.com/docs/install/): Install with pip
- [Configuration](https://www.example.com/docs/config/)

## Optional

- [Privacy](https://www.example.com/privacy/)
"""


def codes(text: str) -> list[str]:
    return sorted(i.code for i in lint(text).issues)


class TestParse:
    def test_good_file(self):
        doc = parse(GOOD)
        assert doc.title == "Example Docs"
        assert doc.summary == "Static documentation for small teams."
        assert doc.details.startswith("Important notes:")
        assert [s.name for s in doc.sections] == ["Docs", "Optional"]
        install = doc.sections[0].items[0]
        assert (install.title, install.url, install.description, install.line) == (
            "Installation",
            "https://www.example.com/docs/install/",
            "Install with pip",
            12,
        )
        assert doc.sections[1].is_optional

    def test_bom_and_multiline_summary(self):
        doc = parse("﻿# T\n> line one\n> line two\n")
        assert doc.title == "T"
        assert doc.summary == "line one line two"

    def test_escaped_brackets_and_angle_urls(self):
        doc = parse("# T\n\n## S\n\n- [a \\[b\\]](<https://x.io/a b>): d\n")
        item = doc.sections[0].items[0]
        assert item.title == "a [b]"
        assert item.url == "https://x.io/a b"

    def test_render_round_trip(self):
        doc = parse(GOOD)
        again = parse(doc.render())
        assert again.all_links() and [(s.name, i) for s, i in again.all_links()] == [
            (s.name, i) for s, i in doc.all_links()
        ]

    def test_render_escapes(self):
        doc = Document("T", "S", sections=[Section("A", [LinkItem("x [y]", "https://x.io/a (b)", "multi\nline")])])
        assert "- [x \\[y\\]](https://x.io/a%20%28b%29): multi line" in doc.render()


class TestLint:
    def test_good_file_is_clean(self):
        assert lint(GOOD).issues == []
        assert lint(GOOD).ok(strict=True)

    def test_empty(self):
        assert codes("") == ["LT001"]
        assert codes("  \n\n") == ["LT001"]

    def test_missing_h1(self):
        assert "LT002" in codes("> summary\n\n## Docs\n\n- [a](https://x.io/)\n")
        assert "LT002" in codes("## Docs\n")

    def test_empty_h1(self):
        assert "LT020" in codes("#\n\n> s\n")

    def test_second_h1(self):
        assert "LT003" in codes("# A\n\n> s\n\n# B\n")

    def test_missing_and_misplaced_summary(self):
        assert codes("# A\n\n## S\n\n- [a](https://x.io/)\n") == ["LT004"]
        assert codes("# A\n\nSome text\n\n> late summary\n\n## S\n\n- [a](https://x.io/)\n") == ["LT005"]

    def test_deep_headings(self):
        assert codes("# A\n\n> s\n\n### Deep\n\n## S\n\n- [a](https://x.io/)\n") == ["LT006"]

    def test_headings_in_code_fences_ignored(self):
        assert codes("# A\n\n> s\n\n```\n### not a heading\n# nor this\n```\n") == []

    def test_bad_list_items(self):
        text = "# A\n\n> s\n\n## S\n\n- just text\n- [a](https://x.io/a) no colon\n- [b](https://x.io/b):\n"
        assert codes(text) == ["LT007", "LT008", "LT019"]

    def test_bad_urls(self):
        text = "# A\n\n> s\n\n## S\n\n- [a](/relative)\n- [b](ftp://x.io/)\n- [c]()\n- [](https://x.io/d)\n"
        assert codes(text) == ["LT009", "LT009", "LT015", "LT021"]

    def test_duplicates_and_empty_sections(self):
        text = (
            "# A\n\n> s\n\n## S\n\n- [a](https://x.io/a)\n\n## Optional\n\n- [b](https://x.io/b)\n\n"
            "## S\n\n- [a again](https://x.io/a)\n\n## Empty\n\n## \n"
        )
        assert codes(text) == ["LT010", "LT010", "LT011", "LT012", "LT013", "LT022"]

    def test_prose_in_link_section(self):
        assert codes("# A\n\n> s\n\n## S\n\nSome prose.\n\n- [a](https://x.io/a)\n") == ["LT014"]

    def test_invalid_utf8(self):
        report = lint_bytes(b"# A\n\n> caf\xe9\n")
        assert [i.code for i in report.issues] == ["LT018"]
        assert report.issues[0].line == 3

    def test_every_code_documented_with_severity(self):
        assert all(sev in ("error", "warning") for sev, _ in RULES.values())

    def test_strict_and_formats(self):
        report = lint("# A\n\n## S\n\n- [a](https://x.io/)\n", "llms.txt")
        assert report.ok() and not report.ok(strict=True)
        assert "llms.txt:1: LT004 warning:" in report.to_text()
        data = json.loads(report.to_json())
        assert data["warnings"] == 1 and data["issues"][0]["code"] == "LT004"
        assert report.to_github().startswith("::warning file=llms.txt,line=1,title=LT004::")


class TestCheckLinks:
    def test_dead_and_redirecting_links(self):
        site = FakeSite(
            extra={
                "/gone/": httpx.Response(410),
                "/moved/": httpx.Response(301, headers={"location": f"{HOST}/docs/"}),
            }
        )
        text = (
            f"# A\n\n> s\n\n## S\n\n- [ok]({HOST}/docs/)\n- [gone]({HOST}/gone/)\n- [moved]({HOST}/moved/)\n"
            f"- [dup]({HOST}/docs/)\n"
        )
        report = lint(text)

        async def go(fetcher):
            await check_links(report, parse(text), fetcher, allow_redirects=False)

        asyncio.run(with_fetcher(site, go, respect_robots=False))
        found = {(i.code, i.line) for i in report.issues}
        assert ("LT016", 8) in found
        assert ("LT017", 9) in found
        assert ("LT011", 10) in found
        assert report.links_checked == 3


OLD = GOOD
NEW = """# Example Docs

> Static documentation for small teams, now with a blog.

## Docs

- [Install guide](https://www.example.com/docs/install/?utm_source=x): Install with pip
- [Upgrading](https://www.example.com/docs/upgrade/)

## Blog

- [Configuration](https://www.example.com/docs/config/)
"""


class TestDiff:
    def test_identical(self):
        result = diff_texts(GOOD, GOOD)
        assert result.identical
        assert "same content" in result.to_text()
        assert result.unified == ""

    def test_changes(self):
        result = diff_texts(OLD, NEW, old_source="live", new_source="built")
        kinds = sorted(c.kind for c in result.changes)
        assert kinds == [
            "details",
            "link-added",
            "link-moved",
            "link-removed",
            "link-title",
            "section-added",
            "section-removed",
            "summary",
        ]
        text = result.to_text()
        assert "+ [Docs] https://www.example.com/docs/upgrade/" in text
        assert "- [Optional] https://www.example.com/privacy/" in text
        assert "moved from 'Docs' to 'Blog'" in text
        assert result.unified.startswith("--- live\n+++ built\n")
        data = json.loads(result.to_json())
        assert data["identical"] is False and data["counts"]["link-added"] == 1

    def test_exact_urls(self):
        result = diff_texts(OLD, NEW, normalize=False)
        added = [c.url for c in result.changes if c.kind == "link-added"]
        assert "https://www.example.com/docs/install/?utm_source=x" in added

    def test_descriptions_can_be_ignored(self):
        a = "# A\n\n## S\n\n- [x](https://x.io/): one\n"
        b = "# A\n\n## S\n\n- [x](https://x.io/): two\n"
        assert [c.kind for c in diff_texts(a, b).changes] == ["link-description"]
        assert diff_texts(a, b, ignore_descriptions=True).identical

    def test_section_order(self):
        a = "# A\n\n## S1\n\n- [x](https://x.io/1)\n\n## S2\n\n- [y](https://x.io/2)\n"
        b = "# A\n\n## S2\n\n- [y](https://x.io/2)\n\n## S1\n\n- [x](https://x.io/1)\n"
        assert [c.kind for c in diff_texts(a, b).changes] == ["section-order"]


@pytest.mark.parametrize("text", [GOOD, NEW, "# x", "garbage\n- [a](b", "## only\n- [a](https://x.io)"])
def test_parser_never_raises(text):
    parse(text)
    lint(text)
