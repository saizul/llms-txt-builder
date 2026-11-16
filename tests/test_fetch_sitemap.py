from __future__ import annotations

import asyncio
import gzip

import httpx
import pytest

from fakesite import HOST, FakeSite, urlset, with_fetcher
from llms_txt_builder.sitemap import SitemapError, maybe_decompress, parse_sitemap, read_sitemaps


class TestParseSitemap:
    def test_urlset_with_fields(self):
        xml = urlset(["/a/", "/b/"], priority={"/a/": "0.8", "/b/": "7"}).encode()
        parsed = parse_sitemap(xml)
        assert parsed.kind == "urlset"
        assert [e.loc for e in parsed.entries] == [f"{HOST}/a/", f"{HOST}/b/"]
        assert parsed.entries[0].priority == 0.8
        assert parsed.entries[1].priority == 1.0  # clamped
        assert parsed.entries[0].lastmod == "2026-11-01"

    def test_sitemap_index(self):
        xml = b"<sitemapindex><sitemap><loc> https://x.io/s1.xml </loc></sitemap><sitemap></sitemap></sitemapindex>"
        parsed = parse_sitemap(xml)
        assert parsed.kind == "sitemapindex"
        assert [e.loc for e in parsed.entries] == ["https://x.io/s1.xml"]

    def test_gzip(self):
        assert parse_sitemap(gzip.compress(urlset(["/a/"]).encode())).entries[0].loc == f"{HOST}/a/"

    def test_rejects_entities(self):
        evil = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><urlset><url><loc>&a;</loc></url></urlset>'
        with pytest.raises(SitemapError, match="DOCTYPE"):
            parse_sitemap(evil)

    def test_rejects_other_documents(self):
        with pytest.raises(SitemapError, match="root element"):
            parse_sitemap(b"<html><body>hi</body></html>")
        with pytest.raises(SitemapError, match="not valid XML"):
            parse_sitemap(b"<urlset><url>")

    def test_gzip_bomb_limit(self):
        with pytest.raises(SitemapError, match="expands"):
            maybe_decompress(gzip.compress(b"a" * 10_000), limit=1000)


class TestReadSitemaps:
    def test_index_plain_and_gzip_children(self, site: FakeSite):
        result = asyncio.run(with_fetcher(site, lambda f: read_sitemaps(f, [f"{HOST}/sitemap_index.xml"])))
        locs = [e.loc for e in result.entries]
        assert f"{HOST}/docs/install/" in locs
        assert f"{HOST}/blog/post-3/" in locs
        assert len(result.sitemaps_read) == 3
        assert not result.errors

    def test_errors_are_collected(self, site: FakeSite):
        site.extra["/broken.xml"] = httpx.Response(200, text="<urlset><url>")
        urls = [f"{HOST}/broken.xml", f"{HOST}/nope.xml", f"{HOST}/page-sitemap.xml"]
        result = asyncio.run(with_fetcher(site, lambda f: read_sitemaps(f, urls)))
        reasons = dict(result.errors)
        assert "not valid XML" in reasons[f"{HOST}/broken.xml"]
        assert reasons[f"{HOST}/nope.xml"] == "HTTP 404"
        assert result.entries

    def test_depth_limit_and_loops(self, site: FakeSite):
        loop = f"<sitemapindex><sitemap><loc>{HOST}/loop.xml</loc></sitemap></sitemapindex>"
        site.extra["/loop.xml"] = httpx.Response(200, text=loop)
        result = asyncio.run(with_fetcher(site, lambda f: read_sitemaps(f, [f"{HOST}/loop.xml"], max_depth=2)))
        assert result.entries == []
        assert site.paths().count("/loop.xml") == 1

    def test_max_urls(self, site: FakeSite):
        result = asyncio.run(with_fetcher(site, lambda f: read_sitemaps(f, [f"{HOST}/sitemap_index.xml"], max_urls=3)))
        assert len(result.entries) == 3


class TestFetcher:
    def test_retries_on_503(self):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(503 if calls["n"] < 3 else 200, text="ok")

        site = FakeSite(extra={"/flaky": handler})
        result = asyncio.run(with_fetcher(site, lambda f: f.get(f"{HOST}/flaky"), retries=2))
        assert result.status == 200
        assert calls["n"] == 3

    def test_network_error_is_reported(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        site = FakeSite(extra={"/down": handler})
        result = asyncio.run(with_fetcher(site, lambda f: f.get(f"{HOST}/down")))
        assert result.status == 0
        assert result.error.startswith("ConnectError")
        assert not result.ok

    def test_max_bytes_truncates(self):
        site = FakeSite(extra={"/big": httpx.Response(200, content=b"x" * 100_000)})
        result = asyncio.run(with_fetcher(site, lambda f: f.get(f"{HOST}/big"), max_bytes=1000))
        assert result.truncated
        assert len(result.content) == 1000

    def test_charset_sniffing(self):
        body = '<html><head><meta charset="iso-8859-1"></head><body>café</body></html>'.encode("latin-1")
        site = FakeSite(extra={"/latin": httpx.Response(200, content=body, headers={"content-type": "text/html"})})
        result = asyncio.run(with_fetcher(site, lambda f: f.get(f"{HOST}/latin")))
        assert "café" in result.text

    def test_robots_cached_and_applied(self, site: FakeSite):
        async def go(f):
            return [await f.allowed(f"{HOST}/private/"), await f.allowed(f"{HOST}/docs/")]

        assert asyncio.run(with_fetcher(site, go)) == [False, True]
        assert site.paths().count("/robots.txt") == 1

    def test_robots_404_allows_everything(self):
        site = FakeSite(robots=None)
        assert asyncio.run(with_fetcher(site, lambda f: f.allowed(f"{HOST}/private/")))

    def test_robots_5xx_disallows_everything(self):
        site = FakeSite(robots_status=503)
        assert not asyncio.run(with_fetcher(site, lambda f: f.allowed(f"{HOST}/docs/")))

    def test_ignore_robots(self, site: FakeSite):
        assert asyncio.run(with_fetcher(site, lambda f: f.allowed(f"{HOST}/private/"), respect_robots=False))
        assert "/robots.txt" not in site.paths()

    def test_head_falls_back_to_get(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(405 if request.method == "HEAD" else 200)

        site = FakeSite(extra={"/nohead": handler})
        result = asyncio.run(with_fetcher(site, lambda f: f.head_or_get(f"{HOST}/nohead")))
        assert result.status == 200
        assert [r.method for r in site.requests] == ["HEAD", "GET"]

    def test_delay_spaces_requests(self, site: FakeSite):
        import time

        async def go(f):
            start = time.monotonic()
            await asyncio.gather(*(f.get(f"{HOST}/docs/") for _ in range(3)))
            return time.monotonic() - start

        elapsed = asyncio.run(with_fetcher(site, go, delay=0.05, respect_robots=False))
        assert elapsed >= 0.09
