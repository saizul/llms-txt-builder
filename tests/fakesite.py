"""A small fake website served through ``httpx.MockTransport``. Tests never touch the network."""

from __future__ import annotations

import gzip
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

HOST = "https://www.example.com"


def page(title: str, body: str, *, description: str = "", head: str = "", lang: str = "en") -> str:
    meta = f'<meta name="description" content="{description}">' if description else ""
    return (
        f'<!doctype html><html lang="{lang}"><head><meta charset="utf-8"><title>{title}</title>{meta}{head}</head>'
        "<body>"
        '<header class="site-header"><a href="/">Example Docs</a>'
        '<nav><a href="/docs/">Docs</a> <a href="/blog/">Blog</a> <a href="/privacy/">Privacy</a></nav></header>'
        f"{body}"
        '<aside class="sidebar"><h3>Related</h3><a href="/blog/post-1/">Post 1</a></aside>'
        '<footer class="site-footer"><p>Copyright Example Docs. <a href="/tag/news/">News</a></p></footer>'
        "<script>console.log('tracking')</script>"
        "</body></html>"
    )


def article(h1: str, paragraphs: list[str], extra: str = "") -> str:
    ps = "".join(f"<p>{p}</p>" for p in paragraphs)
    return f"<main><article><h1>{h1}</h1>{ps}{extra}</article></main>"


LOREM = (
    "This guide walks through every step, explains the trade-offs, and shows the commands you need, "
    "so that a new user can follow it from start to finish without guessing."
)

PAGES: dict[str, str] = {
    "/": page(
        "Example Docs | Fast static documentation",
        article("Example Docs", [LOREM, "Read the <a href='/docs/'>documentation</a> or the blog."]),
        description="Example Docs is a static documentation generator for small teams.",
        head='<meta property="og:site_name" content="Example Docs">',
    ),
    "/docs/": page(
        "Documentation | Example Docs",
        article(
            "Documentation",
            [
                LOREM,
                "Start with <a href='/docs/install/'>installation</a>, then <a href='/docs/config/'>configuration</a>.",
            ],
        ),
        description="Everything about installing and configuring Example Docs.",
    ),
    "/docs/install/": page(
        "Installation | Example Docs",
        article(
            "Installation",
            [LOREM, "Install the package with pip, then run the init command in an empty folder."],
            extra=(
                "<h2>Requirements</h2><ul><li>Python 3.10 or newer</li><li>Git</li></ul>"
                '<pre><code class="language-bash">pip install example-docs\nexample-docs init</code></pre>'
                "<table><tr><th>OS</th><th>Supported</th></tr><tr><td>Linux</td><td>yes</td></tr></table>"
            ),
        ),
        description="Install Example Docs with pip and create your first site.",
        head='<link rel="alternate" type="text/markdown" href="/docs/install.md">',
    ),
    "/docs/config/": page(
        "Configuration | Example Docs",
        article("Configuration", [LOREM, "The <code>site.toml</code> file holds every setting, with defaults."]),
        description="",
    ),
    "/blog/": page(
        "Blog | Example Docs",
        "<main><h1>Blog</h1><ul>"
        "<li><a href='/blog/post-1/'>Post 1</a></li><li><a href='/blog/post-2/'>Post 2</a></li>"
        "<li><a href='/blog/post-3/'>Post 3</a></li></ul></main>",
        description="News and release notes.",
    ),
    "/blog/post-1/": page("Release 1.0 | Example Docs", article("Release 1.0", [LOREM]), description="Version 1.0."),
    "/blog/post-2/": page("Release 1.1 | Example Docs", article("Release 1.1", [LOREM]), description="Version 1.1."),
    "/blog/post-3/": page("Release 1.2 | Example Docs", article("Release 1.2", [LOREM]), description="Version 1.2."),
    "/privacy/": page("Privacy | Example Docs", article("Privacy policy", [LOREM]), description="How we use data."),
    "/tag/news/": page("News | Example Docs", article("Tag: news", [LOREM])),
    "/hidden/": page(
        "Hidden | Example Docs", article("Hidden", [LOREM]), head='<meta name="robots" content="noindex, follow">'
    ),
    "/private/": page("Private | Example Docs", article("Private", [LOREM])),
    "/docs/config-copy/": page(
        "Configuration copy | Example Docs",
        article("Configuration", [LOREM]),
        head=f'<link rel="canonical" href="{HOST}/docs/config/">',
    ),
}

