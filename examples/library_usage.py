"""Use llms-txt-builder as a library: build from an in-memory list of pages, then lint the result.

Runs offline: the pages are served by httpx.MockTransport.
"""

from __future__ import annotations

import asyncio

import httpx

from llms_txt_builder import Collector, Config, Fetcher, FetchSettings, build, lint, make_client
from llms_txt_builder.config import config_from_dict

PAGES = {
    "/": "<html><head><title>Acme</title><meta name='description' content='Acme sells rockets.'></head>"
    "<body><main><h1>Acme</h1><p>Rockets for everyone, shipped worldwide, with a warranty.</p></main></body></html>",
    "/docs/launch/": "<html><head><title>Launching | Acme</title></head><body><main><h1>Launching</h1>"
    "<p>Point the rocket up, light the fuse, and step back at least ten metres.</p></main></body></html>",
}


def handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/robots.txt":
        return httpx.Response(200, text="User-agent: *\nAllow: /\n")
    body = PAGES.get(request.url.path)
    if body is None:
        return httpx.Response(404)
    return httpx.Response(200, text=body, headers={"content-type": "text/html"})


async def main() -> None:
    config: Config = config_from_dict({"sections": [{"name": "Docs", "match": ["/docs/**"]}]})
    settings = FetchSettings(retries=0)
    async with make_client(settings, httpx.MockTransport(handler)) as client:
        collector = Collector(Fetcher(client, settings), config)
        collection = await collector.from_urls(["https://acme.test/", "https://acme.test/docs/launch/"])
    result = build(collection, config)
    print(result.text)
    report = lint(result.text)
    print(report.to_text())
    assert report.ok(strict=True)


if __name__ == "__main__":
    asyncio.run(main())
