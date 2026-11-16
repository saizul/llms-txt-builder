"""URL helpers: normalisation, same-site checks and pattern matching."""

from __future__ import annotations

import fnmatch
import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

# Query parameters that only track campaigns and never change the page content.
TRACKING_PARAMS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "utm_id",
        "gclid",
        "fbclid",
        "msclkid",
        "mc_cid",
        "mc_eid",
        "ref",
        "_ga",
    }
)

# File extensions that are never HTML pages; the crawler does not request them.
NON_HTML_EXTENSIONS = frozenset(
    {
        ".7z",
        ".avi",
        ".avif",
        ".bmp",
        ".css",
        ".csv",
        ".doc",
        ".docx",
        ".eot",
        ".exe",
        ".gif",
        ".gz",
        ".ico",
        ".jpeg",
        ".jpg",
        ".js",
        ".json",
        ".m4a",
        ".mov",
        ".mp3",
        ".mp4",
        ".ogg",
        ".otf",
        ".pdf",
        ".png",
        ".ppt",
        ".pptx",
        ".rar",
        ".rss",
        ".svg",
        ".tar",
        ".tgz",
        ".ttf",
        ".wav",
        ".webm",
        ".webp",
        ".woff",
        ".woff2",
        ".xls",
        ".xlsx",
        ".xml",
        ".zip",
    }
)


def normalize_url(url: str, *, strip_tracking: bool = True, keep_query: bool = True) -> str:
    """Return a canonical form of ``url`` used for de-duplication.

    Lower-cases scheme and host, removes default ports and the fragment, sorts nothing (parameter order can matter
    to some applications) but drops tracking parameters, and turns an empty path into ``/``.
    """
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    port = parts.port
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        host = f"{host}:{port}"
    path = parts.path or "/"
    query = ""
    if keep_query and parts.query:
        pairs = parse_qsl(parts.query, keep_blank_values=True)
        if strip_tracking:
            pairs = [(k, v) for k, v in pairs if k.lower() not in TRACKING_PARAMS]
        query = urlencode(pairs, doseq=True)
    return urlunsplit((scheme, host, path, query, ""))


def absolutize(base: str, href: str) -> str | None:
    """Resolve ``href`` against ``base``; return ``None`` for links that are not web pages."""
    href = (href or "").strip()
    if not href or href.startswith("#"):
        return None
    lowered = href.lower()
    if lowered.startswith(("javascript:", "mailto:", "tel:", "data:", "sms:", "ftp:", "file:")):
        return None
    url = urljoin(base, href)
    if urlsplit(url).scheme not in ("http", "https"):
        return None
    return url


def host_of(url: str) -> str:
    """Host name of ``url`` in lower case, with a non-default port if present."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.port and parts.port not in (80, 443):
        host = f"{host}:{parts.port}"
    return host


def same_site(a: str, b: str, *, allow_www: bool = True) -> bool:
    """True when both URLs are on the same host (``www.`` is ignored when ``allow_www`` is set)."""
    ha, hb = host_of(a), host_of(b)
    if allow_www:
        ha, hb = ha.removeprefix("www."), hb.removeprefix("www.")
    return ha == hb


def origin_of(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "", "", ""))


def looks_like_html(url: str) -> bool:
    """False for URLs whose extension says they are not HTML (images, archives, feeds, ...)."""
    path = urlsplit(url).path.lower()
    dot = path.rfind(".")
    if dot == -1 or "/" in path[dot:]:
        return True
    return path[dot:] not in NON_HTML_EXTENSIONS


def path_with_query(url: str) -> str:
    parts = urlsplit(url)
    return parts.path + (f"?{parts.query}" if parts.query else "")


class UrlPattern:
    """A URL pattern from the configuration.

    * ``re:<regex>`` is a regular expression searched in the full URL;
    * a pattern starting with ``http://`` or ``https://`` is a glob on the full URL;
    * anything else is a glob on the path (plus query), where ``*`` does not cross ``/`` and ``**`` does.
      A trailing ``/**`` also matches the directory itself, so ``/docs/**`` matches ``/docs/``, and a pattern
      that does not end in ``/`` ignores one trailing slash on the URL.
    """

    __slots__ = ("_full", "_regex", "raw")

    def __init__(self, raw: str) -> None:
        self.raw = raw
        if raw.startswith("re:"):
            self._regex = re.compile(raw[3:])
            self._full = True
        else:
            self._full = raw.startswith(("http://", "https://"))
            self._regex = re.compile(_glob_to_regex(raw) if not self._full else fnmatch.translate(raw))

    def matches(self, url: str) -> bool:
        if self.raw.startswith("re:"):
            return self._regex.search(url) is not None
        target = url if self._full else path_with_query(url)
        return self._regex.match(target) is not None

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"UrlPattern({self.raw!r})"


def _glob_to_regex(glob: str) -> str:
    out = ["^"]
    i = 0
    if glob.endswith("/**"):
        stem, tail = glob[:-3], "(?:/.*)?$"
    else:
        # "/blog/*" also matches "/blog/post/": a single trailing slash on the URL is optional.
        stem, tail = glob, ("$" if glob.endswith("/") else "/?$")
    while i < len(stem):
        ch = stem[i]
        if ch == "*":
            if stem[i : i + 2] == "**":
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(ch))
        i += 1
    out.append(tail)
    return "".join(out)


def compile_patterns(patterns: list[str] | tuple[str, ...] | None) -> list[UrlPattern]:
    return [UrlPattern(p) for p in (patterns or [])]


def any_match(patterns: list[UrlPattern], url: str) -> bool:
    return any(p.matches(url) for p in patterns)
