# Security policy

## Supported versions

Security fixes are made for the latest release. Please upgrade before reporting.

| Version | Supported |
| ------- | --------- |
| 0.1.x   | yes       |

## Reporting a vulnerability

Please do not open a public issue for a security problem. Use GitHub's private
vulnerability reporting instead: open the **Security** tab of
https://github.com/saizul/llms-txt-builder and choose **Report a
vulnerability**.

Include what you found, how to reproduce it and what an attacker could do with
it. You should get an answer within a week. Once a fix is released you will be
credited in the changelog unless you prefer otherwise.

Do not include cookies, tokens or private URLs in a report. Replace domains
with `example.com`.

## Scope

In scope:

- parsing of untrusted sitemaps, HTML, robots.txt and llms.txt files: crashes,
  entity expansion, decompression bombs, excessive memory or CPU use
- the crawler leaving the start host, ignoring robots.txt when it should obey
  it, or ignoring the concurrency and delay limits
- injection through page titles, descriptions or content into the generated
  Markdown files
- anything that sends data to a host the user did not ask for

Out of scope: the content of the sites you crawl, and how other software
renders the generated Markdown.
