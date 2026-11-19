"""Command-line interface: ``build``, ``validate``, ``diff`` and ``init``."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import replace
from pathlib import Path

import httpx

from llms_txt_builder._version import __version__
from llms_txt_builder.build import BuildResult, build
from llms_txt_builder.collect import Collection, Collector, read_url_list
from llms_txt_builder.config import Config, ConfigError, load_config
from llms_txt_builder.diff import diff_texts
from llms_txt_builder.fetch import Fetcher, FetchSettings, make_client
from llms_txt_builder.llmstxt import parse
from llms_txt_builder.validate import check_links, lint, lint_bytes

log = logging.getLogger("llms_txt_builder")

EXIT_OK = 0
EXIT_FAIL = 1  # validation errors, differences, or no pages
EXIT_USAGE = 2  # bad arguments, unreadable config or input
EXIT_FETCH = 3  # the source (sitemap, start URL, remote file) could not be fetched

EXAMPLE_CONFIG = """\
# llms-txt-builder configuration. Every key is optional.

[site]
name = "Example Co"
summary = "Example Co makes invoicing software for freelancers. This file lists the pages that explain the product."
notes = ["Prices on the pricing page are in USD and exclude VAT."]

[source]
# sitemaps = ["https://www.example.com/sitemap.xml"]
max_pages = 300
concurrency = 6
delay = 0.0
respect_robots = true

[filter]
exclude = ["/tag/**", "/author/**", "/page/**", "/cart/**", "/checkout/**"]

[output]
title_source = "auto"          # auto | title | h1
description_source = "auto"    # auto | meta | content | none
max_description_length = 160
default_section = "Pages"      # "" drops pages that no section matches
full = true                    # also write llms-full.txt

[[sections]]
name = "Docs"
match = ["/docs/**"]
sort = "url"

[[sections]]
name = "Product"
match = ["/", "/features/**", "/pricing/"]

[[sections]]
name = "Blog"
match = ["/blog/**"]
max_items = 20
overflow = "optional"          # extra posts go to the Optional section

