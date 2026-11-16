from __future__ import annotations

import pytest

from llms_txt_builder.robots import RobotsTxt, _product_token
from llms_txt_builder.urls import (
    UrlPattern,
    absolutize,
    looks_like_html,
    normalize_url,
    same_site,
)


class TestNormalize:
    def test_lowercases_and_drops_fragment_and_default_port(self):
        assert normalize_url("HTTPS://Example.COM:443/Docs#top") == "https://example.com/Docs"

    def test_keeps_non_default_port(self):
        assert normalize_url("http://example.com:8080") == "http://example.com:8080/"

    def test_strips_tracking_parameters(self):
        assert normalize_url("https://x.io/a?utm_source=n&id=3&gclid=1") == "https://x.io/a?id=3"

    def test_without_query(self):
        assert normalize_url("https://x.io/a?id=3", keep_query=False) == "https://x.io/a"


class TestAbsolutize:
    @pytest.mark.parametrize("href", ["#x", "javascript:void(0)", "mailto:a@b.c", "tel:123", "", "data:text/plain,1"])
    def test_ignores_non_pages(self, href):
        assert absolutize("https://x.io/a/", href) is None

    def test_resolves_relative(self):
        assert absolutize("https://x.io/a/b", "../c") == "https://x.io/c"

    def test_protocol_relative(self):
        assert absolutize("https://x.io/", "//cdn.x.io/p") == "https://cdn.x.io/p"


def test_same_site_ignores_www():
    assert same_site("https://www.x.io/a", "https://x.io/b")
    assert same_site("https://www.x.io/a", "https://www.x.io/b", allow_www=False)
    assert not same_site("https://www.x.io/a", "https://x.io/b", allow_www=False)
    assert not same_site("https://x.io/", "https://y.io/")


@pytest.mark.parametrize(
    ("url", "html"),
    [
        ("https://x.io/a.pdf", False),
        ("https://x.io/img/A.JPG", False),
        ("https://x.io/a/", True),
        ("https://x.io/a.html", True),
    ],
)
def test_looks_like_html(url, html):
    assert looks_like_html(url) is html


class TestPatterns:
    def test_star_does_not_cross_slash(self):
        p = UrlPattern("/blog/*")
        assert p.matches("https://x.io/blog/post")
        assert p.matches("https://x.io/blog/post/")
        assert not p.matches("https://x.io/blog/2026/post/")

    def test_double_star(self):
        p = UrlPattern("/docs/**")
        assert p.matches("https://x.io/docs/")
        assert p.matches("https://x.io/docs")
        assert p.matches("https://x.io/docs/a/b/c/")
        assert not p.matches("https://x.io/docsy/")

    def test_exact_root(self):
        p = UrlPattern("/")
        assert p.matches("https://x.io/")
        assert not p.matches("https://x.io/about/")

    def test_regex(self):
        p = UrlPattern(r"re:/20\d\d/")
        assert p.matches("https://x.io/blog/2026/x/")
        assert not p.matches("https://x.io/blog/x/")

    def test_full_url_glob(self):
        p = UrlPattern("https://docs.x.io/*")
        assert p.matches("https://docs.x.io/a")
        assert not p.matches("https://x.io/a")

    def test_query_is_part_of_target(self):
        assert UrlPattern("/search*").matches("https://x.io/search?q=1")


class TestRobots:
    TXT = """
# comment
User-agent: *
Disallow: /private/
Allow: /private/public-page
Disallow: /*.pdf$
Disallow: /search
Crawl-delay: 2

User-agent: llms-txt-builder
User-agent: other
Disallow: /drafts/

User-agent: blocked
Disallow: /

Sitemap: https://x.io/sitemap.xml
Sitemap: https://x.io/news-sitemap.xml
"""

    def setup_method(self):
        self.robots = RobotsTxt.parse(self.TXT)

    def test_sitemaps_collected(self):
        assert self.robots.sitemaps == ["https://x.io/sitemap.xml", "https://x.io/news-sitemap.xml"]

    def test_star_group(self):
        r = self.robots
        assert not r.can_fetch("somebot", "https://x.io/private/a")
        assert r.can_fetch("somebot", "https://x.io/private/public-page")  # longest match wins
        assert not r.can_fetch("somebot", "https://x.io/file.pdf")
        assert r.can_fetch("somebot", "https://x.io/file.pdf?x=1")  # "$" anchors the end
        assert not r.can_fetch("somebot", "https://x.io/search?q=a")
        assert r.can_fetch("somebot", "https://x.io/")

    def test_specific_group_replaces_star(self):
        r = self.robots
        assert not r.can_fetch("llms-txt-builder", "https://x.io/drafts/x")
        # The specific group does not inherit the "*" rules.
        assert r.can_fetch("llms-txt-builder", "https://x.io/private/a")

    def test_blocked_agent(self):
        assert not self.robots.can_fetch("blocked", "https://x.io/anything")
        assert self.robots.can_fetch("blocked", "https://x.io/robots.txt")

    def test_crawl_delay(self):
        assert self.robots.crawl_delay("somebot") == 2
        assert self.robots.crawl_delay("llms-txt-builder") is None

    def test_allow_wins_tie(self):
        r = RobotsTxt.parse("User-agent: *\nDisallow: /a\nAllow: /a\n")
        assert r.can_fetch("x", "https://x.io/a")

    def test_empty_disallow_allows(self):
        assert RobotsTxt.parse("User-agent: *\nDisallow:\n").can_fetch("x", "https://x.io/a")

    def test_percent_encoding_is_compared_decoded(self):
        r = RobotsTxt.parse("User-agent: *\nDisallow: /caf%C3%A9\n")
        assert not r.can_fetch("x", "https://x.io/café")

    def test_allow_and_disallow_all(self):
        assert RobotsTxt.allowing_all().can_fetch("x", "https://x.io/a")
        assert not RobotsTxt.disallowing_all().can_fetch("x", "https://x.io/a")

    def test_product_token(self):
        assert _product_token("Mozilla/5.0 (compatible; llms-txt-builder/0.1; +https://x)") == "llms-txt-builder"
        assert _product_token("curl/8.0") == "curl"