ROBOTS = f"""User-agent: *
Disallow: /private/
Allow: /

User-agent: badbot
Disallow: /

Sitemap: {HOST}/sitemap_index.xml
"""


def urlset(paths: list[str], *, priority: dict[str, str] | None = None) -> str:
    items = []
    for p in paths:
        prio = f"<priority>{priority[p]}</priority>" if priority and p in priority else ""
        items.append(f"<url><loc>{HOST}{p}</loc><lastmod>2026-11-01</lastmod>{prio}</url>")
    return '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + (
        "".join(items) + "</urlset>"
    )


SITEMAP_INDEX = (
    '<?xml version="1.0" encoding="UTF-8"?><sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    f"<sitemap><loc>{HOST}/page-sitemap.xml</loc></sitemap>"
    f"<sitemap><loc>{HOST}/post-sitemap.xml.gz</loc></sitemap>"
    "</sitemapindex>"
)


@dataclass
class FakeSite:
    """Routes requests to canned responses and records what was requested."""

    pages: dict[str, str] = field(default_factory=lambda: dict(PAGES))
    robots: str | None = ROBOTS
    robots_status: int = 200
    extra: dict[str, httpx.Response | Callable[[httpx.Request], httpx.Response]] = field(default_factory=dict)
    requests: list[httpx.Request] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.extra.setdefault("/sitemap_index.xml", httpx.Response(200, text=SITEMAP_INDEX))
        self.extra.setdefault(
            "/page-sitemap.xml",
            httpx.Response(
                200,
                text=urlset(
                    [
                        "/",
                        "/docs/",
                        "/docs/install/",
                        "/docs/config/",
                        "/docs/config-copy/",
                        "/privacy/",
                        "/tag/news/",
                        "/hidden/",
                        "/private/",
                        "/missing/",
                        "/old-install/",
                        "/feed.xml",
                    ]
                ),
            ),
        )
        self.extra.setdefault(
            "/post-sitemap.xml.gz",
            httpx.Response(
                200,
                content=gzip.compress(urlset(["/blog/", "/blog/post-1/", "/blog/post-2/", "/blog/post-3/"]).encode()),
                headers={"content-type": "application/x-gzip"},
            ),
        )
        self.extra.setdefault("/old-install/", httpx.Response(301, headers={"location": f"{HOST}/docs/install/"}))

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if request.url.query:
            path += "?" + request.url.query.decode()
        if path == "/robots.txt":
            if self.robots is None:
                return httpx.Response(404, text="not found")
            return httpx.Response(self.robots_status, text=self.robots)
        if path in self.extra:
            value = self.extra[path]
            if callable(value):
                return value(request)
            # A fresh copy, so one canned response can be served many times.
            return httpx.Response(value.status_code, headers=value.headers, content=value.content)
        if path in self.pages:
            return httpx.Response(200, text=self.pages[path], headers={"content-type": "text/html; charset=utf-8"})
        return httpx.Response(404, text="<h1>Not found</h1>", headers={"content-type": "text/html"})

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def paths(self) -> list[str]:
        return [r.url.path for r in self.requests]


async def with_fetcher(site: FakeSite, fn, **settings):
    """Run ``fn(fetcher)`` with a fetcher wired to ``site`` (no retries, no backoff unless asked)."""
    from llms_txt_builder.fetch import Fetcher, FetchSettings, make_client

    s = FetchSettings(retries=settings.pop("retries", 0), backoff=0, **settings)
    async with make_client(s, site.transport) as client:
        return await fn(Fetcher(client, s))