[[sections]]
name = "Legal"
match = ["/privacy/", "/terms/"]
optional = true                # listed under "## Optional"
"""


def _add_fetch_options(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("fetching")
    g.add_argument("--concurrency", type=int, help="parallel requests (default 8)")
    g.add_argument("--delay", type=float, help="seconds between requests to the same host (default 0)")
    g.add_argument("--timeout", type=float, help="request timeout in seconds (default 20)")
    g.add_argument("--retries", type=int, help="retries on network errors, 429 and 5xx (default 2)")
    g.add_argument("--user-agent", help="User-Agent header")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="llms-txt-builder",
        description="Generate and validate llms.txt / llms-full.txt files for a website.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-v", "--verbose", action="count", default=0, help="more logging (-vv for debug)")
    parser.add_argument("-q", "--quiet", action="store_true", help="only print errors")
    # -v and -q also work after the command name ("warm -q").
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("-v", "--verbose", action="count", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    shared.add_argument("-q", "--quiet", action="store_true", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)

    b = sub.add_parser(
        "build", parents=[shared], help="build llms.txt (and llms-full.txt) from a sitemap, a crawl or a URL list"
    )
    src = b.add_mutually_exclusive_group()
    src.add_argument("--sitemap", action="append", metavar="URL", help="sitemap or sitemap index URL (repeatable)")
    src.add_argument("--crawl", metavar="URL", help="crawl the host of this start URL")
    src.add_argument("--urls", metavar="FILE", help="file with one URL per line ('-' for standard input)")
    src.add_argument(
        "--site", metavar="URL", help="discover sitemaps from robots.txt (then /sitemap.xml), else crawl the site"
    )
    b.add_argument("-c", "--config", help="TOML or YAML configuration file")
    b.add_argument("-o", "--output-dir", default=".", help="directory for llms.txt and llms-full.txt (default .)")
    b.add_argument("--stdout", action="store_true", help="print llms.txt to standard output instead of writing files")
    b.add_argument("--no-full", action="store_true", help="do not write llms-full.txt")
    b.add_argument(
        "--markdown-pages",
        action="store_true",
        help="also write a Markdown file per listed page (URL path + .md) to the output directory",
    )
    b.add_argument("--name", help="site name for the H1 (overrides the config)")
    b.add_argument("--summary", help="summary for the blockquote (overrides the config)")
    b.add_argument("--max-pages", type=int, help="maximum pages in the output (default 500)")
    b.add_argument("--max-depth", type=int, help="crawl depth (default 5)")
    b.add_argument("--include", action="append", default=[], metavar="PATTERN", help="only keep matching URLs")
    b.add_argument("--exclude", action="append", default=[], metavar="PATTERN", help="drop matching URLs")
    b.add_argument("--ignore-robots", action="store_true", help="do not read or obey robots.txt (your own site only)")
    b.add_argument("--report", metavar="FILE", help="write a JSON report of placed, skipped and failed URLs")
    _add_fetch_options(b)

    v = sub.add_parser("validate", parents=[shared], help="lint an llms.txt file or URL")
    v.add_argument("source", help="path or http(s) URL of the llms.txt file ('-' for standard input)")
    v.add_argument("--check-links", action="store_true", help="request every link and report dead ones")
    v.add_argument("--same-host-only", action="store_true", help="with --check-links, only check the file's own host")
    v.add_argument("--no-redirects", action="store_true", help="with --check-links, report redirecting links")
    v.add_argument("--strict", action="store_true", help="exit with 1 on warnings too")
    v.add_argument("-f", "--format", choices=["text", "json", "github"], default="text")
    _add_fetch_options(v)

    d = sub.add_parser("diff", parents=[shared], help="compare two llms.txt files (paths or URLs)")
    d.add_argument("old", help="the reference file, e.g. the live https://example.com/llms.txt")
    d.add_argument("new", help="the new file, e.g. the one you just built")
    d.add_argument("-f", "--format", choices=["text", "json", "unified"], default="text")
    d.add_argument("--ignore-descriptions", action="store_true", help="ignore changes to link notes")
    d.add_argument("--exact-urls", action="store_true", help="compare URLs literally (no normalisation)")
    _add_fetch_options(d)

    i = sub.add_parser("init", parents=[shared], help="write an example configuration file")
    i.add_argument("path", nargs="?", default="llms-txt.toml")
    i.add_argument("--force", action="store_true", help="overwrite an existing file")
    return parser


def _settings(args: argparse.Namespace, config: Config | None = None) -> FetchSettings:
    src = config.source if config else None
    s = FetchSettings()
    if src is not None:
        s = replace(
            s,
            concurrency=src.concurrency,
            delay=src.delay,
            timeout=src.timeout,
            retries=src.retries,
            respect_robots=src.respect_robots,
            user_agent=src.user_agent,
            robots_agent=src.robots_agent,
        )
    for name in ("concurrency", "delay", "timeout", "retries", "user_agent"):
        value = getattr(args, name, None)
        if value is not None:
            s = replace(s, **{name: value})
    if getattr(args, "ignore_robots", False):
        s = replace(s, respect_robots=False)
    return s


def _configure_logging(args: argparse.Namespace) -> None:
    level = logging.ERROR if args.quiet else (logging.WARNING, logging.INFO, logging.DEBUG)[min(args.verbose, 2)]
    logging.basicConfig(level=level, format="%(levelname)s %(message)s", stream=sys.stderr, force=True)


async def _read_source(source: str, fetcher: Fetcher | None) -> tuple[bytes, str]:
    if source == "-":
        return sys.stdin.buffer.read(), "<stdin>"
    if source.startswith(("http://", "https://")):
        assert fetcher is not None
        result = await fetcher.get(source)
        if result.error is not None:
            raise OSError(f"{source}: {result.error}")
        if result.status >= 400:
            raise OSError(f"{source}: HTTP {result.status}")
        return result.content, source
    return Path(source).read_bytes(), source


# ---- build -----------------------------------------------------------------------------------------------------------
async def _discover(collector: Collector, fetcher: Fetcher, site: str) -> Collection:
    origin = site.rstrip("/")
    robots = await fetcher.robots_for(origin + "/")
    sitemaps = list(robots.sitemaps)
    if not sitemaps:
        probe = await fetcher.get(origin + "/sitemap.xml")
        if probe.ok and b"<" in probe.content[:200]:
            sitemaps = [origin + "/sitemap.xml"]
    if sitemaps:
        log.info("using sitemap(s): %s", ", ".join(sitemaps))
        collection = await collector.from_sitemaps(sitemaps)
        if collection.pages:
            return collection
        log.warning("the sitemap(s) gave no usable pages; crawling %s instead", site)
    return await collector.crawl(site)


def _apply_build_args(args: argparse.Namespace, config: Config) -> Config:
    if args.name:
        config.site.name = args.name
    if args.summary:
        config.site.summary = args.summary
    if args.max_pages is not None:
        config.source.max_pages = args.max_pages
    if args.max_depth is not None:
        config.source.max_depth = args.max_depth
    if args.include:
        config.filter.include = [*config.filter.include, *args.include]
    if args.exclude:
        config.filter.exclude = [*config.filter.exclude, *args.exclude]
    if args.no_full:
        config.output.full = False
    if args.markdown_pages:
        config.output.markdown_pages = True
    if args.ignore_robots:
        config.source.respect_robots = False
    config.validate()
    return config


def _report(collection: Collection, result: BuildResult) -> dict:
    return {
        "pages": [
            {"url": p.url, "section": result.placement.get(p.url), "title": p.info.title}
            for p in collection.pages
            if p.url in result.placement
        ],
        "dropped": [{"url": u, "reason": r} for u, r in result.dropped],
        "markdown_pages": sorted(result.markdown_pages),
        "skipped": [{"url": s.url, "reason": s.reason} for s in collection.skipped],
        "errors": [{"url": s.url, "reason": s.reason} for s in collection.errors],
    }


async def _cmd_build(args: argparse.Namespace, transport: httpx.AsyncBaseTransport | None) -> int:
    config = load_config(args.config) if args.config else Config()
    config = _apply_build_args(args, config)
    sitemaps = args.sitemap or ([] if (args.crawl or args.urls or args.site) else config.source.sitemaps)
    crawl = args.crawl or ("" if (args.sitemap or args.urls or args.site) else config.source.crawl)
    urls_file = args.urls or ("" if (args.sitemap or args.crawl or args.site) else config.source.urls_file)
    if not (sitemaps or crawl or urls_file or args.site):
        log.error("give a source: --site, --sitemap, --crawl or --urls (or set it in the config file)")
        return EXIT_USAGE
    settings = _settings(args, config)
    async with make_client(settings, transport) as client:
        fetcher = Fetcher(client, settings)
        collector = Collector(fetcher, config)
        if args.site:
            collection = await _discover(collector, fetcher, args.site)
        elif sitemaps:
            collection = await collector.from_sitemaps(sitemaps)
        elif crawl:
            collection = await collector.crawl(crawl)
        else:
            try:
                urls = read_url_list(urls_file)
            except OSError as exc:
                log.error("cannot read %s: %s", urls_file, exc)
                return EXIT_USAGE
            collection = await collector.from_urls(urls)
    for err in collection.errors:
        log.warning("failed: %s (%s)", err.url, err.reason)
    if not collection.pages:
        log.error("no pages collected (%d skipped, %d failed)", len(collection.skipped), len(collection.errors))
        return EXIT_FETCH if collection.errors and not collection.skipped else EXIT_FAIL

    result = build(collection, config)
    text = result.text
    self_check = lint(text)
    for issue in self_check.issues:
        log.warning("generated file: %s %s", issue.code, issue.message)

    if args.report:
        Path(args.report).write_text(json.dumps(_report(collection, result), indent=2) + "\n", encoding="utf-8")
    if args.stdout:
        sys.stdout.write(text)
        return EXIT_OK
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "llms.txt").write_text(text, encoding="utf-8")
    written = [str(out_dir / "llms.txt")]
    if config.output.full and result.full_text:
        (out_dir / "llms-full.txt").write_text(result.full_text, encoding="utf-8")
        written.append(str(out_dir / "llms-full.txt"))
    for rel, markdown in result.markdown_pages.items():
        target = out_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(markdown, encoding="utf-8")
    if result.markdown_pages:
        written.append(f"{len(result.markdown_pages)} Markdown page(s)")
    if not args.quiet:
        listed = sum(len(s.items) for s in result.document.sections)
        print(
            f"wrote {', '.join(written)}: {listed} link(s) in {len(result.document.sections)} section(s); "
            f"{len(collection.skipped)} skipped, {len(collection.errors)} failed"
        )
    return EXIT_OK


# ---- validate ----------------------------------------------------------------------------------------------------
async def _cmd_validate(args: argparse.Namespace, transport: httpx.AsyncBaseTransport | None) -> int:
    settings = _settings(args)
    async with make_client(settings, transport) as client:
        fetcher = Fetcher(client, replace(settings, respect_robots=False))
        try:
            data, source = await _read_source(args.source, fetcher)
        except OSError as exc:
            log.error("%s", exc)
            return EXIT_FETCH if args.source.startswith("http") else EXIT_USAGE
        report = lint_bytes(data, source)
        if args.check_links and not any(i.code == "LT018" for i in report.issues):
            doc = parse(data.decode("utf-8"))
            await check_links(
                report, doc, fetcher, allow_redirects=not args.no_redirects, same_host_only=args.same_host_only
            )
    if args.format == "json":
        print(report.to_json())
    elif args.format == "github":
        out = report.to_github()
        if out:
            print(out)
        print(report.to_text().splitlines()[-1])
    elif not args.quiet or report.issues:
        print(report.to_text())
    return EXIT_OK if report.ok(strict=args.strict) else EXIT_FAIL


# ---- diff --------------------------------------------------------------------------------------------------------
async def _cmd_diff(args: argparse.Namespace, transport: httpx.AsyncBaseTransport | None) -> int:
    settings = _settings(args)
    async with make_client(settings, transport) as client:
        fetcher = Fetcher(client, replace(settings, respect_robots=False))
        try:
            old_data, old_source = await _read_source(args.old, fetcher)
            new_data, new_source = await _read_source(args.new, fetcher)
        except OSError as exc:
            log.error("%s", exc)
            return EXIT_FETCH
    result = diff_texts(
        old_data.decode("utf-8", errors="replace"),
        new_data.decode("utf-8", errors="replace"),
        old_source=old_source,
        new_source=new_source,
        normalize=not args.exact_urls,
        ignore_descriptions=args.ignore_descriptions,
    )
    if args.format == "json":
        print(result.to_json())
    elif args.format == "unified":
        sys.stdout.write(result.unified)
    else:
        print(result.to_text())
    return EXIT_OK if result.identical else EXIT_FAIL


def _cmd_init(args: argparse.Namespace) -> int:
    path = Path(args.path)
    if path.exists() and not args.force:
        log.error("%s exists; use --force to overwrite it", path)
        return EXIT_USAGE
    path.write_text(EXAMPLE_CONFIG, encoding="utf-8")
    if not args.quiet:
        print(f"wrote {path}")
    return EXIT_OK


def main(argv: list[str] | None = None, *, transport: httpx.AsyncBaseTransport | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args)
    try:
        if args.command == "init":
            return _cmd_init(args)
        handler = {"build": _cmd_build, "validate": _cmd_validate, "diff": _cmd_diff}[args.command]
        return asyncio.run(handler(args, transport))
    except ConfigError as exc:
        log.error("configuration: %s", exc)
        return EXIT_USAGE
    except OSError as exc:
        log.error("%s", exc)
        return EXIT_USAGE
    except KeyboardInterrupt:  # pragma: no cover
        return 130


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
