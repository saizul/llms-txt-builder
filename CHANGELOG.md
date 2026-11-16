# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-11-16

### Added

- `build` command with three sources: sitemaps and sitemap indexes (plain or gzip, nested, loop-safe), a
  same-host breadth-first crawl with depth and page limits, and URL lists; `--site` discovers sitemaps through
  `robots.txt` and `/sitemap.xml` and falls back to a crawl.
- Async fetching with `httpx`: concurrency limit, per-host delay, `Crawl-delay`, retries with backoff on network
  errors, 429 and 5xx, timeouts and download size limits.
- RFC 9309 robots.txt parser with longest-match rules, `*` and `$` wildcards and agent groups.
- Page filtering by noindex (meta and `X-Robots-Tag`), canonical duplicates, off-site redirects, non-HTML URLs,
  query strings and include/exclude patterns.
- Metadata extraction and a readability-style main-content scorer with an HTML to Markdown converter (nested
  lists, code fences, tables, quotes, absolute links).
- llms.txt output following the llmstxt.org structure, with section rules (globs and regular expressions), sort
  modes, `max_items`, overflow into `Optional`, notes and extra links; `llms-full.txt` with the full Markdown of
  every listed page.
- `validate` command with 22 rules (`LT001`-`LT022`), `--strict`, an optional dead link check and text, JSON and
  GitHub Actions annotation output.
- `diff` command comparing two files by structure, with text, JSON and unified output.
- `init` command, TOML and YAML configuration with strict checking, a JSON build report, Docker image published
  to `ghcr.io/saizul/llms-txt-builder`.
