"""Collect pages from a sitemap, a crawl or a list of URLs, and extract them."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path

from llms_txt_builder.config import Config
from llms_txt_builder.extract import PageInfo, extract_page
from llms_txt_builder.fetch import Fetcher, FetchResult
from llms_txt_builder.sitemap import read_sitemaps
from llms_txt_builder.urls import (
    any_match,
    compile_patterns,
    looks_like_html,
    normalize_url,
    origin_of,
    same_site,
)

log = logging.getLogger(__name__)


@dataclass
class Page:
    """A fetched page with its extracted information."""

    url: str
    order: int
    info: PageInfo
    status: int = 200
    priority: float | None = None


@dataclass
class Skipped:
    url: str
    reason: str


@dataclass
class Collection:
    pages: list[Page] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)
    errors: list[Skipped] = field(default_factory=list)
    homepage: PageInfo | None = None

    def skip(self, url: str, reason: str) -> None:
        log.info("skip %s: %s", url, reason)
        self.skipped.append(Skipped(url, reason))


def read_url_list(path: str | Path) -> list[str]:
    """One URL per line; blank lines and ``#`` comments are ignored. ``-`` reads standard input."""
    if str(path) == "-":
        import sys

        text = sys.stdin.read()
    else:
        text = Path(path).read_text(encoding="utf-8")
    urls = []
    for line in text.splitlines():
        token = line.strip().split(maxsplit=1)[0] if line.strip() else ""
        if token.startswith(("http://", "https://")):
            urls.append(token)
    return urls


