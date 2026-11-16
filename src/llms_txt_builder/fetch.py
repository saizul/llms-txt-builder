"""Polite asynchronous HTTP fetching with a concurrency limit, retries and robots.txt support."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

import httpx

from llms_txt_builder._version import __version__
from llms_txt_builder.robots import RobotsTxt
from llms_txt_builder.urls import origin_of

log = logging.getLogger(__name__)

DEFAULT_USER_AGENT = (
    f"Mozilla/5.0 (compatible; llms-txt-builder/{__version__}; +https://github.com/saizul/llms-txt-builder)"
)
ROBOTS_AGENT = "llms-txt-builder"
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int
    headers: dict[str, str] = field(default_factory=dict)
    text: str = ""
    content: bytes = b""
    error: str | None = None
    elapsed: float = 0.0
    truncated: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None and 200 <= self.status < 300

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "").split(";", 1)[0].strip().lower()

    @property
    def is_html(self) -> bool:
        ctype = self.content_type
        if ctype:
            return ctype in ("text/html", "application/xhtml+xml")
        head = self.text[:512].lstrip().lower()
        return head.startswith(("<!doctype html", "<html"))


@dataclass
class FetchSettings:
    concurrency: int = 8
    timeout: float = 20.0
    retries: int = 2
    backoff: float = 0.5
    delay: float = 0.0
    max_bytes: int = 5_000_000
    user_agent: str = DEFAULT_USER_AGENT
    robots_agent: str = ROBOTS_AGENT
    respect_robots: bool = True
    max_redirects: int = 10


def make_client(settings: FetchSettings, transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    """Create the shared client. Tests pass an ``httpx.MockTransport``."""
    return httpx.AsyncClient(
        transport=transport,
        timeout=httpx.Timeout(settings.timeout),
        follow_redirects=True,
        max_redirects=settings.max_redirects,
        headers={
            "User-Agent": settings.user_agent,
            "Accept": "text/html,application/xhtml+xml;q=0.9,text/plain;q=0.8,*/*;q=0.5",
        },
    )


class Fetcher:
    """Fetches URLs with a global concurrency limit and an optional per-origin delay.

    The delay is the larger of ``settings.delay`` and the site's ``Crawl-delay`` (when robots.txt is respected).
    """

    def __init__(self, client: httpx.AsyncClient, settings: FetchSettings | None = None) -> None:
        self.client = client
        self.settings = settings or FetchSettings()
        self._sem = asyncio.Semaphore(max(1, self.settings.concurrency))
        self._robots: dict[str, RobotsTxt] = {}
        self._robots_locks: dict[str, asyncio.Lock] = {}
        self._next_slot: dict[str, float] = {}
        self._slot_lock = asyncio.Lock()
        self.requests = 0

    async def robots_for(self, url: str) -> RobotsTxt:
        origin = origin_of(url)
        if origin in self._robots:
            return self._robots[origin]
        lock = self._robots_locks.setdefault(origin, asyncio.Lock())
        async with lock:
            if origin not in self._robots:
                self._robots[origin] = await self._load_robots(origin)
        return self._robots[origin]

    async def _load_robots(self, origin: str) -> RobotsTxt:
        result = await self._request(f"{origin}/robots.txt", max_bytes=500_000)
        if result.error is not None or result.status >= 500:
            # RFC 9309 section 2.3.1.4: when robots.txt is unreachable because of a server or network error the
            # crawler must assume a complete disallow.
            log.warning(
                "robots.txt for %s unreachable (%s); treating the site as disallowed (use --ignore-robots to override)",
                origin,
                result.error or result.status,
            )
            return RobotsTxt.disallowing_all()
        if result.status >= 400:
            return RobotsTxt.allowing_all()
        return RobotsTxt.parse(result.text)

    async def allowed(self, url: str) -> bool:
        if not self.settings.respect_robots:
            return True
        robots = await self.robots_for(url)
        return robots.can_fetch(self.settings.robots_agent, url)

    async def _wait_for_slot(self, url: str) -> None:
        delay = self.settings.delay
        if self.settings.respect_robots:
            robots = self._robots.get(origin_of(url))
            crawl_delay = robots.crawl_delay(self.settings.robots_agent) if robots else None
            if crawl_delay:
                delay = max(delay, min(crawl_delay, 30.0))
        if delay <= 0:
            return
        origin = origin_of(url)
        async with self._slot_lock:
            now = time.monotonic()
            start = max(now, self._next_slot.get(origin, now))
            self._next_slot[origin] = start + delay
        if start > now:
            await asyncio.sleep(start - now)

    async def get(self, url: str, *, max_bytes: int | None = None) -> FetchResult:
        """Fetch ``url`` (robots.txt must be checked by the caller with :meth:`allowed`)."""
        await self._wait_for_slot(url)
        return await self._request(url, max_bytes=max_bytes or self.settings.max_bytes)

    async def _request(self, url: str, *, max_bytes: int, method: str = "GET") -> FetchResult:
        attempts = max(0, self.settings.retries) + 1
        last: FetchResult | None = None
        for attempt in range(attempts):
            if attempt:
                await asyncio.sleep(self.settings.backoff * (2 ** (attempt - 1)))
            async with self._sem:
                last = await self._once(url, max_bytes=max_bytes, method=method)
            if last.error is None and last.status not in RETRY_STATUSES:
                return last
            log.debug("attempt %d for %s failed: %s", attempt + 1, url, last.error or last.status)
        assert last is not None
        return last

    async def _once(self, url: str, *, max_bytes: int, method: str) -> FetchResult:
        started = time.perf_counter()
        self.requests += 1
        try:
            async with self.client.stream(method, url) as response:
                chunks: list[bytes] = []
                size = 0
                truncated = False
                if method != "HEAD":
                    async for chunk in response.aiter_bytes():
                        chunks.append(chunk)
                        size += len(chunk)
                        if size >= max_bytes:
                            truncated = True
                            break
                content = b"".join(chunks)[:max_bytes]
                headers = {k.lower(): v for k, v in response.headers.items()}
                encoding = response.charset_encoding or _sniff_charset(content) or "utf-8"
                try:
                    text = content.decode(encoding, errors="replace")
                except LookupError:
                    text = content.decode("utf-8", errors="replace")
                return FetchResult(
                    url=url,
                    final_url=str(response.url),
                    status=response.status_code,
                    headers=headers,
                    text=text,
                    content=content,
                    elapsed=time.perf_counter() - started,
                    truncated=truncated,
                )
        except httpx.HTTPError as exc:
            return FetchResult(
                url=url,
                final_url=url,
                status=0,
                error=f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__,
                elapsed=time.perf_counter() - started,
            )

    async def head_or_get(self, url: str) -> FetchResult:
        """Status check for link validation: HEAD first, GET when the server does not support HEAD."""
        await self._wait_for_slot(url)
        result = await self._request(url, max_bytes=0, method="HEAD")
        if result.error is None and result.status in (403, 405, 501):
            result = await self._request(url, max_bytes=65_536)
        return result


def _sniff_charset(content: bytes) -> str | None:
    head = content[:2048].lower()
    marker = head.find(b"charset=")
    if marker == -1:
        return None
    value = head[marker + 8 : marker + 40].strip(b"\"' ")
    end = 0
    while end < len(value) and (chr(value[end]).isalnum() or value[end] in b"-_"):
        end += 1
    return value[:end].decode("ascii", errors="ignore") or None
