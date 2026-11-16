from __future__ import annotations

import asyncio

from fakesite import HOST, FakeSite, with_fetcher
from llms_txt_builder.build import build, demote_headings, page_description, page_title
from llms_txt_builder.collect import Collector, read_url_list
from llms_txt_builder.config import Config, config_from_dict
from llms_txt_builder.extract import PageInfo
from llms_txt_builder.llmstxt import parse
from llms_txt_builder.validate import lint

SECTIONS = {
    "filter": {"exclude": ["/tag/**"]},
    "sections": [
        {"name": "Docs", "match": ["/docs/**"], "sort": "url"},
        {"name": "Blog", "match": ["/blog/*"], "max_items": 2, "overflow": "optional"},
        {"name": "Legal", "match": ["/privacy/"], "optional": True},
    ],
}


def collect_sitemap(site: FakeSite, config: Config):
    async def go(fetcher):
        return await Collector(fetcher, config).from_sitemaps([f"{HOST}/sitemap_index.xml"])

    return asyncio.run(with_fetcher(site, go))


def collect_crawl(site: FakeSite, config: Config, start: str = f"{HOST}/"):
    async def go(fetcher):
        return await Collector(fetcher, config).crawl(start)

    return asyncio.run(with_fetcher(site, go))


class TestCollectFromSitemap:
    def test_pages_and_skip_reasons(self, site: FakeSite):
        collection = collect_sitemap(site, config_from_dict(SECTIONS))
        urls = [p.url for p in collection.pages]
        assert f"{HOST}/docs/install/" in urls
        assert f"{HOST}/blog/post-2/" in urls
        reasons = {s.url: s.reason for s in collection.skipped}
        assert reasons[f"{HOST}/tag/news/"] == "matched by filter.exclude"
        assert reasons[f"{HOST}/hidden/"] == "meta robots noindex"
        assert reasons[f"{HOST}/private/"] == "disallowed by robots.txt"
        assert reasons[f"{HOST}/feed.xml"] == "not an HTML page"
        assert reasons[f"{HOST}/docs/config-copy/"].startswith("canonical points to")
        # The redirect to an already listed page is dropped as a duplicate after the redirect is followed.
        assert {e.url: e.reason for e in collection.errors}[f"{HOST}/missing/"] == "HTTP 404"
        assert "/private/" not in site.paths()
        assert collection.homepage is not None and collection.homepage.site_name == "Example Docs"

    def test_order_follows_the_sitemap(self, site: FakeSite):
        collection = collect_sitemap(site, Config())
        orders = [p.order for p in collection.pages]
        assert orders == sorted(orders)

    def test_max_pages(self, site: FakeSite):
        config = Config()
        config.source.max_pages = 2
        collection = collect_sitemap(site, config)
        assert len(collection.pages) == 2
        assert any("max_pages" in s.reason for s in collection.skipped)

    def test_x_robots_tag_noindex(self, site: FakeSite):
        import httpx

        site.extra["/docs/"] = httpx.Response(
            200,
            text="<html><body><p>x</p></body></html>",
            headers={"x-robots-tag": "noindex", "content-type": "text/html"},
        )
        collection = collect_sitemap(site, Config())
        assert f"{HOST}/docs/" not in [p.url for p in collection.pages]


class TestCrawl:
    def test_crawl_finds_linked_pages(self, site: FakeSite):
        collection = collect_crawl(site, config_from_dict(SECTIONS))
        urls = {p.url for p in collection.pages}
        assert {f"{HOST}/", f"{HOST}/docs/", f"{HOST}/docs/install/", f"{HOST}/blog/post-3/"} <= urls
        assert f"{HOST}/tag/news/" not in urls
        assert "/tag/news/" not in site.paths()  # excluded URLs are not even requested

    def test_depth_limit(self, site: FakeSite):
        config = Config()
        config.source.max_depth = 1
        collection = collect_crawl(site, config)
        urls = {p.url for p in collection.pages}
        assert f"{HOST}/docs/" in urls
        assert f"{HOST}/docs/install/" not in urls  # two clicks away

    def test_include_filters_output_but_crawl_continues(self, site: FakeSite):
        config = config_from_dict({"filter": {"include": ["/docs/**"]}})
        collection = collect_crawl(site, config)
        urls = {p.url for p in collection.pages}
        assert urls == {f"{HOST}/docs/", f"{HOST}/docs/install/", f"{HOST}/docs/config/"}
        assert collection.homepage is not None

    def test_crawl_stays_on_host(self, site: FakeSite):
        site.pages["/docs/"] = site.pages["/docs/"].replace(
            "</article>", '<a href="https://other.example.org/x">x</a></article>'
        )
        collect_crawl(site, Config())
        assert all(r.url.host == "www.example.com" for r in site.requests)