class Collector:
    def __init__(self, fetcher: Fetcher, config: Config) -> None:
        self.fetcher = fetcher
        self.config = config
        self._include = compile_patterns(config.filter.include)
        self._exclude = compile_patterns(config.filter.exclude)

    # ---- filtering -------------------------------------------------------------------------------------------------
    def url_filter_reason(self, url: str) -> str | None:
        """Why ``url`` is excluded by the configuration, or ``None`` when it is kept."""
        if not looks_like_html(url):
            return "not an HTML page"
        if "?" in url and not self.config.filter.allow_query_urls:
            return "URL has a query string"
        if self._include and not any_match(self._include, url):
            return "not matched by filter.include"
        if self._exclude and any_match(self._exclude, url):
            return "matched by filter.exclude"
        return None

    # ---- sources ---------------------------------------------------------------------------------------------------
    async def from_sitemaps(self, sitemap_urls: list[str]) -> Collection:
        src = self.config.source
        result = await read_sitemaps(
            self.fetcher, sitemap_urls, max_depth=src.sitemap_depth, max_urls=max(src.max_pages * 4, src.max_pages)
        )
        collection = Collection()
        for url, message in result.errors:
            collection.errors.append(Skipped(url, message))
        priorities = {e.loc: e.priority for e in result.entries}
        urls = [e.loc for e in result.entries]
        await self._fetch_list(urls, collection, priorities)
        if sitemap_urls:
            await self._load_homepage(origin_of(sitemap_urls[0]) + "/", collection)
        return collection

    async def from_urls(self, urls: list[str]) -> Collection:
        collection = Collection()
        await self._fetch_list(urls, collection, {})
        if urls:
            await self._load_homepage(origin_of(urls[0]) + "/", collection)
        return collection

    async def _fetch_list(self, urls: list[str], collection: Collection, priorities: dict[str, float | None]) -> None:
        seen: set[str] = set()
        todo: list[tuple[int, str]] = []
        for url in urls:
            key = normalize_url(url)
            if key in seen:
                continue
            seen.add(key)
            reason = self.url_filter_reason(url)
            if reason:
                collection.skip(url, reason)
                continue
            if len(todo) >= self.config.source.max_pages:
                collection.skip(url, f"over source.max_pages ({self.config.source.max_pages})")
                continue
            todo.append((len(todo), url))

        async def one(order: int, url: str) -> None:
            if not await self.fetcher.allowed(url):
                collection.skip(url, "disallowed by robots.txt")
                return
            result = await self.fetcher.get(url)
            page = self._to_page(result, order, collection)
            if page is not None:
                page.priority = priorities.get(url)
                collection.pages.append(page)

        await asyncio.gather(*(one(order, url) for order, url in todo))
        collection.pages.sort(key=lambda p: p.order)

    async def _load_homepage(self, url: str, collection: Collection) -> None:
        for page in collection.pages:
            if normalize_url(page.url) == normalize_url(url):
                collection.homepage = page.info
                return
        if not await self.fetcher.allowed(url):
            return
        result = await self.fetcher.get(url)
        if result.ok and result.is_html:
            collection.homepage = extract_page(result.text, result.final_url)

    def _to_page(self, result: FetchResult, order: int, collection: Collection) -> Page | None:
        url = result.url
        if result.error is not None:
            collection.errors.append(Skipped(url, result.error))
            return None
        if result.status >= 400:
            collection.errors.append(Skipped(url, f"HTTP {result.status}"))
            return None
        if not result.is_html:
            collection.skip(url, f"not HTML ({result.content_type or 'unknown type'})")
            return None
        if self.config.filter.skip_noindex and "noindex" in result.headers.get("x-robots-tag", "").lower():
            collection.skip(url, "X-Robots-Tag: noindex")
            return None
        info = extract_page(result.text, result.final_url, images=self.config.output.images)
        if info.noindex and self.config.filter.skip_noindex:
            collection.skip(url, "meta robots noindex")
            return None
        if normalize_url(result.final_url) != normalize_url(url):
            if not same_site(result.final_url, url):
                collection.skip(url, f"redirects to another site ({result.final_url})")
                return None
            info.url = result.final_url
        if (
            self.config.filter.drop_non_canonical
            and info.canonical
            and normalize_url(info.canonical) != normalize_url(info.url)
        ):
            collection.skip(url, f"canonical points to {info.canonical}")
            return None
        return Page(url=info.url, order=order, info=info, status=result.status)

    # ---- crawling --------------------------------------------------------------------------------------------------
    def crawl_filter_reason(self, url: str) -> str | None:
        """Why the crawler does not request ``url``. ``filter.include`` is not applied here: pages outside it
        are still visited to discover links, they just do not end up in the output."""
        if not looks_like_html(url):
            return "not an HTML page"
        if "?" in url and not self.config.filter.allow_query_urls:
            return "URL has a query string"
        if self._exclude and any_match(self._exclude, url):
            return "matched by filter.exclude"
        return None

    async def crawl(self, start_url: str) -> Collection:
        """Breadth-first crawl of ``start_url``'s host, following links up to ``source.max_depth`` levels.

        The crawl stops when ``source.max_pages`` pages were collected or ``3 * max_pages`` URLs were requested.
        """
        src = self.config.source
        collection = Collection()
        seen: set[str] = {normalize_url(start_url)}
        frontier: list[str] = [start_url]
        order = 0
        requested = 0
        request_budget = src.max_pages * 3
        chunk_size = max(1, src.concurrency * 2)

        async def visit(url: str, idx: int) -> tuple[Page | None, list[str], FetchResult | None]:
            if not await self.fetcher.allowed(url):
                collection.skip(url, "disallowed by robots.txt")
                return None, [], None
            result = await self.fetcher.get(url)
            page = self._to_page(result, idx, collection)
            links: list[str] = []
            if page is not None:
                links = page.info.links
            elif result.ok and result.is_html:
                # Links of noindex and non-canonical pages are followed too, the way search engines do.
                links = extract_page(result.text, result.final_url).links
            return page, links, result

        depth = 0
        while frontier and depth <= src.max_depth:
            next_frontier: list[str] = []
            batch = []
            for url in frontier:
                reason = self.crawl_filter_reason(url) if depth > 0 else None
                if reason:
                    collection.skip(url, reason)
                else:
                    batch.append(url)
            for start in range(0, len(batch), chunk_size):
                if len(collection.pages) >= src.max_pages or requested >= request_budget:
                    for url in batch[start:]:
                        collection.skip(url, "crawl limit reached (source.max_pages)")
                    break
                chunk = batch[start : start + chunk_size]
                requested += len(chunk)
                results = await asyncio.gather(*(visit(url, order + i) for i, url in enumerate(chunk)))
                order += len(chunk)
                for (page, links, _), url in zip(results, chunk, strict=True):
                    if page is not None:
                        if depth == 0 and collection.homepage is None:
                            collection.homepage = page.info
                        if self._include and not any_match(self._include, page.url):
                            collection.skip(url, "not matched by filter.include")
                        elif depth == 0 and self.url_filter_reason(page.url):
                            collection.skip(url, self.url_filter_reason(page.url) or "")
                        elif len(collection.pages) < src.max_pages:
                            collection.pages.append(page)
                    for link in links:
                        if not same_site(link, start_url):
                            continue
                        key = normalize_url(link)
                        if key in seen:
                            continue
                        seen.add(key)
                        next_frontier.append(key)
            frontier = next_frontier
            depth += 1
        if frontier:
            log.info("%d URL(s) beyond source.max_depth were not crawled", len(frontier))
        if collection.homepage is None:
            await self._load_homepage(origin_of(start_url) + "/", collection)
        return collection
