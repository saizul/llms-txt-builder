# Contributing

Thanks for taking the time to improve llms-txt-builder. Bug reports, better
extraction on real-world themes, new validation rules and documentation fixes
are all welcome.

## Reporting a bug

Open an issue with:

- the command you ran and its output (`-vv` adds debug logging, including why
  each URL was skipped)
- your Python version and operating system
- for wrong extraction: a small HTML file that shows the problem, with real
  names and URLs replaced by `example.com`
- for validator problems: the smallest `llms.txt` that triggers the wrong result

## Development setup

You need Python 3.10 or newer. No network access is needed for development.

```console
$ git clone https://github.com/saizul/llms-txt-builder.git
$ cd llms-txt-builder
$ python -m venv .venv
$ . .venv/bin/activate
$ python -m pip install -e ".[dev]"
```

Before every commit:

```console
$ ruff check .
$ ruff format --check .
$ pytest
```

All three must pass; CI runs them on Python 3.10 to 3.13.

## Guidelines

- **Tests never use the network.** Add pages, sitemaps or canned responses to
  the fake site in `tests/fakesite.py`, which is served through
  `httpx.MockTransport`.
- **Format changes need a source.** The structure follows https://llmstxt.org/.
  A new validation rule gets the next free `LT0xx` code, a severity, a test
  that triggers it, and a row in the README table. Never reuse or renumber a
  code: CI pipelines filter on them.
- **Be polite by default.** New fetching features must respect `robots.txt`,
  the concurrency limit and the delay unless the user turns them off.
- **No heavy dependencies.** The runtime needs `httpx` only; parsing uses the
  standard library.
- **Every change comes with a test** that fails without it.
- Add a line to the `Unreleased` section of `CHANGELOG.md` for anything a user
  would notice.

## Project layout

```text
src/llms_txt_builder/
    urls.py       normalisation, same-site checks, URL patterns
    robots.py     RFC 9309 robots.txt parser
    fetch.py      async fetcher: concurrency, delays, retries, size limits
    sitemap.py    sitemap and sitemap index reading (gzip, limits)
    dom.py        tolerant HTML tree builder
    extract.py    metadata and main-content extraction
    markdown.py   HTML to Markdown conversion
    collect.py    sitemap, crawl and URL-list collection with filtering
    build.py      grouping into sections, llms.txt and llms-full.txt
    llmstxt.py    document model, parser and renderer
    validate.py   lint rules and the link checker
    diff.py       structural diff
    config.py     settings and the TOML / YAML loader
    cli.py        command-line interface
tests/            pytest suite and the fake website
examples/         configs, a sample llms.txt, a CI workflow, library use
```

## Pull requests

- Keep each pull request focused on one change.
- Describe what changed and why, and how you tested it.
- Do not bump the version number; that happens at release time.

By contributing you agree that your work is released under the MIT License.
