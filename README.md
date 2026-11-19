# llms-txt-builder

[![CI](https://github.com/saizul/llms-txt-builder/actions/workflows/ci.yml/badge.svg)](https://github.com/saizul/llms-txt-builder/actions/workflows/ci.yml)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

Build and check `llms.txt` and `llms-full.txt` files for any website. The tool reads your sitemap (or crawls the
site politely, or takes a list of URLs), pulls out each page's title, meta description, H1 and main content,
groups the pages into sections with your own URL rules, and writes an `llms.txt` that follows the
[llmstxt.org](https://llmstxt.org/) proposal. Next to it you get an `llms-full.txt` with the cleaned-up Markdown of
every listed page. A `validate` command lints any `llms.txt` with error codes and CI exit codes, and `diff` shows
how a new build differs from the file that is live now.

`llms.txt` is a plain Markdown file at the root of a site. It tells large language models, AI crawlers and agents
what the site is and which pages matter. Writing one by hand is easy for five pages and tedious for five hundred.
Keeping it in sync as pages come and go is the part people forget.

```console
$ llms-txt-builder build --site https://www.example.com -c llms-txt.toml -o public/
wrote public/llms.txt, public/llms-full.txt: 7 link(s) in 2 section(s); 5 skipped, 1 failed

$ cat public/llms.txt
# Example Docs

> Static documentation for small teams.

## Docs

- [Documentation](https://www.example.com/docs/): Everything about installing and configuring Example Docs.
- [Configuration](https://www.example.com/docs/config/): This guide walks through every step, explains the trade-offs…
- [Installation](https://www.example.com/docs/install/): Install Example Docs with pip and create your first site.

## Blog

- [Release 1.2](https://www.example.com/blog/post-3/): Version 1.2.

$ llms-txt-builder validate https://www.example.com/llms.txt --check-links
https://www.example.com/llms.txt:14: LT016 error: https://www.example.com/old-page/: HTTP 404
1 error(s), 0 warning(s), 42 link(s) checked
```

## Contents

- [Features](#features)
- [Installation](#installation)
- [Docker image](#docker-image)
- [Usage](#usage)
- [The llms.txt format](#the-llmstxt-format)
- [Configuration](#configuration)
- [Validation rules](#validation-rules)
- [Exit codes](#exit-codes)
- [How content extraction works](#how-content-extraction-works)
- [Library use](#library-use)
- [Examples](#examples)
- [Testing](#testing)
- [Security notes](#security-notes)
- [Limitations](#limitations)
- [Contributing](#contributing)
- [License](#license)

## Features

- **Three inputs**: a sitemap or sitemap index (plain or `.xml.gz`, nested indexes, loop-safe), a breadth-first
  crawl from a start URL (same host only, depth and page limits), or a text file with one URL per line.
  `--site https://example.com` finds the sitemaps in `robots.txt`, then tries `/sitemap.xml`, and crawls only if
  neither works.
- **Polite fetching**: async `httpx` with a concurrency limit, a per-host delay, `Crawl-delay` support, retries
  with backoff on network errors, 429 and 5xx, timeouts and a download size limit.
- **robots.txt done properly** (RFC 9309): longest-match rules, `*` and `$` wildcards, agent groups, and a full
  disallow when `robots.txt` returns a server error. Turn it off with `--ignore-robots` for your own site.
- **Page filtering**: `meta robots` and `X-Robots-Tag` noindex, canonical duplicates, redirects to other sites,
  non-HTML URLs, query strings, and your own include/exclude patterns.
- **Extraction**: title, meta description (with Open Graph fallbacks), H1, `og:site_name`, language, canonical and
  `rel="alternate" type="text/markdown"` links. The main content comes from a readability-style scorer with no
  heavy dependencies, and is converted to clean Markdown: headings, nested lists, code fences with language,
  tables, quotes, links made absolute.
- **llms.txt builder**: H1 site name, blockquote summary, optional notes, H2 sections of `- [title](url): notes`,
  and an `Optional` section that always comes last. Section rules use globs (`/docs/**`) or regular expressions,
  with ordering, sort mode, `max_items` per section and overflow into `Optional`. You can add extra links too
  (GitHub, status page, API reference on another host).
- **llms-full.txt**: the same header, then every listed page as a `##` section with its source URL and the full
  Markdown content. Headings are shifted down so the structure stays valid, and the length per page can be capped.
- **Validator**: 22 rules with stable codes (`LT001`…`LT022`), errors and warnings, `--strict`, an optional dead
  link check (HEAD with GET fallback, redirect reporting), and text, JSON or GitHub Actions annotation output.
- **Diff**: compares two files (paths or URLs) by structure: title, summary, notes, sections added, removed or
  reordered, links added, removed or moved between sections, and changed titles and notes. URLs are normalised so
  tracking parameters do not count as changes. Text, JSON or unified diff output.
- **Config** in TOML (built in) or YAML (optional extra), checked strictly so typos fail loudly.

## Installation

You need Python 3.10 or newer. The only runtime dependency is `httpx` (plus `tomli` on Python 3.10).

```console
$ pip install llms-txt-builder            # from PyPI, once published
$ pip install "llms-txt-builder[yaml]"    # with YAML config support
```

From a checkout:

```console
$ git clone https://github.com/saizul/llms-txt-builder.git
$ cd llms-txt-builder
$ python -m pip install .
```

## Docker image

Every push to `main` and every release publishes a multi-arch image (`linux/amd64`, `linux/arm64`) to the GitHub
Container Registry:

```console
$ docker pull ghcr.io/saizul/llms-txt-builder:latest
$ docker run --rm ghcr.io/saizul/llms-txt-builder --help
```

The working directory inside the container is `/work`. Mount a folder there to read a config file and write the
output, and run as your own user so the files belong to you:

```console
$ docker run --rm -v "$PWD:/work" --user "$(id -u):$(id -g)" ghcr.io/saizul/llms-txt-builder \
    build --site https://www.example.com -c llms-txt.toml -o public/
$ docker run --rm -v "$PWD:/work:ro" ghcr.io/saizul/llms-txt-builder validate public/llms.txt --strict
```

Tags: `latest` (main branch), the short commit SHA, and the version number for releases (`0.1.0`). The image runs
as an unprivileged user (UID 10001) and includes YAML support.

## Usage

```text
llms-txt-builder build    [--site URL | --sitemap URL ... | --crawl URL | --urls FILE] [options]
llms-txt-builder validate PATH_OR_URL [--check-links] [--strict] [-f text|json|github]
llms-txt-builder diff     OLD NEW [-f text|json|unified] [--ignore-descriptions] [--exact-urls]
llms-txt-builder init     [PATH] [--force]
```

Global options: `-v` / `-vv` for more logging (skip reasons, requests), `-q` for errors only, `--version`.

### build

```console
# Find the sitemaps through robots.txt, fall back to a crawl
$ llms-txt-builder build --site https://www.example.com -o public/

# One or more sitemaps (a sitemap index is followed)
$ llms-txt-builder build --sitemap https://www.example.com/sitemap_index.xml -c llms-txt.toml

# Crawl from a start URL, three clicks deep, at most 200 pages, one request per second
$ llms-txt-builder build --crawl https://docs.example.com/ --max-depth 3 --max-pages 200 --delay 1 --concurrency 2

# A hand-picked URL list, printed instead of written
$ llms-txt-builder build --urls examples/urls.txt --stdout --no-full
```

| Option | Meaning |
| --- | --- |
| `--site URL` | sitemaps from `robots.txt`, then `/sitemap.xml`, otherwise a crawl |
| `--sitemap URL` | sitemap or sitemap index (repeatable) |
| `--crawl URL` | crawl the host of this start URL |
| `--urls FILE` | one URL per line; `-` reads standard input |
| `-c, --config FILE` | TOML or YAML configuration |
| `-o, --output-dir DIR` | where `llms.txt` and `llms-full.txt` go (default `.`) |
| `--stdout` | print `llms.txt` instead of writing files |
| `--no-full` | skip `llms-full.txt` |
| `--markdown-pages` | also write a Markdown file per listed page (`docs/install.md`, `docs/index.html.md`) |
| `--name`, `--summary` | override the H1 and the blockquote |
| `--max-pages N`, `--max-depth N` | page and crawl depth limits (default 500 and 5) |
| `--include P`, `--exclude P` | URL patterns, added to the ones in the config (repeatable) |
| `--ignore-robots` | do not fetch or obey `robots.txt` (only for sites you run) |
| `--report FILE` | JSON list of placed, dropped, skipped and failed URLs, with reasons |
| `--concurrency`, `--delay`, `--timeout`, `--retries`, `--user-agent` | fetch settings |

Without a config file the site name comes from `og:site_name`, then from the home page title (`"Home | Example
Co"` gives `Example Co`), then from the host name. The summary comes from the home page meta description. Every
page lands in a single `Pages` section.

After building, the tool lints its own output and logs any issue as a warning.

### validate

```console
$ llms-txt-builder validate public/llms.txt
$ llms-txt-builder validate https://www.example.com/llms.txt --check-links --same-host-only
$ llms-txt-builder validate llms.txt --strict --format github     # annotations in GitHub Actions
$ curl -s https://www.example.com/llms.txt | llms-txt-builder validate - -f json
```

`--check-links` requests every unique link (HEAD, then GET when the server refuses HEAD) using the same concurrency
and retry settings. `--no-redirects` also reports links that redirect (LT017). `--same-host-only` limits the check
to the host of the first link.

### diff

```console
$ llms-txt-builder diff https://www.example.com/llms.txt public/llms.txt
--- https://www.example.com/llms.txt
+++ public/llms.txt
+ [Docs] https://www.example.com/docs/upgrade/
- [Optional] https://www.example.com/privacy/
~ https://www.example.com/docs/config/ moved from 'Docs' to 'Blog'
~ https://www.example.com/docs/install/ title: 'Installation' -> 'Install guide'
4 change(s): 1 link-added, 1 link-moved, 1 link-removed, 1 link-title
```

The exit code is 0 when both files list the same content and 1 when they differ, so a scheduled job can decide
whether to deploy the new file. `-f unified` prints a plain line diff instead.

### init

`llms-txt-builder init` writes a commented `llms-txt.toml` to start from.

## The llms.txt format

The [llmstxt.org](https://llmstxt.org/) proposal defines a Markdown file with these parts, in this order:

1. an **H1** with the name of the project or site. This is the only required part;
2. a **blockquote** with a short summary and the key context for reading the rest of the file;
3. zero or more **Markdown sections of any kind except headings**: paragraphs and lists with notes on how to use
   the site and its files;
4. zero or more **H2 sections**, each a list of links: `- [name](url)`, optionally followed by `: notes`.

An H2 called **Optional** has a special meaning: its links are secondary and can be skipped when a shorter context
is needed. The file usually lives at `/llms.txt`. A subpath such as `/docs/llms.txt` covers the URLs below it.

`llms-full.txt` is a widely used companion convention rather than part of the proposal. It is one file with the
full text of the listed pages, so a reader can load everything in one request. This tool writes it as:

```markdown
# Site name

> Summary

## Page title

Source: https://www.example.com/page/

> The page's meta description

The page content as Markdown, with its headings moved down to ### and below…
```

The proposal also suggests offering a Markdown version of each page at the same URL plus `.md`. When your pages
link one with `<link rel="alternate" type="text/markdown" href="…">`, set `output.link_to_markdown = true` and
`llms.txt` will link those versions.

If your site has no Markdown versions yet, `--markdown-pages` (or `output.markdown_pages = true`) writes them next
to `llms.txt`, one file per listed page, at the path the proposal uses: the URL path plus `.md`, and
`index.html.md` for URLs that end in `/`.

| Page URL | File in the output directory |
| --- | --- |
| `https://www.example.com/` | `index.html.md` |
| `https://www.example.com/docs/` | `docs/index.html.md` |
| `https://www.example.com/docs/install` | `docs/install.md` |

Each file has the page title as H1, the meta description as a quote, a `Source:` line and the page content as
Markdown (headings are kept as they are). Upload the output directory to your web root and set
`output.link_to_markdown = true` to point `llms.txt` at these files; a page that already links its own Markdown
version keeps that link. Paths that could leave the output directory (`..`, encoded slashes or backslashes) are
skipped and listed under `dropped` in the `--report` file, which also lists the files under `markdown_pages`. When
the pages come from more than one host, files are named by path only and the first page wins.

## Configuration

Everything is optional. A full example is in [`examples/llms-txt.toml`](examples/llms-txt.toml), and the same
format in YAML is in [`examples/llms-txt.yaml`](examples/llms-txt.yaml). Unknown keys and wrong types are errors.

```toml
[site]
name = "Example Co"                         # H1; default: og:site_name, home page title, host
summary = "Invoicing software for freelancers."  # blockquote; default: home page meta description
notes = ["Prices are in USD.", "The API is versioned."]  # paragraphs between summary and sections

[source]
sitemaps = ["https://www.example.com/sitemap.xml"]  # or crawl = "https://…/", or urls_file = "urls.txt"
max_pages = 500          # pages in the output (a crawl requests at most 3x this)
max_depth = 5            # crawl depth (clicks from the start URL)
concurrency = 8          # parallel requests, 1-64
delay = 0.0              # seconds between requests to the same host
timeout = 20.0
retries = 2
respect_robots = true
user_agent = "Mozilla/5.0 (compatible; llms-txt-builder/0.1; +https://github.com/saizul/llms-txt-builder)"
robots_agent = "llms-txt-builder"   # the name matched against User-agent lines in robots.txt
sitemap_depth = 3        # nested sitemap index levels

[filter]
include = []             # if set, only matching URLs are kept
exclude = ["/tag/**", "/author/**"]
skip_noindex = true      # meta robots / X-Robots-Tag noindex
drop_non_canonical = true
allow_query_urls = false # URLs with ?query are skipped by default

[output]
title_source = "auto"    # auto | title | h1
strip_site_name = true   # "Pricing | Example Co" -> "Pricing"
description_source = "auto"  # auto (meta, else first sentences) | meta | content | none
max_description_length = 160
default_section = "Pages"    # section for unmatched pages; "" drops them
sort = "input"           # input (sitemap/crawl order) | url | title
link_to_markdown = false
markdown_pages = false   # write a .md file per listed page (see "The llms.txt format")
full = true              # write llms-full.txt
full_max_chars_per_page = 0  # 0 = no limit
full_demote_headings = true
images = false           # keep images in llms-full.txt

[[sections]]             # evaluated top to bottom; the first match wins
name = "Docs"
match = ["/docs/**"]
sort = "url"             # overrides output.sort
max_items = 0            # 0 = no limit
overflow = "drop"        # drop | optional (move extra items to the Optional section)
optional = false         # true puts matching pages in the Optional section

[[links]]                # extra links, for example on other hosts
section = "Docs"
title = "API reference"
url = "https://api.example.com/docs/"
description = "OpenAPI reference"
```

**URL patterns** are used in `match`, `include` and `exclude`:

| Pattern | Matches |
| --- | --- |
| `/` | only the home page |
| `/pricing/` | exactly that path |
| `/blog/*` | `/blog/` and `/blog/post/`; `*` does not cross a `/`, and one trailing slash is optional |
| `/docs/**` | `/docs`, `/docs/` and everything below |
| `https://docs.example.com/**` | a glob on the full URL, for other hosts |
| `re:/20\d\d/\d\d/` | a regular expression searched anywhere in the full URL |

Sections appear in the order of the `[[sections]]` table, then the default section, then `[[links]]` sections
that are not defined, and `Optional` last.

## Validation rules

| Code | Severity | Rule |
| --- | --- | --- |
| LT001 | error | the file is empty |
| LT002 | error | the first content line is not an H1 (`# Name`) |
| LT003 | error | more than one H1 |
| LT004 | warning | no blockquote summary after the H1 |
| LT005 | warning | the summary blockquote is not directly after the H1 |
| LT006 | error | H3 or deeper heading (only H1 and H2 are part of the format) |
| LT007 | error | a list item in a link section is not a Markdown link |
| LT008 | warning | text after a link is not introduced with `:` |
| LT009 | error | the link URL is missing or not a valid http(s) URL |
| LT010 | warning | a section has no links |
| LT011 | warning | the same URL is listed twice |
| LT012 | warning | the same section name is used twice |
| LT013 | warning | `Optional` is not the last section |
| LT014 | warning | paragraphs inside a link section (put notes before the first H2) |
| LT015 | error | the link text is empty |
| LT016 | error | dead link (`--check-links`): HTTP 4xx/5xx or a network error |
| LT017 | warning | the link redirects (`--check-links --no-redirects`) |
| LT018 | error | the file is not valid UTF-8 |
| LT019 | warning | a colon after the link with no notes |
| LT020 | warning | the H1 has no text |
| LT021 | warning | relative link URL |
| LT022 | warning | an H2 with no text |

Headings inside fenced code blocks are ignored.

## Exit codes

| Code | `build` | `validate` | `diff` |
| --- | --- | --- | --- |
| 0 | files written | no errors (and no warnings with `--strict`) | same content |
| 1 | no pages collected | errors found | differences found |
| 2 | bad arguments or config, unreadable URL list | file not readable | — |
| 3 | the source could not be fetched (every page or sitemap failed) | remote file not reachable | a file could not be fetched |

## How content extraction works

The HTML is parsed with the standard library's `html.parser` into a small tolerant tree (implicitly closed `<p>`
and `<li>`, stray end tags and unclosed elements are handled). Then:

1. Links are collected for the crawler first, so navigation menus still lead to new pages.
2. Elements that are never content are removed: scripts, styles, forms, `nav`, `aside`, dialogs, hidden elements
   (`hidden`, `aria-hidden="true"`, `display:none`), page-level `header` and `footer`, landmark roles such as
   `navigation` and `contentinfo`, and blocks whose `class` or `id` looks like a sidebar, comments, share bar,
   cookie banner, newsletter box, breadcrumb or ad. A class that also says `content`, `post` or `article` keeps
   the block.
3. If exactly one `<main>`, `[role=main]` or `<article>` has real text, that is the content.
4. Otherwise every paragraph-like block of 25 characters or more gets a score from its length and commas. Its
   parent gets the full score and its grandparent half. Containers get a bonus or penalty from their class names,
   and the final score is multiplied by `1 - link density`. The best container wins, and `<body>` is the fallback.
5. The winner is converted to Markdown. Text is escaped where Markdown would misread it, links and images become
   absolute, `<pre>` blocks keep their language class, and tables become GitHub-style tables.

When a page has no meta description, its description in `llms.txt` is the first sentences of its prose (headings,
lists, tables and code are skipped), cut at a sentence or word boundary.

## Library use

```python
import asyncio
from llms_txt_builder import Collector, Fetcher, FetchSettings, build, lint, load_config, make_client

async def main() -> None:
    config = load_config("llms-txt.toml")
    settings = FetchSettings(concurrency=4)
    async with make_client(settings) as client:
        collection = await Collector(Fetcher(client, settings), config).from_sitemaps(
            ["https://www.example.com/sitemap.xml"]
        )
    result = build(collection, config)
    print(result.text)        # llms.txt
    print(result.full_text)   # llms-full.txt
    print(lint(result.text).to_text())

asyncio.run(main())
```

`make_client(settings, transport)` accepts any `httpx` transport, so you can plug in a mock or a custom
transport. [`examples/library_usage.py`](examples/library_usage.py) runs offline with `httpx.MockTransport`.

## Examples

- [`examples/llms-txt.toml`](examples/llms-txt.toml): a WordPress-style site with product, docs, blog and legal
  sections, excluded archives and an extra link.
- [`examples/llms-txt.yaml`](examples/llms-txt.yaml): a short YAML config that drops unmatched pages.
- [`examples/urls.txt`](examples/urls.txt): a URL list for `--urls`.
- [`examples/llms.txt`](examples/llms.txt): a valid sample file (CI validates it with `--strict`).
- [`examples/github-actions-llms-txt.yml`](examples/github-actions-llms-txt.yml): a weekly workflow that builds,
  validates with annotations, diffs against the live file and uploads the result.
- [`examples/library_usage.py`](examples/library_usage.py): the library API, offline.

## Testing

```console
$ python -m pip install -e ".[dev]"
$ pytest
$ ruff check . && ruff format --check .
```

The tests never use the network. A fake website in [`tests/fakesite.py`](tests/fakesite.py) is served through
`httpx.MockTransport`. It has a `robots.txt` with disallowed paths, a sitemap index with a plain and a gzip child
sitemap, noindex and canonical-duplicate pages, a redirect, a 404 and a non-HTML URL. The tests cover the robots
parser, sitemaps (including entity and gzip-bomb protection), retries and delays, extraction and Markdown
conversion, grouping rules, every validation rule, the diff and the CLI exit codes. CI runs them on Python 3.10
to 3.13, smoke-tests the CLI offline and builds the Docker image.

## Security notes

- **Only crawl sites you are allowed to crawl.** `robots.txt` is obeyed by default, and the crawler never leaves
  the start host (the `www.` prefix is ignored). Use `--ignore-robots` only for your own sites. Keep the
  concurrency and delay polite on shared hosting.
- **Untrusted input**: sitemaps with a `DOCTYPE` or entity declarations are refused (no entity expansion), gzip
  sitemaps are decompressed with a 50 MB limit, every download is capped (`max_bytes`, 5 MB for pages), and
  redirects are limited to 10. HTML is parsed, never executed.
- **Output**: titles and notes are written into Markdown. Brackets in link text are escaped, and spaces and
  parentheses in URLs are percent-encoded so a page title cannot break the list syntax. The output is not HTML;
  if you render it as HTML, sanitise it as you would any user content.
- **No credentials**: the tool sends no cookies or auth headers and stores nothing besides the files you ask for.
  To crawl a staging site behind HTTP auth, put a proxy in front of it rather than passing secrets on the command
  line.

See [SECURITY.md](SECURITY.md) for reporting a vulnerability.

## Limitations

- JavaScript is not executed. Pages that render their content in the browser (client-side SPAs) give little or
  no main content. Use their sitemap and set `description_source = "meta"`, or serve pre-rendered HTML.
- The content heuristic is good on articles, docs and typical CMS themes, and weaker on page-builder layouts made
  of many small blocks. Check `llms-full.txt` before publishing it.
- The HTML parser is tolerant but not a full HTML5 tree builder. Badly broken markup can end up in the wrong place.
- The site name and summary are guesses without a config. Set `[site]` for a file you will publish.
- The diff compares structure and normalised URLs, not the full Markdown of `llms-full.txt`. Use `-f unified` for
  a line diff.
- The llms.txt format is a proposal and may change. The validator follows the structure described on
  llmstxt.org as of November 2026.

## Contributing

Bug reports and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for the development setup and
guidelines, [CHANGELOG.md](CHANGELOG.md) for the release history, and the [code of conduct](CODE_OF_CONDUCT.md).

## License

[MIT](LICENSE) © 2026 Saizul Amin
