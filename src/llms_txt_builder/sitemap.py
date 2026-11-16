"""Sitemap reading: ``<urlset>`` and ``<sitemapindex>`` files, plain or gzip compressed."""

from __future__ import annotations

import logging
import zlib
from dataclasses import dataclass
from xml.etree import ElementTree

from llms_txt_builder.fetch import Fetcher

log = logging.getLogger(__name__)

# The sitemap protocol limits a file to 50 MB uncompressed and 50,000 URLs.
MAX_SITEMAP_BYTES = 50 * 1024 * 1024


class SitemapError(ValueError):
    """The document is not a sitemap we can read."""


@dataclass(frozen=True)
class SitemapEntry:
    loc: str
    lastmod: str | None = None
    priority: float | None = None
    changefreq: str | None = None


@dataclass
class ParsedSitemap:
    kind: str  # "urlset" or "sitemapindex"
    entries: list[SitemapEntry]


def maybe_decompress(content: bytes, *, limit: int = MAX_SITEMAP_BYTES) -> bytes:
    """Gunzip ``content`` when it starts with the gzip magic bytes; refuse output larger than ``limit``."""
    if content[:2] != b"\x1f\x8b":
        return content
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    out = decoder.decompress(content, limit + 1)
    if len(out) > limit or decoder.unconsumed_tail:
        raise SitemapError(f"compressed sitemap expands to more than {limit} bytes")
    return out


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def parse_sitemap(content: bytes) -> ParsedSitemap:
    """Parse a sitemap document. Namespaces are ignored, so slightly broken sitemaps still work."""
    data = maybe_decompress(content)
    head = data[:4096].lower()
    if b"<!doctype" in head or b"<!entity" in data.lower():
        # Entity declarations are never needed in a sitemap; refusing them avoids entity expansion attacks.
        raise SitemapError("sitemap contains a DOCTYPE or entity declaration")
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError as exc:
        raise SitemapError(f"not valid XML: {exc}") from exc
    kind = _local(root.tag)
    if kind not in ("urlset", "sitemapindex"):
        raise SitemapError(f"unexpected root element <{kind}>")
    child_tag = "url" if kind == "urlset" else "sitemap"
    entries: list[SitemapEntry] = []
    for node in root:
        if _local(node.tag) != child_tag:
            continue
        fields: dict[str, str] = {}
        for sub in node:
            name = _local(sub.tag)
            if name in ("loc", "lastmod", "priority", "changefreq") and sub.text:
                fields[name] = sub.text.strip()
        loc = fields.get("loc")
        if not loc:
            continue
        priority: float | None = None
        if "priority" in fields:
            try:
                priority = min(1.0, max(0.0, float(fields["priority"])))
            except ValueError:
                priority = None
        entries.append(SitemapEntry(loc, fields.get("lastmod"), priority, fields.get("changefreq")))
    return ParsedSitemap(kind, entries)


@dataclass
class SitemapResult:
    entries: list[SitemapEntry]
    sitemaps_read: list[str]
    errors: list[tuple[str, str]]


async def read_sitemaps(
    fetcher: Fetcher,
    urls: list[str],
    *,
    max_depth: int = 3,
    max_sitemaps: int = 500,
    max_urls: int | None = None,
) -> SitemapResult:
    """Read sitemaps recursively, following ``<sitemapindex>`` files up to ``max_depth`` levels."""
    seen_sitemaps: set[str] = set()
    seen_urls: set[str] = set()
    entries: list[SitemapEntry] = []
    errors: list[tuple[str, str]] = []
    read: list[str] = []
    queue: list[tuple[str, int]] = [(u, 0) for u in urls]
    while queue:
        url, depth = queue.pop(0)
        if url in seen_sitemaps:
            continue
        if len(seen_sitemaps) >= max_sitemaps:
            errors.append((url, f"skipped: more than {max_sitemaps} sitemaps"))
            continue
        seen_sitemaps.add(url)
        result = await fetcher.get(url, max_bytes=MAX_SITEMAP_BYTES)
        if not result.ok:
            errors.append((url, result.error or f"HTTP {result.status}"))
            continue
        if result.truncated:
            errors.append((url, "sitemap larger than the download limit"))
            continue
        try:
            parsed = parse_sitemap(result.content)
        except SitemapError as exc:
            errors.append((url, str(exc)))
            continue
        read.append(url)
        if parsed.kind == "sitemapindex":
            if depth >= max_depth:
                errors.append((url, f"sitemap index nested deeper than {max_depth} levels"))
                continue
            queue.extend((e.loc, depth + 1) for e in parsed.entries)
            continue
        for entry in parsed.entries:
            if entry.loc in seen_urls:
                continue
            seen_urls.add(entry.loc)
            entries.append(entry)
            if max_urls is not None and len(entries) >= max_urls:
                return SitemapResult(entries, read, errors)
    for url, message in errors:
        log.warning("sitemap %s: %s", url, message)
    return SitemapResult(entries, read, errors)