class TestBuild:
    def test_document_structure(self, site: FakeSite):
        config = config_from_dict(SECTIONS)
        result = build(collect_sitemap(site, config), config)
        doc = result.document
        assert doc.title == "Example Docs"
        assert doc.summary == "Example Docs is a static documentation generator for small teams."
        names = [s.name for s in doc.sections]
        assert names == ["Docs", "Blog", "Pages", "Optional"]
        docs = doc.section("Docs")
        assert [i.url for i in docs.items] == sorted(i.url for i in docs.items)
        install = next(i for i in docs.items if i.url.endswith("/install/"))
        assert install.title == "Installation"
        assert install.description == "Install Example Docs with pip and create your first site."
        assert len(doc.section("Blog").items) == 2
        optional_urls = [i.url for i in doc.section("Optional").items]
        assert f"{HOST}/privacy/" in optional_urls
        assert f"{HOST}/blog/post-2/" in optional_urls  # overflow from Blog
        assert result.placement[f"{HOST}/blog/post-2/"] == "Optional"
        assert lint(result.text).issues == []

    def test_description_falls_back_to_content(self, site: FakeSite):
        config = config_from_dict(SECTIONS)
        doc = build(collect_sitemap(site, config), config).document
        cfg = next(i for i in doc.section("Docs").items if i.url.endswith("/config/"))
        assert cfg.description.startswith("This guide walks through every step")
        assert len(cfg.description) <= 160

    def test_drop_unmatched_and_overflow_drop(self, site: FakeSite):
        config = config_from_dict(
            {
                "output": {"default_section": ""},
                "sections": [{"name": "Blog", "match": ["/blog/*"], "max_items": 1}],
            }
        )
        result = build(collect_sitemap(site, config), config)
        assert [s.name for s in result.document.sections] == ["Blog"]
        reasons = dict(result.dropped)
        assert "over max_items" in reasons[f"{HOST}/blog/post-2/"]
        assert "no section matched" in reasons[f"{HOST}/docs/"]

    def test_site_overrides_and_notes_and_extra_links(self, site: FakeSite):
        config = config_from_dict(
            {
                "site": {"name": "My Docs", "summary": "Short summary.", "notes": ["Note one.", "Note two."]},
                "links": [
                    {"section": "External", "title": "GitHub", "url": "https://github.com/x/y", "description": "Code"}
                ],
            }
        )
        result = build(collect_sitemap(site, config), config)
        text = result.text
        assert text.startswith("# My Docs\n\n> Short summary.\n\nNote one.\n\nNote two.\n\n## Pages\n")
        assert "## External\n\n- [GitHub](https://github.com/x/y): Code" in text
        assert "github.com" not in result.full_text

    def test_link_to_markdown(self, site: FakeSite):
        config = config_from_dict({"output": {"link_to_markdown": True}})
        result = build(collect_sitemap(site, config), config)
        urls = [i.url for _, i in result.document.all_links()]
        assert f"{HOST}/docs/install.md" in urls

    def test_full_text(self, site: FakeSite):
        config = config_from_dict(SECTIONS)
        full = build(collect_sitemap(site, config), config).full_text
        assert full.startswith("# Example Docs\n\n> Example Docs is a static documentation generator")
        assert f"## Installation\n\nSource: {HOST}/docs/install/" in full
        assert "### Requirements" in full  # demoted below the page heading
        assert "\n# Installation" not in full  # the page H1 is not repeated
        assert "Copyright" not in full
        parsed = parse(full)
        assert parsed.title == "Example Docs"

    def test_full_disabled_and_limit(self, site: FakeSite):
        config = config_from_dict({"output": {"full": False}})
        assert build(collect_sitemap(site, config), config).full_text == ""
        config = config_from_dict({"output": {"full_max_chars_per_page": 50}})
        full = build(collect_sitemap(site, config), config).full_text
        assert "[…]" in full

    def test_sort_by_title(self, site: FakeSite):
        config = config_from_dict({"output": {"sort": "title"}})
        doc = build(collect_sitemap(site, config), config).document
        titles = [i.title.lower() for i in doc.section("Pages").items]
        assert titles == sorted(titles)


class TestTitlesAndDescriptions:
    def test_title_sources(self):
        info = PageInfo(url="https://x.io/a/", title="Pricing plans | Shop", h1="Plans and prices")
        assert page_title(info, Config(), "Shop") == "Pricing plans"
        assert page_title(info, config_from_dict({"output": {"title_source": "h1"}}), "Shop") == "Plans and prices"
        cfg = config_from_dict({"output": {"strip_site_name": False, "title_source": "title"}})
        assert page_title(info, cfg, "Shop") == "Pricing plans | Shop"

    def test_title_equal_to_site_name_uses_h1(self):
        info = PageInfo(url="https://x.io/", title="Shop", h1="Welcome to the shop")
        assert page_title(info, Config(), "Shop") == "Welcome to the shop"

    def test_title_from_url(self):
        assert page_title(PageInfo(url="https://x.io/getting-started/"), Config(), "X") == "Getting started"
        assert page_title(PageInfo(url="https://x.io/"), Config(), "X") == "Home"

    def test_description_sources(self):
        info = PageInfo(url="u", description="Meta text.", content_text="Body text. More.")
        assert page_description(info, Config()) == "Meta text."
        assert (
            page_description(info, config_from_dict({"output": {"description_source": "content"}}))
            == "Body text. More."
        )
        assert page_description(info, config_from_dict({"output": {"description_source": "none"}})) == ""
        empty = PageInfo(url="u", content_text="")
        assert page_description(empty, config_from_dict({"output": {"description_source": "meta"}})) == ""


def test_demote_headings_ignores_code():
    text = "# A\n\n```\n# not a heading\n```\n\n## B"
    assert demote_headings(text) == "### A\n\n```\n# not a heading\n```\n\n#### B"
    assert demote_headings("### already deep") == "### already deep"


def test_read_url_list(tmp_path):
    path = tmp_path / "urls.txt"
    path.write_text("# comment\nhttps://x.io/a\n\n  https://x.io/b  trailing words\nnot-a-url\n", encoding="utf-8")
    assert read_url_list(path) == ["https://x.io/a", "https://x.io/b"]
